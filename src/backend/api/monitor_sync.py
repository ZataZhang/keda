"""dashboard 后台同步调度器的生命周期边界。

调度器是长期运行的后台线程，**只能**由 FastAPI 应用生命周期（``app.py`` 的
lifespan）启停：路由模块 import 不得产生线程或 I/O 副作用，因此本模块只暴露
工厂与唤醒入口，真正的启动动作发生在 lifespan。

设置被 PATCH 修改后，路由调用 :func:`wake_monitor_scheduler` 让调度器立刻
按新设置重算等待时间，而不是沿用旧周期的剩余等待窗口。
"""

from __future__ import annotations

import logging
import threading
from typing import Sequence, cast

from backend.api.routes.agent_runner import (
    _list_enabled_repo_ids,
    get_monitor_sync_coordinator,
)
from backend.core.shared.interfaces.runner_console import (
    IMonitorSnapshotStore,
    MonitorSettingsEntry,
)
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    load_fresh_agent_runner_settings,
)
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncScheduler,
    get_monitor_settings,
)

_logger = logging.getLogger(__name__)

_SCHEDULER: MonitorSyncScheduler | None = None
_SCHEDULER_LOCK = threading.Lock()


def _read_monitor_settings() -> MonitorSettingsEntry:
    """Read the effective sync settings, falling back to the static default."""
    store = cast(IMonitorSnapshotStore, create_console_store())
    default_interval_seconds = (
        load_fresh_agent_runner_settings().console.monitor_sync_interval_seconds
    )
    return get_monitor_settings(store, default_interval_seconds=default_interval_seconds)


def _list_monitored_repo_ids() -> Sequence[str]:
    """Return the repo_id of every enabled registry repository."""
    return _list_enabled_repo_ids()


def _list_missing_snapshot_repo_ids() -> Sequence[str]:
    """Return enabled repositories that have no snapshot yet (first-scan targets)."""
    store = cast(IMonitorSnapshotStore, create_console_store())
    snapshot_repo_ids = {entry.repo_id for entry in store.list_monitor_snapshots()}
    return [repo_id for repo_id in _list_enabled_repo_ids() if repo_id not in snapshot_repo_ids]


def get_monitor_scheduler() -> MonitorSyncScheduler:
    """Return the process-wide scheduler instance (created on first use)."""
    global _SCHEDULER
    if _SCHEDULER is not None:
        return _SCHEDULER
    with _SCHEDULER_LOCK:
        if _SCHEDULER is None:
            _SCHEDULER = MonitorSyncScheduler(
                coordinator=get_monitor_sync_coordinator(),
                settings_reader=_read_monitor_settings,
                repo_id_provider=_list_monitored_repo_ids,
                missing_repo_id_provider=_list_missing_snapshot_repo_ids,
            )
        return _SCHEDULER


def start_monitor_scheduler() -> MonitorSyncScheduler:
    """Start the scheduler loop. Idempotent — only one loop ever runs."""
    scheduler = get_monitor_scheduler()
    scheduler.start()
    return scheduler


def stop_monitor_scheduler(*, timeout_seconds: float = 5.0) -> None:
    """Stop the scheduler loop and join its thread within a bounded wait."""
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        scheduler = _SCHEDULER
        _SCHEDULER = None
    if scheduler is None:
        return
    try:
        scheduler.stop(timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise.
        _logger.warning("Failed to stop monitor scheduler cleanly: %s", exc)


def wake_monitor_scheduler() -> None:
    """Wake a running scheduler so it recomputes its wait from fresh settings."""
    scheduler = _SCHEDULER
    if scheduler is None:
        return
    scheduler.wake()
