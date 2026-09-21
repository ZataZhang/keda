"""agent 注册表配置解析与两层合并测试（rv-3 配置面的自动化部分）。

覆盖：``[agent_runner.agents.<name>]`` 段的解析与逐字段回落、
全新 agent 注册的最小字段校验、非法值 fail-fast、旧版
``[agent_runner.labels]`` 三个 agent 键的兼容覆盖（FR-12）、
config.toml 内置注册块与代码默认（BUILTIN_AGENT_SPECS）的一致性守卫，
以及仓库级 .iar.toml 覆盖的合并语义。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    BUILTIN_AGENT_SPECS,
)
from backend.engines.agent_runner.factory import (
    build_app_config_from_settings,
    merge_repository_config,
)
from backend.engines.agent_runner.factory_config_builder import (
    build_agent_registry_from_settings,
    build_label_config_from_settings,
)
from backend.infrastructure.config.settings import (
    AgentRunnerAgentProfileSettings,
    AgentRunnerAgentSettings,
    AgentRunnerLabelSettings,
    AgentRunnerSettings,
)


def _profile_settings(**kwargs: object) -> AgentRunnerAgentProfileSettings:
    """构造单用途稀疏覆盖的便捷函数。"""
    return AgentRunnerAgentProfileSettings(**kwargs)


# ---------------------------------------------------------------------------
# 注册表构建：逐字段回落 / 全新 agent / 非法值
# ---------------------------------------------------------------------------


def test_partial_override_falls_back_to_builtin_per_field() -> None:
    """覆盖既有 agent 时未声明字段逐字段回落内置默认。"""
    registry = build_agent_registry_from_settings(
        {
            "claude": AgentRunnerAgentSettings(
                profiles={"generate": _profile_settings(read_only=False)}
            )
        }
    )
    claude_spec = registry["claude"]
    assert claude_spec.bin == "claude"
    assert claude_spec.label == "agent/claude"
    assert claude_spec.label_color == BUILTIN_AGENT_SPECS["claude"].label_color
    # 覆盖生效的字段
    assert claude_spec.profiles["generate"].read_only is False
    # 未覆盖的用途保持内置形态
    assert claude_spec.profiles["run"] == BUILTIN_AGENT_SPECS["claude"].profiles["run"]
    # 覆盖既有 agent 保持原注册顺序
    assert list(registry) == list(BUILTIN_AGENT_SPECS)


def test_new_agent_registration_minimal() -> None:
    """注册全新 agent：bin 与 label 必填，未声明的其余字段取默认值。"""
    registry = build_agent_registry_from_settings(
        {
            "myagent": AgentRunnerAgentSettings(
                bin="myagent-bin",
                label="agent/myagent",
                profiles={
                    "run": _profile_settings(
                        args=["--go"],
                        prompt_delivery="stdin",
                    )
                },
            )
        }
    )
    agent_spec = registry["myagent"]
    assert agent_spec.bin == "myagent-bin"
    assert agent_spec.label == "agent/myagent"
    assert agent_spec.label_color == "5319E7"  # 默认色
    assert set(agent_spec.profiles) == {"run"}
    # 新 agent 追加在末尾
    assert list(registry)[-1] == "myagent"


def test_new_agent_requires_bin_and_label() -> None:
    """全新 agent 缺 bin / label 时报错并点名缺失字段。"""
    with pytest.raises(ValueError, match="bin"):
        build_agent_registry_from_settings(
            {"myagent": AgentRunnerAgentSettings(label="agent/myagent")}
        )
    with pytest.raises(ValueError, match="label"):
        build_agent_registry_from_settings({"myagent": AgentRunnerAgentSettings(bin="myagent-bin")})


def test_invalid_label_color_rejected() -> None:
    """label_color 必须是 6 位 hex（不带 #）。"""
    with pytest.raises(ValueError, match="label_color"):
        build_agent_registry_from_settings(
            {
                "claude": AgentRunnerAgentSettings(
                    label_color="#BFDADC",
                )
            }
        )
    with pytest.raises(ValueError, match="label_color"):
        build_agent_registry_from_settings(
            {
                "claude": AgentRunnerAgentSettings(
                    label_color="ZZZZZZ",
                )
            }
        )


def test_unknown_profile_name_rejected() -> None:
    """用途名是闭集：拼错的 profile 名直接报错。"""
    with pytest.raises(ValueError, match="unknown profile"):
        build_agent_registry_from_settings(
            {
                "claude": AgentRunnerAgentSettings(
                    profiles={"rnu": _profile_settings(prompt_delivery="stdin")}
                )
            }
        )


def test_invalid_prompt_delivery_rejected() -> None:
    """prompt_delivery 非法值在配置加载期直接拒绝。"""
    with pytest.raises(ValueError, match="invalid prompt_delivery"):
        build_agent_registry_from_settings(
            {
                "claude": AgentRunnerAgentSettings(
                    profiles={"run": _profile_settings(prompt_delivery="carrier-pigeon")}
                )
            }
        )


def test_flag_delivery_requires_prompt_flag_for_new_profile() -> None:
    """新声明 flag 投递的用途必须同时声明 prompt_flag。"""
    with pytest.raises(ValueError, match="prompt_flag"):
        build_agent_registry_from_settings(
            {
                "myagent": AgentRunnerAgentSettings(
                    bin="x",
                    label="agent/x",
                    profiles={"run": _profile_settings(prompt_delivery="flag")},
                )
            }
        )


# ---------------------------------------------------------------------------
# 旧版 [agent_runner.labels] 三个 agent 键的兼容覆盖（FR-12）
# ---------------------------------------------------------------------------


def test_legacy_label_keys_override_agent_labels() -> None:
    """codex / claude / kimi 旧键显式设置时覆盖注册表派生的路由标签。"""
    label_settings = AgentRunnerLabelSettings(codex="agent/codex-custom")
    registry = build_agent_registry_from_settings({})
    label_config = build_label_config_from_settings(label_settings, registry)
    assert label_config.agent_labels["codex"] == "agent/codex-custom"
    # 未覆盖的键保持注册表派生值
    assert label_config.agent_labels["claude"] == "agent/claude"
    assert label_config.agent_labels["pi"] == "agent/pi"


def test_legacy_label_keys_none_means_no_override() -> None:
    """旧键为 None（未设置）时不产生覆盖，标签全部由注册表派生。"""
    label_settings = AgentRunnerLabelSettings()
    registry = build_agent_registry_from_settings({})
    label_config = build_label_config_from_settings(label_settings, registry)
    assert label_config.agent_labels == {
        name: agent_spec.label for name, agent_spec in registry.items()
    }


def test_registry_labels_stay_in_sync_with_spec_changes() -> None:
    """改注册块的 label 字段后路由标签自动跟随（不再需要三写同步）。"""
    registry = build_agent_registry_from_settings(
        {"claude": AgentRunnerAgentSettings(label="agent/claude-renamed")}
    )
    label_config = build_label_config_from_settings(AgentRunnerLabelSettings(), registry)
    assert label_config.agent_labels["claude"] == "agent/claude-renamed"


# ---------------------------------------------------------------------------
# AppConfig 构建：settings -> 注册表
# ---------------------------------------------------------------------------


def test_app_config_from_settings_carries_registry() -> None:
    """AgentRunnerSettings.agents 经 build_app_config_from_settings 进入 AppConfig。"""
    settings = AgentRunnerSettings(
        agents={
            "myagent": AgentRunnerAgentSettings(
                bin="myagent-bin",
                label="agent/myagent",
                profiles={"run": _profile_settings(prompt_delivery="stdin")},
            )
        }
    )
    config = build_app_config_from_settings(settings)
    assert "myagent" in config.agents
    assert config.agents["myagent"].bin == "myagent-bin"
    # 内置 agent 不受影响
    assert set(BUILTIN_AGENT_SPECS) < set(config.agents)


def test_app_config_default_registry_is_builtin() -> None:
    """未声明 agents 段的既有配置维持当前行为：注册表即内置四个 agent。"""
    config = build_app_config_from_settings(AgentRunnerSettings())
    assert list(config.agents) == list(BUILTIN_AGENT_SPECS)


# ---------------------------------------------------------------------------
# 两层合并：仓库级 .iar.toml 覆盖全局
# ---------------------------------------------------------------------------


def test_repository_level_agent_override(tmp_path: Path) -> None:
    """仓库级 agents 声明在全局注册表之上逐字段覆盖，新 agent 追加末尾。"""
    from backend.infrastructure.config.settings import load_agent_runner_local_settings

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".iar.toml").write_text(
        """
[agent_runner]
id = "demo-repo"

[agent_runner.agents.claude]
label = "agent/claude-repo"

[agent_runner.agents.myagent]
bin = "myagent-bin"
label = "agent/myagent"

[agent_runner.agents.myagent.profiles.run]
args = ["--go"]
prompt_delivery = "stdin"
""",
        encoding="utf-8",
    )
    repo_settings = load_agent_runner_local_settings(repo_path)
    assert repo_settings is not None
    global_config = build_app_config_from_settings(AgentRunnerSettings())
    merged = merge_repository_config(global_config, repo_settings, skip_identity=True)
    # 覆盖既有 agent 的 label
    assert merged.agents["claude"].label == "agent/claude-repo"
    # 既有 agent 的其余字段不受影响
    assert merged.agents["claude"].bin == "claude"
    # 仓库级新 agent 进入注册表
    assert merged.agents["myagent"].bin == "myagent-bin"
    assert merged.agents["myagent"].profiles["run"].args == ("--go",)


# ---------------------------------------------------------------------------
# 守卫：config.toml 内置注册块与代码默认一致（防两处漂移）
# ---------------------------------------------------------------------------


def _config_toml_agent_blocks() -> dict[str, dict[str, object]]:
    """读取仓库 config.toml 的 [agent_runner.agents.*] 声明。"""
    config_path = Path(__file__).resolve().parent.parent / "config.toml"
    with config_path.open("rb") as config_file:
        data = tomllib.load(config_file)
    return data.get("agent_runner", {}).get("agents", {})  # type: ignore[return-value]


def test_config_toml_agent_blocks_match_builtin_specs() -> None:
    """config.toml 的四个注册块与 BUILTIN_AGENT_SPECS 语义一致（缺省键视同默认值）。

    config.toml 写出的是"出厂注册块"：与代码默认等价、便于发现与覆盖。
    本守卫防止两处数据漂移——改内置 spec 必须同步 config.toml，反之亦然。
    """
    blocks = _config_toml_agent_blocks()
    assert set(blocks) == set(BUILTIN_AGENT_SPECS)
    default_profile = {
        "args": [],
        "tail_args": [],
        "expand": [],
        "prompt_flag": None,
        "prompt_delivery": "argv_tail",
        "output_protocol": "plain",
        "read_only": False,
    }
    for agent_name, agent_spec in BUILTIN_AGENT_SPECS.items():
        block = blocks[agent_name]
        scalar_fields = {
            "bin": agent_spec.bin,
            "label": agent_spec.label,
            "label_color": agent_spec.label_color,
            "label_description": agent_spec.label_description,
            "auth_home": agent_spec.auth_home,
            "auth_include": list(agent_spec.auth_include),
            "auth_exclude": list(agent_spec.auth_exclude),
            "project_skills_dir": agent_spec.project_skills_dir,
        }
        for field_name, expected_value in scalar_fields.items():
            assert block.get(field_name) == expected_value, (
                f"config.toml agents.{agent_name}.{field_name} drifts from "
                f"BUILTIN_AGENT_SPECS: {block.get(field_name)!r} != {expected_value!r}"
            )
        declared_profiles = block.get("profiles", {})
        assert set(declared_profiles) == set(agent_spec.profiles)
        for profile_name, profile_spec in agent_spec.profiles.items():
            declared = declared_profiles[profile_name]
            expected = dict(default_profile)
            expected.update(
                {
                    "args": list(profile_spec.args),
                    "tail_args": list(profile_spec.tail_args),
                    "expand": list(profile_spec.expand),
                    "prompt_flag": profile_spec.prompt_flag,
                    "prompt_delivery": profile_spec.prompt_delivery,
                    "output_protocol": profile_spec.output_protocol,
                    "read_only": profile_spec.read_only,
                }
            )
            for field_name, expected_value in expected.items():
                declared_value = declared.get(field_name)
                if declared_value is None:
                    # 缺省键视同默认值；只有期望本身非默认时缺省才算漂移
                    assert expected_value in ([], None, False, "argv_tail", "plain"), (
                        f"config.toml agents.{agent_name}.profiles.{profile_name}."
                        f"{field_name} is missing but has no default value."
                    )
                    continue
                assert declared_value == expected_value, (
                    f"config.toml agents.{agent_name}.profiles.{profile_name}."
                    f"{field_name} drifts from BUILTIN_AGENT_SPECS: "
                    f"{declared_value!r} != {expected_value!r}"
                )


def test_config_toml_agent_blocks_round_trip() -> None:
    """用 config.toml 的注册块构建注册表后，内置 agent 的调用形态不变。"""
    blocks = _config_toml_agent_blocks()
    registry = build_agent_registry_from_settings(
        {
            agent_name: AgentRunnerAgentSettings.model_validate(agent_block)
            for agent_name, agent_block in blocks.items()
        }
    )
    for agent_name, agent_spec in BUILTIN_AGENT_SPECS.items():
        assert registry[agent_name] == agent_spec


def test_partial_profile_set_merges_without_error() -> None:
    """内置 spec 与配置声明两侧都缺某用途时按"该 agent 不提供该用途"处理。

    回归锁：合并曾把这种情况误判为"声明为空且无内置默认"并抛错，导致任何
    profile 集合不全的 agent（例如只提供 run / deliberate / repl 的 opencode）
    都无法写进 ``config.toml``，出厂注册块与内置 spec 的一致性守卫因此不可能满足。
    """
    registry = build_agent_registry_from_settings(
        {
            "opencode": AgentRunnerAgentSettings(
                bin="opencode",
                label="agent/opencode",
                profiles={"run": _profile_settings(prompt_delivery="stdin")},
            )
        }
    )
    merged = registry["opencode"]
    # 缺 generate 是两侧一致的结论——跳过而不是报错
    assert set(merged.profiles) == {"run", "deliberate", "repl"}
    # 配置声明的字段仍然覆盖生效
    assert merged.profiles["run"].prompt_delivery == "stdin"


def test_explicitly_empty_profile_segment_still_rejected() -> None:
    """显式写空段仍然报错：跳过逻辑只针对"两侧都没有"，不吞掉真错误。"""
    with pytest.raises(ValueError, match="prompt_delivery is required for a new profile"):
        build_agent_registry_from_settings(
            {
                "opencode": AgentRunnerAgentSettings(
                    bin="opencode",
                    label="agent/opencode",
                    profiles={"generate": AgentRunnerAgentProfileSettings()},
                )
            }
        )


def test_default_app_config_matches_builtin_registry() -> None:
    """AppConfig 默认注册表即内置 spec（无配置时行为等价）。"""
    config = AppConfig()
    assert config.agents == BUILTIN_AGENT_SPECS
