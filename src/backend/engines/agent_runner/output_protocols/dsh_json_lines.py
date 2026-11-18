"""``dsh-json-lines`` 输出协议：渲染 DeepSeek Harness headless 事件流。

dsh（``dsh --profile headless --json``）把一次运行投影成有界、有序的按行事件：
``session`` / ``status``（``turn_start`` / ``step_start`` / ``step_end`` /
``turn_end``）/ ``thinking`` / ``text`` / ``tool_call`` / ``tool_result`` /
``final``，直驱失败另发 ``error``。它与既有五种协议都不同形（codex 走
``item.completed`` 信封、claude 走 ``stream_event``），因此单独实现一套渲染。

渲染取舍：

- ``text`` / ``thinking`` 只从已提交的 assistant 消息投影，到达即完整文本，无需
  再处理增量。
- ``final`` 不渲染正文：``final.text`` 是"最后一条非空 assistant 消息"的无损
  副本，与最后一个 ``text`` 重复，渲染会让 transcript 出现两遍。
- 成功的 ``tool_result`` 不输出（工具输出可能很大且已由 dsh 侧限长），只在
  ``status == "error"`` 时输出错误摘要，避免 transcript 噪音。
- dsh 对除 ``final`` 外的每个字符串限 8 KiB、每条事件限 32 KiB，超长时丢弃
  结构化字段并标 ``truncated: true``；渲染器按现有字段读取，不假设结构完整。
- 未知事件一律跳过，不猜测其含义（与既有协议一致）。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_output_protocol import (
    DSH_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
    render_jsonl_object,
)
from backend.infrastructure.agent_model_config_snapshot import observe_relay_model_config

#: 工具调用详情里优先取用的键名（dsh 的 ``tool_call.input`` 是工具参数对象）。
_TOOL_DETAIL_KEYS: tuple[str, ...] = ("command", "path", "file_path", "description")


class DshJsonLinesOutputProtocol:
    """渲染 dsh ``--profile headless --json`` 事件流。"""

    @observe_relay_model_config
    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """执行 dsh 并把 JSONL 事件转为可读输出。"""
        return relay_structured_json_lines(
            request,
            protocol_id=DSH_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )


def _render_line(line: str) -> str:
    """从 dsh 事件里渲染正文、思考、工具调用或错误。"""
    return render_jsonl_object(line, _render_event)


def _render_event(event_payload: dict[str, object]) -> str:
    """渲染已解码的 dsh 事件对象。"""
    event_type = event_payload.get("type")
    if event_type == "text":
        message_text = event_payload.get("text")
        return f"{message_text}\n" if isinstance(message_text, str) and message_text else ""
    if event_type == "thinking":
        reasoning_text = event_payload.get("text")
        if isinstance(reasoning_text, str) and reasoning_text:
            return f"\n[agent thinking] {reasoning_text}\n"
        return ""
    if event_type == "tool_call":
        return _render_tool_call(event_payload)
    if event_type == "tool_result" and event_payload.get("status") == "error":
        result_text = event_payload.get("result")
        return f"\n[agent tool error] {result_text}\n" if isinstance(result_text, str) else ""
    if event_type == "error":
        error_text = event_payload.get("message")
        return f"\n[agent error] {error_text}\n" if isinstance(error_text, str) else ""
    return ""


def _render_tool_call(event_payload: dict[str, object]) -> str:
    """渲染一次工具调用：工具名与可读详情。"""
    tool_label = str(event_payload.get("tool") or "tool")
    detail = ""
    tool_input = event_payload.get("input")
    if isinstance(tool_input, dict):
        for key in _TOOL_DETAIL_KEYS:
            raw_detail = tool_input.get(key)
            if isinstance(raw_detail, str) and raw_detail:
                detail = raw_detail
                break
    elif isinstance(tool_input, str) and tool_input:
        detail = tool_input
    suffix = f": {detail}" if detail else ""
    return f"\n[agent tool] {tool_label}{suffix}\n"


__all__ = ["DshJsonLinesOutputProtocol"]
