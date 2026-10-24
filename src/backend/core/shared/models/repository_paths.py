"""仓库内 KedaCode 状态与工作树目录的路径约定。"""

from __future__ import annotations

from pathlib import Path

DEFAULT_WORKTREE_DIR_NAME = ".kc-worktrees"
LEGACY_WORKTREE_DIR_NAME = ".iar-worktrees"

DEFAULT_REPOSITORY_STATE_DIR_NAME = ".kedacode"
LEGACY_REPOSITORY_STATE_DIR_NAME = ".iar"


def resolve_managed_worktree_root(repository_root_path: Path) -> Path:
    """解析工作树容器目录，已有旧目录时继续使用它。

    Args:
        repository_root_path: 已解析的仓库根目录。

    Returns:
        新工作树容器路径，或已经存在的旧容器路径。
    """
    legacy_worktree_root_path = repository_root_path / LEGACY_WORKTREE_DIR_NAME
    if legacy_worktree_root_path.is_dir():
        return legacy_worktree_root_path
    return repository_root_path / DEFAULT_WORKTREE_DIR_NAME


def resolve_repository_state_path(
    repository_root_path: Path,
    current_relative_path: Path,
    legacy_relative_path: Path,
) -> Path:
    """解析仓库状态文件路径，旧路径已存在时继续复用旧状态。

    Args:
        repository_root_path: 仓库或 worktree 根目录。
        current_relative_path: 新版本默认的相对路径。
        legacy_relative_path: 上一版使用的相对路径。

    Returns:
        旧路径已存在时返回旧路径，否则返回新路径。
    """
    legacy_state_path = repository_root_path / legacy_relative_path
    if legacy_state_path.exists():
        return legacy_state_path
    return repository_root_path / current_relative_path


__all__ = [
    "DEFAULT_REPOSITORY_STATE_DIR_NAME",
    "DEFAULT_WORKTREE_DIR_NAME",
    "LEGACY_REPOSITORY_STATE_DIR_NAME",
    "LEGACY_WORKTREE_DIR_NAME",
    "resolve_managed_worktree_root",
    "resolve_repository_state_path",
]
