"""生命周期 Agent 解析（优先级链 + 零配置基线 + fail-fast）的测试。

覆盖 PRD rv-1（优先级链与九键零配置基线、`auto` 逐阶段语义）与 rv-5（未注册
agent fail-fast）。配置值一律来自真实 TOML 文本（``config.toml`` / ``.iar.toml``
临时文件）经 pydantic settings 合并后再被解析函数消费，不直接构造解析结果。
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, RunnerConfig
from backend.core.use_cases.agent_invocation import UnknownAgentError
from backend.core.use_cases.lifecycle_agent_resolution import (
    parse_prd_lifecycle_overrides,
    resolve_lifecycle_agent,
    upsert_prd_lifecycle_overrides,
)
from backend.engines.agent_runner.factory import (
    build_app_config_from_settings,
    resolve_repository_targets,
)
from backend.infrastructure.config import settings_sources
from backend.infrastructure.config.agent_runner_settings import (
    AgentRunnerLifecycleAgentsSettings,
)
from backend.infrastructure.config.settings import AgentRunnerSettings

#: 九键在零配置下的期望值（``selected_agent="codex"`` 时）。这些值与引入矩阵前
#: 各阶段散落配置的解析结果逐一对应，是"零新配置行为不变"的基线。
_ZERO_CONFIG_BASELINE = {
    "implementation": "claude",
    "fix": "codex",
    "closeout": "codex",
    "verifier": "claude",
    "review": "codex",
    "supervisor": "codex",
    "planner": "claude",
    "content_generation": "claude",
    "deliberate": "claude",
}


def _run_git(repo_path: Path, *git_args: str) -> None:
    subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_git_repository(tmp_path: Path, name: str) -> Path:
    repo_path = tmp_path / name
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "checkout", "-b", "main")
    return repo_path


@pytest.fixture
def config_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 config.toml 指向一个 tmp 文件，隔离开发者本机配置。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")

    def _load_section(section_name: str) -> dict:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return data.get(section_name, {})

    monkeypatch.setattr(settings_sources, "_load_toml_section_data", _load_section)
    monkeypatch.setattr(settings_sources, "_load_registry_toml_section_data", lambda _name: {})
    return config_path


def _global_config_from_toml(config_path: Path, toml_text: str) -> AppConfig:
    """把 TOML 文本写盘后经 settings 加载构建全局 AppConfig。"""
    config_path.write_text(toml_text, encoding="utf-8")
    return build_app_config_from_settings(AgentRunnerSettings())


def _issue(labels: tuple[str, ...] = ()) -> IssueSummary:
    return IssueSummary(
        number=1,
        title="t",
        url="https://example/1",
        body="- PRD path: tasks/pending/x.md",
        labels=labels,
    )


def test_zero_config_baseline_matches_pre_matrix_behavior(config_toml: Path) -> None:
    """rv-1 基线：不写任何新配置时九键解析值与改动前逐阶段一致。

    必须走 ``config_toml`` 隔离 fixture：否则读到的是开发者本机（或 keda 源码根）
    那份 ``config.toml``，一旦它声明了矩阵，这个"零配置"基线就不再是零配置。
    """
    config = build_app_config_from_settings(AgentRunnerSettings())
    resolved = {
        lifecycle_key: resolve_lifecycle_agent(
            lifecycle_key, config, issue=_issue(), selected_agent="codex"
        )
        for lifecycle_key in _ZERO_CONFIG_BASELINE
    }
    assert resolved == _ZERO_CONFIG_BASELINE


def test_matrix_global_layer_overrides_legacy_key(config_toml: Path) -> None:
    """矩阵全局层赢过既有散落配置键。"""
    config = _global_config_from_toml(
        config_toml,
        """
[agent_runner]
[agent_runner.lifecycle_agents]
verifier = "codex"
""",
    )
    assert config.validation.verifier_agent == "auto"
    assert resolve_lifecycle_agent("verifier", config, selected_agent="claude") == "codex"


def test_repository_layer_wins_over_global_layer(tmp_path: Path, config_toml: Path) -> None:
    """rv-1：仓库层（.iar.toml）赢过全局层（config.toml）。"""
    repo_path = _init_git_repository(tmp_path, "repo")
    (repo_path / ".iar.toml").write_text(
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nfix = "pi"\n',
        encoding="utf-8",
    )
    _global_config_from_toml(
        config_toml,
        """
[agent_runner]
[agent_runner.lifecycle_agents]
fix = "kimi"
""",
    )
    contexts = resolve_repository_targets(AgentRunnerSettings(), repo_path_override=str(repo_path))
    config = contexts[0].config
    assert config.lifecycle_agents.global_layer["fix"] == "kimi"
    assert config.lifecycle_agents.repository_layer["fix"] == "pi"
    assert resolve_lifecycle_agent("fix", config, selected_agent="codex") == "pi"


def test_repository_layer_unset_falls_back_to_global(tmp_path: Path, config_toml: Path) -> None:
    """rv-1：仓库层未声明的键回落全局层。"""
    repo_path = _init_git_repository(tmp_path, "repo")
    (repo_path / ".iar.toml").write_text("[agent_runner]\n", encoding="utf-8")
    _global_config_from_toml(
        config_toml,
        """
[agent_runner]
[agent_runner.lifecycle_agents]
fix = "kimi"
""",
    )
    contexts = resolve_repository_targets(AgentRunnerSettings(), repo_path_override=str(repo_path))
    config = contexts[0].config
    assert resolve_lifecycle_agent("fix", config, selected_agent="codex") == "kimi"


def test_prd_override_wins_over_matrix(tmp_path: Path, config_toml: Path) -> None:
    """rv-1：PRD 文件头部覆盖赢过矩阵（仓库层与全局层）。"""
    repo_path = _init_git_repository(tmp_path, "repo")
    (repo_path / ".iar.toml").write_text(
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nimplementation = "kimi"\n',
        encoding="utf-8",
    )
    _global_config_from_toml(
        config_toml,
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nimplementation = "codex"\n',
    )
    config = resolve_repository_targets(AgentRunnerSettings(), repo_path_override=str(repo_path))[
        0
    ].config
    overrides = {"implementation": "claude"}
    assert (
        resolve_lifecycle_agent(
            "implementation",
            config,
            issue=_issue(),
            selected_agent="codex",
            prd_overrides=overrides,
        )
        == "claude"
    )
    # 未在 PRD 覆盖里的阶段不受影响。
    assert resolve_lifecycle_agent("verifier", config, selected_agent="codex") == "claude"


def test_prd_override_only_affects_declared_keys(config_toml: Path) -> None:
    """rv-1：PRD 覆盖只影响声明了的键。"""
    config = _global_config_from_toml(config_toml, "[agent_runner]\n")
    overrides = parse_prd_lifecycle_overrides(
        "# PRD\n\n- lifecycle_agents:\n  - review: codex\n  - implementation: claude\n"
    )
    assert (
        resolve_lifecycle_agent(
            "review", config, issue=_issue(), selected_agent="kimi", prd_overrides=overrides
        )
        == "codex"
    )
    assert (
        resolve_lifecycle_agent(
            "supervisor", config, issue=_issue(), selected_agent="kimi", prd_overrides=overrides
        )
        == "kimi"
    )


def test_fix_closeout_default_follow_executor_and_matrix_switches_agent(config_toml: Path) -> None:
    """rv-2 的解析侧：fix/closeout 默认跟随实现者，矩阵显式指定后换人。"""
    default_config = _global_config_from_toml(config_toml, "[agent_runner]\n")
    assert resolve_lifecycle_agent("fix", default_config, selected_agent="codex") == "codex"
    assert resolve_lifecycle_agent("closeout", default_config, selected_agent="codex") == "codex"

    switched_config = _global_config_from_toml(
        config_toml,
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nfix = "kimi"\ncloseout = "pi"\n',
    )
    assert resolve_lifecycle_agent("fix", switched_config, selected_agent="codex") == "kimi"
    assert resolve_lifecycle_agent("closeout", switched_config, selected_agent="codex") == "pi"


def test_executor_rejected_for_non_executor_lifecycle(config_toml: Path) -> None:
    """executor 只对 fix/closeout 合法，其余键在配置加载期报错。"""
    config_toml.write_text(
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nverifier = "executor"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        AgentRunnerSettings()


def test_auto_rejected_for_stage_without_auto_semantics(config_toml: Path) -> None:
    """planner / content_generation / fix / closeout 不接受 auto。"""
    for lifecycle_key in ("planner", "content_generation", "fix", "closeout"):
        with pytest.raises(ValidationError):
            AgentRunnerLifecycleAgentsSettings(**{lifecycle_key: "auto"})


def test_unknown_lifecycle_key_rejected_at_load(config_toml: Path) -> None:
    """键名闭集：写错键（如 implemenation）在加载期报错，不静默忽略。"""
    config_toml.write_text(
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nimplemenation = "claude"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        AgentRunnerSettings()


def test_unregistered_agent_fails_fast_with_stage_name(config_toml: Path) -> None:
    """rv-5：未注册 agent 名阶段前 fail-fast，错误信息含阶段名与 agent 名。"""
    config = _global_config_from_toml(
        config_toml,
        '[agent_runner]\n[agent_runner.lifecycle_agents]\nfix = "no-such-agent"\n',
    )
    with pytest.raises(UnknownAgentError) as exc_info:
        resolve_lifecycle_agent("fix", config, selected_agent="codex")
    assert "fix" in str(exc_info.value)
    assert "no-such-agent" in str(exc_info.value)


def test_unregistered_prd_override_fails_fast(config_toml: Path) -> None:
    """rv-5：PRD 覆盖里的未注册 agent 同样 fail-fast。"""
    config = _global_config_from_toml(config_toml, "[agent_runner]\n")
    with pytest.raises(UnknownAgentError):
        resolve_lifecycle_agent(
            "review",
            config,
            issue=_issue(),
            selected_agent="codex",
            prd_overrides={"review": "not-a-real-agent"},
        )


def test_auto_semantics_per_stage_are_preserved(config_toml: Path) -> None:
    """rv-1：矩阵声明 auto 时按各阶段既有语义解析，不统一成标签路由。"""
    config = _global_config_from_toml(
        config_toml,
        """
[agent_runner]
[agent_runner.lifecycle_agents]
implementation = "auto"
verifier = "auto"
review = "auto"
supervisor = "auto"
""",
    )
    # 实现=标签路由
    assert (
        resolve_lifecycle_agent(
            "implementation",
            config,
            issue=_issue(labels=("agent/kimi",)),
            selected_agent="codex",
        )
        == "kimi"
    )
    # 校验=回退链上第一个 ≠ 实现者
    assert resolve_lifecycle_agent("verifier", config, selected_agent="codex") == "claude"
    # 审核=allow_same_agent 为真时沿用实现者
    assert resolve_lifecycle_agent("review", config, selected_agent="kimi") == "kimi"
    # 监督=沿用本次实现者
    assert resolve_lifecycle_agent("supervisor", config, selected_agent="kimi") == "kimi"


def test_auto_review_picks_different_agent_when_same_not_allowed(
    tmp_path: Path, config_toml: Path
) -> None:
    """审核 auto 且 allow_same_agent=false 时从注册表取第一个 ≠ 实现者。"""
    repo_path = _init_git_repository(tmp_path, "repo")
    (repo_path / ".iar.toml").write_text(
        '[agent_runner]\n[agent_runner.pre_pr_review]\nreview_agent = "auto"\n'
        "allow_same_agent = false\n",
        encoding="utf-8",
    )
    config = resolve_repository_targets(AgentRunnerSettings(), repo_path_override=str(repo_path))[
        0
    ].config
    resolved = resolve_lifecycle_agent("review", config, issue=_issue(), selected_agent="codex")
    assert resolved != "codex"
    assert resolved in config.agents


def test_executor_requires_selected_agent(config_toml: Path) -> None:
    """fix 声明 executor 但调用方没有实现者时明确报错，不静默回落。"""
    config = _global_config_from_toml(config_toml, "[agent_runner]\n")
    with pytest.raises(ValueError):
        resolve_lifecycle_agent("fix", config)


def test_content_generation_matrix_value_reaches_generated_content_config(
    config_toml: Path,
) -> None:
    """内容生成阶段的矩阵值装配进 GeneratedContentConfig 供所有调用方使用。"""
    config = _global_config_from_toml(
        config_toml,
        '[agent_runner]\n[agent_runner.lifecycle_agents]\ncontent_generation = "kimi"\n',
    )
    assert config.generated_content.lifecycle_default_agent == "kimi"


def test_auto_declared_by_matrix_follows_concrete_legacy_keys() -> None:
    """矩阵声明 ``auto`` 时沿用既有配置键的具体 agent，与 runner 实际取值一致。

    ``auto`` 的语义是"沿用该阶段既有语义"，不是"跳过既有键"；否则 console 呈递的
    生效值会与 runner 实际使用的 agent 分叉（见 PR #147 审核发现）。
    """
    from backend.core.shared.models.agent_runner import (
        PrePrReviewConfig,
        PostPrSupervisorConfig,
        ValidationConfig,
    )
    from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
    from backend.core.use_cases.run_agent_once import (
        resolve_reviewer_agent,
        resolve_supervisor_agent,
    )
    from backend.core.use_cases.run_verifier_agent import _choose_verifier_agent

    config = AppConfig(
        runner=RunnerConfig(agent_fallback_order=("codex", "claude")),
        validation=ValidationConfig(verifier_agent="kimi"),
        pre_pr_review=PrePrReviewConfig(review_agent="codex", allow_same_agent=True),
        post_pr_supervisor=PostPrSupervisorConfig(supervisor_agent="kimi"),
        lifecycle_agents=LifecycleAgentsConfig(
            global_layer={"verifier": "auto", "review": "auto", "supervisor": "auto"}
        ),
    )
    issue = _issue()

    # 呈递视图（无 selected_agent 上下文）与 runner 实际解析必须一致。
    assert resolve_lifecycle_agent("verifier", config) == "kimi"
    assert _choose_verifier_agent(config, "claude") == "kimi"
    assert (
        resolve_lifecycle_agent("review", config, issue=issue, selected_agent="claude") == "codex"
    )
    assert resolve_reviewer_agent(issue, config, "claude") == "codex"
    assert (
        resolve_lifecycle_agent("supervisor", config, issue=issue, selected_agent="claude")
        == "kimi"
    )
    assert resolve_supervisor_agent(issue, config, "auto", fallback_agent="claude") == "kimi"


def test_fallback_order_still_driven_by_runner_config() -> None:
    """回退顺序仍由 [agent_runner.runner] 驱动（矩阵只选主 agent）。"""
    config = AppConfig(runner=RunnerConfig(agent_fallback_order=("kimi",)))
    assert resolve_lifecycle_agent("verifier", config, selected_agent="codex") == "kimi"


# ── PRD 文件头部块解析与写回 ─────────────────────────────────────────────────


def test_parse_prd_overrides_handles_absent_block() -> None:
    """没有覆盖块时返回空 dict。"""
    assert parse_prd_lifecycle_overrides("# PRD\n\n- GitHub Issue: x\n") == {}


def test_parse_prd_overrides_rejects_unknown_key() -> None:
    """未知键名报错并带上 PRD 路径。"""
    with pytest.raises(ValueError) as exc_info:
        parse_prd_lifecycle_overrides(
            "# PRD\n\n- lifecycle_agents:\n  - implemenation: claude\n",
            prd_path="tasks/pending/x.md",
        )
    assert "implemenation" in str(exc_info.value)
    assert "tasks/pending/x.md" in str(exc_info.value)


def test_parse_prd_overrides_rejects_executor_on_wrong_stage() -> None:
    """PRD 覆盖里 executor 用在非 fix/closeout 报错。"""
    with pytest.raises(ValueError):
        parse_prd_lifecycle_overrides("# PRD\n\n- lifecycle_agents:\n  - verifier: executor\n")


def test_parse_prd_overrides_ignores_body_mention() -> None:
    """正文里引用该语法的 bullet 不会被当成覆盖块（只扫头部 bullet 区）。"""
    body_mention = (
        "# PRD: Demo\n\n"
        "- GitHub Issue: x\n\n"
        "## §8 Delivery Dependencies\n\n"
        "- lifecycle_agents:\n  - implementation: codex\n\n"
        "## Body\nrest\n"
    )
    assert parse_prd_lifecycle_overrides(body_mention, prd_path="tasks/pending/x.md") == {}


def test_upsert_prd_overrides_leaves_body_mention_intact() -> None:
    """写回只落在头部 bullet 区，正文里的同名 bullet 不被改写也不被当覆盖。"""
    body_mention = (
        "# PRD: Demo\n\n"
        "- GitHub Issue: x\n\n"
        "## §8 Delivery Dependencies\n\n"
        "- lifecycle_agents:\n  - implementation: codex\n\n"
        "## Body\nrest\n"
    )
    updated = upsert_prd_lifecycle_overrides(body_mention, {"review": "kimi"})
    assert parse_prd_lifecycle_overrides(updated) == {"review": "kimi"}
    # 正文那一段原样保留。
    assert (
        "## §8 Delivery Dependencies\n\n- lifecycle_agents:\n  - implementation: codex" in updated
    )


def test_parse_prd_overrides_rejects_empty_value() -> None:
    """空取值显式报错，不再因模式不匹配而把块截断、静默丢掉后续条目。"""
    with pytest.raises(ValueError) as exc_info:
        parse_prd_lifecycle_overrides(
            "# PRD\n\n- lifecycle_agents:\n  - implementation:\n  - review: codex\n"
        )
    assert "implementation" in str(exc_info.value)


def test_upsert_prd_overrides_preserves_crlf_line_endings() -> None:
    """CRLF 的 PRD 写回后仍保持 CRLF，不整篇转成 LF。"""
    original = "# PRD: Demo\r\n\r\n- GitHub Issue: x\r\n\r\n## 1. Intro\r\n正文\r\n"
    updated = upsert_prd_lifecycle_overrides(original, {"implementation": "claude"})
    assert "\r\n" in updated
    # 没有任何裸 LF（即所有换行都还是 CRLF）。
    assert "\n" not in updated.replace("\r\n", "")
    assert parse_prd_lifecycle_overrides(updated) == {"implementation": "claude"}


def test_upsert_prd_overrides_roundtrip_and_preserves_body() -> None:
    """写回后块可被重新解析，且正文与头部其余内容不变。"""
    original = "# PRD: Demo\n\n- GitHub Issue: （创建后回填）\n\n> ✅ 交付前置：无。\n\n## 1. Intro\n正文\n"
    updated = upsert_prd_lifecycle_overrides(original, {"implementation": "claude"})
    assert parse_prd_lifecycle_overrides(updated) == {"implementation": "claude"}
    assert "## 1. Intro\n正文" in updated
    assert "- GitHub Issue: （创建后回填）" in updated

    # 覆盖集合被替换（不是追加）。
    updated_twice = upsert_prd_lifecycle_overrides(updated, {"review": "codex"})
    assert parse_prd_lifecycle_overrides(updated_twice) == {"review": "codex"}

    # 空集合删除整块。
    removed = upsert_prd_lifecycle_overrides(updated_twice, {})
    assert parse_prd_lifecycle_overrides(removed) == {}
    assert "lifecycle_agents" not in removed
    assert "## 1. Intro" in removed
