"""Failure/blocked state marking for the agent runner."""

from __future__ import annotations

import logging

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_workflow import transition_issue_workflow_state

_logger = logging.getLogger(__name__)


def _mark_issue_failed(
    *,
    issue: IssueSummary,
    config: AppConfig,
    github_client: IGitHubClient,
    exc: Exception,
) -> None:
    """将 Issue 标记为失败状态。

    最佳努力（best-effort）报告：即使标签或评论写入失败，
    也保留原始异常，不吞没错误。

    Args:
        issue: Issue 对象
        config: 应用配置
        github_client: GitHub 客户端
        exc: 捕获的异常对象
    """
    try:
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.failed)
    except Exception as label_exc:  # noqa: BLE001 - preserve original failure.
        _logger.error(
            "Failed to mark Issue #%d as %s: %s",
            issue.number,
            config.labels.failed,
            label_exc,
        )

    from backend.core.use_cases.run_agent_once import (
        PublishFailureError,
        format_failure_comment,
        format_minimal_failure_comment,
        format_publish_failure_comment,
    )

    # 尝试从异常中提取尝试历史并格式化失败评论
    attempt_results = getattr(exc, "attempt_results", None)
    if isinstance(exc, PublishFailureError):
        comment_body = format_publish_failure_comment(
            exc,
            issue.number,
            worktree_path=exc.worktree_path,
            failure_category=exc.failure_category,
        )
    elif attempt_results is not None:
        comment_body = format_failure_comment(exc, attempt_results, issue_number=issue.number)
    else:
        comment_body = format_failure_comment(exc, issue_number=issue.number)
    try:
        github_client.comment_issue(issue.number, comment_body)
    except Exception as comment_exc:  # noqa: BLE001 - preserve original failure.
        _logger.error(
            "Failed to comment on Issue #%d failure: %s",
            issue.number,
            comment_exc,
        )
        # The full report can be rejected by GitHub (oversized or control
        # characters from agent output). Fall back to a minimal comment so the
        # failure reason still reaches the Issue instead of being lost.
        try:
            github_client.comment_issue(
                issue.number,
                format_minimal_failure_comment(exc, issue_number=issue.number),
            )
        except Exception as fallback_exc:  # noqa: BLE001 - preserve original failure.
            _logger.error(
                "Failed to post fallback failure comment on Issue #%d: %s",
                issue.number,
                fallback_exc,
            )


def _mark_issue_blocked(
    *,
    issue: IssueSummary,
    config: AppConfig,
    github_client: IGitHubClient,
    exc: Exception,
) -> None:
    """将 Issue 标记为 blocked 状态（可恢复的人工介入）。

    forbidden path 拦截与依赖环境缺口都走这里：两者都是「等人给决定」，不是
    Agent/执行失败，因此用 ``agent/blocked`` 而非 ``agent/failed`` 呈现。

    Args:
        issue: Issue 对象
        config: 应用配置
        github_client: GitHub 客户端
        exc: 捕获的异常对象；带 ``issue_comment_body`` 时以它为评论成稿
    """
    from backend.core.use_cases.agent_runner_failure import (
        ForbiddenBlockedError,
        format_blocked_failure_comment,
    )

    try:
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.blocked)
    except Exception as label_exc:  # noqa: BLE001 - preserve original failure.
        _logger.error(
            "Failed to mark Issue #%d as %s: %s",
            issue.number,
            config.labels.blocked,
            label_exc,
        )

    attempt_results = getattr(exc, "attempt_results", None)
    prepared_comment_body = getattr(exc, "issue_comment_body", None)
    if isinstance(prepared_comment_body, str) and prepared_comment_body.strip():
        # 异常自带面向人的成稿说明（如依赖环境阻塞：缺什么、为什么、人能选什么），
        # 直接用，不再套 forbidden-path 模板。
        comment_body = prepared_comment_body
    elif isinstance(exc, ForbiddenBlockedError) and attempt_results is not None:
        comment_body = format_blocked_failure_comment(
            exc, attempt_results, issue_number=issue.number
        )
    else:
        comment_body = format_blocked_failure_comment(exc, issue_number=issue.number)
    try:
        github_client.comment_issue(issue.number, comment_body)
    except Exception as comment_exc:  # noqa: BLE001 - preserve original failure.
        _logger.error(
            "Failed to comment on Issue #%d blocked: %s",
            issue.number,
            comment_exc,
        )
