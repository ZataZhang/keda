"""``pi-json-lines`` 输出协议：pi 的 JSON Lines 事件流渲染。

pi 以 ``--mode json`` 运行时在 stdout 输出 JSON Lines 事件流
（首行 session 头，随后 ``agent_start`` / ``message_update`` /
``tool_execution_*`` / ``agent_end`` 等）。本协议把事件渲染成与
Claude 流式路径对等的 live 面板文本；解析失败的行按原文透传，
不静默丢弃。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

from backend.core.shared.interfaces.agent_output_protocol import (
    PI_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.interfaces.output_timestamps import format_timestamped_line
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
)
from backend.infrastructure.agent_stream_usage import StreamUsageCollector
from backend.infrastructure.logging.logger import logger


class PiJsonLinesOutputProtocol:
    """pi JSON Lines 事件流渲染协议。

    本实现刻意**不**使用 :attr:`OutputRelayRequest.attempt_key`：它的子进程沿用
    调用方的进程组（不是 ``process_group=0`` 的独占组），按那个键去终止会打到
    KedaCode 自己身上。因此 ``pi`` 作为执行器时，停滞监督只能诊断与交班，
    不能精确取消——监督器读到"归属不可证实"后原样继续，绝不猜测性击杀。
    """

    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """启动 pi 子进程并把 JSON Lines 事件渲染成可读文本。"""
        return relay_structured_json_lines(
            request,
            protocol_id=PI_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )


def _render_event(event_payload: dict[str, object]) -> str:
    """把单个 pi 事件渲染成 live 面板文本；无可见内容返回空串。"""
    event_type = str(event_payload.get("type") or "")
    if event_type == "message_update":
        return _extract_message_text(event_payload)
    if event_type.startswith("tool_execution"):
        tool_name = str(event_payload.get("tool") or event_payload.get("name") or "tool")
        return f"\n[agent tool] {tool_name}\n"
    if event_type == "agent_end":
        error_text = str(event_payload.get("error") or "").strip()
        if error_text:
            return f"\n[agent error] {error_text}\n"
        return ""
    return ""


def _extract_message_text(event_payload: dict[str, object]) -> str:
    """从 ``message_update`` 事件中提取文本增量（容忍不同字段形态）。"""
    text_parts: list[str] = []
    delta = event_payload.get("delta")
    if isinstance(delta, dict) and delta.get("type") in (None, "text"):
        text_value = delta.get("text")
        if isinstance(text_value, str):
            text_parts.append(text_value)
            return "".join(text_parts)
    text_value = event_payload.get("text")
    if isinstance(text_value, str):
        text_parts.append(text_value)
    return "".join(text_parts)


def _relay_events(
    process: subprocess.Popen[str],
    *,
    collect_stdout: bool,
    output_sink: "Callable[[str], None] | None",
    usage_collector: StreamUsageCollector,
) -> str:
    """逐行读取事件流，渲染后交给 sink；返回收集的渲染文本。"""
    rendered_parts: list[str] = []
    try:
        if process.stdout is not None:
            for line in process.stdout:
                # 原始行先交给用量采集器（渲染文本不是事件流，无法事后解析）。
                usage_collector.observe_line(line)
                rendered_text = _render_line(line)
                if rendered_text:
                    rendered_parts.append(rendered_text)
                    if output_sink is not None:
                        # sink 收可读原文：行首时间戳属于消费侧（per-Issue
                        # 路由 sink）的展示，不在生产者这里加。
                        output_sink(rendered_text)
                    else:
                        logger.info("%s", rendered_text.strip())
                        print(format_timestamped_line(rendered_text), end="", flush=True)
        process.wait(timeout=None)
    except Exception:
        process.kill()
        process.wait()
        raise
    if collect_stdout:
        return "".join(rendered_parts)
    return ""


def _render_line(line: str) -> str:
    """渲染单行事件；非 JSON 行按原文透传。"""
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return line if line.strip() else ""
    if not isinstance(event_payload, dict):
        return ""
    return _render_event(event_payload)


def _pump_stderr(
    process: subprocess.Popen[str],
    display_sink: "Callable[[str], None] | None",
) -> None:
    """Drain pi stderr (human-readable logs) to the display sink or terminal."""
    if process.stderr is None:
        return
    for line in process.stderr:
        if display_sink is not None:
            display_sink(line)
        else:
            print(format_timestamped_line(line), end="", file=sys.stderr)


__all__ = ["PiJsonLinesOutputProtocol"]
