"""结构化 JSON Lines agent 协议共用的进程中继。"""

from __future__ import annotations

from collections.abc import Callable
import json

from backend.core.shared.interfaces.agent_output_protocol import OutputRelayRequest
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import PROMPT_DELIVERY_STDIN
from backend.infrastructure.agent_stream_usage import StreamUsageCollector
from backend.infrastructure.process_runner import run_filtered_claude_stream


def decode_jsonl_object(line: str) -> tuple[bool, dict[str, object] | None]:
    """解码 JSONL 对象，返回 JSON 合法性与对象载荷。"""
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return False, None
    if not isinstance(event_payload, dict):
        return True, None
    return True, event_payload


def render_jsonl_object(
    line: str,
    event_renderer: Callable[[dict[str, object]], str],
) -> str:
    """解码并渲染 JSONL 对象；无法解码的非空行按原文透传。"""
    is_valid_json, event_payload = decode_jsonl_object(line)
    if event_payload is None:
        return line if not is_valid_json and line.strip() else ""
    return event_renderer(event_payload)


def render_item_completed(
    event_payload: dict[str, object],
    item_renderer: Callable[[dict[str, object]], str],
) -> str | None:
    """处理 ``item.completed`` 信封；其他事件返回 ``None``。"""
    if event_payload.get("type") != "item.completed":
        return None
    item_payload = event_payload.get("item")
    if not isinstance(item_payload, dict):
        return ""
    return item_renderer(item_payload)


def relay_structured_json_lines(
    request: OutputRelayRequest,
    *,
    protocol_id: str,
    line_renderer: Callable[[str], str],
) -> CommandResult:
    """运行结构化 JSONL 子进程、渲染可读输出并采集会话观察值。"""
    usage_collector = StreamUsageCollector()
    completed = run_filtered_claude_stream(
        request.argv,
        cwd=request.cwd,
        timeout=request.timeout,
        inactivity_timeout=request.inactivity_timeout,
        collect_stdout=request.collect_stdout,
        prompt_text=(
            request.prompt_text if request.prompt_delivery == PROMPT_DELIVERY_STDIN else None
        ),
        output_sink=request.output_sink,
        display_sink=request.display_sink,
        label=request.label,
        usage_collector=usage_collector,
        line_renderer=line_renderer,
    )
    return CommandResult(
        command=request.argv,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output_protocol=protocol_id,
        token_usage=usage_collector.usage,
        session_id=usage_collector.session_id,
        reported_model=usage_collector.reported_model,
    )


__all__ = [
    "decode_jsonl_object",
    "relay_structured_json_lines",
    "render_item_completed",
    "render_jsonl_object",
]
