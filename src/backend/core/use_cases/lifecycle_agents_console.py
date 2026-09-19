"""生命周期 Agent 矩阵的 console 视图构建与写回校验（core 层）。

把"当前生效值 + 来源层"的计算、以及写回入参的合法性校验收敛在这里，API 路由层
只做 HTTP 映射，避免业务逻辑落进 ``api/routes``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.core.shared.models.agent_runner import AppConfig, AgentSpec
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_DESCRIPTIONS,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    LIFECYCLE_SOURCE_BUILTIN,
    LIFECYCLE_SOURCE_GLOBAL,
    LIFECYCLE_SOURCE_LEGACY,
    normalize_lifecycle_agent_value,
)
from backend.core.use_cases.lifecycle_agent_resolution import (
    legacy_configured_agent,
    resolve_lifecycle_agent,
)

SCOPE_GLOBAL = "global"
SCOPE_REPOSITORY = "repository"
VALID_SCOPES: frozenset[str] = frozenset({SCOPE_GLOBAL, SCOPE_REPOSITORY})


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
            ``scope=repository`` 传"全局 + 该仓库 .iar.toml"合并后的配置）。
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
        lifecycles.append(
            {
                "key": lifecycle_key,
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
            }
        )
    return {
        "scope": scope,
        "repo_id": repo_id,
        "agents": list(config.agents),
        "lifecycles": lifecycles,
        # 只列矩阵视图真会返回的来源层：``prd_override`` 只存在于 PRD 覆盖抽屉的
        # 语义里（矩阵视图只读 config 层），放进来只会让图例与数据对不上。
        "source_layers": {
            "repository": "仓库 .iar.toml",
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
        if lifecycle_key not in LIFECYCLE_AGENT_KEYS:
            raise LifecycleAgentsUpdateError(
                f"Unknown lifecycle key '{lifecycle_key}'. "
                f"Valid keys: {', '.join(LIFECYCLE_AGENT_KEYS)}."
            )
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


__all__ = [
    "SCOPE_GLOBAL",
    "SCOPE_REPOSITORY",
    "VALID_SCOPES",
    "LifecycleAgentsUpdateError",
    "build_agent_fallback_order_view",
    "build_agent_labels_view",
    "build_lifecycle_agents_view",
    "find_agent_spec",
    "validate_agent_fallback_order_update",
    "validate_agent_labels_update",
    "validate_lifecycle_agents_update",
]
