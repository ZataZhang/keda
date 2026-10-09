"""生命周期 Agent 矩阵的 console 视图构建与写回校验（core 层）。

把"当前生效值 + 来源层"的计算、以及写回入参的合法性校验收敛在这里，API 路由层
只做 HTTP 映射，避免业务逻辑落进 ``api/routes``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.core.shared.models.agent_model_preset import (
    AgentModelPreset,
    resolve_model_selection,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AgentSpec,
)
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_DESCRIPTIONS,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_ENTRY_BY_KEY,
    LIFECYCLE_AGENT_ENTRY_GROUPS,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_AGENT_STAGE_LABELS,
    LIFECYCLE_AGENT_TRIGGERS,
    LIFECYCLE_SOURCE_BUILTIN,
    LIFECYCLE_SOURCE_GLOBAL,
    LIFECYCLE_SOURCE_LEGACY,
    normalize_lifecycle_agent_value,
)
from backend.core.use_cases.agent_candidate_fallback import (
    effective_fallback_candidates,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    legacy_configured_agent,
    resolve_lifecycle_agent,
    resolve_lifecycle_model_selection,
)

SCOPE_GLOBAL = "global"
SCOPE_REPOSITORY = "repository"
#: CLI 读取专用：按 cwd 能否唯一解析仓库自动落到 repository 或 global；
#: 视图本身只接受 :data:`SCOPE_GLOBAL` / :data:`SCOPE_REPOSITORY`，effective
#: 在上游（CLI / route）解析成二者之一后再取视图。
SCOPE_EFFECTIVE = "effective"
VALID_SCOPES: frozenset[str] = frozenset({SCOPE_GLOBAL, SCOPE_REPOSITORY})

#: 聚合视图逐字段来源的取值（前端据此区分"预设声明 / 继承实现 / Agent 默认 /
#: 该 Agent 不支持该参数"四种语义，绝不混成一个空串）。
FIELD_SOURCE_PRESET = "preset"
FIELD_SOURCE_INHERITED = "inherited"
FIELD_SOURCE_AGENT_DEFAULT = "agent_default"
FIELD_SOURCE_NOT_SUPPORTED = "not_supported"

#: 预设定义来源层（scope=repository 时据全局层对比得出；scope=global 恒为
#: :data:`LIFECYCLE_SOURCE_GLOBAL`）。
PRESET_SOURCE_GLOBAL = "global"
PRESET_SOURCE_REPOSITORY_ONLY = "repository_only"
PRESET_SOURCE_REPOSITORY_OVERRIDES = "repository_overrides"


class LifecycleAgentsUpdateError(ValueError):
    """生命周期矩阵写回入参非法（未知键 / 非法取值 / 未注册 agent）。"""


def _resolve_source_layer(lifecycle: str, config: AppConfig) -> str:
    """返回该键生效值来自哪一层。"""
    declared_layer = config.lifecycle_agents.declared_layer(lifecycle)
    if declared_layer is not None:
        return declared_layer
    legacy_value = normalize_lifecycle_agent_value(legacy_configured_agent(lifecycle, config))
    if legacy_value == LIFECYCLE_AGENT_AUTO and lifecycle not in LIFECYCLE_AGENT_AUTO_KEYS:
        return LIFECYCLE_SOURCE_BUILTIN
    return LIFECYCLE_SOURCE_LEGACY


def build_lifecycle_agents_view(
    config: AppConfig,
    *,
    scope: str,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """构建某个视角下的生命周期矩阵生效视图。

    Args:
        config: 该视角下的应用配置（``scope=global`` 传全局配置；
            ``scope=repository`` 传"全局 + 该仓库 .kedacode.toml"合并后的配置）。
        scope: ``global`` 或 ``repository``。
        repo_id: 仓库级视图的仓库 id；全局视图为 ``None``。

    Returns:
        含 ``lifecycle_agents`` 列表、``agents`` 与视角标注的 JSON 友好字典。

    Raises:
        LifecycleAgentsUpdateError: ``scope`` 非法。
    """
    if scope not in VALID_SCOPES:
        raise LifecycleAgentsUpdateError(
            f"Unknown scope '{scope}'. Valid scopes: {', '.join(sorted(VALID_SCOPES))}."
        )
    global_layer = config.lifecycle_agents.global_layer
    repository_layer = config.lifecycle_agents.repository_layer
    in_scope_layer = global_layer if scope == SCOPE_GLOBAL else repository_layer
    other_layer = repository_layer if scope == SCOPE_GLOBAL else global_layer

    lifecycles: list[dict[str, Any]] = []
    for lifecycle_key in LIFECYCLE_AGENT_KEYS:
        declared_or_legacy = config.lifecycle_agents.declared_value(
            lifecycle_key
        ) or legacy_configured_agent(lifecycle_key, config)
        follows_executor = (
            normalize_lifecycle_agent_value(declared_or_legacy) == LIFECYCLE_AGENT_EXECUTOR
        )
        entry_group = LIFECYCLE_AGENT_ENTRY_BY_KEY[lifecycle_key]
        # 阶段 -> 预设 绑定与生效模型（本 PRD 新增的只读展示字段）：绑定名
        # 取两层显式声明（仓库层 > 全局层），解析结果给出生效的模型/推理档；
        # 未绑定时三项均为 ``None``（前端渲染为空，行为与过去一致）。
        bound_preset_name = config.lifecycle_presets.declared_value(lifecycle_key)
        bound_selection = (
            resolve_lifecycle_model_selection(lifecycle_key, config)
            if bound_preset_name is not None
            else None
        )
        lifecycles.append(
            {
                "key": lifecycle_key,
                # 触发入口分组（纯新增展示字段）：``entry`` 是**行所属触发入口 id**
                # （与视图级 ``entry_groups[].entry`` 同名的匹配键，不是行自身的
                # id），``trigger`` 是该阶段的触发时机。组中文名与组说明只由视图级
                # ``entry_groups`` 下发一份，行上不重复携带，避免同一事实两处出现。
                # 全部取自 core 分组常量，前端只渲染、不持有第二份映射。
                "entry": entry_group.entry,
                "trigger": LIFECYCLE_AGENT_TRIGGERS[lifecycle_key],
                "auto_allowed": lifecycle_key in LIFECYCLE_AGENT_AUTO_KEYS,
                "executor_allowed": lifecycle_key in LIFECYCLE_AGENT_EXECUTOR_KEYS,
                "auto_description": LIFECYCLE_AGENT_AUTO_DESCRIPTIONS.get(lifecycle_key),
                "declared_in_scope": lifecycle_key in in_scope_layer,
                "declared_value": in_scope_layer.get(lifecycle_key),
                "inherited_value": other_layer.get(lifecycle_key),
                # ``executor`` 阶段在无实现者上下文时无法解析成具体 agent，用
                # ``effective_agent=None`` + ``follows_executor`` 如实表达"跟随实现者"。
                "effective_agent": (
                    None if follows_executor else resolve_lifecycle_agent(lifecycle_key, config)
                ),
                "follows_executor": follows_executor,
                "source": _resolve_source_layer(lifecycle_key, config),
                # 预设绑定视图（纯新增）：绑定的预设名 + 解析后的模型/推理档。
                "preset": bound_preset_name,
                "model": bound_selection.model if bound_selection is not None else None,
                "reasoning_effort": (
                    bound_selection.reasoning_effort if bound_selection is not None else None
                ),
            }
        )
    return {
        "scope": scope,
        "repo_id": repo_id,
        "agents": list(config.agents),
        "lifecycles": lifecycles,
        # 触发入口分组按展示顺序下发（组 id / 中文名 / 一行说明）；前端只按行上的
        # ``entry`` 聚块渲染，组文案一律取自这里，不在前端拼第二份。
        "entry_groups": [
            {
                "entry": entry_group.entry,
                "label": entry_group.label,
                "summary": entry_group.summary,
            }
            for entry_group in LIFECYCLE_AGENT_ENTRY_GROUPS
        ],
        # 只列矩阵视图真会返回的来源层：``prd_override`` 只存在于 PRD 覆盖抽屉的
        # 语义里（矩阵视图只读 config 层），放进来只会让图例与数据对不上。
        "source_layers": {
            "repository": "仓库 .kedacode.toml",
            "global": "全局 config.toml",
            "legacy": "既有配置键",
            "builtin": "内置默认",
        },
        "scope_layer": SCOPE_GLOBAL if scope == SCOPE_GLOBAL else LIFECYCLE_SOURCE_GLOBAL,
        "restore_hint": (
            "不写本键（跟随既有配置）" if scope == SCOPE_GLOBAL else "跟随全局（删除本键）"
        ),
    }


def validate_lifecycle_agents_update(
    values: Mapping[str, Any],
    config: AppConfig,
) -> dict[str, str | None]:
    """校验生命周期矩阵写回入参，返回规范化后的键值。

    Args:
        values: 请求体里的 ``values`` 映射；值为 ``None`` 表示删除该键。
        config: 用于确认 agent 是否已注册的应用配置。

    Returns:
        规范化（小写、去空白）后的键值；``None`` 保留表示删除。

    Raises:
        LifecycleAgentsUpdateError: 未知键、空值、非法取值或未注册 agent。
    """
    normalized_values: dict[str, str | None] = {}
    for lifecycle_key, raw_value in values.items():
        _require_known_lifecycle_key(lifecycle_key)
        if raw_value is None:
            normalized_values[lifecycle_key] = None
            continue
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise LifecycleAgentsUpdateError(
                f"lifecycle_agents.{lifecycle_key}: value must be a non-empty string or null."
            )
        normalized_value = normalize_lifecycle_agent_value(raw_value)
        if (
            normalized_value == LIFECYCLE_AGENT_EXECUTOR
            and lifecycle_key not in LIFECYCLE_AGENT_EXECUTOR_KEYS
        ):
            raise LifecycleAgentsUpdateError(
                f"lifecycle_agents.{lifecycle_key}: 'executor' is only valid for "
                f"{', '.join(sorted(LIFECYCLE_AGENT_EXECUTOR_KEYS))}."
            )
        if (
            normalized_value == LIFECYCLE_AGENT_AUTO
            and lifecycle_key not in LIFECYCLE_AGENT_AUTO_KEYS
        ):
            raise LifecycleAgentsUpdateError(
                f"lifecycle_agents.{lifecycle_key}: 'auto' is not a valid value for this stage."
            )
        if (
            normalized_value not in (LIFECYCLE_AGENT_AUTO, LIFECYCLE_AGENT_EXECUTOR)
            and normalized_value not in config.agents
        ):
            raise LifecycleAgentsUpdateError(
                f"lifecycle_agents.{lifecycle_key}: agent '{normalized_value}' is not registered. "
                f"Registered agents: {', '.join(config.agents)}."
            )
        normalized_values[lifecycle_key] = normalized_value
    return normalized_values


def build_agent_fallback_order_view(config: AppConfig) -> dict[str, Any]:
    """构建「agent 回退顺序」编辑视图。"""
    return {
        "agent_fallback_order": list(config.runner.agent_fallback_order),
        "max_agent_switches": config.runner.max_agent_switches,
        "agents": list(config.agents),
    }


def validate_agent_fallback_order_update(
    agent_fallback_order: list[str],
    max_agent_switches: int,
    config: AppConfig,
) -> tuple[list[str], int]:
    """校验回退顺序入参：链内 agent 必须已注册、去重，切换次数非负。

    Raises:
        LifecycleAgentsUpdateError: agent 未注册、链内有重复或切换次数为负。
    """
    if max_agent_switches < 0:
        raise LifecycleAgentsUpdateError("max_agent_switches must be >= 0.")
    seen_agents: set[str] = set()
    normalized_order: list[str] = []
    for raw_agent in agent_fallback_order:
        agent_name = (raw_agent or "").strip()
        if not agent_name:
            raise LifecycleAgentsUpdateError("agent_fallback_order entries must not be empty.")
        if agent_name not in config.agents:
            raise LifecycleAgentsUpdateError(
                f"agent_fallback_order: agent '{agent_name}' is not registered. "
                f"Registered agents: {', '.join(config.agents)}."
            )
        if agent_name in seen_agents:
            raise LifecycleAgentsUpdateError(
                f"agent_fallback_order: agent '{agent_name}' appears more than once."
            )
        seen_agents.add(agent_name)
        normalized_order.append(agent_name)
    return normalized_order, max_agent_switches


def build_agent_labels_view(config: AppConfig) -> list[dict[str, str]]:
    """构建「Agent 标签设置」编辑视图。"""
    return [
        {
            "agent": agent_name,
            "label": agent_spec.label,
            "label_color": agent_spec.label_color,
            "label_description": agent_spec.label_description,
        }
        for agent_name, agent_spec in config.agents.items()
    ]


def validate_agent_labels_update(
    labels: list[Mapping[str, Any]],
    config: AppConfig,
) -> dict[str, dict[str, str]]:
    """校验 Agent 标签写回入参：标签名非空且各 agent 互不重复。

    Args:
        labels: 请求体里的标签列表，每项含 ``agent`` / ``label`` /
            ``label_color`` / ``label_description``。
        config: 应用配置，用于确认 agent 已注册。

    Returns:
        agent 名 -> 规范化后的三个字段。

    Raises:
        LifecycleAgentsUpdateError: agent 未注册、标签名为空或重复、颜色非法。
    """
    normalized_labels: dict[str, dict[str, str]] = {}
    seen_label_names: dict[str, str] = {}
    for label_entry in labels:
        agent_name = str(label_entry.get("agent", "")).strip()
        if agent_name not in config.agents:
            raise LifecycleAgentsUpdateError(
                f"agent-labels: agent '{agent_name}' is not registered. "
                f"Registered agents: {', '.join(config.agents)}."
            )
        label_name = str(label_entry.get("label", "")).strip()
        if not label_name:
            raise LifecycleAgentsUpdateError(
                f"agent-labels.{agent_name}: label name must not be empty."
            )
        if label_name in seen_label_names:
            raise LifecycleAgentsUpdateError(
                f"agent-labels: label '{label_name}' is used by both "
                f"'{seen_label_names[label_name]}' and '{agent_name}'."
            )
        seen_label_names[label_name] = agent_name
        label_color = str(label_entry.get("label_color", "")).strip()
        if label_color and (
            len(label_color) != 6
            or any(character not in "0123456789abcdefABCDEF" for character in label_color)
        ):
            raise LifecycleAgentsUpdateError(
                f"agent-labels.{agent_name}: label_color must be 6 hex digits (without '#')."
            )
        normalized_labels[agent_name] = {
            "label": label_name,
            "label_color": label_color,
            "label_description": str(label_entry.get("label_description", "")).strip(),
        }
    return normalized_labels


def find_agent_spec(config: AppConfig, agent_name: str) -> AgentSpec | None:
    """返回已注册 agent 的 spec（供写入前确认），未注册时返回 ``None``。"""
    return config.agents.get(agent_name)


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


def _implementation_selection(config: AppConfig) -> tuple[str | None, str | None, str | None]:
    """解析实现阶段的基线 (agent, model, reasoning_effort)，供 fix / closeout 继承展示。

    无 Issue / selected_agent 上下文时按 config 解析：绑定预设取预设三元组，否则
    agent 走既有矩阵 / 既有键 / 内置默认，model / effort 未显式配置记为 ``None``。
    """
    bound_preset = config.lifecycle_presets.declared_value("implementation")
    if bound_preset is not None:
        bound_selection = resolve_model_selection(bound_preset, config)
        return (bound_selection.agent, bound_selection.model, bound_selection.reasoning_effort)
    return (resolve_lifecycle_agent("implementation", config), None, None)


def _lifecycle_row_source(key: str, config: AppConfig) -> dict[str, Any]:
    """计算单个生命周期阶段的聚合视图行（不含视图级上下文，仅本阶段事实）。"""
    bound_preset = config.lifecycle_presets.declared_value(key)
    binding_layer = config.lifecycle_presets.declared_layer(key)

    # 情形 A：阶段绑定预设——预设整体决定 (agent, model, effort)，遮蔽矩阵同键声明。
    if bound_preset is not None:
        bound_selection = resolve_model_selection(bound_preset, config)
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

    # 情形 B：fix / closeout 无自绑，取值整体继承实现阶段解析结果。
    follows_implementation = key in LIFECYCLE_AGENT_EXECUTOR_KEYS and (
        config.lifecycle_agents.declared_value(key) is None
    )
    if follows_implementation:
        impl_agent, impl_model, impl_effort = _implementation_selection(config)
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
            "model": FIELD_SOURCE_AGENT_DEFAULT,
            "reasoning_effort": (
                FIELD_SOURCE_AGENT_DEFAULT if effort_supported else FIELD_SOURCE_NOT_SUPPORTED
            ),
        },
        "is_inherited": False,
        "follows_implementation": False,
        "model_supported": model_supported,
        "reasoning_effort_supported": effort_supported,
    }


def _value_field_source(value: str | None, supported: bool) -> str:
    """把"某字段是否有值 + Agent 是否支持"折叠成逐字段来源标签。"""
    if value is None:
        return FIELD_SOURCE_AGENT_DEFAULT
    return FIELD_SOURCE_PRESET if supported else FIELD_SOURCE_NOT_SUPPORTED


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
            selection = resolve_model_selection(bound_preset, config)
            model, reasoning_effort = selection.model, selection.reasoning_effort
            model_supported, effort_supported = _model_capability(config, candidate.agent)
            preset_source: str | None = FIELD_SOURCE_PRESET
            model_source = _value_field_source(model, model_supported)
            effort_source = _value_field_source(reasoning_effort, effort_supported)
        else:
            model = reasoning_effort = None
            model_supported, effort_supported = _model_capability(config, candidate.agent)
            preset_source = None
            model_source = FIELD_SOURCE_AGENT_DEFAULT
            effort_source = FIELD_SOURCE_AGENT_DEFAULT
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
        含 ``lifecycles`` / ``presets`` / ``fallback`` 三段与视角标注的 JSON 字典。

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
        "lifecycles": lifecycles,
        "presets": presets,
        "fallback": build_fallback_candidates_view(config),
        "field_source_vocabulary": {
            FIELD_SOURCE_PRESET: "预设声明",
            FIELD_SOURCE_INHERITED: "继承实现阶段",
            FIELD_SOURCE_AGENT_DEFAULT: "Agent 默认 / 未显式指定",
            FIELD_SOURCE_NOT_SUPPORTED: "该 Agent 不支持该参数",
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


def _require_known_lifecycle_key(lifecycle_key: str) -> None:
    """阶段键必须是 ``LIFECYCLE_AGENT_KEYS`` 成员，否则抛字段级错误。

    Raises:
        LifecycleAgentsUpdateError: 未知生命周期阶段键。
    """
    if lifecycle_key not in LIFECYCLE_AGENT_KEYS:
        raise LifecycleAgentsUpdateError(
            f"Unknown lifecycle key '{lifecycle_key}'. "
            f"Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
        )


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


__all__ = [
    "FIELD_SOURCE_AGENT_DEFAULT",
    "FIELD_SOURCE_INHERITED",
    "FIELD_SOURCE_NOT_SUPPORTED",
    "FIELD_SOURCE_PRESET",
    "PRESET_SOURCE_GLOBAL",
    "PRESET_SOURCE_REPOSITORY_ONLY",
    "PRESET_SOURCE_REPOSITORY_OVERRIDES",
    "SCOPE_EFFECTIVE",
    "SCOPE_GLOBAL",
    "SCOPE_REPOSITORY",
    "VALID_SCOPES",
    "LifecycleAgentsUpdateError",
    "build_agent_fallback_order_view",
    "build_agent_labels_view",
    "build_fallback_candidates_view",
    "build_lifecycle_agents_view",
    "build_lifecycle_settings_view",
    "find_agent_spec",
    "validate_agent_fallback_order_update",
    "validate_agent_labels_update",
    "validate_fallback_candidates_update",
    "validate_lifecycle_agents_update",
    "validate_lifecycle_preset_binding_update",
    "validate_preset_update",
]
