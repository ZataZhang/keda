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
import threading
from collections.abc import Callable

from backend.core.shared.interfaces.agent_output_protocol import (
    PI_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import PROMPT_DELIVERY_STDIN
from backend.infrastructure.logging.logger import logger
from backend.infrastructure.process_runner import _format_timestamped_line


class PiJsonLinesOutputProtocol:
    """pi JSON Lines 事件流渲染协议。"""

    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """启动 pi 子进程并把 JSON Lines 事件渲染成可读文本。"""
        write_stdin = (
            request.prompt_text is not None and request.prompt_delivery == PROMPT_DELIVERY_STDIN
        )
        process = subprocess.Popen(
            list(request.argv),
            cwd=request.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if write_stdin else subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def _write_stdin() -> None:
            if process.stdin is not None:
                try:
                    process.stdin.write(request.prompt_text or "")
                except BrokenPipeError:
                    pass
                process.stdin.close()

        if write_stdin:
            threading.Thread(target=_write_stdin, daemon=True).start()

        stderr_thread = threading.Thread(
            target=_pump_stderr, args=(process, request.display_sink), daemon=True
        )
        stderr_thread.start()
        stdout_text = _relay_events(
            process,
            collect_stdout=request.collect_stdout,
            output_sink=request.output_sink,
        )
        stderr_thread.join(timeout=5)
        return_code = process.returncode if process.returncode is not None else -1
        return CommandResult(
            command=tuple(request.argv),
            return_code=return_code,
            stdout=stdout_text,
            stderr="",
            output_protocol=PI_JSON_LINES_PROTOCOL_ID,
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
) -> str:
    """逐行读取事件流，渲染后交给 sink；返回收集的渲染文本。"""
    rendered_parts: list[str] = []
    try:
        if process.stdout is not None:
            for line in process.stdout:
                rendered_text = _render_line(line)
                if rendered_text:
                    rendered_parts.append(rendered_text)
                    if output_sink is not None:
                        output_sink(rendered_text)
                    else:
                        logger.info("%s", rendered_text.strip())
                        print(_format_timestamped_line(rendered_text), end="", flush=True)
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
            print(_format_timestamped_line(line), end="", file=sys.stderr)


__all__ = ["PiJsonLinesOutputProtocol"]
