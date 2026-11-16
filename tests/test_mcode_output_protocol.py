"""``mcode-json-lines`` 输出协议的渲染测试。

mcode 的事件信封是它自有的 ``exec.*`` / ``turn.*`` / ``item.*`` 四族，
本文件锁死渲染规则：正文、思考、工具调用三类可见，增量事件不重复渲染，
未知事件不猜测其含义。
"""

from __future__ import annotations

import json

from backend.engines.agent_runner.output_protocols.mcode_json_lines import (
    _render_line,
)


def _event(payload: dict[str, object]) -> str:
    """把事件 dict 序列化成一行 JSON（协议看到的就是这一行）。"""
    return json.dumps(payload, ensure_ascii=False)


class TestRenderAgentMessage:
    """agent_message：正文取 ``content``（不是 codex 的 ``text``）。"""

    def test_renders_content(self) -> None:
        rendered = _render_line(
            _event(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "content": "已完成"},
                }
            )
        )
        assert rendered == "已完成\n"

    def test_skips_empty_content(self) -> None:
        rendered = _render_line(
            _event({"type": "item.completed", "item": {"type": "agent_message", "content": ""}})
        )
        assert rendered == ""

    def test_delta_events_are_not_rendered(self) -> None:
        """增量事件与 completed 内容重复，渲染会让 transcript 里出现两遍。"""
        rendered = _render_line(
            _event(
                {
                    "type": "item.updated",
                    "item": {"type": "agent_message", "contentDelta": "已完成"},
                }
            )
        )
        assert rendered == ""


class TestRenderReasoning:
    """reasoning：与正文拆成两类 item，单独成行并带前缀。"""

    def test_renders_with_thinking_prefix(self) -> None:
        rendered = _render_line(
            _event(
                {
                    "type": "item.completed",
                    "item": {"type": "reasoning", "content": "先看日志"},
                }
            )
        )
        assert rendered == "\n[agent thinking] 先看日志\n"


class TestRenderToolCall:
    """tool_call：工具名取 ``toolCall.name``，详情取 ``toolCall.input``。"""

    def test_renders_command_detail(self) -> None:
        rendered = _render_line(
            _event(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "tool_call",
                        "toolCall": {
                            "name": "bash",
                            "status": 2,
                            "input": {"command": "ls -l /tmp"},
                        },
                    },
                }
            )
        )
        assert rendered == "\n[agent tool] bash: ls -l /tmp\n"

    def test_renders_tool_name_without_detail(self) -> None:
        rendered = _render_line(
            _event(
                {
                    "type": "item.completed",
                    "item": {"type": "tool_call", "toolCall": {"name": "read", "status": 2}},
                }
            )
        )
        assert rendered == "\n[agent tool] read\n"

    def test_missing_tool_call_payload_is_skipped(self) -> None:
        rendered = _render_line(_event({"type": "item.completed", "item": {"type": "tool_call"}}))
        assert rendered == ""


class TestRenderFailure:
    """turn.failed：错误信息取 ``status``。"""

    def test_renders_status(self) -> None:
        rendered = _render_line(_event({"type": "turn.failed", "status": "limit_exceeded"}))
        assert rendered == "\n[agent error] limit_exceeded\n"


class TestRenderUnknownEvents:
    """未知事件与畸形输入：不猜测、不抛异常。"""

    def test_unknown_event_is_skipped(self) -> None:
        assert _render_line(_event({"type": "exec.started"})) == ""

    def test_non_json_line_passes_through(self) -> None:
        assert _render_line("plain text") == "plain text"

    def test_blank_line_is_empty(self) -> None:
        assert _render_line("   ") == ""

    def test_non_dict_payload_is_skipped(self) -> None:
        assert _render_line("[1, 2, 3]") == ""
