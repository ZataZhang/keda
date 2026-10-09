"""监控快照后台同步的调度器与生命周期测试。

覆盖 PRD rv-4 中"scheduler 只随 FastAPI lifespan 启动一次并在 shutdown 回收、
路由模块 import 零副作用、设置变化唤醒重算、关闭后零后台扫描"这几条。
GitHub/gh 边界替换为记录调用次数的 fake 扫描器；FastAPI lifespan、真实线程与
事件、TestClient 均不 mock。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.app as app_module
import backend.api.monitor_sync as monitor_sync
from backend.api.app import app
from backend.core.shared.interfaces.runner_console import MonitorSettingsEntry
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncCoordinator,
    MonitorSyncScheduler,
)
from tests.support.polling import wait_until
from tests.support.scheduler_threads import scheduler_thread_ids

_IMPORT_PROBE_SCRIPT = """
import json
import threading

import backend.api.monitor_sync as monitor_sync
import backend.api.routes.agent_runner as agent_runner_routes

extra_threads = [
    thread.name
    for thread in threading.enumerate()
    if thread is not threading.main_thread() and thread.is_alive()
]
print(
    "IMPORT_PROBE "
    + json.dumps(
        {
            "scheduler": monitor_sync._SCHEDULER is not None,
            "coordinator": agent_runner_routes._MONITOR_COORDINATOR is not None,
            "extra_threads": extra_threads,
        }
    )
)
"""


class _ScanRecorder:
    """记录每次扫描请求的 fake ``scan_runner``。"""

    def __init__(self) -> None:
        """初始化空记录。"""
        self._repo_ids: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, repo_id: str) -> None:
        """记录一次扫描请求。"""
        with self._lock:
            self._repo_ids.append(repo_id)

    def recorded(self) -> list[str]:
        """返回已记录的仓库 ID 快照。"""
        with self._lock:
            return list(self._repo_ids)


def _enabled_settings(interval_seconds: int = 60) -> MonitorSettingsEntry:
    """构造一份开启状态下的同步设置。"""
    return MonitorSettingsEntry(
        sync_enabled=True,
        sync_interval_seconds=interval_seconds,
        updated_at="2026-09-16T01:00:00+00:00",
    )


# ─────────────────────────────────────────────────────────────────────────────
# import 副作用
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _neutralize_backlog_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 Backlog 的第二个循环挡住，本模块只验 dashboard 循环。

    app 的 lifespan 现在同时启停 dashboard 与 Backlog 两个循环；不隔离的话这些
    用例会带着真实 provider 去扫真实仓库，测试结论也不再归属单一被测对象。
    """
    monkeypatch.setattr(app_module, "start_backlog_scheduler", lambda: None)
    monkeypatch.setattr(app_module, "stop_backlog_scheduler", lambda **_kwargs: None)


def test_importing_route_modules_starts_nothing() -> None:
    """只 import 路由/装配模块不得启动 scheduler、建协调器或产生后台线程。"""
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE_SCRIPT],
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": str(project_root)},
        capture_output=True,
        text=True,
        check=True,
    )
    probe_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("IMPORT_PROBE ")
    )
    probe = json.loads(probe_line.removeprefix("IMPORT_PROBE "))

    assert probe["scheduler"] is False
    assert probe["coordinator"] is False
    assert probe["extra_threads"] == []


# ─────────────────────────────────────────────────────────────────────────────
# lifespan 生命周期
# ─────────────────────────────────────────────────────────────────────────────


def test_lifespan_owns_exactly_one_scheduler_and_reclaims_it(
    monkeypatch,
) -> None:
    """TestClient 生命周期内恰好一个调度器线程，退出后被有界回收。"""
    recorder = _ScanRecorder()
    coordinator = MonitorSyncCoordinator(scan_runner=recorder)
    monkeypatch.setattr(monitor_sync, "get_monitor_sync_coordinator", lambda: coordinator)
    monkeypatch.setattr(monitor_sync, "_read_monitor_settings", _enabled_settings)
    monkeypatch.setattr(
        monitor_sync,
        "_list_monitored_repo_ids",
        lambda: ["repo-a", "repo-b"],
    )
    monkeypatch.setattr(
        monitor_sync,
        "_list_missing_snapshot_repo_ids",
        lambda: ["repo-b"],
    )

    with TestClient(app):
        scheduler = monitor_sync.get_monitor_scheduler()
        assert wait_until(lambda: len(scheduler_thread_ids(scheduler)) == 1)
        assert scheduler.running is True
        # 重复 start 幂等：不会多出一个调度线程。
        assert monitor_sync.start_monitor_scheduler() is scheduler
        assert monitor_sync.start_monitor_scheduler() is scheduler
        assert len(scheduler_thread_ids(scheduler)) == 1
        # 首扫只为缺失快照的仓库申请，已有快照的仓库等下个周期。
        assert wait_until(lambda: recorder.recorded() == ["repo-b"])

    assert scheduler.running is False
    assert monitor_sync._SCHEDULER is None  # noqa: SLF001
    assert wait_until(lambda: scheduler_thread_ids(scheduler) == set())


def test_lifespan_release_is_safe_without_start(monkeypatch) -> None:
    """没有启动过调度器时，重复 stop 不得抛错（测试导入路径的常见形态）。"""
    monkeypatch.setattr(monitor_sync, "_SCHEDULER", None)

    monitor_sync.stop_monitor_scheduler()
    monitor_sync.stop_monitor_scheduler()

    assert monitor_sync._SCHEDULER is None  # noqa: SLF001


# ─────────────────────────────────────────────────────────────────────────────
# 调度循环：唤醒与开关
# ─────────────────────────────────────────────────────────────────────────────


def test_wake_recomputes_the_wait_window() -> None:
    """wake 立即打断旧等待窗口，并让循环按新设置重新调度。"""
    recorder = _ScanRecorder()
    coordinator = MonitorSyncCoordinator(scan_runner=recorder)
    settings_holder = {"value": _enabled_settings(interval_seconds=3600)}
    scheduler = MonitorSyncScheduler(
        coordinator=coordinator,
        settings_reader=lambda: settings_holder["value"],
        repo_id_provider=lambda: ["repo-a"],
        missing_repo_id_provider=lambda: [],
    )
    scheduler.start()
    try:
        # 首圈没有缺失快照 → 不扫描，进入 3600 秒等待窗口。
        assert wait_until(lambda: scheduler.running)
        time.sleep(0.1)
        assert recorder.recorded() == []
        settings_holder["value"] = _enabled_settings(interval_seconds=60)
        scheduler.wake()
        # 若沿用旧窗口，这里在 3600 秒内不可能出现扫描。
        assert wait_until(lambda: recorder.recorded() == ["repo-a"])
    finally:
        scheduler.stop()
    assert scheduler.running is False


def test_disabled_settings_never_scan_and_wake_keeps_them_off() -> None:
    """关闭自动同步后既不做首扫也不做周期扫描；唤醒后仍不扫描。"""
    recorder = _ScanRecorder()
    coordinator = MonitorSyncCoordinator(scan_runner=recorder)
    settings_reads: list[int] = []
    disabled_settings = MonitorSettingsEntry(
        sync_enabled=False,
        sync_interval_seconds=60,
        updated_at="2026-09-16T01:00:00+00:00",
    )

    def settings_reader() -> MonitorSettingsEntry:
        settings_reads.append(len(settings_reads))
        return disabled_settings

    scheduler = MonitorSyncScheduler(
        coordinator=coordinator,
        settings_reader=settings_reader,
        repo_id_provider=lambda: ["repo-a", "repo-b"],
        missing_repo_id_provider=lambda: ["repo-a", "repo-b"],
    )
    scheduler.start()
    try:
        assert wait_until(lambda: len(settings_reads) >= 1)
        scheduler.wake()
        assert wait_until(lambda: len(settings_reads) >= 2)
    finally:
        scheduler.stop()

    assert recorder.recorded() == []
    assert coordinator.in_flight_repo_ids() == ()


def test_stop_timeout_keeps_handle_so_no_second_loop_starts() -> None:
    """join 超时后不得丢掉线程句柄，否则 start() 会起出第二个调度循环。"""
    entered_reader = threading.Event()
    release_reader = threading.Event()

    def blocking_reader() -> MonitorSettingsEntry:
        entered_reader.set()
        release_reader.wait(timeout=10)
        return _enabled_settings()

    scheduler = MonitorSyncScheduler(
        coordinator=MonitorSyncCoordinator(scan_runner=_ScanRecorder()),
        settings_reader=blocking_reader,
        repo_id_provider=lambda: ["repo-a"],
    )
    scheduler.start()
    try:
        assert entered_reader.wait(timeout=5)
        scheduler.stop(timeout_seconds=0.05)
        # 循环还卡在读取里：stop 必须保守地保留句柄，而不是假装已停止。
        assert scheduler.running is True
        stuck_thread = scheduler._thread  # noqa: SLF001 - 断言同一句柄未被丢弃
        scheduler.start()
        assert scheduler._thread is stuck_thread  # noqa: SLF001
        assert len(scheduler_thread_ids(scheduler)) == 1
    finally:
        release_reader.set()
        scheduler.stop(timeout_seconds=10)

    assert scheduler.running is False
    assert wait_until(lambda: scheduler_thread_ids(scheduler) == set())


def test_settings_reader_failure_does_not_kill_the_loop() -> None:
    """设置读取失败只跳过一轮，循环继续存活并在下一轮恢复扫描。"""
    recorder = _ScanRecorder()
    coordinator = MonitorSyncCoordinator(scan_runner=recorder)
    read_attempts: list[int] = []

    def failing_reader() -> MonitorSettingsEntry:
        read_attempts.append(len(read_attempts))
        if len(read_attempts) == 1:
            raise RuntimeError("settings table locked")
        return _enabled_settings(interval_seconds=60)

    scheduler = MonitorSyncScheduler(
        coordinator=coordinator,
        settings_reader=failing_reader,
        repo_id_provider=lambda: ["repo-a"],
        missing_repo_id_provider=lambda: ["repo-a"],
    )
    scheduler.start()
    try:
        # 第一次读取抛错后进入最小等待窗口，wake 之后第二轮应恢复并请求首扫。
        assert wait_until(lambda: len(read_attempts) >= 1)
        scheduler.wake()
        assert wait_until(lambda: recorder.recorded() == ["repo-a"])
    finally:
        scheduler.stop()

    assert scheduler.running is False
