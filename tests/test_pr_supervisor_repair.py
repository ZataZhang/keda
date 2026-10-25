"""测试 Post-PR 修复遇到空提交请求时的收尾。"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.core.use_cases.agent_runner_git as agent_runner_git
import backend.core.use_cases.pr_supervisor_repair as pr_supervisor_repair
from backend.core.shared.models.agent_runner import AppConfig, CommandResult, IssueSummary
from backend.core.use_cases.agent_runner_commit import EmptyCommitRequestError
from tests.conftest import FakeProcessRunner


def test_execute_repair_verifies_and_returns_after_empty_commit_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空提交请求仍验证当前 tree 并回到监督循环，不把 Issue 判为执行失败。"""
    issue = IssueSummary(
        number=317,
        title="Nightly CI patrol",
        url="https://github.com/example/repo/issues/317",
        body="",
        labels=(),
    )
    worktree_path = tmp_path / "issue-317"
    worktree_path.mkdir()
    fake_runner = FakeProcessRunner()
    verification_result = CommandResult(
        command=("just", "test"),
        return_code=0,
        stdout="passed",
        stderr="",
    )
    verification_worktrees: list[Path] = []

    def report_expected_head(
        _repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
    ) -> str:
        return "head-317"

    def report_expected_branch(
        _repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
    ) -> str:
        return "issue-317"

    def write_empty_repair_request(
        _repair_agent: str,
        _repair_prompt: str,
        repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
        **_kwargs: object,
    ) -> CommandResult:
        request_path = repair_worktree_path / ".agent-runner" / "commit-request.json"
        request_path.parent.mkdir(parents=True)
        request_path.write_text('{"commit_message": "repair Issue #317"}', encoding="utf-8")
        return CommandResult(command=("noop",), return_code=0, stdout="", stderr="")

    def report_empty_commit_request(
        _issue: IssueSummary,
        repair_worktree_path: Path,
        _config: AppConfig,
        _process_runner: FakeProcessRunner,
        *,
        expected_branch: str,
    ) -> list[CommandResult]:
        assert expected_branch == "issue-317"
        request_path = repair_worktree_path / ".agent-runner" / "commit-request.json"
        request_path.unlink()
        raise EmptyCommitRequestError()

    def record_verification(
        repair_worktree_path: Path,
        _config: AppConfig,
        _process_runner: FakeProcessRunner,
    ) -> list[CommandResult]:
        verification_worktrees.append(repair_worktree_path)
        return [verification_result]

    monkeypatch.setattr(agent_runner_git, "get_head_sha", report_expected_head)
    monkeypatch.setattr(agent_runner_git, "get_current_branch", report_expected_branch)
    monkeypatch.setattr(pr_supervisor_repair, "run_agent_with_prompt", write_empty_repair_request)
    monkeypatch.setattr(
        pr_supervisor_repair,
        "commit_requested_changes",
        report_empty_commit_request,
    )
    monkeypatch.setattr(pr_supervisor_repair, "run_verification", record_verification)

    verification_results = pr_supervisor_repair.execute_repair(
        issue=issue,
        worktree_path=worktree_path,
        config=AppConfig(),
        process_runner=fake_runner,
        pr_branch="issue-317",
        expected_head="head-317",
        repair_agent="codex",
    )

    assert verification_results == [verification_result]
    assert verification_worktrees == [worktree_path]
    assert ("git", "push", "origin", "issue-317") in [tuple(call) for call in fake_runner.calls]
