"""仓库 registry 管理用例（校验 + 受限写回编排）。

registry（``config.toml`` 的 ``[agent_runner.repositories.*]``）仍是
项目接入的唯一事实来源；本用例只是它的受控编辑器：

- ``repo_id`` 必须满足 ``^[a-z0-9][a-z0-9-]*$``。
- 路径必须存在且是 git 仓库（含 ``.git`` 目录或文件，worktree 亦可）。
- 同一路径只允许被一个 ``repo_id`` 占用；重复登记必须改名或换路径。
- 新增条目默认 enabled；启停只翻转 ``enabled`` 字段。
- 移除只删 registry 条目，永不删除本地仓库目录（由人工在外部删除）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    IRepositoryRegistryEditor,
    RegistryRepositoryEntry,
)

_REPO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class RegistryValidationError(ValueError):
    """registry 操作的校验失败。"""


def _find_entry_by_path(
    entries: Sequence[RegistryRepositoryEntry], resolved_path: Path
) -> RegistryRepositoryEntry | None:
    """按已解析的绝对路径查找 registry 条目。"""
    for entry in entries:
        if Path(entry.path).expanduser().resolve() == resolved_path:
            return entry
    return None


def list_registry_repositories(
    editor: IRepositoryRegistryEditor,
) -> list[RegistryRepositoryEntry]:
    """列出 registry 的全部条目。"""
    return editor.list_repositories()


def add_registry_repository(
    *,
    editor: IRepositoryRegistryEditor,
    repo_id: str,
    path: str,
    display_name: str | None,
) -> RegistryRepositoryEntry:
    """校验并新增一个 registry 条目。

    Args:
        editor: registry 写回端口。
        repo_id: 新条目 ID。
        path: 仓库本地路径。
        display_name: 可选显示名。

    Returns:
        RegistryRepositoryEntry: 新增后的条目视图。

    Raises:
        RegistryValidationError: repo_id 非法、路径不存在、不是 git
            仓库、ID 已存在或路径已被其他 repo_id 注册。
    """
    if not _REPO_ID_PATTERN.match(repo_id):
        raise RegistryValidationError(f"repo_id must match ^[a-z0-9][a-z0-9-]*$ (got '{repo_id}').")
    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.exists():
        raise RegistryValidationError(f"Path '{resolved_path}' does not exist.")
    if not (resolved_path / ".git").exists():
        raise RegistryValidationError(
            f"Path '{resolved_path}' is not a git repository (no .git entry)."
        )
    existing_entries = editor.list_repositories()
    existing_ids = {entry.repo_id for entry in existing_entries}
    if repo_id in existing_ids:
        raise RegistryValidationError(f"Repository '{repo_id}' already exists in the registry.")
    conflicting_entry = _find_entry_by_path(existing_entries, resolved_path)
    if conflicting_entry is not None:
        raise RegistryValidationError(
            f"Path '{resolved_path}' is already registered as "
            f"'{conflicting_entry.repo_id}'; pick another repo_id or path."
        )
    try:
        editor.add_repository(repo_id=repo_id, path=str(resolved_path), display_name=display_name)
    except ValueError as exc:
        raise RegistryValidationError(str(exc)) from exc
    return RegistryRepositoryEntry(
        repo_id=repo_id,
        path=str(resolved_path),
        enabled=True,
        display_name=display_name,
        path_exists=True,
    )


def set_registry_repository_enabled(
    *,
    editor: IRepositoryRegistryEditor,
    repo_id: str,
    enabled: bool,
) -> None:
    """启用或停用一个 registry 条目。

    Raises:
        RegistryValidationError: repo_id 不存在。
    """
    try:
        editor.set_enabled(repo_id, enabled=enabled)
    except KeyError as exc:
        raise RegistryValidationError(str(exc)) from exc


def remove_registry_repository(
    *,
    editor: IRepositoryRegistryEditor,
    repo_id: str,
) -> RegistryRepositoryEntry:
    """从 registry 移除一个条目。

    只删除 ``config.toml`` 中的注册条目，绝不删除本地仓库目录：托管克隆
    与实际开发仓库都由人工在外部自行处理。停止该仓库的常驻进程属于编排，
    由调用方在移除前完成。

    Args:
        editor: registry 写回端口。
        repo_id: 待移除的条目 ID。

    Returns:
        被移除的条目视图（调用方可据此报告路径）。

    Raises:
        RegistryValidationError: repo_id 不存在。
    """
    entries = {entry.repo_id: entry for entry in editor.list_repositories()}
    entry = entries.get(repo_id)
    if entry is None:
        raise RegistryValidationError(f"Repository '{repo_id}' is not registered.")
    try:
        editor.remove_repository(repo_id)
    except KeyError as exc:
        raise RegistryValidationError(str(exc)) from exc
    return entry
