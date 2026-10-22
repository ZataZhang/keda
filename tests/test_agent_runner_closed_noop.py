"""覆盖「执行期间关闭且无改动」的收尾路径，以及文档承诺仍须失败的四类边界。"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    CommandResult,
    FailureType,
    IssueSummary,
    RunnerConfig,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases import run_agent_execution_loop as execution_loop
from backend.core.use_cases.run_agent_once import MaxRetriesExceededError
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import (
    config_with_review_disabled,
    make_prd_issue,
    make_ready_issue,
)

#: 与 :func:`_patch_clean_run` 布置的 HEAD 一致，用来判定「HEAD 没有前进」。
_BASE_SHA = "base-sha"

#: 执行循环复用 ``run_agent_once`` 的 logger 对象，断言日志时要按这个名字过滤。
_LOOP_LOGGER = "backend.core.use_cases.run_agent_once"


def _request(
    *,
    issue: IssueSummary,
    github_client: FakeGitHubClient,
    worktree_path: Path,
) -> execution_loop.AgentExecutionRequest:
    """构造一次「耗尽即失败」的执行请求，字段形态与 handler 一致。"""
    return execution_loop.AgentExecutionRequest(
        selected_agent="codex",
        issue=issue,
        worktree_path=worktree_path,
        config=AppConfig(
            runner=RunnerConfig(max_recovery_attempts=0, recovery_retry_delay_seconds=0)
        ),
        process_runner=FakeProcessRunner(),
        before_sha=_BASE_SHA,
        expected_branch=f"issue-{issue.number}",
        repo_id="test/keda",
        github_client=github_client,
    )


def _patch_clean_run(
    monkeypatch: pytest.MonkeyPatch,
    validation_calls: list[str],
) -> None:
    """把首轮布置成「agent 正常退出、HEAD 未前进、worktree 连未跟踪文件都没有」。"""
    monkeypatch.setattr(
        execution_loop,
        "run_agent",
        lambda *_args, **_kwargs: CommandResult(("codex",), 0, "", ""),
    )
    monkeypatch.setattr(execution_loop, "get_head_sha", lambda *_args: _BASE_SHA)
    monkeypatch.setattr(execution_loop, "has_changes", lambda *_args: False)
    monkeypatch.setattr(
        execution_loop,
        "run_verification",
        lambda *_args: validation_calls.append("verification") or [],
    )
    monkeypatch.setattr(execution_loop, "mark_agent_session_complete", lambda *_a, **_k: None)


def _patch_downstream_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与本题无关的交付 / 证据 / verifier 门禁替身为 no-op。

    负例要证明的是「无改动闭合门禁没有放行、循环继续往下走」，后续门禁自身的内容
    由各自的专门测试覆盖。
    """
    monkeypatch.setattr(execution_loop, "ensure_prd_delivery_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(execution_loop, "ensure_validation_evidence_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        execution_loop, "ensure_no_misplaced_evidence_helpers", lambda *_a, **_k: None
    )
    monkeypatch.setattr(execution_loop, "warn_legacy_evidence_helpers", lambda *_a, **_k: None)
    monkeypatch.setattr(execution_loop, "ensure_validation_commands_pass", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "backend.core.use_cases.run_verifier_agent.run_verifier_gate", lambda *_a, **_k: None
    )


def test_closed_issue_without_changes_is_success_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """无 PRD Issue 在执行中关闭且 worktree 未变化时应跳过验证和 recovery。"""
    github_client = FakeGitHubClient()
    # 领取快照是 OPEN：放行与否只能认 fresh read，不能认快照。
    issue = make_ready_issue()
    validation_calls: list[str] = []
    _patch_clean_run(monkeypatch, validation_calls)

    def _close_issue_after_agent(*_args: object, **_kwargs: object) -> CommandResult:
        github_client.close_issue(issue.number)
        return CommandResult(("codex",), 0, "", "")

    monkeypatch.setattr(execution_loop, "run_agent", _close_issue_after_agent)

    result = execution_loop.run_agent_until_committed(
        _request(issue=issue, github_client=github_client, worktree_path=tmp_path)
    )

    assert result.issue_closed_without_changes is True
    assert result.attempt_results[0].failure_type is FailureType.SUCCESS
    assert validation_calls == []
    assert any(call["method"] == "get_issue" for call in github_client.calls)


def test_issue_still_open_without_changes_still_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """fresh read 仍是 OPEN 时，无改动照旧判成 ``no_commits`` 的 agent 失败。"""
    github_client = FakeGitHubClient()
    # 领取快照 CLOSED 但 GitHub 上仍是 OPEN：过期的 CLOSED 不得放行无改动闭合。
    issue = replace(make_ready_issue(), state="CLOSED")
    validation_calls: list[str] = []
    _patch_clean_run(monkeypatch, validation_calls)
    _patch_downstream_gates(monkeypatch)

    with pytest.raises(MaxRetriesExceededError) as error_info:
        execution_loop.run_agent_until_committed(
            _request(issue=issue, github_client=github_client, worktree_path=tmp_path)
        )

    attempt = error_info.value.attempt_results[-1]
    assert attempt.failure_type is FailureType.NO_COMMITS
    assert attempt.failure_phase == "agent"
    assert validation_calls == ["verification"]


def test_issue_state_read_failure_keeps_failing_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """读不到 Issue 状态时 fail-closed：告警后走原 no-commit 流程，不当成已关闭。"""
    github_client = FakeGitHubClient()
    github_client.set_get_issue_error(RuntimeError("gh: network down"))
    issue = make_ready_issue()
    validation_calls: list[str] = []
    _patch_clean_run(monkeypatch, validation_calls)
    _patch_downstream_gates(monkeypatch)

    with (
        caplog.at_level(logging.WARNING, logger=_LOOP_LOGGER),
        pytest.raises(MaxRetriesExceededError) as error_info,
    ):
        execution_loop.run_agent_until_committed(
            _request(issue=issue, github_client=github_client, worktree_path=tmp_path)
        )

    assert error_info.value.attempt_results[-1].failure_type is FailureType.NO_COMMITS
    assert f"Could not confirm Issue #{issue.number} closure after a no-change run" in caplog.text
    assert validation_calls == ["verification"]


def test_closed_issue_with_changes_does_not_take_the_noop_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """worktree 有变更时，即使 Issue 已关闭也不走无改动闭合，且不去读 GitHub 状态。"""
    github_client = FakeGitHubClient()
    issue = make_ready_issue()
    github_client.close_issue(issue.number)
    validation_calls: list[str] = []
    _patch_clean_run(monkeypatch, validation_calls)
    _patch_downstream_gates(monkeypatch)
    monkeypatch.setattr(execution_loop, "has_changes", lambda *_args: True)
    monkeypatch.setattr(execution_loop, "commit_requested_changes", lambda *_a, **_k: [])

    with pytest.raises(MaxRetriesExceededError) as error_info:
        execution_loop.run_agent_until_committed(
            _request(issue=issue, github_client=github_client, worktree_path=tmp_path)
        )

    assert error_info.value.attempt_results[-1].failure_type is FailureType.UNCOMMITTED_CHANGES
    assert not any(call["method"] == "get_issue" for call in github_client.calls)


def test_prd_issue_does_not_take_the_noop_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """带 ``PRD path:`` 锚点的任务必须走完交付门禁，不因 Issue 关闭而跳过。"""
    github_client = FakeGitHubClient()
    issue = make_prd_issue()
    github_client.close_issue(issue.number)
    validation_calls: list[str] = []
    _patch_clean_run(monkeypatch, validation_calls)
    _patch_downstream_gates(monkeypatch)

    with pytest.raises(MaxRetriesExceededError) as error_info:
        execution_loop.run_agent_until_committed(
            _request(issue=issue, github_client=github_client, worktree_path=tmp_path)
        )

    attempt = error_info.value.attempt_results[-1]
    assert attempt.failure_type is FailureType.NO_COMMITS
    assert not any(call["method"] == "get_issue" for call in github_client.calls)
    assert validation_calls == ["verification"]


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
