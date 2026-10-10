"""Tests for the backlog API routes."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.backlog_sync as backlog_sync
import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.backlog_actions import BacklogActionError
from backend.core.use_cases.backlog_snapshots import (
    build_backlog_task_key,
    persist_backlog_snapshot,
)
from backend.core.use_cases.monitor_snapshots import MonitorSyncCoordinator
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient

client = TestClient(app)


@pytest.fixture
def backlog_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire backlog routes to tmp-backed store, repo, and fake GitHub client."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    repo_dir = tmp_path / "repo"
    pending_dir = repo_dir / "tasks" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "P1-FEAT-20260101-test.md").write_text(
        "# PRD: Test Feature\n\n## Acceptance Checklist\n- [ ] item\n",
        encoding="utf-8",
    )

    contexts = [
        RepositoryRunContext(
            repo_id="keda-main",
            display_name="Keda Main",
            repo_path=repo_dir,
            config=AppConfig(),
        )
    ]
    github_client = FakeGitHubClient()

    monkeypatch.setattr(backlog_routes, "create_backlog_store", lambda: store)
    monkeypatch.setattr(backlog_routes, "_resolve_contexts", lambda: contexts)
    monkeypatch.setattr(backlog_routes, "create_github_client", lambda repo_path: github_client)
    monkeypatch.setattr(
        backlog_routes,
        "create_process_runner",
        lambda: type("FakeRunner", (), {"run": lambda *a, **k: None})(),
    )
    monkeypatch.setattr(
        backlog_routes,
        "create_process_supervisor",
        lambda: type(
            "FakeSupervisor",
            (),
            {
                "list_processes": lambda: [],
                "spawn": lambda **kwargs: type(
                    "Record",
                    (),
                    {
                        "process_id": "fake-id",
                        "repo_id": kwargs.get("repo_id"),
                        "kind": kwargs.get("kind"),
                        "pid": 1234,
                        "status": "running",
                        "exit_code": None,
                        "log_path": "",
                        "command": kwargs.get("argv"),
                        "started_at": "",
                        "stopped_at": None,
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        backlog_routes, "resolve_console_spawn_cwd", lambda repo_id, contexts: tmp_path
    )

    from backend.infrastructure.config.settings import (
        AgentRunnerConsoleSettings,
        AgentRunnerSettings,
    )

    fake_settings = AgentRunnerSettings(
        console=AgentRunnerConsoleSettings(
            runner_command=["echo", "fake"],
        )
    )
    monkeypatch.setattr(backlog_routes, "load_fresh_agent_runner_settings", lambda: fake_settings)

    return {
        "store": store,
        "repo_dir": repo_dir,
        "github_client": github_client,
        "tmp_path": tmp_path,
    }


@dataclass
class _SnapshotWiring:
    """Backlog 快照在路由层的接线：tmp store + 真实协调器 + 请求计数。

    Attributes:
        coordinator: 真实扫描协调器，用于等待在途扫描结束。
        resync_requests: ``request_backlog_resync`` 收到的任务键序列。
    """

    coordinator: MonitorSyncCoordinator
    resync_requests: list[str]


@pytest.fixture
def snapshot_wiring(
    backlog_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> _SnapshotWiring:
    """把快照读写的存储指向用例 tmp 库，并重置进程级单例。

    协调器保持真实实现（含真实扫描线程），因为"读路径不等待扫描"只有在真有在途
    线程时才会被证伪；用例库与真实 console 库隔离，避免测试写到用户磁盘。
    """
    store = backlog_environment["store"]
    coordinator = MonitorSyncCoordinator(
        scan_runner=backlog_sync._scan_backlog_variant_and_persist  # noqa: SLF001
    )
    requests: list[str] = []

    def recording_resync(repo_id: str, *, include_archived: bool = False) -> None:
        task_key = build_backlog_task_key(repo_id, include_archived=include_archived)
        requests.append(task_key)
        coordinator.request_sync(task_key)

    monkeypatch.setattr(backlog_sync, "get_backlog_snapshot_store", lambda: store)
    monkeypatch.setattr(backlog_sync, "get_backlog_sync_coordinator", lambda: coordinator)
    monkeypatch.setattr(backlog_routes, "request_backlog_resync", recording_resync)
    monkeypatch.setattr(backlog_sync, "_BACKLOG_COORDINATOR", None)
    monkeypatch.setattr(backlog_sync, "_BACKLOG_SCHEDULER", None)

    yield _SnapshotWiring(coordinator=coordinator, resync_requests=requests)

    coordinator.wait_until_idle(timeout_seconds=5)


def test_list_backlog_prds(backlog_environment, snapshot_wiring: _SnapshotWiring) -> None:
    """GET /backlog/prds 读本地快照：首读空态 + stale，后台扫描落库后再读即命中快照。"""
    first = client.get("/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false")
    assert first.status_code == 200
    assert first.json()["prds"] == []
    assert first.json()["stale"] is True
    assert first.json()["scanned_at"] is None
    assert snapshot_wiring.coordinator.wait_until_idle(timeout_seconds=5)

    response = client.get(
        "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["repo_id"] == "keda-main"
    assert data["stale"] is False
    assert data["scanned_at"]
    assert len(data["prds"]) == 1
    assert data["prds"][0]["title"] == "Test Feature"


def test_list_backlog_prds_serves_snapshot_without_rescanning(
    backlog_environment,
    snapshot_wiring: _SnapshotWiring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快照落库后反复轮询都是纯本地读：不再触发扫描，否则 30s 轮询会打爆 GitHub。"""
    client.get("/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false")
    assert snapshot_wiring.coordinator.wait_until_idle(timeout_seconds=5)

    build_calls: list[tuple[str, bool]] = []

    def counting_build(repo_id: str, include_archived: bool) -> dict:
        build_calls.append((repo_id, include_archived))
        return {"prds": [], "skipped": [], "repo_id": repo_id, "include_archived": include_archived}

    monkeypatch.setattr(backlog_routes, "_build_backlog_response", counting_build)

    for _ in range(3):
        response = client.get(
            "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
        )
        assert response.status_code == 200
        assert response.json()["stale"] is False

    assert build_calls == []
    assert snapshot_wiring.coordinator.in_flight_repo_ids() == ()


def test_github_failure_keeps_previous_backlog_snapshot(
    backlog_environment,
    snapshot_wiring: _SnapshotWiring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 GET 触发的扫描遇到 GitHub 异常时仍返回并保留旧快照。"""
    previous_payload = {
        "prds": [
            {
                "prd_path": "tasks/pending/old.md",
                "title": "Old Snapshot",
                "status": "pending",
                "priority": "P1",
                "issue_url": "https://github.com/org/repo/issues/17",
                "issue_number": 17,
                "state": "ready",
                "acceptance_total": 1,
                "acceptance_checked": 0,
                "delivery_dependencies": [],
                "updated_at": "2026-10-08T00:00:00+00:00",
                "block_reason": None,
                "next_action": None,
            }
        ],
        "skipped": [],
        "repo_id": "keda-main",
        "include_archived": False,
        "scanned_at": "2020-01-01T00:00:00+00:00",
    }
    persist_backlog_snapshot(
        backlog_environment["store"],
        repo_id="keda-main",
        include_archived=False,
        payload=previous_payload,
    )
    (backlog_environment["repo_dir"] / "tasks/pending/P1-FEAT-20260101-test.md").write_text(
        "# PRD: Test Feature\n\n- GitHub Issue: https://github.com/org/repo/issues/17\n",
        encoding="utf-8",
    )

    def fail_get_issue(_issue_number: int) -> None:
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(backlog_environment["github_client"], "get_issue", fail_get_issue)

    response = client.get(
        "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
    )

    assert response.status_code == 200
    assert response.json()["prds"][0]["title"] == "Old Snapshot"
    assert response.json()["stale"] is True
    assert snapshot_wiring.coordinator.wait_until_idle(timeout_seconds=5)
    stored_snapshot = backlog_environment["store"].get_backlog_snapshot(
        repo_id="keda-main", include_archived=False
    )
    assert stored_snapshot is not None
    assert json.loads(stored_snapshot.payload_json) == previous_payload


def test_backlog_prds_rejects_unknown_repo_without_leaking_its_snapshot(
    backlog_environment, snapshot_wiring: _SnapshotWiring
) -> None:
    """禁用/已删除仓库仍返回 400，其历史快照不得回流到任何页面。"""
    persist_backlog_snapshot(
        backlog_environment["store"],
        repo_id="ghost-repo",
        include_archived=False,
        payload={
            "prds": [{"title": "Ghost PRD"}],
            "skipped": [],
            "repo_id": "ghost-repo",
            "include_archived": False,
            "scanned_at": "2026-10-08T10:00:00+00:00",
        },
    )

    response = client.get("/api/v1/agent-runner/backlog/prds?repo_id=ghost-repo")

    assert response.status_code == 400
    assert snapshot_wiring.resync_requests == []
    assert snapshot_wiring.coordinator.in_flight_repo_ids() == ()


def test_archived_variant_builds_on_demand(
    backlog_environment, snapshot_wiring: _SnapshotWiring
) -> None:
    """``include_archived=true`` 没有预取，首次请求时按需构建并在落库后命中。"""
    first = client.get("/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=true")
    assert first.json()["stale"] is True
    assert first.json()["include_archived"] is True
    assert snapshot_wiring.coordinator.wait_until_idle(timeout_seconds=5)

    second = client.get("/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=true")

    assert second.json()["stale"] is False
    entry = backlog_environment["store"].get_backlog_snapshot(
        repo_id="keda-main", include_archived=True
    )
    assert entry is not None
    default_entry = backlog_environment["store"].get_backlog_snapshot(
        repo_id="keda-main", include_archived=False
    )
    assert default_entry is None


def test_start_prd_triggers_one_resync(
    snapshot_wiring: _SnapshotWiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start 成功后为默认视图派一次后台重扫，取代原先"弹缓存→下次读全量等待"。

    ``start_prd`` 本身用 fake（rv-6 声明的 mock 边界），被测接线是重扫触发。
    """
    monkeypatch.setattr(
        backlog_routes,
        "start_prd",
        lambda **_kwargs: {"repo_id": "keda-main", "prd_path": "tasks/pending/a.md"},
    )
    encoded = _encode_prd_path("tasks/pending/P1-FEAT-20260101-test.md")

    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/start", json={"repo_id": "keda-main"}
    )

    assert response.status_code == 200, response.text
    assert snapshot_wiring.resync_requests == ["keda-main"]
    assert snapshot_wiring.coordinator.wait_until_idle(timeout_seconds=5)


def test_global_start_triggers_one_resync(
    snapshot_wiring: _SnapshotWiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全局开始同样只派一次重扫，且扫描动作不在请求线程里。"""
    monkeypatch.setattr(
        backlog_routes, "start_global_backlog", lambda **_kwargs: {"started": ["a"]}
    )

    response = client.post(
        "/api/v1/agent-runner/backlog/start-global",
        json={"repo_id": "keda-main", "max_parallel": 1},
    )

    assert response.status_code == 200, response.text
    assert snapshot_wiring.resync_requests == ["keda-main"]


def test_enqueue_ready_triggers_one_resync(
    snapshot_wiring: _SnapshotWiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加入就绪队列后重扫一次，让 backlog 快照反映新的队列状态。"""
    monkeypatch.setattr(
        backlog_routes,
        "enqueue_prd_ready",
        lambda **_kwargs: {"repo_id": "keda-main", "prd_path": "tasks/pending/a.md"},
    )
    encoded = _encode_prd_path("tasks/pending/P1-FEAT-20260101-test.md")

    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/enqueue-ready",
        json={"repo_id": "keda-main"},
    )

    assert response.status_code == 200, response.text
    assert snapshot_wiring.resync_requests == ["keda-main"]


def test_failed_start_does_not_request_a_resync(
    snapshot_wiring: _SnapshotWiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start 失败时不得派重扫：为一次没发生的状态变化重扫只会白烧 GitHub 配额。"""
    monkeypatch.setattr(
        backlog_routes,
        "start_prd",
        lambda **_kwargs: (_ for _ in ()).throw(BacklogActionError("boom")),
    )
    encoded = _encode_prd_path("tasks/pending/P1-FEAT-20260101-test.md")

    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/start", json={"repo_id": "keda-main"}
    )

    assert response.status_code == 400
    assert snapshot_wiring.resync_requests == []


def _encode_prd_path(prd_path: str) -> str:
    """按路由约定把 PRD 路径编码成 URL 安全的形式。"""
    return base64.urlsafe_b64encode(prd_path.encode("utf-8")).decode("ascii")


def test_update_settings(backlog_environment) -> None:
    """PATCH /backlog/settings should persist settings."""
    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": 3, "default_view": "list"},
    )
    assert response.status_code == 200
    assert response.json()["max_parallel"] == 3

    response = client.get("/api/v1/agent-runner/backlog/settings?repo_id=keda-main")
    assert response.status_code == 200
    assert response.json()["max_parallel"] == 3


def test_clear_settings_restores_inheritance(backlog_environment) -> None:
    """PATCH max_parallel=null 应删除设置行（恢复继承），fresh 读回策略为空。"""
    store: SqliteConsoleStore = backlog_environment["store"]
    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": 3, "default_view": "timeline"},
    )
    assert response.status_code == 200
    assert store.get_backlog_settings("keda-main") is not None

    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": None},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["max_parallel"] is None
    assert payload["ceiling_source"] == "inherited"
    assert store.get_backlog_settings("keda-main") is None

    # 恢复继承是删行语义，重复清除保持幂等成功。
    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": None},
    )
    assert response.status_code == 200
    assert response.json()["max_parallel"] is None

    response = client.get("/api/v1/agent-runner/backlog/settings?repo_id=keda-main")
    assert response.status_code == 200
    assert response.json()["max_parallel"] is None


def test_default_view_only_patch_persists_only_with_policy_row(backlog_environment) -> None:
    """视图偏好与策略值同居一行：没有策略行时 PATCH 只带 default_view 不落库。

    ``backlog_settings.max_parallel`` 是 NOT NULL 且哨兵值已被否决（PRD D-04），
    所以「未设置」态无法单独持久视图偏好；响应必须如实回读 ``list``，而不是
    回显请求值骗过页面。建立策略行后同一请求才真正持久化，且不得改写策略。
    """
    store: SqliteConsoleStore = backlog_environment["store"]

    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"default_view": "timeline"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert store.get_backlog_settings("keda-main") is None
    assert payload["default_view"] == "list"
    assert payload["max_parallel"] is None

    client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": 3, "default_view": "list"},
    )
    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"default_view": "timeline"},
    )
    assert response.status_code == 200
    assert response.json()["default_view"] == "timeline"
    assert response.json()["max_parallel"] == 3
    assert store.get_backlog_settings("keda-main").max_parallel == 3


def test_empty_settings_patch_is_no_op(backlog_environment) -> None:
    """省略全部字段的 PATCH 既不落库也不删行：响应只是 fresh 读回。"""
    store: SqliteConsoleStore = backlog_environment["store"]
    client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={"max_parallel": 4, "default_view": "timeline"},
    )

    response = client.patch(
        "/api/v1/agent-runner/backlog/settings?repo_id=keda-main",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["max_parallel"] == 4
    assert response.json()["default_view"] == "timeline"
    assert store.get_backlog_settings("keda-main").max_parallel == 4


def test_start_prd_rejects_missing_repo(backlog_environment) -> None:
    """Starting a PRD for an unknown repo must return 400."""
    import base64

    encoded = base64.urlsafe_b64encode(b"tasks/pending/P1-FEAT-20260101-test.md").decode("ascii")
    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/start",
        json={"repo_id": "unknown"},
    )
    assert response.status_code == 400


def test_start_prd_rejects_direct_pr_with_cli_equivalent_reason(
    backlog_environment, monkeypatch
) -> None:
    """直出 PR 在 HTTP 边界即 400 拒绝，不留「已启动但立刻用法错误退出」的幽灵进程。"""
    import base64
    from types import SimpleNamespace

    encoded = base64.urlsafe_b64encode(b"tasks/pending/P1-FEAT-20260101-test.md").decode("ascii")
    spawn_calls: list[dict] = []

    def _record_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return SimpleNamespace(process_id="fake-id", status="running", command=kwargs.get("argv"))

    monkeypatch.setattr(
        backlog_routes,
        "create_process_supervisor",
        lambda: type(
            "RecordingSupervisor",
            (),
            {"list_processes": lambda: [], "spawn": staticmethod(_record_spawn)},
        )(),
    )

    response = client.post(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/start",
        json={"repo_id": "keda-main", "direct_pr": True},
    )

    assert response.status_code == 400, response.text
    assert "--direct-pr" in response.json()["detail"]
    assert spawn_calls == []
    github_client = backlog_environment["github_client"]
    assert not [c for c in github_client.calls if c["method"] == "edit_issue_labels"]


def test_start_global_drops_max_parallel_and_never_persists_settings(
    backlog_environment,
) -> None:
    """全局开始不再吃请求体 ``max_parallel``：上限由服务端解析，且不落设置行。"""
    store: SqliteConsoleStore = backlog_environment["store"]

    response = client.post(
        "/api/v1/agent-runner/backlog/start-global",
        json={"repo_id": "keda-main", "max_parallel": 0},
    )

    # 多余的 max_parallel 不再是契约字段：被忽略而不是 422，批量按生效上限执行。
    assert response.status_code == 200, response.text
    # 「全局开始」是一次性批量，绝不把请求参数写成仓库设置。
    assert store.get_backlog_settings("keda-main") is None
