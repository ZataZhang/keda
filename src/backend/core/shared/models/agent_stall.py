"""活跃 attempt 停滞监督的声明式领域模型（Issue #256 / FR-5…FR-7）。

监督器是共享 run-attempt 的一个**低频只读 observer**：它不拥有任务、不排队、
不新建 writer，只在"到点且无实质进展"时读一次现场、给出一个结论，然后要么交回
正常执行，要么把诊断摘要交给**既有** recovery 与既有门禁。

本模块只放纯数据与纯判定：

- :class:`StallSupervisorConfig` —— ``[agent_runner.stall_supervisor]`` 的运行时
  视图，默认关闭。
- :class:`ProgressSnapshot` —— 一次现场采样的签名（进展判定的唯一输入）。
- :class:`SupervisorVerdict` —— 只读诊断结论的闭集。
- :class:`AttemptOwnership` / :class:`StallCancelOutcome` —— 取消前的所有权证据
  与取消结果，fail-closed 的载体。

进展签名的构成刻意**排除 stdout**：持续输出但没有任何交付（阶段推进、调用终态、
新 commit、工作区变化）正是"思考很久却没进展"与"卡住"最难区分的场景，把输出活动
算作进展会让监督器永远看不见停滞，也会让真正在长思考的任务被输出节奏误判。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: 监督结论闭集里"可以动手"的唯一取值（其余一律不写、不杀）。
STALLED_VERDICT = "stalled"


@dataclass(frozen=True)
class StallSupervisorConfig:
    """``[agent_runner.stall_supervisor]`` 的运行时视图。

    Attributes:
        enabled: 总开关。``False`` 时监督器一次都不调用模型，也不终止任何进程，
            执行与恢复行为与本特性之前逐字节一致。
        check_interval_seconds: 巡检周期（秒），决定模型调用频率下限。
        stalled_after_seconds: 无实质进展判定窗口（秒），决定误中断门槛。
        agent: 诊断用 agent 名；``"auto"`` 走生命周期矩阵的 ``supervisor`` 键。
        diagnosis_timeout_seconds: 单次诊断调用的 wall-clock 上限。
        diagnosis_inactivity_timeout_seconds: 单次诊断调用的无输出上限。
    """

    enabled: bool = False
    check_interval_seconds: int = 1800
    stalled_after_seconds: int = 1800
    agent: str = "auto"
    diagnosis_timeout_seconds: int = 600
    diagnosis_inactivity_timeout_seconds: int = 300


class StallVerdictKind(Enum):
    """只读诊断结论的分类（闭集）。

    Attributes:
        PROGRESS: 现场仍在推进，无需任何动作。
        BLOCKED: 需要人类输入 / 凭据 / 远端配合，只能交班，不能自动处置。
        STALLED: 可证实的停滞：允许进入"复核所有权 → 精确取消 → 既有 recovery"。
        UNCERTAIN: 证据不足或自相矛盾。与 BLOCKED 同样交班——猜测性击杀的代价
            是用户的未提交工作，远高于"多等一轮"。
    """

    progress = "progress"
    blocked = "blocked"
    stalled = "stalled"
    uncertain = "uncertain"

    @property
    def may_cancel(self) -> bool:
        """该结论是否允许终止活跃 writer（只有 STALLED 允许）。"""
        return self is StallVerdictKind.stalled

    @classmethod
    def from_text(cls, verdict_text: str) -> StallVerdictKind:
        """把 provider 自报的结论文本归入闭集。

        无法归类的取值一律降级为 :attr:`UNCERTAIN`：让一个拼错的单词获得"可以杀
        进程"的权力，是把只读诊断变成了不可信输入。
        """
        normalized = (verdict_text or "").strip().lower()
        for verdict_kind in cls:
            if verdict_kind.value == normalized:
                return verdict_kind
        return cls.uncertain


@dataclass(frozen=True)
class ProgressSnapshot:
    """一次现场采样：判断"有没有实质进展"的唯一输入。

    Attributes:
        signature: 进展签名（阶段 / 调用终态数 / HEAD sha / 工作区状态的合成摘要）。
            两次采样签名相同即视为无进展。
        observed_at_mono: 采样时刻（单调时钟，不受系统改时影响）。
        detail: 人读摘要，用于日志与 attempt 记录；**不含**提示词、环境变量或凭据。
    """

    signature: str
    observed_at_mono: float
    detail: str = ""

    def has_progress_since(self, earlier: ProgressSnapshot) -> bool:
        """相对更早那次采样是否出现了实质进展。"""
        return self.signature != earlier.signature


@dataclass(frozen=True)
class SupervisorVerdict:
    """只读监督诊断的结构化结论。

    Attributes:
        kind: 分类结果。
        summary: 诊断摘要（交给 recovery 提示词的那段文字，也是审计记录的内容）。
        evidence: 支撑该结论的事实条目（如"HEAD 未变、无新调用终态、工作区哈希
            冻结 42 分钟"），逐条脱敏。
    """

    kind: StallVerdictKind
    summary: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def may_cancel(self) -> bool:
        """结论本身是否允许取消（仍须通过所有权复核）。"""
        return self.kind.may_cancel


@dataclass(frozen=True)
class AttemptOwnership:
    """取消前必须"当场再读一次"的所有权证据。

    Attributes:
        confirmed: 全部判据一致时为 ``True``；任何一项不成立都必须交班。
        reason: 未确认时的原因（人读、进 attempt 记录）。
        host_label: 认领标记里的主机标识（跨主机认领一律不处置）。
        process_pid: 认领标记里的 runner 进程 pid。
        child_process_group: 实时读到的 writer 进程组 id；``None`` 表示已不在册。
        process_started_at: 操作系统记录的进程创建时刻；PID/PGID 被复用时用于区分新进程。
    """

    confirmed: bool
    reason: str = ""
    host_label: str = ""
    process_pid: int = 0
    child_process_group: int | None = None
    process_started_at: float | None = None


@dataclass(frozen=True)
class StallCancelOutcome:
    """精确取消的结果。

    Attributes:
        cancelled: 目标进程组是否由本次操作终止（陈旧记录 / 他人进程恒为 ``False``）。
        exited: 终止后是否确认回收（等待 reap 的结果）。
        process_group: 实际操作过的进程组 id；``None`` 表示没有触碰任何进程。
        reason: 失败或拒绝执行的原因（进审计记录）。
    """

    cancelled: bool = False
    exited: bool = False
    process_group: int | None = None
    reason: str = ""


class AgentStallCancelledError(RuntimeError):
    """writer 因可证实的停滞被监督器精确取消。

    刻意继承 :class:`RuntimeError`：共享执行循环的失败分支已经捕获它，于是取消后
    的诊断摘要**原样**流经既有的 ``classify_failure`` → recovery 提示词 → 验证 /
    review / 发布门禁。新增的是"这一轮为什么失败"的信息量，而不是第二条恢复路径。

    Attributes:
        diagnosis_summary: 交给 recovery 的停滞诊断摘要。
        verdict_kind: 触发取消的结论种类（审计用）。
    """

    def __init__(self, diagnosis_summary: str, *, verdict_kind: str = STALLED_VERDICT) -> None:
        """用诊断摘要构造异常，并记住结论种类。"""
        super().__init__(diagnosis_summary)
        self.diagnosis_summary = diagnosis_summary
        self.verdict_kind = verdict_kind


__all__ = [
    "STALLED_VERDICT",
    "AgentStallCancelledError",
    "AttemptOwnership",
    "ProgressSnapshot",
    "StallCancelOutcome",
    "StallSupervisorConfig",
    "StallVerdictKind",
    "SupervisorVerdict",
]
