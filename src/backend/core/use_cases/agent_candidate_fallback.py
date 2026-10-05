"""跨 agent 回退的共享原语：候选 agent 序列构建。

生命周期里有多个阶段会"主 agent 跑不起来就换下一个"（implementation 阶梯、
verifier、review、supervisor）。它们都从 ``[agent_runner.runner]`` 的
``agent_fallback_order`` + ``max_agent_switches`` 取候选链，且都要求"回退尾部
不含本次 builder"以保持独立性。把这段候选枚举收敛到本模块，避免各阶段各抄一份。

各阶段自己的"逐个候选尝试"遍历仍留在各自调用点——它们的失败分类、状态清理与
耗尽语义各不相同（verifier 要快照证据、supervisor 有同 agent 重试预算），强行
统一反而更易出错。语义说明见 docs/guides/lifecycle-agent-matrix.md。
"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import AppConfig

__all__ = ["build_agent_candidates"]


def build_agent_candidates(
    config: AppConfig,
    primary_agent: str,
    *,
    exclude_agent: str | None = None,
) -> tuple[str, ...]:
    """构建某阶段的候选 agent 序列（首选 + 回退链，去重、可选排除、按预算封顶）。

    首选恒在首位；随后依次取 ``config.runner.agent_fallback_order``，跳过空项、
    重复项与 ``exclude_agent``，最多补充 ``config.runner.max_agent_switches`` 个
    （即候选总数 ≤ ``max_agent_switches + 1``，等于最多换人 ``max_agent_switches``
    次）。当 ``primary_agent == exclude_agent`` 时首选仍保留——这对应 review 的
    ``allow_same_agent`` 语义，只保证**回退尾部**不含 ``exclude_agent``。

    Args:
        config: 应用配置，提供 ``runner.agent_fallback_order`` 与
            ``runner.max_agent_switches``。
        primary_agent: 该阶段的首选 agent（矩阵 / 标签路由 / 预设解析结果）。
        exclude_agent: 需要从回退尾部排除的 agent（通常是本次 builder），
            ``None`` 表示不排除。

    Returns:
        去重后的候选序列，首选在首位，长度 ≤ ``max_agent_switches + 1``。
    """
    candidates = [primary_agent]
    max_switches = max(0, config.runner.max_agent_switches)
    for candidate_agent in config.runner.agent_fallback_order:
        if len(candidates) - 1 >= max_switches:
            break
        normalized_agent = candidate_agent.strip()
        if not normalized_agent or normalized_agent in candidates:
            continue
        if exclude_agent is not None and normalized_agent == exclude_agent:
            continue
        candidates.append(normalized_agent)
    return tuple(candidates)
