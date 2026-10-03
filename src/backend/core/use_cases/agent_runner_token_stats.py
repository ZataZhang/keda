"""agent token 用量聚合（Stats 页 Token 汇总区的数据源）。

扫描窗口内的生命周期事件，把挂在事件 ``detail_json`` 里的 token 用量按
**流程**与 **agent** 两个维度汇总。两类来源：

- attempt 族事件（``attempt`` / ``retry`` / ``recovered``）的
  ``detail.token_usage`` —— 实现/修复主调用，flow 固定为 ``implement``；
- ``agent_token_usage`` 观测事件的 ``detail`` —— ``detail.flow`` 原样作为
  flow（``verify`` / ``supervise`` / ``fix`` / ``closeout``）。

缺 ``token_usage`` 的事件不计入；detail 损坏按空 dict 容错（与账本读取
口径一致）。总量 = 四项之和（实际处理量口径，含缓存读写）。
"""

from __future__ import annotations

import json

from backend.core.shared.models.roadmap import TokenUsageStats, TokenUsageTotals

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


__all__ = ["aggregate_token_usage"]
