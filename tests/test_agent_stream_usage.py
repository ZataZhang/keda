"""agent 输出流旁路观测的单元测试：token 用量、会话 id 与执行器自报模型名。

覆盖 :mod:`backend.infrastructure.agent_stream_usage` 的提取与容错行为：
result 事件四字段解析、畸形 usage 降级、非 JSON 行忽略、plain 事后解析，
以及 ``SubprocessRunner.run`` 两条路径（claude 流式 / plain）的
``CommandResult.token_usage`` / ``CommandResult.session_id`` 赋值——后者是
崩溃对账后会话续传的唯一来源。失败边界同样在覆盖范围内：超时击杀走异常、
非零退出经 :class:`ProtocolRoutingProcessRunner` 就地新建异常，两条路都必须
把已经观察到的会话 id 与自报模型名带上去，否则事实在协议边界丢失。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import (
    AGENT_REPORTED_MODEL_ATTR_NAME,
    AGENT_SESSION_ID_ATTR_NAME,
)
from backend.core.shared.models.agent_runner import TokenUsage
from backend.engines.agent_runner.output_protocols import get_output_protocol_registry
from backend.engines.agent_runner.protocol_routing_runner import ProtocolRoutingProcessRunner
from backend.infrastructure.agent_stream_usage import (
    StreamUsageCollector,
    extract_reported_model_event,
    extract_session_id_event,
    extract_usage_event,
    parse_reported_model_from_plain_stdout,
    parse_usage_from_plain_stdout,
)
from backend.infrastructure.process_runner import CommandFailedError, SubprocessRunner

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


def _fake_agent_script(
    stream_lines: list[str],
    *,
    sleep_seconds: int | None = None,
    exit_code: int = 0,
) -> str:
    """生成"逐行原样打印、可选随后长睡、可选非零退出"的假 agent 脚本体。"""
    script_body = "import sys\n"
    for stream_line in stream_lines:
        script_body += f"sys.stdout.write({stream_line!r} + '\\n')\n" "sys.stdout.flush()\n"
    if sleep_seconds is not None:
        script_body += f"import time\ntime.sleep({sleep_seconds})\n"
    if exit_code:
        script_body += f"sys.exit({exit_code})\n"
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


# --- 执行器自报模型名（Issue #242 / FR-2）-----------------------------------


def _init_event_line_with_model(session_id: str, model: str) -> str:
    """构造一行同时自报会话 id 与模型名的 claude 形 system/init 事件。"""
    return json.dumps(
        {"type": "system", "subtype": "init", "session_id": session_id, "model": model}
    )


class TestExtractReportedModelEvent:
    """``extract_reported_model_event`` 只认事件顶层的 ``model`` 字符串。"""

    def test_extracts_top_level_model(self) -> None:
        line = _init_event_line_with_model("sess-abc", "claude-sonnet-4-5-20250929")
        assert extract_reported_model_event(line) == "claude-sonnet-4-5-20250929"

    def test_ignores_nested_message_model(self) -> None:
        """嵌套形态的归属语义不确定：宁可记"未提供"，也不给出可能错误归因的值。"""
        line = json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-4-5-20250929"}})
        assert extract_reported_model_event(line) is None

    def test_ignores_model_usage_keys(self) -> None:
        line = json.dumps(
            {"type": "result", "modelUsage": {"claude-sonnet-4-5-20250929": {"input": 1}}}
        )
        assert extract_reported_model_event(line) is None

    def test_plain_text_line_ignored(self) -> None:
        assert extract_reported_model_event("Running with model gpt-5-mini") is None

    def test_broken_json_ignored(self) -> None:
        assert extract_reported_model_event('{"model": "gpt-5-mini", oops') is None

    def test_non_string_and_blank_model_ignored(self) -> None:
        assert extract_reported_model_event(json.dumps({"model": 42})) is None
        assert extract_reported_model_event(json.dumps({"model": "   "})) is None

    def test_whitespace_stripped(self) -> None:
        assert extract_reported_model_event(json.dumps({"model": " gpt-5-mini "})) == "gpt-5-mini"


class TestStreamUsageCollectorReportedModel:
    """收集器带执行器自报模型名，且绝不影响主流程。"""

    def test_keeps_last_observed_model(self) -> None:
        collector = StreamUsageCollector()
        collector.observe_line(_init_event_line_with_model("sess-1", "claude-first"))
        collector.observe_line('{"type": "assistant"}')
        collector.observe_line(_init_event_line_with_model("sess-2", "claude-last"))
        assert collector.reported_model == "claude-last"

    def test_absent_model_stays_none(self) -> None:
        """没有可信报告值就是 ``None``，不等于"用了配置里的默认模型"。"""
        collector = StreamUsageCollector()
        collector.observe_line("plain text")
        collector.observe_line(_result_event_line(_FIXED_USAGE))
        assert collector.reported_model is None


class TestParseReportedModelFromPlainStdout:
    """plain / PTY 路径的事后容错解析。"""

    def test_hits_last_model_line(self) -> None:
        stdout = "\n".join(
            [
                "human readable prose",
                json.dumps({"model": "gpt-5-mini"}),
                "more prose",
                json.dumps({"model": "gpt-5-mini-2026-01"}),
            ]
        )
        assert parse_reported_model_from_plain_stdout(stdout) == "gpt-5-mini-2026-01"

    def test_no_model_line_returns_none(self) -> None:
        assert parse_reported_model_from_plain_stdout("just prose\nnothing else\n") is None


class TestSubprocessRunnerReportedModel:
    """``SubprocessRunner.run`` 把执行器自报模型名交回调用方的两条通路。"""

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
        script = tmp_path / "fake_model_agent.py"
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

    def test_claude_stream_path_reports_model(self, tmp_path: Path) -> None:
        result = self._run_agent(
            tmp_path,
            [
                _init_event_line_with_model("sess-abc", "claude-sonnet-4-5-20250929"),
                _result_event_line_with_session("sess-abc"),
            ],
            output_protocol="claude-stream-json",
        )
        assert result.return_code == 0
        assert result.reported_model == "claude-sonnet-4-5-20250929"
        assert result.session_id == "sess-abc"

    def test_claude_stream_path_without_model_stays_none(self, tmp_path: Path) -> None:
        result = self._run_agent(
            tmp_path,
            [_init_event_line("sess-abc"), _result_event_line_with_session("sess-abc")],
            output_protocol="claude-stream-json",
        )
        assert result.reported_model is None

    def test_plain_path_parses_model_after_the_fact(self, tmp_path: Path) -> None:
        """plain 协议下 stdout 未被渲染改写：事后解析同样能拿到自报模型名。"""
        result = self._run_agent(
            tmp_path,
            [json.dumps({"model": "gpt-5-mini"})],
            output_protocol="plain",
        )
        assert result.reported_model == "gpt-5-mini"

    def test_timeout_kill_attaches_reported_model_to_exception(self, tmp_path: Path) -> None:
        """超时击杀没有 CommandResult：自报模型名必须挂在异常上，否则事实彻底丢失。"""
        with pytest.raises(subprocess.TimeoutExpired) as timeout_error:
            self._run_agent(
                tmp_path,
                [_init_event_line_with_model("sess-timeout", "claude-sonnet-4-5-20250929")],
                output_protocol="claude-stream-json",
                sleep_seconds=30,
                timeout=1,
            )
        assert getattr(timeout_error.value, AGENT_REPORTED_MODEL_ATTR_NAME, None) == (
            "claude-sonnet-4-5-20250929"
        )
        assert getattr(timeout_error.value, AGENT_SESSION_ID_ATTR_NAME, None) == "sess-timeout"


class TestProtocolRoutingRunnerFailureObservations:
    """协议路由执行器新建失败异常时，必须把中继已观察到的字段一起带上去。

    run 路径的 agent 调用经 :class:`ProtocolRoutingProcessRunner` 走协议中继，
    中继**返回** ``CommandResult``（不抛），非零退出由路由层就地新建
    :class:`CommandFailedError`。若只搬 stdout/stderr，执行器已经自报过的模型名与
    会话 id 就在协议边界凭空消失：调用观测记成"未提供"，崩溃对账也拿不到可续传
    的会话。
    """

    def _run_failing_agent(self, tmp_path: Path, *, output_protocol: str):
        script = tmp_path / "fake_failing_agent.py"
        script.write_text(
            _fake_agent_script(
                [_init_event_line_with_model("sess-fail", "claude-sonnet-4-5-20250929")],
                exit_code=1,
            ),
            encoding="utf-8",
        )
        runner = ProtocolRoutingProcessRunner(SubprocessRunner(), get_output_protocol_registry())
        return runner.run(
            ["uv", "run", "python", str(script)],
            cwd=tmp_path,
            check=True,
            timeout=60,
            output_protocol=output_protocol,
        )

    def test_nonzero_exit_carries_reported_model_and_session(self, tmp_path: Path) -> None:
        with pytest.raises(CommandFailedError) as failure_error:
            self._run_failing_agent(tmp_path, output_protocol="claude-stream-json")
        assert getattr(failure_error.value, AGENT_REPORTED_MODEL_ATTR_NAME, None) == (
            "claude-sonnet-4-5-20250929"
        )
        assert getattr(failure_error.value, AGENT_SESSION_ID_ATTR_NAME, None) == "sess-fail"

    def test_unobserved_fields_stay_absent(self, tmp_path: Path) -> None:
        """执行器什么都没自报时不挂空值：缺失必须诚实呈现为"未提供"。"""
        script = tmp_path / "fake_silent_agent.py"
        script.write_text(
            _fake_agent_script(["just prose, no events"], exit_code=1), encoding="utf-8"
        )
        runner = ProtocolRoutingProcessRunner(SubprocessRunner(), get_output_protocol_registry())
        with pytest.raises(CommandFailedError) as failure_error:
            runner.run(
                ["uv", "run", "python", str(script)],
                cwd=tmp_path,
                check=True,
                timeout=60,
                output_protocol="claude-stream-json",
            )
        assert not hasattr(failure_error.value, AGENT_REPORTED_MODEL_ATTR_NAME)
        assert not hasattr(failure_error.value, AGENT_SESSION_ID_ATTR_NAME)
