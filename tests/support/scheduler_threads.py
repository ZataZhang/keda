"""调度器线程归属判定：lifespan 现在同时托管 dashboard 与 Backlog 两个循环，
按"总共有几个循环线程"断言会让两个用例互相串扰，因此按调度器实例归属计数。
"""

from __future__ import annotations

import threading

from backend.core.use_cases.monitor_snapshots import MonitorSyncScheduler


def scheduler_thread_ids(scheduler: MonitorSyncScheduler | None = None) -> set[int]:
    """返回存活的调度循环线程 ident。

    Args:
        scheduler: 指定时只统计属于该调度器实例的线程；省略时统计全部调度循环。

    Returns:
        set[int]: 命中线程的 ``ident`` 集合。
    """
    matched_ids: set[int] = set()
    for thread in threading.enumerate():
        owner = getattr(thread._target, "__self__", None)  # noqa: SLF001 - 归属判定
        if owner is not None and not isinstance(owner, MonitorSyncScheduler):
            continue
        if owner is None:
            continue
        if scheduler is not None and owner is not scheduler:
            continue
        if thread.ident is not None:
            matched_ids.add(thread.ident)
    return matched_ids
