"""监控快照读取端点与同步设置端点的 HTTP 契约测试。

覆盖 PRD rv-2/rv-3/rv-4 中可通过 HTTP 路径验证的部分：快照端点只读本地库、
按当前 registry 过滤且不触发任何实时扫描；设置端点往返持久化、区间校验、
保存失败不返回成功；手动刷新 job 复用扫描产物写回快照且写失败显式报错。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner as agent_runner_routes
import backend.api.routes.agent_runner_console as console_routes
from backend.api.app import app
from backend.core.shared.interfaces.runner_console import MonitorSnapshotEntry
from backend.core.use_cases.monitor_snapshots import MonitorSyncCoordinator
from backend.infrastructure.config.settings import (
    AgentRunnerConsoleSettings,
    AgentRunnerSettings,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

client = TestClient(app)

SNAPSHOTS_URL = "/api/v1/agent-runner/overview/snapshots"
SETTINGS_URL = "/api/v1/agent-runner/console/monitor/settings"

_REPO_A_SNAPSHOT = {
    "repo_id": "repo-a",
    "display_name": "Repo A",
    "issues": [],
    "anomaly_count": 0,
    "scanned_at": "2026-09-16T01:00:00+00:00",
}

#: registry 中路径失效的仓库（快照响应需要原样透传，供页面显示警示条）。
UNREACHABLE_FIXTURE = [
    {
        "repo_id": "ghost",
        "display_name": "Ghost Repo",
        "configured_path": "/missing/path",
        "error": "Path '/missing/path' does not exist.",
    }
]


@pytest.fixture
def monitor_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """把路由层的 store / 仓库清单 / 配置换成 tmp 隔离的确定性实现。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    monkeypatch.setattr(agent_runner_routes, "create_console_store", lambda: store)
    monkeypatch.setattr(console_routes, "create_console_store", lambda: store)
    monkeypatch.setattr(
        agent_runner_routes,
        "_resolve_enabled_repositories",
        lambda: (["repo-a", "repo-b"], UNREACHABLE_FIXTURE),
    )
    monkeypatch.setattr(
        console_routes,
        "load_fresh_agent_runner_settings",
        lambda: AgentRunnerSettings(
            console=AgentRunnerConsoleSettings(monitor_sync_interval_seconds=300)
        ),
    )
    # 快照读取路径不允许现场扫描 GitHub：任何调用直接判失败。
    monkeypatch.setattr(
        agent_runner_routes,
        "_build_overview_response",
        lambda repo_ids=None: pytest.fail("快照读取路径不得触发实时扫描"),
    )
    return SimpleNamespace(store=store, db_path=db_path)


def _seed_snapshot(store: SqliteConsoleStore, repo_id: str, scanned_at: str) -> None:
    """写入一份形状与 per-repo overview 一致的快照。"""
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id=repo_id,
            payload_json=json.dumps({**_REPO_A_SNAPSHOT, "repo_id": repo_id}),
            scanned_at=scanned_at,
        )
    )


def _read_snapshot_scanned_at(db_path: Path, repo_id: str) -> tuple[str] | None:
    """用独立连接读取某仓库的 ``scanned_at``（不复用路由层实例的内存状态）。"""
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT scanned_at FROM monitoring_snapshots WHERE repo_id = ?",
            (repo_id,),
        ).fetchone()
    finally:
        connection.close()
    return row


# ─────────────────────────────────────────────────────────────────────────────
# GET /overview/snapshots
# ─────────────────────────────────────────────────────────────────────────────


def test_snapshots_endpoint_serves_local_snapshot(monitor_environment: SimpleNamespace) -> None:
    """首屏数据来自本地快照：未扫描的启用仓库进入缺失清单，禁用仓库被过滤。"""
    _seed_snapshot(monitor_environment.store, "repo-a", "2026-09-16T01:00:00+00:00")
    _seed_snapshot(monitor_environment.store, "retired-repo", "2026-09-15T01:00:00+00:00")

    response = client.get(SNAPSHOTS_URL)

    assert response.status_code == 200
    payload = response.json()
    assert [entry["repo_id"] for entry in payload["repositories"]] == ["repo-a"]
    assert payload["repositories"][0]["overview"]["repo_id"] == "repo-a"
    assert payload["repositories"][0]["scanned_at"] == "2026-09-16T01:00:00+00:00"
    assert payload["missing_repo_ids"] == ["repo-b"]
    assert payload["sync_status"] == "partial"
    assert payload["scanned_at"] == "2026-09-16T01:00:00+00:00"
    # registry 解析失败项原样透传，dashboard 据此显示"无法访问"警示。
    assert payload["unreachable_repositories"] == UNREACHABLE_FIXTURE


def test_snapshots_endpoint_reports_pending_first_sync(
    monitor_environment: SimpleNamespace,
) -> None:
    """全新环境返回 pending_first_sync，前端据此显示"尚未同步"空态。"""
    response = client.get(SNAPSHOTS_URL)

    assert response.status_code == 200
    payload = response.json()
    assert payload["repositories"] == []
    assert payload["missing_repo_ids"] == ["repo-a", "repo-b"]
    assert payload["sync_status"] == "pending_first_sync"
    assert payload["scanned_at"] is None


# ─────────────────────────────────────────────────────────────────────────────
# GET / PATCH /console/monitor/settings
# ─────────────────────────────────────────────────────────────────────────────


def test_monitor_settings_get_falls_back_to_static_default(
    monitor_environment: SimpleNamespace,
) -> None:
    """无 DB 记录时返回配置里的静态默认值（默认 5 分钟、开启）。"""
    response = client.get(SETTINGS_URL)

    assert response.status_code == 200
    assert response.json() == {
        "sync_enabled": True,
        "sync_interval_seconds": 300,
        "updated_at": "",
    }


def test_monitor_settings_patch_persists_and_reads_back(
    monitor_environment: SimpleNamespace,
) -> None:
    """PATCH 落库后，新 HTTP 请求与独立连接都能拿到新值（重启保持的前提）。"""
    patch_response = client.patch(
        SETTINGS_URL,
        json={"sync_enabled": False, "sync_interval_seconds": 900},
    )

    assert patch_response.status_code == 200
    saved = patch_response.json()
    assert saved["sync_enabled"] is False
    assert saved["sync_interval_seconds"] == 900
    assert saved["updated_at"]

    read_back = client.get(SETTINGS_URL).json()
    assert read_back["sync_enabled"] is False
    assert read_back["sync_interval_seconds"] == 900

    connection = sqlite3.connect(str(monitor_environment.db_path))
    try:
        row = connection.execute(
            "SELECT sync_enabled, sync_interval_seconds FROM monitor_settings WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert row == (0, 900)


@pytest.mark.parametrize("interval_seconds", [59, 3601, 0])
def test_monitor_settings_patch_rejects_out_of_range_interval(
    monitor_environment: SimpleNamespace,
    interval_seconds: int,
) -> None:
    """合法区间 [60, 3600] 之外的值被拒绝且不写库。"""
    response = client.patch(
        SETTINGS_URL,
        json={"sync_enabled": True, "sync_interval_seconds": interval_seconds},
    )

    assert response.status_code == 422
    assert monitor_environment.store.get_monitor_settings() is None


def test_monitor_settings_patch_requires_both_fields(
    monitor_environment: SimpleNamespace,
) -> None:
    """缺少字段的 PATCH 被拒绝，避免用默认值静默覆盖用户设置。"""
    response = client.patch(SETTINGS_URL, json={"sync_enabled": False})

    assert response.status_code == 422
    assert monitor_environment.store.get_monitor_settings() is None


def test_monitor_settings_patch_wakes_scheduler(
    monitor_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存成功后必须唤醒调度器，让它按新间隔重算等待窗口。"""
    wake_calls: list[bool] = []
    monkeypatch.setattr(
        console_routes,
        "wake_monitor_scheduler",
        lambda: wake_calls.append(True),
    )

    response = client.patch(
        SETTINGS_URL,
        json={"sync_enabled": True, "sync_interval_seconds": 600},
    )

    assert response.status_code == 200
    assert wake_calls == [True]


def test_monitor_settings_patch_reports_persist_failure(
    monitor_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写库失败必须返回错误，不能返回成功假象。"""

    def failing_save(settings: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(monitor_environment.store, "save_monitor_settings", failing_save)

    response = client.patch(
        SETTINGS_URL,
        json={"sync_enabled": True, "sync_interval_seconds": 600},
    )

    assert response.status_code == 500
    assert "disk full" in response.json()["detail"]
    assert monitor_environment.store.get_monitor_settings() is None


# ─────────────────────────────────────────────────────────────────────────────
# 手动刷新 job：写回快照且不重复扫描
# ─────────────────────────────────────────────────────────────────────────────


def _install_job(monkeypatch: pytest.MonkeyPatch, repo_ids: list[str]) -> dict:
    """装配一个走共享协调器的 overview job，并返回可变 job 记录。"""
    monkeypatch.setattr(agent_runner_routes, "_OVERVIEW_JOBS", {})
    monkeypatch.setattr(
        agent_runner_routes,
        "_MONITOR_COORDINATOR",
        MonitorSyncCoordinator(scan_runner=agent_runner_routes._scan_repository_and_persist),
    )
    job: dict = {
        "status": "queued",
        "repo_ids": repo_ids,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "payload": None,
    }
    agent_runner_routes._OVERVIEW_JOBS["job-under-test"] = job
    return job


def test_overview_job_persists_snapshot_without_rescanning(
    monitor_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """手动刷新复用扫描产物写回快照：每个仓库只扫描一次，不为写库再扫。"""
    scan_calls: list[list[str] | None] = []

    def fake_build_overview(repo_ids: list[str] | None = None) -> dict:
        scan_calls.append(repo_ids)
        return {
            "repositories": [
                {
                    **_REPO_A_SNAPSHOT,
                    "repo_id": repo_id,
                    "scanned_at": "2026-09-16T02:00:00+00:00",
                }
                for repo_id in (repo_ids or [])
            ],
            "scanned_at": "2026-09-16T02:00:00+00:00",
            "unreachable_repositories": [],
        }

    monkeypatch.setattr(agent_runner_routes, "_build_overview_response", fake_build_overview)
    job = _install_job(monkeypatch, ["repo-a"])

    agent_runner_routes._run_overview_job("job-under-test", ["repo-a"])

    assert job["status"] == "completed"
    assert scan_calls == [["repo-a"]]
    assert _read_snapshot_scanned_at(monitor_environment.db_path, "repo-a") == (
        "2026-09-16T02:00:00+00:00",
    )
    assert [entry["repo_id"] for entry in job["payload"]["repositories"]] == ["repo-a"]


def test_overview_job_fails_when_snapshot_write_fails(
    monitor_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写库失败时 job 显式失败，旧快照仍可从新连接读回。"""
    old_scanned_at = "2026-09-16T01:00:00+00:00"
    _seed_snapshot(monitor_environment.store, "repo-a", old_scanned_at)

    monkeypatch.setattr(
        agent_runner_routes,
        "_build_overview_response",
        lambda repo_ids=None: {
            "repositories": [{**_REPO_A_SNAPSHOT, "scanned_at": "2026-09-16T03:00:00+00:00"}],
            "scanned_at": "2026-09-16T03:00:00+00:00",
            "unreachable_repositories": [],
        },
    )

    def failing_upsert(entry: object) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr(monitor_environment.store, "upsert_monitor_snapshot", failing_upsert)
    job = _install_job(monkeypatch, ["repo-a"])

    agent_runner_routes._run_overview_job("job-under-test", ["repo-a"])

    assert job["status"] == "failed"
    assert "db locked" in job["error"]
    # 旧快照未被清空：独立连接里仍是失败前的那一份 scanned_at。
    assert _read_snapshot_scanned_at(monitor_environment.db_path, "repo-a") == (old_scanned_at,)
