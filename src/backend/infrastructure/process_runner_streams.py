"""进程输出流协调、PTY 转发与 Claude stream-json 渲染。"""

from __future__ import annotations

import codecs
import json
import os
import select
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from backend.infrastructure.agent_stream_usage import StreamUsageCollector


class ClaudeStreamRenderer:
    """Render Claude stream-json output into concise terminal messages."""

    def __init__(self) -> None:
        self._tool_use_ids: set[str] = set()
        self._saw_text_delta = False
        self._printed_text_content = False

    def render_line(self, line: str) -> str:
        """Return display text for one stream-json line."""
        try:
            event_payload = json.loads(line)
        except json.JSONDecodeError:
            return line
        if not isinstance(event_payload, dict):
            return ""
        event_type = event_payload.get("type")
        if event_type == "stream_event":
            return self._render_stream_event(event_payload.get("event"))
        if event_type == "assistant":
            return self._render_assistant_message(event_payload.get("message"))
        if event_type == "result":
            return self._render_result(event_payload)
        return ""

    def _render_stream_event(self, event_payload: object) -> str:
        if not isinstance(event_payload, dict):
            return ""
        if event_payload.get("type") == "message_stop" and self._saw_text_delta:
            self._saw_text_delta = False
            return "\n"
        delta_payload = event_payload.get("delta")
        if not isinstance(delta_payload, dict):
            return ""
        if delta_payload.get("type") == "text_delta":
            self._saw_text_delta = True
            self._printed_text_content = True
            return str(delta_payload.get("text", ""))
        return ""

    def _render_assistant_message(self, message_payload: object) -> str:
        if not isinstance(message_payload, dict):
            return ""
        content_blocks = message_payload.get("content", [])
        if not isinstance(content_blocks, list):
            return ""
        rendered_blocks: list[str] = []
        for content_block in content_blocks:
            if not isinstance(content_block, dict):
                continue
            if content_block.get("type") != "tool_use":
                continue
            tool_use_id = str(content_block.get("id", ""))
            if tool_use_id in self._tool_use_ids:
                continue
            self._tool_use_ids.add(tool_use_id)
            rendered_blocks.append(_format_tool_use(content_block))
        return "".join(rendered_blocks)

    def _render_result(self, event_payload: dict[str, Any]) -> str:
        result_text = str(event_payload.get("result") or "").strip()
        is_error = bool(event_payload.get("is_error"))
        if not result_text or (not is_error and self._printed_text_content):
            return ""
        prefix = "[agent error] " if is_error else "[agent result] "
        return f"\n{prefix}{result_text}\n"


def run_filtered_claude_stream(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    inactivity_timeout: int | None = None,
    collect_stdout: bool = False,
    prompt_text: str | None = None,
    output_sink: Callable[[str], None] | None = None,
    display_sink: Callable[[str], None] | None = None,
    label: str | None = None,
    usage_collector: StreamUsageCollector | None = None,
    line_renderer: Callable[[str], str] | None = None,
    env: dict[str, str] | None = None,
    attempt_key: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a structured JSON Lines agent stream and print its readable view.

    Args:
        command: Command to run.
        cwd: Working directory.
        timeout: Optional wall-clock timeout in seconds.
        inactivity_timeout: Optional timeout in seconds since the last
            stdout/stderr output.
        collect_stdout: Whether to collect rendered output.
        prompt_text: Optional prompt to pass via stdin.
        output_sink: Optional callback for rendered text chunks (raw readable
            text; line timestamps are added by the consumers, not here).
        display_sink: Optional callback for stderr lines (display only).
            When provided, stderr is drained on a background thread and
            routed here instead of leaking raw onto the terminal.
        label: Optional label for heartbeat/timeout logs.
        usage_collector: Optional token 用量采集器。提供时，每行原始事件
            在渲染前先交给它观察（原始行只有此处可靠可得；渲染后的
            stdout 重解析会静默丢行）。
        line_renderer: 可选的事件渲染器；未提供时使用 Claude stream-json 渲染。
        env: 可选的已净化子进程环境（由 :meth:`SubprocessRunner.run`
            传入，避免同一环境重复构造）；``None`` 时本函数自行调用
            :func:`build_sanitized_child_env`，直接调用方无需感知。
        cancel_check: 人工接管取消探针，由 watchdog 秒级轮询。

    Returns:
        CompletedProcess with collected stdout if requested.

    Raises:
        subprocess.TimeoutExpired: 墙钟或静默期超时被看门狗杀掉时抛出，异常的
            ``output`` 带着杀进程前渲染出的输出（``collect_stdout=False`` 时为空，
            因为那种调用本来就没在收集）。stderr 走 ``display_sink``，不收集。
    """
    from backend.infrastructure import process_runner as process_runner_module

    renderer = ClaudeStreamRenderer()
    capture_stderr = display_sink is not None
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env if env is not None else process_runner_module.build_sanitized_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if capture_stderr else None,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **process_runner_module._OWN_PROCESS_GROUP_KWARGS,
    )
    watchdog = process_runner_module._ProcessWatchdog(
        process,
        command,
        timeout=timeout,
        inactivity_timeout_seconds=inactivity_timeout,
        heartbeat_seconds=process_runner_module._COMMAND_HEARTBEAT_SECONDS,
        base_label="Agent stream",
        context_label=label,
        attempt_key=attempt_key,
        cancel_check=cancel_check,
    )
    watchdog.start()

    def _pump_stderr() -> None:
        if process.stderr is None:
            return
        for stderr_line in process.stderr:
            watchdog.note_output()
            display_sink(stderr_line)

    stderr_thread: threading.Thread | None = None
    if capture_stderr:
        stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
        stderr_thread.start()
    if prompt_text is not None:
        # Write stdin in a background thread to avoid deadlock
        # when the pipe buffer fills up before the child reads.
        def _write_stdin() -> None:
            if process.stdin is not None:
                process.stdin.write(prompt_text)
                process.stdin.close()

        threading.Thread(target=_write_stdin, daemon=True).start()
    else:
        process.stdin.close()
    stdout_lines: list[str] = []
    text_buffer: list[str] = []
    stream_formatter = process_runner_module.TimestampedStreamFormatter()
    try:
        if process.stdout is not None:
            for output_line in process.stdout:
                watchdog.note_output()
                if usage_collector is not None:
                    usage_collector.observe_line(output_line)
                rendered_text = (
                    line_renderer(output_line)
                    if line_renderer is not None
                    else renderer.render_line(output_line)
                )
                if collect_stdout and rendered_text:
                    stdout_lines.append(rendered_text)
                if rendered_text:
                    if output_sink is not None:
                        # The sink drives the live view and the workspace file;
                        # skip stdout/logger writes that would corrupt the
                        # live region. sink 只收可读原文，时间戳由消费侧
                        # （per-Issue 路由 sink）在自己的边界上加。
                        output_sink(rendered_text)
                        continue
                    timestamped = stream_formatter.format_chunk(rendered_text)
                    print(timestamped, end="", flush=True)

                    # Structured events go straight to logger
                    if (
                        "[agent tool]" in rendered_text
                        or "[agent result]" in rendered_text
                        or "[agent error]" in rendered_text
                    ):
                        process_runner_module.logger.info("%s", rendered_text.strip())
                    else:
                        text_buffer.append(rendered_text)
                        buffered_text = "".join(text_buffer)
                        if (
                            rendered_text.endswith("\n")
                            or len(buffered_text) >= process_runner_module._MAX_BUFFER_SIZE
                        ):
                            stripped = buffered_text.strip()
                            if stripped:
                                process_runner_module.logger.info("Agent output: %s", stripped)
                            text_buffer.clear()
        if text_buffer:
            buffered = "".join(text_buffer).strip()
            if buffered:
                process_runner_module.logger.info("Agent output: %s", buffered)
        return_code = process.wait(timeout=timeout)
        watchdog.raise_if_timed_out(partial_stdout="".join(stdout_lines))
        watchdog.raise_if_cancelled(partial_stdout="".join(stdout_lines))
    except BaseException as exc:
        process_runner_module._attach_stream_observations(exc, usage_collector)
        process_runner_module._terminate_process_tree(process)
        process.wait()
        raise
    finally:
        watchdog.stop()
    if stderr_thread is not None:
        stderr_thread.join(timeout=5)
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=return_code,
        stdout="".join(stdout_lines),
        stderr="",
    )


def _run_pty_stream(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    inactivity_timeout: int | None,
    label: str | None,
    output_sink: Callable[[str], None] | None,
    env: dict[str, str] | None = None,
    attempt_key: str | None = None,
    usage_collector: StreamUsageCollector | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a streaming command under a pseudo-terminal so it line-buffers.

    Plain-output agents such as ``kimi`` switch stdout to block buffering when it
    is a pipe, so their progress stays invisible until they exit. Allocating a
    PTY makes them believe stdout is a terminal, restoring live incremental
    output. stdout and stderr are merged onto the PTY (natural ordering, no
    second-pipe deadlock). Rendered chunks go to ``output_sink`` when provided,
    otherwise to this process's stdout with line-buffered logging — mirroring
    :func:`run_filtered_claude_stream`.

    Args:
        command: Command and arguments to execute.
        cwd: Working directory for the subprocess.
        timeout: Optional wall-clock timeout in seconds.
        inactivity_timeout: Optional no-output timeout in seconds.
        label: Optional label for heartbeat/timeout logs.
        output_sink: 可选的可读原始文本回调；行时间戳由消费者添加，本函数不添加。
        env: 可选的已净化子进程环境（由 :meth:`SubprocessRunner.run`
            传入）；``None`` 时本函数自行调用 :func:`build_sanitized_child_env`。
        usage_collector: 可选 agent 输出旁路观测器；观察 PTY 合并流中的原文。
        attempt_key: 活跃 attempt 登记键（停滞监督的所有权证据来源）。

    Returns:
        CompletedProcess with the collected stdout (stderr merged into it).

    Raises:
        subprocess.TimeoutExpired: 墙钟或静默期超时被看门狗杀掉时抛出，异常的
            ``output`` 带着杀进程前从 PTY 读到的全部输出。stderr 已经并进 PTY，
            所以只填 stdout 一侧。
    """
    from backend.infrastructure import process_runner as process_runner_module

    master_fd, slave_fd = process_runner_module.pty.openpty()
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env if env is not None else process_runner_module.build_sanitized_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            **process_runner_module._OWN_PROCESS_GROUP_KWARGS,
        )
    finally:
        os.close(slave_fd)
    watchdog = process_runner_module._ProcessWatchdog(
        process,
        command,
        timeout=timeout,
        inactivity_timeout_seconds=inactivity_timeout,
        heartbeat_seconds=process_runner_module._COMMAND_HEARTBEAT_SECONDS,
        base_label="Command",
        context_label=label,
        attempt_key=attempt_key,
    )
    watchdog.start()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    stream_formatter = process_runner_module.TimestampedStreamFormatter()
    collected: list[str] = []
    line_buffer: list[str] = []

    def _flush_log_lines(*, final: bool = False) -> None:
        joined = "".join(line_buffer)
        line_buffer.clear()
        if not joined:
            return
        segments = joined.split("\n")
        remainder = segments.pop()
        for segment in segments:
            stripped = segment.rstrip("\r").strip()
            if stripped:
                process_runner_module.logger.info("Agent output: %s", stripped)
        if final:
            stripped_remainder = remainder.rstrip("\r").strip()
            if stripped_remainder:
                process_runner_module.logger.info("Agent output: %s", stripped_remainder)
        elif remainder:
            line_buffer.append(remainder)

    def _emit(text: str) -> None:
        if not text:
            return
        collected.append(text)
        if usage_collector is not None:
            usage_collector.observe_text(text)
        if output_sink is not None:
            # sink 只收可读原文：时间戳属于消费侧展示，由 per-Issue 路由
            # sink 在落盘 / 上屏前统一加，合议 workspace 文件因此保持干净。
            output_sink(text)
            return
        print(stream_formatter.format_chunk(text), end="", flush=True)
        line_buffer.append(text)
        if "\n" in text:
            _flush_log_lines()

    try:
        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except (OSError, ValueError):
                break
            if not ready:
                if process.poll() is not None:
                    break
                continue
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break  # EIO once the child closes the slave end == EOF.
            if not data:
                break
            watchdog.note_output()
            _emit(decoder.decode(data))
        _emit(decoder.decode(b"", final=True))
        if output_sink is None:
            _flush_log_lines(final=True)
        return_code = process.wait(timeout=timeout)
        watchdog.raise_if_timed_out(partial_stdout="".join(collected))
    except BaseException as exc:
        process_runner_module._attach_stream_observations(exc, usage_collector)
        process_runner_module._terminate_process_tree(process)
        process.wait()
        raise
    finally:
        watchdog.stop()
        try:
            os.close(master_fd)
        except OSError:
            pass
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=return_code,
        stdout="".join(collected),
        stderr="",
    )


def _format_tool_use(content_block: dict[str, Any]) -> str:
    """Format one tool call without dumping large JSON payloads."""
    tool_name = str(content_block.get("name") or "tool")
    input_payload = content_block.get("input")
    if not isinstance(input_payload, dict):
        return f"\n[agent tool] {tool_name}\n"
    detail_parts: list[str] = []
    for field_name in ("file_path", "path", "command"):
        field_value = input_payload.get(field_name)
        if field_value:
            detail_parts.append(str(field_value))
            break
    if "offset" in input_payload:
        detail_parts.append(f"offset={input_payload['offset']}")
    if "limit" in input_payload:
        detail_parts.append(f"limit={input_payload['limit']}")
    details = f": {' '.join(detail_parts)}" if detail_parts else ""
    return f"\n[agent tool] {tool_name}{details}\n"
