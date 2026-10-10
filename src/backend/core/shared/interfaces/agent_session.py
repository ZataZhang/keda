"""原生执行器会话与按需项目预览的抽象端口（ports）。

Issue #256 把「本机 TTY 里直接跑 provider 原生界面」和「对话中按需起本地
dev server」两条能力接到 KC 上。两者都要真正落地进程，而 ``core`` 层禁止
导入 ``infrastructure`` / ``engines``，因此这里只声明能力契约：

- :class:`IForegroundSessionLauncher`：把一次原生会话计划以**继承当前终端**的
  方式跑起来，交回退出码。语义与普通 ``IProcessRunner.run`` 根本不同——后者
  捕获输出、可设超时；原生 TUI 必须独占真实终端，不能被管道接管。
- :class:`IPreviewProcessManager`：以受管进程组启动预览命令、读回其状态、
  并只停止由自己登记且身份可证实的那个进程组。

按四层依赖方向，argv 组装与 skill/bootstrap 判定在 ``engines``，真正的
``Popen`` / 进程组 / 注册表落盘在 ``infrastructure``；core 用例只依赖本模块。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.models.agent_session import (
    NativeSessionPlan,
    NativeSessionResult,
    PreviewProcessRecord,
)


@dataclass(frozen=True)
class PreviewStartRequest:
    """一次 ``kc preview start`` 的受控启动输入。

    Attributes:
        argv: 已批准（配置声明或用户确认）的精确 argv，不是 shell 文本。
        cwd: 目标仓库根，预览进程的工作目录，也是注册表的身份键。
        ready_url: 期望的 loopback 地址；``None`` 表示只信任进程自报。
        ready_timeout_seconds: 等待自报地址就绪的上限。
    """

    argv: tuple[str, ...]
    cwd: Path
    ready_url: str | None
    ready_timeout_seconds: int


@dataclass(frozen=True)
class PreviewStartOutcome:
    """预览启动结果（失败也带事实，不靠异常表达可预期拒绝）。

    Attributes:
        started: 是否成功启动并保持存活到就绪判定结束。
        record: 启动后的进程记录；``started`` 为假时可能仍非空（已启动但未就绪
            且已被回收时为空）。
        message: 面向终端的一行说明（未就绪、地址非回环、端口已被占用等）。
        timed_out: 是否在 ready 上限内始终没有报告可用地址。
    """

    started: bool
    record: PreviewProcessRecord | None
    message: str
    timed_out: bool = False


@dataclass(frozen=True)
class PreviewStatusOutcome:
    """``kc preview status`` 的读取结果。

    Attributes:
        state: :class:`~backend.core.shared.models.agent_session.PreviewState` 取值。
        record: 注册表里的记录（可能为陈旧记录）；无记录时 ``None``。
        message: 状态说明，含未就绪原因或所有权不可证实的原因。
        url: 已验证的 loopback 地址；未就绪时 ``None``。
    """

    state: str
    record: PreviewProcessRecord | None
    message: str
    url: str | None = None


@dataclass(frozen=True)
class PreviewStopOutcome:
    """``kc preview stop`` 的执行结果。

    Attributes:
        stopped: 是否确认目标进程组已退出。
        killed: 是否由本次调用发出终止信号（陈旧记录为假）。
        record: 被操作（或被拒绝操作）的记录。
        message: 结果说明；拒绝停止时指名是哪一项身份判据不匹配。
    """

    stopped: bool
    killed: bool
    record: PreviewProcessRecord | None
    message: str


class IForegroundSessionLauncher(ABC):
    """在当前终端以前台方式启动一次原生执行器会话的端口。

    与 ``IProcessRunner`` 的区别是本端口**不捕获输出**：provider 直接继承
    KC 的 stdin/stdout/stderr，因此它拿到真实 TTY、能画全屏界面、能收到
    Ctrl-C。实现端负责信号策略（父进程等待期间对 SIGINT/SIGQUIT 的处理）与
    退出码归一，用例层只关心「退出码是多少」。
    """

    @abstractmethod
    def launch(self, session_plan: NativeSessionPlan) -> NativeSessionResult:
        """启动一次原生交互会话并阻塞到 provider 退出。

        Args:
            session_plan: 由声明式 interactive profile 组装的启动计划。

        Returns:
            :class:`NativeSessionResult`：退出码原样反映 provider 状态；
            provider 被信号杀死时按 shell 约定折算为 ``128 + signal``。

        Raises:
            RuntimeError: 可执行文件不存在或无法启动（``errno`` 信息在消息里），
                用例层据此给出可行动错误而不是静默回退。
        """
        ...


class IPreviewProcessManager(ABC):
    """KC 持有的项目预览进程管理端口。

    三条铁律由实现端保证，用例层因此不必重复设防：

    1. **只启动精确 argv**：不接受 shell 字符串，不经过 shell 解析。
    2. **只报告回环地址**：ready 地址主机不在回环闭集内视为未就绪并终止进程组。
    3. **只停自己登记的组**：stop 前用记录里的 pid / 进程组 id 与实时读取结果
       交叉验证，任一不符就拒绝，绝不对陌生 pid 发信号。
    """

    @abstractmethod
    def start_preview(self, request: PreviewStartRequest) -> PreviewStartOutcome:
        """按已批准的 argv 启动预览进程组并等待其报告回环地址。

        Args:
            request: 受控启动输入。

        Returns:
            :class:`PreviewStartOutcome`：未就绪 / 地址不合规 / 已有存活预览都
            以结果对象返回，不抛异常。
        """
        ...

    @abstractmethod
    def inspect_preview(self, *, repo_root: Path) -> PreviewStatusOutcome:
        """读取当前预览状态，不启动也不终止任何进程。

        Args:
            repo_root: 目标仓库根（注册表键的判定上下文）。

        Returns:
            :class:`PreviewStatusOutcome`：无记录、启动中、就绪、已退出、
            所有权不可证实五种取值之一。
        """
        ...

    @abstractmethod
    def stop_preview(self, *, repo_root: Path) -> PreviewStopOutcome:
        """停止 KC 登记且身份可证实的那个预览进程组。

        Args:
            repo_root: 目标仓库根。

        Returns:
            :class:`PreviewStopOutcome`：身份判据不匹配时 ``stopped`` 为假并
            说明原因；记录存在但进程已消失时按陈旧记录清理而不发信号；无记录时
            ``stopped`` 为真（stop 是幂等的收尾动作，不是错误）。
        """
        ...


__all__ = [
    "IForegroundSessionLauncher",
    "IPreviewProcessManager",
    "PreviewStartOutcome",
    "PreviewStartRequest",
    "PreviewStatusOutcome",
    "PreviewStopOutcome",
]
