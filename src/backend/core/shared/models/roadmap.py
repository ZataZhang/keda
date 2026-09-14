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
