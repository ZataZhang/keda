"""Autopilot 合并队列在 Ready 转换前后的授权与 PR 状态处理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, PullRequestContext
from backend.core.use_cases.agent_runner_merge_authorization import (
    can_auto_merge_issue,
    issue_carries_prd_auto_merge_label,
)
from backend.core.use_cases.agent_runner_feedback import extract_prd_path

_logger = logging.getLogger(__name__)
_DRAFT_READY_FAILURE_MARKER = "<!-- kc:merge-queue-draft-ready-failed"


@dataclass(frozen=True)
class PullRequestReadinessRequest:
    """Draft PR 转为 Ready 所需的 GitHub 上下文。"""

    github_client: IGitHubClient
    issue_number: int
    pr_number: int
    pr_branch: str
    comments: list[str]


def merge_authorization_skip_action(
    github_client: IGitHubClient,
    issue: IssueSummary,
    config: AppConfig,
    expected_prd_anchor: str | None,
) -> str | None:
    """重新读取 Issue 授权，返回阻止当前合并 pass 的状态（若有）。"""
    try:
        refreshed_issue = github_client.get_issue(issue.number)
    except Exception as exc:  # noqa: BLE001 - merge authorization must fail closed.
        _logger.warning(
            "Merge queue could not refresh Issue #%d before merge: %s", issue.number, exc
        )
        return "skipped_issue_refresh_failed"

    if issue_carries_prd_auto_merge_label(issue, config) and not issue_carries_prd_auto_merge_label(
        refreshed_issue, config
    ):
        return "skipped_auto_merge_revoked"
    if (
        issue_carries_prd_auto_merge_label(issue, config)
        and extract_prd_path(refreshed_issue.body) != expected_prd_anchor
    ):
        return "skipped_prd_anchor_changed"
    if not can_auto_merge_issue(refreshed_issue, config):
        return "skipped_disabled"
    return None


def ensure_pull_request_ready(request: PullRequestReadinessRequest) -> PullRequestContext | None:
    """将 Draft PR 转为 Ready，并回读 GitHub 确认状态。"""
    ready_transition_error: Exception | None = None
    try:
        request.github_client.mark_pull_request_ready(request.pr_number)
    except Exception as exc:  # noqa: BLE001
        # 并发操作者可能已完成转换，后续回读可确认最终状态。
        ready_transition_error = exc

    try:
        latest_context = request.github_client.get_pull_request_context(request.pr_branch)
    except Exception as exc:  # noqa: BLE001
        # 状态未知时留在 review 阶段，由后续 pass 重试。
        refresh_error = f"Could not refresh PR state: {exc}"
        if ready_transition_error is not None:
            refresh_error = f"{ready_transition_error}; {refresh_error}"
        return _report_readiness_failure(
            request=request,
            failure_detail=refresh_error,
        )

    if latest_context is None or latest_context.is_draft is not False:
        failure_detail = (
            str(ready_transition_error)
            if ready_transition_error is not None
            else "GitHub did not confirm that the PR is ready for review."
        )
        return _report_readiness_failure(
            request=request,
            failure_detail=failure_detail,
        )

    if ready_transition_error is not None:
        _logger.info(
            "PR #%d became ready despite the transition response: %s",
            request.pr_number,
            ready_transition_error,
        )
    return latest_context


def _report_readiness_failure(
    request: PullRequestReadinessRequest,
    failure_detail: str,
) -> None:
    """记录未能确认 Ready 的原因，保留下一轮队列重试机会。"""
    _logger.warning(
        "Merge queue cannot confirm PR #%d ready for Issue #%d: %s",
        request.pr_number,
        request.issue_number,
        failure_detail,
    )
    draft_ready_marker = f"{_DRAFT_READY_FAILURE_MARKER} pr={request.pr_number} -->"
    if not any(draft_ready_marker in comment for comment in request.comments):
        try:
            request.github_client.comment_issue(
                request.issue_number,
                "## Agent Runner Merge Queue — Draft PR Still Draft\n\n"
                "本地合并门禁已通过，但 GitHub 尚未确认 PR 已转为 Ready；"
                "本轮没有合并，"
                "后续队列 pass 会重试。\n\n"
                f"```text\n{failure_detail}\n```\n\n"
                f"{draft_ready_marker}",
            )
        except Exception as exc:  # noqa: BLE001 - 评论失败也不能回退 Issue 生命周期。
            _logger.warning(
                "Merge queue could not report Draft readiness failure for Issue #%d: %s",
                request.issue_number,
                exc,
            )
