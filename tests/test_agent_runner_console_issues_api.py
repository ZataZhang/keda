"""Console Issue 操作端点（Issue #247 新增路由）HTTP 层测试。

覆盖 ``agent_runner_console_issues`` 路由器：全量 Issue 列表、标签读写、
启动候选清单、一句话建 Issue。业务语义已在 ``test_console_cli_parity`` 覆盖，
这里只验证 HTTP 契约：状态码映射、参数校验、上下文解析、审计落库。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console_issues as issues_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RepositoryRunContext,
)
from backend.core.use_cases.console_issue_creation import ConsoleCreatedIssue
from tests.conftest import FakeBacklogStore, FakeGitHubClient, FakeProcessRunner

client = TestClient(app)

REPO_ID = "keda-main"
PREFIX = "/api/v1/agent-runner/console"


@pytest.fixture
def issues_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 Issue 路由的工厂打桩到内存替身，返回可断言的 client/store。"""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    contexts = [
        RepositoryRunContext(
            repo_id=REPO_ID,
            display_name="Keda Main",
            repo_path=repo_dir,
            config=AppConfig(),
        )
    ]
    github_client = FakeGitHubClient()
    store = FakeBacklogStore(repo_id=REPO_ID)

    monkeypatch.setattr(issues_routes, "load_fresh_agent_runner_settings", lambda: object())
    monkeypatch.setattr(
        issues_routes,
        "resolve_repository_targets_with_diagnostics",
        lambda settings: (contexts, []),
    )
    monkeypatch.setattr(issues_routes, "create_github_client", lambda path, runner: github_client)
    monkeypatch.setattr(issues_routes, "create_process_runner", lambda: FakeProcessRunner())
    monkeypatch.setattr(issues_routes, "create_console_store", lambda: store)
    return {"client": github_client, "store": store, "contexts": contexts}


# ── 全量 Issue 列表 ──────────────────────────────────────────────────────────


def test_list_issues_endpoint_marks_monitored(issues_environment) -> None:
    issues_environment["client"].set_list_issues_by_label_result(
        [
            IssueSummary(
                number=1,
                title="a",
                url="https://github.com/example/repo/issues/1",
                body="",
                labels=("agent/ready",),
            ),
            IssueSummary(
                number=2,
                title="b",
                url="https://github.com/example/repo/issues/2",
                body="",
                labels=("bug",),
            ),
        ]
    )

    response = client.get(f"{PREFIX}/repositories/{REPO_ID}/issues")

    assert response.status_code == 200, response.text
    issues = response.json()["issues"]
    assert {item["number"]: item["monitored"] for item in issues} == {1: True, 2: False}


def test_list_issues_endpoint_rejects_bad_limit(issues_environment) -> None:
    response = client.get(f"{PREFIX}/repositories/{REPO_ID}/issues?limit=9999")
    assert response.status_code == 422


def test_list_issues_endpoint_unknown_repo(issues_environment) -> None:
    response = client.get(f"{PREFIX}/repositories/does-not-exist/issues")
    assert response.status_code == 404


# ── 标签读与写 ────────────────────────────────────────────────────────────────


def test_get_issue_labels_endpoint(issues_environment) -> None:
    issues_environment["client"].set_issue_labels(12, ("agent/running",))

    response = client.get(f"{PREFIX}/repositories/{REPO_ID}/issues/12/labels")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["labels"] == ["agent/running"]
    assert "agent/ready" in payload["allowed_labels"]


def test_put_issue_labels_in_set_succeeds_and_audits(issues_environment) -> None:
    issues_environment["client"].set_issue_labels(13, ("agent/failed",))

    response = client.put(
        f"{PREFIX}/repositories/{REPO_ID}/issues/13/labels",
        json={"add": ["agent/ready"], "remove": ["agent/failed"]},
    )

    assert response.status_code == 200, response.text
    assert "agent/ready" in response.json()["labels"]
    assert issues_environment["store"].audits[-1].action == "update_issue_labels"


def test_put_issue_labels_out_of_set_rejected_422(issues_environment) -> None:
    response = client.put(
        f"{PREFIX}/repositories/{REPO_ID}/issues/14/labels",
        json={"add": ["agent/redy"], "remove": []},
    )
    assert response.status_code == 422
    assert not [c for c in issues_environment["client"].calls if c["method"] == "edit_issue_labels"]


# ── 启动候选清单 ──────────────────────────────────────────────────────────────


def test_launch_options_endpoint_returns_candidates(issues_environment) -> None:
    response = client.get(f"{PREFIX}/repositories/{REPO_ID}/launch-options")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agents"] == sorted(issues_environment["contexts"][0].config.agents)
    assert payload["presets"] == []


# ── 一句话建 Issue ────────────────────────────────────────────────────────────


def test_create_issue_from_prompt_endpoint(issues_environment, monkeypatch) -> None:
    monkeypatch.setattr(
        issues_routes,
        "create_issue_from_prompt_for_console",
        lambda **kwargs: ConsoleCreatedIssue(
            number=77, issue_url="https://github.com/example/repo/issues/77"
        ),
    )

    response = client.post(
        f"{PREFIX}/repositories/{REPO_ID}/issues",
        json={"prompt_text": "给网页加一个复核按钮", "issue_type": "feature"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["number"] == 77
    assert issues_environment["store"].audits[-1].action == "create_issue_from_prompt"


def test_create_issue_from_prompt_endpoint_value_error_400(issues_environment, monkeypatch) -> None:
    def _raise(**kwargs):
        raise ValueError("bad prompt")

    monkeypatch.setattr(issues_routes, "create_issue_from_prompt_for_console", _raise)

    response = client.post(
        f"{PREFIX}/repositories/{REPO_ID}/issues",
        json={"prompt_text": "x", "issue_type": "feature"},
    )
    assert response.status_code == 400
