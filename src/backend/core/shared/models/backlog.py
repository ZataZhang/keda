"""Backlog domain models shared between core use cases and API routes.

All dataclasses are frozen and JSON-serializable via the standard route
``_serialize`` helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
        ceiling: The single resolved concurrency ceiling used by this pass
            (``min(policy, runner capacity)``, policy ``None`` inherits capacity).
        ceiling_source: Where the ceiling came from: ``"inherited"`` /
            ``"policy"`` / ``"capped_by_capacity"``.
        free_slots: Slots available for promotion in this pass.
        running_count: Live repository-wide ``agent/running`` count, or ``None``
            if the count query failed and promotions were skipped.
        running_count_is_lower_bound: ``True`` when the live count exceeded the
            ceiling and the value is therefore a lower bound.
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
    ceiling: int
    ceiling_source: str
    free_slots: int
    running_count: int | None
    running_count_is_lower_bound: bool
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
    """PRD 生命周期时间线中的单条事件（API 视图）。

    ``status`` 是该事件自身的语义状态（供前端状态徽章按 event_type 语义渲染），
    比粗粒度 ``phase`` 更细，让「开始执行」「已被领取」「重试」等在时间线上可辨。
    """

    event_type: str
    phase: str
    status: str
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
    invocations: "PrdInvocationDetail | None" = None


@dataclass(frozen=True)
class PrdInvocationRow:
    """一次真实进程调用在「执行过程」视图里的展示行。

    字段全部来自调用账本已落库的事实（身份取自事件列，其余取自起止事件的
    ``detail_json``），本视图不新增采集、不推测回填：

    - ``outcome`` 保持账本闭集语义（``ok`` / ``failed`` / ``timeout`` / ``error``
      / ``unclosed`` / ``incomplete``）；未闭合的调用 ``finished_at`` 与
      ``duration_seconds`` 恒为 ``None``，前端据此显示"未闭合"而不是估算时长。
    - 模型三态由 ``requested_model`` / ``reported_model`` / ``model_source``
      共同表达：未下发与执行器未上报是两件事，不拿配置值冒充实际模型。
    - ``retry_of`` / ``retry_reason`` 保留重试与换执行器回退的关联，失败调用
      不会被合并进后续成功调用。

    Attributes:
        invocation_id: 本次进程调用的唯一 id。
        run_id: 所属执行会话标识（与调用账本的 ``run_id`` 同源）。
        attempt_number: recovery 轮次；非 attempt 主体调用为 ``None``。
        phase: 调用阶段（闭集）。
        role: 由阶段推导的角色。
        executor: 实际执行器注册名（回退换人后即为换到的那个）。
        outcome: 调用终态（闭集）。
        failure_category: 失败类别；成功调用为 ``None``。
        started_at: 开始事件时间（ISO8601）。
        finished_at: 结束事件时间；未闭合为 ``None``。
        duration_seconds: 墙钟耗时；未闭合为 ``None``。
        requested_model: 本次实际下发的模型 id；未下发为 ``None``。
        reported_model: 执行器自报的模型 id；未上报为 ``None``。
        model_source: 实际模型信息来源；无自报时为 ``unknown``。
        model_config_snapshot: 启动前读取的默认模型配置，仅作来源明确的估价线索。
        token_usage: 执行器自报用量；无数据时为 ``None``（≠ 0）。
        retry_of: 本次调用替代的上一次 invocation id；非重试为 ``None``。
        retry_reason: 与 :attr:`retry_of` 配套的闭集重试原因。
        exit_code: 进程退出码；未闭合或未知为 ``None``。
        log_locator: per-Issue 日志的相对定位，供 grep 回原文。
    """

    invocation_id: str
    run_id: str
    attempt_number: int | None
    phase: str
    role: str
    executor: str
    outcome: str
    failure_category: str | None
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    requested_model: str | None
    reported_model: str | None
    model_source: str | None
    token_usage: dict | None
    retry_of: str | None
    retry_reason: str | None
    exit_code: int | None
    log_locator: str | None
    model_config_snapshot: dict[str, object] | None = None


@dataclass(frozen=True)
class PrdInvocationDetail:
    """某个 Issue 的调用明细展示集（挂在生命周期详情的 ``invocations`` 上）。

    三个字段就是页面必须如实披露的全部状态，缺任何一项都会让视图"看起来完整"：

    - ``available=False``：调用账本不可读（存储不具备 Issue 级调用读端，或读取异常）。
      前端显示「调用明细不可用」。
    - 整个区为 ``None``（生命周期详情的 ``invocations`` 缺位）：该 PRD 没有关联 Issue，
      调用事实无从归属——与「读不到」是两件事，页面文案不同。
    - ``available=True`` 且 ``rows=[]``：账本可读但没有该 Issue 的调用记录
      （早于账本上线的旧库、或该 PRD 尚未跑过 Agent）。
    - ``truncated=True``：读取条数触达上限，历史只是**最近一段**而非全部。

    ``rows`` 按事件流出场顺序排列（跨执行会话），不做时间窗口推断。
    """

    available: bool
    truncated: bool
    rows: list[PrdInvocationRow]


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
    ``supervise`` / ``fix`` / ``closeout`` / ``content_generation`` / ``unknown``）；
    ``by_agent`` 按 agent 名分组。
    """

    by_flow: dict[str, TokenUsageTotals]
    by_agent: dict[str, TokenUsageTotals]


@dataclass(frozen=True)
class PrdModelTokenUsageEntry:
    """一个 PRD 在单个模型上的累计 token 用量。

    Attributes:
        repo_id: 仓库标识符。
        prd_path: PRD 相对路径；无法关联时为空。
        issue_number: GitHub Issue 编号；无法关联时为空。
        model_id: 调用账本记录的模型标识符。
        totals: 该 PRD 在该模型上的 token 累计。
    """

    repo_id: str
    prd_path: str | None
    issue_number: int | None
    model_id: str
    totals: TokenUsageTotals


@dataclass(frozen=True)
class ModelTokenUsageStats:
    """调用账本中可归属到模型的 token 用量汇总。

    Attributes:
        by_model: 模型标识符到 token 汇总的映射。
        by_agent: Agent 名称到模型用量的嵌套映射。
        by_prd: PRD（Issue）与模型维度的用量条目。
        truncated: 是否有 Issue 的调用读取触达历史条数上限。
        missing_usage_by_agent: 各 Agent 已结束但没有可用 usage 的调用数，不估算 token。
        zero_usage_by_model: 各模型明确上报全零 usage 的调用数，不代表免费。
        configuration_fallback_counts: 各模型按调用时默认配置归属的用量条数。
        unresolved_model_alias_counts: 默认配置只提供别名的用量条数，仍归为 unknown。
    """

    by_model: dict[str, TokenUsageTotals] = field(default_factory=dict)
    by_agent: dict[str, dict[str, TokenUsageTotals]] = field(default_factory=dict)
    by_prd: tuple[PrdModelTokenUsageEntry, ...] = ()
    truncated: bool = False
    missing_usage_by_agent: dict[str, int] = field(default_factory=dict)
    zero_usage_by_model: dict[str, int] = field(default_factory=dict)
    configuration_fallback_counts: dict[str, int] = field(default_factory=dict)
    unresolved_model_alias_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PrdTokenUsageEntry:
    """单个 PRD（Issue）维度的 token 用量累计（CLI ``kc tokens`` 按 PRD 表的数据源）。

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
    ``token_usage_by_prd`` 是同一窗口 / 同一批 run 上的 PRD（Issue）维度
    token 汇总（按 ``total_tokens`` 降序，口径与 CLI ``kc tokens`` 的
    「按 PRD」表同源）；账本不可用时降级为空列表。
    ``token_usage_by_model`` 来自调用观测账本，以实际报告模型优先分组；不
    能关联模型的旧记录不会被估算或回填。
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
    token_usage_by_prd: list[PrdTokenUsageEntry]
    token_usage_by_model: ModelTokenUsageStats = field(default_factory=ModelTokenUsageStats)


class CiRepairPolicy(str, Enum):
    """单 PRD CI/CD 自动修复策略的三态覆盖值。"""

    INHERIT = "inherit"
    ON = "on"
    OFF = "off"


class CiDeliveryStatus(str, Enum):
    """Backlog CI/CD 交付尾段的聚合状态。"""

    #: 尚无 PR 或无法获取 PR context（无 PR 的 PRD 保持原有流程）。
    NO_PR = "no_pr"
    #: GitHub checks 处于 pending / 尚未完成。
    PENDING = "pending"
    #: checks 全部通过。
    SUCCESS = "success"
    #: checks 存在失败（原始观察，不代表会触发修复）。
    FAILURE = "failure"
    #: GitHub 不可达 / 状态未知，按未验证呈现而不是伪装成通过或失败。
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CiCheckProblem:
    """一条来自 GitHub ``checks_summary`` 的原始失败观察。"""

    name: str
    summary: str
    url: str | None = None
    round_number: int | None = None


@dataclass(frozen=True)
class BacklogCiDelivery:
    """Backlog PRD 的 CI/CD 交付尾段投影（运行时派生，不持久化）。

    Console API 与 ``kc backlog ci status --json`` 共用同一份 DTO 结构；
    轮次事实源是 GitHub PR head SHA 与既有 ``iar:event`` marker。
    """

    prd_path: str
    issue_number: int | None
    status: CiDeliveryStatus
    checks_state: str | None
    checks_summary: tuple[str, ...]
    pr_url: str | None
    head_sha: str | None
    #: 已发生的自动修复轮数（``post_pr_rework_requested`` 且 action=repair 的次数）。
    round_count: int
    #: 修复轮数上限（复用 ``post_pr_supervisor.max_repair_attempts``）。
    max_rounds: int
    problems: tuple[CiCheckProblem, ...]
    #: 对应 Issue 最新 ``iar:ci-auto-repair-policy`` marker；无 marker 即 inherit。
    stored_policy: CiRepairPolicy
    #: 仓库全局 ``post_pr_supervisor.auto_repair_ci``（fresh 配置值）。
    global_enabled: bool
    #: 服务端计算的最终生效值：显式 on/off 优先，否则继承全局。
    effective_enabled: bool
    #: 自动修复预算耗尽（round_count >= max_rounds）。
    exhausted: bool
    exhausted_reason: str | None = None
    #: 最近一次成功同步 PR context 的时间（ISO8601）；GitHub 不可达时保留旧值。
    last_synced_at: str | None = None
