"""Tests for blocked-continue use case."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
)
from backend.core.use_cases.agent_runner_failure import format_blocked_failure_comment
from backend.core.use_cases.agent_runner_check_result import CheckActionDirective
from backend.core.use_cases.agent_runner_failure import HumanActionRequiredError
from backend.core.use_cases.blocked_continue import (
    BlockedContinueError,
    blocked_continue_issue,
    _extract_blocked_paths_from_comments,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import default_worktree_path_response, confirm_legacy_workspace


def test_repair_blocker_continuation_receives_problem_and_previous_repairs(tmp_path: Path) -> None:
    """非禁改路径阻塞续跑时，携带需求、已修项和实际阻塞原因。"""
    from backend.core.use_cases.run_agent_once import build_blocked_continuation_prompt

    issue = IssueSummary(140, "RAG", "https://example.test/140", "旧索引任务必须校验授权修订", ())
    comments = [
        "## Agent Runner Pre-PR Review\n清理去重已修复；远端恢复缺少账号权限",
        "## Agent Runner Blocked\n远端沙箱拒绝访问，需要恢复账号权限",
    ]
    prompt = build_blocked_continuation_prompt(issue, tmp_path, (), issue_comments=comments)
    assert issue.body in prompt
    assert all(comment in prompt for comment in comments)
    assert "forbidden-path rules" not in prompt


def test_blocked_continue_restores_blocked_state_when_repair_remains_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """直接续跑再次遇到修复阻塞时，不能遗留 running 标签。"""
    from backend.core.use_cases import blocked_continue as continuation

    github = FakeGitHubClient()
    github._issue_labels[99] = ("agent/blocked",)
    monkeypatch.setattr(continuation, "_find_worktree_path_for_issue", lambda *args: tmp_path)
    monkeypatch.setattr(continuation, "resolve_issue_branch", lambda *args, **kwargs: "issue-99")
    monkeypatch.setattr(continuation, "refresh_runtime_dependencies", lambda: None)

    def retry_repair(**kwargs: object) -> None:
        raise HumanActionRequiredError(
            99,
            CheckActionDirective(
                block_type="pre_pr_review_repair",
                summary="远端沙箱仍拒绝访问",
                next_action="恢复账号权限",
            ),
            [],
        )

    monkeypatch.setattr(continuation, "_process_blocked_resolution", retry_repair)
    with pytest.raises(BlockedContinueError, match="远端沙箱仍拒绝访问"):
        blocked_continue_issue(
            issue_number=99,
            repo_path=tmp_path,
            config=AppConfig(),
            agent="auto",
            github_client=github,
            process_runner=_setup_worktree_runner(),
        )
    assert github.get_issue(99).labels == ("agent/blocked",)
    assert "远端沙箱仍拒绝访问" in github.list_issue_comments(99)[-1]


def _make_blocked_issue(number: int) -> IssueSummary:
    return IssueSummary(
        number=number,
        title=f"Issue #{number}",
        url=f"https://github.com/example/repo/issues/{number}",
        body="",
        labels=("agent/blocked",),
    )


def _setup_worktree_runner() -> FakeProcessRunner:
    responses = {
        ("git", "status", "--porcelain"): CommandResult(
            ("git", "status", "--porcelain"), 0, "", ""
        ),
        ("git", "branch", "--show-current"): CommandResult(
            ("git", "branch", "--show-current"), 0, "issue-99\n", ""
        ),
        ("git", "rev-parse", "HEAD"): CommandResult(
            ("git", "rev-parse", "HEAD"), 0, "abc123\n", ""
        ),
        ("git", "status", "--short"): CommandResult(("git", "status", "--short"), 0, "", ""),
    }
    return FakeProcessRunner(responses=responses)


def test_blocked_continue_missing_blocked_label_fails() -> None:
    fake_client = FakeGitHubClient()
    fake_client.get_issue = lambda n: IssueSummary(
        number=n,
        title=f"Issue #{n}",
        url=f"https://github.com/example/repo/issues/{n}",
        body="",
        labels=("agent/running",),
    )
    runner = FakeProcessRunner()

    with pytest.raises(BlockedContinueError, match="does not have label"):
        blocked_continue_issue(
            issue_number=1,
            repo_path=Path("."),
            config=AppConfig(),
            agent="auto",
            github_client=fake_client,
            process_runner=runner,
        )


def test_blocked_continue_dirty_worktree_fails(tmp_path: Path) -> None:
    confirm_legacy_workspace(tmp_path, tmp_path, 1)
    fake_client = FakeGitHubClient()
    fake_client.get_issue = lambda n: _make_blocked_issue(n)
    responses = {
        **default_worktree_path_response(1),
        ("git", "status", "--porcelain"): CommandResult(
            ("git", "status", "--porcelain"), 0, " M foo.py\n", ""
        ),
        ("git", "branch", "--show-current"): CommandResult(
            ("git", "branch", "--show-current"), 0, "issue-1\n", ""
        ),
    }
    runner = FakeProcessRunner(responses=responses)

    with pytest.raises(BlockedContinueError, match="uncommitted changes"):
        blocked_continue_issue(
            issue_number=1,
            repo_path=tmp_path,
            config=AppConfig(),
            agent="auto",
            github_client=fake_client,
            process_runner=runner,
        )


def test_blocked_continue_wrong_branch_fails(tmp_path: Path) -> None:
    confirm_legacy_workspace(tmp_path, tmp_path, 1)
    fake_client = FakeGitHubClient()
    fake_client.get_issue = lambda n: _make_blocked_issue(n)
    responses = {
        **default_worktree_path_response(1),
        ("git", "status", "--porcelain"): CommandResult(
            ("git", "status", "--porcelain"), 0, "", ""
        ),
        ("git", "branch", "--show-current"): CommandResult(
            ("git", "branch", "--show-current"), 0, "main\n", ""
        ),
    }
    runner = FakeProcessRunner(responses=responses)

    with pytest.raises(BlockedContinueError, match="main"):
        blocked_continue_issue(
            issue_number=1,
            repo_path=tmp_path,
            config=AppConfig(),
            agent="auto",
            github_client=fake_client,
            process_runner=runner,
        )


def test_blocked_continue_already_claimed_returns_false(tmp_path: Path) -> None:
    confirm_legacy_workspace(tmp_path, tmp_path, 99)
    fake_client = FakeGitHubClient()
    # First get_issue returns blocked; later returns running+blocked (simulating another runner claimed it)
    call_count = 0

    def _get_issue(number: int) -> IssueSummary:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_blocked_issue(number)
        return IssueSummary(
            number=number,
            title=f"Issue #{number}",
            url=f"https://github.com/example/repo/issues/{number}",
            body="",
            labels=("agent/running", "agent/blocked"),
        )

    fake_client.get_issue = _get_issue
    responses = {
        **default_worktree_path_response(99),
        ("git", "status", "--porcelain"): CommandResult(
            ("git", "status", "--porcelain"), 0, "", ""
        ),
        ("git", "branch", "--show-current"): CommandResult(
            ("git", "branch", "--show-current"), 0, "issue-99\n", ""
        ),
        ("git", "rev-parse", "HEAD"): CommandResult(
            ("git", "rev-parse", "HEAD"), 0, "abc123\n", ""
        ),
    }
    runner = FakeProcessRunner(responses=responses)

    result = blocked_continue_issue(
        issue_number=99,
        repo_path=tmp_path,
        config=AppConfig(),
        agent="auto",
        github_client=fake_client,
        process_runner=runner,
    )
    assert result is False


def test_extract_blocked_paths_from_comments() -> None:
    body = format_blocked_failure_comment(
        RuntimeError("Refusing to publish forbidden paths: .env, secrets/key"),
        issue_number=1,
    )
    fake_client = FakeGitHubClient()
    fake_client.comment_issue(1, body)
    issue = _make_blocked_issue(1)

    paths = _extract_blocked_paths_from_comments(issue, fake_client)
    assert ".env" in paths
    assert "secrets/key" in paths
