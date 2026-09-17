"""Tests for the monitor snapshot use cases and the per-repository coordinator.

覆盖 PRD rv-4 中"持久化唯一入口 / 写失败传播 / registry 过滤 / 设置校验 /
同仓库去重与跨仓库并行"这几条：GitHub 侧用记录调用次数与并发窗口的 fake，
线程、事件与 SQLite（由 store 测试覆盖）不 mock。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest

from backend.core.shared.interfaces.runner_console import (
    SYNC_STATUS_PARTIAL,
    SYNC_STATUS_PENDING_FIRST_SYNC,
    SYNC_STATUS_READY,
    MonitorSettingsEntry,
    MonitorSnapshotEntry,
)
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncCoordinator,
    MonitorSyncError,
    get_monitor_settings,
    get_snapshot_overview,
    persist_monitoring_result,
    snapshot_overview_to_payload,
    update_monitor_settings,
)


class _FakeMonitorStore:
    """In-memory ``IMonitorSnapshotStore`` with an injectable write failure."""

    def __init__(self, *, fail_for_repo_ids: frozenset[str] = frozenset()) -> None:
        """Initialize an empty store.

        Args:
            fail_for_repo_ids: Repository IDs whose writes must raise.
        """
        self.snapshots: dict[str, MonitorSnapshotEntry] = {}
        self.settings: MonitorSettingsEntry | None = None
        self.fail_for_repo_ids = fail_for_repo_ids
        self.write_call_count = 0

    def upsert_monitor_snapshot(self, entry: MonitorSnapshotEntry) -> None:
        """Store a snapshot, or raise for pre-configured repository IDs."""
        self.write_call_count += 1
        if entry.repo_id in self.fail_for_repo_ids:
            raise RuntimeError(f"disk full for {entry.repo_id}")
        self.snapshots[entry.repo_id] = entry

    def list_monitor_snapshots(self) -> list[MonitorSnapshotEntry]:
        """Return every stored snapshot."""
        return list(self.snapshots.values())

    def get_monitor_settings(self) -> MonitorSettingsEntry | None:
        """Return the stored settings row, if any."""
        return self.settings

    def save_monitor_settings(self, settings: MonitorSettingsEntry) -> None:
        """Persist the settings row."""
        self.settings = settings


def _overview_payload(*repo_ids: str, scanned_at: str = "2026-09-16T01:00:00+00:00") -> dict:
    """Build an overview response shaped like the backend's ``/overview``."""
    return {
        "repositories": [
            {
                "repo_id": repo_id,
                "display_name": repo_id.upper(),
                "issues": [],
                "anomaly_count": 0,
                "scanned_at": scanned_at,
            }
            for repo_id in repo_ids
        ],
        "scanned_at": scanned_at,
        "unreachable_repositories": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 写路径
# ─────────────────────────────────────────────────────────────────────────────


def test_persist_writes_every_repository_exactly_once() -> None:
    """现成 payload 每个仓库一次 upsert，不重复扫描、不重复写库。"""
    store = _FakeMonitorStore()

    report = persist_monitoring_result(store, _overview_payload("repo-a", "repo-b"))

    assert report.ok
    assert set(report.persisted_repo_ids) == {"repo-a", "repo-b"}
    assert store.write_call_count == 2
    assert store.snapshots["repo-a"].scanned_at == "2026-09-16T01:00:00+00:00"


def test_persist_failure_is_reported_and_keeps_old_snapshot() -> None:
    """写库失败进入报告并保留旧快照，绝不伪装成刷新成功。"""
    store = _FakeMonitorStore(fail_for_repo_ids=frozenset({"repo-b"}))
    stale_snapshot = MonitorSnapshotEntry(
        repo_id="repo-b",
        payload_json='{"repo_id": "repo-b", "issues": []}',
        scanned_at="2026-09-15T23:00:00+00:00",
    )
    store.snapshots["repo-b"] = stale_snapshot

    report = persist_monitoring_result(store, _overview_payload("repo-a", "repo-b"))

    assert report.persisted_repo_ids == ("repo-a",)
    assert set(report.failed_repo_ids) == {"repo-b"}
    assert report.ok is False
    # 失败仓库的旧快照原样保留。
    assert store.snapshots["repo-b"] is stale_snapshot
    assert store.snapshots["repo-b"].scanned_at == "2026-09-15T23:00:00+00:00"


def test_persist_ignores_repositories_without_id() -> None:
    """没有 repo_id 的条目直接跳过，不写成空主键行。"""
    store = _FakeMonitorStore()

    report = persist_monitoring_result(
        store, {"repositories": [{"display_name": "orphan"}], "scanned_at": ""}
    )

    assert report.persisted_repo_ids == ()
    assert store.snapshots == {}


# ─────────────────────────────────────────────────────────────────────────────
# 读路径
# ─────────────────────────────────────────────────────────────────────────────


def test_snapshot_overview_filters_removed_and_disabled_repositories() -> None:
    """已删除/禁用仓库即使仍有历史行也不得重新出现在页面。"""
    store = _FakeMonitorStore()
    persist_monitoring_result(store, _overview_payload("repo-a", "removed-repo", "repo-c"))

    result = get_snapshot_overview(store, enabled_repo_ids=["repo-a", "repo-c"])

    assert [entry.repo_id for entry in result.repositories] == ["repo-a", "repo-c"]
    assert result.missing_repo_ids == ()
    assert result.sync_status == SYNC_STATUS_READY
    assert result.scanned_at == "2026-09-16T01:00:00+00:00"


def test_snapshot_overview_reports_pending_first_sync() -> None:
    """全新环境（全部仓库无快照）暴露 pending_first_sync 供前端显示空态。"""
    store = _FakeMonitorStore()

    result = get_snapshot_overview(store, enabled_repo_ids=["repo-a", "repo-b"])

    assert result.repositories == ()
    assert result.missing_repo_ids == ("repo-a", "repo-b")
    assert result.sync_status == SYNC_STATUS_PENDING_FIRST_SYNC
    assert result.scanned_at is None


def test_snapshot_overview_reports_partial_state() -> None:
    """部分仓库有快照时既展示已有数据，也标记缺失项。"""
    store = _FakeMonitorStore()
    persist_monitoring_result(
        store, _overview_payload("repo-a", scanned_at="2026-09-16T01:00:00+00:00")
    )

    result = get_snapshot_overview(store, enabled_repo_ids=["repo-a", "repo-b"])

    assert [entry.repo_id for entry in result.repositories] == ["repo-a"]
    assert result.missing_repo_ids == ("repo-b",)
    assert result.sync_status == SYNC_STATUS_PARTIAL


def test_corrupt_snapshot_is_treated_as_missing() -> None:
    """快照 JSON 损坏时宁可显示空态，也不把坏数据渲染成队列状态。"""
    store = _FakeMonitorStore()
    store.snapshots["repo-a"] = MonitorSnapshotEntry(
        repo_id="repo-a",
        payload_json="{not-json",
        scanned_at="2026-09-16T01:00:00+00:00",
    )

    result = get_snapshot_overview(store, enabled_repo_ids=["repo-a"])

    assert result.repositories == ()
    assert result.missing_repo_ids == ("repo-a",)


@pytest.mark.parametrize(
    "payload_json",
    [
        '{"foo": 1}',  # 合法 JSON，但不是 per-repo overview
        '{"repo_id": "other-repo", "issues": []}',  # repo_id 与行主键不一致
        '{"repo_id": "repo-a", "issues": "oops"}',  # issues 不是列表
    ],
)
def test_malformed_snapshot_is_treated_as_missing(payload_json: str) -> None:
    """合法 JSON 但形状不对的快照同样视同缺失，避免前端渲染期抛错。"""
    store = _FakeMonitorStore()
    store.snapshots["repo-a"] = MonitorSnapshotEntry(
        repo_id="repo-a",
        payload_json=payload_json,
        scanned_at="2026-09-16T01:00:00+00:00",
    )

    result = get_snapshot_overview(store, enabled_repo_ids=["repo-a"])

    assert result.repositories == ()
    assert result.missing_repo_ids == ("repo-a",)
    assert result.sync_status == SYNC_STATUS_PENDING_FIRST_SYNC


def test_snapshot_overview_payload_shape() -> None:
    """HTTP 响应体带 per-repo scanned_at、缺失清单与整体状态。"""
    store = _FakeMonitorStore()
    persist_monitoring_result(store, _overview_payload("repo-a"))

    payload = snapshot_overview_to_payload(
        get_snapshot_overview(store, enabled_repo_ids=["repo-a"])
    )

    assert payload["repositories"][0]["overview"]["repo_id"] == "repo-a"
    assert payload["repositories"][0]["scanned_at"] == "2026-09-16T01:00:00+00:00"
    assert payload["missing_repo_ids"] == []
    assert payload["sync_status"] == SYNC_STATUS_READY


# ─────────────────────────────────────────────────────────────────────────────
# 设置路径
# ─────────────────────────────────────────────────────────────────────────────


def test_settings_fall_back_to_static_default() -> None:
    """无 DB 记录时回落配置里的静态默认值（默认 5 分钟）。"""
    store = _FakeMonitorStore()

    settings = get_monitor_settings(store, default_interval_seconds=300)

    assert settings.sync_enabled is True
    assert settings.sync_interval_seconds == 300
    assert settings.updated_at == ""


def test_settings_read_stored_override() -> None:
    """保存过的运行时覆盖值优先于静态默认值。"""
    store = _FakeMonitorStore()
    update_monitor_settings(store, sync_enabled=False, sync_interval_seconds=900)

    settings = get_monitor_settings(store, default_interval_seconds=300)

    assert settings.sync_enabled is False
    assert settings.sync_interval_seconds == 900


@pytest.mark.parametrize("interval_seconds", [59, 3601, 0, -1])
def test_settings_reject_out_of_range_interval(interval_seconds: int) -> None:
    """合法区间外的间隔被拒绝，不写库。"""
    store = _FakeMonitorStore()

    with pytest.raises(ValueError, match="sync_interval_seconds"):
        update_monitor_settings(store, sync_enabled=True, sync_interval_seconds=interval_seconds)
    assert store.settings is None


@pytest.mark.parametrize("interval_seconds", [60, 300, 3600])
def test_settings_accept_boundary_intervals(interval_seconds: int) -> None:
    """区间边界值 [60, 3600] 都合法并落库。"""
    store = _FakeMonitorStore()

    saved = update_monitor_settings(
        store, sync_enabled=True, sync_interval_seconds=interval_seconds
    )

    assert saved.sync_interval_seconds == interval_seconds
    assert saved.updated_at
    assert store.settings is not None
    assert store.settings.sync_interval_seconds == interval_seconds


def test_settings_save_failure_propagates() -> None:
    """设置持久化失败必须抛给调用方，不能返回成功假象。"""

    class _FailingStore(_FakeMonitorStore):
        def save_monitor_settings(self, settings: MonitorSettingsEntry) -> None:
            raise RuntimeError("settings table locked")

    with pytest.raises(RuntimeError, match="settings table locked"):
        update_monitor_settings(_FailingStore(), sync_enabled=True, sync_interval_seconds=300)


# ─────────────────────────────────────────────────────────────────────────────
# 按仓库扫描协调
# ─────────────────────────────────────────────────────────────────────────────


def test_same_repo_requests_are_deduplicated() -> None:
    """同仓库周期 + 手动并发只执行一次扫描。"""
    scanned_repo_ids: list[str] = []
    scan_lock = threading.Lock()

    def scan_runner(repo_id: str) -> None:
        time.sleep(0.2)
        with scan_lock:
            scanned_repo_ids.append(repo_id)

    coordinator = MonitorSyncCoordinator(scan_runner=scan_runner)
    first_handle = coordinator.request_sync("repo-a")
    second_handle = coordinator.request_sync("repo-a")

    assert first_handle.started is True
    assert second_handle.started is False
    first_handle.wait(timeout_seconds=5)
    second_handle.wait(timeout_seconds=5)
    with scan_lock:
        assert scanned_repo_ids == ["repo-a"]


def test_different_repositories_scan_in_parallel() -> None:
    """不同仓库可以并行：两个扫描必须同时进入临界区。"""
    barrier = threading.Barrier(2, timeout=5)
    scan_failures: list[str] = []

    def scan_runner(repo_id: str) -> None:
        try:
            barrier.wait()
        except Exception as exc:  # noqa: BLE001 - 记录后交给断言。
            scan_failures.append(f"{repo_id}: {exc}")

    coordinator = MonitorSyncCoordinator(scan_runner=scan_runner)
    handle_a = coordinator.request_sync("repo-a")
    handle_b = coordinator.request_sync("repo-b")

    assert handle_a.started is True
    assert handle_b.started is True
    handle_a.wait(timeout_seconds=5)
    handle_b.wait(timeout_seconds=5)
    assert scan_failures == []
    assert coordinator.wait_until_idle(timeout_seconds=5) is True


def test_wait_raises_when_scan_fails() -> None:
    """扫描/持久化失败通过 wait 抛出，供 job 标记为失败。"""

    def scan_runner(repo_id: str) -> None:
        raise RuntimeError(f"gh crashed for {repo_id}")

    coordinator = MonitorSyncCoordinator(scan_runner=scan_runner)
    handle = coordinator.request_sync("repo-a")

    with pytest.raises(MonitorSyncError, match="gh crashed"):
        handle.wait(timeout_seconds=5)


def test_coordinator_rejects_blank_repo_id() -> None:
    """空 repo_id 直接拒绝，避免把空主键写进快照表。"""
    coordinator = MonitorSyncCoordinator(scan_runner=lambda repo_id: None)

    with pytest.raises(ValueError, match="repo_id"):
        coordinator.request_sync("")


def test_in_flight_set_is_exposed_to_readers() -> None:
    """协调器暴露在途仓库，供生命周期与观测使用。"""
    started = threading.Event()
    release = threading.Event()

    def scan_runner(repo_id: str) -> None:
        started.set()
        release.wait(timeout=5)

    coordinator = MonitorSyncCoordinator(scan_runner=scan_runner)
    coordinator.request_sync("repo-a")
    assert started.wait(timeout=5)
    assert coordinator.is_in_flight("repo-a") is True
    assert coordinator.in_flight_repo_ids() == ("repo-a",)
    release.set()
    assert coordinator.wait_until_idle(timeout_seconds=5) is True
    assert coordinator.in_flight_repo_ids() == ()


def test_coordinator_reclaims_finished_thread_handles() -> None:
    """连续扫描后回收已结束的线程句柄，长驻进程不无界累积。

    断言不经过 ``wait_until_idle``（它本身就会回收句柄），而是在扫描结束的
    自然状态下轮询：回收失效时 5 次扫描会留下 5 个句柄，测试变红。
    """
    coordinator = MonitorSyncCoordinator(scan_runner=lambda repo_id: None)

    for index in range(5):
        handle = coordinator.request_sync(f"repo-{index}")
        handle.wait(timeout_seconds=5)

    assert coordinator.in_flight_repo_ids() == ()
    deadline = time.monotonic() + 5.0
    while len(coordinator._threads) > 1 and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)
    assert len(coordinator._threads) <= 1  # noqa: SLF001 - 断言内部句柄回收，非行为契约


def test_snapshot_entry_is_replaceable_for_reporting() -> None:
    """报告里的失败项可逐条替换成可序列化结构（回归护栏）。"""
    store = _FakeMonitorStore(fail_for_repo_ids=frozenset({"repo-b"}))
    report = persist_monitoring_result(store, _overview_payload("repo-a", "repo-b"))
    serialized = json.dumps(report.failed_repo_ids, ensure_ascii=False)
    assert "repo-b" in serialized
    assert replace(store.snapshots["repo-a"], scanned_at="x").scanned_at == "x"
