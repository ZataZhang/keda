"""显式迁移仓库内的旧工作树容器目录。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.repository_paths import (
    DEFAULT_WORKTREE_DIR_NAME,
    LEGACY_WORKTREE_DIR_NAME,
)


@dataclass(frozen=True)
class WorktreeDirectoryMigrationResult:
    """工作树目录迁移的预演或执行结果。"""

    outcome: str
    source_path: Path
    target_path: Path
    message: str


def migrate_worktree_directory(
    repository_root_path: Path,
    *,
    process_runner: IProcessRunner,
    dry_run: bool = True,
) -> WorktreeDirectoryMigrationResult:
    """无活动 linked worktree 时显式改名旧工作树容器目录。

    Args:
        repository_root_path: 已解析的仓库根目录。
        process_runner: 执行只读 Git worktree 查询的端口。
        dry_run: 为真时只报告计划，不写磁盘。

    Returns:
        迁移计划或执行结果。无法证明安全时返回 ``conflict`` / ``failed``。
    """
    repository_root_path = repository_root_path.resolve()
    source_path = repository_root_path / LEGACY_WORKTREE_DIR_NAME
    target_path = repository_root_path / DEFAULT_WORKTREE_DIR_NAME
    if not source_path.exists():
        return WorktreeDirectoryMigrationResult(
            outcome="absent",
            source_path=source_path,
            target_path=target_path,
            message=f"No legacy worktree directory at {source_path}.",
        )
    if target_path.exists():
        return WorktreeDirectoryMigrationResult(
            outcome="conflict",
            source_path=source_path,
            target_path=target_path,
            message=(
                f"Both {source_path.name} and {target_path.name} exist; resolve the duplicate "
                "directories manually. Nothing was changed."
            ),
        )

    worktree_result = process_runner.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repository_root_path,
        check=False,
    )
    if worktree_result.return_code != 0:
        return WorktreeDirectoryMigrationResult(
            outcome="failed",
            source_path=source_path,
            target_path=target_path,
            message="Cannot verify active Git worktrees; nothing was changed.",
        )

    active_worktree_paths = _linked_worktree_paths(worktree_result.stdout)
    if any(
        worktree_path != repository_root_path and worktree_path.exists()
        for worktree_path in active_worktree_paths
    ):
        return WorktreeDirectoryMigrationResult(
            outcome="conflict",
            source_path=source_path,
            target_path=target_path,
            message="Active Git worktrees exist; stop and remove them before renaming the directory.",
        )
    if dry_run:
        return WorktreeDirectoryMigrationResult(
            outcome="would_rename",
            source_path=source_path,
            target_path=target_path,
            message=f"Would rename {source_path.name} to {target_path.name}.",
        )
    try:
        source_path.rename(target_path)
    except OSError as exc:
        return WorktreeDirectoryMigrationResult(
            outcome="failed",
            source_path=source_path,
            target_path=target_path,
            message=f"Could not rename {source_path.name}: {exc}",
        )
    return WorktreeDirectoryMigrationResult(
        outcome="renamed",
        source_path=source_path,
        target_path=target_path,
        message=f"Renamed {source_path.name} to {target_path.name}.",
    )


def _linked_worktree_paths(porcelain_text: str) -> tuple[Path, ...]:
    """从 porcelain 输出提取已登记的 worktree 根路径。"""
    return tuple(
        Path(line.removeprefix("worktree ")).resolve()
        for line in porcelain_text.splitlines()
        if line.startswith("worktree ")
    )


__all__ = ["WorktreeDirectoryMigrationResult", "migrate_worktree_directory"]
