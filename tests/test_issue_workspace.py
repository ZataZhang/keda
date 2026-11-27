"""通过真实 Git 和 worktree CLI 验证跨仓库同号任务隔离。"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.api.cli import main
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, WorktreeConfig
from backend.core.use_cases.agent_runner_worktree_create import (
    create_or_reuse_worktree,
    format_command,
)
from backend.core.use_cases.agent_runner_worktree_probe import _find_worktree_path_for_issue
from backend.core.use_cases.issue_workspace import (
    bind_issue_workspace,
    issue_branch_name,
    issue_workspace_identity,
    local_issue_worktree_paths,
    resolve_issue_branch,
    validate_issue_workspace,
)
from backend.infrastructure.git.worktree import WorktreeManager
from backend.infrastructure.process_runner import CommandResult, SubprocessRunner
from tests.support.agent_runner import create_commit, init_git_repo
from tests.support.agent_runner import init_bare_git_repo
from tests.conftest import FakeGitHubClient
from backend.core.use_cases.worktree_cleanup import (
    WorktreeCleanupRequest,
    cleanup_managed_worktrees,
    WorktreeCleanupStatus,
)
from backend.core.use_cases.recover_publish import resolve_existing_worktree


def _issue(owner: str = "dewell-ai-assistant-team") -> IssueSummary:
    return IssueSummary(142, "nightly", f"https://github.com/{owner}/freshai/issues/142", "", ())


def _git(repo_path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


class _CliWorktreeRunner(SubprocessRunner):
    """仅把子进程 CLI 入口放到进程内，Git 与磁盘边界保持真实。"""

    def run(self, command: list[str], *, cwd: Path, check: bool = True) -> CommandResult:
        if command[:2] == ["kc", "worktree"]:
            output = io.StringIO()
            with contextlib.chdir(cwd), contextlib.redirect_stdout(output):
                exit_code = main(command[1:])
            return CommandResult(
                command=tuple(command), return_code=exit_code, stdout=output.getvalue(), stderr=""
            )
        return super().run(command, cwd=cwd, check=check)


def _repository(tmp_path: Path) -> Path:
    repo_path = init_git_repo(tmp_path / "repo")
    (repo_path / ".kedacode.toml").write_text(
        '[agent_runner.repository]\nid="test"\ngithub_repo="dewell-ai-assistant-team/freshai"\n',
        encoding="utf-8",
    )
    _git(repo_path, "add", ".kedacode.toml")
    create_commit(repo_path, "initial")
    return repo_path


def test_same_number_different_repositories_preserves_detached_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复现旧目录 detached、新 issue-142 指向 main，两个新任务都不复用旧历史。"""
    repo_path = _repository(tmp_path)
    base_sha = _git(repo_path, "rev-parse", "HEAD")
    legacy_path = WorktreeManager(repo_path).create("issue-142", "main")
    old_sha = create_commit(legacy_path, "unmerged old repository work")
    _git(legacy_path, "checkout", "--detach")
    _git(repo_path, "branch", "-f", "issue-142", "main")
    old_file = legacy_path / "keep-local-work.txt"
    old_file.write_text("preserve me\n", encoding="utf-8")
    database_requests = []
    monkeypatch.setattr(
        "backend.core.use_cases.agent_runner_worktree_create.provision_worktree_database",
        lambda request, _runner: database_requests.append(request),
    )
    config = AppConfig(worktree=WorktreeConfig(provision_database=True))
    runner = _CliWorktreeRunner()

    organization_path = create_or_reuse_worktree(repo_path, _issue(), config, runner)
    personal_path = create_or_reuse_worktree(repo_path, _issue("ZataZhang"), config, runner)

    assert (
        organization_path
        == repo_path / ".kc-worktrees/kc/dewell-ai-assistant-team/freshai/issue-142"
    )
    assert personal_path == repo_path / ".kc-worktrees/kc/zatazhang/freshai/issue-142"
    assert organization_path != personal_path
    assert _git(organization_path, "rev-parse", "HEAD") == base_sha
    assert _git(personal_path, "rev-parse", "HEAD") == base_sha
    assert _git(legacy_path, "rev-parse", "HEAD") == old_sha
    assert _git(legacy_path, "branch", "--show-current") == ""
    assert _git(repo_path, "rev-parse", "issue-142") == base_sha
    assert old_file.read_text(encoding="utf-8") == "preserve me\n"
    assert create_or_reuse_worktree(repo_path, _issue(), config, runner) == organization_path
    assert (
        _find_worktree_path_for_issue(repo_path, _issue(), config, _CliWorktreeRunner())
        == organization_path
    )
    assert (
        resolve_existing_worktree(repo_path, _issue(), config, _CliWorktreeRunner())
        == organization_path
    )
    assert local_issue_worktree_paths(repo_path, 142) == (organization_path,)
    assert _git(organization_path, "status", "--porcelain") == ""
    assert [request.workspace_key for request in database_requests] == [
        issue_branch_name(_issue()),
        issue_branch_name(_issue("ZataZhang")),
        issue_branch_name(_issue()),
    ]


def test_foreign_owner_record_refuses_reuse_before_copying_env(tmp_path: Path) -> None:
    """同名目录的归属记录冲突时，不修分支、不补 env、不启动后续执行。"""
    repo_path = _repository(tmp_path)
    config = AppConfig(worktree=WorktreeConfig(provision_database=False))
    runner = _CliWorktreeRunner()
    worktree_path = create_or_reuse_worktree(repo_path, _issue(), config, runner)
    record_path = worktree_path / ".agent-runner/issue-workspace.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["issue_url"] = _issue("ZataZhang").url.lower()
    record_path.write_text(json.dumps(record), encoding="utf-8")
    (repo_path / ".env.local").write_text("EXAMPLE=keep-at-root\n", encoding="utf-8")

    with pytest.raises(ValueError, match="belongs to"):
        create_or_reuse_worktree(repo_path, _issue(), config, runner)
    assert not (worktree_path / ".env.local").exists()
    with pytest.raises(ValueError, match="belongs to"):
        _find_worktree_path_for_issue(repo_path, _issue(), config, runner)


def test_only_full_prd_identity_allows_legacy_adoption(tmp_path: Path) -> None:
    """同号的其他仓库 PRD 不算归属证据，准确的完整 URL 才允许续跑。"""
    repo_path = _repository(tmp_path)
    legacy_path = WorktreeManager(repo_path).create("issue-142", "main")
    prd_path = legacy_path / "tasks/archive/example.md"
    prd_path.parent.mkdir(parents=True)
    issue = replace(_issue(), body="PRD path: `tasks/pending/example.md`")
    prd_path.write_text(f"- GitHub Issue: {_issue('ZataZhang').url}\n", encoding="utf-8")
    assert resolve_issue_branch(repo_path, issue) == issue_branch_name(issue)
    prd_path.write_text(f"- GitHub Issue: {issue.url}\n", encoding="utf-8")
    assert resolve_issue_branch(repo_path, issue) == "issue-142"
    bind_issue_workspace(repo_path, issue, legacy_path, branch="issue-142")
    prd_path.unlink()
    assert resolve_issue_branch(repo_path, issue) == "issue-142"
    assert resolve_issue_branch(legacy_path, issue) == "issue-142"
    validate_issue_workspace(legacy_path, issue)
    with pytest.raises(ValueError, match="belongs to"):
        validate_issue_workspace(legacy_path, _issue("ZataZhang"))


def test_legacy_template_uses_the_repository_scoped_branch() -> None:
    """用户原有配置无需迁移，创建和定位命令仍使用同一个任务身份。"""
    branch = issue_branch_name(_issue())
    assert format_command(
        "kc worktree path --branch issue-{issue_number}", issue_number=142, issue_branch=branch
    ) == ["kc", "worktree", "path", "--branch", branch]
    assert (
        format_command(
            "kc worktree path --branch {issue_branch}", issue_number=142, issue_branch=branch
        )[-1]
        == branch
    )


def test_cleanup_cannot_delete_another_repository_workspace_even_with_force(tmp_path: Path) -> None:
    """清理真实 Git 工作树时，同号的外仓分支即便已合并且 force 也保留。"""
    repo_path = _repository(tmp_path)
    remote_path = tmp_path / "remote.git"
    init_bare_git_repo(remote_path)
    _git(repo_path, "remote", "add", "origin", str(remote_path))
    _git(repo_path, "push", "origin", "main")
    config = AppConfig(worktree=WorktreeConfig(provision_database=False))
    organization_path = create_or_reuse_worktree(repo_path, _issue(), config, _CliWorktreeRunner())
    personal_path = create_or_reuse_worktree(
        repo_path, _issue("ZataZhang"), config, _CliWorktreeRunner()
    )
    client = FakeGitHubClient()
    client.get_issue = lambda number: replace(_issue(), state="CLOSED")

    outcome = cleanup_managed_worktrees(
        WorktreeCleanupRequest(repo_path=repo_path, dry_run=False, force=True),
        github_client=client,
        process_runner=SubprocessRunner(),
    )

    decisions = {decision.branch: decision for decision in outcome.branches}
    assert decisions[issue_branch_name(_issue())].status is WorktreeCleanupStatus.DELETED
    assert decisions[issue_branch_name(_issue("ZataZhang"))].status is WorktreeCleanupStatus.SKIPPED
    assert not organization_path.exists()
    assert personal_path.is_dir()


@pytest.mark.parametrize(
    "owner,repository", [("CON", "NUL"), ("owner", "repo.lock"), ("owner", "a.b")]
)
def test_workspace_names_are_git_and_windows_compatible(
    owner: str, repository: str, tmp_path: Path
) -> None:
    """合法仓库名中的点号及 Windows 设备名不会生成非法 ref 或目录段。"""
    issue = replace(_issue(owner), url=f"https://github.com/{owner}/{repository}/issues/142")
    identity = issue_workspace_identity(issue)
    assert identity is not None
    repo_path = _repository(tmp_path)
    assert _git(repo_path, "check-ref-format", "--branch", identity.branch) == identity.branch
    assert all(segment.lower() not in {"con", "nul"} for segment in identity.branch.split("/"))
