"""Dataclass models and shared constants for the GitHub CLI client.

This module hosts the small frozen dataclasses used as return types from
:mod:`backend.infrastructure.github_client` plus the constants that
``sanitize_github_body`` depends on. Splitting these out keeps the
client implementation focused on ``gh`` invocation logic while letting
callers and tests type-annotate against stable data shapes.

The dataclasses are re-exported from :mod:`backend.infrastructure.github_client`
for backward compatibility with existing import paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS

# GitHub rejects POST bodies above ~65,536 characters; stay well below it.
_MAX_GITHUB_BODY_LENGTH = 60000

# Marker inserted when a Markdown body is middle-truncated so both the start
# and the tail of the original content survive.
_BODY_TRUNCATION_MARKER = "\n\n... (truncated to fit GitHub's size limit) ...\n\n"


@dataclass(frozen=True)
class IssueSummary:
    """GitHub Issue selected for runner execution.

    镜像 :class:`backend.core.shared.models.agent_runner.IssueSummary`；两个
    ``lifecycle_*`` 字段必须保持同步，否则编排入口的
    ``attach_prd_lifecycle_overrides`` 在做 ``dataclasses.replace`` 时会因缺少
    字段而抛 ``TypeError``。
    """

    number: int
    title: str
    url: str
    body: str
    labels: tuple[str, ...]
    state: str = "OPEN"
    #: 该 Issue 所引用 PRD 的头部 ``lifecycle_agents`` 覆盖（键值对元组）。
    lifecycle_overrides: tuple[tuple[str, str], ...] = ()
    #: 该 Issue 所引用 PRD 的头部 ``lifecycle_presets`` 块（键值对元组）。
    lifecycle_preset_overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PullRequestSummary:
    """Local mirror of :class:`backend.core.shared.models.agent_runner.PullRequestSummary`."""

    number: int
    state: str
    url: str
    is_draft: bool
    merged: bool
    title: str


@dataclass(frozen=True)
class LabelConfig:
    """GitHub labels used as runner queue state."""

    ready: str = "agent/ready"
    running: str = "agent/running"
    supervising: str = "agent/supervising"
    review: str = "agent/review"
    failed: str = "agent/failed"
    blocked: str = "agent/blocked"
    waiting: str = "agent/waiting"
    validation_pending: str = "validation/pending"
    validation_passed: str = "validation/passed"
    rework_prd: str = "agent/rework-prd"
    deliberate: str = "agent/deliberate"
    # 直发档选择标签（非 workflow 状态标签）；与 core ``LabelConfig`` 保持一致。
    direct_pr: str = "direct-pr"
    # agent 路由标签由 agent 注册表派生（agent 名 -> spec.label）。
    agent_labels: dict[str, str] = field(
        default_factory=lambda: {
            agent_name: agent_spec.label for agent_name, agent_spec in BUILTIN_AGENT_SPECS.items()
        }
    )


@dataclass(frozen=True)
class PullRequestContext:
    """GitHub CLI 返回的 Pull Request 上下文。

    Attributes:
        pr_url: PR 页面地址。
        branch: PR head 分支名。
        head_sha: PR head 提交 SHA。
        base_sha: PR base 提交 SHA。
        mergeable: GitHub 当前报告的可合并状态；未知时为 ``None``。
        checks_state: 汇总后的检查状态；尚无结果或无法确定时为 ``None``。
        checks_summary: 未通过检查的人读摘要。
        number: PR 编号；上下文不完整时为 ``None``。
        body: PR 正文。
        is_draft: PR 是否仍处于 Draft 状态；未知时为 ``None``。
    """

    pr_url: str
    branch: str
    head_sha: str
    base_sha: str
    mergeable: bool | None = None
    checks_state: str | None = None
    checks_summary: tuple[str, ...] = ()
    number: int | None = None
    body: str = ""
    is_draft: bool | None = None


@dataclass(frozen=True)
class GhAuthStatus:
    """GitHub CLI authentication status."""

    authenticated: bool
    account: str | None = None
    failure_reason: str | None = None


__all__ = [
    "GhAuthStatus",
    "IssueSummary",
    "LabelConfig",
    "PullRequestContext",
    "PullRequestSummary",
    "_BODY_TRUNCATION_MARKER",
    "_MAX_GITHUB_BODY_LENGTH",
]
