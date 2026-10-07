"""CLI ``--preset`` 一次性锚定：把旗标合成为本轮的预设与阶段绑定。

PRD FR-5：``run`` / ``daemon`` / ``review`` / ``review-daemon`` / ``ask`` /
``issue create`` 支持可选 ``--preset`` / ``--model`` / ``--reasoning-effort``，
锚定各自主生命周期阶段并覆盖绑定同名字段。

实现方式：命令处理器在拿到合并后的 ``AppConfig`` 后，把「预设 + 覆盖」合成为
一个本轮临时的预设条目（``__cli_preset__``），并把它绑到锚定阶段——绑定经
既有的解析链生效（遮蔽矩阵同键声明、显式 ``--agent`` 仍最高），覆盖字段在
合成时折叠进预设，后续消费点零改动。完全不传旗标时返回原 contexts，
行为与今天逐字节一致。
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext

#: CLI 一次性预设的合成条目名（双下划线前缀降低与运维手写预设撞名的概率）。
CLI_PRESET_NAME = "__cli_preset__"


def cli_preset_flag_values(
    parsed: argparse.Namespace,
) -> tuple[str | None, str | None, str | None]:
    """从解析后的命令行取 ``--preset`` / ``--model`` / ``--reasoning-effort``。"""
    return (
        getattr(parsed, "preset", None),
        getattr(parsed, "model", None),
        getattr(parsed, "reasoning_effort", None),
    )


def apply_cli_model_preset(
    contexts: list[RepositoryRunContext],
    parsed: argparse.Namespace,
    *,
    anchored_stage: str,
) -> list[RepositoryRunContext]:
    """把 CLI ``--preset`` 旗标折叠进各目标仓库的合并配置。

    Args:
        contexts: 解析后的目标仓库上下文（含合并配置）。
        parsed: 命令行命名空间（读 ``preset`` / ``model`` / ``reasoning_effort``）。
        anchored_stage: 本命令锚定的生命周期阶段（如 ``implementation``）。

    Returns:
        应用旗标后的新 contexts；未传 ``--preset`` 时原样返回。

    Raises:
        ValueError: 传了 ``--model`` / ``--reasoning-effort`` 但未传 ``--preset``，
            或 ``--preset`` 指向未定义的预设名（fail-fast，不静默忽略）。
    """
    preset_name, model_override, effort_override = cli_preset_flag_values(parsed)
    if not preset_name:
        if model_override or effort_override:
            raise ValueError(
                "--model / --reasoning-effort require --preset: they are one-shot "
                "overrides of a preset's same-name fields, not standalone switches."
            )
        return contexts
    updated_contexts: list[RepositoryRunContext] = []
    for context in contexts:
        updated_contexts.append(
            _anchor_context(
                context,
                preset_name=preset_name,
                model_override=model_override,
                effort_override=effort_override,
                anchored_stage=anchored_stage,
            )
        )
    return updated_contexts


def _anchor_context(
    context: RepositoryRunContext,
    *,
    preset_name: str,
    model_override: str | None,
    effort_override: str | None,
    anchored_stage: str,
) -> RepositoryRunContext:
    """在单个上下文的配置上合成 CLI 预设并绑定锚定阶段。"""
    config = context.config
    declared_preset = config.agent_presets.get(preset_name)
    if declared_preset is None:
        raise ValueError(
            f"Unknown model preset '{preset_name}'. Defined presets: "
            f"{', '.join(config.agent_presets) or '(none)'}."
        )
    synthesized_preset = AgentModelPreset(
        agent=declared_preset.agent,
        model=model_override if model_override is not None else declared_preset.model,
        reasoning_effort=(
            effort_override if effort_override is not None else declared_preset.reasoning_effort
        ),
    )
    merged_presets = {**config.agent_presets, CLI_PRESET_NAME: synthesized_preset}
    # 合成绑定写在**全局层**：仓库层 .kedacode.toml 的同键声明仍会赢过它——
    # 这是既有的两层语义；CLI 一次性旗标的语义是"锚定并覆盖预设字段"，
    # 而不是压过仓库层的显式绑定。PRD 块（随 Issue 流动）仍为最高。
    merged_presets_config = dataclasses.replace(
        config.lifecycle_presets, global_layer={anchored_stage: CLI_PRESET_NAME}
    )
    updated_config = dataclasses.replace(
        config, agent_presets=merged_presets, lifecycle_presets=merged_presets_config
    )
    return dataclasses.replace(context, config=updated_config)


def apply_cli_model_preset_to_config(
    config: AppConfig,
    parsed: argparse.Namespace,
    *,
    anchored_stage: str,
) -> AppConfig:
    """单配置变体：无仓库上下文列表的入口（如 ``kc ask``）使用。"""
    preset_name, model_override, effort_override = cli_preset_flag_values(parsed)
    if not preset_name:
        if model_override or effort_override:
            raise ValueError(
                "--model / --reasoning-effort require --preset: they are one-shot "
                "overrides of a preset's same-name fields, not standalone switches."
            )
        return config
    dummy_context = RepositoryRunContext(
        repo_id="__preset_anchor__",
        display_name="",
        repo_path=Path("."),
        config=config,
    )
    return _anchor_context(
        dummy_context,
        preset_name=preset_name,
        model_override=model_override,
        effort_override=effort_override,
        anchored_stage=anchored_stage,
    ).config


__all__ = [
    "CLI_PRESET_NAME",
    "apply_cli_model_preset",
    "apply_cli_model_preset_to_config",
    "cli_preset_flag_values",
]
