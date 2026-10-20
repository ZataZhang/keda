"""Worktree 前端依赖的**可恢复**准备与诊断（Issue #290）。

``git worktree add`` 只落地被跟踪的文件，gitignore 掉的 ``node_modules``
永远不会出现在新 worktree 里。本模块负责补齐前端依赖，但**绝不**把环境缺口
变成 Issue 的致命错误：

- 准备只做安全可行的动作：能复用就复用（worktree 内已有依赖、主检出的依赖），
  装得动就按锁文件装；可执行文件缺失、安装失败、锁文件互相冲突都只记录诊断。
- 任何失败都不向上抛异常。缺失的工具、选择依据、尝试过的命令、失败原因都写成
  :class:`~backend.core.use_cases.worktree_dependency_context.FrontendProjectRecord`，
  由调用方落盘并作为结构化上下文交给 Agent，由 Agent 自主决定安全的仓库级安装
  或替代方式。
- 需要提权、改主机全局环境或使用 runner 没有的凭据的动作**不代做**，只在记录里
  标注 ``requires_user_action``，供上层以可恢复状态呈现给人。

包管理器识别（:func:`plan_package_manager`）结合三类证据，而不是「看见某个锁文件
就静默选定」：

1. ``package.json`` 的 ``packageManager`` 字段（corepack 钉定的权威声明）；
2. workspace 根配置（祖先目录的 ``pnpm-workspace.yaml``，或 ``package.json`` 的
   ``workspaces``）——成员目录由根安装覆盖，不在成员目录里重复安装；
3. 目录内的锁文件。

三类证据互相矛盾时不作出不可恢复的选择：``package_manager`` 留空、原因记
``manager_conflict``，全部证据交给 Agent 判断，并优先尝试复用已有依赖。

其他设计决定：

- 安装命令与 ``scripts/shared/worktree/create.sh`` 的策略保持一致，含 pnpm 的
  ``--ignore-workspace``：非 workspace 根目录执行 ``pnpm install`` 会去装 workspace
  成员而不是当前目录，且退出码仍为 0——那是「安装成功但依赖缺失」的假绿灯。
- 安装退出码为 0 但目录里没出现 ``node_modules`` 时不判成功，记
  ``install_produced_no_dependencies`` 并继续走复用回退。
- 复用主检出的 ``node_modules`` 前先比对锁文件内容：不一致说明分支依赖已漂移，
  链接过来会让后续命令在错误环境里运行并伪装成就绪，宁可不复用。
- worktree 内已存在的 ``node_modules``（目录或符号链接）永不覆盖——对已有
  worktree 重跑只补空缺。
- 符号链接通过 ``info/exclude`` 排除出 git（见
  :func:`exclude_frontend_node_modules_from_git`），避免 agent 的 ``git add -A``
  提交机器本地绝对链接。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.use_cases.worktree_dependency_context import (
    OUTCOME_INSTALLED,
    OUTCOME_NOT_PREPARED,
    OUTCOME_PRESENT,
    OUTCOME_REUSED,
    OUTCOME_SKIPPED,
    REASON_INSTALL_FAILED,
    REASON_INSTALL_NO_OUTPUT,
    REASON_LAUNCH_FAILED,
    REASON_LINK_FAILED,
    REASON_MANAGER_CONFLICT,
    REASON_NO_ACTION_NEEDED,
    REASON_PREPARATION_CRASHED,
    REASON_REUSABLE_ABSENT,
    REASON_REUSABLE_STALE,
    REASON_TOOL_MISSING,
    REASON_WORKSPACE_COVERED,
    USER_ACTION_HOST_TOOLCHAIN,
    DependencyPreparationReport,
    FrontendProjectRecord,
    classify_user_action_from_failure_text,
    format_command_text,
    sanitize_detail_text,
)
from backend.core.use_cases.worktree_package_manager import (
    PACKAGE_MANIFEST_FILE_NAME,
    PNPM_WORKSPACE_FILE_NAME,
    PackageManagerPlan,
    _LOCKFILE_PACKAGE_MANAGERS,
    _build_install_command,
    plan_package_manager,
)

_logger = logging.getLogger(__name__)

NODE_MODULES_DIR_NAME = "node_modules"

# 扫描时永不下钻的目录：VCS 内部、内嵌 worktree 容器（避免一个 worktree 的树泄漏进
# 另一个）、语言/构建缓存，以及既有依赖与产物目录。
FRONTEND_SCAN_PRUNED_DIR_NAMES = frozenset(
    {
        ".git",
        ".iar-worktrees",
        ".venv",
        "venv",
        ".uv-cache",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "site",
    }
)

__all__ = [
    "FRONTEND_SCAN_PRUNED_DIR_NAMES",
    "NODE_MODULES_DIR_NAME",
    "PACKAGE_MANIFEST_FILE_NAME",
    "PNPM_WORKSPACE_FILE_NAME",
    "PackageManagerPlan",
    "ensure_frontend_node_modules",
    "exclude_frontend_node_modules_from_git",
    "link_frontend_node_modules",
    "plan_package_manager",
]


@dataclasses.dataclass(frozen=True)
class _ProjectPreparationRequest:
    """单个前端项目的准备输入（参数成组收敛，避免调用链层层展开）。"""

    repo_root_path: Path
    worktree_path: Path
    project_dir_path: Path
    relative_project_path: Path
    process_runner: IProcessRunner


@dataclasses.dataclass(frozen=True)
class _ReuseTarget:
    """「复用主检出依赖」所需的路径三元组。"""

    repo_root_path: Path
    project_dir_path: Path
    relative_project_path: Path


@dataclasses.dataclass(frozen=True)
class _InstallAttempt:
    """一次安装尝试的结论（不抛异常，全部事实进记录）。"""

    succeeded: bool
    reason: str = ""
    requires_user_action: str | None = None
    attempted_commands: tuple[str, ...] = ()
    exit_code: int | None = None
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class _ReuseAttempt:
    """一次「复用主检出依赖」的结论。"""

    succeeded: bool
    reason: str = ""
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class _ProjectOutcome:
    """单个项目最终落进诊断记录的处置字段（结论 + 原因 + 人类决定需求）。"""

    outcome: str
    reason: str
    requires_user_action: str | None = None
    attempted_commands: tuple[str, ...] = ()
    exit_code: int | None = None
    detail: str = ""


def _try_install_frontend_dependencies(
    project_dir_path: Path,
    install_plan: PackageManagerPlan,
    process_runner: IProcessRunner,
) -> _InstallAttempt:
    """在 worktree 项目目录里尝试一次锁文件驱动的安装。

    本函数**不抛异常**：可执行文件缺失、进程启动失败、非零退出、退出 0 却没有产出
    依赖，全部只写进返回值（Issue #290 的核心：环境缺口不得中断 Agent 启动）。

    Args:
        project_dir_path: worktree 内的前端项目目录。
        install_plan: 包管理器识别结果。
        process_runner: 执行安装命令的 runner。

    Returns:
        安装尝试结论。
    """
    if install_plan.package_manager is None:
        return _InstallAttempt(
            succeeded=False,
            reason=install_plan.reason,
            detail=sanitize_detail_text("no install attempted: " + install_plan.reason),
        )
    install_command = _build_install_command(install_plan)
    attempted_commands = (format_command_text(install_command),)
    if shutil.which(install_command[0]) is None:
        return _InstallAttempt(
            succeeded=False,
            reason=REASON_TOOL_MISSING,
            requires_user_action=USER_ACTION_HOST_TOOLCHAIN,
            attempted_commands=attempted_commands,
            detail=(
                f"'{install_command[0]}' is not on PATH; Keda does not install host "
                "toolchains, so the command was never run"
            ),
        )
    _logger.info(
        "Installing frontend dependencies in %s: %s",
        project_dir_path,
        " ".join(install_command),
    )
    try:
        install_result = process_runner.run(
            install_command,
            cwd=project_dir_path,
            check=False,
        )
    except OSError as launch_error:
        return _InstallAttempt(
            succeeded=False,
            reason=REASON_LAUNCH_FAILED,
            attempted_commands=attempted_commands,
            detail=sanitize_detail_text(f"{type(launch_error).__name__}: {launch_error}"),
        )
    if install_result.return_code != 0:
        failure_detail = install_result.stderr.strip() or install_result.stdout.strip()
        return _InstallAttempt(
            succeeded=False,
            reason=REASON_INSTALL_FAILED,
            requires_user_action=classify_user_action_from_failure_text(failure_detail),
            attempted_commands=attempted_commands,
            exit_code=install_result.return_code,
            detail=sanitize_detail_text(failure_detail),
        )
    if not (project_dir_path / NODE_MODULES_DIR_NAME).exists():
        return _InstallAttempt(
            succeeded=False,
            reason=REASON_INSTALL_NO_OUTPUT,
            attempted_commands=attempted_commands,
            exit_code=install_result.return_code,
            detail=sanitize_detail_text(
                "install exited 0 but no node_modules appeared in the project directory"
            ),
        )
    return _InstallAttempt(
        succeeded=True,
        attempted_commands=attempted_commands,
        exit_code=install_result.return_code,
    )


def _lockfiles_match_across_checkouts(
    repo_project_path: Path,
    worktree_project_path: Path,
) -> bool:
    """主检出与 worktree 的锁文件内容是否一致（不一致即依赖已漂移）。"""
    for lockfile_name in _LOCKFILE_PACKAGE_MANAGERS:
        repo_lockfile_path = repo_project_path / lockfile_name
        worktree_lockfile_path = worktree_project_path / lockfile_name
        if repo_lockfile_path.is_file() != worktree_lockfile_path.is_file():
            return False
        if not repo_lockfile_path.is_file():
            continue
        try:
            if repo_lockfile_path.read_bytes() != worktree_lockfile_path.read_bytes():
                return False
        except OSError:
            return False
    return True


def _try_reuse_main_checkout_dependencies(
    reuse_target: _ReuseTarget,
) -> _ReuseAttempt:
    """安全可行时复用主检出的 ``node_modules``（零成本回退）。

    只在两棵树的锁文件一致时复用：把漂移过的依赖链进 worktree 会让后续命令在错误的
    环境里运行，并把「缺失」伪装成「就绪」。

    Args:
        reuse_target: 复用所需的三元组路径（主检出根、worktree 项目目录、相对路径）。

    Returns:
        复用尝试结论。
    """
    repo_project_path = reuse_target.repo_root_path / reuse_target.relative_project_path
    source_node_modules_path = repo_project_path / NODE_MODULES_DIR_NAME
    target_node_modules_path = reuse_target.project_dir_path / NODE_MODULES_DIR_NAME
    if not source_node_modules_path.is_dir():
        return _ReuseAttempt(
            succeeded=False,
            reason=REASON_REUSABLE_ABSENT,
            detail=(
                f"main checkout has no {NODE_MODULES_DIR_NAME} for this project "
                f"({repo_project_path})"
            ),
        )
    if not _lockfiles_match_across_checkouts(
        repo_project_path,
        reuse_target.project_dir_path,
    ):
        return _ReuseAttempt(
            succeeded=False,
            reason=REASON_REUSABLE_STALE,
            detail=(
                "main checkout lockfiles differ from this branch; reusing them would "
                "present a stale environment as ready"
            ),
        )
    try:
        os.symlink(
            source_node_modules_path,
            target_node_modules_path,
            target_is_directory=True,
        )
    except OSError as symlink_error:
        return _ReuseAttempt(
            succeeded=False,
            reason=REASON_LINK_FAILED,
            detail=sanitize_detail_text(f"symlink failed: {symlink_error}"),
        )
    return _ReuseAttempt(succeeded=True)


def _prepare_frontend_project(
    preparation_request: _ProjectPreparationRequest,
) -> FrontendProjectRecord:
    """处理单个前端项目：所有失败都在此收敛成诊断记录，绝不上抛。"""
    install_plan = plan_package_manager(
        preparation_request.project_dir_path,
        preparation_request.worktree_path,
    )
    target_node_modules_path = preparation_request.project_dir_path / NODE_MODULES_DIR_NAME
    # is_symlink() 兜住断链：exists() 会跟随链接目标，悬空符号链接会被误判为缺失而被覆盖。
    if target_node_modules_path.exists() or target_node_modules_path.is_symlink():
        project_outcome = _ProjectOutcome(
            outcome=OUTCOME_PRESENT,
            reason=REASON_NO_ACTION_NEEDED,
        )
    elif install_plan.is_workspace_member:
        if install_plan.reason == REASON_MANAGER_CONFLICT:
            project_outcome = _ProjectOutcome(
                outcome=OUTCOME_NOT_PREPARED,
                reason=REASON_MANAGER_CONFLICT,
                detail=(
                    "workspace root and member package-manager evidence conflicts; "
                    "install was left to the agent"
                ),
            )
        else:
            project_outcome = _ProjectOutcome(
                outcome=OUTCOME_SKIPPED,
                reason=REASON_WORKSPACE_COVERED,
                detail="an ancestor workspace install owns this directory's dependencies",
            )
    else:
        install_attempt = _try_install_frontend_dependencies(
            preparation_request.project_dir_path,
            install_plan,
            preparation_request.process_runner,
        )
        if install_attempt.succeeded:
            project_outcome = _ProjectOutcome(
                outcome=OUTCOME_INSTALLED,
                reason="",
                attempted_commands=install_attempt.attempted_commands,
                exit_code=install_attempt.exit_code,
            )
        else:
            reuse_attempt = _try_reuse_main_checkout_dependencies(
                _ReuseTarget(
                    repo_root_path=preparation_request.repo_root_path,
                    project_dir_path=preparation_request.project_dir_path,
                    relative_project_path=preparation_request.relative_project_path,
                )
            )
            diagnostic_detail = " | ".join(
                detail_part
                for detail_part in (install_attempt.detail, reuse_attempt.detail)
                if detail_part
            )
            if reuse_attempt.succeeded:
                # 依赖已经可用，不再要求人做什么：项目是就绪状态，装不上的原因仍留在 reason/detail。
                project_outcome = _ProjectOutcome(
                    outcome=OUTCOME_REUSED,
                    reason=install_attempt.reason,
                    attempted_commands=install_attempt.attempted_commands,
                    exit_code=install_attempt.exit_code,
                    detail=diagnostic_detail,
                )
            else:
                project_outcome = _ProjectOutcome(
                    outcome=OUTCOME_NOT_PREPARED,
                    reason=install_attempt.reason or reuse_attempt.reason,
                    requires_user_action=install_attempt.requires_user_action,
                    attempted_commands=install_attempt.attempted_commands,
                    exit_code=install_attempt.exit_code,
                    detail=diagnostic_detail,
                )
    return FrontendProjectRecord(
        project_path=preparation_request.relative_project_path.as_posix(),
        outcome=project_outcome.outcome,
        reason=project_outcome.reason,
        package_manager=install_plan.package_manager,
        selection_evidence=install_plan.evidence,
        attempted_commands=project_outcome.attempted_commands,
        exit_code=project_outcome.exit_code,
        requires_user_action=project_outcome.requires_user_action,
        detail=project_outcome.detail,
    )


def ensure_frontend_node_modules(
    repo_root_path: Path,
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> DependencyPreparationReport:
    """**可恢复地**为 worktree 内的前端项目准备 ``node_modules``。

    遍历 ``worktree_path``（剪掉 :data:`FRONTEND_SCAN_PRUNED_DIR_NAMES`）找出含
    ``package.json`` 的目录，对每个尚未拥有 ``node_modules`` 的项目：

    1. 识别包管理器（packageManager 字段 + workspace 根配置 + 锁文件；冲突不硬选）。
    2. 安全可行时执行一次仓库内安装（可执行文件缺失或启动失败只记诊断）。
    3. 安装没做成时尝试复用主检出的依赖（锁文件一致才复用）。
    4. 三步都没得到可用依赖时只记录诊断并继续下一个项目——**Agent 仍会启动**，
       由它决定下一步。

    worktree 内已有的 ``node_modules`` 永不覆盖。

    Args:
        repo_root_path: 主检出根目录（其前端项目可能已装好依赖，可被复用）。
        worktree_path: 目标 worktree 绝对路径。
        process_runner: 执行安装命令的 runner。

    Returns:
        每个前端项目一条记录的 :class:`DependencyPreparationReport`。
    """
    repo_root_path = repo_root_path.resolve()
    worktree_path = worktree_path.resolve()
    if worktree_path == repo_root_path:
        return DependencyPreparationReport(worktree_path=str(worktree_path))

    try:
        project_dir_paths = _scan_frontend_project_paths(worktree_path)
    except OSError as walk_error:
        _logger.warning("Frontend project scan failed under %s: %s", worktree_path, walk_error)
        return DependencyPreparationReport(
            worktree_path=str(worktree_path),
            records=(
                FrontendProjectRecord(
                    project_path=".",
                    outcome=OUTCOME_NOT_PREPARED,
                    reason=REASON_PREPARATION_CRASHED,
                    detail=sanitize_detail_text(f"{type(walk_error).__name__}: {walk_error}"),
                ),
            ),
        )

    project_records: list[FrontendProjectRecord] = []
    for project_dir_path in project_dir_paths:
        preparation_request = _ProjectPreparationRequest(
            repo_root_path=repo_root_path,
            worktree_path=worktree_path,
            project_dir_path=project_dir_path,
            relative_project_path=project_dir_path.relative_to(worktree_path),
            process_runner=process_runner,
        )
        try:
            project_records.append(_prepare_frontend_project(preparation_request))
        except Exception as prepare_error:  # noqa: BLE001 - 环境准备绝不允许终止 Agent 启动。
            _logger.warning(
                "Frontend dependency preparation for %s crashed; recorded instead: %s",
                preparation_request.relative_project_path,
                prepare_error,
            )
            project_records.append(
                FrontendProjectRecord(
                    project_path=preparation_request.relative_project_path.as_posix(),
                    outcome=OUTCOME_NOT_PREPARED,
                    reason=REASON_PREPARATION_CRASHED,
                    detail=sanitize_detail_text(f"{type(prepare_error).__name__}: {prepare_error}"),
                )
            )
    return DependencyPreparationReport(
        worktree_path=str(worktree_path),
        records=tuple(project_records),
    )


def _scan_frontend_project_paths(worktree_path: Path) -> list[Path]:
    """按目录名字典序遍历 worktree，收集含 ``package.json`` 的项目目录。

    显式排序子目录（而非 ``os.walk`` 的文件系统返回顺序）：诊断记录会进 Issue 评论和
    prompt，顺序不稳定会让同一份代码两次跑出不同文本，无法逐字节比对。
    """
    project_dir_paths: list[Path] = []
    for current_dir_text, child_dir_names, child_file_names in os.walk(worktree_path):
        child_dir_names[:] = sorted(
            child_dir_name
            for child_dir_name in child_dir_names
            if child_dir_name not in FRONTEND_SCAN_PRUNED_DIR_NAMES
        )
        if PACKAGE_MANIFEST_FILE_NAME in child_file_names:
            project_dir_paths.append(Path(current_dir_text))
    return project_dir_paths


def link_frontend_node_modules(
    repo_root_path: Path,
    worktree_path: Path,
) -> list[Path]:
    """把主检出的前端 ``node_modules`` 符号链接进 worktree（legacy 回退）。

    新代码应调用 :func:`ensure_frontend_node_modules`：它先做可恢复的准备，只在安装
    不可用或失败时回退到链接，并把诊断写成结构化记录交给 Agent。

    Args:
        repo_root_path: 主检出根目录（其前端项目已安装依赖）。
        worktree_path: 目标 worktree 绝对路径。

    Returns:
        成功链接的项目 worktree 相对路径（按扫描顺序）。
    """
    repo_root_path = repo_root_path.resolve()
    worktree_path = worktree_path.resolve()
    if worktree_path == repo_root_path:
        return []

    linked_relative_paths: list[Path] = []
    for project_dir_path in _scan_frontend_project_paths(worktree_path):
        relative_project_path = project_dir_path.relative_to(worktree_path)
        target_node_modules_path = project_dir_path / NODE_MODULES_DIR_NAME
        if target_node_modules_path.exists() or target_node_modules_path.is_symlink():
            continue
        reuse_attempt = _try_reuse_main_checkout_dependencies(
            _ReuseTarget(
                repo_root_path=repo_root_path,
                project_dir_path=project_dir_path,
                relative_project_path=relative_project_path,
            )
        )
        if reuse_attempt.succeeded:
            linked_relative_paths.append(relative_project_path)
        else:
            _logger.warning(
                "Could not reuse main-checkout dependencies for worktree project %s: %s",
                relative_project_path,
                reuse_attempt.detail,
            )
    return linked_relative_paths


def exclude_frontend_node_modules_from_git(
    worktree_path: Path,
    linked_relative_paths: list[Path],
    process_runner: IProcessRunner,
) -> None:
    """把符号链接的 ``node_modules`` 排除出 worktree 的 git status。

    为每个被链接项目的 ``node_modules`` 往 ``info/exclude`` 追加一条**锚定且不带尾部
    斜杠**的规则（例如 ``/frontend/node_modules``）。``node_modules/`` 这类目录规则不
    匹配符号链接，链接会显示为未跟踪文件，并可能被 agent 的 ``git add -A`` 提交成机器
    本地绝对链接。

    安装产出的真实 ``node_modules`` 目录已被仓库常规的 ``node_modules/`` 忽略规则覆盖，
    不需要在这里重复登记。

    ``info/exclude`` 是本地文件（解析到共享 common checkout，永不进版本库），因此不产生
    代码 diff；写入幂等，已存在的行不会重复。

    Args:
        worktree_path: 目标 worktree 绝对路径。
        linked_relative_paths: ``node_modules`` 为符号链接、需要排除的项目相对路径。
        process_runner: 用于通过 ``git rev-parse`` 解析 ``info/exclude`` 位置。
    """
    if not linked_relative_paths:
        return
    exclude_path_result = process_runner.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=worktree_path,
        check=False,
    )
    exclude_path_text = exclude_path_result.stdout.strip()
    if exclude_path_result.return_code != 0 or not exclude_path_text:
        _logger.warning(
            "Could not resolve git info/exclude for %s; symlinked "
            "node_modules may show as untracked.",
            worktree_path,
        )
        return
    exclude_path = Path(exclude_path_text)
    if not exclude_path.is_absolute():
        exclude_path = worktree_path / exclude_path
    if exclude_path.is_dir():
        _logger.warning(
            "Resolved info/exclude path is a directory (%s); skipping node_modules exclusion.",
            exclude_path,
        )
        return
    existing_text = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    known_lines = set(existing_text.splitlines())
    appended_lines: list[str] = []
    for relative_project_path in linked_relative_paths:
        posix_project_path = relative_project_path.as_posix().strip("/")
        if posix_project_path in {"", "."}:
            exclude_line = f"/{NODE_MODULES_DIR_NAME}"
        else:
            exclude_line = f"/{posix_project_path}/{NODE_MODULES_DIR_NAME}"
        if exclude_line not in known_lines:
            appended_lines.append(exclude_line)
            known_lines.add(exclude_line)
    if not appended_lines:
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    prefix_text = existing_text
    if prefix_text and not prefix_text.endswith("\n"):
        prefix_text += "\n"
    exclude_path.write_text(prefix_text + "\n".join(appended_lines) + "\n", encoding="utf-8")
