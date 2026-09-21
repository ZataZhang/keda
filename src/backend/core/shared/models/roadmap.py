"""Roadmap domain models shared between core use cases and API routes.

All dataclasses are frozen and JSON-serializable via the standard route
``_serialize`` helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoadmapPrdState(str, Enum):
    """Unified execution state of a PRD on the roadmap."""

    NOT_STARTED = "not_started"
    READY = "ready"
    RUNNING = "running"
    SUPERVISING = "supervising"
    REVIEW = "review"
    FAILED = "failed"
    BLOCKED = "blocked"
    MERGED = "merged"
    ARCHIVED = "archived"
    UNRESOLVED_DEPENDENCY = "unresolved_dependency"
    WAITING = "waiting"


class RoadmapDependencyKind(str, Enum):
    """Kind of dependency edge shown on the roadmap."""

    PRD = "prd"
    ISSUE = "issue"
    GROUP = "group"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RoadmapDependency:
    """A single dependency edge from one PRD to another target."""

    from_path: str
    to_path: str
    kind: RoadmapDependencyKind
    detail: str | None = None


@dataclass(frozen=True)
class RoadmapPrd:
    """A PRD node in the roadmap graph."""

    prd_path: str
    title: str
    status: str  # pending / archived
    priority: str  # P0 / P1 / P2 / P3 / ""
    issue_url: str | None
    issue_number: int | None
    state: RoadmapPrdState
    acceptance_total: int
    acceptance_checked: int
    delivery_dependencies: tuple[RoadmapDependency, ...]
    updated_at: str  # ISO8601
    block_reason: str | None
    next_action: dict | None


@dataclass(frozen=True)
class RoadmapSettings:
    """Per-repository roadmap user settings persisted in console_store."""

    repo_id: str
    max_parallel: int
    default_view: str  # timeline / list
    updated_at: str = ""


@dataclass(frozen=True)
class RoadmapSettingsEntry:
    """Core-side alias for roadmap settings rows."""

    repo_id: str
    max_parallel: int
    default_view: str
    updated_at: str


@dataclass(frozen=True)
class RoadmapQueueItem:
    """A single PRD entry in the roadmap global scheduling queue."""

    id: int
    repo_id: str
    prd_path: str
    status: str  # queued / running / completed / failed
    trigger: str  # manual / global
    started_at: str | None
    finished_at: str | None
    error_detail: str | None


@dataclass(frozen=True)
class RoadmapActionResult:
    """Result of a single PRD start action."""

    prd_path: str
    issue_number: int | None
    state: RoadmapPrdState
    detail: str


@dataclass(frozen=True)
class RoadmapGlobalStartResult:
    """Result of a global start action."""

    started: list[RoadmapActionResult]
    queued: list[str]
    skipped: list[str]


@dataclass(frozen=True)
class RoadmapAdvanceReport:
    """Result of one continuous-scheduling advance pass.

    Attributes:
        repo_id: Target repository ID.
        dry_run: ``True`` when the pass only computed the plan without writing.
        max_parallel: Concurrency ceiling read from the persisted roadmap settings.
        free_slots: Slots available for promotion in this pass.
        reconciled_completed: PRD paths whose queue entry was closed as ``completed``
            because the PRD is merged or archived.
        reconciled_failed: PRD paths whose queue entry was parked as ``failed``.
        started: Promotions (issue + ``agent/ready`` + queue entry ``running``);
            in dry-run mode these are the promotions that *would* happen.
        queued: PRD paths that were (or would be) enqueued as ``queued``.
        skipped: Promotion attempts rejected by the action layer, with reasons.
    """

    repo_id: str
    dry_run: bool
    max_parallel: int
    free_slots: int
    reconciled_completed: list[str]
    reconciled_failed: list[str]
    started: list[RoadmapActionResult]
    queued: list[str]
    skipped: list[str]


@dataclass(frozen=True)
class PrdLifecycleDurations:
    """单次 PRD 生命周期的耗时拆分（互斥口径，可由事件时间线复算）。

    ``end_to_end_seconds`` 即从首次进入执行队列到归档（进行中则到当前时刻）
    的端到端历时；``active`` / ``waiting`` / ``blocked`` 三者互斥且相加等于
    端到端耗时。``end_to_end_seconds`` 为 ``None`` 表示尚无任何事件。
    """

    end_to_end_seconds: float | None
    active_seconds: float
    waiting_seconds: float
    blocked_seconds: float


@dataclass(frozen=True)
class PrdLifecycleEventView:
    """PRD 生命周期时间线中的单条事件（API 视图）。"""

    event_type: str
    phase: str
    actor: str
    occurred_at: str
    detail: dict


@dataclass(frozen=True)
class PrdLifecycleDetail:
    """单个 PRD 的生命周期详情（Roadmap 详情“执行过程”标签的数据源）。

    ``has_data`` 为 ``False`` 表示该 PRD 还没有任何 lifecycle run/event；
    ``history_complete`` 为 ``False`` 表示观测账本自身有缺口（事件写入失败），
    页面必须显式告警而不是假装数据完整。
    """

    repo_id: str
    prd_path: str
    run_id: str | None
    issue_number: int | None
    trigger: str | None
    current_phase: str
    in_progress: bool
    outcome: str | None
    history_complete: bool
    started_at: str | None
    finished_at: str | None
    durations: PrdLifecycleDurations
    events: list[PrdLifecycleEventView]
    has_data: bool


@dataclass(frozen=True)
class PrdLifecycleStatsRow:
    """仓库级 PRD 生命周期统计中的单行 PRD 明细。"""

    run_id: str
    prd_path: str
    issue_number: int | None
    outcome: str | None
    current_phase: str
    in_progress: bool
    history_complete: bool
    started_at: str
    finished_at: str | None
    durations: PrdLifecycleDurations


@dataclass(frozen=True)
class PrdLifecycleStats:
    """仓库级 PRD 端到端统计（Stats 页“PRD 执行分析”的数据源）。

    ``unlinked_run_count`` 披露无法可靠关联 PRD 的旧 ``run_records`` 条数；
    这些记录不进入完成分位数，也不被伪装成生命周期事件。
    """

    repo_id: str | None
    window_days: int
    completed_runs: int
    average_end_to_end_seconds: float | None
    median_end_to_end_seconds: float | None
    p90_end_to_end_seconds: float | None
    average_blocked_seconds: float | None
    bottleneck_phase: str | None
    bottleneck_phase_seconds: float | None
    unlinked_run_count: int
    incomplete_run_count: int
    runs: list[PrdLifecycleStatsRow]
