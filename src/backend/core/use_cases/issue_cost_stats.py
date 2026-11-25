"""按仓库与 Issue 汇总本机运行历史及调用账本。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.core.shared.models.backlog import TokenUsageTotals
from backend.core.use_cases.agent_runner_invocation_token_stats import (
    parse_event_timestamp,
    resolve_invocation_model,
)
from backend.core.use_cases.agent_runner_token_stats import (
    _combine_totals,
    _parse_detail_json,
    _totals_from_payload,
)

_RUN_HISTORY_LIMIT = 10000
_ISSUE_INVOCATION_EVENT_LIMIT = 500
_INVOCATION_FINISHED_EVENT_TYPE = "invocation_finished"


@dataclass(frozen=True)
class IssueCostQuery:
    """Issue 账本查询窗口。"""

    repo_id: str | None
    start_at: datetime
    before_at: datetime
    run_limit: int = _RUN_HISTORY_LIMIT
    invocation_event_limit: int = _ISSUE_INVOCATION_EVENT_LIMIT


@dataclass(frozen=True)
class IssueModelUsage:
    """Issue 或单次执行在一个模型上的 Token 用量。"""

    model_id: str
    totals: TokenUsageTotals
    configuration_fallback_count: int = 0
    unresolved_model_alias_count: int = 0


@dataclass(frozen=True)
class IssueExecutionUsage:
    """一次本地 Issue 执行及其可关联调用用量。"""

    execution_id: str
    outcome: str
    trigger: str
    agent: str
    started_at: str
    finished_at: str
    duration_seconds: float
    error_summary: str | None
    model_usage: tuple[IssueModelUsage, ...]
    missing_usage_count: int


@dataclass(frozen=True)
class IssueCostHistoryEntry:
    """一个仓库 Issue 在选定期间的本地运行与模型用量。"""

    repo_id: str
    issue_number: int
    issue_title: str | None
    issue_url: str | None
    executions: tuple[IssueExecutionUsage, ...]
    model_usage: tuple[IssueModelUsage, ...]
    totals: TokenUsageTotals
    missing_usage_count: int
    truncated: bool


@dataclass(frozen=True)
class IssueCostLedger:
    """Issue 账本查询结果及无法关联到本地执行的调用数。"""

    entries: tuple[IssueCostHistoryEntry, ...]
    unlinked_invocation_count: int
    truncated_run_history: bool


@dataclass
class _MutableModelUsage:
    """聚合时使用的一个模型用量桶。"""

    totals: TokenUsageTotals = field(default_factory=TokenUsageTotals)
    configuration_fallback_count: int = 0
    unresolved_model_alias_count: int = 0

    def add(
        self,
        *,
        totals: TokenUsageTotals,
        configuration_fallback: bool,
        unresolved_model_alias: bool,
    ) -> None:
        """累加一条模型调用用量。"""
        self.totals = _combine_totals(self.totals, totals)
        self.configuration_fallback_count += int(configuration_fallback)
        self.unresolved_model_alias_count += int(unresolved_model_alias)


@dataclass
class _MutableExecutionUsage:
    """聚合时使用的一次执行行。"""

    run_record: object
    model_usage: dict[str, _MutableModelUsage] = field(default_factory=dict)
    missing_usage_count: int = 0


def _to_model_usage_entries(
    model_usage: dict[str, _MutableModelUsage],
) -> tuple[IssueModelUsage, ...]:
    """把可变模型桶转换为稳定排序的不可变结果。"""
    return tuple(
        IssueModelUsage(
            model_id=model_id,
            totals=usage_bucket.totals,
            configuration_fallback_count=usage_bucket.configuration_fallback_count,
            unresolved_model_alias_count=usage_bucket.unresolved_model_alias_count,
        )
        for model_id, usage_bucket in sorted(
            model_usage.items(),
            key=lambda entry: (-entry[1].totals.total_tokens, entry[0]),
        )
    )


def build_issue_cost_ledger(*, store: object, query: IssueCostQuery) -> IssueCostLedger:
    """按 Issue 執行区间关联本机调用记录，保留无用量执行及未关联计数。

    Args:
        store: 同时提供运行历史与调用事件读取能力的 Console SQLite store。
        query: 仓库过滤和 UTC 日期窗口。

    Returns:
        按 ``repo_id + issue_number`` 聚合的执行与模型用量。
    """
    run_reader = getattr(store, "list_runs_in_window", None)
    event_reader = getattr(store, "list_issue_invocation_events", None)
    if not callable(run_reader):
        return IssueCostLedger(entries=(), unlinked_invocation_count=0, truncated_run_history=False)

    run_records = list(
        run_reader(
            repo_id=query.repo_id,
            since_started_at=query.start_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            before_started_at=query.before_at.astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ),
            limit=query.run_limit,
        )
    )
    truncated_run_history = len(run_records) >= query.run_limit
    executions_by_issue: dict[tuple[str, int], list[_MutableExecutionUsage]] = {}
    for run_record in run_records:
        repo_id = str(getattr(run_record, "repo_id", "") or "")
        issue_number = getattr(run_record, "issue_number", None)
        if not repo_id or not isinstance(issue_number, int):
            continue
        issue_key = (repo_id, issue_number)
        execution_usage = _MutableExecutionUsage(run_record=run_record)
        executions_by_issue.setdefault(issue_key, []).append(execution_usage)

    unlinked_invocation_count = 0
    truncated_issue_keys: set[tuple[str, int]] = set()
    if callable(event_reader):
        for issue_key, execution_usages in executions_by_issue.items():
            repo_id, issue_number = issue_key
            invocation_events = list(
                event_reader(
                    repo_id=repo_id,
                    issue_number=issue_number,
                    limit=query.invocation_event_limit,
                )
            )
            if len(invocation_events) >= query.invocation_event_limit:
                truncated_issue_keys.add(issue_key)
            issue_run_windows = [
                (
                    execution_usage,
                    parse_event_timestamp(getattr(execution_usage.run_record, "started_at", None)),
                    parse_event_timestamp(getattr(execution_usage.run_record, "finished_at", None)),
                )
                for execution_usage in execution_usages
            ]
            for invocation_event in invocation_events:
                if str(getattr(invocation_event, "event_type", "")) != (
                    _INVOCATION_FINISHED_EVENT_TYPE
                ):
                    continue
                occurred_at = parse_event_timestamp(getattr(invocation_event, "occurred_at", None))
                if occurred_at is None:
                    continue
                matching_executions = [
                    execution_window
                    for execution_window in issue_run_windows
                    if execution_window[1] is not None
                    and execution_window[1] <= occurred_at
                    and (execution_window[2] is None or occurred_at <= execution_window[2])
                ]
                if not matching_executions:
                    if query.start_at <= occurred_at < query.before_at:
                        unlinked_invocation_count += 1
                    continue
                execution_usage = max(
                    matching_executions,
                    key=lambda execution_window: execution_window[1],
                )[0]
                detail = _parse_detail_json(getattr(invocation_event, "detail_json", "") or "")
                token_totals = _totals_from_payload(detail.get("token_usage"))
                if token_totals is None:
                    execution_usage.missing_usage_count += 1
                    continue
                model_id, configuration_fallback, unresolved_model_alias = resolve_invocation_model(
                    detail
                )
                model_usage = execution_usage.model_usage.setdefault(model_id, _MutableModelUsage())
                model_usage.add(
                    totals=token_totals,
                    configuration_fallback=configuration_fallback,
                    unresolved_model_alias=unresolved_model_alias is not None,
                )

    history_entries: list[IssueCostHistoryEntry] = []
    for issue_key, execution_usages in executions_by_issue.items():
        repo_id, issue_number = issue_key
        ordered_executions = sorted(
            execution_usages,
            key=lambda execution_usage: str(getattr(execution_usage.run_record, "started_at", "")),
        )
        issue_model_usage: dict[str, _MutableModelUsage] = {}
        execution_rows: list[IssueExecutionUsage] = []
        total_usage = TokenUsageTotals()
        missing_usage_count = 0
        for execution_index, execution_usage in enumerate(ordered_executions, start=1):
            run_record = execution_usage.run_record
            run_model_rows = _to_model_usage_entries(execution_usage.model_usage)
            for model_usage_entry in run_model_rows:
                issue_model_bucket = issue_model_usage.setdefault(
                    model_usage_entry.model_id, _MutableModelUsage()
                )
                issue_model_bucket.totals = _combine_totals(
                    issue_model_bucket.totals, model_usage_entry.totals
                )
                issue_model_bucket.configuration_fallback_count += (
                    model_usage_entry.configuration_fallback_count
                )
                issue_model_bucket.unresolved_model_alias_count += (
                    model_usage_entry.unresolved_model_alias_count
                )
                total_usage = _combine_totals(total_usage, model_usage_entry.totals)
            missing_usage_count += execution_usage.missing_usage_count
            execution_rows.append(
                IssueExecutionUsage(
                    execution_id=(f"{getattr(run_record, 'started_at', '')}#{execution_index}"),
                    outcome=str(getattr(run_record, "outcome", "unknown") or "unknown"),
                    trigger=str(getattr(run_record, "trigger", "") or "unknown"),
                    agent=str(getattr(run_record, "agent", "") or "unknown"),
                    started_at=str(getattr(run_record, "started_at", "")),
                    finished_at=str(getattr(run_record, "finished_at", "")),
                    duration_seconds=float(getattr(run_record, "duration_seconds", 0.0) or 0.0),
                    error_summary=getattr(run_record, "error_summary", None),
                    model_usage=run_model_rows,
                    missing_usage_count=execution_usage.missing_usage_count,
                )
            )
        history_entries.append(
            IssueCostHistoryEntry(
                repo_id=repo_id,
                issue_number=issue_number,
                issue_title=(
                    getattr(ordered_executions[-1].run_record, "issue_title", None)
                    if ordered_executions
                    else None
                ),
                issue_url=(
                    getattr(ordered_executions[-1].run_record, "issue_url", None)
                    if ordered_executions
                    else None
                ),
                executions=tuple(execution_rows),
                model_usage=_to_model_usage_entries(issue_model_usage),
                totals=total_usage,
                missing_usage_count=missing_usage_count,
                truncated=issue_key in truncated_issue_keys,
            )
        )
    history_entries.sort(
        key=lambda history_entry: (
            history_entry.repo_id,
            history_entry.issue_number,
        )
    )
    return IssueCostLedger(
        entries=tuple(history_entries),
        unlinked_invocation_count=unlinked_invocation_count,
        truncated_run_history=truncated_run_history,
    )


__all__ = [
    "IssueCostLedger",
    "IssueCostQuery",
    "IssueExecutionUsage",
    "IssueCostHistoryEntry",
    "IssueModelUsage",
    "build_issue_cost_ledger",
]
