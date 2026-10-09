"""Tests for the backlog API routes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_backlog as backlog_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
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


def test_list_backlog_prds(backlog_environment) -> None:
    """GET /backlog/prds should return scanned PRDs."""
    response = client.get(
        "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["repo_id"] == "keda-main"
    assert len(data["prds"]) == 1
    assert data["prds"][0]["title"] == "Test Feature"


def test_backlog_cache_reused_after_slow_build(
    backlog_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构建耗时超过 TTL 时缓存仍须命中，否则前端每轮轮询都会全量重扫。"""
    build_count = 0

    def slow_build(repo_id: str, include_archived: bool) -> dict:
        nonlocal build_count
        build_count += 1
        time.sleep(0.3)
        return {
            "prds": [],
            "skipped": [],
            "repo_id": repo_id,
            "include_archived": include_archived,
            "scanned_at": "",
        }

    backlog_routes._BACKLOG_CACHE.clear()
    monkeypatch.setattr(backlog_routes, "_BACKLOG_CACHE_TTL_SECONDS", 0.2)
    monkeypatch.setattr(backlog_routes, "_build_backlog_response", slow_build)

    for _ in range(2):
        response = client.get(
            "/api/v1/agent-runner/backlog/prds?repo_id=keda-main&include_archived=false"
        )
        assert response.status_code == 200

    assert build_count == 1
    backlog_routes._BACKLOG_CACHE.clear()


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


def test_start_global_requires_valid_parallel(backlog_environment) -> None:
    """Global start must validate max_parallel bounds."""
    response = client.post(
        "/api/v1/agent-runner/backlog/start-global",
        json={"repo_id": "keda-main", "max_parallel": 0},
    )
    assert response.status_code == 422
