"""控制台目录选择器的只读目录列举。

浏览器拿不到真实绝对路径（``showDirectoryPicker`` 只给不透明 handle，
``webkitdirectory`` 只有相对路径），所以「选取仓库路径」必须由后端列举目录。
本模块只读取目录名与路径，**不返回任何文件内容**，能力范围与既有的仓库扫描
端点（``discover_iar_repositories``）同级。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    BrowsableDirectoryEntry,
    DirectoryBrowseResult,
    IRepositoryRegistryEditor,
)
from backend.core.use_cases.agent_runner_repository_local import normalize_repository_id
from backend.core.use_cases.issue_pr_status import IAR_REPOSITORY_MARKER_FILENAME


def browse_directories(
    *,
    path: Path,
    editor: IRepositoryRegistryEditor,
) -> DirectoryBrowseResult:
    """列举 ``path`` 下的子目录，供控制台目录选择器使用。

    只返回非隐藏子目录，并为每项标注是否 git 仓库、是否有 IAR 本地配置、
    是否已在 registry 中注册，让用户一眼看出哪些目录能通过「校验并添加」。

    Args:
        path: 要浏览的目录。调用方负责把缺省值解析成用户主目录。
        editor: registry 读取端口，用于标记目录是否已注册。

    Returns:
        当前目录信息与按名称排序的子目录列表。

    Raises:
        ValueError: ``path`` 不存在、不是目录，或无法读取。
    """
    resolved_path = path.expanduser().resolve()
    if not resolved_path.is_dir():
        raise ValueError(f"目录不存在或不是目录：{resolved_path}")

    registered_paths = {
        str(Path(entry.path).expanduser().resolve()) for entry in editor.list_repositories()
    }

    return DirectoryBrowseResult(
        path=str(resolved_path),
        parent=_parent_directory_or_none(resolved_path),
        home=str(Path.home().resolve()),
        suggested_repo_id=normalize_repository_id(resolved_path.name),
        suggested_display_name=resolved_path.name,
        directories=[
            _describe_directory(child, registered_paths)
            for child in _list_visible_subdirectories(resolved_path)
        ],
    )


def _parent_directory_or_none(directory: Path) -> str | None:
    """返回上级目录；已经是文件系统根目录时返回 ``None``。"""
    parent = directory.parent
    return None if parent == directory else str(parent)


def _list_visible_subdirectories(parent: Path) -> list[Path]:
    """按名称排序列出非隐藏子目录。

    单个条目读不动（权限、竞态删除）时跳过它，而不是让整次浏览失败。

    Raises:
        ValueError: ``parent`` 自身无法读取。
    """
    try:
        children = sorted(parent.iterdir(), key=lambda child: child.name.lower())
    except OSError as exc:
        raise ValueError(f"无法读取目录：{parent}") from exc

    directories: list[Path] = []
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir():
                directories.append(child)
        except OSError:
            continue
    return directories


def _describe_directory(
    directory: Path,
    registered_paths: set[str],
) -> BrowsableDirectoryEntry:
    """把一个子目录描述成选择器条目。"""
    return BrowsableDirectoryEntry(
        name=directory.name,
        path=str(directory),
        is_git_repo=(directory / ".git").exists(),
        has_iar_config=(directory / IAR_REPOSITORY_MARKER_FILENAME).is_file(),
        already_registered=str(directory) in registered_paths,
        suggested_repo_id=normalize_repository_id(directory.name),
    )
