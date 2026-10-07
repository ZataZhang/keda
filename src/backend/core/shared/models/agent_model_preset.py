"""命名模型预设（agent + 模型 + 推理档三元组）的领域模型与纯解析逻辑。

一个预设声明"用哪个 agent、什么模型、什么推理深度"；生命周期阶段可以
绑定一个预设（``[agent_runner.lifecycle_presets]`` / PRD 头部
``lifecycle_presets`` 块），绑定后该阶段整体改用预设声明的选择。

本模块刻意保持纯数据 + 纯函数：预设清单来自 :class:`AppConfig` 的
``agent_presets`` 字典，解析不读文件、不触网；未知预设名 fail-fast
（抛 :class:`ValueError`），绝不静默回落——"切了模型"不能成为假象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.shared.models.agent_runner import AppConfig


@dataclass(frozen=True)
class AgentModelPreset:
    """一个命名预设的声明内容（``[agent_runner.presets.<name>]`` 段）。

    Attributes:
        agent: 预设绑定的 agent 注册名（必填）。
        model: 模型 id（可选；为 ``None`` 表示该预设不指定模型）。
        reasoning_effort: 推理档（可选；为 ``None`` 表示该预设不指定推理深度）。
    """

    agent: str
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ModelSelection:
    """一次解析后的模型选择：注入 argv 前的最终形态。

    ``model`` / ``reasoning_effort`` 为 ``None`` 表示对应字段不注入；
    两者都为 ``None`` 时本次绑定实际上不改变 argv（只改 agent 路由）。

    Attributes:
        agent: 预设声明的 agent 注册名。
        model: 模型 id；``None`` 表示不注入模型参数。
        reasoning_effort: 推理档；``None`` 表示不注入推理档参数。
        preset_name: 产生本选择的预设名（观测用；CLI 直构的选择可为空串）。
    """

    agent: str
    model: str | None = None
    reasoning_effort: str | None = None
    preset_name: str = ""


def resolve_model_selection(
    preset_name: str,
    config: "AppConfig",
    *,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> ModelSelection:
    """把预设名解析成 :class:`ModelSelection`，命令行覆盖值优先于预设字段。

    Args:
        preset_name: 预设名（须已定义在 ``config.agent_presets``）。
        config: 应用配置（预设清单取自 ``config.agent_presets``）。
        model_override: 命令行 ``--model`` 覆盖值；``None`` 表示未覆盖。
        effort_override: 命令行 ``--reasoning-effort`` 覆盖值；同上。

    Returns:
        解析后的 :class:`ModelSelection`。

    Raises:
        ValueError: 预设名未定义（fail-fast，带已定义预设清单）。
    """
    declared_preset = config.agent_presets.get(preset_name)
    if declared_preset is None:
        raise ValueError(
            f"Unknown model preset '{preset_name}'. Defined presets: "
            f"{', '.join(config.agent_presets) or '(none)'}. "
            "Declare it in an [agent_runner.presets.<name>] block in config.toml / .kedacode.toml."
        )
    return ModelSelection(
        agent=declared_preset.agent,
        model=model_override if model_override is not None else declared_preset.model,
        reasoning_effort=(
            effort_override if effort_override is not None else declared_preset.reasoning_effort
        ),
        preset_name=preset_name,
    )


__all__ = [
    "AgentModelPreset",
    "ModelSelection",
    "resolve_model_selection",
]
