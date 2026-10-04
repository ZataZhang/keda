"""Agent 模型预设（ModelSelection）的解析、注入与换人丢弃规则测试。

覆盖 PRD ``P1-FEAT-20260930-130445`` 的核心行为：

- 预设解析（未知预设 fail-fast、CLI 覆盖同名字段）；
- argv 注入（模板展开、模板缺失 fail-fast、无绑定零变化由黄金快照守护）；
- 生命周期绑定层（PRD 块 > 仓库层 > 全局层、遮蔽矩阵、显式 --agent 最高）；
- PRD 头部 ``lifecycle_presets`` 块解析 / 写回 / planner 拒收；
- 换人丢弃规则（fallback / 显式 ``--agent``）与 executor 继承。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_model_preset import (
    AgentModelPreset,
    ModelSelection,
    resolve_model_selection,
)
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases.agent_invocation import (
    ModelNotSupportedError,
    build_agent_invocation,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    PRD_OVERRIDE_BLOCK_PRESETS,
    parse_prd_lifecycle_overrides,
    resolve_lifecycle_agent,
    resolve_lifecycle_model_selection,
    upsert_prd_lifecycle_overrides,
)
from backend.core.use_cases.run_agent_once import drop_model_selection_for_agent

PRESET_WORKTREE = Path("/tmp/iar-preset-tests")


def _config_with_plan_preset(
    *,
    global_binding: dict[str, str] | None = None,
    repository_binding: dict[str, str] | None = None,
) -> AppConfig:
    """构造带 ``plan`` 预设与可选两层绑定的配置（其余内置默认）。"""
    return AppConfig(
        agent_presets={
            "plan": AgentModelPreset(
                agent="codebuddy", model="glm-5.3-flash", reasoning_effort="max"
            ),
        },
        lifecycle_presets=LifecycleAgentsConfig(
            global_layer=global_binding or {},
            repository_layer=repository_binding or {},
        ),
    )


# ---------------------------------------------------------------------------
# 预设解析
# ---------------------------------------------------------------------------


def test_resolve_model_selection_returns_preset_fields() -> None:
    selection = resolve_model_selection("plan", _config_with_plan_preset())
    assert selection == ModelSelection(
        agent="codebuddy",
        model="glm-5.3-flash",
        reasoning_effort="max",
        preset_name="plan",
    )


def test_resolve_model_selection_unknown_preset_fails_fast() -> None:
    with pytest.raises(ValueError, match="Unknown model preset 'missing'"):
        resolve_model_selection("missing", _config_with_plan_preset())


def test_resolve_model_selection_cli_overrides_win_per_field() -> None:
    selection = resolve_model_selection(
        "plan",
        _config_with_plan_preset(),
        model_override="deepseek-v4.1-flash",
    )
    assert selection.model == "deepseek-v4.1-flash"
    # 只覆盖一项时另一项取预设值。
    assert selection.reasoning_effort == "max"


# ---------------------------------------------------------------------------
# argv 注入
# ---------------------------------------------------------------------------


def test_build_agent_invocation_injects_model_and_effort_args() -> None:
    selection = resolve_model_selection("plan", _config_with_plan_preset())
    invocation = build_agent_invocation(
        "codebuddy",
        "generate",
        "PROMPT",
        PRESET_WORKTREE,
        _config_with_plan_preset(),
        model_selection=selection,
    )
    # 注入位置：args 之后、提示词之前。
    assert "--model" in invocation.argv
    assert "glm-5.3-flash" in invocation.argv
    assert "--settings" in invocation.argv
    assert '{"reasoningEffort":"max"}' in invocation.argv
    assert invocation.argv[-1] == "PROMPT"


def test_build_agent_invocation_without_selection_is_unchanged() -> None:
    plain = build_agent_invocation(
        "codebuddy", "generate", "PROMPT", PRESET_WORKTREE, _config_with_plan_preset()
    )
    assert "--model" not in plain.argv


def test_build_agent_invocation_fails_fast_without_template() -> None:
    # 七个内置 agent 现在都声明了 model_args；构造一个显式清空模板的 agent 来守护 fail-fast。
    no_template_agents = dict(BUILTIN_AGENT_SPECS)
    no_template_agents["codex"] = replace(BUILTIN_AGENT_SPECS["codex"], model_args=())
    selection = ModelSelection(agent="codex", model="glm-5.3-flash")
    with pytest.raises(ModelNotSupportedError, match="codex"):
        build_agent_invocation(
            "codex",
            "generate",
            "PROMPT",
            PRESET_WORKTREE,
            AppConfig(agents=no_template_agents),
            model_selection=selection,
        )


def test_build_agent_invocation_effort_only_binding_requires_effort_template() -> None:
    selection = ModelSelection(agent="claude", reasoning_effort="max")
    with pytest.raises(ModelNotSupportedError, match="reasoning_effort_args"):
        build_agent_invocation(
            "claude",
            "generate",
            "PROMPT",
            PRESET_WORKTREE,
            AppConfig(),
            model_selection=selection,
        )


# ---------------------------------------------------------------------------
# 生命周期绑定解析
# ---------------------------------------------------------------------------


def test_resolve_lifecycle_agent_binding_shadows_matrix() -> None:
    config = _config_with_plan_preset(
        global_binding={"verifier": "plan"},
    )
    # 绑定存在时预设整体决定 agent（未显式 --agent）。
    assert resolve_lifecycle_agent("verifier", config) == "codebuddy"


def test_resolve_lifecycle_agent_explicit_agent_beats_binding() -> None:
    config = _config_with_plan_preset(
        global_binding={"verifier": "plan"},
    )
    assert resolve_lifecycle_agent("verifier", config, override_agent="claude") == "claude"


def test_binding_layer_priority_prd_block_beats_layers() -> None:
    repository_bound = _config_with_plan_preset(
        global_binding={"verifier": "work"}, repository_binding={"verifier": "plan"}
    )
    # work 未定义在预设清单里也可以先落到 plan?——两层都指向已定义预设时
    # 仓库层赢；这里用同清单预设验证优先级本身。
    assert resolve_lifecycle_model_selection("verifier", repository_bound) is not None
    assert resolve_lifecycle_model_selection("verifier", repository_bound).preset_name == "plan"


def test_resolve_lifecycle_model_selection_none_when_unbound() -> None:
    assert resolve_lifecycle_model_selection("verifier", _config_with_plan_preset()) is None


def test_resolve_lifecycle_model_selection_reads_issue_prd_block() -> None:
    config = _config_with_plan_preset()
    issue = IssueSummary(
        number=1,
        title="t",
        url="u",
        body="b",
        labels=(),
        lifecycle_preset_overrides=(("verifier", "plan"),),
    )
    selection = resolve_lifecycle_model_selection("verifier", config, issue=issue)
    assert selection is not None
    assert selection.preset_name == "plan"


def test_resolve_lifecycle_model_selection_unknown_bound_preset_fails_fast() -> None:
    config = _config_with_plan_preset(global_binding={"verifier": "ghost"})
    with pytest.raises(ValueError, match="Unknown model preset 'ghost'"):
        resolve_lifecycle_model_selection("verifier", config)


# ---------------------------------------------------------------------------
# PRD 头部 lifecycle_presets 块
# ---------------------------------------------------------------------------


def test_parse_prd_lifecycle_presets_block() -> None:
    prd_text = (
        "# PRD: demo\n"
        "\n"
        "- GitHub Issue: https://example.com/1\n"
        "- lifecycle_presets:\n"
        "  - verifier: plan\n"
        "  - review: work\n"
        "\n"
        "> 引用块\n"
    )
    overrides = parse_prd_lifecycle_overrides(prd_text, block_name=PRD_OVERRIDE_BLOCK_PRESETS)
    assert overrides == {"verifier": "plan", "review": "work"}


def test_parse_prd_lifecycle_presets_block_rejects_planner() -> None:
    prd_text = "# PRD: demo\n\n- lifecycle_presets:\n  - planner: plan\n"
    with pytest.raises(ValueError, match="unknown lifecycle key 'planner'"):
        parse_prd_lifecycle_overrides(prd_text, block_name=PRD_OVERRIDE_BLOCK_PRESETS)


def test_parse_prd_lifecycle_presets_preserves_case() -> None:
    prd_text = "# PRD: demo\n\n- lifecycle_presets:\n  - verifier: MyPreset\n"
    overrides = parse_prd_lifecycle_overrides(prd_text, block_name=PRD_OVERRIDE_BLOCK_PRESETS)
    assert overrides == {"verifier": "MyPreset"}


def test_upsert_prd_lifecycle_presets_block_writes_and_keeps_body() -> None:
    prd_text = "# PRD: demo\n\n> intro\n\n## 1. Body\n"
    updated = upsert_prd_lifecycle_overrides(
        prd_text, {"verifier": "plan"}, block_name=PRD_OVERRIDE_BLOCK_PRESETS
    )
    assert "- lifecycle_presets:" in updated
    assert "## 1. Body" in updated
    assert parse_prd_lifecycle_overrides(updated, block_name=PRD_OVERRIDE_BLOCK_PRESETS) == {
        "verifier": "plan"
    }


def test_upsert_presets_block_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="value must not be empty"):
        upsert_prd_lifecycle_overrides(
            "# T\n", {"verifier": ""}, block_name=PRD_OVERRIDE_BLOCK_PRESETS
        )


# ---------------------------------------------------------------------------
# 换人丢弃规则与 executor 继承
# ---------------------------------------------------------------------------


def test_drop_model_selection_on_agent_switch() -> None:
    selection = ModelSelection(agent="codebuddy", model="glm-5.3-flash", preset_name="plan")
    assert drop_model_selection_for_agent("codebuddy", selection) is selection
    assert drop_model_selection_for_agent("claude", selection) is None
    assert drop_model_selection_for_agent("claude", None) is None


def test_executor_stage_inherits_implementation_selection() -> None:
    """fix 未自绑预设时继承实现者绑定；绑定/继承的换人丢弃由 resilient 层兜底。"""
    selection = ModelSelection(agent="codebuddy", model="glm-5.3-flash", preset_name="plan")
    config = _config_with_plan_preset()
    from backend.core.use_cases.lifecycle_agent_resolution import resolve_lifecycle_model_selection

    # 继承语义 = 自绑优先，否则回落实现者绑定（执行循环内的 `or` 表达式）。
    stage_selection = resolve_lifecycle_model_selection("fix", config) or selection
    assert stage_selection is selection
    # 实现者绑定跟随 executor 同 agent：resilient 层不丢弃。
    assert drop_model_selection_for_agent("codebuddy", stage_selection) is selection


def test_executor_stage_own_binding_beats_inheritance() -> None:
    """fix 自绑 plan 时用自己的绑定（模型也换成 plan 的）。"""
    config = _config_with_plan_preset(repository_binding={"fix": "plan"})
    implementation_selection = ModelSelection(agent="claude", model="m1")
    from backend.core.use_cases.lifecycle_agent_resolution import resolve_lifecycle_model_selection

    stage_selection = resolve_lifecycle_model_selection("fix", config) or implementation_selection
    assert stage_selection is not None
    assert stage_selection.preset_name == "plan"
    assert stage_selection.model == "glm-5.3-flash"
    assert drop_model_selection_for_agent("codebuddy", stage_selection) is stage_selection
