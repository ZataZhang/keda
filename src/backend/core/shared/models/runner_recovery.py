"""Agent Runner 持久恢复与副作用账本的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RecoveryFailureCategory(StrEnum):
    """恢复策略识别的故障类别。"""

    TRANSIENT_NETWORK = "transient_network"
    RATE_LIMITED = "rate_limited"
    OUTCOME_UNKNOWN = "outcome_unknown"
    AUTH = "auth"
    SEMANTIC_REJECTED = "semantic_rejected"
    AGENT_INVALID_OUTPUT = "agent_invalid_output"
    AGENT_STALLED = "agent_stalled"
    FATAL_STATE = "fatal_state"


class RecoveryAction(StrEnum):
    """分类器允许执行的下一步动作。"""

    RETRY = "retry"
    WAIT = "wait"
    RECONCILE = "reconcile"
    HANDOFF = "handoff"
    CONTINUE_AGENT_RECOVERY = "continue_agent_recovery"
    STOP = "stop"


class OperationState(StrEnum):
    """外部副作用账本中的操作状态。"""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RecoveryDecision:
    """故障分类及决策结果。"""

    category: RecoveryFailureCategory
    action: RecoveryAction
    result_certainty: str
    reason: str


@dataclass(frozen=True)
class OperationIntent:
    """发送外部写入前必须严格持久化的操作身份。"""

    operation_key: str
    run_key: str
    repository_id: str
    issue_number: int
    operation_kind: str
    target: str
    payload_digest: str
    payload_revision: int = 1


@dataclass(frozen=True)
class OperationRecord:
    """外部操作的持久账本快照。"""

    intent: OperationIntent
    state: OperationState
    remote_reference: str | None
    last_error: str | None
    updated_at: datetime


@dataclass(frozen=True)
class RecoveryRunRecord:
    """跨进程恢复预算与调度状态。"""

    run_key: str
    repository_id: str
    issue_number: int
    first_failure_at: datetime
    budget_deadline: datetime
    resume_at: datetime | None
    recovery_attempts: int
    recovery_state: str
    failure_category: RecoveryFailureCategory
    error_summary: str
    report_path: str | None = None


@dataclass(frozen=True)
class RecoveryFailureRecord:
    """一次外部故障及恢复预算策略输入。"""

    run_key: str
    repository_id: str
    issue_number: int
    occurred_at: datetime
    retry_after: datetime | None
    max_attempts: int
    budget_seconds: int
    category: RecoveryFailureCategory
    error_summary: str
    report_path: str | None = None


@dataclass(frozen=True)
class RecoveryStageRecord:
    """可复用阶段结果及其成果前提。"""

    stage_key: str
    run_key: str
    stage: str
    artifact_tree: str
    base_sha: str
    validation_digest: str
    status: str
    completed_at: datetime
