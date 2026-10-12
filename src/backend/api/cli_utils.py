"""Shared CLI helpers not tied to a specific command.

This module keeps ``backend.api.cli`` below the file-size limit by housing
small, reusable formatting and error-handling utilities.
"""

from __future__ import annotations

import shlex
import subprocess
from datetime import datetime

_MAX_CLI_ERROR_STREAM_CHARS = 12000


def format_local_display_time(timestamp: str | None) -> str:
    """把带时区的 ISO-8601 时间戳换算成执行机器本地时区的人读文本。

    CLI 人读输出按本机时区展示时间；JSON 机器输出不经过本函数，账本里的
    UTC 原文保持不变。

    Args:
        timestamp (str | None): ISO-8601 时间戳。带时区（``Z`` 或 ``±HH:MM``
            偏移）的值换算到机器本地时区并保留偏移；无时区的墙钟标签与无法
            解析的值原样返回，不做时区猜测；空值返回空串。

    Returns:
        str: 本地时区的 ``YYYY-MM-DDTHH:MM:SS±HH:MM`` 文本，或原样输入。
    """
    if not timestamp:
        return ""
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    if parsed_timestamp.tzinfo is None:
        return timestamp
    return parsed_timestamp.astimezone().isoformat(timespec="seconds")


def _format_command_for_cli(command: object) -> str:
    """Format a failed command for CLI diagnostics."""
    if isinstance(command, str):
        return command
    if isinstance(command, (list, tuple)):
        return shlex.join(str(command_part) for command_part in command)
    return str(command)


def _decode_cli_error_stream(stream_value: object) -> str:
    """Decode captured subprocess output for CLI diagnostics."""
    if stream_value is None:
        return ""
    if isinstance(stream_value, bytes):
        return stream_value.decode("utf-8", errors="replace")
    return str(stream_value)


def _truncate_cli_error_stream(stream_text: str) -> str:
    """Limit very large captured command output in CLI diagnostics."""
    if len(stream_text) <= _MAX_CLI_ERROR_STREAM_CHARS:
        return stream_text
    omitted_char_count = len(stream_text) - _MAX_CLI_ERROR_STREAM_CHARS
    return (
        stream_text[:_MAX_CLI_ERROR_STREAM_CHARS]
        + f"\n... truncated {omitted_char_count} chars ..."
    )


def _format_cli_exception(exc: BaseException) -> str:
    """Format an exception with subprocess stdout/stderr when available."""
    if not isinstance(exc, subprocess.CalledProcessError):
        return str(exc)

    lines = [
        "Command failed.",
        f"Command: {_format_command_for_cli(exc.cmd)}",
        f"Exit code: {exc.returncode}",
    ]
    stdout_text = _truncate_cli_error_stream(_decode_cli_error_stream(exc.output))
    stderr_text = _truncate_cli_error_stream(_decode_cli_error_stream(exc.stderr))
    if stdout_text:
        lines.extend(["", "stdout:", stdout_text.rstrip()])
    if stderr_text:
        lines.extend(["", "stderr:", stderr_text.rstrip()])
    if not stdout_text and not stderr_text:
        lines.append("No stdout or stderr was captured.")
    return "\n".join(lines)
