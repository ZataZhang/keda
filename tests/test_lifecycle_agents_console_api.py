"""生命周期 Agent console API 契约测试。

覆盖 PRD rv-3（三层各写各自文件、互不污染）、rv-6（回退顺序写 runner 段）、
rv-7（agent 标签写注册块且重复被拒）以及 PRD 覆盖读写（rv-4 的后端半边）。

后端真实起 TestClient，配置走 tmp ``config.toml`` / 真实 git 仓库下的
``.iar.toml``；断言的事实源是**磁盘文件内容**，不是内存状态。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.core.use_cases.lifecycle_agent_resolution import parse_prd_lifecycle_overrides

_PRD_TEXT = (
    "# PRD: Demo\n\n"
    "- GitHub Issue: （创建后回填）\n\n"
    "> ✅ 交付前置：无。\n\n"
    "## 1. Intro\n正文段落\n"
)


def _git(repo_path: Path, *git_args: str) -> None:
    subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def console_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """准备隔离的 config.toml + 真实 git 仓库 + 一份 PRD 文件。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _git(repo_path, "init")
    _git(repo_path, "checkout", "-b", "main")
    (repo_path / ".iar.toml").write_text("[agent_runner]\n", encoding="utf-8")
    prd_relative_path = "tasks/pending/P1-FEAT-20260101-demo.md"
    prd_file = repo_path / prd_relative_path
    prd_file.parent.mkdir(parents=True)
    prd_file.write_text(_PRD_TEXT, encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[agent_runner]\n\n"
        "[agent_runner.repositories.testrepo]\n"
        f'path = "{repo_path.resolve()}"\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IAR_CONFIG", str(config_path))
    return {
        "client": TestClient(app),
        "config_path": config_path,
        "repo_path": repo_path,
        "iar_config_path": repo_path / ".iar.toml",
        "prd_relative_path": prd_relative_path,
        "prd_file": prd_file,
    }


def _lifecycle_entry(view: dict, key: str) -> dict:
    return next(entry for entry in view["lifecycles"] if entry["key"] == key)


def test_get_global_and_repository_views(console_env: dict) -> None:
    """两个视角都能返回九键生效视图与来源层标注。"""
    client = console_env["client"]
    global_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents", params={"scope": "global"}
    ).json()
    assert len(global_view["lifecycles"]) == 9
    assert _lifecycle_entry(global_view, "fix")["follows_executor"] is True

    repo_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents",
        params={"scope": "repository", "repo_id": "testrepo"},
    ).json()
    assert repo_view["repo_id"] == "testrepo"
    assert _lifecycle_entry(repo_view, "verifier")["auto_allowed"] is True


def test_global_write_only_touches_config_toml(console_env: dict) -> None:
    """rv-3：Settings 层保存只写 config.toml，仓库 .iar.toml 不变。"""
    client = console_env["client"]
    iar_before = console_env["iar_config_path"].read_text(encoding="utf-8")

    response = client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "global", "values": {"verifier": "codex"}},
    )
    assert response.status_code == 200

    config_text = console_env["config_path"].read_text(encoding="utf-8")
    assert "[agent_runner.lifecycle_agents]" in config_text
    assert 'verifier = "codex"' in config_text
    # 仓库层文件一字未动。
    assert console_env["iar_config_path"].read_text(encoding="utf-8") == iar_before
    # 写后视图反映新值。
    refreshed = client.get(
        "/api/v1/agent-runner/lifecycle-agents", params={"scope": "global"}
    ).json()
    assert _lifecycle_entry(refreshed, "verifier")["declared_value"] == "codex"


def test_repository_write_only_touches_iar_toml(console_env: dict) -> None:
    """rv-3：仓库层保存只写该仓库 .iar.toml，config.toml 不变。"""
    client = console_env["client"]
    config_before = console_env["config_path"].read_text(encoding="utf-8")

    response = client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "repository", "repo_id": "testrepo", "values": {"verifier": "kimi"}},
    )
    assert response.status_code == 200

    iar_text = console_env["iar_config_path"].read_text(encoding="utf-8")
    assert 'verifier = "kimi"' in iar_text
    assert console_env["config_path"].read_text(encoding="utf-8") == config_before

    repo_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents",
        params={"scope": "repository", "repo_id": "testrepo"},
    ).json()
    assert _lifecycle_entry(repo_view, "verifier")["source"] == "repository"


def test_repository_layer_wins_over_global_in_view(console_env: dict) -> None:
    """rv-3：仓库层值赢过全局层，且来源列标注正确。"""
    client = console_env["client"]
    client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "global", "values": {"fix": "kimi"}},
    )
    client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "repository", "repo_id": "testrepo", "values": {"fix": "pi"}},
    )
    repo_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents",
        params={"scope": "repository", "repo_id": "testrepo"},
    ).json()
    fix_entry = _lifecycle_entry(repo_view, "fix")
    assert fix_entry["effective_agent"] == "pi"
    assert fix_entry["declared_value"] == "pi"
    assert fix_entry["inherited_value"] == "kimi"
    assert fix_entry["source"] == "repository"


def test_repository_restore_key_falls_back_to_global(console_env: dict) -> None:
    """rv-3：仓库层删除键后该键从 .iar.toml 消失并回落全局层。"""
    client = console_env["client"]
    client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "global", "values": {"fix": "kimi"}},
    )
    client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "repository", "repo_id": "testrepo", "values": {"fix": "pi"}},
    )
    assert 'fix = "pi"' in console_env["iar_config_path"].read_text(encoding="utf-8")

    response = client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "repository", "repo_id": "testrepo", "values": {"fix": None}},
    )
    assert response.status_code == 200
    assert "fix" not in console_env["iar_config_path"].read_text(encoding="utf-8")
    repo_view = response.json()
    assert _lifecycle_entry(repo_view, "fix")["effective_agent"] == "kimi"


def test_unregistered_agent_rejected_by_api(console_env: dict) -> None:
    """rv-5：写回未注册 agent 被 422 拒绝，文件不变。"""
    client = console_env["client"]
    config_before = console_env["config_path"].read_text(encoding="utf-8")
    response = client.put(
        "/api/v1/agent-runner/lifecycle-agents",
        json={"scope": "global", "values": {"fix": "codebuddy"}},
    )
    assert response.status_code == 422
    assert "codebuddy" in response.json()["detail"]
    assert console_env["config_path"].read_text(encoding="utf-8") == config_before


def test_fallback_order_write_preserves_other_runner_keys(console_env: dict) -> None:
    """rv-6：回退顺序写 runner 段，段内既有键不变。"""
    client = console_env["client"]
    console_env["config_path"].write_text(
        "[agent_runner]\n\n"
        "[agent_runner.runner]\n"
        'default_agent = "codex"\n'
        'verification_commands = ["git diff --check"]\n',
        encoding="utf-8",
    )
    response = client.put(
        "/api/v1/agent-runner/agent-fallback-order",
        json={"agent_fallback_order": ["codex", "claude"], "max_agent_switches": 1},
    )
    assert response.status_code == 200
    assert response.json()["agent_fallback_order"] == ["codex", "claude"]
    assert response.json()["max_agent_switches"] == 1

    config_text = console_env["config_path"].read_text(encoding="utf-8")
    assert 'agent_fallback_order = ["codex", "claude"]' in config_text
    assert "max_agent_switches = 1" in config_text
    assert 'default_agent = "codex"' in config_text
    assert 'verification_commands = ["git diff --check"]' in config_text


def test_fallback_order_rejects_unregistered_agent(console_env: dict) -> None:
    """rv-6：回退链里的未注册 agent 被 422 拒绝。"""
    client = console_env["client"]
    response = client.put(
        "/api/v1/agent-runner/agent-fallback-order",
        json={"agent_fallback_order": ["codex", "ghost"], "max_agent_switches": 2},
    )
    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]


def test_agent_labels_write_and_duplicate_rejection(console_env: dict) -> None:
    """rv-7：标签写注册块，段内其它字段不变；重复标签被拒。"""
    client = console_env["client"]
    response = client.put(
        "/api/v1/agent-runner/agent-labels",
        json={
            "labels": [
                {
                    "agent": "claude",
                    "label": "agent/cc",
                    "label_color": "BFDADC",
                    "label_description": "Use Claude Code.",
                }
            ]
        },
    )
    assert response.status_code == 200
    config_text = console_env["config_path"].read_text(encoding="utf-8")
    assert "[agent_runner.agents.claude]" in config_text
    assert 'label = "agent/cc"' in config_text
    assert 'label_color = "BFDADC"' in config_text

    duplicate = client.put(
        "/api/v1/agent-runner/agent-labels",
        json={
            "labels": [
                {
                    "agent": "claude",
                    "label": "agent/dup",
                    "label_color": "",
                    "label_description": "",
                },
                {"agent": "kimi", "label": "agent/dup", "label_color": "", "label_description": ""},
            ]
        },
    )
    assert duplicate.status_code == 422
    assert "agent/dup" in duplicate.json()["detail"]

    empty_label = client.put(
        "/api/v1/agent-runner/agent-labels",
        json={"labels": [{"agent": "claude", "label": "  ", "label_color": ""}]},
    )
    assert empty_label.status_code == 422


def test_prd_override_read_write_roundtrip(console_env: dict) -> None:
    """rv-4 后端半边：PRD 覆盖写回文件头部且只影响该 PRD。"""
    client = console_env["client"]
    prd_path = console_env["prd_relative_path"]
    import base64

    encoded = base64.urlsafe_b64encode(prd_path.encode("utf-8")).decode("ascii")

    initial = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded}/agent-overrides",
        params={"repo_id": "testrepo"},
    )
    assert initial.status_code == 200
    assert initial.json()["overrides"] == {}

    response = client.patch(
        f"/api/v1/agent-runner/roadmap/prds/{encoded}/agent-overrides",
        json={"repo_id": "testrepo", "overrides": {"closeout": "pi", "implementation": "claude"}},
    )
    assert response.status_code == 200
    assert response.json()["overrides"] == {"closeout": "pi", "implementation": "claude"}

    prd_text = console_env["prd_file"].read_text(encoding="utf-8")
    assert parse_prd_lifecycle_overrides(prd_text) == {
        "closeout": "pi",
        "implementation": "claude",
    }
    assert "## 1. Intro\n正文段落" in prd_text
    assert "- GitHub Issue: （创建后回填）" in prd_text

    # 只写该 PRD；其它文件不变。
    assert "lifecycle_agents" not in console_env["config_path"].read_text(encoding="utf-8")


def test_prd_override_rejects_unregistered_agent(console_env: dict) -> None:
    """rv-5：PRD 覆盖里的未注册 agent 被 422 拒绝。"""
    client = console_env["client"]
    import base64

    encoded = base64.urlsafe_b64encode(console_env["prd_relative_path"].encode("utf-8")).decode(
        "ascii"
    )
    response = client.patch(
        f"/api/v1/agent-runner/roadmap/prds/{encoded}/agent-overrides",
        json={"repo_id": "testrepo", "overrides": {"review": "codebuddy"}},
    )
    assert response.status_code == 422
    assert "codebuddy" in response.json()["detail"]
