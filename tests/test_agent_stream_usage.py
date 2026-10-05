"""agent 输出流旁路观测的单元测试：token 用量与会话 id。

覆盖 :mod:`backend.infrastructure.agent_stream_usage` 的提取与容错行为：
result 事件四字段解析、畸形 usage 降级、非 JSON 行忽略、plain 事后解析，
以及 ``SubprocessRunner.run`` 两条路径（claude 流式 / plain）的
``CommandResult.token_usage`` / ``CommandResult.session_id`` 赋值——后者是
崩溃对账后会话续传的唯一来源。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import AGENT_SESSION_ID_ATTR_NAME
from backend.core.shared.models.agent_runner import TokenUsage
from backend.infrastructure.agent_stream_usage import (
    StreamUsageCollector,
    extract_session_id_event,
    extract_usage_event,
    parse_usage_from_plain_stdout,
)
from backend.infrastructure.process_runner import SubprocessRunner

_FIXED_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 340,
    "cache_read_input_tokens": 800,
    "cache_creation_input_tokens": 120,
}


def _result_event_line(usage: object) -> str:
    """构造一行 claude 形的 result 事件 JSON。"""
    import json

    payload: dict[str, object] = {"type": "result", "result": "done"}
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload)


class TestExtractUsageEvent:
    """extract_usage_event 的解析与容错。"""

    def test_extracts_all_four_fields(self) -> None:
        line = _result_event_line(_FIXED_USAGE)
        usage = extract_usage_event(line)
        assert usage == TokenUsage(
            input_tokens=1200,
            output_tokens=340,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=120,
        )
        assert usage.total_tokens == 2460

    def test_missing_usage_returns_none(self) -> None:
        assert extract_usage_event(_result_event_line(None)) is None

    def test_malformed_usage_string_returns_none(self) -> None:
        # 畸形 usage（字符串）必须降级为 None，绝不抛异常。
        assert extract_usage_event(_result_event_line("oops")) is None

    def test_partial_usage_fills_zero(self) -> None:
        line = _result_event_line({"input_tokens": 500, "output_tokens": 30})
        usage = extract_usage_event(line)
        assert usage == TokenUsage(input_tokens=500, output_tokens=30)
        assert usage.total_tokens == 530

    def test_non_integer_fields_are_skipped(self) -> None:
        line = _result_event_line(
            {"input_tokens": "12", "output_tokens": True, "cache_read_input_tokens": -3}
        )
        assert extract_usage_event(line) is None

    def test_non_result_event_ignored(self) -> None:
        import json

        line = json.dumps({"type": "assistant", "usage": _FIXED_USAGE})
        assert extract_usage_event(line) is None

    def test_plain_text_line_ignored(self) -> None:
        assert extract_usage_event("Agent output: some human text") is None

    def test_broken_json_ignored(self) -> None:
        assert extract_usage_event('{"type": "result", "usage": {oops') is None

    def test_result_substring_precheck_keeps_valid_line(self) -> None:
        # 预检只跳过不含 "result" 子串的行；合法 result 行不受影响。
        assert extract_usage_event(_result_event_line(_FIXED_USAGE)) is not None


class TestStreamUsageCollector:
    """流式收集器：保留最后一个 result usage，任何行都不抛异常。"""

    def test_collects_last_usage(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line('{"type": "assistant", "message": {}}')
        collector.observe_line(_result_event_line({"input_tokens": 1}))
        collector.observe_line(_result_event_line(_FIXED_USAGE))
        assert collector.usage == TokenUsage(
            input_tokens=1200,
            output_tokens=340,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=120,
        )

    def test_no_usage_stays_none(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line("plain text")
        collector.observe_line('{"type": "assistant"}')
        assert collector.usage is None


class TestParseUsageFromPlainStdout:
    """plain / PTY 事后容错解析。"""

    def test_finds_usage_after_stderr_noise(self) -> None:
        # PTY 合并流：stderr 文本与 JSON 事件交错也不影响解析。
        stdout = "some warning line\n" + _result_event_line(_FIXED_USAGE) + "\n"
        usage = parse_usage_from_plain_stdout(stdout)
        assert usage is not None
        assert usage.input_tokens == 1200

    def test_no_usage_returns_none(self) -> None:
        assert parse_usage_from_plain_stdout("no json here\nat all") is None


class TestSubprocessRunnerTokenUsage:
    """SubprocessRunner.run 两条路径的 token_usage 赋值。"""

    def _write_fake_agent(self, worktree: Path, body_lines: list[str]) -> list[str]:
        script = worktree / "fake_agent.py"
        script.write_text(
            "import sys\n"
            + "".join(f"sys.stdout.write({line!r} + '\\n')\n" for line in body_lines),
            encoding="utf-8",
        )
        return ["uv", "run", "python", str(script)]

    def test_claude_stream_path_collects_usage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        runner = SubprocessRunner()
        stream_lines = [
            json.dumps({"type": "stream_event", "event": {"type": "message_stop"}}),
            _result_event_line(_FIXED_USAGE),
        ]
        command = self._write_fake_agent(tmp_path, stream_lines)
        result = runner.run(
            command,
            cwd=tmp_path,
            check=False,
            timeout=60,
            output_protocol="claude-stream-json",
        )
        assert result.return_code == 0
        assert result.token_usage == TokenUsage(
            input_tokens=1200,
            output_tokens=340,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=120,
        )

    def test_plain_agent_path_without_usage_is_none(self, tmp_path: Path) -> None:
        runner = SubprocessRunner()
        command = self._write_fake_agent(tmp_path, ["plain human output"])
        result = runner.run(
            command,
            cwd=tmp_path,
            check=False,
            timeout=60,
            output_protocol="plain",
        )
        assert result.token_usage is None

    def test_generic_command_is_not_parsed(self, tmp_path: Path) -> None:
        # 普通命令（output_protocol=None）不做用量解析——即使输出含 result JSON。
        import json

        runner = SubprocessRunner()
        command = self._write_fake_agent(
            tmp_path, [json.dumps({"type": "result", "usage": _FIXED_USAGE})]
        )
        result = runner.run(command, cwd=tmp_path, check=False, timeout=60)
        assert result.token_usage is None


def _init_event_line(session_id: str) -> str:
    """构造一行 claude 形的 system/init 事件 JSON（会话 id 的首发位置）。"""
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id})


def _result_event_line_with_session(session_id: str) -> str:
    """构造一行自报用量与会话 id 的 result 事件 JSON。"""
    return json.dumps(
        {"type": "result", "result": "done", "usage": _FIXED_USAGE, "session_id": session_id}
    )


def _fake_agent_script(stream_lines: list[str], *, sleep_seconds: int | None = None) -> str:
    """生成"逐行原样打印、可选随后长睡"的假 agent 脚本体。"""
    script_body = "import sys\n"
    for stream_line in stream_lines:
        script_body += f"sys.stdout.write({stream_line!r} + '\\n')\n" "sys.stdout.flush()\n"
    if sleep_seconds is not None:
        script_body += f"import time\ntime.sleep({sleep_seconds})\n"
    return script_body


class TestExtractSessionIdEvent:
    """session_id 提取与容错：解析失败一律等价于"没拿到会话"。"""

    def test_extracts_from_init_event(self) -> None:
        assert extract_session_id_event(_init_event_line("sess-abc")) == "sess-abc"

    def test_extracts_from_result_event(self) -> None:
        # 续传调用会开新会话，最后的 result 事件同样自报 id，"最后一个"才是对的。
        line = json.dumps({"type": "result", "session_id": "sess-new", "result": "done"})
        assert extract_session_id_event(line) == "sess-new"

    def test_plain_text_line_ignored(self) -> None:
        assert extract_session_id_event("Agent output: session_id mentioned in prose") is None

    def test_broken_json_ignored(self) -> None:
        assert extract_session_id_event('{"session_id": "sess-abc", oops') is None

    def test_non_string_session_id_ignored(self) -> None:
        assert extract_session_id_event(json.dumps({"session_id": 123})) is None

    def test_blank_session_id_ignored(self) -> None:
        assert extract_session_id_event(_init_event_line("   ")) is None

    def test_whitespace_stripped(self) -> None:
        assert extract_session_id_event(_init_event_line(" sess-abc ")) == "sess-abc"


class TestStreamUsageCollectorSessionId:
    """收集器同时带回报文与用量，且绝不影响主流程。"""

    def test_keeps_last_observed_session_id(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line(_init_event_line("sess-first"))
        collector.observe_line('{"type": "assistant"}')
        collector.observe_line(_init_event_line("sess-last"))
        assert collector.session_id == "sess-last"

    def test_absent_session_id_stays_none(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line("plain text")
        collector.observe_line(_result_event_line(_FIXED_USAGE))
        assert collector.session_id is None

    def test_usage_and_session_id_coexist(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line(_init_event_line("sess-abc"))
        collector.observe_line(_result_event_line(_FIXED_USAGE))
        assert collector.session_id == "sess-abc"
        assert collector.usage is not None


class TestSubprocessRunnerSessionCapture:
    """``SubprocessRunner.run`` 把会话 id 交回调用方的两条通路。"""

    def _run_agent(
        self,
        tmp_path: Path,
        stream_lines: list[str],
        *,
        output_protocol: str,
        sleep_seconds: int | None = None,
        timeout: int = 60,
    ):
        runner = SubprocessRunner()
        script = tmp_path / "fake_stream_agent.py"
        script.write_text(
            _fake_agent_script(stream_lines, sleep_seconds=sleep_seconds), encoding="utf-8"
        )
        return runner.run(
            ["uv", "run", "python", str(script)],
            cwd=tmp_path,
            check=False,
            timeout=timeout,
            output_protocol=output_protocol,
        )

    def test_claude_stream_path_reports_session_id(self, tmp_path: Path) -> None:
        result = self._run_agent(
            tmp_path,
            [_init_event_line("sess-abc"), _result_event_line_with_session("sess-abc")],
            output_protocol="claude-stream-json",
        )
        assert result.return_code == 0
        assert result.session_id == "sess-abc"
        assert result.token_usage == TokenUsage(
            input_tokens=1200,
            output_tokens=340,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=120,
        )

    def test_plain_path_reports_no_session_id(self, tmp_path: Path) -> None:
        # plain / PTY 路径不做结构化解析：散文里出现 session_id 也不算会话。
        result = self._run_agent(
            tmp_path,
            ['Session: {"session_id": "sess-abc"} prose'],
            output_protocol="plain",
        )
        assert result.session_id is None

    def test_timeout_kill_attaches_session_id_to_exception(self, tmp_path: Path) -> None:
        """超时击杀没有 CommandResult：会话 id 必须挂在异常上，否则断点彻底丢失。"""
        with pytest.raises(subprocess.TimeoutExpired) as timeout_error:
            self._run_agent(
                tmp_path,
                [_init_event_line("sess-timeout")],
                output_protocol="claude-stream-json",
                sleep_seconds=30,
                timeout=1,
            )
        assert getattr(timeout_error.value, AGENT_SESSION_ID_ATTR_NAME, None) == "sess-timeout"
