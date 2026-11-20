"""平台能力判定：依赖 POSIX 原语的功能入口在启动前显式拒绝不支持的宿主。

支持的 Windows 范围是「CLI 基本面 + ``kc console`` + 托盘 App」；daemon、
preview 与容器模式依赖进程组（``os.killpg`` / ``setsid``）或 POSIX uid/gid 映射，
在 Windows 上没有等价实现。与其让调用落到 ``AttributeError: module 'os' has no
attribute 'killpg'`` 或留下半启动的进程，不如在入口处失败并说明替代路径。

:func:`is_process_alive` 是跨平台的进程探活，取代各处 POSIX 专用的 ``os.kill(pid, 0)``。

本模块放在 core，是因为四个层都可能需要这道判定：core 用例（daemon / container）、
engines（takeover）、infrastructure（preview）都允许依赖 ``core.shared.models``。
"""

from __future__ import annotations

import os

import psutil


class UnsupportedPlatformFeatureError(RuntimeError):
    """当前宿主缺少该功能依赖的原语（目前只有 Windows 触发）。"""


def supports_posix_process_groups() -> bool:
    """判断当前宿主是否具备 POSIX 进程组原语。

    Returns:
        Windows（``os.name == "nt"``）上为 ``False``；同时检查 ``killpg`` 与
        ``getsid``，避免在裁剪过的 POSIX 运行时上误判。
    """
    return os.name != "nt" and hasattr(os, "killpg") and hasattr(os, "getsid")


def require_posix_process_groups(feature: str) -> None:
    """在依赖进程组的功能入口处快速失败。

    Args:
        feature: 面向用户的功能名称，如 ``"kc daemon"`` 或 ``"kc preview"``。

    Raises:
        UnsupportedPlatformFeatureError: 当前宿主不支持 POSIX 进程组。
    """
    if supports_posix_process_groups():
        return
    raise UnsupportedPlatformFeatureError(
        f"{feature} 依赖 POSIX 进程组（os.killpg / setsid），Windows 原生不支持；"
        "请在 Linux 或 macOS 上运行。一次性执行可用 `kc run`，它不依赖进程组。"
    )


def is_process_alive(process_id: int) -> bool:
    """跨平台判断本机进程号是否存活（锁 / 认领 / 在线状态的 owner 探活）。

    POSIX 习惯的 ``os.kill(pid, 0)`` 在 Windows 上不是探活：信号 ``0`` 恰好是
    ``CTRL_C_EVENT``，会走控制台 Ctrl+C 事件，对不存在的进程抛的是
    ``OSError(winerror=87)`` 而不是 ``ProcessLookupError``，于是死进程被当成存活，
    崩溃留下的认领永远回收不了。``psutil.pid_exists`` 在 POSIX 上内部同样用
    ``kill(pid, 0)`` 并把无权限（EPERM）视为存活，在 Windows 上用 ``OpenProcess``。

    Args:
        process_id: 本机进程号。

    Returns:
        进程存在（含属于其他用户的进程）为 ``True``；``process_id <= 0`` 或进程
        不存在为 ``False``。
    """
    if process_id <= 0:
        return False
    return bool(psutil.pid_exists(process_id))


__all__ = [
    "UnsupportedPlatformFeatureError",
    "is_process_alive",
    "require_posix_process_groups",
    "supports_posix_process_groups",
]
