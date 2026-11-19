"""从本次运行的调用账本识别失败阶段与可切换执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from backend.core.shared.interfaces.runner_console import InvocationEventRecord
from backend.core.use_cases.agent_invocation_tracing import (
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_TIMEOUT,
    build_invocation_timeline,
)

_FAILED_INVOCATION_OUTCOMES = frozenset({OUTCOME_ERROR, OUTCOME_FAILED, OUTCOME_TIMEOUT})

# 调用阶段到生命周期覆盖键的唯一映射。内部修复调用归到 fix；内容生成不属于
# issue retry 的 Agent Runner 阶段，因此保留为不可切换阶段。
_LIFECYCLE_STAGE_BY_PHASE: dict[str, str] = {
    "implementation": "implementation",
    "fix": "fix",
    "review": "review",
    "review_repair": "fix",
    "verification": "verifier",
    "verification_recovery": "fix",
    "rebase_recovery": "fix",
    "closeout": "closeout",
    "supervisor": "supervisor",
    "supervisor_repair": "fix",
}
RETRYABLE_LIFECYCLE_STAGES = frozenset(_LIFECYCLE_STAGE_BY_PHASE.values())


@dataclass(frozen=True)
class RuntimeFailureDiagnosis:
    """一次失败运行中最后一个失败执行器调用的摘要。"""

    stage: str
    executor: str
    failed_executors: tuple[str, ...]
    outcome: str
    reason: str


def diagnose_runtime_failure(
    *,
    started_at: str | None,
    finished_at: str | None,
    outcome: str | None,
    error_summary: str | None,
    run_agent: str | None,
    invocation_events: Sequence[InvocationEventRecord],
) -> RuntimeFailureDiagnosis | None:
    """识别运行终态是否由可切换的 Agent 调用失败导致。

    只看该次运行时间窗中的最后一次调用。一个 Agent 成功返回 RED、之后被
    确定性验证门禁拦截时，不会被误判成执行器故障。
    """
    run_start = _parse_timestamp(started_at)
    run_finish = _parse_timestamp(finished_at)
    if outcome != "failed" or run_start is None or run_finish is None or run_finish < run_start:
        return None

    invocation_ids: set[str] = set()
    for event in invocation_events:
        if event.event_type != "invocation_started":
            continue
        event_time = _parse_timestamp(event.occurred_at)
        if event_time is not None and run_start <= event_time <= run_finish:
            invocation_ids.add(event.invocation_id)
    current_run_events = [
        event
        for event in invocation_events
        if event.invocation_id in invocation_ids
        and event.event_type in {"invocation_started", "invocation_finished"}
    ]
    rows = build_invocation_timeline(current_run_events)
    if not rows:
        return None
    last_row = rows[-1]
    stage = _LIFECYCLE_STAGE_BY_PHASE.get(last_row.phase)
    if stage is None or last_row.outcome not in _FAILED_INVOCATION_OUTCOMES:
        return None

    failed_executors = tuple(
        dict.fromkeys(
            row.executor
            for row in rows
            if _LIFECYCLE_STAGE_BY_PHASE.get(row.phase) == stage
            and row.outcome in _FAILED_INVOCATION_OUTCOMES
            and row.executor
        )
    )
    cause = _describe_failure_cause(error_summary, last_row.outcome)
    reason = f"执行器 {last_row.executor} 调用失败：{cause}"
    if failed_executors:
        reason += f"；本阶段已尝试：{', '.join(failed_executors)}"
    if stage == "verifier" and run_agent:
        reason += f"；实现执行器：{run_agent}"
    return RuntimeFailureDiagnosis(
        stage=stage,
        executor=last_row.executor,
        failed_executors=failed_executors,
        outcome=last_row.outcome,
        reason=reason,
    )


def _describe_failure_cause(error_summary: str | None, invocation_outcome: str) -> str:
    """把运行摘要归并成人读原因，保留原始详情由任务详情页呈现。"""
    compact_summary = " ".join((error_summary or "").split())
    normalized_summary = compact_summary.lower()
    if any(
        marker in normalized_summary
        for marker in ("quota", "rate limit", "usage limit", "provider capacity")
    ):
        cause = "provider 额度耗尽或请求被限流"
    elif invocation_outcome == OUTCOME_TIMEOUT:
        cause = "调用超时"
    elif "not found" in normalized_summary or "not installed" in normalized_summary:
        cause = "执行器命令不可用"
    elif invocation_outcome == OUTCOME_ERROR:
        cause = "执行器启动或运行时出错"
    elif invocation_outcome == OUTCOME_FAILED:
        cause = "执行器返回失败状态"
    else:
        cause = "执行器调用未成功"
    if compact_summary:
        detail = compact_summary[:180]
        if len(compact_summary) > 180:
            detail += "…"
        return f"{cause}（{detail}）"
    return cause


def _parse_timestamp(value: str | None) -> datetime | None:
    """解析 ISO-8601 时间并归一为 UTC。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
