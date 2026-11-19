"""个人覆盖 ``.kedacode.local.toml`` 声明的阶段绑定在生命周期设置页的契约测试。

设置页仓库视角只写 ``.kedacode.toml``；绑定若来自个人覆盖，视图必须如实标注来源，
写回必须明确拒绝（而不是报"本层没有这个表"或写进一个不会生效的文件）。
配置走 tmp ``config.toml`` 与真实 git 仓库下的真实 TOML 文件，断言以磁盘内容为准。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app

_SETTINGS_URL = "/api/v1/agent-runner/lifecycle-settings"

_LOCAL_OVERRIDE_TOML = """
[agent_runner.presets.claude-local]
agent = "claude"

[agent_runner.lifecycle_presets]
implementation = "claude-local"
"""


@pytest.fixture
def local_override_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """仓库 ``.kedacode.toml`` 不声明绑定，个人覆盖把 implementation 绑到本机预设。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    repo_config_path = repo_path / ".kedacode.toml"
    repo_config_path.write_text("[agent_runner]\n", encoding="utf-8")
    (repo_path / ".kedacode.local.toml").write_text(_LOCAL_OVERRIDE_TOML, encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n\n"
        "[agent_runner.repositories.testrepo]\n"
        f"path = {json.dumps(str(repo_path.resolve()))}\n"
        "enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))
    return {"client": TestClient(app), "repo_config_path": repo_config_path}


def _row(view: dict, key: str) -> dict:
    return next(row for row in view["lifecycles"] if row["key"] == key)


def test_repository_view_labels_local_override_binding(local_override_env: dict) -> None:
    """个人覆盖声明的绑定来源标为 ``local_override``，而不是可写的 ``repository``。"""
    view = (
        local_override_env["client"]
        .get(_SETTINGS_URL, params={"scope": "repository", "repo_id": "testrepo"})
        .json()
    )

    implementation_row = _row(view, "implementation")
    assert implementation_row["preset_name"] == "claude-local"
    assert implementation_row["field_sources"]["preset"] == "local_override"
    assert _row(view, "verifier")["field_sources"]["preset"] is None


@pytest.mark.parametrize("binding_value", [None, "claude-local"])
def test_repository_write_to_local_override_stage_rejected(
    local_override_env: dict, binding_value: str | None
) -> None:
    """解绑或改绑个人覆盖决定的阶段：422 指向覆盖文件，``.kedacode.toml`` 一字不动。"""
    repo_config_path: Path = local_override_env["repo_config_path"]
    repo_before = repo_config_path.read_bytes()

    response = local_override_env["client"].patch(
        _SETTINGS_URL,
        json={
            "scope": "repository",
            "repo_id": "testrepo",
            "bindings": {"implementation": binding_value},
        },
    )

    assert response.status_code == 422
    assert ".kedacode.local.toml" in response.json()["detail"]
    assert "implementation" in response.json()["detail"]
    assert repo_config_path.read_bytes() == repo_before


def test_global_view_ignores_local_override_stages(local_override_env: dict) -> None:
    """全局视角不读个人覆盖：来源标注不受影响。"""
    view = local_override_env["client"].get(_SETTINGS_URL, params={"scope": "global"}).json()

    assert _row(view, "implementation")["field_sources"]["preset"] is None
