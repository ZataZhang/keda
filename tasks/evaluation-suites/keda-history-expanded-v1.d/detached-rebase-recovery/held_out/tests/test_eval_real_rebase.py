"""通过真实 Git 冲突、detached HEAD 与本地远端验证 rebase 恢复。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    GitConfig,
    IssueSummary,
    PostPrSupervisorConfig,
    RunnerConfig,
)
from backend.core.use_cases import pr_supervisor
from backend.infrastructure.process_runner import SubprocessRunner


def _git(repo: Path, *argv: str) -> str:
    command = subprocess.run(
        ["git", *argv],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    return command.stdout.strip()


@pytest.mark.parametrize(
    "branch_proof",
    ("expected", "wrong", "unavailable"),
)
def test_resolved_real_detached_rebase_pushes_expected_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    branch_proof: str,
) -> None:
    local = tmp_path / "worktree"
    local.mkdir()
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(local, "init", "-b", "main")
    _git(local, "config", "user.name", "Frozen Fixture")
    _git(local, "config", "user.email", "fixture@example.invalid")
    _git(local, "config", "core.editor", "true")
    _git(local, "remote", "add", "origin", str(origin))
    value = local / "value.txt"
    value.write_text("ancestor\n", encoding="utf-8")
    _git(local, "add", "value.txt")
    _git(local, "commit", "-m", "Ancestor")
    _git(local, "switch", "-c", "issue-73")
    value.write_text("topic change\n", encoding="utf-8")
    _git(local, "commit", "-am", "Topic change")
    _git(local, "push", "-u", "origin", "issue-73")
    expected_head = _git(local, "rev-parse", "HEAD")
    _git(local, "switch", "main")
    value.write_text("base change\n", encoding="utf-8")
    _git(local, "commit", "-am", "Conflicting base change")
    _git(local, "push", "-u", "origin", "main")
    base_head = _git(local, "rev-parse", "HEAD")
    _git(local, "switch", "issue-73")

    def resolve_conflict(_agent: str, prompt: str, repo: Path, _runner: object) -> None:
        assert _git(repo, "branch", "--show-current") == ""
        assert "value.txt" in prompt
        value.write_text("resolved from both branches\n", encoding="utf-8")
        commit_request = repo / ".agent-runner" / "commit-request.json"
        commit_request.parent.mkdir(parents=True)
        commit_request.write_text(
            json.dumps({"commit_message": "Resolve fixture conflict"}),
            encoding="utf-8",
        )
        if branch_proof != "expected":
            metadata = repo / _git(repo, "rev-parse", "--git-path", "rebase-merge/head-name")
            assert metadata.is_file()
            if branch_proof == "wrong":
                metadata.write_text("refs/heads/unrelated\n", encoding="utf-8")
            else:
                metadata.unlink()

    monkeypatch.setattr(pr_supervisor, "run_agent_with_prompt", resolve_conflict)

    def execute() -> None:
        pr_supervisor.execute_rebase(
            issue=IssueSummary(
                number=73,
                title="Resolve issue branch conflict",
                url="https://github.com/example/fixture/issues/73",
                body="",
                labels=(),
            ),
            worktree_path=local,
            config=AppConfig(
                git=GitConfig(remote="origin", base_branch="main"),
                runner=RunnerConfig(verification_commands=()),
                post_pr_supervisor=PostPrSupervisorConfig(max_repair_attempts=1),
            ),
            process_runner=SubprocessRunner(),
            pr_branch="issue-73",
            expected_head=expected_head,
            supervisor_agent="codex",
        )

    if branch_proof != "expected":
        with pytest.raises(RuntimeError, match="(?i)(branch|detached|rebase)"):
            execute()
        assert _git(local, "branch", "--show-current") == ""
        assert _git(origin, "rev-parse", "refs/heads/issue-73") == expected_head
        return
    execute()
    assert _git(local, "branch", "--show-current") == "issue-73"
    assert _git(local, "rev-parse", "HEAD^") == base_head
    assert value.read_text(encoding="utf-8") == "resolved from both branches\n"
    assert _git(local, "rev-parse", "HEAD") == _git(origin, "rev-parse", "refs/heads/issue-73")
