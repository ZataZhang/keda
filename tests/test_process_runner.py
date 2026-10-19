"""Tests for subprocess runner output handling."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.core.shared.models.agent_spec import CLAUDE_STREAM_JSON_PROTOCOL_ID
import backend.infrastructure.process_runner as process_runner_module
from backend.infrastructure.process_runner import (
    ClaudeStreamRenderer,
    CommandFailedError,
    SubprocessRunner,
    _terminate_process_tree,
    _with_available_own_command,
    run_filtered_claude_stream,
)


def _json_line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def test_output_protocol_routes_claude_stream_to_filtered_renderer(tmp_path: Path) -> None:
    """``output_protocol="claude-stream-json"`` 走流式渲染路径，其余走通用路径。"""
    text_event = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "approved"},
            },
        }
    )
    stop_event = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})
    mock_process = MagicMock()
    mock_process.stdout = iter([text_event, stop_event])
    mock_process.stdin = MagicMock()
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_process.poll.return_value = None
    mock_process.communicate.return_value = ("", "")

    runner = SubprocessRunner()
    with (
        patch("subprocess.Popen", return_value=mock_process) as popen_mock,
        patch(
            "backend.infrastructure.process_runner.run_filtered_claude_stream",
            wraps=run_filtered_claude_stream,
        ) as filtered_mock,
    ):
        streamed = runner.run(
            ["claude", "--output-format", "stream-json", "-p", "Review."],
            cwd=tmp_path,
            capture_output=True,
            timeout=900,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
        )
        plain = runner.run(
            ["codex", "exec", "prompt"],
            cwd=tmp_path,
            capture_output=True,
            timeout=900,
        )

    # 流式协议进入 run_filtered_claude_stream；plain 协议不走。
    assert filtered_mock.call_count == 1
    assert popen_mock.call_count >= 1
    assert streamed.stdout == "approved\n"
    assert streamed.output_protocol == CLAUDE_STREAM_JSON_PROTOCOL_ID
    assert plain.output_protocol == "plain"


def test_claude_stream_renderer_suppresses_noisy_events() -> None:
    """Renderer should avoid dumping thinking, signatures, and tool results."""
    renderer = ClaudeStreamRenderer()

    thinking_line = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hidden"},
            },
        }
    )
    signature_line = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "signature_delta", "signature": "secret"},
            },
        }
    )
    tool_result_line = _json_line(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "large file content",
                    }
                ]
            },
        }
    )

    assert renderer.render_line(thinking_line) == ""
    assert renderer.render_line(signature_line) == ""
    assert renderer.render_line(tool_result_line) == ""


def test_claude_stream_renderer_prints_concise_progress() -> None:
    """Renderer should keep useful tool, text, and error progress."""
    renderer = ClaudeStreamRenderer()
    tool_line = _json_line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/example.py", "limit": 20},
                    }
                ]
            },
        }
    )
    text_line = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "done"},
            },
        }
    )
    stop_line = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})
    error_line = _json_line({"type": "result", "is_error": True, "result": "API Error: 400"})

    assert renderer.render_line(tool_line) == ("\n[agent tool] Read: /tmp/example.py limit=20\n")
    assert renderer.render_line(tool_line) == ""
    assert renderer.render_line(text_line) == "done"
    assert renderer.render_line(stop_line) == "\n"
    assert renderer.render_line(error_line) == "\n[agent error] API Error: 400\n"


def test_claude_stream_renderer_output_is_transcript_safe() -> None:
    """Collected Claude output should not include raw stream-json noise."""
    renderer = ClaudeStreamRenderer()
    lines = [
        _json_line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "hidden"},
                },
            }
        ),
        _json_line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "visible"},
                },
            }
        ),
        _json_line({"type": "stream_event", "event": {"type": "message_stop"}}),
    ]

    transcript = "".join(rendered for line in lines if (rendered := renderer.render_line(line)))

    assert transcript == "visible\n"


def test_transcript_runner_builds_claude_command() -> None:
    """Transcript runner should build claude deliberation command."""
    from pathlib import Path

    from backend.core.shared.models.agent_runner import AppConfig
    from backend.core.use_cases.agent_invocation import build_agent_invocation

    invocation = build_agent_invocation("claude", "deliberate", "hello", Path("/tmp"), AppConfig())
    cmd = invocation.argv
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    # 提示词在 argv 尾部；协议层（claude-stream-json）执行时再剥离改走 stdin。
    assert cmd[-1] == "hello"
    assert invocation.prompt_delivery == "argv_tail"


def test_transcript_runner_builds_kimi_command() -> None:
    """Transcript runner should build kimi deliberation command."""
    from pathlib import Path

    from backend.core.shared.models.agent_runner import AppConfig
    from backend.core.use_cases.agent_invocation import build_agent_invocation

    invocation = build_agent_invocation("kimi", "deliberate", "hello", Path("/tmp"), AppConfig())
    assert invocation.argv == ("kimi", "--input-format", "text")
    assert invocation.prompt_delivery == "stdin"


def test_transcript_runner_builds_codex_command() -> None:
    """Transcript runner should build codex deliberation command."""
    from pathlib import Path

    from backend.core.shared.models.agent_runner import AppConfig
    from backend.core.use_cases.agent_invocation import build_agent_invocation

    invocation = build_agent_invocation(
        "codex", "deliberate", "hello", Path("/tmp/relative/workspace"), AppConfig()
    )
    cmd = invocation.argv
    assert cmd[0] == "codex"
    assert "--cd" in cmd
    assert "read-only" in cmd
    assert "hello" not in cmd
    assert invocation.prompt_delivery == "stdin"
    assert Path(cmd[cmd.index("--cd") + 1]).is_absolute()


def test_run_filtered_claude_stream_logs_structured_events(tmp_path: Path) -> None:
    """run_filtered_claude_stream should log tool/result/error events."""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    # Create mock process that yields structured events
    tool_event = _json_line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.py"},
                    }
                ]
            },
        }
    )
    result_event = _json_line({"type": "result", "is_error": False, "result": "done"})

    mock_process = MagicMock()
    mock_process.stdout = iter([tool_event, result_event])
    mock_process.wait.return_value = 0
    mock_process.stdin = MagicMock()

    log_records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record)

    logger = logging.getLogger("app")
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.INFO)

    try:
        with patch("subprocess.Popen", return_value=mock_process):
            run_filtered_claude_stream(
                ["claude", "--output-format", "stream-json"],
                cwd=tmp_path,
                timeout=None,
                collect_stdout=True,
            )

        # Check that structured events were logged
        logged_messages = [r.getMessage() for r in log_records]
        assert any("[agent tool]" in msg for msg in logged_messages)
        assert any("[agent result]" in msg for msg in logged_messages)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def test_run_filtered_claude_stream_stdout_timestamp_does_not_split_text_delta(
    tmp_path: Path,
    capsys,
) -> None:
    """Live Claude output should prefix lines, not every text_delta chunk."""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    text_events = [
        _json_line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text_chunk},
                },
            }
        )
        for text_chunk in ("{", '"action"', ": true", "\n")
    ]
    stop_event = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})

    mock_process = MagicMock()
    mock_process.stdout = iter([*text_events, stop_event])
    mock_process.wait.return_value = 0
    mock_process.stdin = MagicMock()

    with patch("subprocess.Popen", return_value=mock_process):
        completed_process = run_filtered_claude_stream(
            ["claude", "--output-format", "stream-json"],
            cwd=tmp_path,
            timeout=None,
            collect_stdout=True,
        )

    captured_output = capsys.readouterr().out
    assert captured_output.count("[") == 1
    assert captured_output.endswith('{"action": true\n\n')
    assert completed_process.stdout == '{"action": true\n\n'


def test_run_filtered_claude_stream_buffers_text_delta(tmp_path: Path) -> None:
    """run_filtered_claude_stream should buffer text_delta and log on message_stop."""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    text_event = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello "},
            },
        }
    )
    text_event2 = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "world"},
            },
        }
    )
    stop_event = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})

    mock_process = MagicMock()
    mock_process.stdout = iter([text_event, text_event2, stop_event])
    mock_process.wait.return_value = 0
    mock_process.stdin = MagicMock()

    log_records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record)

    logger = logging.getLogger("app")
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.INFO)

    try:
        with patch("subprocess.Popen", return_value=mock_process):
            run_filtered_claude_stream(
                ["claude", "--output-format", "stream-json"],
                cwd=tmp_path,
                timeout=None,
                collect_stdout=True,
            )

        # Check that text was logged after message_stop
        logged_messages = [r.getMessage() for r in log_records]
        assert any("hello world" in msg for msg in logged_messages)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def test_run_filtered_claude_stream_output_sink_preserves_newlines(
    tmp_path: Path,
) -> None:
    """Rendered Claude newlines reach the sink as raw text, unprefixed."""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    text_event = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        }
    )
    stop_event = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})

    mock_process = MagicMock()
    mock_process.stdout = iter([text_event, stop_event])
    mock_process.wait.return_value = 0
    mock_process.stdin = MagicMock()
    streamed_output_chunks: list[str] = []

    with patch("subprocess.Popen", return_value=mock_process):
        completed_process = run_filtered_claude_stream(
            ["claude", "--output-format", "stream-json"],
            cwd=tmp_path,
            timeout=None,
            collect_stdout=True,
            output_sink=streamed_output_chunks.append,
        )

    # 生产者交出的必须是可读原文：行首时间戳属于消费侧（per-Issue 路由 sink），
    # 否则合议 workspace 文件等原文消费方会被动带上时间线。
    assert streamed_output_chunks == ["hello", "\n"]
    assert completed_process.stdout == "hello\n"


def test_run_filtered_claude_stream_output_sink_keeps_tool_lines_raw(
    tmp_path: Path,
) -> None:
    """sink 中的工具调用行必须是渲染原文，不带 ``[HH:MM:SS]`` 前缀。"""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    tool_event = _json_line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ]
            },
        }
    )

    mock_process = MagicMock()
    mock_process.stdout = iter([tool_event])
    mock_process.wait.return_value = 0
    mock_process.stdin = MagicMock()
    streamed_output_chunks: list[str] = []

    with patch("subprocess.Popen", return_value=mock_process):
        completed_process = run_filtered_claude_stream(
            ["claude", "--output-format", "stream-json"],
            cwd=tmp_path,
            timeout=None,
            collect_stdout=True,
            output_sink=streamed_output_chunks.append,
        )

    assert "".join(streamed_output_chunks) == "\n[agent tool] Bash: ls\n"
    assert completed_process.stdout == "\n[agent tool] Bash: ls\n"


def _stderr_writer_command(*, line_count: int, exit_code: int) -> list[str]:
    """构造一个只往 stderr 逐行写 ``err-<n>`` 然后以指定退出码结束的真实子进程命令。"""
    script = (
        "import sys\n"
        f"for index in range({line_count}):\n"
        "    sys.stderr.write(f'err-{index}\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, "-c", script]


def test_run_filtered_claude_stream_returns_stderr_tail_and_echoes_without_sink(
    tmp_path: Path,
    capsys,
) -> None:
    """没有 display_sink 时 stderr 照旧回显到终端，同时带回结果供失败原因使用。"""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    completed_process = run_filtered_claude_stream(
        _stderr_writer_command(line_count=3, exit_code=1),
        cwd=tmp_path,
        timeout=30,
        collect_stdout=True,
    )

    assert completed_process.returncode == 1
    assert completed_process.stderr == "err-0\nerr-1\nerr-2\n"
    assert capsys.readouterr().err == "err-0\nerr-1\nerr-2\n"


def test_run_filtered_claude_stream_display_sink_still_receives_stderr_lines(
    tmp_path: Path,
    capsys,
) -> None:
    """有 display_sink 时 stderr 只交给回调、不回显终端，结果里仍保留尾部。"""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    displayed_stderr_lines: list[str] = []

    completed_process = run_filtered_claude_stream(
        _stderr_writer_command(line_count=2, exit_code=0),
        cwd=tmp_path,
        timeout=30,
        display_sink=displayed_stderr_lines.append,
    )

    assert displayed_stderr_lines == ["err-0\n", "err-1\n"]
    assert completed_process.stderr == "err-0\nerr-1\n"
    assert capsys.readouterr().err == ""


def test_run_filtered_claude_stream_stderr_tail_is_bounded(tmp_path: Path) -> None:
    """stderr 只保留最后 ``_STDERR_TAIL_MAX_LINES`` 行，长跑进程不会撑大内存。"""
    from backend.infrastructure import process_runner_streams
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    emitted_line_count = process_runner_streams._STDERR_TAIL_MAX_LINES + 50

    completed_process = run_filtered_claude_stream(
        _stderr_writer_command(line_count=emitted_line_count, exit_code=0),
        cwd=tmp_path,
        timeout=30,
        display_sink=lambda _line: None,
    )

    kept_lines = completed_process.stderr.splitlines()
    assert len(kept_lines) == process_runner_streams._STDERR_TAIL_MAX_LINES
    assert kept_lines[0] == "err-50"
    assert kept_lines[-1] == f"err-{emitted_line_count - 1}"


def test_runner_run_failure_carries_stream_stderr_into_command_failed_error(
    tmp_path: Path,
) -> None:
    """agent 流式调用非零退出时，失败异常要带上 stderr（Issue 评论里的失败原因出处）。"""
    runner = SubprocessRunner()

    with pytest.raises(subprocess.CalledProcessError) as failure_info:
        runner.run(
            _stderr_writer_command(line_count=1, exit_code=7),
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
            check=True,
        )

    assert failure_info.value.returncode == 7
    assert failure_info.value.stderr == "err-0\n"


_QUOTA_LIMIT_TEXT = "You've reached your daily usage limit for Chat. Come back tomorrow."


def _whole_assistant_text_event(text: str) -> str:
    """qoder 风格：整条 assistant 文本消息，没有 stream_event 的 text_delta。"""
    return _json_line(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


def test_renderer_surfaces_last_assistant_text_when_error_result_has_no_text() -> None:
    """失败的 result 不带文本时，带出最近一条 assistant 文本（额度/鉴权原因所在）。"""
    renderer = ClaudeStreamRenderer()

    assert renderer.render_line(_whole_assistant_text_event(_QUOTA_LIMIT_TEXT)) == ""
    error_output = renderer.render_line(
        _json_line({"type": "result", "subtype": "error_during_execution"})
    )

    assert error_output == f"\n[agent error] {_QUOTA_LIMIT_TEXT}\n"


def test_renderer_error_result_without_any_text_falls_back_to_subtype() -> None:
    """既无 result 文本也无 assistant 文本时，至少带出 subtype，不再整行丢弃。"""
    renderer = ClaudeStreamRenderer()

    error_output = renderer.render_line(
        _json_line({"type": "result", "subtype": "error_during_execution"})
    )

    assert error_output == "\n[agent error] error_during_execution\n"


def test_renderer_success_result_output_is_unchanged_by_assistant_text() -> None:
    """成功路径不变：assistant 文本不被渲染，成功 result 仍按原样输出。"""
    renderer = ClaudeStreamRenderer()

    assert renderer.render_line(_whole_assistant_text_event("working on it")) == ""
    success_output = renderer.render_line(
        _json_line({"type": "result", "subtype": "success", "result": "done"})
    )

    assert success_output == "\n[agent result] done\n"


def test_runner_run_quota_failure_is_classified_as_provider_capacity(tmp_path: Path) -> None:
    """qoder 额度耗尽（stdout 整条文本 + 无文本 error result + 退出码 1）要能被判成容量错误。"""
    from backend.core.use_cases.agent_runner_failure import (
        is_provider_capacity_failure,
        is_transient_failure,
    )

    stream_lines = [
        _whole_assistant_text_event(_QUOTA_LIMIT_TEXT),
        _json_line({"type": "result", "subtype": "error_during_execution"}),
    ]
    script = (
        "import sys\n"
        f"for stream_line in {stream_lines!r}:\n"
        "    sys.stdout.write(stream_line)\n"
        "    sys.stdout.flush()\n"
        "sys.exit(1)\n"
    )

    with pytest.raises(subprocess.CalledProcessError) as failure_info:
        SubprocessRunner().run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
            check=True,
        )

    assert _QUOTA_LIMIT_TEXT in str(failure_info.value.output)
    assert is_provider_capacity_failure(failure_info.value)
    assert not is_transient_failure(failure_info.value)


def test_relay_process_stdout_output_sink_preserves_line_boundaries() -> None:
    """Non-Claude transcript streaming should keep stdout line endings."""
    from backend.engines.agent_runner.output_protocols.plain import _relay_process_stdout

    mock_process = MagicMock()
    mock_process.stdout = iter(["first\n", "second\n"])
    mock_process.stderr = iter([])
    mock_process.wait.return_value = 0
    streamed_output_chunks: list[str] = []

    stdout_text = _relay_process_stdout(
        mock_process,
        output_sink=streamed_output_chunks.append,
    )

    assert stdout_text == "first\nsecond\n"
    # 每块是一整行原文：行首时间戳由消费侧添加，生产者不得抢先格式化。
    assert streamed_output_chunks == ["first\n", "second\n"]


def test_subprocess_runner_non_claude_path_streams_via_pty(tmp_path: Path) -> None:
    """Non-Claude streaming runs under a PTY: output is collected and logged.

    The PTY makes the child line-buffer (a pipe would block-buffer and hide
    progress), so a real subprocess is used here rather than mocking Popen.
    """
    import sys

    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()

    log_records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record)

    logger = logging.getLogger("app")
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.INFO)

    try:
        result = runner.run(
            [sys.executable, "-c", "print('output line 1'); print('output line 2')"],
            cwd=tmp_path,
            capture_output=False,
            check=False,
        )

        # Output is collected from the PTY master.
        assert "output line 1" in result.stdout
        assert "output line 2" in result.stdout

        # And mirrored to the logger (line-buffered) when there is no sink.
        logged_messages = [r.getMessage() for r in log_records]
        assert any("output line 1" in msg for msg in logged_messages)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def test_subprocess_runner_pty_routes_output_to_sink(tmp_path: Path) -> None:
    """When a sink is provided, PTY chunks go to the sink (for per-Issue panels)."""
    import sys

    from backend.infrastructure.process_runner import SubprocessRunner

    chunks: list[str] = []
    SubprocessRunner().run(
        [sys.executable, "-c", "print('via-sink')"],
        cwd=tmp_path,
        capture_output=False,
        check=False,
        output_sink=chunks.append,
    )
    assert any("via-sink" in chunk for chunk in chunks)


def test_subprocess_runner_pty_sink_chunks_are_raw_lines(tmp_path: Path) -> None:
    """PTY 路径交给 sink 的是原文行，时间戳由消费侧（路由 sink）添加。"""
    import sys

    from backend.infrastructure.process_runner import SubprocessRunner

    chunks: list[str] = []
    SubprocessRunner().run(
        [sys.executable, "-c", "print('timed-line')"],
        cwd=tmp_path,
        capture_output=False,
        check=False,
        output_sink=chunks.append,
    )
    joined = "".join(chunks)
    assert "timed-line" in joined
    assert not re.search(r"\[\d{2}:\d{2}:\d{2}\]", joined)


def test_pi_relay_events_sink_delivers_raw_chunks() -> None:
    """pi 事件流的 sink 收到渲染原文：行中 delta 不被切断，也不加时间戳。"""
    from backend.engines.agent_runner.output_protocols.pi_json_lines import _relay_events
    from backend.infrastructure.agent_stream_usage import StreamUsageCollector

    event_lines = [
        _json_line({"type": "tool_execution", "tool": "Bash"}),
        _json_line({"type": "message_update", "delta": {"text": "he"}}),
        _json_line({"type": "message_update", "delta": {"text": "llo\n"}}),
    ]
    mock_process = MagicMock()
    mock_process.stdout = iter(event_lines)
    mock_process.wait.return_value = 0
    streamed_output_chunks: list[str] = []

    collected = _relay_events(
        mock_process,
        collect_stdout=True,
        output_sink=streamed_output_chunks.append,
        usage_collector=StreamUsageCollector(),
    )

    assert "".join(streamed_output_chunks) == collected
    assert collected == "\n[agent tool] Bash\nhello\n"


def test_subprocess_runner_claude_capture_uses_filtered_stream(
    tmp_path: Path,
) -> None:
    """Claude capture mode should return rendered output instead of raw JSON."""
    from backend.infrastructure.process_runner import SubprocessRunner

    text_event = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "approved"},
            },
        }
    )
    stop_event = _json_line({"type": "stream_event", "event": {"type": "message_stop"}})
    mock_process = MagicMock()
    mock_process.stdout = iter([text_event, stop_event])
    mock_process.stdin = MagicMock()
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_process.poll.return_value = None

    runner = SubprocessRunner()
    with (
        patch("subprocess.Popen", return_value=mock_process) as popen_mock,
        patch("subprocess.run") as run_mock,
    ):
        result = runner.run(
            ["claude", "--output-format", "stream-json", "-p", "Review."],
            cwd=tmp_path,
            capture_output=True,
            timeout=900,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
        )

    assert result.stdout == "approved\n"
    assert result.stderr == ""
    assert popen_mock.called
    assert not run_mock.called


def test_subprocess_runner_captured_timeout_path_uses_popen(
    tmp_path: Path,
) -> None:
    """Captured non-Claude commands with timeout should use watchdog Popen path."""
    from backend.infrastructure.process_runner import SubprocessRunner

    mock_process = MagicMock()
    mock_process.communicate.return_value = ("raw output\n", "raw error\n")
    mock_process.returncode = 0
    mock_process.poll.return_value = None

    runner = SubprocessRunner()
    with (
        patch("subprocess.Popen", return_value=mock_process) as popen_mock,
        patch("subprocess.run") as run_mock,
    ):
        result = runner.run(
            ["some", "command"],
            cwd=tmp_path,
            capture_output=True,
            check=False,
            timeout=900,
        )

    assert result.stdout == "raw output\n"
    assert result.stderr == "raw error\n"
    assert popen_mock.called
    assert not run_mock.called


def test_process_watchdog_logs_heartbeat_and_kills_on_timeout() -> None:
    """Process watchdog should log progress and terminate after timeout."""
    from backend.infrastructure import process_runner

    class _NeverStoppedEvent:
        def wait(self, timeout: float) -> bool:  # noqa: ARG002
            return False

    mock_process = MagicMock()
    mock_process.poll.return_value = None
    # 真实 pid 才读得出真实进程组：替身 pid 会让身份判定退回无校验路径。
    mock_process.pid = os.getpid()
    watchdog = process_runner._ProcessWatchdog(
        mock_process,
        ["slow", "command"],
        timeout=2,
        heartbeat_seconds=1,
        base_label="Command",
    )
    watchdog._started_at = 0
    watchdog._stop_event = _NeverStoppedEvent()

    with (
        patch.object(process_runner.time, "monotonic", side_effect=[1.1, 2.1]),
        patch.object(process_runner, "logger") as logger_mock,
        patch.object(process_runner, "_terminate_process_tree") as terminate_mock,
    ):
        watchdog._run()

    logger_mock.info.assert_any_call(
        "%s still running after %ds: %s",
        "Command",
        1,
        "slow command",
    )
    logger_mock.error.assert_called_once_with(
        "%s timed out after %ds; terminating: %s",
        "Command",
        2,
        "slow command",
    )
    # 拆除必须带上创建时刻读到的身份，不能只按 pid 现取一次进程组。
    assert watchdog._expected_process_group == os.getpgid(os.getpid())
    assert watchdog._expected_process_started_at is not None
    terminate_mock.assert_called_once_with(
        mock_process,
        expected_process_group=watchdog._expected_process_group,
        expected_process_started_at=watchdog._expected_process_started_at,
    )


def test_process_watchdog_includes_context_label_in_logs() -> None:
    """Process watchdog should include the context label in heartbeat logs."""
    from backend.infrastructure import process_runner

    class _NeverStoppedEvent:
        def wait(self, timeout: float) -> bool:  # noqa: ARG002
            return False

    mock_process = MagicMock()
    mock_process.poll.return_value = None
    watchdog = process_runner._ProcessWatchdog(
        mock_process,
        ["slow", "command"],
        timeout=2,
        heartbeat_seconds=1,
        base_label="Claude stream",
        context_label="Issue #23: https://github.com/example/repo/issues/23",
    )
    watchdog._started_at = 0
    watchdog._stop_event = _NeverStoppedEvent()

    # 必须把 _terminate_process_tree 也挡掉。本用例考的是日志标签，但超时分支会
    # 顺带调用它，而这里传进去的是 MagicMock：`mock.pid` 不是整数，os.getpgid()
    # 会对参数调 __index__，MagicMock 的 __index__ 默认返回 1，于是拿到 1 号进程组
    # 并对它发 SIGKILL。macOS 上被 PermissionError 兜住所以一直没暴露，Linux CI 上
    # 则真的把 runner 打没，表现为任务永远不结束且日志完全拿不到。
    with (
        patch.object(process_runner.time, "monotonic", side_effect=[1.1, 2.1]),
        patch.object(process_runner, "logger") as logger_mock,
        patch.object(process_runner, "_terminate_process_tree"),
    ):
        watchdog._run()

    logger_mock.info.assert_any_call(
        "%s still running after %ds: %s",
        "Claude stream (Issue #23: https://github.com/example/repo/issues/23)",
        1,
        "slow command",
    )
    logger_mock.error.assert_called_once_with(
        "%s timed out after %ds; terminating: %s",
        "Claude stream (Issue #23: https://github.com/example/repo/issues/23)",
        2,
        "slow command",
    )


def test_process_watchdog_kills_on_inactivity_timeout() -> None:
    """Watchdog should kill a process that produces no output."""
    from backend.infrastructure import process_runner

    class _NeverStoppedEvent:
        def wait(self, timeout: float) -> bool:  # noqa: ARG002
            return False

    mock_process = MagicMock()
    mock_process.poll.return_value = None
    # 真实 pid 才读得出真实进程组：替身 pid 会让身份判定退回无校验路径。
    mock_process.pid = os.getpid()
    watchdog = process_runner._ProcessWatchdog(
        mock_process,
        ["slow", "command"],
        timeout=None,
        inactivity_timeout_seconds=2,
        heartbeat_seconds=10,
        base_label="Command",
    )
    watchdog._started_at = 0
    watchdog._last_output_at = 0
    watchdog._stop_event = _NeverStoppedEvent()

    with (
        patch.object(process_runner.time, "monotonic", side_effect=[3.0, 3.0]),
        patch.object(process_runner, "_terminate_process_tree") as terminate_mock,
    ):
        watchdog._run()

    # 拆除必须带上创建时刻读到的身份，不能只按 pid 现取一次进程组。
    assert watchdog._expected_process_group == os.getpgid(os.getpid())
    assert watchdog._expected_process_started_at is not None
    terminate_mock.assert_called_once_with(
        mock_process,
        expected_process_group=watchdog._expected_process_group,
        expected_process_started_at=watchdog._expected_process_started_at,
    )


def test_process_watchdog_does_not_kill_on_inactivity_when_output_is_active() -> None:
    """Watchdog should not kill a process that keeps producing output."""
    from backend.infrastructure import process_runner

    mock_process = MagicMock()
    mock_process.poll.return_value = None
    watchdog = process_runner._ProcessWatchdog(
        mock_process,
        ["slow", "command"],
        timeout=None,
        inactivity_timeout_seconds=2,
        heartbeat_seconds=10,
        base_label="Command",
    )
    watchdog._started_at = 0
    watchdog._last_output_at = 0
    stop_event = MagicMock()
    stop_event.wait.side_effect = [False, True]
    watchdog._stop_event = stop_event

    with patch.object(process_runner.time, "monotonic", side_effect=[1.5, 1.5, 1.5]):
        watchdog.note_output()
        watchdog._run()

    mock_process.kill.assert_not_called()


def test_subprocess_runner_kills_silent_process_on_inactivity_timeout(
    tmp_path: Path,
) -> None:
    """A silent process should be killed by inactivity timeout."""
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()

    with pytest.raises(subprocess.TimeoutExpired):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            capture_output=True,
            timeout=3600,
            inactivity_timeout=1,
        )


def test_captured_timeout_exception_carries_partial_output(tmp_path: Path) -> None:
    """墙钟超时被杀时,已收到的输出必须挂在异常上。

    捕获模式下调用方拿不到任何流式输出,如果超时把已收集的 stdout 丢掉,一次
    "跑了半小时被杀"就只剩一行 timed out——比如 verifier agent 被杀,操作者看
    不到它判到哪一步。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()
    script = "import time\nprint('checked rv-1', flush=True)\ntime.sleep(60)\n"

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        runner.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            timeout=2,
        )

    assert "checked rv-1" in str(exc_info.value.output or "")


def test_inactivity_timeout_exception_carries_partial_output(tmp_path: Path) -> None:
    """静默期超时走另一条读取路径,同样必须带回部分输出。"""
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()
    script = "import time\nprint('checked rv-1', flush=True)\ntime.sleep(60)\n"

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        runner.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            timeout=3600,
            inactivity_timeout=2,
        )

    assert "checked rv-1" in str(exc_info.value.output or "")


def test_pty_stream_timeout_exception_carries_partial_output(tmp_path: Path) -> None:
    """PTY 流式路径(kimi / codex)被超时杀掉时也必须带回部分输出。

    这条路径的失败摘要只剩一句 "No output was captured before the kill."——
    Issue 的 Attempt History 因此把"卡在派子 agent 之前"渲染成"什么都没输出",
    排障只能回去翻 runner 的文件日志。
    """
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()
    script = "import time\nprint('checked rv-1', flush=True)\ntime.sleep(60)\n"

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        runner.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=False,
            timeout=3600,
            inactivity_timeout=2,
        )

    assert "checked rv-1" in str(exc_info.value.output or "")


def test_claude_stream_timeout_exception_carries_partial_output(tmp_path: Path) -> None:
    """Claude stream-json 路径超时被杀时,渲染出的输出必须挂在异常上。"""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    text_event = _json_line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "checked rv-1\n"},
            },
        }
    )
    script = f"import time\nprint({text_event.strip()!r}, flush=True)\ntime.sleep(60)\n"

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_filtered_claude_stream(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            timeout=3600,
            inactivity_timeout=2,
            collect_stdout=True,
        )

    assert "checked rv-1" in str(exc_info.value.output or "")


def test_claude_stream_honors_takeover_cancel_check(tmp_path: Path) -> None:
    """claude-stream-json 的捕获路径也必须回收目标进程组并抛取消异常。"""
    import time

    runner = SubprocessRunner()
    started_at = time.monotonic()
    script = "import time\nprint('started', flush=True)\ntime.sleep(60)\n"

    with pytest.raises(InterruptedError, match="takeover cancel request"):
        runner.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
            cancel_check=lambda: time.monotonic() - started_at >= 1,
        )


def _pid_alive(pid: int) -> bool:
    """进程是否仍在；``PermissionError`` 说明进程存在但不属于当前用户。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_pid_gone(pid: int, timeout_seconds: float = 10.0) -> bool:
    """在限定窗口内确认进程已退出并被回收（直接子进程需要 reap，僵尸不算消失）。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return True
        except ChildProcessError:
            return True  # 已由父进程（或被收养的 init）回收。
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def test_takeover_cancel_kills_only_the_target_process_group(tmp_path: Path) -> None:
    """取消只终结目标进程组：孙进程一并回收，旁观进程与 runner 自己存活。

    这是 PRD 不变量 2 的回归面。跨进程 oracle
    （``scripts/rv1_takeover_handoff_probe.py``）已经真跑过一次，但回归套件必须自己
    守住这条边界——把 ``killpg`` 的范围写错（杀整棵进程树之外的东西、或只杀直接子
    进程）时必须有测试变红，而不是等 CI 上没人跑的浏览器层去发现。
    """
    runner = SubprocessRunner()
    pid_file = tmp_path / "pids.json"
    script = (
        "import json, os, subprocess, sys, time\n"
        "outside = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(600)'],\n"
        "    start_new_session=True,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        "    json.dump(\n"
        "        {\n"
        "            'pid': os.getpid(),\n"
        "            'pgid': os.getpgid(0),\n"
        "            'grandchild': grandchild.pid,\n"
        "            'outside': outside.pid,\n"
        "        },\n"
        "        handle,\n"
        "    )\n"
        "time.sleep(600)\n"
    )
    started_at = time.monotonic()
    identities: dict[str, int] = {}

    def _cancel_when_identities_are_known() -> bool:
        """子进程把三类 PID 都写盘之后才请求取消，否则断言对象还不存在。"""
        if time.monotonic() - started_at < 0.5:
            return False
        try:
            return bool(json.loads(pid_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return False

    try:
        with pytest.raises(InterruptedError, match="takeover cancel request"):
            runner.run(
                [sys.executable, "-c", script, str(pid_file)],
                cwd=tmp_path,
                capture_output=True,
                timeout=30,
                cancel_check=_cancel_when_identities_are_known,
            )
        identities = json.loads(pid_file.read_text(encoding="utf-8"))

        # 子进程确实落在自己的进程组里（组 id == pid），否则"只杀这一组"无从谈起。
        assert identities["pgid"] == identities["pid"]
        assert identities["pgid"] != os.getpgid(0)
        # 目标组整体消失：直接子进程与它拉起的孙进程都被终止并回收。
        assert _wait_pid_gone(identities["pid"]) is True
        assert _wait_pid_gone(identities["grandchild"]) is True
        # 旁观者（另一进程组）没被牵连——daemon / 其他 Issue 的进程走的是这条边界。
        assert _pid_alive(identities["outside"]) is True
        # runner 自己（测试进程）当然还活着。
        assert _pid_alive(os.getpid()) is True
    finally:
        for key in ("outside", "grandchild", "pid"):
            process_id = identities.get(key)
            if process_id is not None and _pid_alive(process_id):
                try:
                    os.kill(process_id, signal.SIGKILL)
                except OSError:
                    pass


def test_process_watchdog_cancel_classification_wins_over_timeout() -> None:
    """同一 tick 里取消与超时都置位时，必须按人工接管分类（Issue #278）。

    若 TimeoutExpired 抢先抛出，上层会走 verifier 超时回退路径，错失交班：
    Issue 不进 agent/review、不写交接评论，把"人停掉了验证"误报成"验证自己超时"。
    """
    watchdog = process_runner_module._ProcessWatchdog(
        MagicMock(),
        ["verifier", "cli"],
        timeout=30,
        heartbeat_seconds=1,
        base_label="Command",
    )
    watchdog._timed_out = True
    watchdog._cancelled = True

    watchdog.raise_if_timed_out()  # 取消在场时不得抛 TimeoutExpired。
    with pytest.raises(InterruptedError, match="takeover cancel request"):
        watchdog.raise_if_cancelled()

    # 纯超时（未取消）仍须按超时上报，保住既有停滞监督语义。
    watchdog._cancelled = False
    with pytest.raises(subprocess.TimeoutExpired):
        watchdog.raise_if_timed_out()


def test_subprocess_runner_keeps_active_process_alive(
    tmp_path: Path,
) -> None:
    """A process that keeps printing should not be killed by inactivity timeout."""
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()
    # 逐行 flush：stdout 接到管道时 Python 默认块缓冲，不 flush 的话子进程要等退出
    # 才把 tick 一次性倒出来，观测到的"静默"时长就由解释器启动速度决定——慢机器上
    # 光启动就逼近 1s，1s 阈值会被直接击杀，测试变成测机器快慢。flush 后留 2s 阈值
    # （脚本持续输出 2s > 2s 检查周期），断言仍是在测"持续输出能否续命"。
    script = (
        "import time\n"
        "for _ in range(20):\n"
        "    print('tick', flush=True)\n"
        "    time.sleep(0.1)\n"
    )

    result = runner.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
        inactivity_timeout=2,
    )

    assert result.return_code == 0
    assert result.stdout.count("tick") == 20


def test_subprocess_runner_replaces_invalid_utf8_in_captured_output(
    tmp_path: Path,
) -> None:
    """Binary bytes in stdout (e.g. PDF content in git diff) must not crash decoding."""
    import sys

    from backend.infrastructure.process_runner import SubprocessRunner

    emit_binary_stdout = (
        "import sys; sys.stdout.buffer.write(b'diff --git a/source.pdf\\n\\x78\\xda\\xff\\n')"
    )
    runner = SubprocessRunner()

    captured_result = runner.run(
        [sys.executable, "-c", emit_binary_stdout],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert captured_result.return_code == 0
    assert "diff --git a/source.pdf" in captured_result.stdout
    assert "�" in captured_result.stdout

    watchdog_result = runner.run(
        [sys.executable, "-c", emit_binary_stdout],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert watchdog_result.return_code == 0
    assert "�" in watchdog_result.stdout


def test_capture_output_true_not_polluted(tmp_path: Path) -> None:
    """capture_output=True should return raw stdout without timestamp prefix."""
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = "raw output\n"
    mock_completed.stderr = ""

    with patch("subprocess.run", return_value=mock_completed):
        result = runner.run(
            ["some", "command"],
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )

    # stdout should be raw, not timestamped
    assert result.stdout == "raw output\n"
    assert "[HH:MM:SS]" not in result.stdout


def test_command_failed_error_includes_stderr(tmp_path: Path) -> None:
    """stderr must appear in the string representation of the raised exception."""
    runner = SubprocessRunner()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        runner.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('failure detail'); sys.exit(1)",
            ],
            cwd=tmp_path,
            capture_output=True,
        )

    assert isinstance(exc_info.value, subprocess.CalledProcessError)
    message = str(exc_info.value)
    assert "failure detail" in message
    assert "--- stderr/stdout ---" in message


def test_command_failed_error_falls_back_to_stdout(tmp_path: Path) -> None:
    """stdout must be used when stderr is empty."""
    runner = SubprocessRunner()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        runner.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('stdout failure'); sys.exit(1)",
            ],
            cwd=tmp_path,
            capture_output=True,
        )

    message = str(exc_info.value)
    assert "stdout failure" in message
    assert "--- stderr/stdout ---" in message


def test_command_failed_error_truncates_long_output(tmp_path: Path) -> None:
    """Output longer than 4 KB must be truncated and annotated."""
    runner = SubprocessRunner()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        runner.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('x' * 5000); sys.exit(1)",
            ],
            cwd=tmp_path,
            capture_output=True,
        )

    message = str(exc_info.value)
    assert "... (truncated)" in message
    assert len(message) < 5500


def test_command_failed_error_no_output(tmp_path: Path) -> None:
    """The exception message must keep command and return code when no output exists."""
    runner = SubprocessRunner()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        runner.run(
            [sys.executable, "-c", "import sys; sys.exit(2)"],
            cwd=tmp_path,
            capture_output=True,
        )

    message = str(exc_info.value)
    assert "--- stderr/stdout ---" not in message
    assert "2" in message


def test_command_failed_error_preserves_attributes() -> None:
    """CommandFailedError must expose the standard CalledProcessError attributes."""
    exc = CommandFailedError(1, ["cmd"], output="out", stderr="err")

    assert exc.returncode == 1
    assert exc.cmd == ["cmd"]
    assert exc.output == "out"
    assert exc.stderr == "err"


def test_terminate_process_tree_signals_child_process_group() -> None:
    """终止超时命令时必须向子进程所在的进程组发信号，而不是只杀直接子进程。"""
    from backend.infrastructure import process_runner

    mock_process = MagicMock()
    mock_process.pid = 4242

    def _fake_getpgid(pid: int) -> int:
        return 4242 if pid == 4242 else 777

    with (
        patch.object(process_runner.os, "getpgid", side_effect=_fake_getpgid),
        patch.object(process_runner.os, "killpg") as killpg_mock,
    ):
        _terminate_process_tree(mock_process)

    killpg_mock.assert_called_once_with(4242, signal.SIGKILL)
    mock_process.kill.assert_not_called()


def test_terminate_process_tree_never_kills_runner_own_group() -> None:
    """子进程仍在 runner 自己的进程组里时只能杀直接子进程，否则 runner 会自杀。"""
    from backend.infrastructure import process_runner

    mock_process = MagicMock()
    mock_process.pid = 4242

    with (
        patch.object(process_runner.os, "getpgid", return_value=777),
        patch.object(process_runner.os, "killpg") as killpg_mock,
    ):
        _terminate_process_tree(mock_process)

    killpg_mock.assert_not_called()
    mock_process.kill.assert_called_once_with()


def test_terminate_process_tree_never_kills_init_group() -> None:
    """子进程的组 id 解析成 1 号组时只能杀直接子进程。

    1 号组属于 init，不可能是子进程独立建出来的组（那个组 id 等于子进程 pid）。
    pid 取到异常值时如果照发 SIGKILL，在 CI runner 上会直接把 runner 打没，
    表现为任务永远不结束且日志完全拿不到——本仓库实测过一次，
    定位花了七轮才收敛到单个用例。
    """
    from backend.infrastructure import process_runner

    mock_process = MagicMock()
    mock_process.pid = 1

    def _fake_getpgid(pid: int) -> int:
        return 1 if pid == 1 else 777

    with (
        patch.object(process_runner.os, "getpgid", side_effect=_fake_getpgid),
        patch.object(process_runner.os, "killpg") as killpg_mock,
    ):
        _terminate_process_tree(mock_process)

    killpg_mock.assert_not_called()
    mock_process.kill.assert_called_once_with()


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="进程组回收依赖 POSIX killpg")
def test_captured_timeout_reclaims_surviving_pipeline_member(tmp_path: Path) -> None:
    """超时命令留下的管道成员不得让 captured 路径永久阻塞。

    这是 daemon 卡死的回归测试：``bash -lc "sleep 30 | tee ..."`` 超时后 ``tee``
    仍持有继承来的 stdout 写端，只杀直接子进程会让 ``communicate()`` 永远读不到
    EOF，runner 线程无限期挂住（实测曾让 daemon 停止轮询任何 Issue）。
    """
    child_pid_path = tmp_path / "child-pid.txt"
    runner = SubprocessRunner()
    command = ["bash", "-lc", f"echo $$ > {child_pid_path}; sleep 30 | tee /dev/null"]
    run_outcomes: list[str] = []

    def _invoke_runner() -> None:
        try:
            runner.run(command, cwd=tmp_path, check=False, capture_output=True, timeout=1)
        except subprocess.TimeoutExpired:
            run_outcomes.append("timeout")
        except BaseException as unexpected_error:  # pragma: no cover - 失败时便于定位
            run_outcomes.append(f"error:{unexpected_error!r}")
        else:
            run_outcomes.append("returned")

    runner_thread = threading.Thread(target=_invoke_runner, daemon=True)
    runner_thread.start()
    runner_thread.join(timeout=20)

    assert not runner_thread.is_alive(), "超时命令仍在阻塞 runner：进程组没有被整组回收"
    assert run_outcomes == ["timeout"]

    leaked_group_id = int(child_pid_path.read_text(encoding="utf-8").strip())
    group_gone_deadline = time.monotonic() + 10
    while time.monotonic() < group_gone_deadline:
        try:
            os.killpg(leaked_group_id, 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"进程组 {leaked_group_id} 在超时后仍存活")


def test_captured_keyboard_interrupt_tears_down_process_group(tmp_path: Path) -> None:
    """Ctrl-C 也必须整组回收，且拆除带着创建时刻读到的身份。

    子进程改用独立进程组后，终端产生的 SIGINT 不会再自动送达子进程，
    所以拆除逻辑必须捕获 BaseException 而不只是 Exception。
    """
    from backend.infrastructure import process_runner

    with (
        patch.object(process_runner.subprocess, "Popen") as popen_mock,
        patch.object(process_runner, "_terminate_process_tree") as terminate_mock,
    ):
        fake_process = popen_mock.return_value
        # 真实 pid 才读得出真实进程组：替身的 pid 会让身份判定退回无校验路径。
        fake_process.pid = os.getpid()
        fake_process.communicate.side_effect = KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            process_runner._run_captured_process(["sleep", "30"], cwd=tmp_path, timeout=5)

    terminate_mock.assert_called_once()
    assert terminate_mock.call_args.args == (fake_process,)
    assert terminate_mock.call_args.kwargs["expected_process_group"] == os.getpgid(os.getpid())
    assert terminate_mock.call_args.kwargs["expected_process_started_at"] is not None


def test_takeover_termination_reuses_identity_captured_at_popen() -> None:
    """接管取消的两轮拆除都只认创建时刻的进程组，绝不再按 pid 取一次组。

    ``raise_if_cancelled`` 抛进 except 分支时 ``communicate()`` 已经把子进程回收，
    ``pid`` 随即释放；此时再 ``os.getpgid(process.pid)`` 读到的是**复用该 pid 的别的进程**，
    据此发 SIGKILL 就会打到无关进程组上（Issue #278 评审 finding）。
    """
    from backend.infrastructure import process_runner

    mock_process = MagicMock()
    mock_process.pid = 4242
    mock_process.poll.return_value = None

    with (
        patch.object(process_runner.os, "getpgid", return_value=4242) as getpgid_mock,
        patch.object(process_runner, "process_start_time", return_value=1234.5),
        patch.object(process_runner, "_terminate_process_tree") as terminate_mock,
    ):
        watchdog = process_runner._ProcessWatchdog(
            mock_process,
            ["verifier", "cli"],
            timeout=30,
            heartbeat_seconds=1,
            base_label="Command",
            cancel_check=lambda: True,
        )
        # 身份只在创建时读一次，不是每次拆除都读。
        assert getpgid_mock.call_count == 1
        assert watchdog._check_takeover_cancel() is True  # 第一轮：watchdog 命中取消
        assert watchdog.terminate() is not None  # 第二轮：except 分支的收尾拆除

    assert getpgid_mock.call_count == 1
    assert terminate_mock.call_count == 2
    for termination_call in terminate_mock.call_args_list:
        assert termination_call.args == (mock_process,)
        assert termination_call.kwargs == {
            "expected_process_group": 4242,
            "expected_process_started_at": 1234.5,
        }


def test_claude_stream_takeover_termination_carries_captured_identity(
    tmp_path: Path,
) -> None:
    """claude-stream 的取消收尾拆除同样不得回到"按 pid 现取进程组"的老形状。"""
    from backend.infrastructure import process_runner
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    class _InterruptingStdout:
        """流式读取一开头就被外部取消打断的 stdout 替身。"""

        def __iter__(self):
            raise KeyboardInterrupt

    mock_process = MagicMock()
    mock_process.pid = 4242
    mock_process.stdout = _InterruptingStdout()
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None

    with (
        patch.object(process_runner.subprocess, "Popen", return_value=mock_process),
        patch.object(process_runner.os, "getpgid", return_value=4242) as getpgid_mock,
        patch.object(process_runner, "process_start_time", return_value=1234.5),
        patch.object(process_runner, "_terminate_process_tree") as terminate_mock,
    ):
        with pytest.raises(KeyboardInterrupt):
            run_filtered_claude_stream(
                ["claude", "--output-format", "stream-json"],
                cwd=tmp_path,
                timeout=30,
            )
        # 抛错前 watchdog 已经构造完，getpgid 只在创建时刻被调用过一次。
        assert getpgid_mock.call_count == 1

    assert getpgid_mock.call_count == 1
    terminate_mock.assert_called_once_with(
        mock_process,
        expected_process_group=4242,
        expected_process_started_at=1234.5,
    )


def test_command_result_records_duration(tmp_path: Path) -> None:
    """CommandResult 必须带耗时，否则"哪条命令慢"没有数据源可记进 attempt 历史。"""
    runner = SubprocessRunner()

    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )

    assert result.return_code == 0
    assert result.duration_seconds >= 0.05


# ---------------------------------------------------------------------------
# Agent 子进程环境净化（PRD: P1-BUG-20260928-232844-agent-runner-child-env-sanitize）
# ---------------------------------------------------------------------------

_SANITIZE_DENYLIST_VARS = {
    "SERVER__PORT": "56469",
    "CODEBUDDY_SERVICE_PROXY_URL": "http://127.0.0.1:56469/internal/hooks/services/invoke",
    "CODEBUDDY_SESSION_ID": "session-abc",
    "CODEBUDDY_CONVERSATION_REQUEST_ID": "conv-abc",
    "CODEBUDDY_ROOT_REQUEST_ID": "root-abc",
    "CODEBUDDY_CONVERSATION_MESSAGE_ID": "msg-abc",
    "CODEBUDDY_PROJECT_DIR": "/Users/someone/code/keda",
    "CODEBUDDY_CURRENT_MODEL_ID": "glm-5.3-flash",
}

_SANITIZE_PASSTHROUGH_VARS = {
    "IAR_SANITIZE_PROBE_KEEP_ME": "keep-value-1",
    "IAR_SANITIZE_PROBE_API_KEY": "sk-probe-key",
}


def _inject_sanitize_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把名单变量与透传探针变量写入当前测试进程环境。"""
    for key, value in {**_SANITIZE_DENYLIST_VARS, **_SANITIZE_PASSTHROUGH_VARS}.items():
        monkeypatch.setenv(key, value)


def test_build_sanitized_child_env_removes_denylisted_vars_and_keeps_rest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """净化函数剔除全部名单变量、原样透传其余变量，并对每个被剔变量名告警一次。"""
    from backend.infrastructure import child_env as child_env_module
    from backend.infrastructure.child_env import (
        AGENT_CHILD_ENV_DENYLIST,
        build_sanitized_child_env,
    )

    assert set(AGENT_CHILD_ENV_DENYLIST) == set(_SANITIZE_DENYLIST_VARS)
    _inject_sanitize_env(monkeypatch)
    # 去重告警状态是进程级的，清空后才可断言「首次剔除即 WARNING」。
    monkeypatch.setattr(child_env_module, "_WARNED_DENYLIST_REMOVALS", set())

    with caplog.at_level(logging.WARNING, logger="backend.infrastructure.child_env"):
        child_env = build_sanitized_child_env()

    for key in _SANITIZE_DENYLIST_VARS:
        assert key not in child_env, f"denylisted var {key} leaked into child env"
    for key, value in _SANITIZE_PASSTHROUGH_VARS.items():
        assert child_env.get(key) == value, f"passthrough var {key} was altered"
    # 原环境不受影响（返回副本而非原地修改）。
    assert os.environ["SERVER__PORT"] == "56469"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    warned_names = set(
        re.search(r"removed (\S+)", r.getMessage()).group(1)
        for r in warnings
        if re.search(r"removed (\S+)", r.getMessage())
    )
    assert warned_names == set(_SANITIZE_DENYLIST_VARS)
    for record in warnings:
        message = record.getMessage()
        # 日志只含变量名与值长度摘要，不得记录完整值。
        for value in _SANITIZE_DENYLIST_VARS.values():
            assert value not in message


def test_build_sanitized_child_env_warns_once_per_key_per_process(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """同一变量名只在首次剔除时 WARNING，后续剔除降为 DEBUG。

    默认净化档自 Issue #230 起覆盖每一次 ``SubprocessRunner.run()``（git/gh
    等工具命令单轮可达数十次），逐次 WARNING 会淹没「名单误剔」排障信号。
    """
    from backend.infrastructure import child_env as child_env_module

    _inject_sanitize_env(monkeypatch)
    monkeypatch.setattr(child_env_module, "_WARNED_DENYLIST_REMOVALS", set())

    with caplog.at_level(logging.DEBUG, logger=child_env_module.logger.name):
        first_env = child_env_module.build_sanitized_child_env()
        second_env = child_env_module.build_sanitized_child_env()

    # 净化本身逐次照常发生，降级的只是告警频次。
    for env in (first_env, second_env):
        for key in _SANITIZE_DENYLIST_VARS:
            assert key not in env

    sanitized_records = [r for r in caplog.records if "child env sanitized" in r.getMessage()]
    warning_records = [r for r in sanitized_records if r.levelno == logging.WARNING]
    debug_records = [r for r in sanitized_records if r.levelno == logging.DEBUG]
    assert {record.getMessage() for record in warning_records} == {
        f"child env sanitized: removed {key} (value length {len(value)})"
        for key, value in _SANITIZE_DENYLIST_VARS.items()
    }
    assert len(debug_records) == len(_SANITIZE_DENYLIST_VARS)
    assert {record.getMessage() for record in debug_records} == {
        record.getMessage() for record in warning_records
    }


def test_run_filtered_claude_stream_child_env_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """经公开派发函数启动的真实子进程，其环境必须已被净化（rv-1 主断言）。"""
    from backend.infrastructure.process_runner import run_filtered_claude_stream

    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env.txt"

    completed = run_filtered_claude_stream(
        ["/bin/sh", "-c", f"env > {probe_file}"],
        cwd=tmp_path,
        timeout=60,
        collect_stdout=True,
    )

    assert completed.returncode == 0
    child_env_text = probe_file.read_text(encoding="utf-8")
    for key in _SANITIZE_DENYLIST_VARS:
        assert f"{key}=" not in child_env_text, f"denylisted var {key} reached child env"
    for key, value in _SANITIZE_PASSTHROUGH_VARS.items():
        assert f"{key}={value}" in child_env_text, f"passthrough var {key} missing"
    assert "PATH=" in child_env_text
    assert "HOME=" in child_env_text


def test_run_filtered_claude_stream_without_sanitizer_leaks_poison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """负控：绕过净化（模拟旧行为）时，同一探针必须观测到致毒变量。"""
    from unittest.mock import patch

    from backend.infrastructure import process_runner

    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env_unsanitized.txt"

    with patch(
        "backend.infrastructure.process_runner.build_sanitized_child_env",
        side_effect=lambda: dict(os.environ),
    ):
        completed = process_runner.run_filtered_claude_stream(
            ["/bin/sh", "-c", f"env > {probe_file}"],
            cwd=tmp_path,
            timeout=60,
            collect_stdout=True,
        )

    assert completed.returncode == 0
    child_env_text = probe_file.read_text(encoding="utf-8")
    assert "SERVER__PORT=56469" in child_env_text


def test_with_available_own_command_uses_current_names_and_preserves_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SubprocessRunner 只在当前自有命令间替换 argv[0]，包装形态原样保留。

    覆盖「配置写 kc、环境里只有 kedacode」这类错配，同时确认旧 ``iar`` 名称与带包装
    的命令行（``uv run kc daemon``）不会被误改。
    """
    available_command_names = {"kedacode"}
    monkeypatch.setattr(
        process_runner_module.shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}" if name in available_command_names else None,
    )

    # 主命令 kc 缺失、长命令 kedacode 可用：换成当前支持的自有名。
    assert _with_available_own_command(["kc", "run", "--all-ready"]) == [
        "kedacode",
        "run",
        "--all-ready",
    ]

    # 已移除的 iar 不再属于自有入口，不能触发命令名回退。
    assert _with_available_own_command(["iar", "daemon"]) == ["iar", "daemon"]

    # 包装形态：首个 token 是 uv，不是自有命令名，整条命令行原样保留、不做替换。
    assert _with_available_own_command(["uv", "run", "kc", "daemon"]) == [
        "uv",
        "run",
        "kc",
        "daemon",
    ]


# ---------------------------------------------------------------------------
# run() 默认净化与内容生成路径（Issue #230：env=None 不得等于全量继承）
# ---------------------------------------------------------------------------


def _assert_child_env_sanitized(child_env_text: str) -> None:
    """断言子进程实际环境：名单变量被剔除、透传变量原样在。"""
    for key in _SANITIZE_DENYLIST_VARS:
        assert f"{key}=" not in child_env_text, f"denylisted var {key} reached child env"
    for key, value in _SANITIZE_PASSTHROUGH_VARS.items():
        assert f"{key}={value}" in child_env_text, f"passthrough var {key} missing"


def test_run_without_env_profile_defaults_to_sanitized_child_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run() 不指定 env_profile 时默认走净化档：致毒变量不得进入子进程环境。"""
    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env_run_default.txt"

    result = SubprocessRunner().run(
        ["/bin/sh", "-c", f"env > {probe_file}"],
        cwd=tmp_path,
        timeout=60,
    )

    assert result.return_code == 0
    _assert_child_env_sanitized(probe_file.read_text(encoding="utf-8"))


def test_run_stdin_branch_sanitizes_default_child_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """input_text 分支（内容生成的 stdin 投递形态）同样必须用净化档。"""
    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env_run_stdin.txt"

    result = SubprocessRunner().run(
        ["/bin/sh", "-c", f"cat > /dev/null; env > {probe_file}"],
        cwd=tmp_path,
        timeout=60,
        input_text="prompt-via-stdin",
    )

    assert result.return_code == 0
    _assert_child_env_sanitized(probe_file.read_text(encoding="utf-8"))


def test_run_non_pty_stream_branch_sanitizes_default_child_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PTY 不可用时的管道中继兜底分支也不得回退到全量继承。"""
    from backend.infrastructure import process_runner as process_runner_module

    _inject_sanitize_env(monkeypatch)
    monkeypatch.setattr(process_runner_module, "_PTY_AVAILABLE", False)
    probe_file = tmp_path / "probe_env_run_pipe_fallback.txt"

    result = process_runner_module.SubprocessRunner().run(
        ["/bin/sh", "-c", f"env > {probe_file}"],
        cwd=tmp_path,
        capture_output=False,
        timeout=60,
    )

    assert result.return_code == 0
    _assert_child_env_sanitized(probe_file.read_text(encoding="utf-8"))


def test_run_without_sanitizer_leaks_poison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """负控：绕过净化（模拟 Issue #230 修复前的默认全量继承）时，探针必须观测到致毒变量。"""
    from backend.infrastructure import process_runner

    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env_run_unsanitized.txt"

    with patch.object(
        process_runner,
        "build_sanitized_child_env",
        side_effect=lambda: dict(os.environ),
    ):
        result = process_runner.SubprocessRunner().run(
            ["/bin/sh", "-c", f"env > {probe_file}"],
            cwd=tmp_path,
            timeout=60,
        )

    assert result.return_code == 0
    assert "SERVER__PORT=56469" in probe_file.read_text(encoding="utf-8")


def test_content_generator_plain_path_child_env_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实内容生成入口（Issue #229 事故路径）：generate 启动的子进程环境必须已净化。

    子进程用真实探针脚本打印自身环境，不 mock Popen；agent 注册表注入
    一个把环境写入 ``$1`` 的 shell 探针，覆盖
    ``SubprocessContentGenerator.generate`` → ``SubprocessRunner.run``
    这条 plain 协议投递链。
    """
    from backend.core.shared.models.agent_runner import AppConfig
    from backend.core.shared.models.agent_spec import (
        AGENT_PROFILE_GENERATE,
        AgentProfileSpec,
        AgentSpec,
    )
    from backend.engines.agent_runner.factories.content_generators import (
        SubprocessContentGenerator,
    )

    _inject_sanitize_env(monkeypatch)
    probe_file = tmp_path / "probe_env_content_generator.txt"
    probe_script = tmp_path / "env_probe.sh"
    probe_script.write_text('#!/bin/sh\nenv > "$1"\n', encoding="utf-8")
    probe_script.chmod(0o755)
    config = AppConfig(
        agents={
            "env-probe": AgentSpec(
                bin=str(probe_script),
                label="agent/env-probe",
                label_color="000000",
                label_description="Test probe that dumps its own environment.",
                profiles={AGENT_PROFILE_GENERATE: AgentProfileSpec(read_only=True)},
            )
        }
    )
    generator = SubprocessContentGenerator(SubprocessRunner(), config=config)

    result = generator.generate("env-probe", str(probe_file), cwd=tmp_path, timeout=60)

    assert result.return_code == 0
    _assert_child_env_sanitized(probe_file.read_text(encoding="utf-8"))
