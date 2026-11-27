"""仓库内 KedaCode 状态与工作树目录的路径约定。"""

from __future__ import annotations

from pathlib import Path

DEFAULT_WORKTREE_DIR_NAME = ".kc-worktrees"
LEGACY_WORKTREE_DIR_NAME = ".iar-worktrees"

DEFAULT_REPOSITORY_STATE_DIR_NAME = ".kedacode"
LEGACY_REPOSITORY_STATE_DIR_NAME = ".iar"
AGENT_SESSION_RECORD_DIRS = (
    Path(DEFAULT_REPOSITORY_STATE_DIR_NAME) / "agent-runner" / "sessions",
    Path(LEGACY_REPOSITORY_STATE_DIR_NAME) / "agent-runner" / "sessions",
)


def resolve_managed_worktree_root(repository_root_path: Path) -> Path:
    """解析 KedaCode 新建工作树的容器目录。

    Args:
        repository_root_path: 已解析的仓库根目录。

    Returns:
        默认工作树容器路径。
    """
    return repository_root_path / DEFAULT_WORKTREE_DIR_NAME


def resolve_git_pointer_directory(pointer_path: Path, *, prefix: str = "") -> Path | None:
    """读取 Git 目录指针，并相对指针文件所在目录解析路径。

    Args:
        pointer_path: 工作树的 .git 文件或 Git 元数据中的 commondir 文件。
        prefix: .git 文件使用 gitdir: 前缀；commondir 文件省略。

    Returns:
        指向的绝对目录；要求的前缀不匹配时返回 None。

    Raises:
        OSError: 指针不可读；由调用方保留原有错误处理策略。
    """
    pointer_text = pointer_path.read_text(encoding="utf-8").strip()
    if not pointer_text.startswith(prefix):
        return None
    directory_text = pointer_text.removeprefix(prefix).strip()
    return (pointer_path.parent / directory_text).resolve()


def resolve_repository_state_path(
    repository_root_path: Path,
    current_relative_path: Path,
    legacy_relative_path: Path,
) -> Path:
    """解析仓库状态文件路径，旧状态根存在时继续复用其目录。

    Args:
        repository_root_path: 仓库或 worktree 根目录。
        current_relative_path: 新版本默认的相对路径。
        legacy_relative_path: 上一版使用的相对路径。

    Returns:
        旧状态根或完整旧路径存在时返回旧路径，否则返回新路径。
    """
    legacy_state_path = repository_root_path / legacy_relative_path
    legacy_state_root_path = repository_root_path / LEGACY_REPOSITORY_STATE_DIR_NAME
    if legacy_state_root_path.is_dir() or legacy_state_path.exists():
        return legacy_state_path
    return repository_root_path / current_relative_path


__all__ = [
    "AGENT_SESSION_RECORD_DIRS",
    "DEFAULT_REPOSITORY_STATE_DIR_NAME",
    "DEFAULT_WORKTREE_DIR_NAME",
    "LEGACY_REPOSITORY_STATE_DIR_NAME",
    "LEGACY_WORKTREE_DIR_NAME",
    "resolve_git_pointer_directory",
    "resolve_managed_worktree_root",
    "resolve_repository_state_path",
]
