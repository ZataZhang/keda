"""token 用量采集单元测试。

覆盖 :mod:`backend.infrastructure.agent_stream_usage` 的提取与容错行为：
result 事件四字段解析、畸形 usage 降级、非 JSON 行忽略、plain 事后解析、
以及 ``SubprocessRunner.run`` 两条路径（claude 流式 / plain）的
``CommandResult.token_usage`` 赋值。
"""

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import TokenUsage
from backend.infrastructure.agent_stream_usage import (
    StreamUsageCollector,
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
