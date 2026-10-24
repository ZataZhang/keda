"""仓库旧工作树容器目录的显式迁移。"""

from __future__ import annotations

from pathlib import Path

from backend.engines.agent_runner.worktree_directory_migration import migrate_worktree_directory
from backend.infrastructure.process_runner import SubprocessRunner
from backend.infrastructure.git.worktree import WorktreeManager
from tests.support.agent_runner import init_git_repo


def test_worktree_directory_migration_previews_then_renames(tmp_path: Path) -> None:
    """预演不写盘；apply 将空闲仓库的旧目录改名为默认目录。"""
    repository_root_path = init_git_repo(tmp_path / "repository")
    legacy_worktree_root_path = repository_root_path / ".iar-worktrees"
    legacy_worktree_root_path.mkdir()
    process_runner = SubprocessRunner()

    preview_result = migrate_worktree_directory(
        repository_root_path,
        process_runner=process_runner,
        dry_run=True,
    )

    assert preview_result.outcome == "would_rename"
    assert legacy_worktree_root_path.is_dir()
    assert not (repository_root_path / ".kc-worktrees").exists()

    apply_result = migrate_worktree_directory(
        repository_root_path,
        process_runner=process_runner,
        dry_run=False,
    )

    assert apply_result.outcome == "renamed"
    assert not legacy_worktree_root_path.exists()
    assert (repository_root_path / ".kc-worktrees").is_dir()


def test_worktree_directory_migration_refuses_active_linked_worktrees(
    tmp_path: Path,
) -> None:
    """活动 linked worktree 存在时不改名旧容器目录。"""
    repository_root_path = init_git_repo(tmp_path / "repository")
    legacy_worktree_root_path = repository_root_path / ".iar-worktrees"
    legacy_worktree_root_path.mkdir()
    worktree_manager = WorktreeManager(repository_root_path, SubprocessRunner())
    worktree_manager.create(branch="active-issue", base_branch="HEAD")

    migration_result = migrate_worktree_directory(
        repository_root_path,
        process_runner=SubprocessRunner(),
        dry_run=False,
    )

    assert migration_result.outcome == "conflict"
    assert legacy_worktree_root_path.is_dir()
    assert not (repository_root_path / ".kc-worktrees").exists()
