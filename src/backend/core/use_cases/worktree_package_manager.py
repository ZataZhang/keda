"""Worktree 前端项目的包管理器识别与安装命令规划。

识别同时结合 ``package.json`` 的 ``packageManager`` 字段、workspace 根配置与锁文件。
证据互相矛盾时保留全部线索并交给 Agent 判断，不静默选择可能错误的安装工具。
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
from pathlib import Path

from backend.core.use_cases.worktree_dependency_context import (
    REASON_MANAGER_CONFLICT,
    REASON_NO_LOCKFILE,
    REASON_WORKSPACE_COVERED,
)

PACKAGE_MANIFEST_FILE_NAME = "package.json"
PNPM_WORKSPACE_FILE_NAME = "pnpm-workspace.yaml"

_LOCKFILE_PACKAGE_MANAGERS = {
    "pnpm-lock.yaml": "pnpm",
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "bun.lock": "bun",
    "bun.lockb": "bun",
}

__all__ = [
    "PACKAGE_MANIFEST_FILE_NAME",
    "PNPM_WORKSPACE_FILE_NAME",
    "PackageManagerPlan",
    "plan_package_manager",
]


@dataclasses.dataclass(frozen=True)
class PackageManagerPlan:
    """包管理器识别结果与其证据。

    Attributes:
        package_manager: 用于安装命令的包管理器名；无法安全决定时为 ``None``。
        reason: 未选定或需要回退时的原因码（``REASON_*``）；已选定为空串。
        evidence: 选择依据证据行（packageManager 字段、workspace 根、锁文件）。
        is_workspace_root: 该目录自身是否声明了 workspace（其安装覆盖成员）。
        is_workspace_member: 该目录是否被其祖先 workspace 覆盖（无需重复安装）。
    """

    package_manager: str | None
    reason: str = ""
    evidence: tuple[str, ...] = ()
    is_workspace_root: bool = False
    is_workspace_member: bool = False


def _read_package_manifest(project_dir_path: Path) -> tuple[dict[str, object] | None, str]:
    """读取 ``package.json``。

    Returns:
        ``(manifest, error_note)``：解析成功时 ``error_note`` 为空串；文件缺失或
        不可解析时 ``manifest`` 为 ``None``，note 直接进入诊断证据。
    """
    manifest_path = project_dir_path / PACKAGE_MANIFEST_FILE_NAME
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except OSError as read_error:
        return None, f"{PACKAGE_MANIFEST_FILE_NAME} could not be read: {read_error}"
    try:
        manifest_object = json.loads(manifest_text)
    except ValueError as parse_error:
        return None, f"{PACKAGE_MANIFEST_FILE_NAME} is not valid JSON: {parse_error}"
    if not isinstance(manifest_object, dict):
        return None, f"{PACKAGE_MANIFEST_FILE_NAME} is not a JSON object"
    return manifest_object, ""


def _declared_package_manager(manifest: dict[str, object] | None) -> str | None:
    """取 ``packageManager`` 字段声明的包管理器名（去掉版本与 hash）。"""
    if not manifest:
        return None
    declared_value = manifest.get("packageManager")
    if not isinstance(declared_value, str) or not declared_value.strip():
        return None
    # 形状为 ``pnpm@9.12.3+sha256.xxx``：``@`` 之前才是工具名。
    return declared_value.split("@", 1)[0].strip() or None


def _workspace_patterns(manifest: dict[str, object] | None) -> tuple[str, ...]:
    """取 ``package.json`` 的 ``workspaces`` 声明（数组或 ``{packages: []}`` 两种形状）。"""
    if not manifest:
        return ()
    workspaces_value = manifest.get("workspaces")
    if isinstance(workspaces_value, dict):
        workspaces_value = workspaces_value.get("packages")
    if isinstance(workspaces_value, list):
        return tuple(str(pattern) for pattern in workspaces_value if isinstance(pattern, str))
    return ()


def _pnpm_workspace_patterns(workspace_file_path: Path) -> tuple[str, ...]:
    """从 ``pnpm-workspace.yaml`` 取 ``packages:`` 列表（无 YAML 依赖的行扫描）。

    与 ``scripts/shared/worktree/create.sh`` 内嵌的解析同口径：只认 ``packages:``
    之后的 ``- <pattern>`` 行，遇到下一个顶层键即停止。
    """
    try:
        workspace_text = workspace_file_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    patterns: list[str] = []
    in_packages_section = False
    for raw_line in workspace_text.splitlines():
        stripped_line = raw_line.strip()
        if stripped_line.startswith("packages:"):
            in_packages_section = True
            continue
        if not in_packages_section or not stripped_line or stripped_line.startswith("#"):
            continue
        if stripped_line.startswith("- "):
            patterns.append(stripped_line[2:].strip().strip("\"'"))
        else:
            in_packages_section = False
    return tuple(patterns)


def _matches_workspace_pattern(relative_posix_path: str, pattern: str) -> bool:
    """workspace 成员匹配：支持精确路径与简单 glob，容忍 trailing slash。"""
    normalized_pattern = pattern.strip().rstrip("/")
    if not normalized_pattern:
        return False
    candidate_path = relative_posix_path.rstrip("/")
    if fnmatch.fnmatch(candidate_path, normalized_pattern):
        return True
    return fnmatch.fnmatch(f"{candidate_path}/", normalized_pattern)


def _workspace_member_patterns(parent_dir_path: Path) -> tuple[str, ...]:
    """``parent_dir_path`` 作为 workspace 根时声明的成员 pattern 集合。"""
    pnpm_patterns = _pnpm_workspace_patterns(parent_dir_path / PNPM_WORKSPACE_FILE_NAME)
    if pnpm_patterns:
        return pnpm_patterns
    parent_manifest, _ = _read_package_manifest(parent_dir_path)
    return _workspace_patterns(parent_manifest)


def _find_covering_workspace(
    project_dir_path: Path,
    worktree_root_path: Path,
) -> tuple[str, str] | None:
    """返回覆盖该目录的最近 workspace 根（相对 worktree 路径）与命中的成员 pattern。

    只自近及远遍历 ``project_dir_path`` 在 worktree 内的祖先，最深到 worktree 根，
    因此不会越出 worktree 去读主机上层的配置。
    """
    try:
        relative_project_path = project_dir_path.relative_to(worktree_root_path)
    except ValueError:
        return None
    project_path_parts = relative_project_path.parts
    for kept_ancestor_part_count in range(len(project_path_parts) - 1, -1, -1):
        ancestor_parts = project_path_parts[:kept_ancestor_part_count]
        parent_dir_path = worktree_root_path.joinpath(*ancestor_parts)
        member_relative_posix_path = "/".join(project_path_parts[kept_ancestor_part_count:])
        matched_pattern = next(
            (
                member_pattern
                for member_pattern in _workspace_member_patterns(parent_dir_path)
                if _matches_workspace_pattern(member_relative_posix_path, member_pattern)
            ),
            None,
        )
        if matched_pattern is None:
            continue
        covering_root = parent_dir_path.relative_to(worktree_root_path).as_posix() or "."
        return covering_root, matched_pattern
    return None


def _present_lockfile_names(project_dir_path: Path) -> tuple[str, ...]:
    """目录内存在的锁文件名（按 :data:`_LOCKFILE_PACKAGE_MANAGERS` 顺序）。"""
    return tuple(
        lockfile_name
        for lockfile_name in _LOCKFILE_PACKAGE_MANAGERS
        if (project_dir_path / lockfile_name).is_file()
    )


def _workspace_root_manager_context(
    worktree_root_path: Path,
    covering_root: str,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """读取 workspace 根的包管理器证据与自身冲突状态。"""
    workspace_root_path = (
        worktree_root_path if covering_root == "." else worktree_root_path / covering_root
    )
    workspace_root_manifest, manifest_error_note = _read_package_manifest(workspace_root_path)
    declared_root_manager = _declared_package_manager(workspace_root_manifest)
    root_lockfile_names = _present_lockfile_names(workspace_root_path)
    root_lockfile_managers = tuple(
        dict.fromkeys(_LOCKFILE_PACKAGE_MANAGERS[name] for name in root_lockfile_names)
    )
    manager_sources = tuple(
        dict.fromkeys(
            manager
            for manager in (declared_root_manager, *root_lockfile_managers)
            if manager is not None
        )
    )
    evidence_lines = [
        (
            f"workspace root '{covering_root}' package.json packageManager declares "
            f"'{declared_root_manager}'"
            if declared_root_manager
            else (
                f"workspace root '{covering_root}' package.json: {manifest_error_note}"
                if manifest_error_note
                else f"workspace root '{covering_root}' packageManager: absent"
            )
        ),
        f"workspace root '{covering_root}' lockfile(s): "
        f"{', '.join(root_lockfile_names) if root_lockfile_names else 'none'}",
    ]
    has_conflict = len(manager_sources) > 1
    return manager_sources, tuple(evidence_lines), has_conflict


def plan_package_manager(project_dir_path: Path, worktree_root_path: Path) -> PackageManagerPlan:
    """结合 packageManager 字段、workspace 根配置与锁文件规划包管理器。

    证据冲突时不静默选定一个不可恢复的结果：``package_manager`` 留空并记
    ``manager_conflict``，全部证据交给 Agent。

    Args:
        project_dir_path: 含 ``package.json`` 的前端项目目录。
        worktree_root_path: worktree 根目录（向上寻找 workspace 配置的边界）。

    Returns:
        识别结果与逐条证据。
    """
    manifest_object, manifest_error_note = _read_package_manifest(project_dir_path)
    evidence_lines = [
        f"manifest: {manifest_error_note}" if manifest_error_note else "manifest: parsed"
    ]
    declared_manager = _declared_package_manager(manifest_object)
    if declared_manager:
        evidence_lines.append(f"package.json packageManager declares '{declared_manager}'")
    lockfile_names = _present_lockfile_names(project_dir_path)
    evidence_lines.append(
        "lockfile(s): " + (", ".join(lockfile_names) if lockfile_names else "none")
    )

    covering_workspace = _find_covering_workspace(project_dir_path, worktree_root_path)
    is_workspace_member = covering_workspace is not None
    workspace_manager_sources: tuple[str, ...] = ()
    workspace_manager_conflict = False
    if covering_workspace is not None:
        covering_root, covering_pattern = covering_workspace
        evidence_lines.append(
            f"workspace member covered by root '{covering_root}' "
            f"pattern '{covering_pattern}' install"
        )
        (
            workspace_manager_sources,
            workspace_manager_evidence,
            workspace_manager_conflict,
        ) = _workspace_root_manager_context(worktree_root_path, covering_root)
        evidence_lines.extend(workspace_manager_evidence)
    is_workspace_root = (project_dir_path / PNPM_WORKSPACE_FILE_NAME).is_file() or bool(
        _workspace_patterns(manifest_object)
    )
    if is_workspace_root:
        evidence_lines.append("this directory is a workspace root (its install covers members)")

    lockfile_managers = tuple(
        dict.fromkeys(_LOCKFILE_PACKAGE_MANAGERS[name] for name in lockfile_names)
    )
    manager_sources = tuple(
        dict.fromkeys(
            manager
            for manager in (
                declared_manager,
                *lockfile_managers,
                *workspace_manager_sources,
            )
            if manager is not None
        )
    )
    conflict_note = ""
    if len(manager_sources) > 1 or workspace_manager_conflict:
        conflict_note = (
            "conflict: package-manager evidence across the project and workspace root "
            f"disagrees ({', '.join(manager_sources)})"
        )
    if conflict_note:
        evidence_lines.append(conflict_note)
        return PackageManagerPlan(
            package_manager=None,
            reason=REASON_MANAGER_CONFLICT,
            evidence=tuple(evidence_lines),
            is_workspace_root=is_workspace_root,
            is_workspace_member=is_workspace_member,
        )

    if is_workspace_member:
        return PackageManagerPlan(
            package_manager=manager_sources[0] if manager_sources else None,
            reason=REASON_WORKSPACE_COVERED,
            evidence=tuple(evidence_lines),
            is_workspace_root=is_workspace_root,
            is_workspace_member=True,
        )

    if not lockfile_managers:
        if declared_manager:
            evidence_lines.append(
                f"no lockfile to install from; '{declared_manager}' is declared but unpinned"
            )
        return PackageManagerPlan(
            package_manager=None,
            reason=REASON_NO_LOCKFILE,
            evidence=tuple(evidence_lines),
            is_workspace_root=is_workspace_root,
            is_workspace_member=is_workspace_member,
        )

    selected_manager = lockfile_managers[0]
    if selected_manager == "pnpm" and not is_workspace_root:
        evidence_lines.append(
            "pnpm install runs with --ignore-workspace: this directory is not the "
            "workspace root that owns the lockfile"
        )
    return PackageManagerPlan(
        package_manager=selected_manager,
        reason="",
        evidence=tuple(evidence_lines),
        is_workspace_root=is_workspace_root,
        is_workspace_member=is_workspace_member,
    )


def _build_install_command(install_plan: PackageManagerPlan) -> list[str]:
    """按识别结果构造安装命令 argv。

    与 ``scripts/shared/worktree/create.sh`` 的
    ``install_frontend_dependencies_in_current_directory`` 保持同一策略，使 Python 与
    shell 两条 worktree 路径行为一致。

    Args:
        install_plan: 包管理器识别结果。

    Returns:
        安装命令 argv；未选定包管理器时返回空列表。
    """
    if install_plan.package_manager == "pnpm":
        command = ["pnpm", "install", "--ignore-scripts"]
        if not install_plan.is_workspace_root:
            command.append("--ignore-workspace")
        return command
    if install_plan.package_manager == "npm":
        return ["npm", "ci", "--ignore-scripts"]
    if install_plan.package_manager == "yarn":
        return ["yarn", "install", "--ignore-scripts"]
    if install_plan.package_manager == "bun":
        return ["bun", "install", "--ignore-scripts"]
    return []
