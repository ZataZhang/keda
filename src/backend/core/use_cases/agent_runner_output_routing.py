"""Per-Issue output routing for parallel daemon passes.

When ``kc daemon`` processes Issues concurrently, each Issue's agent stream and
log lines must be attributable instead of interleaving on one stdout. This
module provides the core-side plumbing, depending only on ``core/shared``
interfaces and the standard library (no ``engines`` / ``infrastructure``
imports), so the layering rule ``core -> engines -> infrastructure`` holds:

- :class:`_OutputRoutedProcessRunner` wraps any :class:`IProcessRunner` and
  injects a per-Issue ``output_sink`` into every ``run`` call. Because the
  process runner is already threaded through the whole processing pipeline, this
  routes the agent stream without adding a parameter to every function.
- :func:`issue_output_routing` opens the per-Issue log file, builds the sink
  (file + live-view panel) and installs a thread-scoped logging handler so the
  worker thread's ``_logger`` lines also land in that Issue's file. The sink is
  also the **only** place that adds the ``[HH:MM:SS]`` line timestamp (Issue
  #223): producers hand it readable raw text, so the per-Issue file, the live
  board and the serial terminal mirror all show the same timeline while the
  deliberation workspace files — fed by the same producers — stay clean.
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Sequence

from backend.core.shared.interfaces.output_timestamps import TimestampedStreamFormatter
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.issue_logs import ATTEMPT_END_MARKER

# Logger namespace the per-Issue handler attaches to. All backend modules log
# under this root (e.g. ``backend.core.use_cases.agent_runner_orchestrate``), so
# attaching here captures the worker thread's narrative via propagation.
_BACKEND_LOGGER_NAME = "backend"


class _OutputRoutedProcessRunner:
    """Wrap an ``IProcessRunner`` and inject a per-Issue ``output_sink``.

    Implements the ``IProcessRunner`` contract via duck typing. Every ``run``
    call is delegated to the wrapped runner with ``output_sink`` defaulted to
    this Issue's sink, unless the caller passed an explicit sink.
    """

    def __init__(self, wrapped: object, sink: Callable[[str], None]) -> None:
        self._wrapped = wrapped
        self._sink = sink
        # 测试 fake 与自定义 runner 常用精简签名（缺 output_sink /
        # output_protocol 等新参数）。预先探测被包装 run 接受的参数集，
        # 委托时只传它声明得起的部分，避免包装器比接口「多嘴」炸 TypeError。
        try:
            self._accepted_params = set(inspect.signature(wrapped.run).parameters)
        except (TypeError, ValueError):
            self._accepted_params = set()

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        inactivity_timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
        label: str | None = None,
        output_sink: Callable[[str], None] | None = None,
        output_protocol: str | None = None,
    ) -> CommandResult:
        """Delegate to the wrapped runner, defaulting ``output_sink`` per Issue.

        签名与 :class:`IProcessRunner` 逐参数对齐，但只把被包装运行器实际
        声明的关键字参数继续下传；未声明的保持缺省语义（等价于历史行为里
        根本不存在该参数）。
        """
        run_kwargs: dict[str, object] = {
            "cwd": cwd,
            "check": check,
            "timeout": timeout,
            "inactivity_timeout": inactivity_timeout,
            "capture_output": capture_output,
            "input_text": input_text,
            "label": label,
            "output_sink": output_sink if output_sink is not None else self._sink,
            "output_protocol": output_protocol,
        }
        if self._accepted_params:
            run_kwargs = {
                name: value for name, value in run_kwargs.items() if name in self._accepted_params
            }
        return self._wrapped.run(command, **run_kwargs)


class _IssueLogWriter:
    """Thread-safe append writer used by both the sink and the log handler."""

    def __init__(self, file_path: Path) -> None:
        self._lock = threading.Lock()
        self._file = file_path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        """Append ``text`` and flush so live ``tail -f`` sees it promptly."""
        with self._lock:
            self._file.write(text)
            self._file.flush()

    def flush(self) -> None:
        """Flush the underlying file (used by the logging handler)."""
        with self._lock:
            if not self._file.closed:
                self._file.flush()

    def close(self) -> None:
        """Close the underlying file."""
        with self._lock:
            if not self._file.closed:
                self._file.close()


class _ThreadLogFilter(logging.Filter):
    """Only pass log records emitted from a specific thread."""

    def __init__(self, thread_ident: int) -> None:
        super().__init__()
        self._thread_ident = thread_ident

    def filter(self, record: logging.LogRecord) -> bool:
        """Return True only for records from the registered thread."""
        return record.thread == self._thread_ident


def per_issue_log_path(log_base: Path, repo_id: str, issue_number: int) -> Path:
    """Return the per-Issue log file path under ``log_base``.

    Layout: ``<log_base>/agent-runner/issues/<repo_id>/issue-<n>-<ts>.log``.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return log_base / "agent-runner" / "issues" / repo_id / f"issue-{issue_number}-{timestamp}.log"


@contextmanager
def issue_output_routing(
    *,
    repo_id: str,
    issue_number: int,
    log_base: Path,
    output_view: IRunnerLiveView,
    console_sink: Callable[[str], None] | None = None,
) -> Iterator[Callable[[str], None]]:
    """Route one Issue's output to its own log file and live-view panel.

    Yields a ``sink(chunk)`` callable to hand to
    :class:`_OutputRoutedProcessRunner`. While active, the calling thread's
    ``backend.*`` log records are also written to the Issue's file (scoped by
    thread id), so the file holds both the agent stream and the worker
    narrative. The handler and file are torn down on exit.

    该 sink 是行首 ``[HH:MM:SS]`` 时间戳的唯一落点（Issue #223）：生产者传入
    的仍是可读原文，前缀在这里加上后再分发给日志文件、看板与前台镜像，三个
    消费端因此逐行一致。日志末尾的 ``[iar-attempt-end]`` 终态标记由 writer
    直接写入，不经 sink，因此保持裸行、可被 ``--follow`` 精确匹配。

    Args:
        repo_id: Repository identifier (log subdirectory).
        issue_number: Issue number (log filename + panel key).
        log_base: Base directory for logs (typically ``<repo_path>/logs``).
        output_view: Live view receiving each chunk for the Issue's panel.
        console_sink: 可选的「原终端镜像」回调。串行 ``kc run`` 传入它，
            让 sink 将同一份（带行首时间戳的）文本写回启动终端，与未经路由
            时的终端实时视图逐行一致；并行 daemon 则保持 ``None``（面板已承担
            展示）。只传**可读文本**，避免 Rich 控制字符污染日志文件。
    """
    file_path = per_issue_log_path(log_base, repo_id, issue_number)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    writer = _IssueLogWriter(file_path)
    # 每次尝试一个 formatter：碎片可能在任意位置切断，状态机保证时间戳只
    # 落在物理行首；作用域结束即丢弃，不会把上一行的状态漏到下一份日志。
    line_timestamps = TimestampedStreamFormatter()

    def sink(chunk: str) -> None:
        timestamped_chunk = line_timestamps.format_chunk(chunk)
        writer.write(timestamped_chunk)
        output_view.append(issue_number, timestamped_chunk)
        if console_sink is not None:
            console_sink(timestamped_chunk)

    handler = logging.StreamHandler(stream=writer)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_ThreadLogFilter(threading.get_ident()))
    backend_logger = logging.getLogger(_BACKEND_LOGGER_NAME)
    backend_logger.addHandler(handler)
    try:
        yield sink
    finally:
        backend_logger.removeHandler(handler)
        # 追加显式尝试终态标记：``--follow`` 需要它把「运行结束」和「这一刻没有
        # 新字节」区分开（后者在 Agent 两次写入之间与重试间隔里都会出现）。
        writer.write(f"\n{ATTEMPT_END_MARKER}\n")
        writer.close()
