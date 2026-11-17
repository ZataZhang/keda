"""Issue 单次续跑执行器覆盖用例测试。"""

from __future__ import annotations

import pytest

from backend.core.shared.models.agent_runner_config import AppConfig, IssueSummary
from backend.core.use_cases.issue_retry_overrides import (
    IssueRetryOverrideRequest,
    prepare_issue_retry_override,
)


class _IssueRetryOverrideStore:
    def __init__(self, selection: tuple[str, str] | None) -> None:
        self.selection = selection
        self.pop_count = 0

    def set_issue_retry_override(
        self, *, repo_id: str, issue_number: int, stage: str, executor: str
    ) -> None:
        self.selection = (stage, executor)

    def get_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        return self.selection

    def pop_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        self.pop_count += 1
        selection = self.selection
        self.selection = None
        return selection

    def clear_issue_retry_override(self, *, repo_id: str, issue_number: int) -> None:
        self.selection = None


def test_prepare_retry_override_applies_and_consumes_once() -> None:
    store = _IssueRetryOverrideStore(("implementation", "claude"))
    issue = _issue()

    prepared_issue, claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=issue,
            issue_kind="ready",
            repo_id="keda-main",
            config=AppConfig(),
            run_history_store=store,
        )
    )

    assert prepared_issue.lifecycle_overrides == (("implementation", "claude"),)
    assert claim is not None
    assert store.selection == ("implementation", "claude")
    claim.consume()
    claim.consume()
    assert store.selection is None
    assert store.pop_count == 1


def test_prepare_retry_override_rejects_unregistered_executor() -> None:
    store = _IssueRetryOverrideStore(("implementation", "missing"))

    with pytest.raises(ValueError, match="no longer registered"):
        prepare_issue_retry_override(
            IssueRetryOverrideRequest(
                issue=_issue(),
                issue_kind="ready",
                repo_id="keda-main",
                config=AppConfig(agents={}),
                run_history_store=store,
            )
        )


def test_verifier_override_keeps_implementation_independent() -> None:
    store = _IssueRetryOverrideStore(("verifier", "claude"))
    _, claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=_issue(),
            issue_kind="ready",
            repo_id="keda-main",
            config=AppConfig(),
            run_history_store=store,
        )
    )

    assert claim is not None
    with pytest.raises(ValueError, match="matches the implementation executor"):
        claim.validate_selected_executor("claude")
    claim.validate_selected_executor("codex")


def _issue() -> IssueSummary:
    return IssueSummary(
        number=19,
        title="Retry with a different executor",
        url="https://github.com/example/repo/issues/19",
        body="",
        labels=("agent/ready",),
    )
