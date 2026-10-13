"""可选桌面 companion 的 core 端口与状态语义。

Console 面板本身跑在浏览器里，离开浏览器后没有能留在其他桌面应用上方的入口。
本端口只描述「开启 / 聚焦 / 关闭一个紧凑系统窗口」这件事，**不描述窗口里显示
什么**：窗口内容仍是 Console 自己的精简 route，走同一批本机只读 API。

架构约束（PRD §5、§9 Architecture Acceptance）：core 只定义端口，不
``subprocess``、不 import engines；进程与窗口生命周期由
:mod:`backend.engines.desktop_companion` 实现，api 层只通过本端口调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class DesktopCompanionStatus(str, Enum):
    """桌面 companion 的可观察状态。

    每个状态都要能被前端渲染成一句人话，不允许「静默失败」：``NOT_INSTALLED``
    与 ``FAILED`` 分开，因为前者提示安装、后者提示重试；``IDLE`` 与
    ``RUNNING`` 分开，因为前者是「可以开」，后者是「已经在屏幕上」。
    """

    #: 本次调用新建了 companion 进程。
    LAUNCHED = "launched"
    #: 窗口已存在，本次调用只把它带到前台。
    FOCUSED = "focused"
    #: 窗口正在运行，本次调用没有产生动作（纯状态查询）。
    RUNNING = "running"
    #: 没有窗口，但桌面运行时可用、当前平台支持，随时可开。
    IDLE = "idle"
    #: 桌面运行时未安装：Web 看板继续可用，页面给出安装指引。
    NOT_INSTALLED = "not_installed"
    #: 当前平台不支持系统窗口。
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    #: 窗口创建或 companion 进程启动失败（GUI 会话缺失、依赖抛错等）。
    FAILED = "failed"


#: 状态到人读说明的唯一映射表，前端与 CLI 共用，避免两处各写一份文案。
STATUS_HINTS: dict[DesktopCompanionStatus, str] = {
    DesktopCompanionStatus.LAUNCHED: "已开启桌面小窗。",
    DesktopCompanionStatus.FOCUSED: "桌面小窗已在运行，已请求带到前台。",
    DesktopCompanionStatus.RUNNING: "桌面小窗正在运行。",
    DesktopCompanionStatus.IDLE: "桌面小窗未开启。",
    DesktopCompanionStatus.NOT_INSTALLED: (
        "桌面运行时未安装：安装可选依赖 `kedacode[desktop]` 后可开启小窗，网页看板不受影响。"
    ),
    DesktopCompanionStatus.UNSUPPORTED_PLATFORM: "当前操作系统不支持系统窗口，网页看板不受影响。",
    DesktopCompanionStatus.FAILED: "桌面小窗启动失败，网页看板不受影响；可稍后重试。",
}


@dataclass(frozen=True)
class DesktopCompanionSnapshot:
    """一次 companion 状态查询的结果。

    Attributes:
        status: 当前状态分桶。
        console_origin: 窗口固定加载的 Console origin；不可用时为 ``None``。
        console_session: 本 Console 进程的身份串。helper 用它识别「上一个
            Console 留下的孤儿窗口」，Console 重启后旧窗口自行退出。
        focus_ticket: 聚焦计数。helper 观测到该值变大就把自己带到前台，
            因此「重复点击开启」不会开出第二个窗口。
        close_ticket: 关闭计数。变化时 helper 关闭自己的窗口并退出。
        detail: 状态的人读补充（错误摘要、退出码）；无则为 ``None``。
    """

    status: DesktopCompanionStatus
    console_origin: str | None = None
    console_session: str = ""
    focus_ticket: int = 0
    close_ticket: int = 0
    detail: str | None = None

    @property
    def hint(self) -> str:
        """返回该状态默认的人读说明。"""
        return STATUS_HINTS[self.status]

    @property
    def window_exists(self) -> bool:
        """当前是否已有一个由本 Console 持有的 companion 窗口。"""
        return self.status in {
            DesktopCompanionStatus.LAUNCHED,
            DesktopCompanionStatus.FOCUSED,
            DesktopCompanionStatus.RUNNING,
        }


class DesktopCompanionLauncher(ABC):
    """可选系统窗口的启动 / 聚焦 / 关闭端口。

    实现方必须保证：

    - 只接受**已通过同源校验**的 ``console_origin``，不自行拼接、不接受其他地址；
    - 同一个 Console 最多持有一个窗口；重复 :meth:`open_window` 聚焦而非新建；
    - 窗口固定加载 ``{console_origin}/app/companion/``，不加载调用方给的
      路径、查询串或任意 URL；
    - 桌面运行时缺失时 :meth:`snapshot` 返回
      :attr:`DesktopCompanionStatus.NOT_INSTALLED`，不抛出、不阻断 Web 看板。
    """

    @abstractmethod
    def snapshot(self, *, console_origin: str) -> DesktopCompanionSnapshot:
        """返回当前 companion 状态，不启动任何进程。

        Args:
            console_origin: 已通过同源校验的 Console origin。

        Returns:
            DesktopCompanionSnapshot: 当前状态。
        """

    @abstractmethod
    def open_window(self, *, console_origin: str) -> DesktopCompanionSnapshot:
        """开启 companion 窗口；已存在时聚焦并递增 focus ticket。

        Args:
            console_origin: 已通过同源校验的 Console origin。

        Returns:
            DesktopCompanionSnapshot: 本次动作后的状态。
        """

    @abstractmethod
    def close_window(self) -> DesktopCompanionSnapshot:
        """关闭 companion 窗口并递增 close ticket；无窗口时为 :attr:`IDLE`。

        Returns:
            DesktopCompanionSnapshot: 本次动作后的状态。
        """


__all__ = [
    "STATUS_HINTS",
    "DesktopCompanionLauncher",
    "DesktopCompanionSnapshot",
    "DesktopCompanionStatus",
]
