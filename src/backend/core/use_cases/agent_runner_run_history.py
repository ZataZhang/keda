"""Agent runner run-history side-channel recording."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    IRunHistoryStore,
    RunRecord,
)
from backend.core.shared.models.agent_runner import (
    AttemptResult,
    FailureType,
    IssueSummary,
    PhaseDuration,
)
from backend.core.use_cases.agent_invocation_tracing import (
    EVENT_INVOCATION_FINISHED,
    EVENT_INVOCATION_STARTED,
    InvocationTimelineRow,
    build_invocation_timeline,
    resolve_invocation_store,
)

_logger = logging.getLogger(__name__)

#: 实时 attempt 历史评论最多渲染的行数，避免评论体随反复 claim 无限增长。
ATTEMPT_HISTORY_MAX_ROWS = 50

# 单次运行详情查询的历史扫描上限，避免 Issue 长期重试导致无界读取。
_RUN_ATTEMPT_DETAIL_LIMIT = 5000
_RUN_INVOCATION_DETAIL_LIMIT = 5000

__all__ = [
    "ATTEMPT_HISTORY_MAX_ROWS",
    "IssueAttemptTrail",
    "RunAttemptWindow",
    "append_run_record",
    "load_run_attempt_details",
    "load_run_invocation_details",
    "load_issue_attempt_trail",
]


def append_run_record(
    *,
    run_history_store: IRunHistoryStore | None,
    repo_id: str,
    repo_path: Path,
    issue: IssueSummary,
    trigger: str,
    agent: str,
    outcome: str,
    error_summary: str | None,
    started_at: "datetime",
) -> None:
    """旁路写入一条运行记录；任何失败都不阻断 runner。

    Args:
        run_history_store: 运行历史存储；为 ``None`` 时直接跳过。
        repo_id: 目标仓库标识。
        repo_path: 目标仓库路径。
        issue: 本次运行处理的 Issue。
        trigger: 触发来源（如 ``cli_run``）。
        agent: 实际使用的 AI agent 名称。
        outcome: 运行结果摘要标识。
        error_summary: 失败时的错误摘要；成功时为 ``None``。
        started_at: 运行开始时间（UTC）。
    """
    if run_history_store is None:
        return
    finished_at = datetime.now(timezone.utc)
    try:
        run_history_store.append_run(
            RunRecord(
                repo_id=repo_id,
                repo_path=str(repo_path),
                issue_number=issue.number,
                trigger=trigger,
                agent=agent,
                outcome=outcome,
                error_summary=error_summary,
                started_at=started_at.isoformat(timespec="seconds"),
                finished_at=finished_at.isoformat(timespec="seconds"),
                duration_seconds=(finished_at - started_at).total_seconds(),
                issue_title=issue.title or None,
                issue_url=issue.url or None,
            )
        )
    except Exception as record_exc:  # noqa: BLE001 - side channel only.
        _logger.warning(
            "Failed to record run history for Issue #%d: %s",
            issue.number,
            record_exc,
        )


@dataclass(frozen=True)
class IssueAttemptTrail:
    """某个 Issue 已落库的 attempt 轨迹（渲染视图）。

    Attributes:
        attempts: 按时间正序排列的 attempt；无存储或读取失败时为空列表。
        older_omitted: 是否因超出 ``ATTEMPT_HISTORY_MAX_ROWS`` 而丢弃了更早的记录。
    """

    attempts: list[AttemptResult]
    older_omitted: bool


@dataclass(frozen=True)
class RunAttemptWindow:
    """指定一次运行的仓库、Issue 与时间窗口。"""

    repo_id: str
    issue_number: int
    started_at: datetime
    finished_at: datetime


def load_issue_attempt_trail(
    *,
    run_history_store: IRunHistoryStore | None,
    repo_id: str,
    issue_number: int,
) -> IssueAttemptTrail:
    """读取某个 Issue 跨 agent、跨 claim 的完整 attempt 轨迹。

    runner 内存中的 attempt 列表在跨 agent fallback 和重新 claim 时都会从 1
    重新开始，因此只能代表"本轮"；存储侧按 Issue 累积全部尝试，是实时评论的
    渲染源。任何读取失败都降级为空轨迹，由调用方回退到内存列表。

    Args:
        run_history_store: 运行历史存储；为 ``None`` 时返回空轨迹。
        repo_id: 目标仓库标识。
        issue_number: 目标 Issue 编号。

    Returns:
        IssueAttemptTrail: 轨迹与截断状态。
    """
    if run_history_store is None:
        return IssueAttemptTrail(attempts=[], older_omitted=False)
    try:
        stored_attempts = run_history_store.list_issue_attempts(
            repo_id=repo_id,
            issue_number=issue_number,
            limit=ATTEMPT_HISTORY_MAX_ROWS + 1,
        )
    except Exception as trail_exc:  # noqa: BLE001 - side channel only.
        _logger.warning(
            "Failed to load attempt trail for Issue #%d: %s",
            issue_number,
            trail_exc,
        )
        return IssueAttemptTrail(attempts=[], older_omitted=False)
    rendered_attempts = [
        _stored_attempt_to_result(stored_attempt)
        for stored_attempt in stored_attempts[-ATTEMPT_HISTORY_MAX_ROWS:]
    ]
    return IssueAttemptTrail(
        attempts=[attempt for attempt in rendered_attempts if attempt is not None],
        older_omitted=len(stored_attempts) > ATTEMPT_HISTORY_MAX_ROWS,
    )


def load_run_attempt_details(
    *,
    run_history_store: IRunHistoryStore | None,
    run_window: RunAttemptWindow,
) -> list[AttemptResult]:
    """加载指定运行时间窗口内已持久化的 Agent 尝试详情。

    Args:
        run_history_store: 运行历史存储；为 ``None`` 时返回空列表。
        run_window: 本次运行的仓库、Issue 和带时区起止时间。

    Returns:
        按时间正序排列的尝试详情；读取失败或窗口无效时返回空列表。
    """
    if (
        run_history_store is None
        or run_window.started_at.utcoffset() is None
        or run_window.finished_at.utcoffset() is None
        or run_window.finished_at < run_window.started_at
    ):
        return []
    try:
        stored_attempts = run_history_store.list_issue_attempts(
            repo_id=run_window.repo_id,
            issue_number=run_window.issue_number,
            limit=_RUN_ATTEMPT_DETAIL_LIMIT,
        )
    except Exception as trail_exc:  # noqa: BLE001 - read-only history must not break UI.
        _logger.warning(
            "Failed to load run attempt details for Issue #%d: %s",
            run_window.issue_number,
            trail_exc,
        )
        return []

    normalized_run_start = run_window.started_at.astimezone(timezone.utc)
    normalized_run_end = run_window.finished_at.astimezone(timezone.utc)
    matching_attempts: list[AttemptResult] = []
    for stored_attempt in stored_attempts:
        attempt_started_at = _parse_attempt_timestamp(stored_attempt.started_at)
        if attempt_started_at is None:
            continue
        if not normalized_run_start <= attempt_started_at <= normalized_run_end:
            continue
        rendered_attempt = _stored_attempt_to_result(stored_attempt)
        if rendered_attempt is not None:
            matching_attempts.append(rendered_attempt)
    return matching_attempts


def load_run_invocation_details(
    *,
    run_history_store: IRunHistoryStore | None,
    run_window: RunAttemptWindow,
) -> list[InvocationTimelineRow]:
    """读取指定运行时间窗口内 Agent 进程调用的执行器、模型与耗时。

    Args:
        run_history_store: 运行历史存储；不支持调用观测时返回空列表。
        run_window: 本次运行的仓库、Issue 和带时区起止时间。

    Returns:
        按调用开始顺序排列的 Agent 调用详情；旧库或读取失败时为空列表。
    """
    if (
        run_history_store is None
        or run_window.started_at.utcoffset() is None
        or run_window.finished_at.utcoffset() is None
        or run_window.finished_at < run_window.started_at
    ):
        return []
    invocation_store = resolve_invocation_store(run_history_store)
    if invocation_store is None:
        return []
    try:
        invocation_events = invocation_store.list_issue_invocation_events(
            repo_id=run_window.repo_id,
            issue_number=run_window.issue_number,
            limit=_RUN_INVOCATION_DETAIL_LIMIT,
        )
    except Exception as invocation_read_error:  # noqa: BLE001 - 观测读取失败不阻断详情页。
        _logger.warning(
            "Failed to load invocation details for Issue #%d: %s",
            run_window.issue_number,
            invocation_read_error,
        )
        return []

    normalized_run_start = run_window.started_at.astimezone(timezone.utc)
    normalized_run_end = run_window.finished_at.astimezone(timezone.utc)
    selected_invocation_ids: set[str] = set()
    for invocation_event in invocation_events:
        if invocation_event.event_type != EVENT_INVOCATION_STARTED:
            continue
        invocation_started_at = _parse_attempt_timestamp(invocation_event.occurred_at)
        if invocation_started_at is not None and (
            normalized_run_start <= invocation_started_at <= normalized_run_end
        ):
            selected_invocation_ids.add(invocation_event.invocation_id)

    selected_invocation_events = [
        invocation_event
        for invocation_event in invocation_events
        if invocation_event.invocation_id in selected_invocation_ids
        and invocation_event.event_type in (EVENT_INVOCATION_STARTED, EVENT_INVOCATION_FINISHED)
    ]
    return build_invocation_timeline(selected_invocation_events)


def _parse_attempt_timestamp(timestamp: str) -> datetime | None:
    """解析 SQLite 中的 attempt 时间戳，并归一为 UTC。"""
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_timestamp.tzinfo is None:
        parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
    return parsed_timestamp.astimezone(timezone.utc)


def _stored_attempt_to_result(attempt_record: AttemptRecord) -> AttemptResult | None:
    """把一条已落库的 attempt 记录还原为可渲染的 :class:`AttemptResult`。

    ``failure_type`` 落库时写的是枚举字面值；若历史行的取值已不在当前
    :class:`FailureType` 中（例如枚举更名后读到旧库），跳过该行并返回
    ``None``，而不是让整条轨迹渲染失败。阶段字段新增前的历史行仅对 runner
    固定生成、能唯一确定阶段的诊断前缀做兼容识别。
    """
    try:
        failure_type = FailureType(attempt_record.failure_type)
    except ValueError:
        _logger.warning(
            "Skipping stored attempt with unknown failure_type %r for Issue #%d.",
            attempt_record.failure_type,
            attempt_record.issue_number,
        )
        return None
    failure_phase = attempt_record.failure_phase
    if failure_phase is None:
        if attempt_record.detail.startswith(
            "Agent command failed before runner verification could start."
        ):
            failure_phase = "agent"
        elif attempt_record.detail.startswith("PRD delivery check failed."):
            failure_phase = "prd_delivery"
        elif attempt_record.detail.startswith("Verification before staging failed."):
            failure_phase = "verification"
        elif attempt_record.detail.startswith(
            "Verification after runner staged changes with git add -A failed."
        ) or attempt_record.detail.startswith("The runner could not process the commit request."):
            failure_phase = "commit"
        elif attempt_record.detail == "Agent produced no git commits.":
            failure_phase = "agent"
    return AttemptResult(
        attempt_number=attempt_record.attempt_number,
        failure_type=failure_type,
        recovered=attempt_record.recovered,
        detail=attempt_record.detail,
        agent=attempt_record.agent,
        started_at=attempt_record.started_at,
        finished_at=attempt_record.finished_at,
        duration_seconds=attempt_record.duration_seconds,
        preset=attempt_record.preset or "",
        model=attempt_record.model or "",
        failure_phase=failure_phase,
        phase_durations=tuple(
            PhaseDuration(name=phase_name, seconds=phase_seconds)
            for phase_name, phase_seconds in attempt_record.phase_durations
        ),
    )
