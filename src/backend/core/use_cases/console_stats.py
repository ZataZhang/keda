"""管理终端的完成度统计（实时 GitHub 口径 + 本地历史趋势）。

实时口径：

- 对每个 workflow label 以 ``state="all"`` 查询 Issue 并按编号去重。
- ``completed``：closed 且不含 failed/blocked label。
- ``failed`` / ``blocked``：含对应 label（无论 open/closed）。
- ``open_in_pipeline``：open 且不含 failed/blocked label。
- ``completion_rate = completed / total_tracked``，分母为 0 时为 ``None``。

历史趋势直接委托 ``IRunHistoryStore.daily_run_trend``（SQLite 按天聚合）。
GitHub 仍是 workflow 状态唯一事实来源；SQLite 只反映 runner 处理留痕。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.interfaces.runner_console import (
    DailyRunTrendEntry,
    IRunHistoryStore,
    PerformanceAttemptRecord,
)
from backend.core.shared.statistics import linear_percentile
from backend.core.shared.models.agent_runner import (
    IssueSummary,
    RepositoryRunContext,
)

_logger = logging.getLogger(__name__)

#: 单 label 查询上限；命中上限时标记 truncated，避免静默截断。
_PER_LABEL_QUERY_LIMIT = 200

#: 跨仓库并发的线程数上限；沿用 ``agent_runner_monitor`` 的既有范式。
_MAX_CONCURRENT_REPOS = 5


@dataclass(frozen=True)
class RepositoryCompletionStats:
    """单个仓库的实时完成度统计。"""

    repo_id: str
    display_name: str
    total_tracked: int
    completed: int
    failed: int
    blocked: int
    open_in_pipeline: int
    completion_rate: float | None
    truncated: bool
    error: str | None = None


@dataclass(frozen=True)
class FailureTypeCount:
    """一类非成功 attempt 的数量。"""

    failure_type: str
    count: int


@dataclass(frozen=True)
class AttemptPerformanceGroup:
    """一个 Agent 或预设分组的 attempt 统计。"""

    repo_id: str
    agent: str
    preset: str | None
    model: str | None
    attempt_count: int
    success_count: int
    non_success_count: int
    success_rate: float
    non_success_rate: float
    p50_duration_seconds: float | None
    p90_duration_seconds: float | None
    failure_types: tuple[FailureTypeCount, ...]


@dataclass(frozen=True)
class RunOutcomePerformanceGroup:
    """一个最终任务结果分组的 run 耗时统计。"""

    repo_id: str
    outcome: str
    run_count: int
    p50_duration_seconds: float | None
    p90_duration_seconds: float | None


@dataclass(frozen=True)
class AgentPerformanceStats:
    """Stats 页使用的 Agent / 预设 attempt 与整项任务统计。"""

    repo_id: str | None
    window_days: int
    agents: tuple[AttemptPerformanceGroup, ...]
    presets: tuple[AttemptPerformanceGroup, ...]
    unbound_preset_attempt_count: int
    runs: tuple[RunOutcomePerformanceGroup, ...]


def _workflow_labels(context: RepositoryRunContext) -> tuple[str, ...]:
    labels = context.config.labels
    return (
        labels.ready,
        labels.running,
        labels.supervising,
        labels.review,
        labels.failed,
        labels.blocked,
    )


def _is_closed(issue: IssueSummary) -> bool:
    return issue.state.upper() == "CLOSED"


def build_completion_stats(
    *,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> RepositoryCompletionStats:
    """统计一个仓库被 agent workflow 跟踪过的 Issue 完成度。

    Args:
        context: 仓库运行上下文。
        github_client: 该仓库的 GitHub 客户端。

    Returns:
        RepositoryCompletionStats: 实时完成度统计；GitHub 查询失败时
        返回带 ``error`` 的空统计，不让单仓库失败拖死整个统计页。
    """
    tracked_issues: dict[int, IssueSummary] = {}
    truncated = False
    try:
        for workflow_label in _workflow_labels(context):
            labeled_issues = github_client.list_issues_by_label(
                workflow_label, _PER_LABEL_QUERY_LIMIT, state="all"
            )
            if len(labeled_issues) >= _PER_LABEL_QUERY_LIMIT:
                truncated = True
            for issue in labeled_issues:
                tracked_issues[issue.number] = issue
    except Exception as exc:  # noqa: BLE001 - isolate per-repo stats failures.
        _logger.warning("Completion stats unavailable for '%s': %s", context.repo_id, exc)
        return RepositoryCompletionStats(
            repo_id=context.repo_id,
            display_name=context.display_name,
            total_tracked=0,
            completed=0,
            failed=0,
            blocked=0,
            open_in_pipeline=0,
            completion_rate=None,
            truncated=False,
            error=str(exc),
        )

    failed_label = context.config.labels.failed
    blocked_label = context.config.labels.blocked
    completed_count = 0
    failed_count = 0
    blocked_count = 0
    open_in_pipeline_count = 0
    for issue in tracked_issues.values():
        has_failed = failed_label in issue.labels
        has_blocked = blocked_label in issue.labels
        if has_failed:
            failed_count += 1
        if has_blocked:
            blocked_count += 1
        if _is_closed(issue):
            if not has_failed and not has_blocked:
                completed_count += 1
        elif not has_failed and not has_blocked:
            open_in_pipeline_count += 1

    total_tracked = len(tracked_issues)
    completion_rate = completed_count / total_tracked if total_tracked > 0 else None
    return RepositoryCompletionStats(
        repo_id=context.repo_id,
        display_name=context.display_name,
        total_tracked=total_tracked,
        completed=completed_count,
        failed=failed_count,
        blocked=blocked_count,
        open_in_pipeline=open_in_pipeline_count,
        completion_rate=completion_rate,
        truncated=truncated,
    )


def _build_stats_for_context(
    context: RepositoryRunContext,
    github_client_factory: Callable[[Path], IGitHubClient],
) -> RepositoryCompletionStats:
    """单仓库统计的兜底包装：客户端构建或查询失败都降级为该行 ``error``。"""
    try:
        return build_completion_stats(
            context=context,
            github_client=github_client_factory(context.repo_path),
        )
    except Exception as exc:  # noqa: BLE001 - isolate per-repo stats failures.
        _logger.warning("Completion stats unavailable for '%s': %s", context.repo_id, exc)
        return RepositoryCompletionStats(
            repo_id=context.repo_id,
            display_name=context.display_name,
            total_tracked=0,
            completed=0,
            failed=0,
            blocked=0,
            open_in_pipeline=0,
            completion_rate=None,
            truncated=False,
            error=str(exc),
        )


def build_completion_stats_overview(
    *,
    contexts: list[RepositoryRunContext],
    github_client_factory: Callable[[Path], IGitHubClient],
) -> list[RepositoryCompletionStats]:
    """对全部仓库构建实时完成度统计（线程池并发）。

    每个仓库的统计相互独立，逐仓库在 ``gh`` 子进程上串行会随仓库数线性
    变慢；改为线程池并发后整体落在秒级。``executor.map`` 保证返回顺序与
    入参 ``contexts`` 一致；单仓库失败（客户端构建或 GitHub 查询）已在
    ``_build_stats_for_context`` 内降级为该仓库条目的 ``error`` 字段，
    不会拖垮整页统计。

    Args:
        contexts: 当前 enabled 的仓库运行上下文。
        github_client_factory: 按仓库路径构建 GitHub 客户端的工厂。

    Returns:
        与 ``contexts`` 顺序一致的各仓库完成度统计列表。
    """
    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_REPOS) as executor:
        return list(
            executor.map(
                lambda context: _build_stats_for_context(context, github_client_factory),
                contexts,
            )
        )


def build_run_history_trend(
    *,
    store: IRunHistoryStore,
    repo_id: str | None,
    days: int,
) -> list[DailyRunTrendEntry]:
    """读取本地运行历史的按天趋势。

    Args:
        store: 运行历史存储端口。
        repo_id: 仓库过滤；``None`` 表示全部仓库。
        days: 回看天数（1-365 之间截断）。
    """
    bounded_days = min(max(days, 1), 365)
    return store.daily_run_trend(repo_id=repo_id, days=bounded_days)


def build_agent_performance_stats(
    *,
    store: IRunHistoryStore,
    repo_id: str | None,
    days: int,
    reference_now: datetime | None = None,
) -> AgentPerformanceStats:
    """按 attempt 和最终任务结果构建执行表现统计。

    Args:
        store: 运行历史存储端口。
        repo_id: 仓库过滤；``None`` 表示全部仓库。
        days: 回看天数，最终限制在 1–365 天。
        reference_now: 可注入的 UTC 参考时间，供确定性验证使用。

    Returns:
        按 Agent、历史 preset 快照与 run 最终结果独立分组的统计。
    """
    bounded_days = min(max(days, 1), 365)
    window_end = reference_now or datetime.now(timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    since = (window_end - timedelta(days=bounded_days)).isoformat(timespec="seconds")
    records = store.list_agent_performance_records(repo_id=repo_id, since=since)

    agent_groups: dict[tuple[str, str | None, str, str | None], list[PerformanceAttemptRecord]] = (
        defaultdict(list)
    )
    preset_groups: dict[tuple[str, str | None, str, str | None], list[PerformanceAttemptRecord]] = (
        defaultdict(list)
    )
    unbound_preset_attempt_count = 0
    for attempt_record in records.attempts:
        agent_name = attempt_record.agent or "未记录"
        agent_groups[(attempt_record.repo_id, None, agent_name, None)].append(attempt_record)
        if not attempt_record.preset:
            unbound_preset_attempt_count += 1
            continue
        preset_groups[
            (
                attempt_record.repo_id,
                attempt_record.preset,
                agent_name,
                attempt_record.model,
            )
        ].append(attempt_record)

    outcome_groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run_record in records.runs:
        if run_record.outcome not in {"completed", "failed", "blocked"}:
            continue
        outcome_groups[(run_record.repo_id, run_record.outcome)].append(run_record.duration_seconds)

    outcome_order = {"completed": 0, "failed": 1, "blocked": 2}
    run_groups: list[RunOutcomePerformanceGroup] = []
    for (run_repo_id, outcome), durations in sorted(
        outcome_groups.items(),
        key=lambda entry: (entry[0][0], outcome_order[entry[0][1]]),
    ):
        valid_durations = sorted(duration for duration in durations if duration >= 0)
        run_groups.append(
            RunOutcomePerformanceGroup(
                repo_id=run_repo_id,
                outcome=outcome,
                run_count=len(durations),
                p50_duration_seconds=linear_percentile(valid_durations, 0.5),
                p90_duration_seconds=linear_percentile(valid_durations, 0.9),
            )
        )

    return AgentPerformanceStats(
        repo_id=repo_id,
        window_days=bounded_days,
        agents=_build_attempt_performance_groups(agent_groups),
        presets=_build_attempt_performance_groups(preset_groups),
        unbound_preset_attempt_count=unbound_preset_attempt_count,
        runs=tuple(run_groups),
    )


def _build_attempt_performance_groups(
    grouped_attempts: dict[tuple[str, str | None, str, str | None], list[PerformanceAttemptRecord]],
) -> tuple[AttemptPerformanceGroup, ...]:
    """将相同分组键的 attempt 汇总为展示行。"""
    performance_groups: list[AttemptPerformanceGroup] = []
    for (repo_id, preset_name, agent_name, model_id), attempts in sorted(
        grouped_attempts.items(),
        key=lambda entry: (
            entry[0][0],
            entry[0][1] or "",
            entry[0][2],
            entry[0][3] or "",
        ),
    ):
        success_count = sum(attempt.failure_type == "success" for attempt in attempts)
        non_success_count = len(attempts) - success_count
        failure_counts: dict[str, int] = defaultdict(int)
        valid_durations = sorted(
            attempt.duration_seconds for attempt in attempts if attempt.duration_seconds >= 0
        )
        for attempt in attempts:
            if attempt.failure_type != "success":
                failure_counts[attempt.failure_type] += 1
        attempt_count = len(attempts)
        performance_groups.append(
            AttemptPerformanceGroup(
                repo_id=repo_id,
                agent=agent_name,
                preset=preset_name,
                model=model_id,
                attempt_count=attempt_count,
                success_count=success_count,
                non_success_count=non_success_count,
                success_rate=success_count / attempt_count,
                non_success_rate=non_success_count / attempt_count,
                p50_duration_seconds=linear_percentile(valid_durations, 0.5),
                p90_duration_seconds=linear_percentile(valid_durations, 0.9),
                failure_types=tuple(
                    FailureTypeCount(failure_type=failure_type, count=count)
                    for failure_type, count in sorted(failure_counts.items())
                ),
            )
        )
    return tuple(performance_groups)
