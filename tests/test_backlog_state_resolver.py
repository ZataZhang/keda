"""Tests for backlog state resolver."""

from __future__ import annotations

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.backlog import (
    BacklogPrd,
    BacklogPrdState,
)
from backend.core.use_cases.backlog_state_resolver import (
    BacklogStateResolutionContext,
    resolve_backlog_states,
)
from tests.conftest import FakeGitHubClient


def _make_prd(
    prd_path: str,
    issue_number: int | None = None,
    status: str = "pending",
) -> BacklogPrd:
    return BacklogPrd(
        prd_path=prd_path,
        title="Test",
        status=status,
        priority="P1",
        issue_url=None,
        issue_number=issue_number,
        state=BacklogPrdState.NOT_STARTED,
        acceptance_total=0,
        acceptance_checked=0,
        delivery_dependencies=(),
        updated_at="2026-01-01T00:00:00+00:00",
        block_reason=None,
        next_action=None,
    )


def _resolution_context(
    block_reasons: dict[str, str | None] | None = None,
    *,
    fail_on_github_error: bool = False,
) -> BacklogStateResolutionContext:
    """构造状态解析用例上下文。"""
    return BacklogStateResolutionContext(
        config=AppConfig(),
        block_reasons=block_reasons or {},
        fail_on_github_error=fail_on_github_error,
    )


def test_no_issue_stays_not_started() -> None:
    """PRDs without issues should remain not_started."""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md")
    client = FakeGitHubClient()
    resolved = resolve_backlog_states([prd], client, _resolution_context())
    assert len(resolved) == 1
    assert resolved[0].state == BacklogPrdState.NOT_STARTED
    assert resolved[0].next_action is not None
    assert resolved[0].next_action["label"] == "开始"


def test_ready_label_maps_to_ready_state() -> None:
    """Issue with agent/ready label should map to ready state."""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    client = FakeGitHubClient()
    client._issue_labels[1] = ("agent/ready",)
    resolved = resolve_backlog_states([prd], client, _resolution_context())
    assert resolved[0].state == BacklogPrdState.READY


def test_review_label_maps_to_review_state() -> None:
    """Issue with agent/review label should map to review state."""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md", issue_number=2)
    client = FakeGitHubClient()
    client._issue_labels[2] = ("agent/review",)
    client._issue_comments[2] = ["PR Branch: `issue-2`"]
    client._pr_contexts["issue-2"] = type(
        "FakePrContext",
        (),
        {"pr_url": "https://github.com/org/repo/pull/5", "branch": "issue-2"},
    )()
    resolved = resolve_backlog_states([prd], client, _resolution_context())
    assert resolved[0].state == BacklogPrdState.REVIEW
    assert resolved[0].next_action is not None


def test_closed_issue_with_merged_pr_maps_to_merged() -> None:
    """Closed issue with merged PR should map to merged state."""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md", issue_number=3)
    client = FakeGitHubClient()
    client._issue_states[3] = "CLOSED"
    client._issue_comments[3] = [
        "<!-- iar:event version=1 phase=draft_pr_created cycle=1 pr_branch=issue-3 -->"
    ]
    client._merged_prs["issue-3"] = "https://github.com/org/repo/pull/9"
    resolved = resolve_backlog_states([prd], client, _resolution_context())
    assert resolved[0].state == BacklogPrdState.MERGED
    assert resolved[0].next_action is not None
    assert resolved[0].next_action["label"] == "开始下一个"


def test_archived_prd_is_archived() -> None:
    """PRDs in archive directory should be archived."""
    prd = _make_prd(
        "tasks/archive/P1-FEAT-20260101-a.md",
        issue_number=4,
        status="archived",
    )
    client = FakeGitHubClient()
    resolved = resolve_backlog_states([prd], client, _resolution_context())
    assert resolved[0].state == BacklogPrdState.ARCHIVED


def test_block_reason_overrides_state_to_waiting() -> None:
    """Dependency blocker should set state to waiting."""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md", issue_number=5)
    client = FakeGitHubClient()
    client._issue_labels[5] = ("agent/ready",)
    block_reasons = {prd.prd_path: "等待上游 PRD"}
    resolved = resolve_backlog_states([prd], client, _resolution_context(block_reasons))
    assert resolved[0].state == BacklogPrdState.WAITING
    assert resolved[0].block_reason == "等待上游 PRD"


def test_github_lookup_failure_can_abort_snapshot_scan() -> None:
    """快照构建启用严格策略时，GitHub 查询异常不能降级成新状态。"""
    prd = _make_prd("tasks/pending/P1-FEAT-20260101-a.md", issue_number=6)
    client = FakeGitHubClient()

    def fail_get_issue(_issue_number: int) -> None:
        raise RuntimeError("GitHub unavailable")

    client.get_issue = fail_get_issue  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        resolve_backlog_states(
            [prd],
            client,
            _resolution_context(fail_on_github_error=True),
        )
