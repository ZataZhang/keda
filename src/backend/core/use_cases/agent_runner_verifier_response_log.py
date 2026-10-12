"""把 verifier 的原始响应落盘：出结论、超时被杀、人工接管取消三条路径。

verifier 以 ``capture_output`` 运行，输出既不进 stdout 也不逐行落日志：一旦判定
阻断、超时或被停下，操作者事后只剩一行结论，看不到它判到第几项。落盘只为可诊断
性，写盘失败绝不影响门禁语义与取消语义。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from backend.core.shared.models.agent_runner import IssueSummary

_logger = logging.getLogger(__name__)

VERIFIER_RESPONSE_FILENAME = "verifier-response.txt"


def write_verifier_response_log(
    response_log_path: Path,
    header_lines: list[str],
    response_text: str,
) -> Path | None:
    """把 verifier 的原始响应落盘,便于事后分辨"真红"与"漏 marker"。

    verifier 以 ``capture_output`` 运行,输出不进 stdout 也不逐行落日志;一旦
    判定阻断,操作者事后无从查证它到底说了什么。写盘失败绝不能影响门禁本身。

    Returns:
        实际写入的路径;写入失败时返回 ``None``。
    """
    try:
        response_log_path.parent.mkdir(parents=True, exist_ok=True)
        response_log_path.write_text(
            "\n".join([*header_lines, "", response_text]),
            encoding="utf-8",
        )
    except OSError as write_error:
        _logger.warning(
            "Could not save verifier response to %s: %s",
            response_log_path,
            write_error,
        )
        return None
    return response_log_path


def save_timed_out_verifier_response(
    timeout_error: subprocess.TimeoutExpired,
    *,
    issue: IssueSummary,
    verifier_agent: str,
    builder_sha: str,
    response_log_path: Path | None,
) -> None:
    """超时被杀时,把 verifier 已经产出的部分输出落盘。

    verifier 以 ``capture_output`` 运行,输出既不进 stdout 也不逐行落日志:超时
    这条路径上如果不落盘,一次"跑了半小时被杀"就只剩一行 ``timed out``,连它判
    到第几项、是不是已经发现了问题都看不到。写盘只为可诊断性,失败不影响门禁。
    """
    partial_output = decode_partial_stream(timeout_error.output)
    partial_stderr = decode_partial_stream(timeout_error.stderr)
    _logger.error(
        "Independent verifier (agent %r) timed out after %ss for Issue #%d; "
        "captured %d chars of partial output before the kill.",
        verifier_agent,
        timeout_error.timeout,
        issue.number,
        len(partial_output),
    )
    if response_log_path is None:
        return
    write_verifier_response_log(
        response_log_path,
        [
            "# Independent verifier raw response (TIMED OUT — no verdict)",
            f"# issue: #{issue.number}",
            f"# verifier agent: {verifier_agent}",
            f"# builder sha: {builder_sha}",
            f"# timeout seconds: {timeout_error.timeout}",
            f"# partial stdout chars: {len(partial_output)}",
            f"# partial stderr chars: {len(partial_stderr)}",
        ],
        "\n".join(part for part in (partial_output, partial_stderr) if part),
    )


def save_cancelled_verifier_response(
    cancelled_error: InterruptedError,
    *,
    issue: IssueSummary,
    verifier_agent: str,
    builder_sha: str,
    response_log_path: Path | None,
) -> None:
    """人工接管取消时,把 verifier 截至被杀的部分输出落盘（Issue #278）。

    与超时落盘同源:接管**不产生 verdict**,但操作者接手审查时值得看到它被
    停下前判到了哪一步。写盘失败只影响诊断性,不影响取消语义。
    """
    captured = str(cancelled_error)
    _logger.error(
        "Independent verifier (agent %r) was cancelled by a human takeover for "
        "Issue #%d; captured %d chars before the kill.",
        verifier_agent,
        issue.number,
        len(captured),
    )
    if response_log_path is None:
        return
    write_verifier_response_log(
        response_log_path,
        [
            "# Independent verifier raw response (CANCELLED BY HUMAN TAKEOVER — no verdict)",
            f"# issue: #{issue.number}",
            f"# verifier agent: {verifier_agent}",
            f"# builder sha: {builder_sha}",
            f"# captured chars: {len(captured)}",
        ],
        captured,
    )


def decode_partial_stream(stream_content: str | bytes | None) -> str:
    """把 ``TimeoutExpired`` 上的部分输出统一成 ``str``。

    同一个属性在 text 模式下是 ``str``、在 bytes 模式下是 ``bytes``,而超时路径
    不该因为解码细节再抛一次异常。
    """
    if stream_content is None:
        return ""
    if isinstance(stream_content, bytes):
        return stream_content.decode("utf-8", errors="replace")
    return stream_content
