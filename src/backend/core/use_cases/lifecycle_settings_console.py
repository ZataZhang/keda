"""生命周期模型设置 console 视图与写回校验。

服务统一生命周期设置页：九阶段聚合视图（预设 / 继承 / Agent 默认 / 不支持 /
绑定预设未定义五种来源）、预设 upsert、阶段->预设 绑定、执行器回退候选的写前校验。
scope 与来源词汇表、``LifecycleAgentsUpdateError`` 复用
:mod:`~backend.core.use_cases.lifecycle_agents_console`，跨引用完整性门禁在
:mod:`~backend.core.use_cases.lifecycle_settings_integrity`。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.core.shared.models.agent_model_preset import (
    AgentModelPreset,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
)
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_AGENT_STAGE_LABELS,
    LIFECYCLE_SOURCE_GLOBAL,
    normalize_lifecycle_agent_value,
)
from backend.core.use_cases.agent_candidate_fallback import (
    effective_fallback_candidates,
)
from backend.core.use_cases.agent_onboarding import agent_availability_entries
from backend.core.use_cases.lifecycle_agent_resolution import (
    legacy_configured_agent,
    resolve_lifecycle_agent,
)
from backend.core.use_cases.lifecycle_agents_console import (
    FIELD_SOURCE_AGENT_DEFAULT,
    FIELD_SOURCE_INHERITED,
    FIELD_SOURCE_NOT_SUPPORTED,
    FIELD_SOURCE_PRESET,
    FIELD_SOURCE_PRESET_UNRESOLVED,
    PRESET_SOURCE_GLOBAL,
    PRESET_SOURCE_REPOSITORY_ONLY,
    PRESET_SOURCE_REPOSITORY_OVERRIDES,
    SCOPE_REPOSITORY,
    VALID_SCOPES,
    LifecycleAgentsUpdateError,
    _require_known_lifecycle_key,
    _resolve_bound_selection,
    _resolve_source_layer,
)


def _model_capability(config: AppConfig, agent_name: str | None) -> tuple[bool, bool]:
    """返回某 agent 是否声明了模型 / 推理档参数模板（``model_supported, effort_supported``）。

    可注入性只由注册块是否存在 ``model_args`` / ``reasoning_effort_args`` 模板决定，
    不调用任何外部模型目录。agent 名缺失或未注册时两项均为 ``False``。
    """
    if agent_name is None:
        return (False, False)
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None:
        return (False, False)
    return (bool(agent_spec.model_args), bool(agent_spec.reasoning_effort_args))


def _bound_stages_by_preset(config: AppConfig) -> dict[str, list[str]]:
    """构建"预设名 -> 当前绑定了它的生命周期阶段"映射（仓库层 > 全局层合并视图）。"""
    stages_by_preset: dict[str, list[str]] = {}
    for lifecycle_key in LIFECYCLE_AGENT_KEYS:
        bound_preset = config.lifecycle_presets.declared_value(lifecycle_key)
        if bound_preset is not None:
            stages_by_preset.setdefault(bound_preset, []).append(lifecycle_key)
    return stages_by_preset


def _implementation_selection(
    config: AppConfig,
) -> tuple[str | None, str | None, str | None, bool]:
    """解析实现阶段的基线 (agent, model, reasoning_effort)，供 fix / closeout 继承展示。

    无 Issue / selected_agent 上下文时按 config 解析：绑定预设取预设三元组，否则
    agent 走既有矩阵 / 既有键 / 内置默认，model / effort 未显式配置记为 ``None``。
    返回第四元素 ``resolved`` 标记基线是否可解析：绑定预设缺失（已删除 / 未声明）时
    置 ``False``，三元组均为 ``None``，交由调用方如实标注 :data:`FIELD_SOURCE_PRESET_UNRESOLVED`，
    不得回落到 ``resolve_lifecycle_agent``（那会重新命中同一条悬空绑定并抛错）。
    """
    bound_preset = config.lifecycle_presets.declared_value("implementation")
    if bound_preset is not None:
        bound_selection = _resolve_bound_selection(config, bound_preset)
        if bound_selection is None:
            return (None, None, None, False)
        return (
            bound_selection.agent,
            bound_selection.model,
            bound_selection.reasoning_effort,
            True,
        )
    return (resolve_lifecycle_agent("implementation", config), None, None, True)


def _lifecycle_row_source(key: str, config: AppConfig) -> dict[str, Any]:
    """计算单个生命周期阶段的聚合视图行（不含视图级上下文，仅本阶段事实）。"""
    bound_preset = config.lifecycle_presets.declared_value(key)
    binding_layer = config.lifecycle_presets.declared_layer(key)

    # 情形 A：阶段绑定预设——预设整体决定 (agent, model, effort)，遮蔽矩阵同键声明。
    if bound_preset is not None:
        bound_selection = _resolve_bound_selection(config, bound_preset)
        if bound_selection is None:
            # 绑定指向已被删除 / 未声明的预设：如实标注，不编造生效值、不抛错，
            # 保证九键视图与"写成功后读取不 500"（写路径已在保存前拒绝此类悬空绑定）。
            return {
                "key": key,
                "label": LIFECYCLE_AGENT_STAGE_LABELS.get(key, key),
                "preset_name": bound_preset,
                "effective_agent": None,
                "model": None,
                "reasoning_effort": None,
                "field_sources": {
                    "agent": FIELD_SOURCE_PRESET_UNRESOLVED,
                    "preset": binding_layer or LIFECYCLE_SOURCE_GLOBAL,
                    "model": FIELD_SOURCE_PRESET_UNRESOLVED,
                    "reasoning_effort": FIELD_SOURCE_PRESET_UNRESOLVED,
                },
                "is_inherited": False,
                "follows_implementation": False,
                "model_supported": False,
                "reasoning_effort_supported": False,
            }
        effective_agent = bound_selection.agent
        model_supported, effort_supported = _model_capability(config, effective_agent)
        field_sources: dict[str, str] = {
            "agent": FIELD_SOURCE_PRESET,
            "preset": binding_layer or LIFECYCLE_SOURCE_GLOBAL,
        }
        # 模型 / 推理档：有值即预设声明；无值即"不注入 = Agent CLI 默认"。声明了值
        # 但 Agent 缺模板，标记为 not_supported（写回会在建预设 / 绑定阶段拒绝）。
        field_sources["model"] = _value_field_source(bound_selection.model, model_supported)
        field_sources["reasoning_effort"] = _value_field_source(
            bound_selection.reasoning_effort, effort_supported
        )
        return {
            "key": key,
            "label": LIFECYCLE_AGENT_STAGE_LABELS.get(key, key),
            "preset_name": bound_preset,
            "effective_agent": effective_agent,
            "model": bound_selection.model,
            "reasoning_effort": bound_selection.reasoning_effort,
            "field_sources": field_sources,
            "is_inherited": False,
            "follows_implementation": False,
            "model_supported": model_supported,
            "reasoning_effort_supported": effort_supported,
        }

    # 情形 B：fix / closeout 声明 executor（含兼容写回显式落盘的 "executor"）或
    # 未声明（回落既有键的 executor 语义）时，取值整体继承实现阶段解析结果。
    # 判据与 build_lifecycle_agents_view 同口径：归一化"显式声明或既有键"取值，
    # 而不是只看声明是否为 None——否则显式 "executor" 会掉进情形 C 的
    # resolve_lifecycle_agent，在无实现者上下文时抛 ValueError 把视图打成 500。
    declared_or_legacy = config.lifecycle_agents.declared_value(key) or legacy_configured_agent(
        key, config
    )
    follows_implementation = key in LIFECYCLE_AGENT_EXECUTOR_KEYS and (
        normalize_lifecycle_agent_value(declared_or_legacy) == LIFECYCLE_AGENT_EXECUTOR
    )
    if follows_implementation:
        impl_agent, impl_model, impl_effort, impl_resolved = _implementation_selection(config)
        if not impl_resolved:
            # 实现阶段绑定了指向已删除 / 未声明预设的悬空引用：继承基线不可解析，
            # 如实标注 preset_unresolved，绝不回落 resolve（那会重新命中悬空绑定并抛错）。
            dangling_preset = config.lifecycle_presets.declared_value("implementation")
            dangling_layer = config.lifecycle_presets.declared_layer("implementation")
            return {
                "key": key,
                "label": LIFECYCLE_AGENT_STAGE_LABELS.get(key, key),
                "preset_name": dangling_preset,
                "effective_agent": None,
                "model": None,
                "reasoning_effort": None,
                "field_sources": {
                    "agent": FIELD_SOURCE_PRESET_UNRESOLVED,
                    "preset": dangling_layer or LIFECYCLE_SOURCE_GLOBAL,
                    "model": FIELD_SOURCE_PRESET_UNRESOLVED,
                    "reasoning_effort": FIELD_SOURCE_PRESET_UNRESOLVED,
                },
                "is_inherited": True,
                "follows_implementation": True,
                "model_supported": False,
                "reasoning_effort_supported": False,
            }
        model_supported, effort_supported = _model_capability(config, impl_agent)
        return {
            "key": key,
            "label": LIFECYCLE_AGENT_STAGE_LABELS.get(key, key),
            "preset_name": None,
            "effective_agent": impl_agent,
            "model": impl_model,
            "reasoning_effort": impl_effort,
            "field_sources": {
                "agent": FIELD_SOURCE_INHERITED,
                "preset": None,
                "model": _value_field_source(impl_model, model_supported),
                "reasoning_effort": _value_field_source(impl_effort, effort_supported),
            },
            "is_inherited": True,
            "follows_implementation": True,
            "model_supported": model_supported,
            "reasoning_effort_supported": effort_supported,
        }

    # 情形 C：未绑定预设的其余阶段——agent 按既有矩阵 / 既有键 / 内置默认解析，
    # model / effort 无显式来源，一律"Agent CLI 默认 / 未显式指定"。
    effective_agent = resolve_lifecycle_agent(key, config)
    model_supported, effort_supported = _model_capability(config, effective_agent)
    return {
        "key": key,
        "label": LIFECYCLE_AGENT_STAGE_LABELS.get(key, key),
        "preset_name": None,
        "effective_agent": effective_agent,
        "model": None,
        "reasoning_effort": None,
        "field_sources": {
            "agent": _resolve_source_layer(key, config),
            "preset": None,
            "model": _value_field_source(None, model_supported),
            "reasoning_effort": _value_field_source(None, effort_supported),
        },
        "is_inherited": False,
        "follows_implementation": False,
        "model_supported": model_supported,
        "reasoning_effort_supported": effort_supported,
    }


def _value_field_source(value: str | None, supported: bool) -> str:
    """把"某字段是否有值 + Agent 是否支持"折叠成逐字段来源标签。

    Agent 未声明对应参数模板时一律 :data:`FIELD_SOURCE_NOT_SUPPORTED`，包括该字段
    没有显式值的行：既没有可注入的通道，也就没有"Agent 默认 / 未显式指定"可标，
    不能把"不支持"混同成"未配置"（PRD：无法由注册模板注入时页面与 CLI 明确指出不支持）。
    """
    if not supported:
        return FIELD_SOURCE_NOT_SUPPORTED
    return FIELD_SOURCE_AGENT_DEFAULT if value is None else FIELD_SOURCE_PRESET


def _preset_definition_source(
    preset_name: str,
    preset: AgentModelPreset,
    config: AppConfig,
    global_config: AppConfig | None,
    scope: str,
) -> str:
    """判定预设定义的来源层。

    ``scope=global`` 恒为全局；``scope=repository`` 时拿合并配置与独立加载的
    全局配置对比：全局未定义该预设 -> 仓库独有；两边同名同值 -> 沿用全局；
    同名不同值 -> 仓库整体覆盖全局。
    """
    if scope != SCOPE_REPOSITORY or global_config is None:
        return PRESET_SOURCE_GLOBAL
    global_preset = global_config.agent_presets.get(preset_name)
    if global_preset is None:
        return PRESET_SOURCE_REPOSITORY_ONLY
    if global_preset == preset:
        return PRESET_SOURCE_GLOBAL
    return PRESET_SOURCE_REPOSITORY_OVERRIDES


def _fallback_candidate_view(config: AppConfig) -> list[dict[str, Any]]:
    """把有效回退候选折叠成有序视图（1-based 位置、候选 preset 三元组与来源）。"""
    candidates: list[dict[str, Any]] = []
    for position, candidate in enumerate(effective_fallback_candidates(config), start=1):
        bound_preset = candidate.preset
        if bound_preset is not None:
            selection = _resolve_bound_selection(config, bound_preset)
            model_supported, effort_supported = _model_capability(config, candidate.agent)
            if selection is None:
                # 候选绑定预设缺失：如实标注、不抛错（与生命周期行 A 悬空分支同口径）。
                model = reasoning_effort = None
                preset_source: str | None = FIELD_SOURCE_PRESET_UNRESOLVED
                model_source = effort_source = FIELD_SOURCE_PRESET_UNRESOLVED
            else:
                model, reasoning_effort = selection.model, selection.reasoning_effort
                preset_source = FIELD_SOURCE_PRESET
                model_source = _value_field_source(model, model_supported)
                effort_source = _value_field_source(reasoning_effort, effort_supported)
        else:
            model = reasoning_effort = None
            model_supported, effort_supported = _model_capability(config, candidate.agent)
            preset_source = None
            # 与生命周期行情形 C 共用同一判据：未绑定预设时"有模板却没显式值"才是
            # "Agent 默认 / 未显式指定"，没有模板则如实 not_supported。
            model_source = _value_field_source(None, model_supported)
            effort_source = _value_field_source(None, effort_supported)
        candidates.append(
            {
                "position": position,
                "agent": candidate.agent,
                "preset": bound_preset,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "model_supported": model_supported,
                "reasoning_effort_supported": effort_supported,
                "field_sources": {
                    "preset": preset_source,
                    "model": model_source,
                    "reasoning_effort": effort_source,
                },
            }
        )
    return candidates


def build_fallback_candidates_view(config: AppConfig) -> dict[str, Any]:
    """构建「执行器回退候选」编辑视图（有序候选、候选步数预算、可用 agent）。

    候选数组是**机器级** :data:`SCOPE_GLOBAL` 事实源；``scope=repository`` 的合并
    配置里若声明了仓库级候选，同样在此展开成 effective 视图，供页面/CLI 展示。

    Args:
        config: 用于解析有效候选与预设三元组的应用配置。

    Returns:
        含 ``candidates`` / ``max_agent_switches`` / ``budget_by_candidate_step``
        与 ``agents`` 的 JSON 友好字典。
    """
    return {
        "candidates": _fallback_candidate_view(config),
        "max_agent_switches": config.runner.max_agent_switches,
        "budget_by_candidate_step": True,
        "agents": list(config.agents),
    }


def build_lifecycle_settings_view(
    config: AppConfig,
    *,
    scope: str,
    repo_id: str | None = None,
    global_config: AppConfig | None = None,
) -> dict[str, Any]:
    """构建"生命周期矩阵 + 模型预设 + 执行器回退"的聚合读写视图。

    一次返回九个阶段各自的最终生效 (agent, model, reasoning_effort)、逐字段来源、
    继承 / 默认 / 不支持状态，外加预设定义清单（含每个预设当前绑定的阶段）与
    有序回退候选视图。矩阵、预设、回退共用同一份 core 解析结果，页面与 CLI 都取
    这个视图，不各算各的。

    Args:
        config: 该视角下的应用配置（``scope=global`` 传全局；``scope=repository``
            传"全局 + 该仓库 .kedacode.toml"合并后的配置）。
        scope: ``global`` 或 ``repository``。
        repo_id: 仓库级视图的仓库 id；全局视图为 ``None``。
        global_config: ``scope=repository`` 时独立加载的全局配置，用于区分预设来源层。

    Returns:
        含 ``lifecycles`` / ``presets`` / ``fallback`` 三段、``agent_availability``
        与视角标注的 JSON 字典。

    Raises:
        LifecycleAgentsUpdateError: ``scope`` 非法。
    """
    if scope not in VALID_SCOPES:
        raise LifecycleAgentsUpdateError(
            f"Unknown scope '{scope}'. Valid scopes: {', '.join(sorted(VALID_SCOPES))}."
        )

    stages_by_preset = _bound_stages_by_preset(config)
    lifecycles = [_lifecycle_row_source(key, config) for key in LIFECYCLE_AGENT_KEYS]
    # 绑定预设的影响阶段列表：行上回显"改这个预设会牵动哪些阶段"，让编辑共享
    # 预设前就能看见波及范围（同一预设绑到多阶段时列表相同）。
    for lifecycle_row in lifecycles:
        bound_preset = lifecycle_row["preset_name"]
        lifecycle_row["affected_stages"] = (
            list(stages_by_preset.get(bound_preset, [])) if bound_preset is not None else []
        )

    presets: list[dict[str, Any]] = []
    for preset_name, preset in config.agent_presets.items():
        model_supported, effort_supported = _model_capability(config, preset.agent)
        presets.append(
            {
                "name": preset_name,
                "agent": preset.agent,
                "model": preset.model,
                "reasoning_effort": preset.reasoning_effort,
                "model_supported": model_supported,
                "reasoning_effort_supported": effort_supported,
                "bound_stages": list(stages_by_preset.get(preset_name, [])),
                "source": _preset_definition_source(
                    preset_name, preset, config, global_config, scope
                ),
            }
        )

    return {
        "scope": scope,
        "repo_id": repo_id,
        "agents": list(config.agents),
        # 注册表可用性事实（与 Agent-only 矩阵视图同一份来源、同一套键名）：页面把
        # "未安装 / 解析路径"标在下拉项、矩阵行、预设与回退候选上，只呈现不拦截，
        # 真正的阻断在 core 的启动前门禁。
        "agent_availability": agent_availability_entries(config.agents),
        "lifecycles": lifecycles,
        "presets": presets,
        "fallback": build_fallback_candidates_view(config),
        "field_source_vocabulary": {
            FIELD_SOURCE_PRESET: "预设声明",
            FIELD_SOURCE_INHERITED: "继承实现阶段",
            FIELD_SOURCE_AGENT_DEFAULT: "Agent 默认 / 未显式指定",
            FIELD_SOURCE_NOT_SUPPORTED: "该 Agent 不支持该参数",
            FIELD_SOURCE_PRESET_UNRESOLVED: "绑定预设未定义（已删除或未声明，请先解绑或重建该预设）",
        },
    }


def validate_preset_update(
    preset_name: str,
    values: Mapping[str, Any],
    config: AppConfig,
) -> dict[str, str | None]:
    """校验命名预设 upsert 入参，返回规范化后的 ``{agent, model, reasoning_effort}``。

    预设是原子三元组：``agent`` 必填且必须已注册；``model`` / ``reasoning_effort``
    为 ``None`` 表示该预设**不设置**该字段（沿用 Agent CLI 默认），显式给值时要求
    目标 agent 声明了对应参数模板，否则 fail-fast（避免"看似设了却没注入"）。

    Args:
        preset_name: 预设名（自由命名空间，仅去首尾空白）。
        values: 含 ``agent`` / ``model`` / ``reasoning_effort`` 的映射。
        config: 用于确认 agent 注册与参数模板能力的应用配置。

    Returns:
        规范化后的三元组；``model`` / ``reasoning_effort`` 可为 ``None``。

    Raises:
        LifecycleAgentsUpdateError: 预设名为空、agent 缺失 / 未注册、或给了
            model / reasoning_effort 但目标 agent 无对应参数模板。
    """
    normalized_name = (preset_name or "").strip()
    if not normalized_name:
        raise LifecycleAgentsUpdateError("preset name must not be empty.")

    raw_agent = values.get("agent")
    if not isinstance(raw_agent, str) or not raw_agent.strip():
        raise LifecycleAgentsUpdateError(
            f"preset '{normalized_name}': agent is required and must be a non-empty string."
        )
    agent_name = raw_agent.strip()
    if agent_name not in config.agents:
        raise LifecycleAgentsUpdateError(
            f"preset '{normalized_name}': agent '{agent_name}' is not registered. "
            f"Registered agents: {', '.join(config.agents)}."
        )

    model_value = _normalized_optional_field(values.get("model"))
    effort_value = _normalized_optional_field(values.get("reasoning_effort"))
    model_supported, effort_supported = _model_capability(config, agent_name)
    if model_value is not None and not model_supported:
        raise LifecycleAgentsUpdateError(
            f"preset '{normalized_name}': agent '{agent_name}' declares no model argument "
            "template (model_args), so an explicit model would never reach its CLI."
        )
    if effort_value is not None and not effort_supported:
        raise LifecycleAgentsUpdateError(
            f"preset '{normalized_name}': agent '{agent_name}' declares no reasoning-effort "
            "argument template (reasoning_effort_args), so an explicit effort would never "
            "reach its CLI."
        )
    return {"agent": agent_name, "model": model_value, "reasoning_effort": effort_value}


def _normalized_optional_field(raw_value: Any) -> str | None:
    """把可选字段值规范化：``None`` / 空串 -> ``None``（表示不设置该字段）。"""
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        return None
    stripped_value = raw_value.strip()
    return stripped_value or None


def validate_lifecycle_preset_binding_update(
    values: Mapping[str, Any],
    config: AppConfig,
) -> dict[str, str | None]:
    """校验阶段 -> 预设 绑定写回入参，返回规范化后的 ``{stage: preset | None}``。

    值为 ``None`` 表示删除该阶段在当前层的绑定（仓库层删除后恢复全局绑定）。
    非空绑定必须指向已定义预设，且该预设声明的 agent 必须已注册（fail-fast）。

    Raises:
        LifecycleAgentsUpdateError: 未知阶段、预设未定义或预设 agent 未注册。
    """
    normalized_bindings: dict[str, str | None] = {}
    for lifecycle_key, raw_value in values.items():
        _require_known_lifecycle_key(lifecycle_key)
        if raw_value is None:
            normalized_bindings[lifecycle_key] = None
            continue
        preset_name = _normalized_optional_field(raw_value)
        if preset_name is None:
            raise LifecycleAgentsUpdateError(
                f"lifecycle_presets.{lifecycle_key}: value must be a preset name or null."
            )
        declared_preset = config.agent_presets.get(preset_name)
        if declared_preset is None:
            raise LifecycleAgentsUpdateError(
                f"lifecycle_presets.{lifecycle_key}: preset '{preset_name}' is not defined. "
                f"Defined presets: {', '.join(config.agent_presets) or '(none)'}."
            )
        if declared_preset.agent not in config.agents:
            raise LifecycleAgentsUpdateError(
                f"lifecycle_presets.{lifecycle_key}: preset '{preset_name}' binds agent "
                f"'{declared_preset.agent}', which is not registered."
            )
        normalized_bindings[lifecycle_key] = preset_name
    return normalized_bindings


def validate_fallback_candidates_update(
    candidates: list[Mapping[str, Any]],
    max_agent_switches: int,
    config: AppConfig,
) -> tuple[list[dict[str, str | None]], int]:
    """校验有序回退候选写回入参，返回规范化后的候选列表与切换预算。

    候选身份是 ``(agent, preset)``：agent 必须已注册；绑定的预设必须存在且其
    声明 agent 与该候选一致（不允许把别的 agent 的预设挂到本候选）；完全相同的
    ``(agent, preset)`` 组合拒绝重复，同一 agent 换不同预设则是独立候选。

    Raises:
        LifecycleAgentsUpdateError: 预算为负、候选 agent 空 / 未注册、预设缺失
            或与候选 agent 不匹配、或 ``(agent, preset)`` 组合重复。
    """
    if max_agent_switches < 0:
        raise LifecycleAgentsUpdateError("max_agent_switches must be >= 0.")
    seen_combinations: set[tuple[str, str | None]] = set()
    normalized_candidates: list[dict[str, str | None]] = []
    for candidate_index, candidate_entry in enumerate(candidates, start=1):
        agent_name = str(candidate_entry.get("agent", "")).strip()
        if not agent_name:
            raise LifecycleAgentsUpdateError(
                f"agent_fallback_candidates[{candidate_index}]: agent must not be empty."
            )
        if agent_name not in config.agents:
            raise LifecycleAgentsUpdateError(
                f"agent_fallback_candidates[{candidate_index}]: agent '{agent_name}' is not "
                f"registered. Registered agents: {', '.join(config.agents)}."
            )
        preset_name = _normalized_optional_field(candidate_entry.get("preset"))
        if preset_name is not None:
            declared_preset = config.agent_presets.get(preset_name)
            if declared_preset is None:
                raise LifecycleAgentsUpdateError(
                    f"agent_fallback_candidates[{candidate_index}]: preset '{preset_name}' "
                    f"is not defined. Defined presets: "
                    f"{', '.join(config.agent_presets) or '(none)'}."
                )
            if declared_preset.agent != agent_name:
                raise LifecycleAgentsUpdateError(
                    f"agent_fallback_candidates[{candidate_index}]: preset '{preset_name}' "
                    f"binds agent '{declared_preset.agent}', which does not match candidate "
                    f"agent '{agent_name}'."
                )
        combination = (agent_name, preset_name)
        if combination in seen_combinations:
            raise LifecycleAgentsUpdateError(
                f"agent_fallback_candidates[{candidate_index}]: duplicate (agent, preset) "
                f"combination ('{agent_name}', '{preset_name}')."
            )
        seen_combinations.add(combination)
        normalized_candidates.append({"agent": agent_name, "preset": preset_name})
    return normalized_candidates, max_agent_switches
