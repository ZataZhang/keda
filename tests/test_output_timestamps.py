"""行首时间戳格式化工具（``core/shared/interfaces/output_timestamps``）的单元测试。

这些格式化器是 per-Issue 实时输出（Issue #223）与终端实时视图共用的展示层
工具：行为契约是「每个非空物理行恰好一个前缀，空行与行中碎片不加」。
"""

from __future__ import annotations

import re

from backend.core.shared.interfaces.output_timestamps import (
    TimestampedStreamFormatter,
    format_timestamped_line,
)

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def test_format_timestamped_line_adds_timestamp_prefix() -> None:
    """整行文本在行首得到 ``[HH:MM:SS] `` 前缀。"""
    result = format_timestamped_line("test output\n")

    assert _TIMESTAMP_PREFIX.match(result)
    assert result.endswith("test output\n")


def test_format_timestamped_line_handles_leading_newline() -> None:
    """前导空行保持裸行，时间戳落在真正有内容的那一行行首。"""
    result = format_timestamped_line("\n[agent tool] Read\n")

    assert result.startswith("\n")
    assert _TIMESTAMP_PREFIX.match(result[len("\n") :])
    assert "[agent tool] Read" in result


def test_format_timestamped_line_empty_string() -> None:
    """空串不产生任何前缀。"""
    assert format_timestamped_line("") == ""


def test_format_timestamped_line_multi_line_text() -> None:
    """多行文本的每个非空行各有一个前缀，空行不产生只有前缀的行。"""
    result = format_timestamped_line("first\n\nsecond\n")

    lines = result.splitlines()
    assert len(lines) == 3
    assert all(_TIMESTAMP_PREFIX.match(line) for line in lines if line)
    assert lines[1] == ""
    assert [line[len("[00:00:00] ") :] for line in lines] == ["first", "", "second"]


def test_timestamped_stream_formatter_keeps_chunks_on_same_line() -> None:
    """同一物理行被切成任意碎片时只在行首加一次时间戳。"""
    formatter = TimestampedStreamFormatter()

    first_line = "".join(
        formatter.format_chunk(chunk) for chunk in ("{", '"action"', ": true", "\n")
    )
    second_line = formatter.format_chunk('"next"')

    assert first_line.count("[") == 1
    assert first_line.endswith('{"action": true\n')
    assert second_line.count("[") == 1
    assert second_line.endswith('"next"')


def test_timestamped_stream_formatter_skips_empty_chunks_and_lone_newlines() -> None:
    """空碎片与纯换行碎片不应产生只有前缀的行。"""
    formatter = TimestampedStreamFormatter()

    assert formatter.format_chunk("") == ""
    assert formatter.format_chunk("\n\n") == "\n\n"
    assert _TIMESTAMP_PREFIX.match(formatter.format_chunk("after blank"))
