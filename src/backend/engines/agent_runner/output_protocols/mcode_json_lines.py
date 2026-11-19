"""``mcode-json-lines`` 输出协议：渲染 MiniMax Code exec 的 JSONL 事件。

mcode（``mcode exec --output-format stream-json``）的事件信封是它自有的
``exec.*`` / ``session.*`` / ``turn.*`` / ``item.*`` 四族事件，既不匹配
``claude-stream-json``（信封不同），也不匹配 ``codex-json-lines``（item 字段
是 ``content`` 而非 ``text``，工具事件是 ``item.toolCall`` 而非
``item.command_execution``），因此单独实现一套渲染。

渲染取舍：

- 只渲染 ``item.completed``。``item.started`` / ``item.updated`` 携带的是
  ``contentDelta`` 增量，与 completed 的完整文本重复渲染会让 transcript 里
  出现同一段话两遍。
- ``reasoning`` 单独成行并加 ``[agent thinking]`` 前缀：mcode 把思考与正文
  拆成两类 item，混在一起会丢掉"这段是思考"的语义。
- 未知事件一律跳过，不猜测其含义（与既有协议一致）。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_output_protocol import (
    MCODE_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
    render_item_completed,
    render_jsonl_object,
)
from backend.infrastructure.agent_model_config_snapshot import observe_relay_model_config

#: 工具调用详情里优先取用的键名（mcode 的 toolCall.input 是 dict）。
_TOOL_DETAIL_KEYS: tuple[str, ...] = ("command", "filePath", "path", "description")


class McodeJsonLinesOutputProtocol:
    """渲染 mcode ``exec --output-format stream-json`` 事件流。"""

    @observe_relay_model_config
    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """执行 mcode 并把 JSONL 事件转为可读输出。"""
        return relay_structured_json_lines(
            request,
            protocol_id=MCODE_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )


def _render_line(line: str) -> str:
    """从 mcode 事件里渲染正文、思考或工具调用。"""
    return render_jsonl_object(line, _render_event)


def _render_event(event_payload: dict[str, object]) -> str:
    """渲染已解码的 mcode 事件对象。"""
    event_type = event_payload.get("type")
    completed_output = render_item_completed(event_payload, _render_completed_item)
    if completed_output is not None:
        return completed_output
    if event_type == "turn.failed":
        error_text = event_payload.get("status") or event_payload.get("message")
        return f"\n[agent error] {error_text}\n" if error_text else ""
    return ""


def _render_completed_item(item: dict[str, object]) -> str:
    """渲染 mcode 完成的消息、思考或工具 item。"""
    item_type = item.get("type")
    if item_type == "agent_message":
        message_text = item.get("content")
        return f"{message_text}\n" if isinstance(message_text, str) and message_text else ""
    if item_type == "reasoning":
        reasoning_text = item.get("content")
        if isinstance(reasoning_text, str) and reasoning_text:
            return f"\n[agent thinking] {reasoning_text}\n"
        return ""
    if item_type == "tool_call":
        return _render_tool_call(item)
    return ""


def _render_tool_call(item: dict[str, object]) -> str:
    """渲染一次工具调用：工具名与可读详情。"""
    tool_call = item.get("toolCall")
    if not isinstance(tool_call, dict):
        return ""
    tool_name = tool_call.get("name")
    tool_label = str(tool_name or "tool")
    detail = ""
    tool_input = tool_call.get("input")
    if isinstance(tool_input, dict):
        for key in _TOOL_DETAIL_KEYS:
            raw_detail = tool_input.get(key)
            if isinstance(raw_detail, str) and raw_detail:
                detail = raw_detail
                break
    suffix = f": {detail}" if detail else ""
    return f"\n[agent tool] {tool_label}{suffix}\n"


__all__ = ["McodeJsonLinesOutputProtocol"]
