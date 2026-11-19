"""Agent Runner Issue 的资源准入与 Direct PR 交接判定。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.agent_runner_orchestration_models import RunOnceRequest

_logger = logging.getLogger("backend.core.use_cases.agent_runner_orchestration_runtime")


def _has_published_direct_pr_handoff(
    github_client: IGitHubClient,
    issue: IssueSummary,
) -> bool:
    """只读证明待交接 PR，避免发现过滤器拦截已发布轮次。"""
    from backend.core.use_cases.agent_runner_direct_pr_round import has_readonly_direct_pr_cleanup

    try:
        return has_readonly_direct_pr_cleanup(github_client, issue)
    except Exception as exc:  # noqa: BLE001 - 关联读失败不能绕过发现门禁。
        _logger.warning(
            "Cannot establish pending Direct PR handoff for Issue #%d: %s", issue.number, exc
        )
        return False


def _is_issue_admitted(request: RunOnceRequest, issue: IssueSummary) -> bool:
    """在领取或启动 worker 前检查可选资源准入门。"""
    if request.issue_admission_check is None or request.issue_admission_check():
        return True
    _logger.warning(
        "Disk admission paused before claiming Issue #%d; the Issue remains queued.",
        issue.number,
    )
    return False


def _process_issue_if_admitted(
    issue: IssueSummary,
    issue_kind: str,
    *,
    request: RunOnceRequest,
    process_issue: Callable[..., int],
    **process_kwargs: Any,
) -> int:
    """准入通过后委托给单 Issue 处理流程。"""
    if not _is_issue_admitted(request, issue):
        return 0
    return process_issue(issue, issue_kind, **process_kwargs)
