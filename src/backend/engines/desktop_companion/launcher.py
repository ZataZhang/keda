"""桌面 companion 的系统窗口实现（pywebview 运行时）。

进程与窗口边界全部收在本子树：

- Console 进程内的 :class:`PywebviewCompanionLauncher` 只做「有没有窗口、
  要不要再起一个、聚焦/关闭工单」，**自己不创建窗口**；
- 真正的 NSApplication / WebView 循环跑在 helper 子进程的**主线程**上
  （macOS 的 Cocoa 要求 GUI 在主线程，而 Console 的主线程属于 uvicorn），
  因此 :meth:`open_window` 以固定 argv 起 ``python -m
  backend.engines.desktop_companion.helper --origin <已校验 origin>``；
- helper 反过来只读当前 loopback Console 的状态与工单接口，窗口内容仍是
  Console 自己的精简 route。

pywebview 随 KedaCode 默认安装；若运行环境仍缺失，本模块仍可 import，
:meth:`snapshot` 如实返回 ``not_installed``，Web Dashboard 继续可用。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from importlib.util import find_spec
from pathlib import Path

from backend.core.shared.interfaces.desktop_companion import (
    DesktopCompanionLauncher,
    DesktopCompanionSnapshot,
    DesktopCompanionStatus,
)
from backend.core.shared.models.product_identity import state_home

#: companion 进程身份文件（pid + 命令行校验 + 归属 Console 会话），随状态目录走。
PIDFILE_NAME = "desktop-companion.pid"

#: helper 子进程的模块入口，固定 argv 的第二段，绝不接受调用方替换。
HELPER_MODULE = "backend.engines.desktop_companion.helper"

#: 判断"这个 pid 是不是我们的 helper"的命令行标记。
_HELPER_ARGV_MARKER = f"-m {HELPER_MODULE}"


def _pid_is_alive(pid: int) -> bool:
    """探测本机进程是否仍存在；权限不足时按「存在」处理，避免误起第二个窗口。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def desktop_runtime_available() -> bool:
    """当前解释器是否能 import pywebview（不真正 import，避免拉起 GUI 依赖）。"""
    return find_spec("webview") is not None


def platform_supported() -> bool:
    """当前平台是否存在可置顶系统窗口的常规路径。"""
    return sys.platform in {"darwin", "win32", "linux"}


def _read_pidfile_entry(pidfile_path: Path) -> tuple[int, str] | None:
    """读取 pidfile 里的 ``(pid, 所属 Console 会话)``。

    文件缺失、损坏或命令行标记不匹配（pid 被复用成别的进程）时返回 ``None``。
    会话行为是可选的：旧版本只写两行，此时按「归属未知」处理。

    Args:
        pidfile_path: companion 身份文件路径。

    Returns:
        ``tuple[int, str]``：pid 与写入该 pid 的 Console 会话身份（未知时为空串）。
    """
    try:
        raw = pidfile_path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = raw.split("\n")
    pid_value = lines[0].strip() if lines else ""
    argv_marker = lines[1].strip() if len(lines) > 1 else ""
    owner_session = lines[2].strip() if len(lines) > 2 else ""
    if argv_marker != _HELPER_ARGV_MARKER:
        # pid 被复用成别的进程：宁认可疑残留，也不把别人的进程当自己的窗口。
        return None
    try:
        pid = int(pid_value)
    except ValueError:
        return None
    return (pid, owner_session)


def _read_pidfile(pidfile_path: Path) -> int | None:
    """读取 pidfile 里的 companion pid；不判定归属，只关心「这个进程还在不在」。"""
    entry = _read_pidfile_entry(pidfile_path)
    if entry is None:
        return None
    pid, _owner_session = entry
    return pid if _pid_is_alive(pid) else None


def _write_pidfile(pidfile_path: Path, pid: int, console_session: str) -> None:
    """写入 companion pid、命令行标记与归属会话；写失败不阻断窗口开启。

    归属会话让另一个 Console 进程认不出这是自己的窗口，从而不会把别人的窗口
    报成「已聚焦」，也不会替别人关窗。
    """
    try:
        pidfile_path.parent.mkdir(parents=True, exist_ok=True)
        pidfile_path.write_text(
            f"{pid}\n{_HELPER_ARGV_MARKER}\n{console_session}\n",
            encoding="utf-8",
        )
    except OSError:
        return


class PywebviewCompanionLauncher(DesktopCompanionLauncher):
    """以固定子进程承载 pywebview 窗口的 companion launcher。

    Args:
        pidfile_path: companion 进程身份文件路径；缺省落在产品状态目录下。
        console_session: 本 Console 进程的身份串，helper 据此识别孤儿窗口。
    """

    def __init__(self, *, pidfile_path: Path | None = None, console_session: str = "") -> None:
        self._pidfile_path = pidfile_path or (state_home() / PIDFILE_NAME)
        self._console_session = console_session
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._focus_ticket = 0
        self._close_ticket = 0
        self._last_error: str | None = None

    # ── 端口实现 ──────────────────────────────────────────────────────────

    def snapshot(self, *, console_origin: str) -> DesktopCompanionSnapshot:
        """返回 companion 当前状态；不产生任何进程或窗口副作用。"""
        with self._lock:
            return self._snapshot_locked(console_origin=console_origin)

    def open_window(self, *, console_origin: str) -> DesktopCompanionSnapshot:
        """开启 companion 窗口；已存在时只递增 focus ticket 让 helper 自己提前台。

        重复点击不新起进程，是 PRD「不无限新建窗口」的直接实现：ticket 变化由
        helper 轮询到，原生窗口的置顶与聚焦由其主线程完成。
        """
        with self._lock:
            if not platform_supported():
                return self._snapshot_locked(
                    console_origin=console_origin,
                    forced_status=DesktopCompanionStatus.UNSUPPORTED_PLATFORM,
                )
            if not desktop_runtime_available():
                return self._snapshot_locked(
                    console_origin=console_origin,
                    forced_status=DesktopCompanionStatus.NOT_INSTALLED,
                )
            if self._running_pid() is not None:
                self._focus_ticket += 1
                return self._snapshot_locked(
                    console_origin=console_origin,
                    forced_status=DesktopCompanionStatus.FOCUSED,
                )

            try:
                self._process = subprocess.Popen(  # noqa: S603 - argv 固定，无 shell、无外部输入
                    [sys.executable, "-m", HELPER_MODULE, "--origin", console_origin],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                self._last_error = f"companion 进程启动失败：{exc}"
                self._process = None
                return self._snapshot_locked(
                    console_origin=console_origin,
                    forced_status=DesktopCompanionStatus.FAILED,
                )
            _write_pidfile(self._pidfile_path, self._process.pid, self._console_session)
            self._focus_ticket += 1
            return self._snapshot_locked(
                console_origin=console_origin,
                forced_status=DesktopCompanionStatus.LAUNCHED,
            )

    def close_window(self, *, wait_seconds: float = 1.5) -> DesktopCompanionSnapshot:
        """递增 close ticket 请 helper 自行关窗退出；不做 kill，避免留下半个窗口。

        有界等待 helper 收尾，是为了让「关闭」这个用户动作立刻得到诚实的状态，
        而不是让前端读到窗口仍在运行的旧快照；超时不报错，因为关窗本来就可能
        被原生循环拖延。

        等待段必须在锁外：状态探针（网页看板与 helper 自身）也要拿这把锁，
        在锁内 sleep 会把它们一起堵住最长 ``wait_seconds``。
        """
        with self._lock:
            self._close_ticket += 1
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while self._running_pid() is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        with self._lock:
            self._prune_finished_process()
            return self._snapshot_locked(console_origin=None)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _prune_finished_process(self) -> None:
        """回收已退出的子进程句柄；正常退出（码 0）不算失败，崩溃才记录原因。"""
        if self._process is None or self._process.poll() is None:
            return
        exit_code = self._process.returncode
        self._process = None
        if exit_code:
            self._last_error = f"companion 进程异常退出（退出码 {exit_code}）。"
        else:
            self._last_error = None

    def _running_pid(self) -> int | None:
        """返回**本 Console 拥有**且仍在运行的 companion pid，否则 ``None``。"""
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        self._prune_finished_process()
        entry = _read_pidfile_entry(self._pidfile_path)
        if entry is None:
            return None
        pid, owner_session = entry
        if not _pid_is_alive(pid):
            return None
        if owner_session != self._console_session:
            # pidfile 归别的 Console 进程：它的 focus / close 工单写在对方那份
            # launcher 里，本进程递增永远送不到窗口，所以既不能声称「已聚焦」，
            # 也不能替别人关窗——各入口各管自己的小窗。
            return None
        return pid

    def _snapshot_locked(
        self,
        *,
        console_origin: str | None,
        forced_status: DesktopCompanionStatus | None = None,
    ) -> DesktopCompanionSnapshot:
        """在持锁状态下拼出快照；``forced_status`` 用于平台/依赖这类前置判定。"""
        running = self._running_pid() is not None
        if forced_status is not None:
            status = forced_status
        elif running:
            status = DesktopCompanionStatus.RUNNING
        elif not platform_supported():
            status = DesktopCompanionStatus.UNSUPPORTED_PLATFORM
        elif not desktop_runtime_available():
            status = DesktopCompanionStatus.NOT_INSTALLED
        elif self._last_error:
            status = DesktopCompanionStatus.FAILED
        else:
            status = DesktopCompanionStatus.IDLE
        return DesktopCompanionSnapshot(
            status=status,
            console_origin=console_origin,
            console_session=self._console_session,
            focus_ticket=self._focus_ticket,
            close_ticket=self._close_ticket,
            detail=self._last_error,
        )


#: Console 进程内唯一的 launcher 实例：ticket 必须跨请求保持，否则重复点击会
#: 各起一个窗口。
_shared_launcher: PywebviewCompanionLauncher | None = None
_shared_lock = threading.Lock()


def create_desktop_companion_launcher(*, console_session: str) -> DesktopCompanionLauncher:
    """返回当前 Console 进程共享的 companion launcher。

    Args:
        console_session: 本 Console 进程的身份串（首次调用时固定，后续调用
            沿用同一个实例，ticket 计数才不会重置）。

    Returns:
        DesktopCompanionLauncher: 进程内共享实例。
    """
    global _shared_launcher  # noqa: PLW0603 - 进程内单例就是本函数的产物
    with _shared_lock:
        if _shared_launcher is None:
            _shared_launcher = PywebviewCompanionLauncher(console_session=console_session)
        return _shared_launcher


def wait_for_helper_exit(timeout_seconds: float = 2.0) -> bool:
    """有界等待 helper 退出，用于「Console 关掉后小窗进入断连」的可观察性。

    Args:
        timeout_seconds: 最长等待秒数。

    Returns:
        窗口进程在超时前退出时为 ``True``。
    """
    deadline = time.monotonic() + timeout_seconds
    pid = _read_pidfile(state_home() / PIDFILE_NAME)
    if pid is None:
        return True
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_is_alive(pid)


__all__ = [
    "HELPER_MODULE",
    "PIDFILE_NAME",
    "PywebviewCompanionLauncher",
    "create_desktop_companion_launcher",
    "desktop_runtime_available",
    "platform_supported",
    "wait_for_helper_exit",
]
