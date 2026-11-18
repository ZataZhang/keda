"""``dsh-json-lines`` 输出协议的渲染测试。

dsh（``dsh --profile headless --json``）的事件词汇是 ``session`` / ``status`` /
``text`` / ``thinking`` / ``tool_call`` / ``tool_result`` / ``final`` / ``error``。
本文件锁死渲染规则：正文、思考、工具调用（含错误）可见，成功工具结果与
``final`` 不重复渲染，未知事件不猜测其含义。
"""

from __future__ import annotations

import json

from backend.engines.agent_runner.output_protocols.dsh_json_lines import (
    _render_line,
)


def _event(payload: dict[str, object]) -> str:
    """把事件 dict 序列化成一行 JSON（协议看到的就是这一行）。"""
    return json.dumps(payload, ensure_ascii=False)


class TestRenderText:
    """text：dsh 已提交的 assistant 文本块，原样成行。"""

    def test_renders_text(self) -> None:
        assert _render_line(_event({"type": "text", "text": "已完成"})) == "已完成\n"

    def test_skips_empty_text(self) -> None:
        assert _render_line(_event({"type": "text", "text": ""})) == ""


class TestRenderThinking:
    """thinking：推理块单独成行并带前缀，避免与正文混淆。"""

    def test_renders_with_thinking_prefix(self) -> None:
        assert (
            _render_line(_event({"type": "thinking", "text": "先看日志"}))
            == "\n[agent thinking] 先看日志\n"
        )


class TestRenderToolCall:
    """tool_call：工具名取 ``tool``，详情取 ``input`` 里的命令/路径。"""

    def test_renders_command_detail(self) -> None:
        rendered = _render_line(
            _event({"type": "tool_call", "tool": "bash", "input": {"command": "ls -l /tmp"}})
        )
        assert rendered == "\n[agent tool] bash: ls -l /tmp\n"

    def test_renders_path_detail(self) -> None:
        rendered = _render_line(
            _event({"type": "tool_call", "tool": "read", "input": {"path": "/tmp/a.txt"}})
        )
        assert rendered == "\n[agent tool] read: /tmp/a.txt\n"

    def test_renders_string_input(self) -> None:
        rendered = _render_line(
            _event({"type": "tool_call", "tool": "bash", "input": "ls -l /tmp"})
        )
        assert rendered == "\n[agent tool] bash: ls -l /tmp\n"

    def test_renders_tool_name_without_detail(self) -> None:
        assert (
            _render_line(_event({"type": "tool_call", "tool": "bash"})) == "\n[agent tool] bash\n"
        )

    def test_missing_tool_name_falls_back(self) -> None:
        assert _render_line(_event({"type": "tool_call", "input": {}})) == "\n[agent tool] tool\n"


class TestRenderToolResult:
    """tool_result：成功结果不输出（避免噪音），错误摘要输出。"""

    def test_skips_successful_result(self) -> None:
        assert (
            _render_line(
                _event({"type": "tool_result", "status": "completed", "result": "大量输出"})
            )
            == ""
        )

    def test_renders_error_result(self) -> None:
        assert (
            _render_line(_event({"type": "tool_result", "status": "error", "result": "权限不足"}))
            == "\n[agent tool error] 权限不足\n"
        )


class TestRenderFailure:
    """error：用法错误与 runner 直驱失败都通过该事件上报。"""

    def test_renders_error_message(self) -> None:
        assert (
            _render_line(_event({"type": "error", "message": "permission 未知预设"}))
            == "\n[agent error] permission 未知预设\n"
        )


class TestSilentEvents:
    """session / status / final 不产生正文行。"""

    def test_session_is_silent(self) -> None:
        assert _render_line(_event({"type": "session", "sessionId": "session-x"})) == ""

    def test_status_is_silent(self) -> None:
        assert _render_line(_event({"type": "status", "phase": "step_end"})) == ""

    def test_final_is_silent(self) -> None:
        """final.text 与最后一条 text 重复，渲染它会让 transcript 出现两遍。"""
        assert (
            _render_line(_event({"type": "final", "sessionId": "session-x", "text": "已完成"}))
            == ""
        )


class TestTruncatedEvents:
    """dsh 的限长事件可能丢字段并标 truncated，渲染器不得假设结构完整。"""

    def test_truncated_tool_call_without_input(self) -> None:
        rendered = _render_line(_event({"type": "tool_call", "tool": "bash", "truncated": True}))
        assert rendered == "\n[agent tool] bash\n"


class TestRenderUnknownEvents:
    """未知事件与畸形输入：不猜测、不抛异常。"""

    def test_unknown_event_is_skipped(self) -> None:
        assert _render_line(_event({"type": "turn/future_event"})) == ""

    def test_non_json_line_passes_through(self) -> None:
        assert _render_line("plain text") == "plain text"

    def test_blank_line_is_empty(self) -> None:
        assert _render_line("   ") == ""

    def test_non_dict_payload_is_skipped(self) -> None:
        assert _render_line("[1, 2, 3]") == ""
