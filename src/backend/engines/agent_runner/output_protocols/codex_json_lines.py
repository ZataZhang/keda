"""``codex-json-lines`` 输出协议：渲染 Codex exec JSONL 事件并提取 thread id。"""

from __future__ import annotations

import json

from backend.infrastructure.agent_model_config_snapshot import observe_relay_model_config

from backend.core.shared.interfaces.agent_output_protocol import (
    CODEX_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
)


class CodexJsonLinesOutputProtocol:
    """渲染 Codex ``exec --json`` 的消息与工具事件。"""

    @observe_relay_model_config
    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """执行 Codex 并把 JSONL 事件转为可读输出。"""
        return relay_structured_json_lines(
            request,
            protocol_id=CODEX_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )


def _render_line(line: str) -> str:
    """渲染单条 Codex JSONL 事件；未知 JSON 事件不输出。"""
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return line if line.strip() else ""
    if not isinstance(event_payload, dict):
        return ""
    event_type = event_payload.get("type")
    if event_type == "item.completed":
        item = event_payload.get("item")
        if not isinstance(item, dict):
            return ""
        item_type = item.get("type")
        if item_type == "agent_message":
            message_text = item.get("text")
            return f"{message_text}\n" if isinstance(message_text, str) and message_text else ""
        if item_type in ("command_execution", "mcp_tool_call", "web_search"):
            tool_label = str(item.get("type") or "tool").replace("_", " ")
            command_text = item.get("command")
            detail = f": {command_text}" if isinstance(command_text, str) and command_text else ""
            return f"\n[agent tool] {tool_label}{detail}\n"
        return ""
    if event_type == "item.started":
        item = event_payload.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            command_text = item.get("command")
            detail = f": {command_text}" if isinstance(command_text, str) and command_text else ""
            return f"\n[agent tool] command{detail}\n"
    if event_type in ("error", "turn.failed"):
        error_text = event_payload.get("message") or event_payload.get("error")
        if isinstance(error_text, dict):
            error_text = error_text.get("message")
        return f"\n[agent error] {error_text}\n" if error_text else ""
    return ""


__all__ = ["CodexJsonLinesOutputProtocol"]
