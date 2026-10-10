"""Agent Runner domain models and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.shared.interfaces.agent_output_protocol import PLAIN_PROTOCOL_ID
from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_decision import (
    InteractiveDecisionConfig,
    ReplConfig,
)
from backend.core.shared.models.agent_deliberation import DeliberationConfig
from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_spec import (
    BUILTIN_AGENT_SPECS,
    AgentSpec,
)
from backend.core.shared.models.lifecycle_agent import (
    LifecycleAgentsConfig,
    LifecyclePresetsConfig,
)

if TYPE_CHECKING:
    # `ValidationVerdict` lives in the use-cases layer; import it only for
    # type checking to avoid a circular runtime import (run_verifier_agent
    # imports from this module). Annotations are strings via __future__.
    from backend.core.use_cases.run_verifier_agent import ValidationVerdict


DEFAULT_VALIDATION_EVIDENCE_DIR = "tasks/evidence"
"""Realistic Validation 证据目录的默认仓库相对根路径。

新约定下证据按任务分子目录（``tasks/evidence/<prd-stem>/``，无 PRD 的 Issue 用
``tasks/evidence/issue-<N>/`` 兜底），``.md`` 文本报告经 ``.gitignore`` 白名单
进入版本库，原始产物被排除。显式把 ``validation.evidence_dir`` 配置成其它值
（如 legacy 的 ``.iar/evidence``）的仓库保持整目录排除的旧行为，逐字节不变。
"""


def evidence_dir_uses_task_subdirs(evidence_dir: str) -> bool:
    """判断证据目录配置是否走"按任务分子目录"的新约定。

    只有默认值 ``tasks/evidence`` 启用子目录语义；任何显式配置（含 legacy
    ``.iar/evidence``）保持扁平目录的旧行为。
    """
    return evidence_dir.strip("/") == DEFAULT_VALIDATION_EVIDENCE_DIR


@dataclass(frozen=True)
class AgentCommitResult:
    """Result of a successful agent execution with attempt history."""

    verification_results: list[CommandResult]
    attempt_results: list[AttemptResult]
    verifier_verdict: "ValidationVerdict | None" = None


@dataclass(frozen=True)
class TokenUsage:
    """单次 agent 子进程调用的官方 token 用量。

    数据全部来自 agent 输出自报的 usage 统计（如 claude stream-json 的
    ``result.usage``），系统只解析、不估算；拿不到时上层表达为 ``None``。
    Anthropic 语义下 ``input_tokens`` 不含缓存部分，因此输入侧实际处理量
    = input + cache_read + cache_creation，:attr:`total_tokens` 按此把缓存
    计入总量（实际处理量口径）。

    Attributes:
        input_tokens: 未命中缓存的输入（上传）token 数。
        output_tokens: 输出（下传）token 数。
        cache_read_input_tokens: 缓存命中（复用已缓存前缀）的输入 token 数。
        cache_creation_input_tokens: 首次写入缓存的输入 token 数。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """总量（实际处理量口径）= 输入侧（含缓存读写）+ 输出。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True)
class CommandResult:
    """Captured subprocess result.

    Attributes:
        command: Executed command and arguments.
        return_code: Process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
        duration_seconds: Wall-clock seconds the command took. ``0.0`` when the
            caller constructed the result synthetically instead of running a
            process.
        output_protocol: 产出本结果的输出协议 id（见
            ``iar.agent_output_protocols`` 注册表）。``"plain"`` 表示通用
            文本中继；agent 响应文本提取等消费方以此判断 stdout 是否为
            渲染后的结构化流，而不再嗅探命令行里的 agent 名。
        token_usage: 输出自报的 token 用量；协议未提供或解析不出时为
            ``None``（≠ 0，表示"无数据"而非"零消耗"）。
        session_id: agent CLI 自报的会话 id（如 claude stream-json 首行
            ``system/init`` 事件里的 ``session_id``），供崩溃对账后的会话续传
            回填给 ``--resume``。协议未解析到或未声明该能力时为 ``None``。
        reported_model: 执行器**自报**的实际模型名（如 claude stream-json
            ``system/init`` 事件里的 ``model``）。只表示"执行器报告了什么"，
            不声称已独立验证服务端内部运行的模型；协议未报告时为 ``None``
            （≠ 配置里的默认模型，展示为"未提供"）。
    """

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    output_protocol: str = PLAIN_PROTOCOL_ID
    token_usage: TokenUsage | None = None
    session_id: str | None = None
    reported_model: str | None = None


@dataclass(frozen=True)
class E2EVerificationArtifact:
    """浏览器 E2E 验证命令声明的单个产物（FR-5）。

    路径以 worktree 根为基准（POSIX 分隔符）。硬层检查（存在 / 非零字节 /
    mime / min_size / 新鲜度）复用 FR-11a 的 ``validate_evidence_artifact``，
    ``key_claim`` 与其他产物声明一样只喂 verifier，不做机检。

    Attributes:
        path: 产物相对 worktree 根的路径。
        mime: 期望的 ``file --mime-type`` 精确值（如 ``image/png``）。
        min_size: 期望的最小字节数；``None`` 表示只要求非空。
        key_claim: 给 verifier 的软层关键断言文本，可为空。
    """

    path: str
    mime: str
    min_size: int | None = None
    key_claim: str = ""


@dataclass(frozen=True)
class BrowserE2EVerificationCommand:
    """``verification_commands`` 的浏览器 E2E 结构化条目（FR-1）。

    与纯字符串条目共存、在同一队列按序执行；纯字符串条目的解析与执行
    行为逐字段不变。声明语义：``script`` 是 ``bash -lc`` 执行的脚本入口
    （相对 worktree 根），``app_start`` 声明如何后台拉起被测应用，产物
    声明见 :class:`E2EVerificationArtifact`。keda 只负责"提供执行环境 +
    收证据 + 门禁"，不绑定任何 E2E 断言框架（D-01）。

    Attributes:
        kind: 条目形态标记，固定为 ``"browser_e2e"``。
        script: E2E 脚本入口（worktree 相对路径），执行前做存在性预检。
        app_start: 被测应用的启动 shell 命令；``None`` 表示无应用管理层。
        ready_url: 应用就绪的 HTTP 探测地址；未提供 ``app_start`` 时无意义。
        ready_timeout_seconds: ``ready_url`` 探测的等待上限。
        startup_wait_seconds: 无 ``ready_url`` 时的固定等待（回退形态）。
        probe: 浏览器运行时可用性探测命令；``None`` 使用内置启发式探测。
        timeout_seconds: E2E 运行的 wall-clock 超时，超时击杀整个进程组。
        inactivity_timeout_seconds: 无输出超时；``None`` 不启用。
        env_allow: 追加进子进程环境变量白名单的变量名（运营者显式声明）。
        artifacts: 产物声明列表。
    """

    kind: str = "browser_e2e"
    script: str = ""
    app_start: str | None = None
    ready_url: str | None = None
    ready_timeout_seconds: int = 60
    startup_wait_seconds: int | None = None
    probe: str | None = None
    timeout_seconds: int = 900
    inactivity_timeout_seconds: int | None = None
    env_allow: tuple[str, ...] = ()
    artifacts: tuple[E2EVerificationArtifact, ...] = ()


#: ``verification_commands`` 的条目联合：纯 shell 字符串或 E2E 结构化条目。
VerificationCommand = str | BrowserE2EVerificationCommand


def describe_verification_command(command: VerificationCommand) -> str:
    """把一条验证命令配置渲染成给人 / agent 阅读的单行描述。

    纯字符串条目返回原命令；E2E 结构化条目返回带形态标记的摘要，
    保证 prompt 注入的命令清单对两类条目都有可读表示。
    """
    if isinstance(command, str):
        return command
    description = f"[browser_e2e] {command.script}"
    if command.app_start:
        description += f" (app: {command.app_start})"
    return description


class FailureType(Enum):
    """Categorized failure reason from an agent execution attempt."""

    SUCCESS = "success"
    UNCOMMITTED_CHANGES = "uncommitted_changes"
    NO_COMMITS = "no_commits"
    VERIFICATION_FAILED = "verification_failed"
    AGENT_ERROR = "agent_error"
    TRANSIENT = "transient"
    PROVIDER_CAPACITY = "provider_capacity"
    UNRECOVERABLE = "unrecoverable"
    FORBIDDEN_BLOCKED = "forbidden_blocked"
    DELIVERY_CLOSEOUT = "delivery_closeout"
    # 跨进程对账（daemon 崩溃 / 硬击杀后遗留的 ``agent/running`` 僵尸 attempt）。
    # 该分类由 ``agent_runner_failure.py`` 的 ``StaleAttemptDisposition`` 渲染，
    # 不来自 ``classify_failure`` 的 attempt 判定阶梯。
    STALE_ATTEMPT = "stale_attempt"


class DeliveryGateFailureKind(Enum):
    """交付门禁失败的分类，决定该失败能否交给收尾层（Closeout Agent）。

    分类由**抛出门禁错误的那行代码**显式声明，而不是在执行循环里匹配错误文案：
    门禁文案是给 agent 读的英文散文，会随 prompt 调优被改写，用正则分流必然漂移，
    且漂移方向恰好最危险——一条被改写的"RV 重跑失败"文案匹配不上真失败规则就会
    掉进轻量收尾路径。默认取值是 :attr:`SUBSTANTIVE`，因此新增抛出点漏标的后果
    是"少省一点时间"而不是"门禁被绕过"。

    Attributes:
        SUBSTANTIVE: 真失败——行为没做对或验证没真跑过，必须整轮重跑完整实现。
        CHECKLIST_UNCHECKED: 验收清单存在未勾条目。
        CHANGE_LOG_INCOMPLETE: PRD 已改动但 Change Log 缺失、为零、未追加或字段不全。
        EVIDENCE_MANIFEST_FORMAT: 结构化证据清单文件的字段格式非法。
        FRONTEND_VISUAL_EVIDENCE_MISSING: 前端有改动但证据目录缺少截图 / 录屏。
        ACCEPTANCE_BANNER_MISMATCH: PRD 验收状态横幅与验收清单不一致（例如人审组仍有
            空框却没写待人工验收）。
    """

    SUBSTANTIVE = "substantive"
    CHECKLIST_UNCHECKED = "checklist_unchecked"
    CHANGE_LOG_INCOMPLETE = "change_log_incomplete"
    EVIDENCE_MANIFEST_FORMAT = "evidence_manifest_format"
    FRONTEND_VISUAL_EVIDENCE_MISSING = "frontend_visual_evidence_missing"
    ACCEPTANCE_BANNER_MISMATCH = "acceptance_banner_mismatch"

    @property
    def is_closeout_eligible(self) -> bool:
        """该分类是否可以交给收尾层处理（真失败类永远不可以）。"""
        return self is not DeliveryGateFailureKind.SUBSTANTIVE

    @property
    def needs_visual_capture(self) -> bool:
        """收尾是否需要真实启动应用采集视觉证据（决定用哪个超时预算）。"""
        return self is DeliveryGateFailureKind.FRONTEND_VISUAL_EVIDENCE_MISSING


class DeliveryGateError(RuntimeError):
    """携带 :class:`DeliveryGateFailureKind` 的交付门禁错误基类。

    PRD 交付门禁与 Realistic Validation 证据门禁的错误类型都继承本类，使
    "分类在抛出处声明"这条规则只有一份实现。仍然继承 :class:`RuntimeError`，
    既有 ``except RuntimeError`` 调用方行为不变。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: DeliveryGateFailureKind = DeliveryGateFailureKind.SUBSTANTIVE,
    ) -> None:
        """记录门禁失败文案与分类。

        Args:
            message: 给 agent 阅读的失败说明。
            kind: 门禁失败分类；不传时按真失败处理。
        """
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class PhaseDuration:
    """Wall-clock time one execution-loop phase consumed inside an attempt.

    只有一个 attempt 总时长时，"卡了很久"无法定位：几千秒到底是 agent 自己在
    想、还是 ``just test`` 挂住、还是某条 RV 命令一路超时重试，看不出来。按阶段
    累计后，attempt 历史才能直接回答"时间花在哪"。

    Attributes:
        name: Phase identifier, e.g. ``agent`` / ``verification`` /
            ``prd_delivery`` / ``evidence`` / ``rv_reexec`` / ``verifier`` /
            ``commit``.
        seconds: Accumulated wall-clock seconds for that phase within the
            attempt (同一阶段被多次进入时累加).
    """

    name: str
    seconds: float


@dataclass(frozen=True)
class AttemptResult:
    """Record of a single agent execution attempt.

    Attributes:
        attempt_number: 1-based attempt index within a single agent's run.
        failure_type: Classified outcome of the attempt.
        recovered: Whether this attempt recovered from a prior failure.
        detail: Human-readable detail rendered into the failure comment.
        agent: Name of the agent that produced this attempt.
        started_at: ISO-8601 UTC timestamp when the attempt started.
        finished_at: ISO-8601 UTC timestamp when the attempt finished.
        duration_seconds: Wall-clock seconds spent in the attempt.
        phase_durations: Per-phase breakdown of ``duration_seconds``；空元组表示
            本次 attempt 未打点（旧记录或未走执行循环的路径）。
        token_usage: 本次 attempt 对应 agent 调用的 token 用量；来源调用
            未产出可用 usage 时为 ``None``。
        preset: 绑定生效时实际使用的预设名（空串 = 未绑定或绑定被丢弃）。
        model: 绑定生效时实际注入的模型 id（空串 = 未注入）。
    """

    attempt_number: int
    failure_type: FailureType
    recovered: bool
    detail: str
    agent: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    phase_durations: tuple[PhaseDuration, ...] = ()
    token_usage: TokenUsage | None = None
    #: 绑定生效时实际使用的预设名（空串 = 未绑定或绑定被丢弃）。
    preset: str = ""
    #: 绑定生效时实际注入的模型 id（空串 = 未注入）。
    model: str = ""


@dataclass(frozen=True)
class IssueSummary:
    """GitHub Issue selected for runner execution."""

    number: int
    title: str
    url: str
    body: str
    labels: tuple[str, ...]
    state: str = "OPEN"
    #: 该 Issue 所引用 PRD 的头部 ``lifecycle_agents`` 覆盖（键值对元组）。
    #: 编排入口拿到仓库路径后从 PRD 文件解析回填，使实现 / 审核 / 监督等阶段
    #: 的解析函数无需额外参数即可读到 PRD 级覆盖（PRD 级优先级最高）。
    lifecycle_overrides: tuple[tuple[str, str], ...] = ()
    #: 该 Issue 所引用 PRD 的头部 ``lifecycle_presets`` 块（键值对元组）。
    #: 与 ``lifecycle_overrides`` 同机制：随 Issue 流动，供模型绑定解析。
    lifecycle_preset_overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PullRequestSummary:
    """View-model summary of a Pull Request linked to an Issue.

    Attributes:
        number: PR number within the repository.
        state: Normalized state, one of ``"open"``, ``"draft"``,
            ``"merged"``, ``"closed"``.
        url: Web URL of the PR.
        is_draft: Whether the PR is currently a draft.
        merged: Whether the PR has been merged.
        title: PR title (used by ``--output json`` consumers).
    """

    number: int
    state: str
    url: str
    is_draft: bool
    merged: bool
    title: str


@dataclass(frozen=True)
class IssueWithPulls:
    """View-model row for ``kc issue list`` output.

    Attributes:
        repo: ``owner/name`` identifier when the list spans multiple
            repositories; ``None`` for single-repository listings.
        number: Issue number within the repository.
        title: Issue title.
        state: GitHub state (``"OPEN"`` / ``"CLOSED"``).
        labels: Label names attached to the Issue.
        updated_at: ISO-8601 timestamp of the last update (or empty
            string when the backend cannot supply one).
        url: Web URL of the Issue.
        pulls: Linked Pull Requests, already normalized.
    """

    repo: str | None
    number: int
    title: str
    state: str
    labels: tuple[str, ...]
    updated_at: str
    url: str
    pulls: tuple[PullRequestSummary, ...]


@dataclass(frozen=True)
class LabelConfig:
    """GitHub labels used as runner queue state."""

    ready: str = "agent/ready"
    running: str = "agent/running"
    supervising: str = "agent/supervising"
    review: str = "agent/review"
    failed: str = "agent/failed"
    blocked: str = "agent/blocked"
    waiting: str = "agent/waiting"
    validation_pending: str = "validation/pending"
    validation_passed: str = "validation/passed"
    verifier_passed: str = "validation/verifier-passed"
    rework_prd: str = "agent/rework-prd"
    deliberate: str = "agent/deliberate"
    # 直发档选择标签：不是 workflow 状态标签，状态切换必须保留它，
    # 由认领赢家在一次成功发布后消费（移除）。
    direct_pr: str = "direct-pr"
    # agent 路由标签由 agent 注册表派生（agent 名 -> spec.label），
    # 注册顺序即 choose_agent 的标签匹配顺序。
    agent_labels: dict[str, str] = field(
        default_factory=lambda: {
            agent_name: agent_spec.label for agent_name, agent_spec in BUILTIN_AGENT_SPECS.items()
        }
    )


#: worktree 命令默认值：命令名取自身份模块，``{…}`` 占位符留给运行期 format。
#: 与 infrastructure ``agent_runner_settings.py`` 的同名字面量必须保持一致
#: （双类陷阱：运行时读的是本模块的默认值）。
_WORKTREE_CREATE_COMMAND_DEFAULT = (
    f"{product_identity.PRIMARY_COMMAND_NAME} worktree create "
    "--branch issue-{issue_number} --base-branch {base_branch}"
)
_WORKTREE_PATH_COMMAND_DEFAULT = (
    f"{product_identity.PRIMARY_COMMAND_NAME} worktree path " "--branch issue-{issue_number}"
)


@dataclass(frozen=True)
class GitConfig:
    """Git publishing configuration."""

    remote: str = "origin"
    base_branch: str = "main"


@dataclass(frozen=True)
class WorktreeConfig:
    """Commands used to create and locate target worktrees.

    ``base_branch`` is the repository base branch (e.g. ``main``). It is
    passed to the ``create_command`` template so ``git worktree add`` can
    fork from the correct ref. See
    :mod:`backend.infrastructure.config.agent_runner_settings` for the
    matching Pydantic defaults; both classes must keep the same literal
    because the runtime reads this one.
    """

    create_command: str = _WORKTREE_CREATE_COMMAND_DEFAULT
    reuse_command: str = _WORKTREE_PATH_COMMAND_DEFAULT
    path_command: str = _WORKTREE_PATH_COMMAND_DEFAULT
    # 默认开启;缺建库脚本或无关系型 DB 时告警回退到共享库(见 use_cases.worktree_database)。
    provision_database: bool = True
    base_branch: str = "main"


@dataclass(frozen=True)
class AgentFallbackCandidate:
    """``[[agent_runner.runner.agent_fallback_candidates]]`` 的一条有序回退候选。

    Attributes:
        agent: 候选执行器名。
        preset: 该候选命中执行时应用的 ``[agent_runner.presets.<name>]`` 预设名；
            ``None`` 表示不注入模型参数（与旧纯 agent 名单语义等价）。
    """

    agent: str
    preset: str | None = None


@dataclass(frozen=True)
class RunnerConfig:
    """Local runner behavior.

    Attributes:
        agent_fallback_order: Ordered agents to try for one Issue after the
            primary agent. Empty disables cross-agent fallback (single-agent
            behavior). The primary agent is prepended and de-duplicated by
            ``resolve_agent_fallback_order``.
        agent_fallback_candidates: Ordered ``(agent, optional preset)`` fallback
            candidates. When non-empty this array takes over the runtime
            fallback chain wholesale (``agent_fallback_order`` then only serves
            as the legacy read path for no-preset candidates); the same agent
            may repeat with different presets. Empty keeps the legacy
            agent-name-only semantics byte-for-byte.
        max_agent_switches: Maximum number of agent switches before the Issue
            is marked failed.
        transient_retry_attempts: In-place retries granted to transient
            network/transport errors (Level 1 of the escalation ladder).
        transient_retry_delay_seconds: Backoff between transient retries.
        max_concurrent_issues: Maximum Issues processed in parallel within a
            single daemon pass. ``1`` keeps the sequential path (zero
            regression); ``> 1`` enables the thread-pool parallel path.
        timeout_seconds: Wall-clock timeout for a single agent execution.
        inactivity_timeout_seconds: Kill the agent if it produces no stdout or
            stderr for this many seconds.
        fix_agent_enabled: Whether the lightweight Fix Agent runs when staged
            verification fails inside the commit proxy. When ``False``, staged
            verification failures escalate straight to the full Recovery Agent.
        fix_timeout_seconds: Optional shorter timeout for the Fix Agent phase.
            When ``None``, falls back to ``timeout_seconds``.
        recovery_timeout_seconds: Optional timeout for the full Recovery Agent
            phase. When ``None``, falls back to ``timeout_seconds``.
        closeout_agent_enabled: 交付收尾层（Closeout Agent）开关。为 ``False``
            时，四类收尾类门禁失败按本层落地前的行为整轮重跑。
        closeout_timeout_seconds: 文本类收尾（补勾选 / 补 Change Log / 修证据
            清单字段）的 wall-clock 上限。``None`` 时依次回退到
            ``fix_timeout_seconds``、再到 ``timeout_seconds``。
        closeout_visual_timeout_seconds: 视觉证据补采的 wall-clock 上限。补一张
            截图要真把应用跑起来，不该和"补一条 Change Log"共用同一个短超时。
            ``None`` 时的回退链同上。
    """

    max_issues: int = 1
    max_concurrent_issues: int = 1
    default_agent: str = "auto"
    max_recovery_attempts: int = 5
    recovery_retry_delay_seconds: int = 30
    agent_fallback_order: tuple[str, ...] = ("claude", "kimi", "codex")
    agent_fallback_candidates: tuple[AgentFallbackCandidate, ...] = ()
    max_agent_switches: int = 2
    transient_retry_attempts: int = 2
    transient_retry_delay_seconds: int = 10
    timeout_seconds: int = 14400
    fix_agent_enabled: bool = True
    fix_timeout_seconds: int | None = None
    recovery_timeout_seconds: int | None = None
    closeout_agent_enabled: bool = True
    closeout_timeout_seconds: int | None = 600
    closeout_visual_timeout_seconds: int | None = 1800
    inactivity_timeout_seconds: int = 1200
    verification_commands: tuple[VerificationCommand, ...] = (
        "git diff --check",
        "uv run mkdocs build",
    )
    pre_commit_verification_command: str | None = None

    def __post_init__(self) -> None:
        """把 ``agent_fallback_candidates`` 的 dict 形态条目收进强类型元组。

        ``factory_config_merge._merge_optional_model`` 用 ``dataclasses.asdict``
        展平基础配置、并把仓库级 pydantic 覆盖 ``model_dump`` 出的 dict 原样传入
        构造函数，因此这里统一回填为 :class:`AgentFallbackCandidate`，保证运行期
        只见到一种条目形态。
        """
        normalized_candidates: list[AgentFallbackCandidate] = []
        for candidate_entry in tuple(self.agent_fallback_candidates or ()):
            if isinstance(candidate_entry, AgentFallbackCandidate):
                normalized_candidates.append(candidate_entry)
            else:
                normalized_candidates.append(AgentFallbackCandidate(**dict(candidate_entry)))
        object.__setattr__(self, "agent_fallback_candidates", tuple(normalized_candidates))

    def resolve_closeout_timeout_seconds(self, kind: DeliveryGateFailureKind) -> int:
        """返回该类收尾实际生效的 wall-clock 超时秒数。

        视觉证据补采与文本类收尾各有一个上限；任一项为空时依次回退到 Fix Agent
        超时、再到常规 agent 超时，因此运营者只配 ``timeout_seconds`` 也能工作。

        Args:
            kind: 触发本次收尾的门禁失败分类。

        Returns:
            该次收尾传给子进程的超时秒数。
        """
        configured_timeout_seconds = (
            self.closeout_visual_timeout_seconds
            if kind.needs_visual_capture
            else self.closeout_timeout_seconds
        )
        return configured_timeout_seconds or self.fix_timeout_seconds or self.timeout_seconds


@dataclass(frozen=True)
class MemoryConfig:
    """Local memory and skill distillation configuration.

    Anchor semantics for ``base_dir`` / ``skill_drafts_dir`` /
    ``promoted_skills_dirs``:

    - 相对路径由 :mod:`backend.engines.agent_runner.factory` 在构建
      :class:`RepositoryRunContext` 时解析到目标仓库主检出根，并在
      该点展开 ``~``。
    - 绝对路径（含 ``expanduser`` 后变绝对者）保持原样，不再挂到任何
      锚点下；运营者把目录移出仓库外（防 ``git clean -fdx`` 误删或
      多机共享）只需把相对路径改为绝对路径即可。
    - ``worktree_path`` 在组装 store 时仍作为实参传入，但只要上述
      目录已是绝对路径就不会被使用——是 Zero-signature-disturbance
      修复路径的副作用。

    Attributes:
        enabled: Master switch — when ``False`` the runner skips every memory
            read/write and skill distillation step. Existing happy-path
            behaviour must not change in that mode.
        base_dir: 短期/长期记忆的相对或绝对目录；详见上方"锚点语义"。
        skill_drafts_dir: 等待晋升的 skill 草稿目录；锚点语义同上。
        promoted_skills_dirs: 已晋升 skill 的扫描目录列表（有序），
            同样遵循上述锚点语义；第一个存在的目录在按名查找 skill
            时胜出。
        top_k_skills: Maximum number of promoted skills to surface per
            ``build_prompt`` invocation.
        top_k_facts: Maximum number of long-term facts to surface per
            ``build_prompt`` invocation.
        auto_promote: Whether the runner may auto-move drafts to a
            promoted-skills directory once thresholds are met.
        auto_promote_threshold: Minimum ``usage_count`` required to
            auto-promote a draft.
        auto_promote_min_success_rate: Minimum ``success_count /
            usage_count`` ratio required to auto-promote a draft.
    """

    enabled: bool = True
    base_dir: str = ".kedacode/memory"
    skill_drafts_dir: str = ".kedacode/skills/drafts"
    promoted_skills_dirs: tuple[str, ...] = (".kedacode/skills",)
    top_k_skills: int = 3
    top_k_facts: int = 5
    auto_promote: bool = True
    auto_promote_threshold: int = 3
    auto_promote_min_success_rate: float = 1.0


@dataclass(frozen=True)
class SafetyConfig:
    """Safety boundaries enforced before publishing."""

    auto_merge: bool = False
    forbidden_path_patterns: tuple[str, ...] = (
        ".env",
        ".env.*",
        "secrets/*",
        "docker-compose.prod.yml",
    )


@dataclass(frozen=True)
class AutopilotConfig:
    """Autopilot / merge queue configuration.

    该字段决定 **review pass 末尾是否启用合并队列消费** ``safety.auto_merge``。
    两个开关同时为真才会真正消费合并队列；任一为假都退回到"等人工合并"的
    现状，严格档行为零变化。这种"双开关"设计专门为了防止
    :class:`SafetyConfig.auto_merge` 死开关"突然激活"——历史上
    ``safety.auto_merge`` 一直是死配置，从未被代码消费；现在它真正生效，
    任何早前误设 ``true`` 的环境若没同时开启 ``autopilot.enabled`` 新键
    也不会开始自动合并，提供一层防呆保护。

    Attributes:
        enabled: 主开关；为 ``False`` 时合并队列整段 no-op。独立于
            ``safety.auto_merge`` 是为了"双开关"语义。
        merge_method: 唯一接受的合并方式：``"squash"``。其它值在配置加载期
            由 ``AutopilotConfig`` 校验期拒绝。这是用户锁定的设计决策——
            单提交便于 ``git revert`` 回退。
        require_verifier_pass: 当该 Issue 需要 validation 时，是否要求
            ``validation/verifier-passed`` 标签先存在；缺失则在合并队列轮
            里跳过本轮，留给 verifier / repair 流程。
        auto_sign_off: 是否替人工勾选 PR body 的 Realistic Validation
            sign-off 清单；勾选与 marker 评论均幂等。
        merge_check_timeout_seconds: 等 PR checks 全绿的最大等待秒数；
            默认 1800（30 分钟），超时则放弃本轮合并。
    """

    enabled: bool = False
    merge_method: str = "squash"
    require_verifier_pass: bool = True
    auto_sign_off: bool = True
    merge_check_timeout_seconds: int = 1800


@dataclass(frozen=True)
class BacklogConfig:
    """Backlog 持续调度配置，与自动合并配置相互独立。"""

    auto_advance: bool = False


@dataclass(frozen=True)
class PromptConfig:
    """Agent prompt template configuration."""

    default_phase: str = "execution"
    phases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationConfig:
    """Realistic Validation evidence gate configuration.

    ``evidence_dir`` is relative to the worktree root. With the default
    ``tasks/evidence`` the runner resolves a per-task subdirectory
    (``tasks/evidence/<prd-stem>/``, or ``tasks/evidence/issue-<N>/`` when the
    Issue references no canonical PRD) and relies on the ``.gitignore``
    whitelist provisioned by ``kc init`` so that only ``*.md`` reports enter
    the code diff while raw artifacts stay out of git history. A repo that
    explicitly configures another value (e.g. the legacy ``.iar/evidence``)
    keeps the old whole-directory ``info/exclude`` exclusion, byte for byte.
    ``branch_prefix`` names the orphan branches that carry
    evidence to reviewers (``<branch_prefix>issue-<N>``); these branches are
    never merged and are deleted once the Issue closes.

    ``evidence_format_check`` toggles the per-item evidence matching rules
    (every item needs its own ``rv-<n>-*`` file in the format the item
    names); switching it off keeps only the non-empty evidence requirement.
    Individual PRDs can opt out via an ``Evidence Format Waiver: <reason>``
    line in their Realistic Validation section.

    ``language`` controls the fixed labels in prompts and PR evidence comments.
    ``structured_evidence`` enables the ``evidence.json`` manifest requirement
    for new Issues that carry the ``iar:structured-evidence`` marker.
    ``require_negative_control`` (default on) makes the gate reject any
    structured-evidence item lacking a ``negative_control`` (red→green proof);
    set it off to opt out per repository.

    ``frontend_visual_evidence_required`` (default on) makes the gate reject a
    delivery whose git changes touch any ``frontend_paths`` prefix unless the
    evidence directory holds at least one visual file (image/video). It is
    driven by the diff, not by checklist keywords, and is independent of
    ``verifier_enabled``; set it off to opt out per repository.

    ``verifier_timeout_seconds`` 是墙钟上限,``verifier_inactivity_timeout_seconds``
    是"多久没有任何输出就判卡死"。只有墙钟一条线时,"复跑 E2E 的慢 verifier"和
    "彻底卡死的 verifier"没有区别:想让前者跑完就必须把墙钟拉长,而拉长同样惠及
    后者。两条线并存后,墙钟可以按最慢的真实工作量给足(多条 RV + negative control
    动辄超过半小时),真卡死仍在静默期结束时就被杀掉。语义与 builder 侧的
    ``RunnerConfig.inactivity_timeout_seconds`` 相同,默认值也对齐。
    """

    enabled: bool = True
    evidence_dir: str = DEFAULT_VALIDATION_EVIDENCE_DIR
    branch_prefix: str = "iar-evidence/"
    evidence_format_check: bool = True
    parse_evidence_format_with_agent: bool = True
    language: str = "zh-CN"
    structured_evidence: bool = True
    require_negative_control: bool = True
    reexecute_commands: bool = True
    reexecute_timeout_seconds: int = 300
    reexecute_cache_enabled: bool = True
    verifier_enabled: bool = True
    verifier_agent: str = "auto"
    verifier_timeout_seconds: int = 1800
    verifier_inactivity_timeout_seconds: int = 1200
    artifact_health_enabled: bool = True
    frontend_visual_evidence_required: bool = True
    frontend_paths: tuple[str, ...] = ("frontend-admin", "frontend-public")


@dataclass(frozen=True)
class ReviewFinding:
    """Structured finding emitted by the pre-push reviewer."""

    category: str = ""
    severity: str = ""
    title: str = ""
    description: str = ""
    file: str = ""
    line: int = 0
    recommendation: str = ""


@dataclass(frozen=True)
class PrePrReviewConfig:
    """Pre-PR AI review gate configuration.

    The review runs **after** the implementation commit has been pushed to the
    remote but **before** the Draft PR is created, so reviewer patches are
    pushed to the feature branch while the PR creation gate waits for the
    review to converge.
    """

    enabled: bool = True
    review_agent: str = "auto"
    allow_same_agent: bool = True
    # 谁应用本阶段 findings 的修复：``self``（审核者自己打补丁，历史行为）/
    # ``executor``（交回本次实现者）/ 任意已注册 agent 名。与
    # ``runner.fix_agent_enabled``（本地验证失败的轻量修复 agent）无关。
    repair_agent: str = "self"
    max_attempts: int = 2
    timeout_seconds: int = 1800
    # When the reviewer reports findings but fails to write a commit request,
    # the runner appends a reminder and re-invokes the reviewer up to this
    # many times within the same review cycle. This prevents the runner from
    # giving up just because the reviewer listed problems without producing a
    # patch.
    commit_request_reminder_attempts: int = 1
    # Overrides for the review rules appended after the review packet.
    # Empty tuple means "use the embedded default template" which instructs
    # the reviewer to call the ``code-reviewer`` skill and emit a findings
    # array. Repositories can override individual lines via ``.kedacode.toml``
    # without forking ``agent_review.py``.
    review_prompt_template: tuple[str, ...] = ()


@dataclass(frozen=True)
class FindingDetail:
    """一条 supervisor finding 明细（补丁 4：跨 cycle 累积的最小载体）。

    Attributes:
        severity: ``high`` / ``medium`` / ``low``。
        title: finding 标题，与 ``file`` 组成跨 cycle 去重键。
        description: 补充说明，可为空。
        file: 相关文件（worktree 相对路径），可为空。
        line: 相关行号，未知时为 ``0``。
        status: ``open`` 表示仍未解决，``resolved`` 表示本 cycle 已修。
        cycle_reported: 首次报告的 cycle 序号。
    """

    severity: str = "medium"
    title: str = ""
    description: str = ""
    file: str = ""
    line: int = 0
    status: str = "open"
    cycle_reported: int = 0


@dataclass(frozen=True)
class PostPrSupervisorConfig:
    """Post-PR supervisor cycle configuration."""

    enabled: bool = True
    supervisor_agent: str = "auto"
    # 谁执行 supervisor 判定后的代码修复：``self``（supervisor 自己修，历史
    # 行为）/ ``executor``（交回本次实现者，拿不到时按 Issue 标签回落并记录
    # 来源）/ 任意已注册 agent 名。
    repair_agent: str = "self"
    # CI/CD 自动修复策略（独立开关）：仅约束 repair_scope=ci 的修复；
    # 代码审查修复不受此开关限制，但共享修复轮数预算。默认关闭。与
    # autopilot.enabled / safety.auto_merge / runner.fix_agent_enabled 语义独立。
    auto_repair_ci: bool = False
    max_repair_attempts: int = 2
    max_agent_crash_retries: int = 5
    crash_retry_initial_backoff_seconds: int = 30
    crash_retry_max_backoff_seconds: int = 600
    # 补丁 3：关键路径前缀；命中的文件 diff 全量注入 supervisor prompt
    key_paths: tuple[str, ...] = ()
    max_diff_chars: int = 6000
    # 补丁 4：跨 cycle finding 注入开关（opt-out）与 artifact 落盘目录
    previous_findings_injection_enabled: bool = True
    findings_artifact_dir: str = ".iar/state"


@dataclass(frozen=True)
class PullRequestContext:
    """Minimal PR context for supervisor decisions."""

    pr_url: str
    branch: str
    head_sha: str
    base_sha: str
    mergeable: bool | None = None
    checks_state: str | None = None
    checks_summary: tuple[str, ...] = ()
    number: int | None = None
    body: str = ""


@dataclass(frozen=True)
class ReviewEventMarker:
    """Parsed iar:event hidden marker from an Issue comment."""

    version: int
    phase: str
    cycle: int
    head_sha: str | None = None
    base_sha: str | None = None
    pr_branch: str | None = None
    action: str | None = None
    checks_state: str | None = None
    mergeable: bool | None = None
    issue_comments_count: int | None = None
    pr_comments_count: int | None = None
    blocked_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupervisorActionResult:
    """Outcome of a single supervisor cycle."""

    action: str
    summary: str = ""
    findings_counts: dict[str, int] = field(default_factory=dict)
    verification_status: str = ""
    head_sha: str | None = None
    findings_detail: tuple[FindingDetail, ...] = ()
    #: 修复范围由 Supervisor 明确声明；缺失或无效时保守归为 CI。
    repair_scope: str = "ci"
    #: 本 cycle 的 supervisor agent 调用自报用量；守卫层改写不携带它，由
    #: cycle 末尾统一回填，供观测层发用量事件。
    token_usage: TokenUsage | None = None


@dataclass(frozen=True)
class GeneratedContentTargetConfig:
    """Configuration for a single generated-content target."""

    enabled: bool = True
    mode: str = "agent"
    output: str = "json"
    title_template: str = ""
    body_template: str = ""
    agent: str = "auto"
    timeout_seconds: int = 120
    prompt: str = ""
    include_commit_log: bool = True
    include_diff_stat: bool = True


@dataclass(frozen=True)
class GeneratedContentConfig:
    """Generated-content configuration for Issues and PRs."""

    enabled: bool = True
    fallback: str = "template"
    max_input_chars: int = 20000
    default_agent: str = "auto"
    # 生命周期矩阵 ``content_generation`` 显式声明的具体 agent（装配期从
    # ``AppConfig.lifecycle_agents`` 派生）。非空时优先于 ``default_agent``，
    # 只影响内容生成阶段，target 级 ``agent`` 仍更优先。
    lifecycle_default_agent: str | None = None
    # 各 target 默认都是 agent 模式；``output`` 必须与各自提示词的回复格式一致：
    # issue_from_prd 的提示词回 JSON（沿用类默认），draft_pr / prd_from_issue 回 Markdown。
    issue_from_prd: GeneratedContentTargetConfig = field(
        default_factory=GeneratedContentTargetConfig
    )
    # 一句话需求 → Issue 正文（``kc issue create --from-prompt``）：产物不含 PRD 锚点，
    # 由 generated_issue_prompt_content 独立消费，不走 issue_from_prd 的锚点校验。
    issue_from_prompt: GeneratedContentTargetConfig = field(
        default_factory=GeneratedContentTargetConfig
    )
    draft_pr: GeneratedContentTargetConfig = field(
        default_factory=lambda: GeneratedContentTargetConfig(enabled=False, output="markdown")
    )
    prd_from_issue: GeneratedContentTargetConfig = field(
        default_factory=lambda: GeneratedContentTargetConfig(output="markdown")
    )


@dataclass(frozen=True)
class GeneratedIssueContent:
    """Result of generated Issue content."""

    title: str
    body: str
    source: str = "fallback"


@dataclass(frozen=True)
class GeneratedPrContent:
    """Result of generated PR content."""

    title: str
    body: str
    source: str = "fallback"


@dataclass(frozen=True)
class RepositoryIdentity:
    """Per-context view of a repository's identity (core/ dataclass).

    The ``engines`` layer materialises this from the Pydantic
    ``AgentRunnerRepositorySettings`` during ``merge_repository_config``
    so that ``core/`` use cases can look up the GitHub owner/repo label
    without depending on ``infrastructure/`` Pydantic types.

    Attributes:
        id: Optional iar-internal identifier (e.g. ``"keda-main"``). Falls
            back to ``path`` when the registry entry did not set one.
        path: Resolved absolute path to the repository working tree.
        display_name: Human-readable label used by status surfaces.
        github_repo: Optional ``owner/name`` string consumed by
            ``gh pr list --repo``. ``None`` means the PR column should
            remain empty with a one-shot stderr warning.
    """

    id: str | None
    path: str
    display_name: str | None
    github_repo: str | None = None


@dataclass(frozen=True)
class DaemonConfig:
    """Daemon 长驻轮询层的仓库级开关（``.kedacode.toml`` 的 ``[agent_runner.daemon]``）。

    只承载**按仓库判定**才对账正确、且确实被消费的两个键；轮询间隔等仍由 CLI 边界
    按全局设置与 ``--interval`` 旗标解析（daemon 一次服务多仓，间隔无法按仓取值），
    因此这里刻意不镜像那些键，避免配置视图里出现没人消费的真值来源。

    Attributes:
        reconcile_stale_attempts: 崩溃对账主开关；``None`` 表示仓库未声明，沿用调用方
            传入的全局默认。``False`` 时 Phase -1 整轮空转，僵尸保持 ``agent/running``
            不被触碰（即本特性落地前的现状）。
        reclaim_ttl_seconds: claim 老化阈值(秒)；``None`` 表示沿用调用方默认。
    """

    reconcile_stale_attempts: bool | None = None
    reclaim_ttl_seconds: int | None = None


@dataclass(frozen=True)
class AppConfig:
    """Application configuration."""

    # ``repositories`` is the merged per-context view of repository identity
    # (keyed by repo_id or path). It is **not** the same field as
    # ``AgentRunnerSettings.repositories`` (the load-time registry); that
    # field is Pydantic and lives in ``infrastructure/``. Keeping
    # ``RepositoryIdentity`` as a frozen dataclass in ``core/`` avoids
    # ``core/`` -> ``infrastructure/`` reverse dependency.
    repositories: dict[str, RepositoryIdentity] = field(default_factory=dict)
    # agent 注册表：agent 名 -> 声明式调用 spec。默认值为内置四个 agent
    # （codex / claude / kimi / pi）；config.toml / .kedacode.toml 的
    # [agent_runner.agents.*] 段经 factory 映射与两层合并覆盖此字段。
    agents: dict[str, AgentSpec] = field(default_factory=lambda: dict(BUILTIN_AGENT_SPECS))
    labels: LabelConfig = LabelConfig()
    git: GitConfig = GitConfig()
    worktree: WorktreeConfig = WorktreeConfig()
    runner: RunnerConfig = RunnerConfig()
    memory: MemoryConfig = MemoryConfig()
    safety: SafetyConfig = SafetyConfig()
    autopilot: AutopilotConfig = AutopilotConfig()
    backlog: BacklogConfig = BacklogConfig()
    validation: ValidationConfig = ValidationConfig()
    prompts: PromptConfig = field(default_factory=PromptConfig)
    pre_pr_review: PrePrReviewConfig = PrePrReviewConfig()
    post_pr_supervisor: PostPrSupervisorConfig = PostPrSupervisorConfig()
    generated_content: GeneratedContentConfig = field(default_factory=GeneratedContentConfig)
    interactive_decision: InteractiveDecisionConfig = field(
        default_factory=InteractiveDecisionConfig
    )
    repl: ReplConfig = field(default_factory=ReplConfig)
    # daemon 层的仓库级对账开关（None = 未声明，沿用调用方全局默认）。
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    deliberation: DeliberationConfig = field(default_factory=DeliberationConfig)
    # 生命周期 Agent 矩阵的显式声明（含来源层）；未声明的键由
    # resolve_lifecycle_agent 回落到既有散落配置键与内置默认。
    lifecycle_agents: LifecycleAgentsConfig = field(default_factory=LifecycleAgentsConfig)
    # 命名模型预设清单（[agent_runner.presets.<name>]）；绑定层经
    # resolve_lifecycle_model_selection 消费。
    agent_presets: dict[str, AgentModelPreset] = field(default_factory=dict)
    # 阶段 -> 预设 绑定的两层声明视图（PRD 块随 Issue 流动，不在本视图内）。
    lifecycle_presets: LifecyclePresetsConfig = field(default_factory=LifecyclePresetsConfig)


@dataclass(frozen=True)
class RepositoryRunContext:
    """Resolved target repository with merged configuration."""

    repo_id: str
    display_name: str
    repo_path: Path
    config: AppConfig


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
