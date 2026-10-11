"""``opencode-json-lines`` 输出协议：渲染 OpenCode JSON 事件并提取 session id。"""

from __future__ import annotations

import json

from backend.core.shared.interfaces.agent_output_protocol import (
    OPENCODE_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
)


class OpenCodeJsonLinesOutputProtocol:
    """渲染 OpenCode ``run --format json`` 事件。"""

    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """执行 OpenCode 并把 JSON 事件转为可读输出。"""
        return relay_structured_json_lines(
            request,
            protocol_id=OPENCODE_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )


def _render_line(line: str) -> str:
    """从 OpenCode 事件里渲染文本增量、工具调用或错误。"""
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return line if line.strip() else ""
    if not isinstance(event_payload, dict):
        return ""
    part = event_payload.get("part")
    part_payload = part if isinstance(part, dict) else {}
    part_type = part_payload.get("type")
    event_type = event_payload.get("type")
    if event_type == "text" or part_type == "text":
        message_text = part_payload.get("text") or event_payload.get("text")
        return message_text if isinstance(message_text, str) else ""
    if event_type in ("tool", "tool_use") or part_type == "tool":
        tool_name = part_payload.get("tool") or event_payload.get("tool") or "tool"
        state = part_payload.get("state")
        state_payload = state if isinstance(state, dict) else {}
        title = state_payload.get("title")
        input_payload = state_payload.get("input")
        detail = title if isinstance(title, str) and title else ""
        if not detail and isinstance(input_payload, dict):
            for key in ("command", "filePath", "path"):
                raw_detail = input_payload.get(key)
                if isinstance(raw_detail, str) and raw_detail:
                    detail = raw_detail
                    break
        suffix = f": {detail}" if detail else ""
        return f"\n[agent tool] {tool_name}{suffix}\n"
    if event_type == "error" or part_type == "error":
        error_payload = event_payload.get("error") or part_payload.get("error")
        if isinstance(error_payload, dict):
            error_payload = error_payload.get("message")
        if not isinstance(error_payload, str):
            error_payload = event_payload.get("message")
        return f"\n[agent error] {error_payload}\n" if error_payload else ""
    return ""


__all__ = ["OpenCodeJsonLinesOutputProtocol"]
