"""生命周期 Agent console API 契约测试。

覆盖 PRD rv-3（三层各写各自文件、互不污染）、rv-6（回退顺序写 runner 段）、
rv-7（agent 标签写注册块且重复被拒）以及 PRD 覆盖读写（rv-4 的后端半边）。

后端真实起 TestClient，配置走 tmp ``config.toml`` / 真实 git 仓库下的
``.iar.toml``；断言的事实源是**磁盘文件内容**，不是内存状态。
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_ENTRY_GROUPS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS,
)
from backend.core.use_cases.lifecycle_agent_resolution import parse_prd_lifecycle_overrides
from backend.core.use_cases.lifecycle_agents_console import (
    FIELD_SOURCE_PRESET_UNRESOLVED,
)

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


def _parse_toml(path: Path) -> dict:
    """读取磁盘上的 TOML，作为断言的事实源（不看内存状态）。"""
    with path.open("rb") as handle:
        return tomllib.load(handle)


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


def test_view_rows_carry_entry_group_and_trigger(console_env: dict) -> None:
    """每行带非空 entry / trigger，键序与既有字段保持兼容。"""
    client = console_env["client"]
    global_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents", params={"scope": "global"}
    ).json()

    # 键序契约：lifecycles 仍按 LIFECYCLE_AGENT_KEYS 原序（分组只影响前端呈现）。
    assert [row["key"] for row in global_view["lifecycles"]] == list(LIFECYCLE_AGENT_KEYS)
    for row in global_view["lifecycles"]:
        assert row["entry"]
        assert row["trigger"]

    # 组并集恰好覆盖九键、不重不漏，与 core 分组常量一一对应；组中文名与一行说明
    # 只在视图级 entry_groups 下发一份（行上不重复携带同名的 entry_label）。
    assert [group["entry"] for group in global_view["entry_groups"]] == [
        group.entry for group in LIFECYCLE_AGENT_ENTRY_GROUPS
    ]
    assert [group["label"] for group in global_view["entry_groups"]] == [
        group.label for group in LIFECYCLE_AGENT_ENTRY_GROUPS
    ]
    grouped_keys: dict[str, list[str]] = {}
    for row in global_view["lifecycles"]:
        grouped_keys.setdefault(row["entry"], []).append(row["key"])
    # 组归属与常量一一对应（集合语义）；组内展示顺序即 lifecycles 数组相对顺序，
    # 与 LIFECYCLE_AGENT_KEYS 键序一致，不由常量重复声明。
    assert set(grouped_keys) == {group.entry for group in LIFECYCLE_AGENT_ENTRY_GROUPS}
    for group in LIFECYCLE_AGENT_ENTRY_GROUPS:
        assert sorted(grouped_keys[group.entry]) == sorted(group.keys)

    # 既有字段名与含义未变（抽样既有键）。
    planner_row = _lifecycle_entry(global_view, "planner")
    assert planner_row["entry"] == "standalone"
    assert planner_row["auto_allowed"] is False
    assert planner_row["follows_executor"] is False

    # repository 视角带同一分组与键序。
    repo_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents",
        params={"scope": "repository", "repo_id": "testrepo"},
    ).json()
    assert [row["key"] for row in repo_view["lifecycles"]] == list(LIFECYCLE_AGENT_KEYS)
    assert [row["entry"] for row in repo_view["lifecycles"]] == [
        row["entry"] for row in global_view["lifecycles"]
    ]
    assert repo_view["entry_groups"] == global_view["entry_groups"]


def test_prd_override_view_carries_same_entry_groups(console_env: dict) -> None:
    """PRD 覆盖视图沿用同一分组：行上带 entry，entry_groups 与矩阵视图一致。"""
    client = console_env["client"]
    import base64

    encoded = base64.urlsafe_b64encode(console_env["prd_relative_path"].encode("utf-8")).decode(
        "ascii"
    )
    override_view = client.get(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
        params={"repo_id": "testrepo"},
    ).json()
    assert [row["key"] for row in override_view["lifecycles"]] == list(
        LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS
    )
    for row in override_view["lifecycles"]:
        assert row["entry"]
        assert row["trigger"]

    repo_view = client.get(
        "/api/v1/agent-runner/lifecycle-agents",
        params={"scope": "repository", "repo_id": "testrepo"},
    ).json()
    assert override_view["entry_groups"] == repo_view["entry_groups"]


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
        json={"scope": "global", "values": {"fix": "no-such-agent"}},
    )
    assert response.status_code == 422
    assert "no-such-agent" in response.json()["detail"]
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
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
        params={"repo_id": "testrepo"},
    )
    assert initial.status_code == 200
    assert initial.json()["overrides"] == {}

    response = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
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
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
        json={"repo_id": "testrepo", "overrides": {"review": "no-such-agent"}},
    )
    assert response.status_code == 422
    assert "no-such-agent" in response.json()["detail"]


def test_prd_override_view_excludes_planner(console_env: dict) -> None:
    """planner 没有 PRD 消费点：覆盖抽屉不下发该行，写回也拒绝。"""
    client = console_env["client"]
    import base64

    encoded = base64.urlsafe_b64encode(console_env["prd_relative_path"].encode("utf-8")).decode(
        "ascii"
    )
    view = client.get(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
        params={"repo_id": "testrepo"},
    ).json()
    override_keys = [row["key"] for row in view["lifecycles"]]
    assert "planner" not in override_keys
    assert "content_generation" in override_keys
    assert len(override_keys) == 8

    rejected = client.patch(
        f"/api/v1/agent-runner/backlog/prds/{encoded}/agent-overrides",
        json={"repo_id": "testrepo", "overrides": {"planner": "kimi"}},
    )
    assert rejected.status_code == 422
    assert "planner" in rejected.json()["detail"]
    # 被拒绝的写回不落盘。
    assert "lifecycle_agents" not in console_env["prd_file"].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 聚合生命周期设置端点（/lifecycle-settings）：矩阵 + 预设 + 绑定
# ---------------------------------------------------------------------------


def test_lifecycle_settings_aggregate_view_shape(console_env: dict) -> None:
    """GET /lifecycle-settings 返回九阶段生效值 + 逐字段来源 + 预设清单 + 回退候选三段。"""
    client = console_env["client"]
    view = client.get("/api/v1/agent-runner/lifecycle-settings", params={"scope": "global"}).json()
    assert view["scope"] == "global"
    assert len(view["lifecycles"]) == 9
    assert set(view["field_source_vocabulary"]) >= {
        "preset",
        "agent_default",
        "not_supported",
    }
    for row in view["lifecycles"]:
        assert set(row["field_sources"]) == {
            "agent",
            "preset",
            "model",
            "reasoning_effort",
        }
        assert row["affected_stages"] == []
    implementation = next(r for r in view["lifecycles"] if r["key"] == "implementation")
    assert implementation["preset_name"] is None
    assert "fallback" in view and "presets" in view


def test_preset_create_and_bind_writes_config_toml(console_env: dict) -> None:
    """一次 PATCH 既新建预设又绑定阶段：磁盘落预设表与绑定表，视图回显生效值与继承。"""
    client = console_env["client"]
    response = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {
                "hot": {
                    "agent": "claude",
                    "model": "sonnet-hot",
                    "reasoning_effort": None,
                }
            },
            "bindings": {"implementation": "hot"},
        },
    )
    assert response.status_code == 200
    on_disk = _parse_toml(console_env["config_path"])
    assert on_disk["agent_runner"]["presets"]["hot"] == {
        "agent": "claude",
        "model": "sonnet-hot",
    }
    assert on_disk["agent_runner"]["lifecycle_presets"]["implementation"] == "hot"

    view = response.json()
    implementation = next(r for r in view["lifecycles"] if r["key"] == "implementation")
    assert implementation["preset_name"] == "hot"
    assert implementation["effective_agent"] == "claude"
    assert implementation["model"] == "sonnet-hot"
    assert implementation["field_sources"]["agent"] == "preset"
    assert implementation["reasoning_effort"] is None
    assert implementation["affected_stages"] == ["implementation"]
    # claude 不声明 reasoning_effort_args：推理深度不可注入。
    assert implementation["reasoning_effort_supported"] is False

    fix_row = next(r for r in view["lifecycles"] if r["key"] == "fix")
    assert fix_row["follows_implementation"] is True
    assert fix_row["effective_agent"] == "claude"
    assert fix_row["model"] == "sonnet-hot"

    hot_preset = next(p for p in view["presets"] if p["name"] == "hot")
    assert hot_preset["bound_stages"] == ["implementation"]
    assert hot_preset["model_supported"] is True
    assert hot_preset["reasoning_effort_supported"] is False
    assert hot_preset["source"] == "global"


def test_preset_rejects_reasoning_effort_without_template(console_env: dict) -> None:
    """给无推理深度模板的 agent 设推理深度被拒，且不落盘。"""
    client = console_env["client"]
    rejected = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {
                "bad": {
                    "agent": "claude",
                    "model": None,
                    "reasoning_effort": "high",
                }
            },
            "bindings": {},
        },
    )
    assert rejected.status_code == 422
    assert "reasoning_effort" in rejected.json()["detail"]
    on_disk = _parse_toml(console_env["config_path"])
    assert "presets" not in on_disk.get("agent_runner", {})


def test_binding_rejects_undefined_preset(console_env: dict) -> None:
    """绑定到未定义、且本批也未新建的预设被拒。"""
    client = console_env["client"]
    rejected = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={"scope": "global", "presets": {}, "bindings": {"implementation": "ghost"}},
    )
    assert rejected.status_code == 422
    assert "ghost" in rejected.json()["detail"]


def test_repository_scope_writes_repo_config_only(console_env: dict) -> None:
    """仓库视角的预设与绑定写入仓库配置文件，不碰全局 config.toml。"""
    client = console_env["client"]
    response = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "repository",
            "repo_id": "testrepo",
            "presets": {
                "repo_hot": {
                    "agent": "claude",
                    "model": "m",
                    "reasoning_effort": None,
                }
            },
            "bindings": {"verifier": "repo_hot"},
        },
    )
    assert response.status_code == 200
    repo_cfg = _parse_toml(console_env["iar_config_path"])
    assert repo_cfg["agent_runner"]["presets"]["repo_hot"]["agent"] == "claude"
    assert repo_cfg["agent_runner"]["lifecycle_presets"]["verifier"] == "repo_hot"
    global_cfg = _parse_toml(console_env["config_path"])
    assert "presets" not in global_cfg.get("agent_runner", {})


def test_preset_delete_prunes_table_and_unbinds(console_env: dict) -> None:
    """删除预设（三元组删空即剪表）并解绑阶段。"""
    client = console_env["client"]
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {"temp": {"agent": "claude", "model": "m", "reasoning_effort": None}},
            "bindings": {"implementation": "temp"},
        },
    )
    removed = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {"temp": None},
            "bindings": {"implementation": None},
        },
    )
    assert removed.status_code == 200
    on_disk = _parse_toml(console_env["config_path"])
    assert "temp" not in on_disk.get("agent_runner", {}).get("presets", {})
    assert "implementation" not in on_disk.get("agent_runner", {}).get("lifecycle_presets", {})


def test_preset_delete_rejects_dangling_reference_and_keeps_file(console_env: dict) -> None:
    """删除仍被同层绑定引用的预设：422 点名预设，文件字节不变，不做部分写入。"""
    client = console_env["client"]
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {"temp": {"agent": "claude", "model": "m", "reasoning_effort": None}},
            "bindings": {"implementation": "temp"},
        },
    )
    on_disk_before = console_env["config_path"].read_bytes()
    rejected = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={"scope": "global", "presets": {"temp": None}},
    )
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert "temp" in detail and "引用" in detail
    assert console_env["config_path"].read_bytes() == on_disk_before
    on_disk = _parse_toml(console_env["config_path"])
    assert on_disk["agent_runner"]["presets"]["temp"]["agent"] == "claude"
    assert on_disk["agent_runner"]["lifecycle_presets"]["implementation"] == "temp"


def test_global_preset_delete_rejects_cross_layer_repo_reference(console_env: dict) -> None:
    """全局删除仅被仓库继承绑定引用的预设：422，全局与仓库文件均不变。"""
    client = console_env["client"]
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {"shared": {"agent": "claude", "model": "m", "reasoning_effort": None}},
            "bindings": {},
        },
    )
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={"scope": "repository", "repo_id": "testrepo", "bindings": {"verifier": "shared"}},
    )
    global_before = console_env["config_path"].read_bytes()
    repo_before = console_env["iar_config_path"].read_bytes()
    rejected = client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={"scope": "global", "presets": {"shared": None}},
    )
    assert rejected.status_code == 422
    assert "shared" in rejected.json()["detail"]
    assert console_env["config_path"].read_bytes() == global_before
    assert console_env["iar_config_path"].read_bytes() == repo_before


def test_han_edited_dangling_binding_view_never_500_and_marks_unresolved(
    console_env: dict,
) -> None:
    """手改出的悬空绑定（绕过写门禁）：生效视图仍返回九键、实现与继承阶段如实标注未解析，不 500。"""
    client = console_env["client"]
    with console_env["config_path"].open("a", encoding="utf-8") as handle:
        handle.write('\n[agent_runner.lifecycle_presets]\nimplementation = "ghost"\n')
    view = client.get("/api/v1/agent-runner/lifecycle-settings", params={"scope": "global"})
    assert view.status_code == 200
    payload = view.json()
    assert len(payload["lifecycles"]) == len(LIFECYCLE_AGENT_KEYS)
    for stage in ("implementation", "fix"):
        sources = _lifecycle_entry(payload, stage)["field_sources"]
        assert sources["model"] == FIELD_SOURCE_PRESET_UNRESOLVED
        assert sources["reasoning_effort"] == FIELD_SOURCE_PRESET_UNRESOLVED


# ---------------------------------------------------------------------------
# 执行器回退候选端点（/agent-fallback-candidates）
# ---------------------------------------------------------------------------


def test_fallback_candidates_write_ordered_with_presets(console_env: dict) -> None:
    """同一 agent 用不同预设是两个候选；整体数组写入 runner 段并携带预算。"""
    client = console_env["client"]
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {
                "max": {"agent": "claude", "model": "m1", "reasoning_effort": None},
                "eco": {"agent": "claude", "model": "m2", "reasoning_effort": None},
            },
            "bindings": {},
        },
    )
    response = client.put(
        "/api/v1/agent-runner/agent-fallback-candidates",
        json={
            "candidates": [
                {"agent": "claude", "preset": "max"},
                {"agent": "claude", "preset": "eco"},
                {"agent": "codex", "preset": None},
            ],
            "max_agent_switches": 3,
        },
    )
    assert response.status_code == 200
    view = response.json()
    assert view["budget_by_candidate_step"] is True
    assert [c["agent"] for c in view["candidates"]] == ["claude", "claude", "codex"]
    assert view["candidates"][0]["model"] == "m1"
    assert view["candidates"][2]["preset"] is None
    on_disk = _parse_toml(console_env["config_path"])
    candidates = on_disk["agent_runner"]["runner"]["agent_fallback_candidates"]
    assert [c.get("preset") for c in candidates] == ["max", "eco", None]
    assert on_disk["agent_runner"]["runner"]["max_agent_switches"] == 3


def test_fallback_candidates_reject_duplicate_and_mismatch(console_env: dict) -> None:
    """完全相同的 (agent,preset) 被拒；候选 agent 与预设 agent 不一致也被拒。"""
    client = console_env["client"]
    client.patch(
        "/api/v1/agent-runner/lifecycle-settings",
        json={
            "scope": "global",
            "presets": {
                "only_claude": {
                    "agent": "claude",
                    "model": "m",
                    "reasoning_effort": None,
                }
            },
            "bindings": {},
        },
    )
    dup = client.put(
        "/api/v1/agent-runner/agent-fallback-candidates",
        json={
            "candidates": [
                {"agent": "claude", "preset": "only_claude"},
                {"agent": "claude", "preset": "only_claude"},
            ],
            "max_agent_switches": 1,
        },
    )
    assert dup.status_code == 422
    assert "duplicate" in dup.json()["detail"]

    mismatch = client.put(
        "/api/v1/agent-runner/agent-fallback-candidates",
        json={
            "candidates": [{"agent": "codex", "preset": "only_claude"}],
            "max_agent_switches": 1,
        },
    )
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.json()["detail"]


def test_fallback_candidates_reject_unknown_preset(console_env: dict) -> None:
    """候选引用未定义预设被拒。"""
    client = console_env["client"]
    rejected = client.put(
        "/api/v1/agent-runner/agent-fallback-candidates",
        json={
            "candidates": [{"agent": "claude", "preset": "ghost"}],
            "max_agent_switches": 1,
        },
    )
    assert rejected.status_code == 422
    assert "ghost" in rejected.json()["detail"]


def test_han_edited_dangling_fallback_candidate_never_500(console_env: dict) -> None:
    """手改出的悬空候选预设：视图不 500，该候选如实标注未解析。"""
    client = console_env["client"]
    with console_env["config_path"].open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[[agent_runner.runner.agent_fallback_candidates]]\n"
            'agent = "claude"\npreset = "ghost"\n'
        )
    view = client.get("/api/v1/agent-runner/lifecycle-settings", params={"scope": "global"})
    assert view.status_code == 200
    candidate = view.json()["fallback"]["candidates"][0]
    assert candidate["preset"] == "ghost"
    assert candidate["field_sources"]["preset"] == FIELD_SOURCE_PRESET_UNRESOLVED
