"""Backlog 列表本地快照（stale-while-revalidate）的存储、编排与生命周期测试。

覆盖 PRD rv-2（快照跨进程读回、坏数据视同缺失、附加迁移不动既有表）、rv-3（读路径
不等待扫描、过期时带 stale 并触发一次后台重扫）、rv-4（调度循环预取缺快照仓库、
关闭开关时零扫描）、rv-5（扫描失败保留旧快照），以及 Architecture Acceptance 要求的
"路由/装配模块 import 零副作用 + 循环只随 lifespan 启停"。

边界替换原则：GitHub / PRD 扫描（``_build_backlog_response``）换成计数探针；真实
SQLite 文件、真实线程与事件、真实协调器与调度器、真实 :mod:`backend.api.backlog_sync`
接线均不 mock——"请求线程绝不等待扫描"只能在真有在途线程时被证伪。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.backlog_sync as backlog_sync
import backend.api.monitor_sync as monitor_sync
import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.interfaces.runner_console import (
    BacklogSnapshotEntry,
    MonitorSettingsEntry,
)
from backend.core.use_cases.backlog_snapshots import (
    BACKLOG_SNAPSHOT_STALE_TTL_SECONDS,
    build_backlog_task_key,
    is_backlog_snapshot_stale,
    list_missing_pending_repo_ids,
    parse_backlog_task_key,
    persist_backlog_snapshot,
    read_backlog_snapshot,
)
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncCoordinator,
    MonitorSyncScheduler,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.support.polling import wait_until
from tests.support.scheduler_threads import scheduler_thread_ids

REPO_ID = "keda-main"
OTHER_REPO_ID = "keda-other"

_IMPORT_PROBE_SCRIPT = """
import json
import threading

import backend.api.app
import backend.api.backlog_sync as backlog_sync
import backend.api.routes.agent_runner_backlog as backlog_routes

extra_threads = [
    thread.name
    for thread in threading.enumerate()
    if thread is not threading.main_thread() and thread.is_alive()
]
print(
    "IMPORT_PROBE "
    + json.dumps(
        {
            "scheduler": backlog_sync._BACKLOG_SCHEDULER is not None,
            "coordinator": backlog_sync._BACKLOG_COORDINATOR is not None,
            "route_loaded": hasattr(backlog_routes, "list_backlog_prds"),
            "extra_threads": extra_threads,
        }
    )
)
"""


def _timestamp_at(moment: datetime) -> str:
    """把 aware datetime 转成快照使用的秒级 ISO 文本。"""
    return moment.isoformat(timespec="seconds")


def _timestamp_with_age(age_seconds: float) -> str:
    """构造一个相对当前时刻偏移 ``age_seconds`` 的 ISO 快照时间。"""
    return _timestamp_at(datetime.now(timezone.utc) - timedelta(seconds=age_seconds))


def _list_payload(
    repo_id: str,
    *,
    include_archived: bool,
    title: str = "Test Feature",
    age_seconds: float = 0.0,
) -> dict:
    """构造与 ``_build_backlog_response`` 同构的列表响应。"""
    return {
        "prds": [{"title": title, "prd_path": "tasks/pending/x.md"}],
        "skipped": [],
        "repo_id": repo_id,
        "include_archived": include_archived,
        "scanned_at": _timestamp_with_age(age_seconds),
    }


def _enabled_settings(interval_seconds: int = 60) -> MonitorSettingsEntry:
    """构造一份开启状态下的全局同步设置（Backlog 复用同一来源）。"""
    return MonitorSettingsEntry(
        sync_enabled=True,
        sync_interval_seconds=interval_seconds,
        updated_at="2026-10-08T01:00:00+00:00",
    )


class _ScanProbe:
    """记录扫描请求的 fake ``_build_backlog_response``，可阻塞或抛错。"""

    def __init__(self) -> None:
        """初始化空记录与非阻塞、无异常的默认行为。"""
        self.calls: list[tuple[str, bool]] = []
        self.block_until_released = False
        self.error_message: str | None = None
        self._lock = threading.Lock()
        self._entered = threading.Event()
        self._release = threading.Event()

    def __call__(self, repo_id: str, include_archived: bool) -> dict:
        """记录一次扫描，并按配置阻塞（模拟慢仓库）或抛错（模拟扫描失败）。"""
        with self._lock:
            self.calls.append((repo_id, include_archived))
        if self.error_message is not None:
            raise RuntimeError(self.error_message)
        if self.block_until_released:
            self._entered.set()
            assert self._release.wait(timeout=10), "the scan probe was never released"
        return _list_payload(repo_id, include_archived=include_archived)

    def release(self) -> None:
        """放行阻塞中的扫描。"""
        self._release.set()

    def wait_for_enter(self, *, timeout_seconds: float = 5.0) -> bool:
        """等待阻塞型扫描已进入扫描体。"""
        return wait_until(lambda: self._entered.is_set(), timeout_seconds=timeout_seconds)

    def recorded(self) -> list[tuple[str, bool]]:
        """返回已记录扫描的副本。"""
        with self._lock:
            return list(self.calls)


@dataclass
class _BacklogHarness:
    """一套指向 tmp 库的 Backlog 快照接线。

    Attributes:
        store: tmp 文件支撑的 console store（快照与既有表同库）。
        probe: 扫描计数探针。
        coordinator: 真实协调器，其 ``scan_runner`` 走真实持久化路径。
    """

    store: SqliteConsoleStore
    probe: _ScanProbe
    coordinator: MonitorSyncCoordinator


@pytest.fixture
def backlog_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _BacklogHarness:
    """把 Backlog 快照接线指向 tmp 库，扫描边界换成探针，协调器与调度器保持真实。

    进程级单例在用例前后清空，避免调度线程跨用例泄漏。
    """
    store = SqliteConsoleStore(tmp_path / "console.db")
    probe = _ScanProbe()
    coordinator = MonitorSyncCoordinator(
        scan_runner=backlog_sync._scan_backlog_variant_and_persist  # noqa: SLF001
    )
    monkeypatch.setattr(backlog_sync, "get_backlog_snapshot_store", lambda: store)
    monkeypatch.setattr(backlog_routes, "_build_backlog_response", probe)
    monkeypatch.setattr(backlog_sync, "get_backlog_sync_coordinator", lambda: coordinator)
    monkeypatch.setattr(backlog_sync, "_BACKLOG_COORDINATOR", None)
    monkeypatch.setattr(backlog_sync, "_BACKLOG_SCHEDULER", None)

    yield _BacklogHarness(store=store, probe=probe, coordinator=coordinator)

    scheduler = backlog_sync._BACKLOG_SCHEDULER  # noqa: SLF001
    if scheduler is not None:
        scheduler.stop(timeout_seconds=5)
    probe.release()
    coordinator.wait_until_idle(timeout_seconds=5)


def _write_raw_snapshot(
    store: SqliteConsoleStore,
    *,
    repo_id: str,
    include_archived: bool,
    payload_json: str,
) -> None:
    """绕过 core 编排直接写一行快照，用于制造损坏或形状不符的数据。"""
    store.upsert_backlog_snapshot(
        BacklogSnapshotEntry(
            repo_id=repo_id,
            include_archived=include_archived,
            payload_json=payload_json,
            scanned_at=_timestamp_with_age(0.0),
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# 任务键与过期判定
# ─────────────────────────────────────────────────────────────────────────────


def test_task_key_round_trips_both_view_variants() -> None:
    """两个视图变体必须映射到不同任务键并能原样还原，否则去重粒度就是错的。"""
    pending_key = build_backlog_task_key(REPO_ID, include_archived=False)
    archived_key = build_backlog_task_key(REPO_ID, include_archived=True)

    assert pending_key == REPO_ID
    assert archived_key == f"{REPO_ID}::archived"
    assert parse_backlog_task_key(pending_key) == (REPO_ID, False)
    assert parse_backlog_task_key(archived_key) == (REPO_ID, True)


@pytest.mark.parametrize("bad_key", ["", "::archived"])
def test_task_key_rejects_keys_without_repo_id(bad_key: str) -> None:
    """空键或只有后缀的键都必须报错，不能让无归属的扫描进入后台。"""
    with pytest.raises(ValueError):
        parse_backlog_task_key(bad_key)


def test_stale_flag_covers_missing_unparsable_and_expired_timestamps() -> None:
    """缺失、无法解析、超过 TTL 都判过期；TTL 内判新鲜——这是前端 3 秒轮询的唯一触发条件。"""
    now_reference = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)
    expired = _timestamp_at(
        now_reference - timedelta(seconds=BACKLOG_SNAPSHOT_STALE_TTL_SECONDS + 1)
    )
    fresh = _timestamp_at(now_reference - timedelta(seconds=BACKLOG_SNAPSHOT_STALE_TTL_SECONDS - 1))

    assert is_backlog_snapshot_stale(None, now_reference=now_reference) is True
    assert is_backlog_snapshot_stale("not-a-date", now_reference=now_reference) is True
    assert is_backlog_snapshot_stale(expired, now_reference=now_reference) is True
    assert is_backlog_snapshot_stale(fresh, now_reference=now_reference) is False


# ─────────────────────────────────────────────────────────────────────────────
# rv-2：持久化跨重启、损坏视同缺失、附加迁移不动既有表
# ─────────────────────────────────────────────────────────────────────────────


def test_snapshot_survives_a_new_store_instance(backlog_harness: _BacklogHarness) -> None:
    """写入后用全新 store 实例（等价于 console 重启后的进程）读回同一份 payload。"""
    payload = _list_payload(REPO_ID, include_archived=False)
    persist_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=False, payload=payload
    )

    reopened_store = SqliteConsoleStore(backlog_harness.store._db_path)  # noqa: SLF001
    view = read_backlog_snapshot(reopened_store, repo_id=REPO_ID, include_archived=False)

    assert view.payload == payload
    assert view.scanned_at == payload["scanned_at"]
    assert view.stale is False


def test_view_variants_do_not_overwrite_each_other(backlog_harness: _BacklogHarness) -> None:
    """主键是 ``(repo_id, include_archived)``：归档变体不得覆盖默认视图行。"""
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=_list_payload(REPO_ID, include_archived=False, title="Pending Only"),
    )
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=True,
        payload=_list_payload(REPO_ID, include_archived=True, title="With Archived"),
    )

    pending_view = read_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=False
    )
    archived_view = read_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=True
    )

    assert pending_view.payload["prds"][0]["title"] == "Pending Only"
    assert archived_view.payload["prds"][0]["title"] == "With Archived"
    assert len(backlog_harness.store.list_backlog_snapshots()) == 2


def test_upsert_overwrites_the_same_variant_row(backlog_harness: _BacklogHarness) -> None:
    """同一变体重复写入是覆盖而非追加，否则快照表会随刷新无界增长。"""
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=_list_payload(REPO_ID, include_archived=False, title="First"),
    )
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=_list_payload(REPO_ID, include_archived=False, title="Second"),
    )

    view = read_backlog_snapshot(backlog_harness.store, repo_id=REPO_ID, include_archived=False)

    assert view.payload["prds"][0]["title"] == "Second"
    assert len(backlog_harness.store.list_backlog_snapshots()) == 1


def test_corrupt_or_misshapen_snapshot_reads_as_missing(
    backlog_harness: _BacklogHarness,
) -> None:
    """坏 JSON、仓库不符、变体不符都视同缺失：返回空骨架 + stale，绝不下发坏数据。"""
    _write_raw_snapshot(
        backlog_harness.store,
        repo_id="corrupt-repo",
        include_archived=False,
        payload_json="{not json",
    )
    _write_raw_snapshot(
        backlog_harness.store,
        repo_id="wrong-repo",
        include_archived=False,
        payload_json=json.dumps(_list_payload("another-repo", include_archived=False)),
    )
    _write_raw_snapshot(
        backlog_harness.store,
        repo_id="wrong-variant",
        include_archived=True,
        payload_json=json.dumps(_list_payload("wrong-variant", include_archived=False)),
    )

    for repo_id, include_archived in (
        ("corrupt-repo", False),
        ("wrong-repo", False),
        ("wrong-variant", True),
    ):
        view = read_backlog_snapshot(
            backlog_harness.store, repo_id=repo_id, include_archived=include_archived
        )
        assert view.payload["prds"] == []
        assert view.payload["skipped"] == []
        assert view.payload["repo_id"] == repo_id
        assert view.scanned_at is None
        assert view.stale is True


def test_stale_snapshot_still_renders_its_old_payload(backlog_harness: _BacklogHarness) -> None:
    """过期快照仍原样返回旧 payload，只多带一个 stale——空态只在真没快照时出现。"""
    payload = _list_payload(REPO_ID, include_archived=False, title="Old List", age_seconds=600)
    persist_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=False, payload=payload
    )

    view = read_backlog_snapshot(backlog_harness.store, repo_id=REPO_ID, include_archived=False)

    assert view.payload == payload
    assert view.stale is True


def test_missing_pending_repo_ids_only_lists_repos_without_default_snapshot(
    backlog_harness: _BacklogHarness,
) -> None:
    """预取目标只含"启用但还没有默认视图快照"的仓库；仅有归档行不算已有。"""
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=_list_payload(REPO_ID, include_archived=False),
    )
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=OTHER_REPO_ID,
        include_archived=True,
        payload=_list_payload(OTHER_REPO_ID, include_archived=True),
    )

    missing = list_missing_pending_repo_ids(
        backlog_harness.store, enabled_repo_ids=[REPO_ID, OTHER_REPO_ID, "brand-new"]
    )

    assert missing == (OTHER_REPO_ID, "brand-new")


# ─────────────────────────────────────────────────────────────────────────────
# rv-3：读路径不等待扫描，过期时带 stale 并触发一次后台重扫
# ─────────────────────────────────────────────────────────────────────────────


def test_read_serves_old_snapshot_while_scan_runs_in_background(
    backlog_harness: _BacklogHarness,
) -> None:
    """慢扫描在途时读路径必须立即返回旧 scanned_at + stale=true，不等网络。"""
    stale_payload = _list_payload(
        REPO_ID, include_archived=False, title="Old List", age_seconds=600
    )
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=stale_payload,
    )
    backlog_harness.probe.block_until_released = True

    started_at = time.perf_counter()
    view = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)
    elapsed_seconds = time.perf_counter() - started_at

    assert elapsed_seconds < 0.5, f"the read path waited {elapsed_seconds:.3f}s for the scan"
    assert view.stale is True
    assert view.scanned_at == stale_payload["scanned_at"]
    assert view.payload["prds"][0]["title"] == "Old List"
    assert backlog_harness.probe.wait_for_enter()
    pending_task_key = build_backlog_task_key(REPO_ID, include_archived=False)
    assert backlog_harness.coordinator.is_in_flight(pending_task_key)


def test_repeat_reads_dedup_to_one_in_flight_scan_per_variant(
    backlog_harness: _BacklogHarness,
) -> None:
    """同一变体在途扫描期间反复轮询只能触发一次扫描，否则慢仓库会被轮询打爆。"""
    backlog_harness.probe.block_until_released = True

    for _ in range(3):
        view = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)
        assert view.stale is True
        assert view.scanned_at is None
        assert view.payload["prds"] == []

    assert backlog_harness.probe.wait_for_enter()
    assert backlog_harness.probe.recorded() == [(REPO_ID, False)]


def test_archived_variant_is_a_separate_scan_task(
    backlog_harness: _BacklogHarness,
) -> None:
    """``include_archived=true`` 与默认视图是两个独立任务，不能互相顶掉。"""
    backlog_harness.probe.block_until_released = True

    backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)
    assert backlog_harness.probe.wait_for_enter()
    backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=True)
    assert backlog_harness.probe.wait_for_enter()

    assert backlog_harness.probe.recorded() == [(REPO_ID, False), (REPO_ID, True)]


def test_fresh_snapshot_read_requests_no_rescan(backlog_harness: _BacklogHarness) -> None:
    """TTL 内的快照读取是纯本地读：不派扫描，30 秒轮询因此零成本。"""
    persist_backlog_snapshot(
        backlog_harness.store,
        repo_id=REPO_ID,
        include_archived=False,
        payload=_list_payload(REPO_ID, include_archived=False, age_seconds=1),
    )

    view = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)

    assert view.stale is False
    assert backlog_harness.probe.recorded() == []
    assert backlog_harness.coordinator.in_flight_repo_ids() == ()


def test_snapshot_written_by_background_scan_flips_stale_to_false(
    backlog_harness: _BacklogHarness,
) -> None:
    """重扫落库后再次读取必须 stale=false——这是前端把轮询退回 30 秒的判据。"""
    first = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)

    assert first.stale is True
    assert backlog_harness.probe.recorded() == [(REPO_ID, False)]
    assert backlog_harness.coordinator.wait_until_idle(timeout_seconds=5)

    second = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)

    assert second.stale is False
    assert second.payload["prds"][0]["title"] == "Test Feature"
    assert (
        backlog_harness.store.get_backlog_snapshot(repo_id=REPO_ID, include_archived=False)
        is not None
    )


# ─────────────────────────────────────────────────────────────────────────────
# rv-5：扫描或写库失败都不得动旧快照
# ─────────────────────────────────────────────────────────────────────────────


def test_failed_scan_keeps_previous_snapshot(backlog_harness: _BacklogHarness) -> None:
    """扫描抛错时旧快照原样保留，读侧继续返回旧数据并如实标记 stale。"""
    old_payload = _list_payload(REPO_ID, include_archived=False, title="Old List", age_seconds=600)
    persist_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=False, payload=old_payload
    )
    backlog_harness.probe.error_message = "github rate limited"

    view = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)

    assert view.stale is True
    assert backlog_harness.coordinator.wait_until_idle(timeout_seconds=5)
    after_failure = backlog_sync.ensure_fresh_backlog_snapshot(REPO_ID, include_archived=False)
    assert after_failure.payload == old_payload
    assert after_failure.scanned_at == old_payload["scanned_at"]
    stored_entry = backlog_harness.store.get_backlog_snapshot(
        repo_id=REPO_ID, include_archived=False
    )
    assert stored_entry is not None
    assert json.loads(stored_entry.payload_json) == old_payload


def test_persist_failure_propagates_and_keeps_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    backlog_harness: _BacklogHarness,
) -> None:
    """写库失败必须上抛而不是静默吞掉，否则旧快照会被谎报成"刷新成功"。"""
    old_payload = _list_payload(REPO_ID, include_archived=False, title="Old List")
    persist_backlog_snapshot(
        backlog_harness.store, repo_id=REPO_ID, include_archived=False, payload=old_payload
    )

    def failing_upsert(_snapshot_entry: BacklogSnapshotEntry) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backlog_harness.store, "upsert_backlog_snapshot", failing_upsert)

    with pytest.raises(sqlite3.OperationalError):
        persist_backlog_snapshot(
            backlog_harness.store,
            repo_id=REPO_ID,
            include_archived=False,
            payload=_list_payload(REPO_ID, include_archived=False, title="New List"),
        )

    survivor = read_backlog_snapshot(backlog_harness.store, repo_id=REPO_ID, include_archived=False)
    assert survivor.payload["prds"][0]["title"] == "Old List"


# ─────────────────────────────────────────────────────────────────────────────
# rv-4 + Architecture：调度循环与 lifespan
# ─────────────────────────────────────────────────────────────────────────────


def test_importing_backlog_modules_starts_no_side_effects() -> None:
    """只 import app / backlog_sync / 路由模块不得启动调度器、建协调器或产生线程。"""
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

    assert probe["route_loaded"] is True
    assert probe["scheduler"] is False
    assert probe["coordinator"] is False
    assert probe["extra_threads"] == []


def test_lifespan_owns_the_backlog_loop_and_prefetches_missing_repos(
    monkeypatch: pytest.MonkeyPatch,
    backlog_harness: _BacklogHarness,
) -> None:
    """lifespan 内 Backlog 与 dashboard 各起一个循环；首扫只补缺快照仓库的默认视图。"""
    monkeypatch.setattr(backlog_sync, "_list_enabled_repo_ids", lambda: [REPO_ID, OTHER_REPO_ID])
    monkeypatch.setattr(backlog_sync, "_read_monitor_settings", _enabled_settings)
    monkeypatch.setattr(monitor_sync, "_read_monitor_settings", _enabled_settings)
    monkeypatch.setattr(monitor_sync, "_list_monitored_repo_ids", lambda: [REPO_ID])
    monkeypatch.setattr(monitor_sync, "_list_missing_snapshot_repo_ids", lambda: [])
    monkeypatch.setattr(
        monitor_sync,
        "get_monitor_sync_coordinator",
        lambda: MonitorSyncCoordinator(scan_runner=lambda _repo_id: None),
    )

    with TestClient(app):
        assert wait_until(lambda: len(scheduler_thread_ids()) == 2)
        # 预取按默认视图逐仓库各一次；归档变体按需构建，不参与预取。
        assert wait_until(
            lambda: sorted(backlog_harness.probe.recorded())
            == sorted([(REPO_ID, False), (OTHER_REPO_ID, False)])
        )
        backlog_scheduler = backlog_sync.get_backlog_scheduler()
        assert backlog_scheduler.running is True
        assert wait_until(
            lambda: backlog_harness.store.get_backlog_snapshot(
                repo_id=REPO_ID, include_archived=False
            )
            is not None
        )

    assert backlog_scheduler.running is False
    assert backlog_sync._BACKLOG_SCHEDULER is None  # noqa: SLF001
    assert wait_until(lambda: scheduler_thread_ids() == set())


def test_disabled_sync_never_scans_backlog(backlog_harness: _BacklogHarness) -> None:
    """关闭全局自动同步后，Backlog 循环既不做首扫也不做周期扫描。"""
    disabled_settings = MonitorSettingsEntry(
        sync_enabled=False,
        sync_interval_seconds=60,
        updated_at="2026-10-08T01:00:00+00:00",
    )
    scheduler = MonitorSyncScheduler(
        coordinator=backlog_harness.coordinator,
        settings_reader=lambda: disabled_settings,
        repo_id_provider=lambda: [REPO_ID],
        missing_repo_id_provider=lambda: [REPO_ID],
    )

    scheduler.start()
    try:
        assert wait_until(lambda: scheduler.running)
        scheduler.wake()
        time.sleep(0.2)
    finally:
        scheduler.stop(timeout_seconds=5)

    assert backlog_harness.probe.recorded() == []
    assert scheduler.running is False


def test_stop_and_wake_are_safe_without_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """没启动过循环时重复 stop 与 wake 都不得抛错，也不得反向把循环拉起来。"""
    monkeypatch.setattr(backlog_sync, "_BACKLOG_SCHEDULER", None)

    backlog_sync.stop_backlog_scheduler()
    backlog_sync.stop_backlog_scheduler()
    backlog_sync.wake_backlog_scheduler()

    assert backlog_sync._BACKLOG_SCHEDULER is None  # noqa: SLF001
