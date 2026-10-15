"""视频证据门禁：编码兼容、黑屏与报告内嵌。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.agent_runner_video_evidence import (
    ensure_video_evidence_embedded,
    ensure_video_evidence_healthy,
    list_video_evidence_files,
)


class _ScriptedRunner:
    """按命令首词返回预设结果的假命令执行端口。"""

    def __init__(self, ffprobe_stdout: str, ffmpeg_stderr: str = "") -> None:
        self._ffprobe_stdout = ffprobe_stdout
        self._ffmpeg_stderr = ffmpeg_stderr

    def run(self, command, **_kwargs) -> CommandResult:
        is_probe = command[0] == "ffprobe"
        return CommandResult(
            command=tuple(command),
            return_code=0,
            stdout=self._ffprobe_stdout if is_probe else "",
            stderr="" if is_probe else self._ffmpeg_stderr,
        )


def _probe_json(codec: str, pix_fmt: str, duration: float) -> str:
    return json.dumps(
        {
            "streams": [{"codec_name": codec, "pix_fmt": pix_fmt}],
            "format": {"duration": str(duration)},
        }
    )


def _video(tmp_path: Path, name: str = "rv-1-flow.mp4") -> list[Path]:
    video_path = tmp_path / name
    video_path.write_bytes(b"x")
    return [video_path]


def test_h264_yuv420p_video_passes(tmp_path: Path) -> None:
    runner = _ScriptedRunner(_probe_json("h264", "yuv420p", 18.2))
    ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_hevc_mp4_is_rejected(tmp_path: Path) -> None:
    runner = _ScriptedRunner(_probe_json("hevc", "yuv420p", 18.2))
    with pytest.raises(ValidationEvidenceError, match="blank"):
        ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_yuv444_mp4_is_rejected(tmp_path: Path) -> None:
    runner = _ScriptedRunner(_probe_json("h264", "yuv444p", 18.2))
    with pytest.raises(ValidationEvidenceError, match="yuv444p"):
        ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_vp8_webm_passes(tmp_path: Path) -> None:
    runner = _ScriptedRunner(_probe_json("vp8", "yuv420p", 5.0))
    ensure_video_evidence_healthy(_video(tmp_path, "rv-2-flow.webm"), tmp_path, runner)


def test_too_short_video_is_rejected(tmp_path: Path) -> None:
    runner = _ScriptedRunner(_probe_json("h264", "yuv420p", 0.2))
    with pytest.raises(ValidationEvidenceError, match="at least"):
        ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_all_black_video_is_rejected(tmp_path: Path) -> None:
    runner = _ScriptedRunner(
        _probe_json("h264", "yuv420p", 10.0),
        "[blackdetect] black_start:0 black_end:10 black_duration:10",
    )
    with pytest.raises(ValidationEvidenceError, match="black"):
        ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_missing_video_stream_is_rejected(tmp_path: Path) -> None:
    runner = _ScriptedRunner(json.dumps({"streams": [], "format": {"duration": "9"}}))
    with pytest.raises(ValidationEvidenceError, match="no video stream"):
        ensure_video_evidence_healthy(_video(tmp_path), tmp_path, runner)


def test_video_must_be_embedded_in_report(tmp_path: Path) -> None:
    video_files = _video(tmp_path)
    (tmp_path / "x.evidence-report.md").write_text("[录屏](rv-1-flow.mp4)\n", encoding="utf-8")
    with pytest.raises(ValidationEvidenceError, match="not embedded"):
        ensure_video_evidence_embedded(video_files, tmp_path)


def test_embedded_video_tag_passes(tmp_path: Path) -> None:
    video_files = _video(tmp_path)
    (tmp_path / "x.evidence-report.md").write_text(
        '<video controls src="rv-1-flow.mp4"></video>\n', encoding="utf-8"
    )
    ensure_video_evidence_embedded(video_files, tmp_path)


def test_no_report_yet_skips_embed_check(tmp_path: Path) -> None:
    ensure_video_evidence_embedded(_video(tmp_path), tmp_path)


def test_list_video_evidence_files_filters_by_suffix(tmp_path: Path) -> None:
    candidate_files = [tmp_path / "a.png", tmp_path / "b.MP4", tmp_path / "c.webm"]
    assert list_video_evidence_files(candidate_files) == candidate_files[1:]


def _flat_comment(tmp_path: Path, file_names: tuple[str, ...]) -> str:
    from backend.core.use_cases.agent_runner_structured_evidence import EvidenceUpload
    from backend.core.use_cases.agent_runner_validation_publication import build_evidence_comment
    from tests.test_agent_runner_structured_evidence import _legacy_config

    return build_evidence_comment(
        upload=EvidenceUpload(
            branch="iar-evidence/issue-9", commit_sha="c1", file_names=file_names
        ),
        worktree_path=tmp_path,
        config=_legacy_config(),
        pr_url="https://github.com/example/repo/pull/7",
        head_sha="abc1234",
        issue_body="",
        evidence_dir=tmp_path,
    )


def test_pr_comment_announces_video_evidence(tmp_path: Path) -> None:
    (tmp_path / "rv-1-flow.mp4").write_bytes(b"x" * 2048)
    comment = _flat_comment(tmp_path, ("rv-1-flow.mp4",))
    assert "Video evidence: 1 recording(s)" in comment
    assert "`rv-1-flow.mp4` (2 KB)" in comment
    assert "[Open file]" in comment


def test_pr_comment_without_video_has_no_summary(tmp_path: Path) -> None:
    (tmp_path / "rv-1-out.txt").write_text("ok", encoding="utf-8")
    assert "Video evidence" not in _flat_comment(tmp_path, ("rv-1-out.txt",))


def test_pr_comment_survives_unreadable_video(tmp_path: Path) -> None:
    comment = _flat_comment(tmp_path, ("rv-1-missing.mp4",))
    assert "size unknown" in comment
