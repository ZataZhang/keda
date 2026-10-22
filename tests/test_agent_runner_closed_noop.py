"""覆盖执行期间关闭且无变更的 Issue 收尾路径。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    CommandResult,
    FailureType,
    RunnerConfig,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import run_agent_execution_loop as execution_loop
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import config_with_review_disabled, make_ready_issue


@pytest.mark.parametrize("initial_state", ["OPEN", "CLOSED"])
def test_closed_issue_without_changes_is_success_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    initial_state: str,
) -> None:
    """无 PRD Issue 在执行中关闭且 worktree 未变化时应跳过验证和 recovery。"""
    github_client = FakeGitHubClient()
    issue = replace(make_ready_issue(), state=initial_state)
    validation_calls: list[str] = []

    def _close_issue_after_agent(*_args: object, **_kwargs: object) -> CommandResult:
        github_client._issue_states[issue.number] = "CLOSED"
        return CommandResult(("codex",), 0, "", "")

    monkeypatch.setattr(execution_loop, "run_agent", _close_issue_after_agent)
    monkeypatch.setattr(execution_loop, "get_head_sha", lambda *_args: "base-sha")
    monkeypatch.setattr(execution_loop, "has_changes", lambda *_args: False)
    monkeypatch.setattr(
        execution_loop,
        "run_verification",
        lambda *_args: validation_calls.append("verification") or [],
    )
    monkeypatch.setattr(execution_loop, "mark_agent_session_complete", lambda *_a, **_k: None)

    request = execution_loop.AgentExecutionRequest(
        selected_agent="codex",
        issue=issue,
        worktree_path=tmp_path,
        config=AppConfig(runner=RunnerConfig(max_recovery_attempts=0)),
        process_runner=FakeProcessRunner(),
        before_sha="base-sha",
        expected_branch=f"issue-{issue.number}",
        repo_id="test/keda",
        github_client=github_client,
    )

    result = execution_loop.run_agent_until_committed(request)

    assert result.issue_closed_without_changes is True
    assert result.attempt_results[0].failure_type is FailureType.SUCCESS
    assert validation_calls == []
    assert any(call["method"] == "get_issue" for call in github_client.calls)


def test_ready_issue_skips_publication_for_closed_no_change_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """执行循环确认无改动完成后，ready Issue 处理器不得继续发布 PR。"""
    from backend.core.use_cases import agent_runner_issue_handlers as handlers

    github_client = FakeGitHubClient()
    issue = make_ready_issue()
    config = config_with_review_disabled(tmp_path)
    captured: dict[str, object] = {}
    stage_decision = SimpleNamespace(
        issue=issue,
        publish_stage=PublishStage.NORMAL,
        direct_pr_label=None,
    )

    monkeypatch.setattr(handlers, "choose_agent", lambda *_args: "codex")
    monkeypatch.setattr(handlers, "resolve_implementation_model_selection", lambda *_a, **_k: None)
    monkeypatch.setattr(handlers, "assert_run_gate", lambda *_args: None)
    monkeypatch.setattr(
        handlers,
        "arbitrate_first_claim",
        lambda **_kwargs: SimpleNamespace(comment_id="claim"),
    )
    monkeypatch.setattr(handlers, "reset_unstarted_direct_pr_selection", lambda *_args: None)
    monkeypatch.setattr(handlers, "resolve_claim_publish_stage", lambda *_args: stage_decision)
    monkeypatch.setattr(handlers, "create_or_reuse_worktree", lambda *_args: tmp_path)
    monkeypatch.setattr(handlers, "get_head_sha", lambda *_args: "base-sha")
    monkeypatch.setattr(handlers, "get_current_branch", lambda *_args: f"issue-{issue.number}")
    monkeypatch.setattr(handlers, "record_issue_input_snapshot", lambda *_args: None)
    monkeypatch.setattr(handlers, "_resume_direct_pr_handoff", lambda *_args: False)
    monkeypatch.setattr(handlers, "admit_claimed_selection", lambda *_args: None)
    monkeypatch.setattr(handlers, "_reuse_existing_local_commit", lambda *_args: None)

    def _closed_no_change_result(**kwargs: object) -> AgentCommitResult:
        captured.update(kwargs)
        return AgentCommitResult([], [], issue_closed_without_changes=True)

    monkeypatch.setattr(execution_loop, "execute_agent_request", _closed_no_change_result)
    monkeypatch.setattr(
        handlers,
        "_finish_implementation_publication",
        lambda **_kwargs: pytest.fail("closed no-change Issues must not create a PR"),
    )

    handlers._process_ready_issue(
        issue=issue,
        repo_path=Path("."),
        config=config,
        agent="codex",
        github_client=github_client,
        process_runner=FakeProcessRunner(),
    )

    request = captured["request"]
    assert isinstance(request, execution_loop.AgentExecutionRequest)
    assert request.github_client is github_client
