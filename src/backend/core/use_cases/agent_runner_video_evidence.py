"""视频证据门禁：编码兼容性、黑屏检测与报告内嵌播放器。

录屏证据常见两类“文件在、却看不了”的失败：编码播放器不认（如 HEVC、
yuv444）、整段是黑屏；以及视频躺在证据目录里却没写进报告，评审根本看不到。
本模块把这两类问题挡在交付前，错误会进入既有 recovery 循环。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError

logger = logging.getLogger(__name__)

VIDEO_EVIDENCE_SUFFIXES = frozenset({".mp4", ".mov", ".webm"})
# mp4/mov 要求 H.264 + yuv420p（Safari、QuickTime、系统预览都能播）；
# webm 为 Playwright 默认产物，浏览器原生可播，放宽到常见 WebM 编码。
_MP4_FAMILY_CODECS = frozenset({"h264"})
_MP4_FAMILY_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
_WEBM_CODECS = frozenset({"vp8", "vp9", "av1"})
_MIN_VIDEO_DURATION_SECONDS = 1.0
_BLACK_RATIO_LIMIT = 0.9
_BLACK_DURATION_PATTERN = re.compile(r"black_duration:(?P<seconds>[0-9.]+)")
_VIDEO_TAG_WINDOW_CHARS = 600
_EVIDENCE_REPORT_SUFFIX = ".evidence-report.md"


def list_video_evidence_files(evidence_files: list[Path]) -> list[Path]:
    """Pick the video files out of a first-level evidence listing."""
    return [
        evidence_file
        for evidence_file in evidence_files
        if evidence_file.suffix.lower() in VIDEO_EVIDENCE_SUFFIXES
    ]


def ensure_video_evidence_healthy(
    video_files: list[Path],
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> None:
    """Fail when a recorded video is unplayable, too short, or all black.

    ``ffprobe`` / ``ffmpeg`` 不存在时只告警并跳过：机器缺工具不应让交付
    永远卡死，但门禁在装了 ffmpeg 的机器上仍然生效。

    Args:
        video_files: 证据目录第一层的视频文件。
        worktree_path: worktree 根目录，用作命令 cwd。
        process_runner: 命令执行端口。

    Raises:
        ValidationEvidenceError: 编码不兼容、无视频流、过短或几乎全黑。
    """
    for video_file in video_files:
        probe_result = _run_tool(
            process_runner,
            worktree_path,
            (
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt:format=duration",
                "-of",
                "json",
                str(video_file),
            ),
        )
        if probe_result is None:
            logger.warning("ffprobe unavailable; skip video evidence health check")
            return
        video_stream, duration_seconds = _parse_probe_json(video_file, probe_result.stdout)
        _check_codec(video_file, video_stream)
        if duration_seconds < _MIN_VIDEO_DURATION_SECONDS:
            raise ValidationEvidenceError(
                f"Video evidence `{video_file.name}` is {duration_seconds:.1f}s; a real "
                f"screen recording must run at least {_MIN_VIDEO_DURATION_SECONDS:.0f}s."
            )
        _check_not_black(video_file, duration_seconds, worktree_path, process_runner)


def ensure_video_evidence_embedded(video_files: list[Path], evidence_dir: Path) -> None:
    """Require each video to sit inside a ``<video>`` player in the evidence report.

    证据报告尚未生成时不判定（报告由收尾阶段写入，缺报告另有门禁）。

    Raises:
        ValidationEvidenceError: 报告存在，但有视频没被 ``<video>`` 标签引用。
    """
    if not video_files or not evidence_dir.is_dir():
        return
    report_files = sorted(evidence_dir.glob(f"*{_EVIDENCE_REPORT_SUFFIX}"))
    if not report_files:
        return
    report_text = "\n".join(
        report_file.read_text(encoding="utf-8", errors="replace") for report_file in report_files
    )
    video_tag_windows = _collect_video_tag_windows(report_text)
    unembedded_names = [
        video_file.name
        for video_file in video_files
        if not any(video_file.name in window for window in video_tag_windows)
    ]
    if not unembedded_names:
        return
    names_text = ", ".join(f"`{name}`" for name in unembedded_names)
    raise ValidationEvidenceError(
        f"Video evidence {names_text} is not embedded in the evidence report. Add a native "
        '`<video controls src="<relative path>"></video>` player for each recording to '
        f"`*{_EVIDENCE_REPORT_SUFFIX}`; a bare file in the evidence directory (or a plain "
        "link) cannot be reviewed on the PR."
    )


def _collect_video_tag_windows(report_text: str) -> list[str]:
    """Slice the text following every ``<video`` opener, up to its closing tag."""
    windows: list[str] = []
    for tag_match in re.finditer(r"<video\b", report_text, re.IGNORECASE):
        window_text = report_text[tag_match.start() : tag_match.start() + _VIDEO_TAG_WINDOW_CHARS]
        closing_index = window_text.lower().find("</video>")
        windows.append(window_text if closing_index < 0 else window_text[:closing_index])
    return windows


def _run_tool(process_runner: IProcessRunner, cwd: Path, command: tuple[str, ...]):
    """Run a media tool; ``None`` means the binary is missing."""
    try:
        command_result = process_runner.run(command, cwd=cwd, check=False, capture_output=True)
    except (FileNotFoundError, OSError):
        return None
    if command_result.return_code == 127:
        return None
    return command_result


def _parse_probe_json(video_file: Path, probe_stdout: str) -> tuple[dict[str, str], float]:
    """Extract the first video stream and container duration from ffprobe JSON."""
    try:
        probe_payload = json.loads(probe_stdout)
        video_streams = probe_payload.get("streams") or []
        duration_seconds = float((probe_payload.get("format") or {}).get("duration", 0.0))
    except (ValueError, TypeError, AttributeError):
        raise ValidationEvidenceError(
            f"Video evidence `{video_file.name}` could not be probed by ffprobe; the file is "
            "corrupt or not a video."
        ) from None
    if not video_streams:
        raise ValidationEvidenceError(
            f"Video evidence `{video_file.name}` has no video stream (audio-only or truncated)."
        )
    return video_streams[0], duration_seconds


def _check_codec(video_file: Path, video_stream: dict[str, str]) -> None:
    """Reject codecs that common players refuse to render."""
    codec_name = str(video_stream.get("codec_name", "")).lower()
    pixel_format = str(video_stream.get("pix_fmt", "")).lower()
    if video_file.suffix.lower() == ".webm":
        if codec_name not in _WEBM_CODECS:
            raise ValidationEvidenceError(
                f"Video evidence `{video_file.name}` uses codec `{codec_name}`; "
                f"a .webm must be one of {sorted(_WEBM_CODECS)}."
            )
        return
    if codec_name not in _MP4_FAMILY_CODECS or pixel_format not in _MP4_FAMILY_PIXEL_FORMATS:
        raise ValidationEvidenceError(
            f"Video evidence `{video_file.name}` is `{codec_name}`/`{pixel_format}`; players "
            "such as Safari and QuickTime show it blank. Re-encode with "
            "`ffmpeg -i <in> -c:v libx264 -pix_fmt yuv420p -movflags +faststart <out>.mp4`."
        )


def _check_not_black(
    video_file: Path,
    duration_seconds: float,
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> None:
    """Fail when black frames cover almost the whole recording."""
    black_result = _run_tool(
        process_runner,
        worktree_path,
        (
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(video_file),
            "-vf",
            "blackdetect=d=0.5:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ),
    )
    if black_result is None:
        logger.warning("ffmpeg unavailable; skip black-frame check")
        return
    black_seconds = sum(
        float(black_match.group("seconds"))
        for black_match in _BLACK_DURATION_PATTERN.finditer(black_result.stderr)
    )
    if black_seconds >= duration_seconds * _BLACK_RATIO_LIMIT:
        raise ValidationEvidenceError(
            f"Video evidence `{video_file.name}` is {black_seconds:.1f}s black out of "
            f"{duration_seconds:.1f}s — the page was not rendered when recording. Wait for "
            "the page to render before recording, then re-record."
        )
