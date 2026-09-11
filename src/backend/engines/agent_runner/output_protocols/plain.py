"""``plain`` 输出协议：通用逐行中继。

行为等价于历史 ``transcript_runner`` 的
``_run_agent_with_stdin_prompt`` / ``_relay_process_stdout``：
逐行把 stdout 交给 ``output_sink``（或终端 + logger），stderr 在后台
线程里泵给 ``display_sink``（仅展示、不进 transcript），提示词按需
写入 stdin。不做任何结构化渲染。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable

from backend.core.shared.interfaces.agent_output_protocol import (
    PLAIN_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import PROMPT_DELIVERY_STDIN
from backend.infrastructure.logging.logger import logger
from backend.infrastructure.process_runner import _format_timestamped_line


class PlainOutputProtocol:
    """通用文本中继协议。"""

    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """启动子进程并逐行中继其输出直至退出。"""
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
        stdout_text = _relay_process_stdout(process, output_sink=request.output_sink)
        stderr_thread.join(timeout=5)
        return CommandResult(
            command=tuple(request.argv),
            return_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout_text,
            stderr="",
            output_protocol=PLAIN_PROTOCOL_ID,
        )


def _pump_stderr(
    process: subprocess.Popen[str],
    display_sink: "Callable[[str], None] | None",
) -> None:
    """Drain subprocess stderr, routing each line to the display sink.

    Agents such as ``codex`` write their human-readable reasoning/tool log
    to stderr. When a ``display_sink`` is present it shows those lines live
    without collecting them into the transcript. With no sink we preserve
    the prior behaviour of echoing stderr to the terminal.
    """
    if process.stderr is None:
        return
    for line in process.stderr:
        if display_sink is not None:
            display_sink(line)
        else:
            print(_format_timestamped_line(line), end="", file=sys.stderr)


def _relay_process_stdout(
    process: subprocess.Popen[str],
    output_sink: "Callable[[str], None] | None" = None,
) -> str:
    """Relay subprocess stdout to terminal and logger.

    Stderr is drained on a background thread so the agent's reasoning/tool
    log reaches ``display_sink`` (live view) without blocking stdout or
    leaking raw onto the terminal and corrupting the live region.
    """
    stdout_lines: list[str] = []
    try:
        if process.stdout is not None:
            for line in process.stdout:
                stdout_lines.append(line)
                if output_sink is not None:
                    # The sink drives the live view and the workspace file;
                    # avoid writing to stdout (would corrupt the live region).
                    output_sink(line)
                else:
                    logger.info("%s", line.rstrip("\n"))
                    timestamped = _format_timestamped_line(line)
                    print(timestamped, end="")
        return_code = process.wait(timeout=None)
    except Exception:
        process.kill()
        process.wait()
        raise
    _ = return_code
    return "".join(stdout_lines)


__all__ = ["PlainOutputProtocol"]
