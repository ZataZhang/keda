"""进程身份读取的基础设施适配器。

PID 与进程组号会被操作系统复用；需要持久化或延迟处置的 owner 证据还必须包含
进程创建时刻。psutil 无法读取时返回 ``None``，调用方应按身份不可证实处理。
"""

from __future__ import annotations

import psutil


def process_start_time(process_pid: int) -> float | None:
    """读取进程的稳定创建时刻；进程不存在或权限不足时返回 ``None``。

    Args:
        process_pid: 操作系统进程号。

    Returns:
        psutil 提供的 Unix 创建时刻；无法读取时返回 ``None``。
    """
    try:
        return float(psutil.Process(process_pid).create_time())
    except psutil.Error:
        return None


__all__ = ["process_start_time"]
