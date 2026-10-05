"""Backlog domain models shared between core use cases and API routes.

All dataclasses are frozen and JSON-serializable via the standard route
``_serialize`` helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BacklogPrdState(str, Enum):
    """Unified execution state of a PRD on the backlog."""

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


class BacklogDependencyKind(str, Enum):
    """Kind of dependency edge shown on the backlog."""

    PRD = "prd"
    ISSUE = "issue"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class BacklogDependency:
    """A single dependency edge from one PRD to another target."""

    from_path: str
    to_path: str
    kind: BacklogDependencyKind
    detail: str | None = None


@dataclass(frozen=True)
class BacklogPrd:
    """A PRD node in the backlog graph."""

    prd_path: str
    title: str
    status: str  # pending / archived
    priority: str  # P0 / P1 / P2 / P3 / ""
    issue_url: str | None
    issue_number: int | None
    state: BacklogPrdState
    acceptance_total: int
    acceptance_checked: int
    delivery_dependencies: tuple[BacklogDependency, ...]
    updated_at: str  # ISO8601
    block_reason: str | None
    next_action: dict | None
    #: CI/CD 交付投影；仅有关联 PR 时有值，无 PR 或读取不到时保持 ``None``。
    #: 这是运行时派生视图，不落库（见 tasks/archive 的 Data Model 约束）。
    ci_delivery: CiDelivery | None = None


class BacklogCiRepairPolicy(str, Enum):
    """单个 PRD 的 CI 自动修复策略（三态）。

    ``INHERIT`` 是"未单独设置"，不是"关闭"：它随仓库全局值实时变化，因此清除
    覆盖必须写回 ``inherit`` 而不是把当时的全局布尔值复制成永久设置。
    """

    INHERIT = "inherit"
    ON = "on"
    OFF = "off"


class CiDeliveryStatus(str, Enum):
    """CI/CD 交付状态（只描述远端观察事实，不含修复决定）。

    原始 checks 状态与 Supervisor/策略结论分开呈现：``FAILING`` 不等于"会自动
    修复"，``NOT_RUN`` 也不得标成代码失败或 CI 通过。
    """

    NO_PR = "no_pr"  # 无关联 PR：本功能不适用，保持原有流程
    UNAVAILABLE = "unavailable"  # 无法 fresh 读取 PR context / GitHub 不可达
    PENDING = "pending"  # 有 PR，checks 尚未全部完成
    NOT_RUN = "not_run"  # 零 job / 无 checks 记录：未执行、未验证
    FAILING = "failing"  # 存在未通过的 check（原始观察，不推断根因）
    PASSING = "passing"  # 最新 head 的 checks 全绿


class CiRepairGateDecision(str, Enum):
    """服务端对 repair 动作的放行结论（只约束修复，不改写 Agent 的其他动作）。

    ``POLICY_OFF`` 与 ``BUDGET_EXHAUSTED`` 都必须零自动副作用，并把问题留在详情
    里；两者靠不同结论码区分，界面与 CLI 不再各自解释。
    """

    ALLOWED = "ci_repair_allowed"
    DUPLICATE = "ci_repair_duplicate"  # 同一 failure key 已获准过：重入不再修复
    POLICY_OFF = "ci_failed_manual"  # 生效策略关闭：失败交人工处理
    BUDGET_EXHAUSTED = "ci_repair_exhausted"  # 达到既有 max_repair_attempts
    UNRESOLVED = "ci_unresolved"  # 无法解析当前 PR/head/failure，拒绝修复


#: :class:`CiCheckProblem.kind` 的取值：问题只来自 ``checks_summary`` 或明确的
#: 汇总失败，禁止伪造不存在的 job 名。
CI_PROBLEM_KIND_CHECK_FAILURE = "check_failure"
CI_PROBLEM_KIND_CHECK_PENDING = "check_pending"
CI_PROBLEM_KIND_AGGREGATE = "aggregate_summary"
CI_PROBLEM_KIND_NOT_RUN = "not_run"
CI_PROBLEM_KIND_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CiCheckProblem:
    """一条 CI/CD 问题（原始事实 + 可跳转出处，不含推断根因）。"""

    name: str
    detail: str
    kind: str = CI_PROBLEM_KIND_CHECK_FAILURE
    url: str | None = None
    head_sha: str | None = None
    round_index: int | None = None


@dataclass(frozen=True)
class CiDelivery:
    """PRD 的 CI/CD 交付投影（从 PR context 与 ``iar:event`` markers 重建）。

    ``stored_policy`` / ``global_auto_repair`` / ``effective_auto_repair`` 三个值
    一起返回，effective 值由 core 计算，前端与 CLI 都不得自行推断。
    """

    prd_path: str
    status: CiDeliveryStatus
    pr_number: int | None = None
    pr_url: str = ""
    pr_branch: str = ""
    head_sha: str = ""
    checks_state: str | None = None
    problems: tuple[CiCheckProblem, ...] = ()
    repair_rounds: int = 0
    max_repair_attempts: int = 0
    repair_exhausted: bool = False
    stored_policy: BacklogCiRepairPolicy = BacklogCiRepairPolicy.INHERIT
    global_auto_repair: bool = False
    effective_auto_repair: bool = False
    policy_source: str = BacklogCiRepairPolicy.INHERIT.value
    last_decision: str | None = None
    failure_key: str | None = None
    supervisor_action: str | None = None
    supervisor_summary: str | None = None
    last_synced_at: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BacklogCiAutoRepairState:
    """仓库级 CI 自动修复设置快照（写后 fresh load 读回，不是请求体回显）。

    ``persisted_auto_repair`` 为 ``None`` 表示仓库本地配置里没有这个键，生效值来自
    全局配置默认值；它与 ``auto_repair_ci``（生效值）分开返回，页面才能如实区分
    “没设置过”和“显式设为 off”。
    """

    repo_id: str
    auto_repair_ci: bool
    max_repair_attempts: int
    config_source: str = ""
    persisted_auto_repair: bool | None = None


@dataclass(frozen=True)
class BacklogCiRepairResult:
    """一次性手动 repair 的执行结论。"""

    prd_path: str
    accepted: bool
    decision: str
    failure_key: str = ""
    head_sha: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BacklogSettings:
    """Per-repository backlog user settings persisted in console_store."""

    repo_id: str
    max_parallel: int
    default_view: str  # timeline / list
    updated_at: str = ""


@dataclass(frozen=True)
class BacklogSettingsEntry:
    """Core-side alias for backlog settings rows."""

    repo_id: str
    max_parallel: int
    default_view: str
    updated_at: str


@dataclass(frozen=True)
class BacklogQueueItem:
    """A single PRD entry in the backlog global scheduling queue."""

    id: int
    repo_id: str
    prd_path: str
    status: str  # queued / running / completed / failed
    trigger: str  # manual / global
    started_at: str | None
    finished_at: str | None
    error_detail: str | None


@dataclass(frozen=True)
class BacklogActionResult:
    """Result of a single PRD start action."""

    prd_path: str
    issue_number: int | None
    state: BacklogPrdState
    detail: str


@dataclass(frozen=True)
class BacklogGlobalStartResult:
    """Result of a global start action."""

    started: list[BacklogActionResult]
    queued: list[str]
    skipped: list[str]


@dataclass(frozen=True)
class BacklogAdvanceReport:
    """Result of one continuous-scheduling advance pass.

    Attributes:
        repo_id: Target repository ID.
        dry_run: ``True`` when the pass only computed the plan without writing.
        max_parallel: Concurrency ceiling read from the persisted backlog settings.
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
    started: list[BacklogActionResult]
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
    """单个 PRD 的生命周期详情（Backlog 详情“执行过程”标签的数据源）。

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
class TokenUsageTotals:
    """一个分组（流程或 agent）的 token 用量累计。

    ``total_tokens`` 为四项之和（实际处理量口径，含缓存命中与写入）；
    ``usage_count`` 是计入汇总的用量条数（缺 usage 的事件不计入）。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_tokens: int = 0
    usage_count: int = 0


@dataclass(frozen=True)
class TokenUsageStats:
    """仓库窗口内的 token 用量汇总（Stats 页 Token 汇总区的数据源）。

    ``by_flow`` 按 agent 调用流程分组（``implement`` / ``verify`` /
    ``supervise`` / ``fix`` / ``closeout``）；``by_agent`` 按 agent 名分组。
    """

    by_flow: dict[str, TokenUsageTotals]
    by_agent: dict[str, TokenUsageTotals]


@dataclass(frozen=True)
class PrdTokenUsageEntry:
    """单个 PRD（Issue）维度的 token 用量累计（CLI ``iar tokens`` 按 PRD 表的数据源）。

    同一 PRD 的多次 run 合并累计；``run_count`` 是参与累计的 run 条数，
    ``usage_count`` 是计入的用量条数（缺 usage 的事件不计入）。
    """

    repo_id: str | None
    prd_path: str | None
    issue_number: int | None
    run_count: int
    totals: TokenUsageTotals


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
    token_usage: TokenUsageStats
