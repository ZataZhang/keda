"""PRD 生命周期账本：事件定义、阶段推导、耗时分类与统计聚合。

本模块是 PRD 生命周期观测的唯一业务口径来源：

- **身份**：``repo_id + prd_path + run_id``。``run_id`` 确定性推导，使 roadmap
  启动侧与 runner 执行侧无需互相传参就能写到同一 run，重试与跨进程恢复也不会
  新建重复 run。
- **事件**：追加式闭集；``event_key`` 在同一 run 内唯一，重复写幂等。
- **耗时**：端到端为主指标，执行 / 等待 / 阻塞为互斥拆分，三者相加等于端到端。
  前端不得重新定义这些口径。
- **降级**：账本只是旁路观测。任何写入 / 读取失败都不允许阻断 runner 主流程；
  写入失败时把对应 run 标记为 ``history_complete=False``，让页面显式披露观测缺口。

时间戳统一为秒级 ISO8601 UTC 字符串，与 console 其余旁路账本保持一致。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, cast

from backend.core.shared.interfaces.runner_console import (
    IPrdLifecycleStore,
    PrdLifecycleEventRecord,
    PrdLifecycleRunRecord,
)
from backend.core.shared.models.roadmap import (
    PrdLifecycleDetail,
    PrdLifecycleDurations,
    PrdLifecycleEventView,
    PrdLifecycleStats,
    PrdLifecycleStatsRow,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_PHASES",
    "LifecycleEventType",
    "LifecyclePhase",
    "build_prd_lifecycle_detail",
    "build_prd_lifecycle_stats",
    "classify_durations",
    "derive_current_phase",
    "lifecycle_run_id",
    "record_lifecycle_event",
    "record_lifecycle_terminal",
    "resolve_lifecycle_store",
]


class LifecycleEventType(str, Enum):
    """PRD 生命周期事件闭集。

    新增事件种类时必须同时更新 :data:`_EVENT_PHASE`，否则推导阶段会缺省；
    事件种类刻意保持粗粒度，避免把 runner 内部实现细节泄漏为长期契约。
    """

    QUEUED = "queued"
    STARTED = "started"
    CLAIMED = "claimed"
    ATTEMPT = "attempt"
    RETRY = "retry"
    RECOVERED = "recovered"
    IMPLEMENTATION_COMPLETED = "implementation_completed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    REVIEW_STARTED = "review_started"
    REVIEW_PASSED = "review_passed"
    REVIEW_FAILED = "review_failed"
    MERGE_STARTED = "merge_started"
    MERGED = "merged"
    ARCHIVED = "archived"
    BLOCKED = "blocked"
    UNBLOCKED = "unblocked"
    FAILED = "failed"


class LifecyclePhase(str, Enum):
    """PRD 生命周期当前阶段（由事件聚合得出）。"""

    NONE = "none"
    QUEUED = "queued"
    EXECUTING = "executing"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    MERGING = "merging"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


#: 计入“有效执行”的阶段：只有 Agent 真正在跑的时间。
ACTIVE_PHASES = frozenset({LifecyclePhase.EXECUTING})

#: 计入“阻塞”的阶段。
_BLOCKED_PHASES = frozenset({LifecyclePhase.BLOCKED})

_EVENT_PHASE: dict[LifecycleEventType, LifecyclePhase] = {
    LifecycleEventType.QUEUED: LifecyclePhase.QUEUED,
    LifecycleEventType.STARTED: LifecyclePhase.EXECUTING,
    LifecycleEventType.CLAIMED: LifecyclePhase.EXECUTING,
    LifecycleEventType.ATTEMPT: LifecyclePhase.EXECUTING,
    LifecycleEventType.RETRY: LifecyclePhase.EXECUTING,
    LifecycleEventType.RECOVERED: LifecyclePhase.EXECUTING,
    LifecycleEventType.IMPLEMENTATION_COMPLETED: LifecyclePhase.REVIEWING,
    LifecycleEventType.VALIDATION_STARTED: LifecyclePhase.VALIDATING,
    LifecycleEventType.VALIDATION_PASSED: LifecyclePhase.VALIDATING,
    LifecycleEventType.VALIDATION_FAILED: LifecyclePhase.VALIDATING,
    LifecycleEventType.REVIEW_STARTED: LifecyclePhase.REVIEWING,
    LifecycleEventType.REVIEW_PASSED: LifecyclePhase.REVIEWING,
    LifecycleEventType.REVIEW_FAILED: LifecyclePhase.REVIEWING,
    LifecycleEventType.MERGE_STARTED: LifecyclePhase.MERGING,
    LifecycleEventType.MERGED: LifecyclePhase.COMPLETED,
    LifecycleEventType.ARCHIVED: LifecyclePhase.COMPLETED,
    LifecycleEventType.BLOCKED: LifecyclePhase.BLOCKED,
    LifecycleEventType.UNBLOCKED: LifecyclePhase.EXECUTING,
    LifecycleEventType.FAILED: LifecyclePhase.FAILED,
}

#: 终态事件：写入后 run 收口，之后只允许追加不改变阶段的观测事件。
_TERMINAL_EVENT_TYPES = frozenset(
    {
        LifecycleEventType.MERGED,
        LifecycleEventType.ARCHIVED,
        LifecycleEventType.FAILED,
        LifecycleEventType.BLOCKED,
    }
)


def now_iso() -> str:
    """返回秒级 ISO8601 UTC 时间戳（与 console 其余账本一致）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lifecycle_run_id(*, repo_id: str, issue_number: int | None, prd_path: str) -> str:
    """确定性推导 lifecycle run id。

    优先用 Issue 编号（PRD 改名后仍稳定）；尚无 Issue 时退化为 PRD 路径摘要，
    待 Issue 建立后再由 ``upsert_lifecycle_run`` 的 COALESCE 补齐。
    """
    if issue_number is not None:
        return f"{repo_id}#{issue_number}"
    digest = hashlib.sha1(prd_path.encode("utf-8")).hexdigest()[:12]
    return f"{repo_id}#prd-{digest}"


def resolve_lifecycle_store(store: object | None) -> IPrdLifecycleStore | None:
    """从任意旁路存储对象中识别 lifecycle 账本能力（鸭子类型，失败返回 ``None``）。

    roadmap 与 runner 两侧传入的分别是 ``IRoadmapStore`` / ``IRunHistoryStore``，
    但都由同一个 :class:`~backend.infrastructure.persistence.console_store.SqliteConsoleStore`
    实现。用能力探测而非新增端口参数，避免把生命周期观测泄漏进既有调用链。
    """
    if store is None:
        return None
    if hasattr(store, "upsert_lifecycle_run") and hasattr(store, "append_lifecycle_event"):
        return cast(IPrdLifecycleStore, store)
    return None


def _json_detail(detail: dict[str, Any] | None) -> str:
    """把结构化摘要序列化为 JSON；只存摘要，不存敏感原文。"""
    return json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)


def _parse_detail(detail_json: str) -> dict:
    """解析事件 detail；历史行损坏时降级为空字典而非让整条时间线失败。"""
    try:
        parsed = json.loads(detail_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_store_call(store: IPrdLifecycleStore, run_id: str, action: str, call: Any) -> Any:
    """执行一次账本写入；失败时尽力标记 run 不完整，绝不向上抛出。"""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - observation must not break the main flow.
        _logger.warning("PRD lifecycle %s failed for run %s: %s", action, run_id, exc)
        try:
            store.mark_lifecycle_run_incomplete(run_id)
        except Exception as mark_exc:  # noqa: BLE001 - incomplete marker is best effort too.
            _logger.warning("Failed to mark lifecycle run %s incomplete: %s", run_id, mark_exc)
        return None


def record_lifecycle_event(
    *,
    store: object | None,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    trigger: str,
    event_type: LifecycleEventType,
    actor: str,
    occurred_at: str | None = None,
    event_key: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """旁路追加一条生命周期事件；任何失败都不阻断调用方主流程。

    Args:
        store: 任意实现了 lifecycle 账本能力的存储；``None`` 或不支持时静默跳过。
        repo_id: 目标仓库标识。
        prd_path: 规范化 PRD 相对路径。
        issue_number: 关联 Issue 编号；未知时为 ``None``。
        trigger: run 首次写入时记录的触发来源（cli_run / console_daemon / roadmap ...）。
        event_type: 事件种类（闭集）。
        actor: 事件来源标识（如 ``runner`` / ``roadmap`` / ``validation_gate``）。
        occurred_at: 事件发生时间；缺省取当前 UTC 时间。
        event_key: run 内唯一键；缺省用 ``event_type@occurred_at``，可抵抗同秒重复写。
        detail: 结构化非敏感摘要。
    """
    lifecycle_store = resolve_lifecycle_store(store)
    if lifecycle_store is None:
        return
    timestamp = occurred_at or now_iso()
    run_id = lifecycle_run_id(repo_id=repo_id, issue_number=issue_number, prd_path=prd_path)
    phase = _EVENT_PHASE.get(event_type, LifecyclePhase.NONE)
    run_record = PrdLifecycleRunRecord(
        run_id=run_id,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger=trigger,
        started_at=timestamp,
        finished_at=None,
        outcome=None,
        history_complete=True,
    )
    _safe_store_call(
        lifecycle_store,
        run_id,
        "run upsert",
        lambda: lifecycle_store.upsert_lifecycle_run(run_record),
    )
    event_record = PrdLifecycleEventRecord(
        run_id=run_id,
        event_key=event_key or f"{event_type.value}@{timestamp}",
        event_type=event_type.value,
        phase=phase.value,
        actor=actor,
        occurred_at=timestamp,
        detail_json=_json_detail(detail),
    )
    _safe_store_call(
        lifecycle_store,
        run_id,
        f"event append ({event_type.value})",
        lambda: lifecycle_store.append_lifecycle_event(event_record),
    )
    # 非终态事件意味着该 run 正在继续（重试 / 解除阻塞后再次执行）：必须重开已
    # 收口的 run，否则 finished_at 会早于后续事件时间，端到端与三类拆分互相矛盾。
    if event_type not in _TERMINAL_EVENT_TYPES:
        _safe_store_call(
            lifecycle_store,
            run_id,
            "run reopen",
            lambda: lifecycle_store.reopen_lifecycle_run(run_id),
        )


def record_lifecycle_terminal(
    *,
    store: object | None,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    trigger: str,
    event_type: LifecycleEventType,
    outcome: str,
    actor: str,
    occurred_at: str | None = None,
    event_key: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """追加终态事件并收口 run（写 ``finished_at`` 与 ``outcome``）。

    终态一次写定；重复调用同一 ``event_key`` 幂等命中，不产生重复事件。此后若
    同一 stable run id 被再次执行（重试 / 解除阻塞），后续的**非终态**事件会
    通过 ``reopen_lifecycle_run`` 重开该 run，``finished_at`` / ``outcome`` 回到
    “进行中”；更早的 ``failed`` / ``blocked`` 事件仍完整保留在时间线上，因此
    “后续成功不覆盖失败历史” 说的是事件历史，而不是 run 的当前状态字段。
    """
    timestamp = occurred_at or now_iso()
    record_lifecycle_event(
        store=store,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger=trigger,
        event_type=event_type,
        actor=actor,
        occurred_at=timestamp,
        event_key=event_key or f"{event_type.value}@{timestamp}",
        detail=detail,
    )
    lifecycle_store = resolve_lifecycle_store(store)
    if lifecycle_store is None:
        return
    run_id = lifecycle_run_id(repo_id=repo_id, issue_number=issue_number, prd_path=prd_path)
    _safe_store_call(
        lifecycle_store,
        run_id,
        "run finish",
        lambda: lifecycle_store.finish_lifecycle_run(
            run_id=run_id, outcome=outcome, finished_at=timestamp
        ),
    )


def is_terminal_event(event_type: str) -> bool:
    """判断事件种类是否为终态（用于统计是否已收口）。"""
    try:
        return LifecycleEventType(event_type) in _TERMINAL_EVENT_TYPES
    except ValueError:
        return False


@dataclass(frozen=True)
class _DurationBreakdown:
    """内部耗时分解结果（含分阶段累计，供瓶颈定位）。"""

    end_to_end_seconds: float | None
    active_seconds: float
    waiting_seconds: float
    blocked_seconds: float
    phase_totals: dict[str, float]

    def to_dto(self) -> PrdLifecycleDurations:
        return PrdLifecycleDurations(
            end_to_end_seconds=self.end_to_end_seconds,
            active_seconds=self.active_seconds,
            waiting_seconds=self.waiting_seconds,
            blocked_seconds=self.blocked_seconds,
        )


def _parse_timestamp(value: str | None) -> datetime | None:
    """解析 ISO8601 时间戳；非法值返回 ``None`` 而不是抛出。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_sort_key(event: PrdLifecycleEventRecord) -> tuple[datetime, str]:
    """事件的排序键：先按解析后的时间，再按原始字符串，保证稳定且与真实时序一致。

    直接按 ISO8601 字符串排序只在该字符串始终同偏移时才等于时序；一旦混入
    非 UTC 偏移，字典序会与真实先后不一致。解析失败的行回落到最早时间，交由
    原始字符串稳定次序。
    """
    parsed = _parse_timestamp(event.occurred_at)
    return (parsed or datetime.min.replace(tzinfo=timezone.utc), event.occurred_at)


def _ordered_events(
    events: list[PrdLifecycleEventRecord],
) -> list[tuple[PrdLifecycleEventRecord, datetime]]:
    """按真实时序返回 ``(事件, 解析时间)``；时间不可解析的事件被丢弃。"""
    parsed_events: list[tuple[PrdLifecycleEventRecord, datetime]] = []
    for event in sorted(events, key=_event_sort_key):
        timestamp = _parse_timestamp(event.occurred_at)
        if timestamp is not None:
            parsed_events.append((event, timestamp))
    return parsed_events


def classify_durations(
    events: list[PrdLifecycleEventRecord],
    *,
    finished_at: str | None,
    now: datetime | None = None,
) -> _DurationBreakdown:
    """按事件区间把端到端耗时拆分为执行 / 等待 / 阻塞（互斥可复算）。

    每个区间归属“前一个事件”的阶段：从事件 i 到事件 i+1（最后一个事件到
    ``finished_at`` 或当前时刻）记为事件 i 所属阶段的时长。这样页面上的数字
    可以完全由时间线重新算出，不需要额外口径。
    """
    reference_now = now or datetime.now(timezone.utc)
    parsed_events = _ordered_events(events)
    if not parsed_events:
        return _DurationBreakdown(
            end_to_end_seconds=None,
            active_seconds=0.0,
            waiting_seconds=0.0,
            blocked_seconds=0.0,
            phase_totals={},
        )

    first_timestamp = parsed_events[0][1]
    last_timestamp = parsed_events[-1][1]
    # 结束边界取 ``finished_at``、最后一个事件时间与当前时刻中的最晚者：
    # - 未收口（finished_at 为 None）→ 当前时刻，进行中计算到 now；
    # - 已收口 → finished_at，但若存在晚于它的观测事件（历史脏数据 / 重开失败），
    #   至少扩到最后一个事件，保证各区间非负且三段之和恒等于端到端。
    candidate_boundary = _parse_timestamp(finished_at) or reference_now
    end_boundary = max(candidate_boundary, last_timestamp)
    active = waiting = blocked = 0.0
    phase_totals: dict[str, float] = {}
    for index, (event, start) in enumerate(parsed_events):
        next_timestamp = (
            parsed_events[index + 1][1] if index + 1 < len(parsed_events) else end_boundary
        )
        segment_seconds = max(0.0, (next_timestamp - start).total_seconds())
        try:
            phase = LifecyclePhase(event.phase)
        except ValueError:
            phase = LifecyclePhase.NONE
        phase_totals[phase.value] = phase_totals.get(phase.value, 0.0) + segment_seconds
        if phase in ACTIVE_PHASES:
            active += segment_seconds
        elif phase in _BLOCKED_PHASES:
            blocked += segment_seconds
        else:
            waiting += segment_seconds

    end_to_end = max(0.0, (end_boundary - first_timestamp).total_seconds())
    return _DurationBreakdown(
        end_to_end_seconds=end_to_end,
        active_seconds=active,
        waiting_seconds=waiting,
        blocked_seconds=blocked,
        phase_totals=phase_totals,
    )


def derive_current_phase(
    run_record: PrdLifecycleRunRecord,
    events: list[PrdLifecycleEventRecord],
) -> LifecyclePhase:
    """由事件聚合出当前阶段；无事件时按 run 终态降级。"""
    if events:
        last_event = max(events, key=_event_sort_key)
        try:
            return LifecyclePhase(last_event.phase)
        except ValueError:
            return LifecyclePhase.NONE
    if run_record.outcome == "completed":
        return LifecyclePhase.COMPLETED
    if run_record.outcome == "failed":
        return LifecyclePhase.FAILED
    if run_record.outcome == "blocked":
        return LifecyclePhase.BLOCKED
    return LifecyclePhase.NONE


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    """线性插值分位数；空列表返回 ``None``。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[int(position)]
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + (upper_value - lower_value) * (position - lower_index)


def _round_seconds(value: float | None) -> float | None:
    """耗时统一保留一位小数，避免浮点噪声进入页面与证据。"""
    return None if value is None else round(value, 1)


def _durations_dto(breakdown: _DurationBreakdown) -> PrdLifecycleDurations:
    """把内部耗时分解转换为 API DTO（统一舍入，避免各调用点重复口径）。"""
    return PrdLifecycleDurations(
        end_to_end_seconds=_round_seconds(breakdown.end_to_end_seconds),
        active_seconds=round(breakdown.active_seconds, 1),
        waiting_seconds=round(breakdown.waiting_seconds, 1),
        blocked_seconds=round(breakdown.blocked_seconds, 1),
    )


def _empty_lifecycle_detail(repo_id: str, prd_path: str) -> PrdLifecycleDetail:
    """构造“尚无账本数据”的空态详情（存储不可用与无 run 共用同一形状）。"""
    return PrdLifecycleDetail(
        repo_id=repo_id,
        prd_path=prd_path,
        run_id=None,
        issue_number=None,
        trigger=None,
        current_phase=LifecyclePhase.NONE.value,
        in_progress=False,
        outcome=None,
        history_complete=False,
        started_at=None,
        finished_at=None,
        durations=_durations_dto(
            _DurationBreakdown(
                end_to_end_seconds=None,
                active_seconds=0.0,
                waiting_seconds=0.0,
                blocked_seconds=0.0,
                phase_totals={},
            )
        ),
        events=[],
        has_data=False,
    )


def build_prd_lifecycle_detail(
    *,
    store: object | None,
    repo_id: str,
    prd_path: str,
    now: datetime | None = None,
) -> PrdLifecycleDetail:
    """构建单个 PRD 的生命周期详情视图；账本不可用或无数据时返回空态。"""
    lifecycle_store = resolve_lifecycle_store(store)
    if lifecycle_store is None:
        return _empty_lifecycle_detail(repo_id, prd_path)

    try:
        run_record = lifecycle_store.get_latest_lifecycle_run(repo_id=repo_id, prd_path=prd_path)
    except Exception as exc:  # noqa: BLE001 - read failure degrades to empty state.
        _logger.warning("Failed to read lifecycle run for %s/%s: %s", repo_id, prd_path, exc)
        run_record = None
    if run_record is None:
        return _empty_lifecycle_detail(repo_id, prd_path)

    try:
        stored_events = lifecycle_store.list_lifecycle_events(run_id=run_record.run_id)
    except Exception as exc:  # noqa: BLE001 - read failure degrades to incomplete.
        _logger.warning("Failed to read lifecycle events for %s: %s", run_record.run_id, exc)
        stored_events = []

    breakdown = classify_durations(stored_events, finished_at=run_record.finished_at, now=now)
    current_phase = derive_current_phase(run_record, stored_events)
    return PrdLifecycleDetail(
        repo_id=repo_id,
        prd_path=prd_path,
        run_id=run_record.run_id,
        issue_number=run_record.issue_number,
        trigger=run_record.trigger,
        current_phase=current_phase.value,
        in_progress=run_record.finished_at is None,
        outcome=run_record.outcome,
        history_complete=run_record.history_complete,
        started_at=run_record.started_at,
        finished_at=run_record.finished_at,
        durations=_durations_dto(breakdown),
        events=[
            PrdLifecycleEventView(
                event_type=event.event_type,
                phase=event.phase,
                actor=event.actor,
                occurred_at=event.occurred_at,
                detail=_parse_detail(event.detail_json),
            )
            for event in stored_events
        ],
        has_data=True,
    )


def build_prd_lifecycle_stats(
    *,
    store: object | None,
    repo_id: str | None,
    days: int,
    now: datetime | None = None,
) -> PrdLifecycleStats:
    """构建仓库级 PRD 端到端统计（完成分位数 + 阶段瓶颈 + 每 PRD 明细）。"""
    reference_now = now or datetime.now(timezone.utc)
    bounded_days = min(max(days, 1), 365)
    since = (reference_now - timedelta(days=bounded_days)).isoformat(timespec="seconds")
    lifecycle_store = resolve_lifecycle_store(store)
    if lifecycle_store is None:
        return PrdLifecycleStats(
            repo_id=repo_id,
            window_days=bounded_days,
            completed_runs=0,
            average_end_to_end_seconds=None,
            median_end_to_end_seconds=None,
            p90_end_to_end_seconds=None,
            average_blocked_seconds=None,
            bottleneck_phase=None,
            bottleneck_phase_seconds=None,
            unlinked_run_count=0,
            incomplete_run_count=0,
            runs=[],
        )

    try:
        run_records = lifecycle_store.list_lifecycle_runs(repo_id=repo_id, since=since)
    except Exception as exc:  # noqa: BLE001 - read failure degrades to empty stats.
        _logger.warning("Failed to list lifecycle runs: %s", exc)
        run_records = []

    rows: list[PrdLifecycleStatsRow] = []
    completed_end_to_end: list[float] = []
    completed_blocked: list[float] = []
    phase_totals: dict[str, float] = {}
    incomplete_count = 0
    for run_record in run_records:
        try:
            stored_events = lifecycle_store.list_lifecycle_events(run_id=run_record.run_id)
        except Exception:  # noqa: BLE001 - one run must not break the whole stats page.
            stored_events = []
        breakdown = classify_durations(
            stored_events, finished_at=run_record.finished_at, now=reference_now
        )
        if not run_record.history_complete:
            incomplete_count += 1
        is_completed = run_record.outcome == "completed" and run_record.finished_at is not None
        if is_completed and breakdown.end_to_end_seconds is not None:
            completed_end_to_end.append(breakdown.end_to_end_seconds)
            completed_blocked.append(breakdown.blocked_seconds)
            for phase_name, phase_seconds in breakdown.phase_totals.items():
                phase_totals[phase_name] = phase_totals.get(phase_name, 0.0) + phase_seconds
        rows.append(
            PrdLifecycleStatsRow(
                run_id=run_record.run_id,
                prd_path=run_record.prd_path,
                issue_number=run_record.issue_number,
                outcome=run_record.outcome,
                current_phase=derive_current_phase(run_record, stored_events).value,
                in_progress=run_record.finished_at is None,
                history_complete=run_record.history_complete,
                started_at=run_record.started_at,
                finished_at=run_record.finished_at,
                durations=_durations_dto(breakdown),
            )
        )

    try:
        unlinked_count = lifecycle_store.count_legacy_runs_without_lifecycle(
            repo_id=repo_id, since=since
        )
    except Exception as exc:  # noqa: BLE001 - degradation disclosure is best effort.
        _logger.warning("Failed to count legacy run records: %s", exc)
        unlinked_count = 0

    sorted_end_to_end = sorted(completed_end_to_end)
    bottleneck_phase: str | None = None
    bottleneck_seconds: float | None = None
    # 阶段瓶颈只在“等待类”阶段中挑选：执行与阻塞时长已在顶部指标单独呈现，
    # 混进来会让“瓶颈 = executing”这种无信息结论盖过真正的等待环节。
    waiting_phase_totals = {
        phase_name: phase_seconds
        for phase_name, phase_seconds in phase_totals.items()
        if phase_name
        in {
            LifecyclePhase.QUEUED.value,
            LifecyclePhase.VALIDATING.value,
            LifecyclePhase.REVIEWING.value,
            LifecyclePhase.MERGING.value,
        }
    }
    if waiting_phase_totals:
        bottleneck_phase = max(waiting_phase_totals, key=lambda name: waiting_phase_totals[name])
        bottleneck_seconds = waiting_phase_totals[bottleneck_phase]

    return PrdLifecycleStats(
        repo_id=repo_id,
        window_days=bounded_days,
        completed_runs=len(completed_end_to_end),
        average_end_to_end_seconds=(
            _round_seconds(sum(completed_end_to_end) / len(completed_end_to_end))
            if completed_end_to_end
            else None
        ),
        median_end_to_end_seconds=_round_seconds(_percentile(sorted_end_to_end, 0.5)),
        p90_end_to_end_seconds=_round_seconds(_percentile(sorted_end_to_end, 0.9)),
        average_blocked_seconds=(
            _round_seconds(sum(completed_blocked) / len(completed_blocked))
            if completed_blocked
            else None
        ),
        bottleneck_phase=bottleneck_phase,
        bottleneck_phase_seconds=_round_seconds(bottleneck_seconds),
        unlinked_run_count=unlinked_count,
        incomplete_run_count=incomplete_count,
        runs=rows,
    )
