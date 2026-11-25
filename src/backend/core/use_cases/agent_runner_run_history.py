"""Agent runner run-history side-channel recording."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    InvocationEventRecord,
    IRunHistoryStore,
    RunInvocationLookup,
    RunRecord,
)
from backend.core.shared.models.agent_runner import (
    AttemptResult,
    FailureType,
    IssueSummary,
    PhaseDuration,
)
from backend.core.use_cases.agent_invocation_tracing import (
    EVENT_INVOCATION_STARTED,
    InvocationTimelineRow,
    active_invocation_trace_context,
    build_invocation_timeline,
)

_logger = logging.getLogger(__name__)

#: 实时 attempt 历史评论最多渲染的行数，避免评论体随反复 claim 无限增长。
ATTEMPT_HISTORY_MAX_ROWS = 50

# 单次运行详情查询的历史扫描上限，避免 Issue 长期重试导致无界读取。
_RUN_ATTEMPT_DETAIL_LIMIT = 5000

__all__ = [
    "ATTEMPT_HISTORY_MAX_ROWS",
    "IssueAttemptTrail",
    "RunAttemptWindow",
    "append_run_record",
    "load_run_attempt_details",
    "load_run_invocation_details",
    "load_run_executor_summaries",
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
    trace_context = active_invocation_trace_context()
    invocation_run_id = (
        trace_context.run_id
        if trace_context is not None
        and (trace_context.repo_id, trace_context.issue_number) == (repo_id, issue.number)
        else None
    )
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
                invocation_run_id=invocation_run_id,
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
    record_id: int | None = None
    invocation_run_id: str | None = None

    def invocation_lookup(self) -> RunInvocationLookup:
        """组装调用会话查询条件。

        Returns:
            仓库、Issue、UTC 起止时间和可选的调用会话 ID。
        """
        return (
            self.repo_id,
            self.issue_number,
            self.started_at.astimezone(timezone.utc).isoformat(),
            self.finished_at.astimezone(timezone.utc).isoformat(),
            self.invocation_run_id,
        )

    @classmethod
    def from_record(cls, run: RunRecord) -> RunAttemptWindow | None:
        """从存储记录还原带时区的运行窗口。

        Args:
            run: 持久化的运行记录。

        Returns:
            运行窗口；时间值无效时为空。
        """
        started_at = _parse_attempt_timestamp(run.started_at)
        finished_at = _parse_attempt_timestamp(run.finished_at)
        if started_at is None or finished_at is None or finished_at < started_at:
            return None
        return cls(
            repo_id=run.repo_id,
            issue_number=run.issue_number,
            started_at=started_at,
            finished_at=finished_at,
            record_id=run.record_id,
            invocation_run_id=run.invocation_run_id,
        )


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
    open_window: bool = False,
) -> list[InvocationTimelineRow]:
    """读取指定运行的调用详情，与 Stats 的执行器摘要共用会话归属。

    Args:
        run_history_store: 运行历史存储。
        run_window: 详情请求中的运行窗口和可选记录主键。
        open_window: 未指定结束时间的实时查询，按开始时间识别随后写入的汇总。

    Returns:
        按开始时间排列的调用详情；没有可归属的会话时为空。
    """
    if run_history_store is None:
        return []
    open_window = open_window and run_window.record_id is None
    # 汇总可能在查找记录和读取事件之间提交。兼容查询为空时重新查找一次。
    for lookup_attempt in range(2 if open_window else 1):
        try:
            stored_runs = run_history_store.find_runs_for_invocation_lookup(
                run_window.invocation_lookup(), open_window=open_window
            )
        except Exception as lookup_error:  # noqa: BLE001 - 旁路读取失败不阻断详情。
            _logger.warning("Failed to find run for invocation details: %s", lookup_error)
            return []
        matched_runs = [
            run
            for run in stored_runs
            if run_window.record_id is None or run.record_id == run_window.record_id
        ]
        if len(matched_runs) == 1:
            selected_window = RunAttemptWindow.from_record(matched_runs[0])
            return _load_run_invocation_timelines(run_history_store, [selected_window])[0]
        if stored_runs or run_window.record_id is not None:
            return []
        timeline = _load_run_invocation_timelines(run_history_store, [run_window])[0]
        if timeline or lookup_attempt == 1:
            return timeline
    return []


def load_run_executor_summaries(
    *,
    run_history_store: IRunHistoryStore | None,
    runs: Sequence[RunRecord],
) -> list[list[str]]:
    """汇总各次运行的真实执行器顺序，折叠连续重复并保留回切。

    Args:
        run_history_store: 运行历史存储。
        runs: Stats 当前批次的运行记录。

    Returns:
        与运行记录一一对应的执行器序列。
    """
    run_windows = [RunAttemptWindow.from_record(run) for run in runs]
    invocation_timelines = _load_run_invocation_timelines(run_history_store, run_windows)
    executor_summaries: list[list[str]] = []
    for invocation_timeline in invocation_timelines:
        executor_names: list[str] = []
        for invocation_row in invocation_timeline:
            if not executor_names or invocation_row.executor != executor_names[-1]:
                executor_names.append(invocation_row.executor)
        executor_summaries.append(executor_names)
    return executor_summaries


def _load_run_invocation_timelines(
    run_history_store: IRunHistoryStore | None,
    run_windows: Sequence[RunAttemptWindow | None],
) -> list[list[InvocationTimelineRow]]:
    """用同一批读取与会话归属生成列表和详情时间线。"""
    lookups = [window.invocation_lookup() for window in run_windows if window is not None]
    if run_history_store is None or not lookups:
        return [[] for _ in run_windows]
    try:
        invocation_events, peer_runs = run_history_store.read_run_invocation_events(lookups)
    except Exception as read_error:  # noqa: BLE001 - 旁路读取失败不阻断 Stats。
        _logger.warning("Failed to read run invocations: %s", read_error)
        return [[] for _ in run_windows]

    sessions: dict[tuple[str, int, str], list[InvocationEventRecord]] = {}
    for event in invocation_events:
        if event.issue_number is not None:
            sessions.setdefault((event.repo_id, event.issue_number, event.run_id), []).append(event)

    linked_sessions = {
        (window.repo_id, window.issue_number, window.invocation_run_id)
        for window in run_windows
        if window is not None and window.invocation_run_id is not None
    }
    legacy_windows: dict[tuple[str, int], set[RunAttemptWindow]] = {}
    for window in [*run_windows, *(RunAttemptWindow.from_record(run) for run in peer_runs)]:
        if window is not None and window.invocation_run_id is None:
            legacy_windows.setdefault((window.repo_id, window.issue_number), set()).add(window)

    legacy_sessions: dict[RunAttemptWindow, list[list[InvocationEventRecord]]] = {}
    for session_key, session_events in sessions.items():
        if session_key in linked_sessions:
            continue
        owner = _find_legacy_session_owner(
            session_events, legacy_windows.get(session_key[:2], set())
        )
        if owner is not None:
            legacy_sessions.setdefault(owner, []).append(session_events)

    timelines: list[list[InvocationTimelineRow]] = []
    for window in run_windows:
        selected_events: list[InvocationEventRecord] = []
        if window is not None and window.invocation_run_id is not None:
            selected_events = sessions.get(
                (window.repo_id, window.issue_number, window.invocation_run_id), []
            )
        elif window is not None and len(legacy_sessions.get(window, [])) == 1:
            selected_events = legacy_sessions[window][0]
        timelines.append(build_invocation_timeline(selected_events))
    return timelines


def _find_legacy_session_owner(
    session_events: Sequence[InvocationEventRecord],
    candidates: set[RunAttemptWindow],
) -> RunAttemptWindow | None:
    """只在完整调用会话能唯一归入一个旧窗口时返回归属。"""
    started_times = [
        event_time
        for event in session_events
        if event.event_type == EVENT_INVOCATION_STARTED
        and (event_time := _parse_attempt_timestamp(event.occurred_at)) is not None
    ]
    if not started_times:
        return None
    earliest_call, latest_call = min(started_times), max(started_times)

    # 旧 run_id 中的 UTC 秒取自进入 Issue 锁后、记录运行开始时间之前。
    session_stamp = session_events[0].run_id.rsplit("#", 1)[-1].split("-", 1)[0]
    try:
        created_at = datetime.strptime(session_stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        created_at = None
    matching_windows: list[RunAttemptWindow] = []
    for window in candidates:
        # 结束时间曾截断到秒，末秒内的毫秒级调用同样属于候选窗口。
        end_precision = (
            timedelta(seconds=1) if window.finished_at.microsecond == 0 else timedelta.resolution
        )
        if earliest_call < window.started_at or latest_call >= window.finished_at + end_precision:
            continue
        # 交接秒内才创建的会话可能来自尚未写入汇总的下一轮运行。
        if window.finished_at <= earliest_call and (
            created_at is None or created_at >= window.finished_at
        ):
            continue
        # UTC 秒不同就无法确认归属。前轮甚至可能没有汇总可供交叉检查。
        if created_at is not None and created_at != window.started_at.replace(microsecond=0):
            continue
        matching_windows.append(window)
    return matching_windows[0] if len(matching_windows) == 1 else None


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
