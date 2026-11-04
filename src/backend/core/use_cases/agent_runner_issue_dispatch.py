"""单个 Issue 的输出路由与观测作用域。

从 :mod:`backend.core.use_cases.agent_runner_orchestration_runtime` 拆分而来：
单文件非空行有 CI 硬上限，而这里只承载每个 Issue 的日志/看板路由、调用观测
上下文与生命周期进度转发；Issue 本身的处理函数由 runtime 在调用时注入。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.interfaces.runner_console import (
    IRunHistoryStore,
    PrdLifecycleEventRecord,
)
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.models.agent_runner_progress import RunProgressEvent
from backend.core.use_cases.agent_invocation_tracing import (
    bound_invocation_trace_context,
    build_invocation_trace_context,
)
from backend.core.use_cases.agent_runner_lifecycle_progress import lifecycle_progress_scope
from backend.core.use_cases.agent_runner_output_routing import (
    _OutputRoutedProcessRunner,
    issue_output_routing,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _IssueRoutingContext:
    """单个 Issue 执行时的输出路由与观测上下文（同一轮内所有 Issue 共用）。"""

    effective_repo_id: str
    log_base: Path
    output_view: IRunnerLiveView
    console_sink: Callable[[str], None] | None
    process_runner: IProcessRunner
    run_history_store: IRunHistoryStore | None
    progress_callback: Callable[[RunProgressEvent], None] | None
    process_kwargs: dict[str, Any]


def _mirror_to_console(chunk: str) -> None:
    """把串行执行的 Issue 输出镜像到启动终端。"""
    print(chunk, end="", flush=True, file=sys.stdout)


def _process_routed_issue(
    item: tuple[IssueSummary, str],
    *,
    routing: _IssueRoutingContext,
    process_issue: Callable[..., int],
) -> int:
    """在输出路由与观测上下文里处理一个 Issue；路由自身的异常按失败返回。"""
    issue, issue_kind = item
    try:
        with issue_output_routing(
            repo_id=routing.effective_repo_id,
            issue_number=issue.number,
            log_base=routing.log_base,
            output_view=routing.output_view,
            console_sink=routing.console_sink,
        ) as sink:
            scoped_runner = _OutputRoutedProcessRunner(routing.process_runner, sink)

            def emit_issue_progress(event_record: PrdLifecycleEventRecord) -> None:
                if routing.progress_callback is None:
                    return
                routing.progress_callback(
                    RunProgressEvent(
                        repo_id=routing.effective_repo_id,
                        issue_number=issue.number,
                        lifecycle_event=event_record.event_type,
                        phase=event_record.phase,
                        status=event_record.status,
                        occurred_at=event_record.occurred_at,
                    )
                )

            progress_sink = emit_issue_progress if routing.progress_callback is not None else None
            with bound_invocation_trace_context(
                build_invocation_trace_context(
                    repo_id=routing.effective_repo_id,
                    issue_number=issue.number,
                    run_history_store=routing.run_history_store,
                )
            ):
                with lifecycle_progress_scope(progress_sink):
                    return process_issue(
                        issue,
                        issue_kind,
                        process_runner=scoped_runner,
                        output_view=routing.output_view,
                        **routing.process_kwargs,
                    )
    except Exception as exc:  # noqa: BLE001 - 单个 Issue 的 I/O 不应中断本轮。
        _logger.error("Output routing failed for Issue #%d: %s", issue.number, exc)
        return 1
