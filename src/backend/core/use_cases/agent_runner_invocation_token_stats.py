"""从每次实际 Agent 调用生成一致的 token 用量统计。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.core.shared.models.backlog import (
    ModelTokenUsageStats,
    PrdModelTokenUsageEntry,
    PrdTokenUsageEntry,
    TokenUsageStats,
    TokenUsageTotals,
)
from backend.core.use_cases.agent_invocation_tracing import (
    PHASE_CLOSEOUT,
    PHASE_CONTENT_GENERATION,
    PHASE_FIX,
    PHASE_IMPLEMENTATION,
    PHASE_REBASE_RECOVERY,
    PHASE_REVIEW,
    PHASE_REVIEW_REPAIR,
    PHASE_SUPERVISOR,
    PHASE_SUPERVISOR_REPAIR,
    PHASE_UNSPECIFIED,
    PHASE_VERIFICATION,
    PHASE_VERIFICATION_RECOVERY,
)
from backend.core.use_cases.agent_runner_token_stats import (
    _combine_totals,
    _empty_totals,
    _list_window_runs,
    _merge_totals,
    _parse_detail_json,
    _run_events,
    _sum_totals,
    _totals_from_payload,
    aggregate_token_usage,
)

_logger = logging.getLogger(__name__)

_INVOCATION_FINISHED_EVENT_TYPE = "invocation_finished"
_MIRRORED_ATTEMPT_EVENT_TYPES = frozenset({"retry", "recovered"})

#: 调用阶段归并到页面使用的流程分组；未识别阶段单独列为 unknown，不猜测语义。
_INVOCATION_PHASE_TO_FLOW = {
    PHASE_IMPLEMENTATION: "implement",
    PHASE_VERIFICATION: "verify",
    PHASE_VERIFICATION_RECOVERY: "fix",
    PHASE_REVIEW: "supervise",
    PHASE_REVIEW_REPAIR: "fix",
    PHASE_SUPERVISOR: "supervise",
    PHASE_SUPERVISOR_REPAIR: "fix",
    PHASE_FIX: "fix",
    PHASE_REBASE_RECOVERY: "fix",
    PHASE_CLOSEOUT: "closeout",
    PHASE_CONTENT_GENERATION: "content_generation",
    PHASE_UNSPECIFIED: "unknown",
}


@dataclass(frozen=True)
class InvocationTokenUsageSummary:
    """基于每次实际 Agent 进程调用统一生成的 token 汇总。"""

    token_usage: TokenUsageStats
    token_usage_by_prd: tuple[PrdTokenUsageEntry, ...]
    token_usage_by_model: ModelTokenUsageStats


def _parse_event_timestamp(raw_timestamp: object) -> datetime | None:
    """解析调用 / lifecycle 时间戳；无效值返回 ``None``。"""
    if not isinstance(raw_timestamp, str):
        return None
    try:
        parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_timestamp.tzinfo is None:
        return None
    return parsed_timestamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class _InvocationUsageRecord:
    """已关联到 lifecycle run 的一条调用 usage。"""

    run_record: object
    repo_id: str
    issue_number: int
    agent_name: str
    flow_name: str
    model_id: str
    totals: TokenUsageTotals


@dataclass
class _TokenUsageBuckets:
    """累加调用与旧 lifecycle 回退行的多维 token 总量。"""

    by_flow: dict[str, TokenUsageTotals]
    by_agent: dict[str, TokenUsageTotals]
    by_model: dict[str, TokenUsageTotals]
    by_agent_model: dict[str, dict[str, TokenUsageTotals]]
    by_prd_model: dict[tuple[str, str | None, int | None, str], TokenUsageTotals]
    by_run: dict[str, TokenUsageTotals]

    @classmethod
    def empty(cls) -> _TokenUsageBuckets:
        """返回一组空汇总桶。"""
        return cls({}, {}, {}, {}, {}, {})

    def add_invocation(self, usage_record: _InvocationUsageRecord) -> None:
        """把一条实际调用用量累加到所有展示维度。"""
        run_id = str(getattr(usage_record.run_record, "run_id", ""))
        if not run_id:
            return
        _merge_totals(self.by_flow, usage_record.flow_name, usage_record.totals)
        _merge_totals(self.by_agent, usage_record.agent_name, usage_record.totals)
        _merge_totals(self.by_model, usage_record.model_id, usage_record.totals)
        _merge_totals(self.by_run, run_id, usage_record.totals)
        agent_models = self.by_agent_model.setdefault(usage_record.agent_name, {})
        _merge_totals(agent_models, usage_record.model_id, usage_record.totals)
        prd_model_key = (
            usage_record.repo_id,
            getattr(usage_record.run_record, "prd_path", None),
            usage_record.issue_number,
            usage_record.model_id,
        )
        current_prd_model_totals = self.by_prd_model.get(prd_model_key, _empty_totals())
        self.by_prd_model[prd_model_key] = _combine_totals(
            current_prd_model_totals,
            usage_record.totals,
        )

    def add_legacy_run(
        self,
        *,
        run_id: str,
        run_record: object,
        lifecycle_usage: TokenUsageStats,
    ) -> None:
        """为没有调用 usage 的旧 run 增加 lifecycle 账本回退数据。"""
        if run_id in self.by_run:
            return
        run_totals = _sum_totals(lifecycle_usage.by_flow.values())
        if run_totals.usage_count == 0:
            return
        self.by_run[run_id] = run_totals
        for flow_name, flow_totals in lifecycle_usage.by_flow.items():
            _merge_totals(self.by_flow, flow_name, flow_totals)
        for agent_name, agent_totals in lifecycle_usage.by_agent.items():
            _merge_totals(self.by_agent, agent_name, agent_totals)
            agent_models = self.by_agent_model.setdefault(agent_name, {})
            _merge_totals(agent_models, "unknown", agent_totals)
        _merge_totals(self.by_model, "unknown", run_totals)
        prd_model_key = (
            str(getattr(run_record, "repo_id", "") or ""),
            getattr(run_record, "prd_path", None),
            getattr(run_record, "issue_number", None),
            "unknown",
        )
        current_prd_model_totals = self.by_prd_model.get(prd_model_key, _empty_totals())
        self.by_prd_model[prd_model_key] = _combine_totals(current_prd_model_totals, run_totals)


def _build_run_window_index(
    run_records: list[object],
) -> tuple[
    dict[tuple[str, int], list[tuple[object, datetime, datetime | None]]],
    dict[str, object],
]:
    """为 invocation 时间戳关联建立按 Issue 的 run 窗口索引。"""
    run_windows_by_issue: dict[tuple[str, int], list[tuple[object, datetime, datetime | None]]] = {}
    run_records_by_id: dict[str, object] = {}
    for run_record in run_records:
        run_id = str(getattr(run_record, "run_id", ""))
        if run_id:
            run_records_by_id[run_id] = run_record
        repo_id = str(getattr(run_record, "repo_id", ""))
        issue_number = getattr(run_record, "issue_number", None)
        started_at = _parse_event_timestamp(getattr(run_record, "started_at", None))
        finished_at = _parse_event_timestamp(getattr(run_record, "finished_at", None))
        if not repo_id or not isinstance(issue_number, int) or started_at is None:
            continue
        issue_key = (repo_id, issue_number)
        run_windows_by_issue.setdefault(issue_key, []).append((run_record, started_at, finished_at))
    return run_windows_by_issue, run_records_by_id


def _read_invocation_usage_records(
    *,
    store: object,
    run_windows_by_issue: dict[tuple[str, int], list[tuple[object, datetime, datetime | None]]],
) -> tuple[list[_InvocationUsageRecord], bool]:
    """读取调用结束事件并通过时间窗关联到 lifecycle run。"""
    issue_event_reader = getattr(store, "list_issue_invocation_events", None)
    if not callable(issue_event_reader):
        return [], False
    usage_records: list[_InvocationUsageRecord] = []
    history_truncated = False
    for (repo_id, issue_number), issue_run_windows in run_windows_by_issue.items():
        try:
            invocation_events = list(
                issue_event_reader(repo_id=repo_id, issue_number=issue_number, limit=500)
            )
        except Exception as exc:  # noqa: BLE001 - one Issue must not break the Stats page.
            _logger.warning(
                "Failed to read invocation token usage for %s#%s: %s",
                repo_id,
                issue_number,
                exc,
            )
            continue
        history_truncated = history_truncated or len(invocation_events) >= 500
        for invocation_event in invocation_events:
            if str(getattr(invocation_event, "event_type", "")) != (
                _INVOCATION_FINISHED_EVENT_TYPE
            ):
                continue
            occurred_at = _parse_event_timestamp(getattr(invocation_event, "occurred_at", None))
            if occurred_at is None:
                continue
            matching_run_windows = [
                run_window
                for run_window in issue_run_windows
                if run_window[1] <= occurred_at
                and (run_window[2] is None or occurred_at <= run_window[2])
            ]
            if not matching_run_windows:
                continue
            run_record = max(matching_run_windows, key=lambda run_window: run_window[1])[0]
            detail_json = getattr(invocation_event, "detail_json", "") or ""
            detail = _parse_detail_json(detail_json)
            usage_totals = _totals_from_payload(detail.get("token_usage"))
            if usage_totals is None:
                continue
            model_id = next(
                (
                    model_value.strip()
                    for model_value in (
                        detail.get("reported_model"),
                        detail.get("requested_model"),
                    )
                    if isinstance(model_value, str) and model_value.strip()
                ),
                "unknown",
            )
            phase_name = str(getattr(invocation_event, "phase", "") or detail.get("phase") or "")
            usage_records.append(
                _InvocationUsageRecord(
                    run_record=run_record,
                    repo_id=repo_id,
                    issue_number=issue_number,
                    agent_name=str(getattr(invocation_event, "agent", "") or "unknown"),
                    flow_name=_INVOCATION_PHASE_TO_FLOW.get(phase_name, "unknown"),
                    model_id=model_id,
                    totals=usage_totals,
                )
            )
    return usage_records, history_truncated


def _build_prd_usage_entries(
    *,
    totals_by_run: dict[str, TokenUsageTotals],
    run_records_by_id: dict[str, object],
) -> tuple[PrdTokenUsageEntry, ...]:
    """把 run 汇总合并成排序后的 PRD token 用量列表。"""
    totals_by_prd: dict[tuple[str | None, str | None, int | None], TokenUsageTotals] = {}
    run_counts_by_prd: dict[tuple[str | None, str | None, int | None], int] = {}
    for run_id, run_totals in totals_by_run.items():
        run_record = run_records_by_id[run_id]
        prd_key = (
            getattr(run_record, "repo_id", None),
            getattr(run_record, "prd_path", None),
            getattr(run_record, "issue_number", None),
        )
        totals_by_prd[prd_key] = _combine_totals(
            totals_by_prd.get(prd_key, _empty_totals()), run_totals
        )
        run_counts_by_prd[prd_key] = run_counts_by_prd.get(prd_key, 0) + 1
    entries = [
        PrdTokenUsageEntry(
            repo_id=prd_key[0],
            prd_path=prd_key[1],
            issue_number=prd_key[2],
            run_count=run_counts_by_prd[prd_key],
            totals=prd_totals,
        )
        for prd_key, prd_totals in totals_by_prd.items()
    ]
    entries.sort(
        key=lambda entry: (
            -entry.totals.total_tokens,
            entry.repo_id or "",
            entry.issue_number or 0,
        )
    )
    return tuple(entries)


def _build_prd_model_usage_entries(
    totals_by_prd_model: dict[tuple[str, str | None, int | None, str], TokenUsageTotals],
) -> tuple[PrdModelTokenUsageEntry, ...]:
    """把调用模型汇总转换成排序后的 PRD / 模型条目。"""
    entries = [
        PrdModelTokenUsageEntry(
            repo_id=repo_id,
            prd_path=prd_path,
            issue_number=issue_number,
            model_id=model_id,
            totals=totals,
        )
        for (
            repo_id,
            prd_path,
            issue_number,
            model_id,
        ), totals in totals_by_prd_model.items()
    ]
    entries.sort(
        key=lambda entry: (
            -entry.totals.total_tokens,
            entry.repo_id,
            entry.issue_number or 0,
            entry.model_id,
        )
    )
    return tuple(entries)


def build_invocation_token_usage_summary(
    *,
    store: object,
    run_records: list[object],
    events_by_run: dict[str, list[object]] | None = None,
) -> InvocationTokenUsageSummary:
    """从实际进程调用账本统一汇总流程、Agent、PRD 与模型用量。

    调用账本的 run id 是每次 Issue 执行唯一值，而生命周期 run id 是 PRD
    级身份，因此通过同一仓库 / Issue 的时间窗口关联两本账。``reported_model``
    优先于 ``requested_model``；两者都缺失时保留为 ``unknown``。仅当某个 run
    完全没有可用调用 usage 时，才用生命周期事件兼容旧数据；缺失 usage 不估算。

    Args:
        store: 同时提供调用账本与生命周期账本读取能力的存储对象。
        run_records: 当前统计窗口内已加载的 lifecycle run。
        events_by_run: 调用方已加载的 lifecycle 事件；缺省时仅在旧数据回退路径读取。

    Returns:
        各维度使用同一组实际调用事实生成的汇总；当某 Issue 调用历史达到读取上限时，
        ``token_usage_by_model.truncated`` 为 ``True``。
    """
    run_windows_by_issue, run_records_by_id = _build_run_window_index(run_records)
    invocation_usage_records, history_truncated = _read_invocation_usage_records(
        store=store,
        run_windows_by_issue=run_windows_by_issue,
    )
    usage_buckets = _TokenUsageBuckets.empty()
    for usage_record in invocation_usage_records:
        usage_buckets.add_invocation(usage_record)

    for run_id, run_record in run_records_by_id.items():
        if run_id in usage_buckets.by_run:
            continue
        lifecycle_events = (
            events_by_run.get(run_id, [])
            if events_by_run is not None
            else _run_events(store, run_id)
        )
        legacy_usage_events = [
            lifecycle_event
            for lifecycle_event in lifecycle_events
            if str(getattr(lifecycle_event, "event_type", "")) not in _MIRRORED_ATTEMPT_EVENT_TYPES
        ]
        usage_buckets.add_legacy_run(
            run_id=run_id,
            run_record=run_record,
            lifecycle_usage=aggregate_token_usage(legacy_usage_events),
        )

    return InvocationTokenUsageSummary(
        token_usage=TokenUsageStats(
            by_flow=usage_buckets.by_flow,
            by_agent=usage_buckets.by_agent,
        ),
        token_usage_by_prd=_build_prd_usage_entries(
            totals_by_run=usage_buckets.by_run,
            run_records_by_id=run_records_by_id,
        ),
        token_usage_by_model=ModelTokenUsageStats(
            by_model=usage_buckets.by_model,
            by_agent=usage_buckets.by_agent_model,
            by_prd=_build_prd_model_usage_entries(usage_buckets.by_prd_model),
            truncated=history_truncated,
        ),
    )


def build_model_token_usage_stats(
    *,
    store: object,
    run_records: list[object],
) -> ModelTokenUsageStats:
    """仅返回统一调用汇总中的模型维度。

    Args:
        store: 同时提供调用与生命周期账本读取能力的存储对象。
        run_records: 当前统计窗口内已加载的 lifecycle run。

    Returns:
        模型、Agent 与 PRD 维度的调用 usage 汇总。
    """
    return build_invocation_token_usage_summary(
        store=store,
        run_records=run_records,
    ).token_usage_by_model


def build_token_usage_stats_for_issue(
    *,
    store: object,
    repo_id: str | None,
    days: int,
    issue_number: int,
    now: datetime | None = None,
) -> TokenUsageStats:
    """构建单个 Issue（PRD）视角的按流程 / 按 agent 汇总。

    Args:
        store: 同时提供调用与生命周期账本读取能力的存储对象。
        repo_id: 仓库过滤；``None`` 表示全部仓库。
        days: 时间窗口天数（内部钳制 1–365）。
        issue_number: 只统计该 Issue。
        now: 统计基准时间；缺省取当前 UTC 时间。

    Returns:
        该 Issue 的流程与 Agent 用量汇总。
    """
    issue_runs = [
        run_record
        for run_record in _list_window_runs(store, repo_id=repo_id, days=days, now=now)
        if run_record.issue_number == issue_number
    ]
    return build_invocation_token_usage_summary(
        store=store,
        run_records=issue_runs,
    ).token_usage


__all__ = [
    "InvocationTokenUsageSummary",
    "build_invocation_token_usage_summary",
    "build_model_token_usage_stats",
    "build_token_usage_stats_for_issue",
]
