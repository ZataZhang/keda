"""Agent Runner 的交付、恢复与依赖数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from dataclasses import field  # noqa: F401
from typing import TYPE_CHECKING  # noqa: F401

from backend.core.shared.interfaces.agent_output_protocol import PLAIN_PROTOCOL_ID  # noqa: F401
from backend.core.shared.models import product_identity  # noqa: F401
from backend.core.shared.models.agent_runner_execution import (  # noqa: F401
    DEFAULT_VALIDATION_EVIDENCE_DIR,
    evidence_dir_uses_task_subdirs,
    AgentCommitResult,
    TokenUsage,
    CommandResult,
    E2EVerificationArtifact,
    BrowserE2EVerificationCommand,
    VerificationCommand,
    describe_verification_command,
    FailureType,
    DeliveryGateFailureKind,
    DeliveryGateError,
    PhaseDuration,
    AttemptResult,
)
from backend.core.shared.models.agent_runner_config import (  # noqa: F401
    AgentModelPreset,
    AgentSessionConfig,
    AgentSpec,
    BUILTIN_AGENT_SPECS,
    DeliberationConfig,
    InteractiveDecisionConfig,
    LifecycleAgentsConfig,
    LifecyclePresetsConfig,
    ReplConfig,
    StallSupervisorConfig,
    IssueSummary,
    PullRequestSummary,
    IssueWithPulls,
    LabelConfig,
    GitConfig,
    WorktreeConfig,
    AgentFallbackCandidate,
    RunnerConfig,
    MemoryConfig,
    SafetyConfig,
    AutopilotConfig,
    BacklogConfig,
    PromptConfig,
    ValidationConfig,
    ReviewFinding,
    PrePrReviewConfig,
    FindingDetail,
    PostPrSupervisorConfig,
    PullRequestContext,
    ReviewEventMarker,
    SupervisorActionResult,
    GeneratedContentTargetConfig,
    GeneratedContentConfig,
    GeneratedIssueContent,
    GeneratedPrContent,
    RepositoryIdentity,
    DaemonConfig,
    AppConfig,
)
from backend.core.shared.models.machine_identity import MachineIdentity  # noqa: F401


@dataclass(frozen=True)
class RepositoryRunContext:
    """Resolved target repository with merged configuration."""

    repo_id: str
    display_name: str
    repo_path: Path
    config: AppConfig


@dataclass(frozen=True)
class IssueCommentRecord:
    """GitHub Issue 评论的可信读取投影。"""

    comment_id: int
    body: str
    updated_at: datetime
    author: str


class PublishFailureCategory(Enum):
    """Categorized publish failure reason for recovery context."""

    PUSH = "push"
    PR_LOOKUP = "pr_lookup"
    PR_CREATE = "pr_create"
    LABEL_UPDATE = "label_update"
    COMMENT_UPDATE = "comment_update"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PublishRecoveryRequest:
    """Request for publish recovery."""

    issue_number: int
    expected_branch: str | None = None


@dataclass(frozen=True)
class PublishRecoveryResult:
    """Result of successful publish recovery."""

    issue_number: int
    branch: str
    head_sha: str
    pr_url: str
    pr_reused: bool
    supervisor_action: str = ""


# ---------------------------------------------------------------------------
# Dependency gate models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryDependencyDeclaration:
    """Structured dependency declaration parsed from a PRD.

    Attributes:
        depends_on_issues: Specific Issue numbers that must be closed.
        depends_on_prds: PRD paths or filenames to resolve at Issue creation time.
        gate_type: One of ``"hard"``, ``"soft"``, ``"none"``.
        sequence: One of ``"via-main"`` (default) or ``"stack"``.
        notes: Free-form operator notes.
    """

    depends_on_issues: tuple[int, ...] = ()
    depends_on_prds: tuple[str, ...] = ()
    gate_type: str = "none"
    sequence: str = "via-main"
    notes: str = ""


@dataclass(frozen=True)
class DependencyDeclaration:
    """Materialised dependency declaration from an Issue body.

    Attributes:
        issue_numbers: Upstream Issue numbers this Issue depends on.
        sequence: Sequencing strategy carried from the PRD's ``Sequence`` field;
            ``"via-main"`` waits for the upstream Issue to close, ``"stack"``
            waits for the upstream branch to be ready and forks from it.
    """

    issue_numbers: tuple[int, ...] = ()
    sequence: str = "via-main"


@dataclass(frozen=True)
class DependencyBlocker:
    """A single unsatisfied dependency."""

    blocker_type: str
    target: str
    current_state: str


@dataclass(frozen=True)
class DependencyVerdict:
    """Result of evaluating an Issue's dependencies."""

    satisfied: bool
    blockers: tuple[DependencyBlocker, ...] = ()
    has_failed_or_blocked_upstream: bool = False
