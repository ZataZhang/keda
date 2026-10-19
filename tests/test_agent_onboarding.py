"""``agent_onboarding`` 的可用性事实、引导计划与自定义注册测试（rv-1 / rv-3 自动化部分）。

本模块把"这台机器装了哪些 agent"收敛成唯一判定（``shutil.which``），并在**触达
配置文件之前**给出 setup / fix / add 计划：可用选择与缺失选择在计划里必须可区分，
非法引导参数组合在写文件之前拒绝。所有 PATH 判定都换成受控集合，不依赖执行机器装了
什么。启动前门禁（rv-2 的自动化部分）见 ``tests/test_agent_availability_gate.py``。
"""

from __future__ import annotations

import contextlib
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AGENT_PROFILE_JUDGE, AGENT_PROFILE_RUN
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases.agent_onboarding import (
    AgentOnboardingError,
    agent_availability_entries,
    apply_add_plan,
    apply_fix_plan,
    apply_setup_plan,
    availability_available_agents,
    build_agent_add_plan,
    build_fix_plan,
    build_setup_plan,
    list_agent_availability,
    resolve_agent_availability,
    resolve_setup_hint,
)
from backend.core.use_cases.lifecycle_agents_console import build_lifecycle_agents_view
from backend.core.use_cases.lifecycle_settings_console import build_lifecycle_settings_view
from tests.support.agent_availability import install_bins
from tests.support.agent_availability import make_config as _config
from tests.support.agent_availability import make_spec as _spec

# ---------------------------------------------------------------------------
# 测试骨架
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def path_lookup(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """把 ``shutil.which`` 换成受控集合，测试自己声明这台机器装了哪些 bin。

    Returns:
        已安装 bin 名的可变集合；``add`` 即视为 PATH 解析得到。
    """
    return install_bins(monkeypatch)


class RecordingEditor:
    """记录写回序列的假编辑器：验证计划的落盘形态，不触达任何文件。"""

    def __init__(self) -> None:
        self.writes: list[tuple[Any, ...]] = []
        self.transactions = 0

    @property
    def config_path(self) -> Path:
        return Path("/fake/config.toml")

    @contextlib.contextmanager
    def write_transaction(self):
        self.transactions += 1
        yield

    def update_lifecycle_agents(self, values: Any) -> None:
        self.writes.append(("lifecycle_agents", dict(values)))

    def update_lifecycle_presets(self, values: Any) -> None:
        self.writes.append(("lifecycle_presets", dict(values)))

    def update_agent_registration(
        self, agent_name: str, fields: Any, profile_run_fields: Any
    ) -> None:
        self.writes.append(("registration", agent_name, dict(fields), dict(profile_run_fields)))


# ---------------------------------------------------------------------------
# 可用性事实（rv-1 判定来源）
# ---------------------------------------------------------------------------


def test_availability_is_decided_by_path_lookup_only(path_lookup: set[str]) -> None:
    """available 只由 which 决定；命中给出绝对路径，未命中为 None。"""
    availability = resolve_agent_availability("claude", _spec("claude"))
    assert availability.available is False
    assert availability.resolved_path is None

    path_lookup.add("claude")
    installed = resolve_agent_availability("claude", _spec("claude"))
    assert installed.available is True
    assert installed.resolved_path == Path("/fake/bin/claude")
    assert installed.bin == "claude"


def test_declared_profiles_follow_stable_profile_order(path_lookup: set[str]) -> None:
    """用途键按 AGENT_PROFILES 的稳定顺序呈现，与注册字典插入顺序无关。"""
    availability = resolve_agent_availability(
        "kimi", _spec("kimi", (AGENT_PROFILE_JUDGE, AGENT_PROFILE_RUN))
    )
    assert availability.declared_profiles == (AGENT_PROFILE_RUN, AGENT_PROFILE_JUDGE)


def test_listing_preserves_registry_order_and_filters_installed(path_lookup: set[str]) -> None:
    """列表顺序即注册顺序；引导菜单候选只保留解析得到的 agent。"""
    config = _config()
    path_lookup.add("claude")

    listed = list_agent_availability(config.agents)
    assert [item.agent for item in listed] == ["claude", "kimi", "ghost"]
    assert [item.agent for item in availability_available_agents(config.agents)] == ["claude"]


def test_availability_entries_document_is_the_shared_shape(path_lookup: set[str]) -> None:
    """机读条目键名与顺序固定：CLI 与 Console 视图共用同一份序列化。"""
    config = _config()
    path_lookup.add("claude")

    entries = agent_availability_entries(config.agents)
    assert [entry["agent"] for entry in entries] == ["claude", "kimi", "ghost"]
    assert set(entries[0]) == {"agent", "bin", "available", "bin_path", "declared_profiles"}
    assert entries[0]["available"] is True and entries[0]["bin_path"] == "/fake/bin/claude"
    assert entries[1]["available"] is False and entries[1]["bin_path"] is None
    assert entries[2]["bin"] == "ghostbin"


# ---------------------------------------------------------------------------
# 首启提示判定（FR-4）
# ---------------------------------------------------------------------------


def test_hint_asks_when_no_explicit_selection_exists(path_lookup: set[str]) -> None:
    """auto 不是具体选择：首次使用需要提示一次。"""
    hint = resolve_setup_hint(_config())
    assert (hint.needed, hint.reason, hint.agent, hint.bin) == (True, "no-selection", None, None)


def test_hint_is_silent_when_selection_is_usable(path_lookup: set[str]) -> None:
    """已选并且装好了，就不再提示。"""
    path_lookup.add("claude")
    hint = resolve_setup_hint(_config(lifecycle_agents={"implementation": "claude"}))
    assert (hint.needed, hint.reason, hint.agent) == (False, "ok", "claude")


def test_hint_points_at_missing_or_unregistered_agent(path_lookup: set[str]) -> None:
    """选择指向没装的 CLI 或未注册 agent 时都要提示，并点名 bin。"""
    hint = resolve_setup_hint(_config(lifecycle_agents={"implementation": "kimi"}))
    assert (hint.needed, hint.reason, hint.bin) == (True, "unavailable", "kimi")

    unregistered = resolve_setup_hint(_config(lifecycle_agents={"implementation": "nobody"}))
    assert (unregistered.needed, unregistered.reason, unregistered.bin) == (
        True,
        "unavailable",
        None,
    )


def test_hint_reads_the_effective_binding_not_the_matrix(path_lookup: set[str]) -> None:
    """预设绑定优先：提示判定与运行期解析口径一致。"""
    path_lookup.add("claude")
    config = _config(
        presets={"team": AgentModelPreset(agent="claude")},
        lifecycle_presets={"implementation": "team"},
        lifecycle_agents={"implementation": "kimi"},
    )
    hint = resolve_setup_hint(config)
    assert (hint.needed, hint.agent) == (False, "claude")


# ---------------------------------------------------------------------------
# setup 计划（rv-1 的自动化部分）
# ---------------------------------------------------------------------------


def test_setup_plan_rejects_bad_argument_combinations(path_lookup: set[str]) -> None:
    """阶段名非法、``--agent`` / ``--preset`` 没给或同时给都在触文件前拒绝。"""
    config = _config()
    with pytest.raises(AgentOnboardingError, match="unknown lifecycle stage"):
        build_setup_plan(config, stage="vibes", agent="claude")
    with pytest.raises(AgentOnboardingError, match="exactly one of"):
        build_setup_plan(config, stage="implementation")
    with pytest.raises(AgentOnboardingError, match="exactly one of"):
        build_setup_plan(config, stage="implementation", agent="claude", preset="team")


def test_setup_plan_classifies_agent_failures_by_kind(path_lookup: set[str]) -> None:
    """未注册 → usage(2)；没装 → not_found(3)：退出码归类由 kind 决定。"""
    config = _config()
    with pytest.raises(AgentOnboardingError, match="not registered") as unregistered:
        build_setup_plan(config, stage="implementation", agent="nobody")
    assert unregistered.value.kind == "usage"

    with pytest.raises(AgentOnboardingError, match="not available on PATH") as missing:
        build_setup_plan(config, stage="implementation", agent="kimi")
    assert missing.value.kind == "not_found"


def test_setup_plan_rejects_unknown_or_unusable_preset(path_lookup: set[str]) -> None:
    """预设未定义是用法错误；预设的 agent 没装按未找到归类。"""
    config = _config(presets={"fast": AgentModelPreset(agent="kimi")})
    with pytest.raises(AgentOnboardingError, match="is not defined") as undefined:
        build_setup_plan(config, stage="implementation", preset="gone")
    assert undefined.value.kind == "usage"

    with pytest.raises(AgentOnboardingError, match="not available on PATH") as unusable:
        build_setup_plan(config, stage="implementation", preset="fast")
    assert unusable.value.kind == "not_found"


def test_setup_plan_marks_the_masking_preset_for_unbind(path_lookup: set[str]) -> None:
    """写矩阵必须解绑遮蔽它的同阶段全局预设，否则选择永不生效。"""
    path_lookup.add("claude")
    config = _config(
        presets={"fast": AgentModelPreset(agent="claude")},
        lifecycle_presets={"implementation": "fast"},
    )
    plan = build_setup_plan(config, stage="implementation", agent="claude")
    assert plan.agent == "claude" and plan.preset is None
    assert plan.unbind_stage_preset is True
    assert plan.already_satisfied is False

    # 仓库层预设不属于全局层：setup 写全局矩阵不会被它遮蔽，也不越层解绑。
    repository_bound = dataclasses.replace(
        config,
        lifecycle_presets=LifecycleAgentsConfig(repository_layer={"implementation": "fast"}),
    )
    assert (
        build_setup_plan(
            repository_bound, stage="implementation", agent="claude"
        ).unbind_stage_preset
        is False
    )


def test_setup_plan_is_idempotent_and_only_reports_global_layer(path_lookup: set[str]) -> None:
    """全局层已等于目标状态时 already_satisfied 为真（重复执行不写文件）。"""
    path_lookup.add("claude")
    config = _config(lifecycle_agents={"implementation": "claude"})
    assert (
        build_setup_plan(config, stage="implementation", agent="claude").already_satisfied is True
    )

    # 同一阶段在仓库层另有声明：全局层已经满足，setup 的判等只看全局层。
    repo_override = dataclasses.replace(
        config,
        lifecycle_agents=LifecycleAgentsConfig(
            global_layer={"implementation": "claude"},
            repository_layer={"implementation": "kimi"},
        ),
    )
    assert (
        build_setup_plan(repo_override, stage="implementation", agent="claude").already_satisfied
        is True
    )


def test_setup_plan_for_preset_selection(path_lookup: set[str]) -> None:
    """选预设写 ``lifecycle_presets``，且不矩阵路径上不声明 unbind。"""
    path_lookup.add("claude")
    config = _config(presets={"team": AgentModelPreset(agent="claude")})
    plan = build_setup_plan(config, stage="review", preset="team")
    assert (plan.stage, plan.agent, plan.preset) == ("review", None, "team")
    assert plan.unbind_stage_preset is False
    assert plan.already_satisfied is False
    assert (
        build_setup_plan(
            _config(
                presets={"team": AgentModelPreset(agent="claude")},
                lifecycle_presets={"review": "team"},
            ),
            stage="review",
            preset="team",
        ).already_satisfied
        is True
    )


def test_apply_setup_plan_write_sequence(path_lookup: set[str]) -> None:
    """落盘顺序：先解绑遮蔽预设，再写矩阵；已满足则一次锁都不取。"""
    path_lookup.add("claude")
    editor = RecordingEditor()
    apply_setup_plan(editor, build_setup_plan(_config(), stage="implementation", agent="claude"))
    assert editor.transactions == 1
    assert editor.writes == [("lifecycle_agents", {"implementation": "claude"})]

    masked = _config(
        presets={"fast": AgentModelPreset(agent="claude")},
        lifecycle_presets={"implementation": "fast"},
    )
    masked_editor = RecordingEditor()
    apply_setup_plan(
        masked_editor, build_setup_plan(masked, stage="implementation", agent="claude")
    )
    assert masked_editor.writes == [
        ("lifecycle_presets", {"implementation": None}),
        ("lifecycle_agents", {"implementation": "claude"}),
    ]

    preset_editor = RecordingEditor()
    apply_setup_plan(
        preset_editor,
        build_setup_plan(
            _config(presets={"team": AgentModelPreset(agent="claude")}),
            stage="review",
            preset="team",
        ),
    )
    assert preset_editor.writes == [("lifecycle_presets", {"review": "team"})]


def test_apply_setup_plan_skips_write_when_already_satisfied(path_lookup: set[str]) -> None:
    """already_satisfied 的计划不取写锁、不写任何键。"""
    path_lookup.add("claude")
    config = _config(lifecycle_agents={"implementation": "claude"})
    editor = RecordingEditor()
    apply_setup_plan(editor, build_setup_plan(config, stage="implementation", agent="claude"))
    assert editor.transactions == 0 and editor.writes == []


# ---------------------------------------------------------------------------
# --fix 计划
# ---------------------------------------------------------------------------


def test_fix_plan_lists_only_global_bindings_to_unusable_agents(path_lookup: set[str]) -> None:
    """--fix 只清理全局层里指向不可用 agent 的显式绑定，并给出点名理由。"""
    path_lookup.add("claude")
    config = _config(
        presets={"fast": AgentModelPreset(agent="kimi"), "ok": AgentModelPreset(agent="claude")}
    )
    config = dataclasses.replace(
        config,
        lifecycle_agents=LifecycleAgentsConfig(
            global_layer={"verifier": "kimi", "implementation": "claude", "archive": "auto"},
            repository_layer={"pr_drafting": "kimi"},
        ),
        lifecycle_presets=LifecycleAgentsConfig(
            global_layer={"review": "gone", "summary": "fast", "iteration": "ok"},
            repository_layer={"iteration": "fast"},
        ),
    )

    entries = build_fix_plan(config)
    by_stage_layer = {(entry.stage, entry.layer) for entry in entries}
    assert by_stage_layer == {
        ("verifier", "lifecycle_agents"),
        ("review", "lifecycle_presets"),
        ("summary", "lifecycle_presets"),
    }
    reasons = {(entry.stage, entry.layer): entry.reason for entry in entries}
    assert "is not defined" in reasons[("review", "lifecycle_presets")]
    assert "not available on PATH" in reasons[("summary", "lifecycle_presets")]
    assert reasons[("verifier", "lifecycle_agents")] == (
        "agent 'kimi' (bin 'kimi') is not available on PATH"
    )
    # 可用的 claude 与 auto / executor 特殊令牌不动；仓库层绑定永不清理。
    assert ("implementation", "lifecycle_agents") not in by_stage_layer
    assert ("archive", "lifecycle_agents") not in by_stage_layer
    assert ("pr_drafting", "lifecycle_agents") not in by_stage_layer


def test_fix_plan_flags_unregistered_matrix_value(path_lookup: set[str]) -> None:
    """矩阵指向未注册 agent：理由点名"not registered"。"""
    config = _config(lifecycle_agents={"review": "nobody"})
    entries = build_fix_plan(config)
    assert [(entry.stage, entry.value) for entry in entries] == [("review", "nobody")]
    assert "not registered" in entries[0].reason


def test_apply_fix_plan_batches_unbinds_in_one_transaction(path_lookup: set[str]) -> None:
    """一条锁内批删两段绑定；空计划完全不取锁。"""
    config = _config(
        presets={"fast": AgentModelPreset(agent="kimi")},
        lifecycle_agents={"verifier": "kimi"},
        lifecycle_presets={"review": "fast"},
    )
    editor = RecordingEditor()
    apply_fix_plan(editor, build_fix_plan(config))
    assert editor.transactions == 1
    assert editor.writes == [
        ("lifecycle_presets", {"review": None}),
        ("lifecycle_agents", {"verifier": None}),
    ]

    clean = RecordingEditor()
    apply_fix_plan(clean, build_fix_plan(_config()))
    assert clean.transactions == 0 and clean.writes == []


# ---------------------------------------------------------------------------
# 自定义注册计划（rv-3 的自动化部分）
# ---------------------------------------------------------------------------


def test_add_plan_previews_the_registration_block(path_lookup: set[str]) -> None:
    """默认只预览：素材是可解析的注册块文本，且只声明 run 用途。"""
    plan = build_agent_add_plan(
        _config(),
        "myagent",
        bin_name="myagent",
        args=("--print",),
        prompt_delivery="argv_tail",
    )
    assert plan.table_fields == {"bin": "myagent", "label": "agent/myagent"}
    assert plan.profile_fields == {
        "args": ["--print"],
        "prompt_delivery": "argv_tail",
        "output_protocol": "plain",
    }
    assert list(plan.agent_spec.profiles) == [AGENT_PROFILE_RUN]
    preview = plan.toml_preview
    assert "[agent_runner.agents.myagent]" in preview
    assert "[agent_runner.agents.myagent.profiles.run]" in preview
    assert '"--print"' in preview
    assert plan.agent_spec.label_color == "5319E7"


def test_add_plan_honours_optional_fields(path_lookup: set[str]) -> None:
    """``--auth-home`` / ``--label`` / ``prompt_flag`` 只在给出时进入注册块。"""
    plan = build_agent_add_plan(
        _config(),
        "myagent",
        bin_name="myagent",
        prompt_delivery="flag",
        prompt_flag="--prompt",
        auth_home="~/.myagent",
        label="agent/custom",
    )
    assert plan.table_fields["auth_home"] == "~/.myagent"
    assert plan.table_fields["label"] == "agent/custom"
    assert plan.profile_fields["prompt_flag"] == "--prompt"


def test_add_plan_rejects_invalid_registrations_before_touching_files(
    path_lookup: set[str],
) -> None:
    """名称非法 / 重名 / 空 bin / 非法投递方式 / 缺 prompt_flag / 协议未注册均拒绝。"""
    config = _config()

    def _expect(message: str, agent_name: str = "myagent", **kwargs: Any) -> None:
        arguments: dict[str, Any] = {"bin_name": "myagent", **kwargs}
        with pytest.raises(AgentOnboardingError, match=message) as exc_info:
            build_agent_add_plan(config, agent_name, **arguments)
        assert exc_info.value.kind == "usage"

    _expect("invalid agent name", "9agent")
    _expect("invalid agent name", "-leading-dash")
    _expect("invalid agent name", "")
    _expect("already registered", "claude")
    _expect("non-empty executable", bin_name="   ")
    _expect("invalid --prompt-delivery", prompt_delivery="chat")
    _expect("requires --prompt-flag", prompt_delivery="flag")
    _expect("is not registered", output_protocol="nope")


def test_added_registration_assembles_argv_on_the_runtime_path(path_lookup: set[str]) -> None:
    """计划阶段用真正的组装函数演算过一次：注册块拿出去就能拼出命令。"""
    from backend.core.use_cases.agent_invocation import build_agent_invocation

    config = _config()
    plan = build_agent_add_plan(
        config,
        "myagent",
        bin_name="myagent",
        args=("--print",),
        prompt_delivery="flag",
        prompt_flag="--prompt",
    )
    candidate = dataclasses.replace(
        config, agents={**config.agents, plan.agent_name: plan.agent_spec}
    )

    invocation = build_agent_invocation(
        "myagent", AGENT_PROFILE_RUN, "golden-prompt", Path.cwd(), candidate
    )
    assert invocation.argv[0] == "myagent"
    assert "--print" in invocation.argv
    assert list(invocation.argv[invocation.argv.index("--prompt") + 1 :]) == ["golden-prompt"]


def test_apply_add_plan_writes_the_validated_block(path_lookup: set[str]) -> None:
    """校验通过的计划经同一把写锁落到全局配置。"""
    editor = RecordingEditor()
    plan = build_agent_add_plan(_config(), "myagent", bin_name="myagent")
    apply_add_plan(editor, plan)
    assert editor.transactions == 1
    assert editor.writes == [("registration", "myagent", plan.table_fields, plan.profile_fields)]


# ---------------------------------------------------------------------------
# 视图共用同一份可用性事实（rv-4 的后端侧）
# ---------------------------------------------------------------------------


def test_lifecycle_views_expose_the_same_availability_facts(path_lookup: set[str]) -> None:
    """矩阵视图与聚合设置视图的 ``agent_availability`` 共用 core 的同一份序列化。"""
    path_lookup.add("claude")
    # 内置注册表：各阶段的内置默认 agent 都在表里，视图构建才会走完整解析链。
    config = AppConfig()
    expected = agent_availability_entries(config.agents)

    matrix_view = build_lifecycle_agents_view(config, scope="global")
    assert matrix_view["agent_availability"] == expected
    assert matrix_view["agents"] == list(config.agents)

    settings_view = build_lifecycle_settings_view(config, scope="global")
    assert settings_view["agent_availability"] == expected
    assert [entry["agent"] for entry in expected if entry["available"]] == ["claude"]
    assert {entry["bin"] for entry in expected} >= {"claude", "kimi"}
