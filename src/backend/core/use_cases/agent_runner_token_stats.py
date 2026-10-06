"""agent token 用量聚合（Stats 页 Token 汇总区的数据源）。

扫描窗口内的生命周期事件，把挂在事件 ``detail_json`` 里的 token 用量按
**流程**、**agent** 与 **PRD（Issue）** 三个维度汇总。两类来源：

- attempt 族事件（``attempt`` / ``retry`` / ``recovered``）的
  ``detail.token_usage`` —— 实现/修复主调用，flow 固定为 ``implement``；
- ``agent_token_usage`` 观测事件的 ``detail`` —— ``detail.flow`` 原样作为
  flow（``verify`` / ``supervise`` / ``fix`` / ``closeout``）。

缺 ``token_usage`` 的事件不计入；detail 损坏按空 dict 容错（与账本读取
口径一致）。总量 = 四项之和（实际处理量口径，含缓存读写）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from backend.core.shared.models.backlog import (
    PrdTokenUsageEntry,
    TokenUsageStats,
    TokenUsageTotals,
)

_logger = logging.getLogger(__name__)

#: attempt 族事件里携带 usage 的事件类型值；其 flow 统一归为实现主调用。
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
    """把窗口内的 token 用量按 PRD（Issue）维度汇总。

    同一 PRD 的多次 run 合并累计；口径与 :func:`aggregate_token_usage`
    单源（每条用量先经它同款提取规则校验，再并入 PRD 分组）。

    Args:
        store: 生命周期账本（鸭子类型：只需 ``list_lifecycle_runs`` /
            ``list_lifecycle_events`` 两个读方法）；调用方自带
            ``run_records`` 与 ``events_by_run`` 时不会被读取。
        repo_id: 仓库过滤；``None`` 表示全部仓库（仅自读账本时生效）。
        days: 时间窗口天数（内部钳制 1–365；仅自读账本时生效）。
        now: 统计基准时间；缺省取当前 UTC 时间（仅自读账本时生效）。
        issue_number: 只统计该 Issue 的 run；``None`` 表示不过滤。
        run_records: 调用方已持有的窗口内 run 记录；提供时不再
            ``list_lifecycle_runs``，保证与调用方同一读集、不做二次读库。
        events_by_run: run_id 到事件列表的映射；提供时单 run 事件直接取
            用映射（缺失视为无事件），不再 ``list_lifecycle_events``。

    Returns:
        按 ``total_tokens`` 降序的 :class:`PrdTokenUsageEntry` 列表；无数据
        返回空列表。
    """
    window_runs = (
        list(run_records)
        if run_records is not None
        else _list_window_runs(store, repo_id=repo_id, days=days, now=now)
    )
    if issue_number is not None:
        window_runs = [record for record in window_runs if record.issue_number == issue_number]

    grouped: dict[tuple, tuple[int, TokenUsageTotals]] = {}
    for run_record in window_runs:
        if events_by_run is not None:
            events = events_by_run.get(run_record.run_id, [])
        else:
            events = _run_events(store, run_record.run_id)
        # 复用 aggregate_token_usage 的单条提取与校验规则：对单 run 事件
        # 聚合后把各 flow 份合并成该 run 的总量，再并入 PRD 分组。
        run_stats = aggregate_token_usage(events)
        run_totals = _sum_totals(run_stats.by_flow.values())
        if run_totals.usage_count == 0:
            # 整个 run 无可用用量（agent 未上报 / 全部畸形）：按"缺失排除"
            # 口径跳过，不为它制造全零行。
            continue
        key = (run_record.repo_id, run_record.prd_path, run_record.issue_number)
        previous_count, previous_totals = grouped.get(key, (0, TokenUsageTotals()))
        grouped[key] = (previous_count + 1, _combine_totals(previous_totals, run_totals))

    entries = [
        PrdTokenUsageEntry(
            repo_id=group_repo_id,
            prd_path=group_prd_path,
            issue_number=group_issue_number,
            run_count=group_run_count,
            totals=group_totals,
        )
        for (
            group_repo_id,
            group_prd_path,
            group_issue_number,
        ), (group_run_count, group_totals) in grouped.items()
    ]
    entries.sort(
        key=lambda entry: (-entry.totals.total_tokens, entry.repo_id or "", entry.issue_number or 0)
    )
    return entries


def build_token_usage_stats_for_issue(
    *,
    store: object,
    repo_id: str | None,
    days: int,
    issue_number: int,
    now: datetime | None = None,
) -> TokenUsageStats:
    """构建单个 Issue（PRD）视角的按流程 / 按 agent 汇总。

    事件提取与分组规则完全复用 :func:`aggregate_token_usage`；本函数只
    负责把 run 范围收窄到该 Issue。
    """
    collected: list[object] = []
    for run_record in _list_window_runs(store, repo_id=repo_id, days=days, now=now):
        if run_record.issue_number != issue_number:
            continue
        collected.extend(_run_events(store, run_record.run_id))
    return aggregate_token_usage(collected)


__all__ = [
    "aggregate_token_usage",
    "build_token_usage_by_prd",
    "build_token_usage_stats_for_issue",
]
