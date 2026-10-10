"""Backlog 列表后台同步的 api 层接线与生命周期边界。

镜像 :mod:`backend.api.monitor_sync` 的纪律：调度器与协调器都是进程级单例，
**只能**由 FastAPI 应用生命周期（``app.py`` 的 lifespan）启停；本模块 import
时不建线程、不做 I/O。调度循环复用 dashboard 已验证的
``MonitorSyncScheduler`` / ``MonitorSyncCoordinator``（两者通过注入解耦），
只换一组 provider 与 scan_runner，不新写第二套调度器。

读路径入口是 :func:`ensure_fresh_backlog_snapshot`：它只读本地快照并做 stale
判定，过期时在协调器里登记一次非阻塞重扫请求就立即返回——请求线程绝不等待
扫描本身。全局同步的开关与间隔复用 dashboard 既有设置，本模块只消费不推断。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import cast

from backend.api.monitor_sync import _read_monitor_settings
from backend.api.routes.agent_runner import _list_enabled_repo_ids
from backend.core.shared.interfaces.runner_console import IBacklogSnapshotStore
from backend.core.use_cases.agent_runner_factory import create_console_store
from backend.core.use_cases.backlog_snapshots import (
    BacklogSnapshotView,
    build_backlog_task_key,
    list_missing_pending_repo_ids,
    parse_backlog_task_key,
    persist_backlog_snapshot,
    read_backlog_snapshot,
)
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncCoordinator,
    MonitorSyncScheduler,
)

_logger = logging.getLogger(__name__)

_BACKLOG_COORDINATOR: MonitorSyncCoordinator | None = None
_BACKLOG_COORDINATOR_LOCK = threading.Lock()
_BACKLOG_SCHEDULER: MonitorSyncScheduler | None = None
_BACKLOG_SCHEDULER_LOCK = threading.Lock()


def _scan_backlog_variant_and_persist(backlog_task_key: str) -> None:
    """构建一个视图变体的列表响应并写回快照（只在后台线程执行）。

    Args:
        backlog_task_key: ``(repo_id, include_archived)`` 编码后的协调器任务键。

    Raises:
        Exception: 扫描或写库失败时原样上抛，由协调器记进任务状态；旧快照不动。
    """
    # 延迟 import：本模块被 app.py 先于 routes 加载，而路由模块要反过来调用
    # ensure_fresh_backlog_snapshot / request_backlog_resync。顶层 import 会形成
    # 循环，使路由模块拿到一个尚未定义完的 backlog_sync。
    from backend.api.routes.agent_runner_backlog import _build_backlog_response

    repo_id, include_archived = parse_backlog_task_key(backlog_task_key)
    built_payload = _build_backlog_response(repo_id, include_archived)
    persist_backlog_snapshot(
        get_backlog_snapshot_store(),
        repo_id=repo_id,
        include_archived=include_archived,
        payload=built_payload,
    )


def get_backlog_snapshot_store() -> IBacklogSnapshotStore:
    """Return the console SQLite store backing Backlog list snapshots."""
    return cast(IBacklogSnapshotStore, create_console_store())


def get_backlog_sync_coordinator() -> MonitorSyncCoordinator:
    """Return the process-wide Backlog scan coordinator (created on first use).

    Creating the coordinator starts no thread and performs no I/O; the periodic
    loop itself is owned by the FastAPI application lifespan.
    """
    global _BACKLOG_COORDINATOR
    if _BACKLOG_COORDINATOR is not None:
        return _BACKLOG_COORDINATOR
    with _BACKLOG_COORDINATOR_LOCK:
        if _BACKLOG_COORDINATOR is None:
            _BACKLOG_COORDINATOR = MonitorSyncCoordinator(
                scan_runner=_scan_backlog_variant_and_persist
            )
        return _BACKLOG_COORDINATOR


def ensure_fresh_backlog_snapshot(
    repo_id: str,
    *,
    include_archived: bool,
    now_reference: datetime | None = None,
) -> BacklogSnapshotView:
    """读本地快照并在缺失/过期时申请一次后台重扫，绝不等待扫描结果。

    Args:
        repo_id: 目标仓库 ID。
        include_archived: 视图变体。
        now_reference: 注入的当前时间，用于 stale 判定。

    Returns:
        BacklogSnapshotView: 快照视图（无可用快照时为空态骨架），带如实的
            ``stale`` 标记。
    """
    snapshot_view = read_backlog_snapshot(
        get_backlog_snapshot_store(),
        repo_id=repo_id,
        include_archived=include_archived,
        now_reference=now_reference,
    )
    if snapshot_view.stale:
        # 协调器按任务键去重：同一变体在途扫描不会叠加，慢仓库也不会被轮询打爆。
        get_backlog_sync_coordinator().request_sync(
            build_backlog_task_key(repo_id, include_archived=include_archived)
        )
    return snapshot_view


def request_backlog_resync(repo_id: str, *, include_archived: bool = False) -> None:
    """写操作（入队 / 开始 / 全局开始）成功后立即为对应仓库申请一次后台重扫。

    替换原先"弹掉内存缓存"的失效方式：缓存被删掉后下一次读仍要等一次全量扫描，
    而这里直接把重建动作派到后台，读取方拿到的是上一份快照 + 过期标记。

    Args:
        repo_id: 目标仓库 ID。
        include_archived: 需要重建的视图变体。
    """
    get_backlog_sync_coordinator().request_sync(
        build_backlog_task_key(repo_id, include_archived=include_archived)
    )


def _list_missing_backlog_snapshot_repo_ids() -> list[str]:
    """Return enabled repositories whose default-view snapshot is missing."""
    return list(
        list_missing_pending_repo_ids(
            get_backlog_snapshot_store(),
            enabled_repo_ids=_list_enabled_repo_ids(),
        )
    )


def get_backlog_scheduler() -> MonitorSyncScheduler:
    """Return the process-wide Backlog scheduler instance (created on first use)."""
    global _BACKLOG_SCHEDULER
    if _BACKLOG_SCHEDULER is not None:
        return _BACKLOG_SCHEDULER
    with _BACKLOG_SCHEDULER_LOCK:
        if _BACKLOG_SCHEDULER is None:
            _BACKLOG_SCHEDULER = MonitorSyncScheduler(
                coordinator=get_backlog_sync_coordinator(),
                settings_reader=_read_monitor_settings,
                repo_id_provider=_list_enabled_repo_ids,
                missing_repo_id_provider=_list_missing_backlog_snapshot_repo_ids,
            )
        return _BACKLOG_SCHEDULER


def start_backlog_scheduler() -> MonitorSyncScheduler:
    """Start the Backlog scheduler loop. Idempotent — only one loop ever runs."""
    scheduler = get_backlog_scheduler()
    scheduler.start()
    return scheduler


def stop_backlog_scheduler(*, timeout_seconds: float = 5.0) -> None:
    """Stop the Backlog scheduler loop and join its thread within a bounded wait."""
    global _BACKLOG_SCHEDULER
    with _BACKLOG_SCHEDULER_LOCK:
        scheduler = _BACKLOG_SCHEDULER
        _BACKLOG_SCHEDULER = None
    if scheduler is None:
        return
    try:
        scheduler.stop(timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise.
        _logger.warning("Failed to stop backlog scheduler cleanly: %s", exc)


def wake_backlog_scheduler() -> None:
    """Wake a running Backlog scheduler so it recomputes its wait from fresh settings."""
    scheduler = _BACKLOG_SCHEDULER
    if scheduler is None:
        return
    scheduler.wake()


__all__ = [
    "ensure_fresh_backlog_snapshot",
    "get_backlog_scheduler",
    "get_backlog_snapshot_store",
    "get_backlog_sync_coordinator",
    "request_backlog_resync",
    "start_backlog_scheduler",
    "stop_backlog_scheduler",
    "wake_backlog_scheduler",
]
