"""Shared CLI helper utilities used by ``backend.api.cli``.

These helpers are thin presentation-layer adapters (auth checks, output
formatting, target resolution) that do not themselves invoke long-running
business logic. Keeping them in a dedicated module lets the main dispatch
file stay focused on command wiring.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    OUTPUT_FORMAT_TABLE,
    CliError,
    render_cli_error,
)
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    create_github_client,
    find_repository_match_for_path,
    load_fresh_agent_runner_settings,
    logger,
    resolve_repository_targets,
)
from backend.core.use_cases.agent_runner_repository_local import (
    IARRepositoryNotInitializedError,
    detect_git_repository_root,
    require_iar_repository_initialized,
)
from backend.core.use_cases.worktree_cleanup import (
    WorktreeCleanupResult,
    WorktreeCleanupStatus,
)

if TYPE_CHECKING:
    from backend.core.shared.interfaces.agent_runner import IProcessRunner
    from backend.core.shared.models.agent_runner import RepositoryRunContext


def _ensure_gh_auth_or_prompt(repo_path: Path, process_runner: "IProcessRunner") -> None:
    """Check gh auth status and exit with a friendly message if not authenticated."""
    if os.environ.get("IAR_SKIP_GH_AUTH_CHECK") == "1":
        return
    github_client = create_github_client(repo_path, process_runner)
    auth_status = github_client.check_auth_status()
    if auth_status.authenticated:
        return
    detail = auth_status.failure_reason or "GitHub CLI 未认证。"
    raise CliError(
        f"GitHub CLI 认证失败：{detail}",
        code=ExitCode.PERMISSION,
        suggestion="gh auth login -h github.com",
    )


def _print_worktree_cleanup_result(cleanup_result: WorktreeCleanupResult) -> None:
    """Print a concise branch cleanup summary."""
    if not cleanup_result.branches:
        console.print("[green]No local iAR issue branches found.[/]")
        return

    for branch_result in cleanup_result.branches:
        worktree_suffix = f" ({branch_result.worktree_path})" if branch_result.worktree_path else ""
        if branch_result.status is WorktreeCleanupStatus.WOULD_DELETE:
            console.print(
                f"[yellow]Would delete:[/] {branch_result.branch}{worktree_suffix} - "
                f"{branch_result.reason}"
            )
        elif branch_result.status is WorktreeCleanupStatus.DELETED:
            console.print(
                f"[green]Deleted:[/] {branch_result.branch}{worktree_suffix} - "
                f"{branch_result.reason}"
            )
        elif branch_result.status is WorktreeCleanupStatus.FAILED:
            console.print(
                f"[red]Failed:[/] {branch_result.branch}{worktree_suffix} - {branch_result.reason}"
            )
        else:
            console.print(
                f"[dim]Skipped:[/] {branch_result.branch}{worktree_suffix} - {branch_result.reason}"
            )

    console.print(
        "Cleanup summary: "
        f"deleted={cleanup_result.deleted_count}, "
        f"would_delete={cleanup_result.would_delete_count}, "
        f"skipped={cleanup_result.skipped_count}, "
        f"failed={cleanup_result.failed_count}"
    )


def repository_selector_error(exc: ValueError) -> CliError:
    """把仓库选择器的 ``ValueError`` 归成带语义退出码的 :class:`CliError`。

    引擎层用 ``ValueError`` 表达所有"选择器不合法"，本函数是 api 层唯一的分类点：
    互斥/组合非法 → ``USAGE(2)``，未注册 → ``NOT_FOUND(3)``，其余 → ``GENERAL(1)``。

    Args:
        exc: ``resolve_repository_targets`` 抛出的异常。

    Returns:
        携带语义退出码与可跑 ``suggestion`` 的命令错误。
    """
    message = str(exc)
    lowered = message.lower()
    if "mutually exclusive" in lowered or "cannot be combined" in lowered:
        return CliError(
            message,
            code=ExitCode.USAGE,
            suggestion="iar registry list",
        )
    if "not found in config" in lowered or "not found in registry" in lowered:
        return CliError(
            message,
            code=ExitCode.NOT_FOUND,
            suggestion="iar registry list",
        )
    if "disabled" in lowered:
        return CliError(
            message,
            code=ExitCode.PERMISSION,
            suggestion="iar registry reinit --repo-id <id>",
        )
    return CliError(message, code=ExitCode.GENERAL, suggestion="iar registry list")


def _resolve_cli_repository_targets(
    *,
    parsed: argparse.Namespace,
    runner_settings: Any,
    repo_id: str | None,
    repo_override: str | None,
) -> list["RepositoryRunContext"]:
    """Resolve repository targets for parsed CLI selectors."""
    try:
        return resolve_repository_targets(
            runner_settings,
            repo_id=repo_id,
            repo_path_override=repo_override,
            all_repositories=getattr(parsed, "all_repositories", False),
        )
    except ValueError as exc:
        raise repository_selector_error(exc) from exc


def require_single_repository_target(
    command: str,
    repository_contexts: list["RepositoryRunContext"],
) -> "RepositoryRunContext":
    """取出唯一目标仓库上下文，不唯一时抛带语义退出码的 :class:`CliError`。

    只支持单目标的入口（``ask`` / ``repl`` / ``deliberate`` / ``recover`` /
    ``blocked-continue`` / ``roadmap advance`` / ``worktree cleanup`` / ``logs``）
    在 ``--all`` 或 cwd 匹配出多个仓库时必须收敛到一个选择器。分类点集中在这里，
    人类与机器两条路径才能给出同一条可跑 ``suggestion``。

    Args:
        command: 命令名，用于错误文本（例如 ``"ask"``、``"worktree cleanup"``）。
        repository_contexts: 仓库选择器的解析结果。

    Returns:
        唯一的 :class:`RepositoryRunContext`。

    Raises:
        CliError: 目标数量不是 1，退出码 ``USAGE(2)``。
    """
    if len(repository_contexts) != 1:
        raise CliError(
            f"iar {command} requires exactly one target repository. "
            "Use --repo or --repo-id to specify.",
            code=ExitCode.USAGE,
            suggestion="iar registry list",
        )
    return repository_contexts[0]


@dataclasses.dataclass(frozen=True)
class _DefaultDaemonTarget:
    """Result of inferring a daemon target from cwd."""

    repo_id: str | None
    error: str


def daemon_target_error(message: str) -> CliError:
    """把 cwd 仓库推断失败归成带语义退出码的 :class:`CliError`。

    ``_resolve_default_daemon_target`` 用一段人类文本表达所有"cwd 选不出目标"，
    这里按文本分类：歧义选择 → ``USAGE(2)``、禁用 → ``PERMISSION(4)``、其余
    （非 Git / 未注册 / 未初始化）→ ``NOT_FOUND(3)``。人类模式仍打印原文，
    机器模式由中央点渲染成 envelope。

    Args:
        message: ``_DefaultDaemonTarget.error`` 的文本。

    Returns:
        带语义退出码与可跑 ``suggestion`` 的命令错误。
    """
    lowered = message.lower()
    if "multiple enabled repositories" in lowered:
        return CliError(message, code=ExitCode.USAGE, suggestion="iar registry list")
    if "is disabled" in lowered:
        return CliError(
            message,
            code=ExitCode.PERMISSION,
            suggestion="iar registry reinit --repo-id <id>",
        )
    if "not initialized" in lowered:
        return CliError(message, code=ExitCode.NOT_FOUND, suggestion="iar init")
    return CliError(message, code=ExitCode.NOT_FOUND, suggestion="iar registry list")


def report_daemon_target_error(message: str, *, fmt: str) -> int:
    """落 cwd 目标推断失败：人类模式保留原日志文本，机器模式落 envelope。

    Args:
        message: ``_DefaultDaemonTarget.error`` 的文本。
        fmt: ``resolve_output_format`` 的结果。

    Returns:
        该失败对应的退出码。
    """
    error = daemon_target_error(message)
    if fmt == OUTPUT_FORMAT_JSON:
        return render_cli_error(error, fmt=fmt)
    logger.error(message)
    return int(error.code)


def _resolve_default_daemon_target() -> _DefaultDaemonTarget:
    """Infer the daemon target repository from the current working directory.

    Returns:
        _DefaultDaemonTarget: when ``repo_id`` is set, use that repository;
        when ``error`` is set, fail early with the error message. This function
        no longer falls back to ``--all``; callers must explicitly request all
        enabled registry entries.
    """
    try:
        cwd_git_root = detect_git_repository_root(Path.cwd())
    except ValueError:
        return _DefaultDaemonTarget(
            repo_id=None,
            error=(
                "Current directory is not a Git repository. "
                "Run from an initialized iAR repository, or use --all to target all enabled registry entries."
            ),
        )
    settings = load_fresh_agent_runner_settings()
    match = find_repository_match_for_path(settings, cwd_git_root)
    if match.is_unique_enabled:
        assert match.matched_repo_id is not None  # noqa: S101
        try:
            require_iar_repository_initialized(cwd_git_root)
        except IARRepositoryNotInitializedError:
            return _DefaultDaemonTarget(
                repo_id=None,
                error=(
                    f"Repository '{match.matched_repo_id}' is not initialized. "
                    "Run 'iar init' in the repository root, or use --all to target all enabled registry entries."
                ),
            )
        return _DefaultDaemonTarget(repo_id=match.matched_repo_id, error="")
    if match.is_disabled:
        assert match.disabled_repo_id is not None  # noqa: S101
        return _DefaultDaemonTarget(
            repo_id=None,
            error=(
                f"Repository '{match.disabled_repo_id}' is disabled. "
                "Use --repo-id to target it explicitly, or enable it in config.toml."
            ),
        )
    if match.is_ambiguous:
        candidates = ", ".join(repo_id for repo_id, _ in match.enabled_candidates)
        return _DefaultDaemonTarget(
            repo_id=None,
            error=(
                f"Current directory matches multiple enabled repositories: {candidates}. "
                "Use --repo-id to target one, or --all to target all."
            ),
        )
    return _DefaultDaemonTarget(
        repo_id=None,
        error=(
            "Current directory is not an enabled iAR registry target. "
            "Use --repo-id to target a registered repository, or --all to target all enabled registry entries."
        ),
    )


def _resolve_run_trigger(command_kind: str) -> str:
    """解析运行记录的 trigger 来源。

    管理终端托管的子进程带有 ``IAR_CONSOLE=1`` 环境标记，记为
    ``console_*``；否则记为 ``cli_*``。

    Args:
        command_kind: ``"run"`` 或 ``"daemon"``。
    """
    prefix = "console" if os.environ.get("IAR_CONSOLE") == "1" else "cli"
    return f"{prefix}_{command_kind}"


def _create_run_history_store_or_none():
    """创建运行历史存储；初始化失败时降级为 None（不阻断 CLI）。"""
    try:
        return create_console_store()
    except Exception as exc:  # noqa: BLE001 - history is a side channel.
        logger.warning("Run history store unavailable: %s", exc)
        return None


def _handle_not_initialized_error(
    exc: IARRepositoryNotInitializedError, *, fmt: str = OUTPUT_FORMAT_TABLE
) -> int:
    """Print a friendly error and suggest running `iar init`.

    仓库缺少 ``.iar.toml`` 属于「未找到」类别：机器模式落 envelope 并返回 ``3``，
    人类模式保留原有的四行引导文本（只改退出码，不改文案）。
    """
    error = CliError(
        f"Repository is not initialized for iar; expected local config: {exc.config_path}",
        code=ExitCode.NOT_FOUND,
        suggestion="iar init",
    )
    if fmt == OUTPUT_FORMAT_JSON:
        return render_cli_error(error, fmt=fmt)
    error_console.print("[red]Repository is not initialized for iar.[/]")
    error_console.print(f"Expected local config: {exc.config_path}", soft_wrap=True)
    error_console.print("Run the following command from the repository root:")
    error_console.print("  iar init")
    return int(error.code)
