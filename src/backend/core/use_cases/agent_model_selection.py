"""实现阶段模型预设的解析与 agent 一致性校验。"""

from __future__ import annotations

import logging

from backend.core.shared.models.agent_model_preset import ModelSelection, resolve_model_selection
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary

_logger = logging.getLogger(__name__)


def drop_model_selection_for_agent(
    agent_name: str,
    model_selection: ModelSelection | None,
) -> ModelSelection | None:
    """执行 agent 与预设声明 agent 不一致时丢弃模型绑定并记日志。

    不同 CLI 的模型命名空间不同（codebuddy 的 ``--settings`` 对 claude 无意义），
    绝不把 A CLI 的模型参数塞给 B CLI。回退换人与显式 ``--agent`` 换人都走
    这里——两种情形都表现为"执行 agent != 预设 agent"。

    Returns:
        匹配时原样返回 ``model_selection``；不匹配（或入参为空）时返回 ``None``。
    """
    if model_selection is None:
        return None
    if model_selection.agent == agent_name:
        return model_selection
    _logger.warning(
        "model binding dropped on agent switch: preset declares agent '%s' but this "
        "attempt runs '%s'; the model/effort flags are NOT applied (each CLI has its "
        "own model namespace).",
        model_selection.agent,
        agent_name,
    )
    return None


def resolve_implementation_model_selection(
    selected_agent: str,
    config: AppConfig,
    *,
    issue: IssueSummary | None = None,
    fallback_preset: str | None = None,
) -> ModelSelection | None:
    """解析 implementation 阶段对本次执行生效的模型绑定（单一入口）。

    两条来源在"执行 agent 确定后"收敛到同一个丢弃校验：

    - ``fallback_preset``（执行器回退链轮到带 preset 的候选）：直接解析该预设，
      不经 ``lifecycle_presets`` 阶段绑定；若手工编辑的配置让预设 agent 与候选
      agent 不一致，同样走 :func:`drop_model_selection_for_agent` 丢弃。
    - 其余情形（含一次性 ``--agent``、无 preset 候选）：按阶段绑定解析
      （PRD 级 > 仓库层 > 全局层），换人时丢弃，模型 flag 绝不跨 CLI 注入。

    Args:
        selected_agent: 本次实际执行 implementation 的 agent 名。
        config: 应用配置。
        issue: 当前 Issue（阶段绑定的 PRD 级预设随它流动）。
        fallback_preset: 回退候选携带的预设名；``None`` 表示走阶段绑定。

    Returns:
        生效的 :class:`ModelSelection`，或 ``None``（零注入）。

    Raises:
        ValueError: ``fallback_preset`` 未在 ``[agent_runner.presets]`` 中定义
            （fail-fast，不静默回落）。
    """
    if fallback_preset is not None:
        # 预设名未定义 → ValueError（fail-fast）；预设声明的 agent 未注册时由
        # 调用构建处的 UnknownAgentError 兜住，与阶段绑定路径同一判定。
        candidate_selection = resolve_model_selection(fallback_preset, config)
        return drop_model_selection_for_agent(selected_agent, candidate_selection)
    from backend.core.use_cases.lifecycle_agent_resolution import (
        resolve_lifecycle_model_selection,
    )

    return drop_model_selection_for_agent(
        selected_agent,
        resolve_lifecycle_model_selection("implementation", config, issue=issue),
    )
