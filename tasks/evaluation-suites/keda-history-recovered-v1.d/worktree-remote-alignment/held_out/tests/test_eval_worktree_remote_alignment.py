"""真实本地 Git 远端验证工作树准备入口的分支安全行为。"""

from pathlib import Path
import subprocess

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    GitConfig,
    IssueSummary,
    WorktreeConfig,
)
from backend.core.use_cases.run_agent_once import create_or_reuse_worktree
from backend.infrastructure.process_runner import SubprocessRunner

BRANCH = "issue-42"
REMOTE = "team"


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    return completed.stdout.strip()


def _set_author(repo: Path) -> None:
    _git(repo, "config", "user.name", "Eval Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "core.hooksPath", "/dev/null")


def _commit(repo: Path, filename: str) -> str:
    (repo / filename).write_text(filename, encoding="utf-8")
    _git(repo, "add", "--", filename)
    _git(repo, "commit", "-m", filename)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def two_clones(tmp_path: Path) -> tuple[Path, Path]:
    """本机 bare 远端和两个分支工作区，共享同一个公开的初始提交。"""
    upstream = tmp_path / "remote.git"
    local = tmp_path / "local"
    partner = tmp_path / "partner"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "init", "-b", BRANCH, str(local))
    _set_author(local)
    _commit(local, "initial.txt")
    _git(local, "remote", "add", REMOTE, str(upstream))
    _git(local, "push", "-u", REMOTE, BRANCH)
    _git(tmp_path, "clone", "--branch", BRANCH, str(upstream), str(partner))
    _set_author(partner)
    return local, partner


def _prepare_worktree(local: Path) -> Path:
    return create_or_reuse_worktree(
        local,
        IssueSummary(number=42, title="Prepare worktree", url="local", body="", labels=()),
        AppConfig(
            git=GitConfig(remote=REMOTE),
            worktree=WorktreeConfig(
                create_command="git rev-parse --show-toplevel",
                path_command="git rev-parse --show-toplevel",
            ),
        ),
        SubprocessRunner(),
    )


def _advance_remote(partner: Path) -> str:
    remote_head = _commit(partner, "new-on-remote.txt")
    _git(partner, "push", "origin", BRANCH)
    return remote_head


def test_clean_worktree_fast_forwards_using_configured_remote(two_clones) -> None:
    """非 origin 的已配置远端领先时，安全快进并用真实内容确认。"""
    local, partner = two_clones
    remote_head = _advance_remote(partner)
    assert _prepare_worktree(local) == local.resolve()
    assert _git(local, "rev-parse", "HEAD") == remote_head
    assert (local / "new-on-remote.txt").is_file()


def test_dirty_worktree_refuses_fast_forward_without_losing_edit(two_clones) -> None:
    """工作树有修改时出错，HEAD 与未提交内容都必须保留。"""
    local, partner = two_clones
    head_before = _git(local, "rev-parse", "HEAD")
    _advance_remote(partner)
    modified = local / "initial.txt"
    modified.write_text("uncommitted edit", encoding="utf-8")
    with pytest.raises(RuntimeError):
        _prepare_worktree(local)
    assert _git(local, "rev-parse", "HEAD") == head_before
    assert modified.read_text(encoding="utf-8") == "uncommitted edit"


def test_diverged_worktree_reports_problem_and_preserves_both_tips(two_clones) -> None:
    """分叉情形不推送或重写本地提交。"""
    local, partner = two_clones
    local_head = _commit(local, "unpushed.txt")
    remote_head = _advance_remote(partner)
    with pytest.raises(RuntimeError):
        _prepare_worktree(local)
    assert _git(local, "rev-parse", "HEAD") == local_head
    remote_ref = _git(local, "ls-remote", "--heads", REMOTE, BRANCH)
    assert remote_ref.split()[0] == remote_head


def test_local_ahead_worktree_keeps_unpublished_commit(two_clones) -> None:
    """远端落后时，准备工作树保留本地未发布提交。"""
    local, _ = two_clones
    local_head = _commit(local, "unpushed.txt")
    assert _prepare_worktree(local) == local.resolve()
    assert _git(local, "rev-parse", "HEAD") == local_head
