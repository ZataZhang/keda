"""真实 HTTP 路由与磁盘配置验证超时设置，不依赖新增帮助函数。"""

import json
import subprocess
import tomllib

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app

URL = "/api/v1/agent-runner/lifecycle-settings"


@pytest.fixture
def settings_environment(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    local = repo / ".kedacode.toml"
    local.write_text(
        '[agent_runner]\n[agent_runner.repository]\nbase_branch="preserved"\n', encoding="utf-8"
    )
    global_config = tmp_path / "config.toml"
    global_config.write_text(
        "[agent_runner.repositories.fixture]\npath=" + json.dumps(str(repo)) + "\nenabled=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(global_config))
    return TestClient(app), global_config, local


def test_defaults_are_visible(settings_environment):
    client, _, _ = settings_environment
    response = client.get(URL, params={"scope": "global"})
    assert response.status_code == 200
    targets = response.json().get("content_generation_timeouts", {}).get("targets", [])
    assert {entry["key"]: entry["timeout_seconds"] for entry in targets} == {
        "issue_from_prd": 600,
        "issue_from_prompt": 600,
        "draft_pr": 1200,
        "prd_from_issue": 600,
    }


def test_global_repository_override_and_reset(settings_environment):
    client, global_config, local = settings_environment
    endpoint = URL + "/content-generation-timeouts"
    assert (
        client.put(endpoint, json={"scope": "global", "values": {"draft_pr": 1800}}).status_code
        == 200
    )
    global_bytes = global_config.read_bytes()
    response = client.put(
        endpoint, json={"scope": "repository", "repo_id": "fixture", "values": {"draft_pr": 2400}}
    )
    assert response.status_code == 200
    assert (
        tomllib.loads(local.read_text(encoding="utf-8"))["agent_runner"]["generated_content"][
            "draft_pr"
        ]["timeout_seconds"]
        == 2400
    )
    response = client.put(
        endpoint, json={"scope": "repository", "repo_id": "fixture", "values": {"draft_pr": None}}
    )
    assert response.status_code == 200
    target = next(
        entry
        for entry in response.json()["content_generation_timeouts"]["targets"]
        if entry["key"] == "draft_pr"
    )
    assert target["timeout_seconds"] == target["global_timeout_seconds"] == 1800
    assert global_config.read_bytes() == global_bytes
    assert (
        tomllib.loads(local.read_text(encoding="utf-8"))["agent_runner"]["repository"][
            "base_branch"
        ]
        == "preserved"
    )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"unknown": 900},
        {"draft_pr": 0},
        {"draft_pr": 86401},
        {"draft_pr": True},
        {"draft_pr": 1.5},
    ],
)
def test_invalid_updates_leave_both_files_unchanged(settings_environment, values):
    client, global_config, local = settings_environment
    before = global_config.read_bytes(), local.read_bytes()
    response = client.put(
        URL + "/content-generation-timeouts", json={"scope": "global", "values": values}
    )
    assert response.status_code == 422
    assert (global_config.read_bytes(), local.read_bytes()) == before
