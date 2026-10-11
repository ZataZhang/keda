"""Agent Runner 生命周期 agent 路由与修复 agent 策略。"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from backend.core.shared.models.agent_model_preset import resolve_model_selection
from backend.core.shared.models.agent_runner import AgentFallbackCandidate, AppConfig, IssueSummary
from backend.core.use_cases.agent_candidate_fallback import effective_fallback_candidates
from backend.core.use_cases.agent_invocation import (
    resolve_agent_spec,
    resolve_registered_agents,
)

_logger = logging.getLogger("backend.core.use_cases.run_agent_once")

REPAIR_AGENT_SELF = "self"
"""``repair_agent`` 取值：本阶段的审核者 / supervisor 自己修（默认，历史行为）。"""

REPAIR_AGENT_EXECUTOR = "executor"
"""``repair_agent`` 取值：把修复交回本次实现者。"""


def choose_agent(issue: IssueSummary, config: AppConfig, override_agent: str) -> str:
    """Choose an AI agent for the Issue.

    优先级：显式 ``override_agent``（CLI / loop recipe）> 阶段预设绑定
    （``lifecycle_presets``，预设整体决定 agent）> Issue 上的 agent 标签路由
    > 生命周期矩阵 ``implementation`` 显式声明 > ``runner.default_agent`` >
    内置默认（``claude``）。未绑定预设时标签路由与 loop recipe 仍是更高
    优先级——矩阵只替换 ``default_agent`` 这一回落层。
    """
    if override_agent != "auto":
        return override_agent
    bound_agent = _resolve_bound_lifecycle_agent("implementation", config, issue=issue)
    if bound_agent is not None:
        return bound_agent
    for agent_name, label in config.labels.agent_labels.items():
        if label in issue.labels:
            return agent_name
    declared_agent = _resolve_declared_lifecycle_agent(
        "implementation", config, issue=issue, override_agent=override_agent
    )
    if declared_agent is not None:
        return declared_agent
    return config.runner.default_agent if config.runner.default_agent != "auto" else "claude"


def resolve_agent_fallback_candidate_specs(
    issue: IssueSummary,
    config: AppConfig,
    override_agent: str,
) -> list[AgentFallbackCandidate]:
    """返回某 Issue 的有序 ``(agent, preset)`` 回退候选序列。

    首位是 :func:`choose_agent` 解析出的主选 agent，且**不携带**候选 preset
    （主选的模型绑定来自生命周期预设，与回退候选的预设注入互不混淆）。尾部取
    :func:`effective_fallback_candidates`（候选数组赢，旧名单映射为无 preset
    候选），按 ``(agent, preset)`` 对去重——同一 agent 以不同 preset 重复出现是
    合法配置。这里不设预算上限，``max_agent_switches`` 封顶由阶梯调用方完成。

    Args:
        issue: 正在处理的 Issue。
        config: Agent Runner 配置。
        override_agent: ``--agent`` 覆盖（``"auto"`` 走标签路由）。

    Returns:
        去重后的候选 spec 列表，主选在首位。
    """
    primary_agent = choose_agent(issue, config, override_agent)
    fallback_specs = [AgentFallbackCandidate(agent=primary_agent)]
    for candidate in effective_fallback_candidates(config):
        candidate_spec = AgentFallbackCandidate(
            agent=candidate.agent.strip(),
            preset=candidate.preset,
        )
        if not candidate_spec.agent or candidate_spec in fallback_specs:
            continue
        fallback_specs.append(candidate_spec)
    return fallback_specs


def resolve_repair_agent(
    raw_setting: str,
    *,
    issue: IssueSummary,
    config: AppConfig,
    reviewing_agent: str,
    executor_agent: str | None = None,
) -> str:
    """解析某阶段的 ``repair_agent`` 配置为实际执行修复的 agent 名。

    ``self`` 返回本阶段的审核者 / supervisor；``executor`` 返回本次真正执行
    实现的 agent，调用方不知道它时回落为按 Issue 标签路由的结果并在日志中
    注明来源；其它取值必须是已注册 agent，未注册时抛
    :class:`UnknownAgentError`（fail-fast，不静默回落到别人）。

    Args:
        raw_setting: 配置里的原始取值。
        issue: 当前 Issue，``executor`` 回落时用于标签路由。
        config: 应用配置，注册表取自 ``config.agents``。
        reviewing_agent: 本阶段的审核者 / supervisor。
        executor_agent: 本次真正执行实现的 agent；``None`` 表示调用方不知道，
            此时按 Issue 标签回落。

    Returns:
        执行修复的 agent 名。

    Raises:
        UnknownAgentError: 取值既不是 ``self`` / ``executor``，也不在注册表中。
    """
    normalized_setting = _normalize_repair_agent_setting(raw_setting)
    if normalized_setting == REPAIR_AGENT_SELF:
        return reviewing_agent
    if normalized_setting == REPAIR_AGENT_EXECUTOR:
        if executor_agent is not None:
            return executor_agent
        fallback_agent = choose_agent(issue, config, "auto")
        _logger.info(
            "repair_agent='executor' for Issue #%d: the executor of this run is "
            "unknown here, falling back to Issue-label routing -> '%s'.",
            issue.number,
            fallback_agent,
        )
        return fallback_agent
    resolve_agent_spec(normalized_setting, config)
    return normalized_setting


def repair_agent_is_self(raw_setting: str) -> bool:
    """``repair_agent`` 是否表示"本阶段的审核者 / supervisor 自己修"。"""
    return _normalize_repair_agent_setting(raw_setting) == REPAIR_AGENT_SELF


def _normalize_repair_agent_setting(raw_setting: str) -> str:
    """把 ``repair_agent`` 配置值规范化成小写、去空白的裸值。"""
    return (raw_setting or "").strip().lower() or REPAIR_AGENT_SELF


def resolve_supervisor_agent(
    issue: IssueSummary,
    config: AppConfig,
    override_agent: str,
    *,
    fallback_agent: str | None = None,
    prd_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析 post-PR supervisor：命令行 ``--agent`` > 配置 > 回落。

    生命周期矩阵（或 PRD 覆盖）显式声明了具体 agent 时用它；``fallback_agent``
    描述"配置为 ``auto`` 时用谁"：发布路径传本次实现者（保持历史行为），
    ``kc supervise`` 不传，回落到按 Issue 标签路由。

    Args:
        issue: 当前 Issue。
        config: 应用配置。
        override_agent: 命令行 ``--agent``；``"auto"`` 表示未指定。
        fallback_agent: 配置为 ``auto`` 时的回落 agent；``None`` 表示按标签路由。
        prd_overrides: PRD 文件头部覆盖（最高优先级），可选。

    Returns:
        supervisor agent 名。
    """
    if override_agent != "auto":
        return override_agent
    declared_agent = _resolve_declared_lifecycle_agent(
        "supervisor",
        config,
        issue=issue,
        selected_agent=fallback_agent,
        prd_overrides=prd_overrides,
    )
    if declared_agent is not None:
        return declared_agent
    configured_agent = config.post_pr_supervisor.supervisor_agent
    if configured_agent != "auto":
        return configured_agent
    if fallback_agent is not None:
        return fallback_agent
    return choose_agent(issue, config, "auto")


def _resolve_bound_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str | None:
    """返回阶段预设绑定声明的 agent；未绑定时返回 ``None``。

    绑定整体决定 (agent, 模型, 推理档)，因此 agent 也取预设声明值；未知
    预设名 / 未注册 agent 的 fail-fast 语义与 :func:`resolve_lifecycle_agent`
    一致。局部导入 :mod:`lifecycle_agent_resolution` 以打破循环依赖。
    """
    from backend.core.use_cases.lifecycle_agent_resolution import (
        declared_lifecycle_preset,
    )

    bound_preset_name = declared_lifecycle_preset(
        lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
    )
    if bound_preset_name is None:
        return None
    from backend.core.use_cases.lifecycle_agent_resolution import _validate_registered

    bound_selection = resolve_model_selection(bound_preset_name, config)
    return _validate_registered(bound_selection.agent, lifecycle=lifecycle, config=config)


def _resolve_declared_lifecycle_agent(
    lifecycle: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    selected_agent: str | None = None,
    override_agent: str = "auto",
    prd_overrides: Mapping[str, str] | None = None,
    prd_preset_overrides: Mapping[str, str] | None = None,
) -> str | None:
    """返回矩阵 / PRD 覆盖 / 预设绑定**显式声明**的具体 agent；纯 auto 或全未声明时返回 ``None``。

    这是各阶段既有解析函数接入生命周期矩阵与预设绑定的统一入口：只有"显式
    换人"才短路，声明 ``auto`` 且无绑定时交回各函数原有的 auto 语义，绝不改变
    既有行为。阶段绑定预设时预设整体声明该阶段的 agent（遮蔽矩阵同键声明）。

    局部导入 :mod:`lifecycle_agent_resolution` 是为了打破它与本模块的循环依赖
    （解析函数需要 ``choose_agent`` / ``resolve_registered_agents``）。
    """
    from backend.core.shared.models.lifecycle_agent import (
        LIFECYCLE_AGENT_AUTO,
        normalize_lifecycle_agent_value,
    )
    from backend.core.use_cases.lifecycle_agent_resolution import (
        declared_lifecycle_preset,
        effective_prd_overrides,
        resolve_lifecycle_agent,
    )

    merged_overrides = effective_prd_overrides(issue, prd_overrides)
    declared_value = merged_overrides.get(lifecycle)
    if declared_value is None:
        declared_value = config.lifecycle_agents.declared_value(lifecycle)
    bound_preset_name = declared_lifecycle_preset(
        lifecycle, config, issue=issue, prd_preset_overrides=prd_preset_overrides
    )
    if declared_value is None and bound_preset_name is None:
        return None
    if bound_preset_name is None and normalize_lifecycle_agent_value(declared_value) == (
        LIFECYCLE_AGENT_AUTO
    ):
        return None
    return resolve_lifecycle_agent(
        lifecycle,
        config,
        issue=issue,
        selected_agent=selected_agent,
        override_agent=override_agent,
        prd_overrides=merged_overrides,
        prd_preset_overrides=prd_preset_overrides,
    )


def resolve_reviewer_agent(
    issue: IssueSummary,
    config: AppConfig,
    selected_agent: str,
    *,
    prd_overrides: Mapping[str, str] | None = None,
) -> str:
    """解析 pre-PR review 的审核者。

    生命周期矩阵（或 PRD 覆盖）显式声明了具体 agent 时用它；声明 ``auto`` 或
    两层都未声明时沿用既有语义：显式 ``pre_pr_review.review_agent`` 优先；
    ``auto`` 时在 ``allow_same_agent`` 为真时沿用实现者，为假时从 agent 注册表
    里取第一个不等于实现者的 agent（不再硬编码 ``codex``）。注册表里只有实现者
    一个 agent 时保持实现者并 WARN。

    Args:
        issue: 当前 Issue。
        config: 应用配置。
        selected_agent: 本次实现者。
        prd_overrides: PRD 文件头部覆盖（最高优先级），可选。

    Returns:
        审核者 agent 名。
    """
    declared_agent = _resolve_declared_lifecycle_agent(
        "review",
        config,
        issue=issue,
        selected_agent=selected_agent,
        prd_overrides=prd_overrides,
    )
    if declared_agent is not None:
        return declared_agent
    review_config = config.pre_pr_review
    if review_config.review_agent != "auto":
        return review_config.review_agent
    if review_config.allow_same_agent:
        return selected_agent
    for registered_agent in resolve_registered_agents(config):
        if registered_agent != selected_agent:
            return registered_agent
    _logger.warning(
        "pre_pr_review.allow_same_agent=false for Issue #%d but the agent "
        "registry only contains '%s'; keeping it as the reviewer.",
        issue.number,
        selected_agent,
    )
    return selected_agent
