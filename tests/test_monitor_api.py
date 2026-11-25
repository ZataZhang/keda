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
from backend.core.shared.interfaces.runner_console import MonitorSnapshotEntry, RunRecord
from backend.core.use_cases.monitor_snapshots import MonitorSyncCoordinator
from backend.infrastructure.config.settings import (
    AgentRunnerConsoleSettings,
    AgentRunnerSettings,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.support.relative_time import utc_minutes_ago

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


def test_tracked_issue_scan_deduplicates_run_history_before_issue_cap(
    monitor_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复运行记录不能占满名额并漏掉较早但仍在窗口内的 Issue。"""
    run_started_at = utc_minutes_ago(5).isoformat()
    monitor_environment.store.append_run(
        RunRecord(
            repo_id="repo-a",
            repo_path="/tmp/repo-a",
            issue_number=10,
            trigger="console_run",
            agent="qoder",
            outcome="failed",
            error_summary=None,
            started_at=run_started_at,
            finished_at=run_started_at,
            duration_seconds=1.0,
        )
    )
    for _ in range(20):
        monitor_environment.store.append_run(
            RunRecord(
                repo_id="repo-a",
                repo_path="/tmp/repo-a",
                issue_number=59,
                trigger="console_run",
                agent="qoder",
                outcome="failed",
                error_summary=None,
                started_at=run_started_at,
                finished_at=run_started_at,
                duration_seconds=1.0,
            )
        )
    monkeypatch.setattr(
        agent_runner_routes,
        "create_console_store",
        lambda: monitor_environment.store,
    )

    tracked_issue_numbers = agent_runner_routes._recent_tracked_issue_numbers(
        [SimpleNamespace(repo_id="repo-a")]
    )

    assert tracked_issue_numbers == {"repo-a": [59, 10]}


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


@pytest.mark.parametrize("interval_seconds", [299, 3601, 0])
def test_monitor_settings_patch_rejects_out_of_range_interval(
    monitor_environment: SimpleNamespace,
    interval_seconds: int,
) -> None:
    """合法区间 [300, 3600] 之外的值被拒绝且不写库。"""
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

    def fake_build_overview(repo_ids: list[str] | None = None, **_kwargs) -> dict:
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
        lambda repo_ids=None, **_kwargs: {
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


def test_cli_issue_refresh_updates_local_snapshot_without_remote_scan(
    monitor_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实际 CLI 入口只读两条远端记录，旧全仓扫描无法覆盖其结果。"""
    from typer.testing import CliRunner

    from backend.api import cli_typer_monitor
    from backend.api.cli_typer_app import app as cli_app
    from backend.core.shared.models.agent_runner import (
        AppConfig,
        IssueSummary,
        RepositoryRunContext,
    )
    from backend.core.use_cases.monitor_snapshots import persist_monitoring_result

    store = monitor_environment.store
    config = AppConfig()
    repository = RepositoryRunContext("repo-a", "Repo A", Path("."), config)
    calls: list[tuple[str, int]] = []

    def get_issue(number: int) -> IssueSummary:
        calls.append(("issue", number))
        return IssueSummary(
            number, "Updated", "https://example.test/issues/448", "", (config.labels.supervising,)
        )

    def get_pr(number: int) -> dict:
        calls.append(("pr", number))
        return {"number": number, "url": "https://example.test/pull/451", "is_draft": False}

    github_client = SimpleNamespace(get_issue=get_issue, get_pull_request_monitor_state=get_pr)
    monkeypatch.setattr(cli_typer_monitor, "create_console_store", lambda: store)
    monkeypatch.setattr(cli_typer_monitor, "create_github_client", lambda _path: github_client)
    monkeypatch.setattr(
        cli_typer_monitor,
        "resolve_repository_targets_with_diagnostics",
        lambda _: ([repository], []),
    )
    old_issue = {
        "number": 448,
        "title": "Before",
        "url": "https://example.test/issues/448",
        "labels": [config.labels.blocked],
        "state": "OPEN",
        "primary_label": config.labels.blocked,
        "pr": {"number": 451, "is_draft": True},
    }
    old_snapshot = {
        **_REPO_A_SNAPSHOT,
        "issues": [old_issue],
        "labels": {"blocked": config.labels.blocked, "supervising": config.labels.supervising},
    }
    overview = {"repositories": [old_snapshot]}
    assert persist_monitoring_result(store, overview).ok
    revision_url = "/api/v1/agent-runner/overview/issue-revision"
    original_revision = client.get(revision_url).json()["revision"]
    old_scan_start = time.time_ns()

    result = CliRunner().invoke(
        cli_app, ["monitor", "refresh", "--repo-id", "repo-a", "--issue", "448"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [("issue", 448), ("pr", 451)]
    assert client.get(revision_url).json()["revision"] != original_revision
    assert persist_monitoring_result(store, overview, scan_started_ns=old_scan_start).ok

    response = client.get(SNAPSHOTS_URL).json()
    row = response["repositories"][0]["overview"]["issues"][0]
    assert row["labels"] == [config.labels.supervising]
    assert row["pr"]["is_draft"] is False
    assert response["scanned_at"] == old_snapshot["scanned_at"]
    assert calls == [("issue", 448), ("pr", 451)]

    assert persist_monitoring_result(
        store,
        {"repositories": [{**old_snapshot, "issues": [row]}]},
        scan_started_ns=time.time_ns(),
    ).ok
    assert store.get_monitor_issue_refresh("repo-a", 448) is None


def test_simultaneous_issue_refreshes_share_remote_reads(tmp_path: Path) -> None:
    """两个存储实例收到同一 Issue 的并发请求时只观察远端一次。"""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from backend.core.shared.models.agent_runner import (
        AppConfig,
        IssueSummary,
        RepositoryRunContext,
    )
    from backend.core.use_cases.monitor_issue_refresh import (
        IssueRefreshTarget,
        refresh_monitor_issue,
    )

    first_store = SqliteConsoleStore(tmp_path / "console.db")
    second_store = SqliteConsoleStore(tmp_path / "console.db")
    first_read_started = Event()
    second_waiting = Event()
    calls: list[int] = []

    def get_issue(number: int) -> IssueSummary:
        calls.append(number)
        first_read_started.set()
        assert second_waiting.wait(timeout=5)
        return IssueSummary(number, "Concurrent refresh", "https://example.test/issues/448", "", ())

    original_lock = second_store.monitor_issue_refresh_lock

    @contextmanager
    def second_lock(repo_id: str, issue_number: int):
        second_waiting.set()
        with original_lock(repo_id, issue_number):
            yield

    second_store.monitor_issue_refresh_lock = second_lock
    target = IssueRefreshTarget(
        RepositoryRunContext("repo-a", "Repo A", tmp_path, AppConfig()), 448
    )
    github_client = SimpleNamespace(get_issue=get_issue)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(refresh_monitor_issue, first_store, github_client, target)
        assert first_read_started.wait(timeout=5)
        second = pool.submit(refresh_monitor_issue, second_store, github_client, target)
        assert first.result(timeout=5).observed_ns == second.result(timeout=5).observed_ns
    assert calls == [448]
