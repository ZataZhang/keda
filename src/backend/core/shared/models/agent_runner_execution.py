"""Agent Runner 的执行与尝试数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from backend.core.shared.interfaces.agent_output_protocol import PLAIN_PROTOCOL_ID

if TYPE_CHECKING:
    # `ValidationVerdict` lives in the use-cases layer; import it only for
    # type checking to avoid a circular runtime import. Annotations are strings.
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
    """Agent 执行收敛结果。

    Attributes:
        verification_results: 执行过的仓库验证结果。
        attempt_results: 本轮各次执行尝试的结果。
        verifier_verdict: 独立 verifier 的结论；未运行时为 ``None``。
        issue_closed_without_changes: Issue 在本轮执行期间关闭且没有变更需要发布时为 ``True``。
    """

    verification_results: list[CommandResult]
    attempt_results: list[AttemptResult]
    verifier_verdict: "ValidationVerdict | None" = None
    issue_closed_without_changes: bool = False


@dataclass(frozen=True)
class TokenUsage:
    """单次 agent 子进程调用的官方 token 用量。

    数据全部来自 agent 输出自报的 usage 统计（如结构化流中的
    ``assistant.message.usage`` 或 ``result.usage``），系统只解析、不估算；拿不到时
    上层表达为 ``None``。
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
            ``kedacode.agent_output_protocols`` 注册表）。``"plain"`` 表示通用
            文本中继；agent 响应文本提取等消费方以此判断 stdout 是否为
            渲染后的结构化流，而不再嗅探命令行里的 agent 名。
        token_usage: 输出自报的 token 用量；协议未提供或解析不出时为
            ``None``（≠ 0，表示"无数据"而非"零消耗"）。
        session_id: agent CLI 自报的会话 id（不同 CLI 可能来自 JSONL 事件或退出提示），
            供崩溃对账后的会话续传
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
        failure_phase: 失败发生的生命周期阶段；成功或旧记录可为空。
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
    failure_phase: str | None = None
