"""Agent 响应文本提取（从 ``run_agent_once`` 拆出的单一职责模块）。

从 :class:`~backend.core.shared.models.agent_runner.CommandResult` 中提取
assistant 的最终文本回复：直接 stdout 或 Claude ``stream-json`` 事件流。
"""

from __future__ import annotations

import json

from backend.core.shared.interfaces.agent_output_protocol import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
)
from backend.core.shared.models.agent_runner import CommandResult

__all__ = ["extract_agent_response_text"]


def extract_agent_response_text(result: CommandResult) -> str:
    """Return assistant response text from direct stdout or Claude stream-json.

    Claude 使用 `--output-format stream-json` 时，每行输出是一个 JSON 事件，
    包含 stream_event（文本增量）、assistant（完整消息）或 result（最终结果）。
    本函数按优先级提取有效文本；非流式协议的结果直接返回原始 stdout。

    注意：流式协议会把事件流渲染成纯文本再返回，此时 stdout 已不是原始
    事件流；若仍逐行重解析，恰好构成合法 JSON 标量的行（如数组末尾不带
    逗号的字符串元素）会被静默丢弃，破坏其中的 JSON 内容。因此只有
    ``output_protocol`` 确实是流式协议时才走事件提取，否则原样返回。
    """
    if not result.stdout:
        return ""
    if result.output_protocol != CLAUDE_STREAM_JSON_PROTOCOL_ID:
        return result.stdout

    stream_text_parts: list[str] = []
    assistant_text_parts: list[str] = []
    result_parts: list[str] = []
    saw_stream_json_event = False
    for output_line in result.stdout.splitlines():
        try:
            event_payload = json.loads(output_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event_payload, dict):
            continue
        event_type = event_payload.get("type")
        if event_type == "stream_event":
            saw_stream_json_event = True
            _append_claude_stream_event_text(event_payload, stream_text_parts)
        elif event_type == "assistant":
            saw_stream_json_event = True
            _append_claude_assistant_text(event_payload, assistant_text_parts)
        elif event_type == "result":
            saw_stream_json_event = True
            result_text = str(event_payload.get("result") or "").strip()
            if result_text:
                result_parts.append(result_text)

    if not saw_stream_json_event:
        return result.stdout
    if stream_text_parts:
        return "".join(stream_text_parts)
    if assistant_text_parts:
        return "".join(assistant_text_parts)
    if result_parts:
        return "\n".join(result_parts)
    return result.stdout


def _append_claude_stream_event_text(
    event_payload: dict[str, object],
    text_parts: list[str],
) -> None:
    event = event_payload.get("event")
    if not isinstance(event, dict):
        return
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return
    if delta.get("type") == "text_delta":
        text_parts.append(str(delta.get("text", "")))


def _append_claude_assistant_text(
    event_payload: dict[str, object],
    text_parts: list[str],
) -> None:
    message = event_payload.get("message")
    if not isinstance(message, dict):
        return
    content_blocks = message.get("content", [])
    if not isinstance(content_blocks, list):
        return
    for content_block in content_blocks:
        if not isinstance(content_block, dict):
            continue
        if content_block.get("type") == "text":
            text_parts.append(str(content_block.get("text", "")))
