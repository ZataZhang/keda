"""Issue 级自动合并授权规则。"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary


def configured_prd_auto_merge_label(config: AppConfig) -> str | None:
    """返回配置指定的单 PRD 自动合并标签；空字符串表示禁用。"""
    label = (config.labels.prd_auto_merge or "").strip()
    return label or None


def issue_carries_prd_auto_merge_label(issue: IssueSummary, config: AppConfig) -> bool:
    """判断 Issue 是否带有单 PRD 自动合并授权标签。"""
    label = configured_prd_auto_merge_label(config)
    return label is not None and label in issue.labels


def can_auto_merge_issue(issue: IssueSummary, config: AppConfig) -> bool:
    """判断 Issue 是否通过全局开关或任一 Issue 级标签获得自动合并资格。

    ``auto-merge`` 只授予通用合并资格，不跳过 verifier 或 PRD 人工验收门禁；
    ``prd-auto-merge`` 保留单 PRD 专属语义。

    Args:
        issue: 待判断的 GitHub Issue。
        config: 当前运行配置，包含 autopilot、安全开关和授权标签。

    Returns:
        自动合并是否获得授权。
    """
    auto_merge_label = (config.labels.auto_merge or "").strip()
    has_generic_label = bool(auto_merge_label and auto_merge_label in issue.labels)
    return bool(
        config.autopilot.enabled
        and (
            config.safety.auto_merge
            or has_generic_label
            or issue_carries_prd_auto_merge_label(issue, config)
        )
    )


__all__ = [
    "can_auto_merge_issue",
    "configured_prd_auto_merge_label",
    "issue_carries_prd_auto_merge_label",
]
