"""Token 用量基础解析与旧生命周期账本兼容。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from backend.core.shared.models.backlog import (
    ModelTokenUsageStats,
    PrdTokenUsageEntry,
    TokenUsageStats,
    TokenUsageTotals,
)

_logger = logging.getLogger(__name__)

#: attempt 族事件都携带同一实现流程的 usage；兼容汇总保留历史行为。
_ATTEMPT_FLOW_EVENT_TYPES = frozenset({"attempt", "retry", "recovered"})

#: attempt 族事件聚合出的 flow 名。
_IMPLEMENT_FLOW = "implement"

#: 观测事件的事件类型值。
_OBSERVATION_EVENT_TYPE = "agent_token_usage"

_USAGE_INT_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _parse_detail_json(detail_json: str) -> dict:
    """解析事件 detail；损坏行降级为空 dict（与账本读取口径一致）。"""
    try:
        parsed = json.loads(detail_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _empty_totals() -> TokenUsageTotals:
    """返回全零的汇总行（新增分组的初始值）。"""
    return TokenUsageTotals()


def _totals_from_payload(payload: object) -> TokenUsageTotals | None:
    """从 detail 里的 usage payload 构造单条汇总；无可提取字段返回 ``None``。"""
    if not isinstance(payload, dict):
        return None
    field_values: dict[str, int] = {}
    for field_name in _USAGE_INT_FIELDS:
        raw_value = payload.get(field_name)
        # bool 是 int 的子类，必须显式排除；负数视为畸形同样排除。
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
            continue
        field_values[field_name] = raw_value
    if not field_values:
        return None
    input_tokens = field_values.get("input_tokens", 0)
    output_tokens = field_values.get("output_tokens", 0)
    cache_read = field_values.get("cache_read_input_tokens", 0)
    cache_creation = field_values.get("cache_creation_input_tokens", 0)
    return TokenUsageTotals(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        # 总量 = 四项之和（实际处理量口径，含缓存命中与写入）。
        total_tokens=input_tokens + output_tokens + cache_read + cache_creation,
        usage_count=1,
    )


def _merge_totals(
    totals: dict[str, TokenUsageTotals],
    key: str,
    addition: TokenUsageTotals,
) -> None:
    """把一条用量累加进对应分组；分组不存在时初始化为全零再累加。"""
    current = totals.get(key)
    if current is None:
        current = _empty_totals()
    totals[key] = TokenUsageTotals(
        input_tokens=current.input_tokens + addition.input_tokens,
        output_tokens=current.output_tokens + addition.output_tokens,
        cache_read_input_tokens=(
            current.cache_read_input_tokens + addition.cache_read_input_tokens
        ),
        cache_creation_input_tokens=(
            current.cache_creation_input_tokens + addition.cache_creation_input_tokens
        ),
        total_tokens=current.total_tokens + addition.total_tokens,
        usage_count=current.usage_count + addition.usage_count,
    )


def aggregate_token_usage(events: list[object]) -> TokenUsageStats:
    """把事件明细聚合为按流程与按 agent 的 token 汇总。

    Args:
        events: 生命周期事件记录列表（鸭子类型：只读取 ``event_type`` 与
            ``detail_json`` 属性，避免与存储层类型绑死）。

    Returns:
        :class:`TokenUsageStats`；无可统计事件时两张表为空 dict。
    """
    by_flow: dict[str, TokenUsageTotals] = {}
    by_agent: dict[str, TokenUsageTotals] = {}
    for event in events:
        event_type = str(getattr(event, "event_type", ""))
        if event_type in _ATTEMPT_FLOW_EVENT_TYPES:
            detail = _parse_detail_json(getattr(event, "detail_json", "") or "")
            usage = _totals_from_payload(detail.get("token_usage"))
            if usage is None:
                continue
            flow = _IMPLEMENT_FLOW
            agent = str(detail.get("agent") or "") or "unknown"
        elif event_type == _OBSERVATION_EVENT_TYPE:
            detail = _parse_detail_json(getattr(event, "detail_json", "") or "")
            usage = _totals_from_payload(detail.get("token_usage"))
            if usage is None:
                continue
            flow = str(detail.get("flow") or "") or "unknown"
            agent = str(detail.get("agent") or "") or "unknown"
        else:
            continue
        _merge_totals(by_flow, flow, usage)
        _merge_totals(by_agent, agent, usage)
    return TokenUsageStats(by_flow=by_flow, by_agent=by_agent)


def _combine_totals(left: TokenUsageTotals, right: TokenUsageTotals) -> TokenUsageTotals:
    """把两份汇总逐字段相加（分组累计与跨 run 合并共用）。"""
    return TokenUsageTotals(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_input_tokens=(left.cache_read_input_tokens + right.cache_read_input_tokens),
        cache_creation_input_tokens=(
            left.cache_creation_input_tokens + right.cache_creation_input_tokens
        ),
        total_tokens=left.total_tokens + right.total_tokens,
        usage_count=left.usage_count + right.usage_count,
    )


def _sum_totals(values: Iterable[TokenUsageTotals]) -> TokenUsageTotals:
    """把若干份汇总合并为一份；空序列返回全零。"""
    result = TokenUsageTotals()
    for value in values:
        result = _combine_totals(result, value)
    return result


def _window_since(days: int, now: datetime | None) -> str:
    """把天数窗口换算成 ``since`` 时间戳（与 build_prd_lifecycle_stats 同一规则）。"""
    reference_now = now or datetime.now(timezone.utc)
    bounded_days = min(max(days, 1), 365)
    return (reference_now - timedelta(days=bounded_days)).isoformat(timespec="seconds")


def _list_window_runs(
    store: object, *, repo_id: str | None, days: int, now: datetime | None
) -> list[object]:
    """列出窗口内的 run 记录；读取失败降级为空列表（与 Stats 构建口径一致）。"""
    if store is None:
        return []
    try:
        return list(store.list_lifecycle_runs(repo_id=repo_id, since=_window_since(days, now)))
    except Exception as exc:  # noqa: BLE001 - one broken ledger must not break the CLI.
        _logger.warning("Failed to list lifecycle runs: %s", exc)
        return []


def _run_events(store: object, run_id: str) -> list[object]:
    """读取单个 run 的事件；失败按空列表容错（单 run 不拖垮整体）。"""
    try:
        return list(store.list_lifecycle_events(run_id=run_id))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Failed to list events for run %s: %s", run_id, exc)
        return []


def build_token_usage_by_prd(
    *,
    store: object,
    repo_id: str | None,
    days: int,
    now: datetime | None = None,
    issue_number: int | None = None,
    run_records: list[object] | None = None,
    events_by_run: dict[str, list[object]] | None = None,
) -> list[PrdTokenUsageEntry]:
    """按 PRD 汇总 token 用量，并保留旧模块导入路径。

    Args:
        store: 生命周期与调用账本读取对象。
        repo_id: 仓库过滤；``None`` 表示全部仓库。
        days: 时间窗口天数。
        now: 可选的统计基准时间。
        issue_number: 可选的 Issue 过滤条件。
        run_records: 可选的调用方预加载 run 记录。
        events_by_run: 可选的调用方预加载生命周期事件。

    Returns:
        按总 token 数降序排列的 PRD 汇总列表。
    """
    window_runs = (
        list(run_records)
        if run_records is not None
        else _list_window_runs(store, repo_id=repo_id, days=days, now=now)
    )
    if issue_number is not None:
        window_runs = [record for record in window_runs if record.issue_number == issue_number]

    from backend.core.use_cases.agent_runner_invocation_token_stats import (
        build_invocation_token_usage_summary,
    )

    summary = build_invocation_token_usage_summary(
        store=store,
        run_records=window_runs,
        events_by_run=events_by_run,
    )
    return list(summary.token_usage_by_prd)


def build_model_token_usage_stats(
    *,
    store: object,
    run_records: list[object],
) -> ModelTokenUsageStats:
    """按模型汇总 usage，并保留旧模块导入路径。

    Args:
        store: 生命周期与调用账本读取对象。
        run_records: 当前统计窗口内已加载的 lifecycle run。

    Returns:
        模型、Agent 与 PRD 维度的调用 usage 汇总。
    """
    from backend.core.use_cases.agent_runner_invocation_token_stats import (
        build_model_token_usage_stats as build_invocation_model_token_usage_stats,
    )

    return build_invocation_model_token_usage_stats(store=store, run_records=run_records)


def build_token_usage_stats_for_issue(
    *,
    store: object,
    repo_id: str | None,
    days: int,
    issue_number: int,
    now: datetime | None = None,
) -> TokenUsageStats:
    """按单个 Issue 汇总流程与 Agent usage，并保留旧模块导入路径。

    Args:
        store: 生命周期与调用账本读取对象。
        repo_id: 仓库过滤；``None`` 表示全部仓库。
        days: 时间窗口天数。
        issue_number: Issue 编号。
        now: 可选的统计基准时间。

    Returns:
        该 Issue 的流程与 Agent 用量汇总。
    """
    from backend.core.use_cases.agent_runner_invocation_token_stats import (
        build_token_usage_stats_for_issue as build_invocation_token_usage_stats_for_issue,
    )

    return build_invocation_token_usage_stats_for_issue(
        store=store,
        repo_id=repo_id,
        days=days,
        issue_number=issue_number,
        now=now,
    )


__all__ = [
    "aggregate_token_usage",
    "build_model_token_usage_stats",
    "build_token_usage_by_prd",
    "build_token_usage_stats_for_issue",
]
