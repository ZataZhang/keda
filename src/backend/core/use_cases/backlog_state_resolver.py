"""Resolve live GitHub state for backlog PRDs.

Maps Issue labels, PR state, and dependency blockers onto the unified
``BacklogPrdState`` and computes the next actionable item for each PRD. CI/CD
delivery is projected in the same pass (see
:func:`backend.core.use_cases.backlog_ci_delivery.build_ci_delivery`), reusing
the issue, PR context and the single comment fetch already made here instead of
opening a second round of GitHub calls.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

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
from backend.core.use_cases.backlog_ci_delivery import build_ci_delivery

_logger = logging.getLogger(__name__)


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
    comments: list[str] | None = None,
) -> tuple[bool, str | None]:
    """Return whether the associated PR has been merged and its URL.

    ``comments`` 由调用方传入时不再重复请求 GitHub：Backlog 每个 PRD 只允许读一次
    评论（见 :func:`resolve_backlog_states`）。
    """
    if comments is None:
        try:
            comments = github_client.list_issue_comments(issue_number)
        except Exception as exc:  # noqa: BLE001
            _logger.info("Failed to list comments for issue #%s: %s", issue_number, exc)
            comments = []

    # Reuse the monitor helper to resolve the PR branch from event markers.
    from backend.core.shared.models.agent_runner import IssueSummary

    issue = IssueSummary(number=issue_number, title="", url="", body=issue_body, labels=())
    pr_branch = _extract_pr_branch_from_issue(issue, github_client, comments)
    if pr_branch is None:
        return False, None

    try:
        merged_url = github_client.find_merged_pr_by_head(pr_branch)
    except Exception as exc:  # noqa: BLE001
        _logger.info("Failed to find merged PR for %s: %s", pr_branch, exc)
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
    config: AppConfig,
    block_reasons: Mapping[str, str | None],
) -> list[BacklogPrd]:
    """Resolve live GitHub state for a list of backlog PRDs.

    Args:
        prds: PRDs from the scanner.
        github_client: GitHub client.
        config: Merged app config for the target repository.
        block_reasons: Dependency blocker map from :func:`evaluate_backlog_dependencies`.

    Returns:
        New list of PRDs with ``state``, ``block_reason``, and ``next_action`` updated.
    """
    labels_config = config.labels
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

        # 每个 PRD 只读一次 Issue 评论：merged 判定、PR 分支解析与 CI 投影共用它。
        # 多读一次不会改变结果，但会让评论数在这一次响应里前后不一致，从而在下一轮
        # supervisor 的 context-changed 判定里制造假变化。
        try:
            comments = github_client.list_issue_comments(prd.issue_number)
        except Exception as exc:  # noqa: BLE001
            _logger.info("Failed to list comments for issue #%s: %s", prd.issue_number, exc)
            comments = []
        pr_branch = _extract_pr_branch_from_issue(issue, github_client, comments)

        pr_merged, merged_url = _is_pr_merged(prd.issue_number, github_client, issue.body, comments)
        pr_context = _lookup_pr_context(issue, github_client, comments)
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

        ci_delivery = build_ci_delivery(
            prd_path=prd.prd_path,
            pr_context=pr_context,
            comments=comments,
            config=config,
            pr_branch=pr_branch or "",
            unavailable_reason=(
                "PR 上下文不可用（GitHub 读取失败或 PR 状态异常）；" "不视为通过，也不启动修复。"
                if pr_branch and pr_context is None and not pr_merged
                else ""
            ),
        )

        resolved.append(
            replace(
                prd,
                state=state,
                block_reason=block_reason,
                ci_delivery=ci_delivery,
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
