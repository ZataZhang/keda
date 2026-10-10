"""跨 agent 回退的共享原语：候选链解析（agent + 可选 preset）。

生命周期里有多个阶段会"主 agent 跑不起来就换下一个"（implementation 阶梯、
verifier、review、supervisor）。它们都从 ``[agent_runner.runner]`` 的
``agent_fallback_candidates`` / ``agent_fallback_order`` + ``max_agent_switches``
取候选链，且都要求"回退尾部不含本次 builder"以保持独立性。把这段候选枚举收敛到
本模块，避免各阶段各抄一份。

两级语义（PRD FR-9 / FR-10）：

- ``agent_fallback_candidates`` 非空时在运行时**整体接管**候选链；同一 agent
  可以以不同 preset 重复出现，去重单位是 ``(agent, preset)`` 对。
- 空数组时读取旧 ``agent_fallback_order`` 映射为无 preset 候选，行为逐字节
  不变。

阶段侧的分歧只发生在"如何取值"：verifier / review / supervisor 按 agent 名遍历
（:func:`build_agent_candidates`，同名不同 preset 折叠为一个名字，独立性排除
也按 agent 名）；implementation 执行器阶梯按 ``(agent, preset)`` spec 遍历
（:func:`build_fallback_candidate_specs`），preset 只在链条真正轮到该候选时应用。

语义说明见 docs/guides/lifecycle-agent-matrix.md。
"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import (
    AgentFallbackCandidate,
    AppConfig,
)

__all__ = [
    "build_agent_candidates",
    "build_fallback_candidate_specs",
    "effective_fallback_candidates",
]


def effective_fallback_candidates(config: AppConfig) -> tuple[AgentFallbackCandidate, ...]:
    """返回当前生效的有序回退候选链（候选数组赢，旧名单兜底）。

    ``[agent_runner.runner].agent_fallback_candidates`` 非空时原样采用；
    否则把 ``agent_fallback_order`` 逐 agent 映射为无 preset 候选（跳过空白项、
    按 agent 名去重），保持与旧纯名单语义等价。

    Args:
        config: 应用配置，提供 ``runner.agent_fallback_candidates`` 与
            ``runner.agent_fallback_order``。

    Returns:
        有序候选链；每项为 ``(agent, preset | None)``。
    """
    configured_candidates = config.runner.agent_fallback_candidates
    if configured_candidates:
        return tuple(configured_candidates)
    folded: list[AgentFallbackCandidate] = []
    seen_agents: set[str] = set()
    for candidate_agent in config.runner.agent_fallback_order:
        normalized_agent = candidate_agent.strip()
        if not normalized_agent or normalized_agent in seen_agents:
            continue
        seen_agents.add(normalized_agent)
        folded.append(AgentFallbackCandidate(agent=normalized_agent))
    return tuple(folded)


def build_fallback_candidate_specs(
    config: AppConfig,
    primary_agent: str,
    *,
    exclude_agent: str | None = None,
) -> tuple[AgentFallbackCandidate, ...]:
    """构建 ``(agent, preset)`` 粒度的候选序列（首选 + 回退尾部，按预算封顶）。

    首选恒在首位且**不携带候选 preset**（一次性的主选解析来自生命周期绑定，
    与回退候选的预设注入是两条独立路径）；尾部取
    :func:`effective_fallback_candidates`，跳过空白 agent、与已选
    ``(agent, preset)`` 对重复项和 ``exclude_agent``（按 agent 名排除，保持
    各阶段"回退尾部不含本次 builder"的独立性语义），最多补充
    ``max_agent_switches`` 个候选步（换人预算按候选计，不按去重后的名字计）。

    Args:
        config: 应用配置。
        primary_agent: 链条首选 agent 名。
        exclude_agent: 需要从回退尾部按 agent 名排除的执行器，``None`` 表示不排除。

    Returns:
        候选 spec 序列，首选在首位，长度 ≤ ``max_agent_switches + 1``。
    """
    specs = [AgentFallbackCandidate(agent=primary_agent)]
    max_switches = max(0, config.runner.max_agent_switches)
    for candidate in effective_fallback_candidates(config):
        if len(specs) - 1 >= max_switches:
            break
        normalized_agent = candidate.agent.strip()
        if not normalized_agent:
            continue
        if exclude_agent is not None and normalized_agent == exclude_agent:
            continue
        candidate_spec = AgentFallbackCandidate(agent=normalized_agent, preset=candidate.preset)
        if any(existing == candidate_spec for existing in specs):
            continue
        specs.append(candidate_spec)
    return tuple(specs)


def build_agent_candidates(
    config: AppConfig,
    primary_agent: str,
    *,
    exclude_agent: str | None = None,
) -> tuple[str, ...]:
    """构建某阶段的候选 agent 名序列（首选 + 回退链，按名字去重、预算封顶）。

    首选恒在首位；随后依次取生效候选链的 agent 名，跳过空项、名字重复项与
    ``exclude_agent``，最多补充 ``config.runner.max_agent_switches`` 个（即候选
    总数 ≤ ``max_agent_switches + 1``，等于最多换人 ``max_agent_switches`` 次）。
    当 ``primary_agent == exclude_agent`` 时首选仍保留——这对应 review 的
    ``allow_same_agent`` 语义，只保证**回退尾部**不含 ``exclude_agent``。

    本视图只表达"换哪个 agent"，不携带候选 preset：verifier / review /
    supervisor 的模型选择由各阶段自己的 ``lifecycle_presets`` 绑定决定。无
    候选数组配置时与旧 ``agent_fallback_order`` 语义逐字节一致。

    Args:
        config: 应用配置，提供生效候选链与 ``runner.max_agent_switches``。
        primary_agent: 该阶段的首选 agent（矩阵 / 标签路由 / 预设解析结果）。
        exclude_agent: 需要从回退尾部排除的 agent（通常是本次 builder），
            ``None`` 表示不排除。

    Returns:
        去重后的候选名序列，首选在首位，长度 ≤ ``max_agent_switches + 1``。
    """
    agent_names: list[str] = [primary_agent]
    max_switches = max(0, config.runner.max_agent_switches)
    for candidate in effective_fallback_candidates(config):
        if len(agent_names) - 1 >= max_switches:
            break
        normalized_agent = candidate.agent.strip()
        if not normalized_agent or normalized_agent in agent_names:
            continue
        if exclude_agent is not None and normalized_agent == exclude_agent:
            continue
        agent_names.append(normalized_agent)
    return tuple(agent_names)
