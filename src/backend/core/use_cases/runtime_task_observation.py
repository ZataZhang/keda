"""协调运行摘要中的历史终态与跨重试复用的生命周期会话。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from backend.core.shared.interfaces.runner_console import IPrdLifecycleStore, PrdLifecycleRunRecord
from backend.core.shared.models.runtime_task import RuntimeTask, RuntimeTaskStatus
from backend.core.use_cases.agent_runner_lifecycle import derive_current_phase, is_terminal_event

SOURCE_PRD_LIFECYCLE = "prd_lifecycle"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeObservationContext:
    """一批任务共享的只读生命周期来源、参考时间与降级记录。"""

    lifecycle_store: IPrdLifecycleStore | None
    reference_time: datetime
    unavailable_sources: set[str]


def parse_runtime_timestamp(value: str | None) -> datetime | None:
    """把账本里的 ISO-8601 字符串解析成带时区时间；坏值返回 ``None``。

    Args:
        value: 账本存储的 ISO-8601 时间戳，可能为空、缺失时区或格式损坏。

    Returns:
        带 UTC 时区的时间；无法解析时返回 ``None``，调用方据此保留「未知」。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconcile_runtime_task(
    task: RuntimeTask,
    lifecycle_run: PrdLifecycleRunRecord | None,
    *,
    context: RuntimeObservationContext,
) -> RuntimeTask:
    """用新会话开始事件协调运行窗口，保留无法确认的状态。

    Args:
        task: 快照与运行账本组装的任务。
        lifecycle_run: 同一 Issue 最近的生命周期记录；它会跨重试复用。
        context: 生命周期读端、参考时间与读取失败披露。

    Returns:
        运行窗口一致的摘要；标签与历史终态冲突时返回未知状态。
    """
    if task.status not in {
        RuntimeTaskStatus.RUNNING,
        RuntimeTaskStatus.COMPLETED,
        RuntimeTaskStatus.UNKNOWN,
    }:
        return task
    lifecycle_events = []
    if lifecycle_run is not None and context.lifecycle_store is not None:
        try:
            lifecycle_events = context.lifecycle_store.list_lifecycle_events(
                run_id=lifecycle_run.run_id
            )
        except Exception as read_error:  # noqa: BLE001 - 单项读失败不影响其他摘要。
            _logger.warning(
                "Runtime lifecycle events unreadable for %s: %s", task.task_key, read_error
            )
            context.unavailable_sources.add(SOURCE_PRD_LIFECYCLE)

    recorded_finish = parse_runtime_timestamp(task.finished_at)
    session_starts = [
        parsed_start
        for event in lifecycle_events
        if event.event_type in {"started", "claimed"}
        if (parsed_start := parse_runtime_timestamp(event.occurred_at)) is not None
    ]
    # 生命周期 run 的 started_at 是 Issue 的首次开始时间，重试必须优先取新会话事件。
    session_start = (
        max(session_starts)
        if session_starts
        else (parse_runtime_timestamp(lifecycle_run.started_at) if lifecycle_run else None)
    )
    session_events = [
        event
        for event in lifecycle_events
        if (event_time := parse_runtime_timestamp(event.occurred_at)) is not None
        and session_start is not None
        and event_time >= session_start
    ]
    terminal_events = [event for event in session_events if is_terminal_event(event.event_type)]
    session_terminal = max(terminal_events, key=lambda event: event.occurred_at, default=None)
    if (
        lifecycle_run is not None
        and not lifecycle_run.finished_at
        and session_terminal is None
        and session_start is not None
        and (recorded_finish is None or session_start > recorded_finish)
    ):
        session_phase = derive_current_phase(lifecycle_run, session_events).value
        return replace(
            task,
            status=RuntimeTaskStatus.RUNNING,
            status_reason="生命周期记录了未收口的执行会话",
            started_at=session_start.isoformat(timespec="seconds"),
            finished_at=None,
            duration_seconds=max(0.0, (context.reference_time - session_start).total_seconds()),
            last_outcome=None,
            agent=None,
            preset=None,
            model=None,
            attempt_number=None,
            phase=session_phase
            if session_phase != "none"
            else (task.phase if not task.finished_at else None),
        )
    if task.status is RuntimeTaskStatus.RUNNING and lifecycle_run:
        terminal_finish = (
            parse_runtime_timestamp(session_terminal.occurred_at) if session_terminal else None
        )
        lifecycle_finish = terminal_finish or parse_runtime_timestamp(lifecycle_run.finished_at)
        if (
            lifecycle_finish is not None
            and session_start is not None
            and (recorded_finish is None or lifecycle_finish > recorded_finish)
        ):
            task = replace(
                task,
                started_at=session_start.isoformat(timespec="seconds"),
                finished_at=lifecycle_finish.isoformat(timespec="seconds"),
                last_outcome=(
                    derive_current_phase(lifecycle_run, [session_terminal]).value
                    if session_terminal
                    else lifecycle_run.outcome
                ),
                duration_seconds=max(0.0, (lifecycle_finish - session_start).total_seconds()),
                agent=None,
                preset=None,
                model=None,
                attempt_number=None,
                phase=derive_current_phase(lifecycle_run, session_events).value,
            )
    if task.status is RuntimeTaskStatus.RUNNING and task.finished_at:
        return replace(
            task,
            status=RuntimeTaskStatus.UNKNOWN,
            status_reason="监控标签仍标记运行，但最近运行已结束；当前执行状态待核实",
        )
    return task
