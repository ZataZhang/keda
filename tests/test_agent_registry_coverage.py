"""自定义 agent 注册在**全部消费路径**上被识别的回归测试（PRD rv-3 的自动化部分）。

一条注册声明只有在每个消费方都认得它时才算真的生效：Skills 安装根与候选、循环配方、
内容生成、标签同步与 Issue 路由标签。这些路径各自收到一个"生效注册表"入参
（``agents=`` / ``agent_registry=``），漏接任何一条都会让合法的自定义名被当成拼写错误
拒掉、或让 skill 装不全。因此每条断言都**成对**写：传入生效注册表时认得这个自定义名
字，不传（只遍历内置注册表）时它不存在——漏接一条路径就让对应用例失败。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_labels import standard_label_specs
from backend.core.shared.models.agent_runner import AppConfig, LabelConfig
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_RUN,
    AgentProfileSpec,
    AgentSpec,
)
from backend.core.shared.prd_skill_location import default_prd_skill_candidate_paths
from backend.core.use_cases.create_issue_from_prd import (
    IssueFromPrdRequest,
    build_issue_labels,
    resolve_agent_name,
)
from backend.core.use_cases.loop_recipe import parse_loop_recipe, parse_loop_recipe_text
from backend.core.use_cases.sync_labels import sync_labels
from backend.engines.agent_runner.remote_template_skills import (
    PackagedSkillInstallResult,
    RemoteTemplateSkillInstallOptions,
    RemoteTemplateSkillInstallResult,
    install_remote_template_skills,
    resolve_user_skill_install_roots,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner

CUSTOM_AGENT = "myagent"
CUSTOM_LABEL = f"agent/{CUSTOM_AGENT}"
CUSTOM_AUTH_HOME_RELATIVE = ".myagent"

#: 全局配置的机器级来源声明：注册块写进这里，消费路径才认得。
_CUSTOM_REGISTRATION_TOML = f"""
[agent_runner]

[agent_runner.agents.{CUSTOM_AGENT}]
bin = "{CUSTOM_AGENT}"
label = "{CUSTOM_LABEL}"
label_color = "AA1122"
label_description = "Run the in-house coding CLI."
auth_home = "~/{CUSTOM_AUTH_HOME_RELATIVE}"

[agent_runner.agents.{CUSTOM_AGENT}.profiles.{AGENT_PROFILE_RUN}]
args = ["--print"]
prompt_delivery = "argv_tail"
"""


def _custom_spec(*, profiles: tuple[str, ...] = (AGENT_PROFILE_RUN,)) -> AgentSpec:
    """带 ``auth_home`` 的自定义注册声明：Skills 目录与标签都从它派生。"""
    return AgentSpec(
        bin=CUSTOM_AGENT,
        label=CUSTOM_LABEL,
        label_color="AA1122",
        label_description="Run the in-house coding CLI.",
        auth_home=f"~/{CUSTOM_AUTH_HOME_RELATIVE}",
        profiles={name: AgentProfileSpec() for name in profiles},
    )


def _effective_registry() -> dict[str, AgentSpec]:
    """生效注册表：内置条目 + 自定义 agent。"""
    return {**AppConfig().agents, CUSTOM_AGENT: _custom_spec()}


# ---------------------------------------------------------------------------
# Skills 安装根与 prd skill 候选
# ---------------------------------------------------------------------------


def test_skill_install_roots_follow_the_effective_registry(tmp_path: Path) -> None:
    """自定义 agent 的配置目录已存在时，安装根里有它；不传注册表时没有。"""
    user_home_path = tmp_path / "home"
    (user_home_path / CUSTOM_AUTH_HOME_RELATIVE).mkdir(parents=True)
    custom_root = user_home_path / CUSTOM_AUTH_HOME_RELATIVE / "skills"

    assert custom_root in resolve_user_skill_install_roots(
        user_home_path, agents=_effective_registry()
    )
    assert custom_root not in resolve_user_skill_install_roots(user_home_path)


def test_skill_install_roots_never_invent_a_missing_agent_directory(tmp_path: Path) -> None:
    """注册了但本机没有该 agent 的配置目录时，不凭空创建安装根。"""
    user_home_path = tmp_path / "home"
    user_home_path.mkdir()
    custom_root = user_home_path / CUSTOM_AUTH_HOME_RELATIVE / "skills"

    assert custom_root not in resolve_user_skill_install_roots(
        user_home_path, agents=_effective_registry()
    )
    assert not custom_root.parent.exists()


def test_prd_skill_candidates_include_the_custom_agent_dir(tmp_path: Path) -> None:
    """prd skill 的候选路径按生效注册表派生：读取侧也认得自定义 agent 的目录。"""
    user_home_path = tmp_path / "home"
    custom_candidate = user_home_path / CUSTOM_AUTH_HOME_RELATIVE / "skills" / "prd" / "SKILL.md"

    assert custom_candidate in default_prd_skill_candidate_paths(
        user_home_path, agents=_effective_registry()
    )
    assert custom_candidate not in default_prd_skill_candidate_paths(user_home_path)


def test_installer_writes_template_skills_into_the_custom_agent_root(tmp_path: Path) -> None:
    """安装入口（dry-run）把计划目标覆盖到自定义 agent 的用户级目录。"""
    user_home_path = tmp_path / "home"
    (user_home_path / CUSTOM_AUTH_HOME_RELATIVE).mkdir(parents=True)
    custom_root = user_home_path / CUSTOM_AUTH_HOME_RELATIVE / "skills"

    with_registry = install_remote_template_skills(
        RemoteTemplateSkillInstallOptions(
            process_runner=FakeProcessRunner(),
            dry_run=True,
            user_home_path=user_home_path,
            agents=_effective_registry(),
        )
    )
    without_registry = install_remote_template_skills(
        RemoteTemplateSkillInstallOptions(
            process_runner=FakeProcessRunner(),
            dry_run=True,
            user_home_path=user_home_path,
        )
    )

    assert custom_root in with_registry.target_skills_roots
    assert custom_root not in without_registry.target_skills_roots


# ---------------------------------------------------------------------------
# 循环配方
# ---------------------------------------------------------------------------


def _recipe_text(agent_name: str) -> str:
    """最小可用配方文本：只把 ``agent`` 换成待验证的名字。"""
    return (
        "---\n"
        "id: nightly-sweep\n"
        "schedule: 0 9 * * *\n"
        "repo_id: keda\n"
        f"agent: {agent_name}\n"
        "---\n\n"
        "Sweep the backlog.\n"
    )


def test_loop_recipe_accepts_a_custom_agent_name() -> None:
    """配方点名自定义 agent：传入生效注册表时合法，且名字原样落到任务上。"""
    recipe = parse_loop_recipe_text(
        _recipe_text(CUSTOM_AGENT),
        source_path=Path("tasks/loop/nightly-sweep.md"),
        agents=_effective_registry(),
    )
    assert recipe.agent == CUSTOM_AGENT


def test_loop_recipe_rejects_a_custom_name_without_the_effective_registry() -> None:
    """不传注册表时只认内置条目——这正是漏接该路径后的表现。"""
    with pytest.raises(ValueError, match="Loop recipe agent must be one of"):
        parse_loop_recipe_text(
            _recipe_text(CUSTOM_AGENT), source_path=Path("tasks/loop/nightly-sweep.md")
        )


def test_loop_recipe_file_reads_the_effective_registry(tmp_path: Path) -> None:
    """从磁盘读配方同样按生效注册表校验（``parse_loop_recipe`` 的入参不是摆设）。"""
    recipe_path = tmp_path / "nightly-sweep.md"
    recipe_path.write_text(_recipe_text(CUSTOM_AGENT), encoding="utf-8")

    assert parse_loop_recipe(recipe_path, agents=_effective_registry()).agent == CUSTOM_AGENT
    with pytest.raises(ValueError, match="must be one of"):
        parse_loop_recipe(recipe_path)


# ---------------------------------------------------------------------------
# 内容生成
# ---------------------------------------------------------------------------


def test_content_generation_accepts_a_custom_generate_capable_agent() -> None:
    """声明了 ``generate`` 用途的自定义 agent 被内容生成接受。"""
    registry = {
        **AppConfig().agents,
        CUSTOM_AGENT: _custom_spec(profiles=(AGENT_PROFILE_RUN, AGENT_PROFILE_GENERATE)),
    }
    assert resolve_agent_name(CUSTOM_AGENT, agents=registry) == CUSTOM_AGENT
    assert resolve_agent_name(CUSTOM_AGENT) == "claude"


def test_content_generation_refuses_a_registered_agent_without_generate_profile() -> None:
    """显式点名只会跑活的自定义 agent：报错而不是悄悄换成别的 agent。"""
    with pytest.raises(ValueError, match="does not declare the 'generate' profile"):
        resolve_agent_name(CUSTOM_AGENT, agents=_effective_registry())


# ---------------------------------------------------------------------------
# 标签同步与 Issue 路由标签
# ---------------------------------------------------------------------------


def _labels_with_custom() -> LabelConfig:
    """按生效注册表派生 ``agent_labels`` 后的标签配置（与 factory 同一规则）。"""
    registry = _effective_registry()
    return LabelConfig(agent_labels={name: spec.label for name, spec in registry.items()})


def test_label_specs_use_the_custom_agent_color_and_description() -> None:
    """标签同步的颜色与描述来自生效注册表；只遍历内置表时落到通用兜底。"""
    specs = {
        spec.standard_name: spec
        for spec in standard_label_specs(_labels_with_custom(), _effective_registry())
    }
    assert specs[CUSTOM_LABEL].color == "AA1122"
    assert specs[CUSTOM_LABEL].description == "Run the in-house coding CLI."

    builtin_only = {
        spec.standard_name: spec for spec in standard_label_specs(_labels_with_custom())
    }
    assert builtin_only[CUSTOM_LABEL].color != "AA1122"


def test_sync_labels_forwards_the_registry_to_the_client() -> None:
    """core 不重复派生逻辑，只把生效注册表原样透传给客户端。"""
    client = FakeGitHubClient()
    registry = _effective_registry()
    sync_labels(
        labels_config=_labels_with_custom(),
        github_client=client,
        agent_registry=registry,
    )

    forwarded = [call for call in client.calls if call["method"] == "sync_labels"]
    assert len(forwarded) == 1
    assert forwarded[0]["agent_registry"] is registry
    assert forwarded[0]["agent_registry"][CUSTOM_AGENT].bin == CUSTOM_AGENT


def _prd_request(tmp_path: Path, agent_name: str) -> IssueFromPrdRequest:
    """构造只到"打标签"这一步为止的建 Issue 请求。"""
    prd_path = tmp_path / "P1-FEAT-example.md"
    prd_path.write_text("# 示例 PRD\n", encoding="utf-8")
    return IssueFromPrdRequest(
        repo_path=tmp_path,
        prd_path=prd_path,
        issue_type="feature",
        issue_agent=agent_name,
        agents=_effective_registry(),
    )


def test_issue_labels_route_the_custom_agent(tmp_path: Path) -> None:
    """建 Issue 时点名自定义 agent：路由标签来自按注册表派生的标签配置。"""
    labels = build_issue_labels(_prd_request(tmp_path, CUSTOM_AGENT), _labels_with_custom())
    assert CUSTOM_LABEL in labels

    # 标签配置只覆盖内置注册表时，同一个名字不是可路由的键。
    with pytest.raises(ValueError, match="issue_agent must be one of"):
        build_issue_labels(_prd_request(tmp_path, CUSTOM_AGENT), LabelConfig())


# ---------------------------------------------------------------------------
# 机器级配置 → 消费路径
# ---------------------------------------------------------------------------


def test_registration_block_parses_into_the_effective_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写进 ``config.toml`` 的注册块经真实加载进入生效注册表，字段不丢。"""
    from backend.core.use_cases.agent_runner_factory import build_app_config

    _patch_machine_config(monkeypatch, tmp_path)
    registry = build_app_config().agents

    assert CUSTOM_AGENT in registry
    assert registry[CUSTOM_AGENT].bin == CUSTOM_AGENT
    assert registry[CUSTOM_AGENT].auth_home == f"~/{CUSTOM_AUTH_HOME_RELATIVE}"
    assert registry[CUSTOM_AGENT].profiles[AGENT_PROFILE_RUN].args == ("--print",)
    # 内置条目没有被替换掉：合并发生在内置默认之上。
    assert "claude" in registry


def test_cli_skill_install_passes_the_effective_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实入口 ``kc skill install``：安装根解析用的是生效配置里的注册表。"""
    from backend.api.cli import main

    captured: list[Any] = []
    _patch_machine_config(monkeypatch, tmp_path)

    def _capture(
        options: RemoteTemplateSkillInstallOptions,
    ) -> RemoteTemplateSkillInstallResult:
        captured.append(options)
        return RemoteTemplateSkillInstallResult(
            target_skills_roots=(tmp_path / "skills",),
            installed_skill_names=("prd", "code-reviewer"),
            dry_run=options.dry_run,
        )

    monkeypatch.setattr("backend.api.cli_skill.install_remote_template_skills", _capture)
    monkeypatch.setattr(
        "backend.api.cli_skill.install_packaged_operator_skill",
        lambda **options: PackagedSkillInstallResult(
            target_path=options["target_skills_root"] / product_identity.OPERATOR_SKILL_NAME,
            action="install",
            dry_run=bool(options["dry_run"]),
        ),
    )

    assert main(["skill", "install", "--dry-run"]) == 0

    assert captured and captured[0].agents is not None
    assert CUSTOM_AGENT in captured[0].agents
    # 生效注册表不是"只有自定义条目"：内置 agent 仍在同一份表里。
    assert "claude" in captured[0].agents


def test_cli_loop_create_accepts_a_recipe_naming_a_custom_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实入口 ``kc loop create``：配方点名配置里注册的自定义 agent 时照常建任务。"""
    from backend.api import cli_loop
    from backend.engines.agent_runner.persistence.loop_state_json import JsonLoopStateStore

    _patch_machine_config(monkeypatch, tmp_path)
    recipe_path = tmp_path / "nightly-sweep.md"
    recipe_path.write_text(_recipe_text(CUSTOM_AGENT), encoding="utf-8")
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")

    parsed = argparse.Namespace(
        command="loop create",
        loop_id="nightly-sweep",
        recipe=str(recipe_path),
        cron=None,
        every=None,
        force=False,
        loop_repo_id=None,
        loop_repo=None,
    )
    assert cli_loop.run_loop_create_command(parsed, state_store_factory=lambda: state_store) == 0
    assert state_store.get_task("nightly-sweep").agent == CUSTOM_AGENT


def _patch_machine_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把机器级配置指向声明了自定义 agent 的临时 ``config.toml``。

    ``build_app_config()`` 转换的是进程级 ``config.agent_runner`` 单例；只改环境变量
    不会让它重新读盘，因此这里按 CLI 冷启动的口径重建那一段（conftest 剥离宿主注入
    配置时用的是同一手法），结束后由 monkeypatch 还原。
    """
    from backend.infrastructure.config import settings as app_settings_module
    from backend.infrastructure.config.settings import AgentRunnerSettings

    config_path = tmp_path / "config.toml"
    config_path.write_text(_CUSTOM_REGISTRATION_TOML, encoding="utf-8")
    monkeypatch.setenv(f"{product_identity.ENV_PREFIX}CONFIG", str(config_path))
    monkeypatch.setattr(app_settings_module.config, "agent_runner", AgentRunnerSettings())
