"""Agent 可用性测试的共享骨架：受控 PATH 与最小生效配置构造器。

可用性判定只有一个来源（``shutil.which``），因此任何"这台机器装了什么"的断言都
必须在测试自己声明的 PATH 上跑，不能跟着执行机器的 PATH 漂移。配置构造器同样只
服务这一族测试：它构造的是**内存里的全局层**，不读磁盘，所以不会把维护者机器上的
``~/.kedacode/config.toml`` 当成事实来源。
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from backend.core.shared.models.agent_model_preset import AgentModelPreset
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AGENT_PROFILE_RUN, AgentProfileSpec, AgentSpec
from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig
from backend.core.use_cases import agent_onboarding


def install_bins(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """把 ``shutil.which`` 换成受控集合，返回"已安装 bin 名"的可变集合。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。

    Returns:
        已安装 bin 名的集合；``add`` 即视为 PATH 解析得到，解析结果为
        ``/fake/bin/<bin>``。
    """
    installed: set[str] = set()
    monkeypatch.setattr(
        agent_onboarding.shutil,
        "which",
        lambda bin_name: f"/fake/bin/{bin_name}" if bin_name in installed else None,
    )
    return installed


def make_spec(bin_name: str, profiles: tuple[str, ...] = (AGENT_PROFILE_RUN,)) -> AgentSpec:
    """构造最小注册声明：只为可用性判定提供 ``bin`` 与用途键。"""
    return AgentSpec(
        bin=bin_name,
        label=f"agent/{bin_name}",
        label_color="5319E7",
        label_description="",
        profiles={name: AgentProfileSpec() for name in profiles},
    )


def make_config(
    *,
    agents: dict[str, AgentSpec] | None = None,
    presets: dict[str, AgentModelPreset] | None = None,
    lifecycle_agents: dict[str, str] | None = None,
    lifecycle_presets: dict[str, str] | None = None,
    default_agent: str | None = None,
) -> AppConfig:
    """构造全局层配置的测试夹具（不读磁盘，因此不受本机 config.toml 影响）。

    Args:
        agents: 生效注册表；默认 claude / kimi（bin 同名）+ ghost（bin ``ghostbin``）。
        presets: ``[agent_runner.presets.*]`` 定义。
        lifecycle_agents: 全局层矩阵显式声明。
        lifecycle_presets: 全局层阶段 → 预设绑定。
        default_agent: ``runner.default_agent``；``None`` 保留内置默认 ``auto``。
    """
    config = AppConfig()
    registry = agents
    if registry is None:
        registry = {
            "claude": make_spec("claude"),
            "kimi": make_spec("kimi"),
            "ghost": make_spec("ghostbin"),
        }
    overrides: dict[str, Any] = {"agents": registry}
    if presets is not None:
        overrides["agent_presets"] = presets
    if lifecycle_agents is not None:
        overrides["lifecycle_agents"] = LifecycleAgentsConfig(global_layer=dict(lifecycle_agents))
    if lifecycle_presets is not None:
        overrides["lifecycle_presets"] = LifecycleAgentsConfig(global_layer=dict(lifecycle_presets))
    if default_agent is not None:
        overrides["runner"] = dataclasses.replace(config.runner, default_agent=default_agent)
    return dataclasses.replace(config, **overrides)
