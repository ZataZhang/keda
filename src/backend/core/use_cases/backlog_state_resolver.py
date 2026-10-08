"""Resolve live GitHub state for backlog PRDs.

Maps Issue labels, PR state, and dependency blockers onto the unified
``BacklogPrdState`` and computes the next actionable item for each PRD.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, LabelConfig
from backend.core.shared.models.backlog import (
    BacklogPrd,
    BacklogPrdState,
)
from backend.core.use_cases.agent_runner_monitor import (
    _extract_pr_branch_from_issue,
    _lookup_pr_context,
    _resolve_primary_label,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacklogStateResolutionContext:
    """解析 Backlog 状态时共享的配置与失败策略。

    Attributes:
        config: 目标仓库生效的 Agent 配置。
        block_reasons: PRD 路径到依赖阻塞原因的映射。
        fail_on_github_error: 为 ``True`` 时，GitHub 查询错误会向上抛出。
    """

    config: AppConfig
    block_reasons: Mapping[str, str | None]
    fail_on_github_error: bool = False


def _state_from_labels(
    labels: tuple[str, ...],
    labels_config: LabelConfig,
    issue_state: str,
    pr_merged: bool,
) -> BacklogPrdState:
    """Map Issue labels and PR state to a backlog state."""
    if issue_state.upper() == "CLOSED" and pr_merged:
        return BacklogPrdState.MERGED
    primary = _resolve_primary_label(labels, labels_config)
    mapping = {
        labels_config.ready: BacklogPrdState.READY,
        labels_config.running: BacklogPrdState.RUNNING,
        labels_config.supervising: BacklogPrdState.SUPERVISING,
        labels_config.review: BacklogPrdState.REVIEW,
        labels_config.failed: BacklogPrdState.FAILED,
        labels_config.blocked: BacklogPrdState.BLOCKED,
        labels_config.waiting: BacklogPrdState.WAITING,
    }
    return mapping.get(primary, BacklogPrdState.NOT_STARTED)


def _is_pr_merged(
    issue_number: int,
    github_client: IGitHubClient,
    issue_body: str,
    *,
    fail_on_github_error: bool,
) -> tuple[bool, str | None]:
    """Return whether the associated PR has been merged and its URL."""
    try:
        if fail_on_github_error:
            comments = github_client.list_issue_comments(issue_number, require_success=True)
        else:
            comments = github_client.list_issue_comments(issue_number)
    except Exception as exc:  # noqa: BLE001
        _logger.info("Failed to list comments for issue #%s: %s", issue_number, exc)
        if fail_on_github_error:
            raise
        comments = []

    # Reuse the monitor helper to resolve the PR branch from event markers.
    from backend.core.shared.models.agent_runner import IssueSummary

    issue = IssueSummary(number=issue_number, title="", url="", body=issue_body, labels=())
    pr_branch = _extract_pr_branch_from_issue(issue, github_client, comments)
    if pr_branch is None:
        return False, None

    try:
        if fail_on_github_error:
            merged_url = github_client.find_merged_pr_by_head(pr_branch, require_success=True)
        else:
            merged_url = github_client.find_merged_pr_by_head(pr_branch)
    except Exception as exc:  # noqa: BLE001
        _logger.info("Failed to find merged PR for %s: %s", pr_branch, exc)
        if fail_on_github_error:
            raise
        merged_url = None
    return bool(merged_url), merged_url


def _compute_next_action(
    state: BacklogPrdState,
    pr_context: object | None,
    issue_url: str | None,
) -> dict | None:
    """Build the operator-facing next-action hint for a PRD."""
    if state is BacklogPrdState.REVIEW and pr_context is not None:
        pr_url = getattr(pr_context, "pr_url", None)
        if pr_url:
            return {"label": "去审阅 PR", "url": pr_url}
    if state is BacklogPrdState.MERGED:
        return {"label": "开始下一个", "url": None}
    if state is BacklogPrdState.NOT_STARTED:
        return {"label": "开始", "url": None}
    if state is BacklogPrdState.FAILED:
        return {"label": "重试", "url": issue_url}
    if state is BacklogPrdState.BLOCKED:
        return {"label": "继续", "url": issue_url}
    return None


def resolve_backlog_states(
    prds: Sequence[BacklogPrd],
    github_client: IGitHubClient,
    context: BacklogStateResolutionContext,
) -> list[BacklogPrd]:
    """Resolve live GitHub state for a list of backlog PRDs.

    Args:
        prds: PRDs from the scanner.
        github_client: GitHub client.
        context: Merged app config, dependency blockers, and GitHub failure policy.

    Returns:
        New list of PRDs with ``state``, ``block_reason``, and ``next_action`` updated.

    Raises:
        Exception: GitHub 查询失败且上下文启用严格失败策略时原样抛出。
    """
    labels_config = context.config.labels
    block_reasons = context.block_reasons
    resolved: list[BacklogPrd] = []

    for prd in prds:
        if prd.status == "archived":
            resolved.append(
                replace(
                    prd,
                    state=BacklogPrdState.ARCHIVED,
                    block_reason=None,
                    next_action=None,
                )
            )
            continue

        if prd.issue_number is None:
            block_reason = block_reasons.get(prd.prd_path)
            state = (
                BacklogPrdState.UNRESOLVED_DEPENDENCY
                if block_reason and "无法解析" in block_reason
                else BacklogPrdState.NOT_STARTED
            )
            resolved.append(
                replace(
                    prd,
                    state=state,
                    block_reason=block_reason,
                    next_action=_compute_next_action(state, None, prd.issue_url),
                )
            )
            continue

        try:
            issue = github_client.get_issue(prd.issue_number)
        except Exception as exc:  # noqa: BLE001
            _logger.info("Failed to fetch issue #%s: %s", prd.issue_number, exc)
            if context.fail_on_github_error:
                raise
            block_reason = block_reasons.get(prd.prd_path)
            resolved.append(
                replace(
                    prd,
                    state=BacklogPrdState.NOT_STARTED,
                    block_reason=block_reason or f"无法获取 Issue #{prd.issue_number}",
                    next_action=None,
                )
            )
            continue

        pr_merged, merged_url = _is_pr_merged(
            prd.issue_number,
            github_client,
            issue.body,
            fail_on_github_error=context.fail_on_github_error,
        )
        pr_context = _lookup_pr_context(
            issue, github_client, require_success=context.fail_on_github_error
        )
        state = _state_from_labels(issue.labels, labels_config, issue.state, pr_merged)

        # Override with dependency blocker if present, unless already merged/archived.
        block_reason = block_reasons.get(prd.prd_path)
        if block_reason and state not in {
            BacklogPrdState.MERGED,
            BacklogPrdState.ARCHIVED,
        }:
            state = BacklogPrdState.WAITING

        # Use merged URL as PR context URL for the review/merged action.
        if pr_merged and merged_url and pr_context is not None:
            pr_context = replace_pr_context_url(pr_context, merged_url)

        resolved.append(
            replace(
                prd,
                state=state,
                block_reason=block_reason,
                next_action=_compute_next_action(state, pr_context, issue.url),
            )
        )

    # Second pass: highlight downstream PRDs whose upstream just merged.
    final: list[BacklogPrd] = []
    for prd in resolved:
        next_action = prd.next_action
        if prd.state is BacklogPrdState.WAITING and not prd.block_reason:
            # All upstream dependencies cleared but not yet started.
            next_action = {"label": "可开始", "url": None}
        final.append(replace(prd, next_action=next_action))
    return final


def replace_pr_context_url(pr_context: object, url: str) -> object:
    """Return a new PR context with the given URL.

    This helper avoids importing ``PullRequestContext`` directly into the
    function body while still allowing URL override.
    """
    from backend.core.shared.models.agent_runner import PullRequestContext

    if isinstance(pr_context, PullRequestContext):
        return PullRequestContext(
            pr_url=url,
            branch=pr_context.branch,
            head_sha=pr_context.head_sha,
            base_sha=pr_context.base_sha,
            mergeable=pr_context.mergeable,
            checks_state=pr_context.checks_state,
            checks_summary=pr_context.checks_summary,
            number=pr_context.number,
            body=pr_context.body,
        )
    return pr_context
