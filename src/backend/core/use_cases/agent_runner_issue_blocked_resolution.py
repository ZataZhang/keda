"""Blocked Issue 的解除请求识别。"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary, ReviewEventMarker
from backend.core.use_cases.agent_runner_workflow import find_latest_unconsumed_marker

_BLOCKED_RESOLUTION_COMPLETION_PHASES = {
    "implementation_complete",
    "draft_pr_created",
    "publish_recovered",
    "rebase_repair_complete",
    "blocked_resolution_complete",
}


def _guard_blocked_issue_has_resolution(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> ReviewEventMarker | None:
    """检测 blocked Issue 是否包含未消费的解除请求 marker。

    Args:
        issue: Issue 对象。
        github_client: GitHub 客户端。

    Returns:
        未消费的 blocked_resolution marker，或 None。
    """
    comments = github_client.list_issue_comments(issue.number)
    return find_latest_unconsumed_marker(
        comments,
        phase="blocked_resolution_requested",
        completion_phases=_BLOCKED_RESOLUTION_COMPLETION_PHASES,
    )
