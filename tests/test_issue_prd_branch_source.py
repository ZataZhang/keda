"""已发布任务分支上的 PRD 读取与 runner 起点测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_worktree_create import _resolve_fork_base
from backend.core.use_cases.lifecycle_agent_resolution import attach_prd_lifecycle_overrides
from backend.infrastructure.process_runner import SubprocessRunner
from tests.support.agent_runner import create_commit, init_bare_git_repo, init_git_repo


def _run_git(repo_path: Path, *arguments: str) -> str:
    """在夹具仓库中运行真实 Git 命令。"""
    command_result = subprocess.run(
        ["git", *arguments],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return command_result.stdout.strip()


def _published_issue(*, issue_number: int, commit_sha: str) -> IssueSummary:
    """构造带有任务分支发布 marker 的 Issue。"""
    prd_relative_path = "tasks/pending/P1-FEAT-20261119-000000-demo.md"
    return IssueSummary(
        number=issue_number,
        title="Published PRD",
        url=f"https://github.com/example/repo/issues/{issue_number}",
        body=(
            f"- PRD path: `{prd_relative_path}`\n\n"
            "<!-- kc:prd-publication version=1 state=published queue_ready=true "
            f"branch=issue-{issue_number} commit={commit_sha} -->"
        ),
        labels=("agent/ready",),
    )


def _init_remote_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """创建 GitHub URL 重写到本地 bare 仓库的 checkout。"""
    remote_path = init_bare_git_repo(tmp_path / "origin.git")
    repo_path = init_git_repo(tmp_path / "checkout")
    _run_git(repo_path, "remote", "add", "origin", "https://github.com/example/repo.git")
    _run_git(
        repo_path,
        "config",
        f"url.{remote_path.as_uri()}.insteadOf",
        "https://github.com/example/repo.git",
    )
    return repo_path, remote_path


def test_runner_reads_published_prd_and_forks_from_issue_branch(tmp_path: Path) -> None:
    """执行器选择前从远端任务分支覆盖过期的 main PRD。"""
    repo_path, _ = _init_remote_checkout(tmp_path)
    prd_relative_path = Path("tasks/pending/P1-FEAT-20261119-000000-demo.md")
    main_prd_path = repo_path / prd_relative_path
    main_prd_path.parent.mkdir(parents=True)
    main_prd_path.write_text(
        "# PRD: Demo\n\n- lifecycle_agents:\n  - implementation: codex\n",
        encoding="utf-8",
    )
    _run_git(repo_path, "add", prd_relative_path.as_posix())
    create_commit(repo_path, "add stale main PRD")
    _run_git(repo_path, "push", "-u", "origin", "main")

    _run_git(repo_path, "checkout", "-b", "issue-17")
    main_prd_path.write_text(
        "# PRD: Demo\n\n- lifecycle_agents:\n  - implementation: qoder\n",
        encoding="utf-8",
    )
    _run_git(repo_path, "add", prd_relative_path.as_posix())
    branch_commit_sha = create_commit(repo_path, "publish task branch PRD")
    _run_git(repo_path, "push", "-u", "origin", "issue-17")
    _run_git(repo_path, "checkout", "main")

    published_issue = _published_issue(issue_number=17, commit_sha=branch_commit_sha)
    issue_with_remote_overrides = attach_prd_lifecycle_overrides(
        published_issue,
        repo_path,
        process_runner=SubprocessRunner(),
        git_remote="origin",
    )
    fork_base_ref = _resolve_fork_base(
        repo_path,
        published_issue,
        AppConfig(),
        SubprocessRunner(),
    )

    assert dict(issue_with_remote_overrides.lifecycle_overrides)["implementation"] == "qoder"
    assert fork_base_ref == "origin/issue-17"
    assert (
        (repo_path / prd_relative_path)
        .read_text(encoding="utf-8")
        .endswith("implementation: codex\n")
    )


def test_published_issue_branch_missing_fails_closed(tmp_path: Path) -> None:
    """已发布 marker 的分支缺失时不能静默回退到 main。"""
    repo_path, _ = _init_remote_checkout(tmp_path)
    (repo_path / "README.md").write_text("main only\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    create_commit(repo_path, "initial main")
    _run_git(repo_path, "push", "-u", "origin", "main")
    published_issue = _published_issue(issue_number=18, commit_sha="a" * 40)

    with pytest.raises(RuntimeError, match="issue-18"):
        _resolve_fork_base(repo_path, published_issue, AppConfig(), SubprocessRunner())
