"""post-PR supervisor 的 rebase 冲突恢复测试。"""

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, CommandResult, IssueSummary
from backend.core.use_cases.pr_supervisor import execute_rebase
from tests.conftest import FakeProcessRunner


def test_execute_rebase_conflict_agent_empty_commit_request_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空提交请求仍尝试继续 rebase，不把监督 Issue 误判为失败。"""
    issue = IssueSummary(number=331, title="巡查修复", url="U", body="B", labels=())
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    request_path = worktree_path / ".agent-runner" / "commit-request.json"
    verification_results = [
        CommandResult(command=("just", "test"), return_code=0, stdout="passed", stderr="")
    ]
    rebase_continue = ("git", "-c", "core.editor=true", "rebase", "--continue")
    push_command = ("git", "push", "--force-with-lease", "origin", "issue-331")
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout="abc123\n",
                stderr="",
            ),
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-331\n",
                stderr="",
            ),
            ("git", "fetch", "origin", "main"): CommandResult(
                command=("git", "fetch", "origin", "main"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            ("git", "rebase", "origin/main"): CommandResult(
                command=("git", "rebase", "origin/main"),
                return_code=1,
                stdout="",
                stderr="CONFLICT",
            ),
            ("git", "diff", "--name-only", "--diff-filter=U"): CommandResult(
                command=("git", "diff", "--name-only", "--diff-filter=U"),
                return_code=0,
                stdout="file.py\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            rebase_continue: CommandResult(
                command=rebase_continue,
                return_code=0,
                stdout="",
                stderr="",
            ),
            push_command: CommandResult(
                command=push_command,
                return_code=0,
                stdout="",
                stderr="",
            ),
        }
    )

    def write_empty_commit_request(
        _agent_name: str,
        _prompt: str,
        agent_worktree_path: Path,
        _process_runner: FakeProcessRunner,
        **_kwargs: object,
    ) -> CommandResult:
        agent_request_path = agent_worktree_path / ".agent-runner" / "commit-request.json"
        agent_request_path.parent.mkdir(parents=True, exist_ok=True)
        agent_request_path.write_text(
            '{"commit_message": "repair Issue #331 rebase"}',
            encoding="utf-8",
        )
        return CommandResult(command=("noop",), return_code=0, stdout="", stderr="")

    monkeypatch.setattr(
        "backend.core.use_cases.pr_supervisor.run_agent_with_prompt",
        write_empty_commit_request,
    )
    monkeypatch.setattr(
        "backend.core.use_cases.pr_supervisor._current_rebase_conflict_commit",
        lambda _worktree_path, _process_runner: "conflict-commit",
    )
    monkeypatch.setattr(
        "backend.core.use_cases.pr_supervisor.ensure_verification_passed_with_recovery",
        lambda **_kwargs: verification_results,
    )

    actual_results = execute_rebase(
        issue=issue,
        worktree_path=worktree_path,
        config=AppConfig(),
        process_runner=fake_runner,
        pr_branch="issue-331",
        expected_head="abc123",
        supervisor_agent="codex",
    )

    commands = [tuple(call) for call in fake_runner.calls]
    assert actual_results == verification_results
    assert rebase_continue in commands
    assert push_command in commands
    assert ("git", "add", "-A") not in commands
    assert not request_path.exists()
