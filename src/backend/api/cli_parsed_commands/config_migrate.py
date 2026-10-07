"""``kc config migrate`` handler.

两步迁移，共用一套预检（预演与正式执行结论一致，预演永不写盘）：

1. **本机步骤**：把旧状态目录 ``~/.iar``（legacy-alias）搬到 ``~/.kedacode``，旧位置留一个指向新
   目录的相对链接，并把新状态目录里 ``config.toml`` 写着旧路径的取值改写过来。
2. **仓库步骤**：把当前仓库的 ``.iar.toml``（legacy-alias）改名为 ``.kedacode.toml``（只改名，不
   碰 git，由用户自己决定何时提交），再清掉旧 ``kc init`` 钉进配置的默认值。

占用中（任何以 ``kc`` / ``kedacode`` / ``iar`` 启动的进程、受管登记或锁文件里存活
的 PID）时拒绝动手并列出进程号。范围规则：仓库内或带 ``--repo`` 时两步都做；不在
仓库里隐式执行只做本机步骤；``--repo`` 指向非仓库给「找不到目标」；``--repo-id`` 不
支持。
"""

from __future__ import annotations

import difflib
from pathlib import Path

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError, json_literal
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.use_cases.agent_runner_config_migration import (
    ConfigMigrationResult,
    ConfigRenameResult,
    PinDecision,
    StateHomeMigrationResult,
    migrate_repository_local_config,
    migrate_state_home,
    rename_repository_local_config_file,
)
from backend.core.use_cases.agent_runner_config_migration import (
    STATE_HOME_CONFLICT_OUTCOMES as CONFLICT_OUTCOMES,
)
from backend.core.use_cases.agent_runner_config_migration import (
    STATE_HOME_FAILURE_OUTCOMES as FAILURE_OUTCOMES,
)
from backend.core.use_cases.agent_runner_repository_local import detect_git_repository_root


def _format_pin_lines(pin_decisions: tuple[PinDecision, ...], *, with_reason: bool) -> list[str]:
    """按所在表分组，把钉子渲染成 ``[表名]`` + 缩进的 ``key = value`` 行。"""
    pins_by_table_name: dict[str, list[PinDecision]] = {}
    for pin in pin_decisions:
        pins_by_table_name.setdefault(pin.table_name, []).append(pin)

    pin_lines: list[str] = []
    for table_name, table_pins in pins_by_table_name.items():
        pin_lines.append(f"  [{table_name}]")
        for pin in table_pins:
            pinned_value_text = json_literal(pin.pinned_value)
            reason_suffix = f"  ({pin.reason})" if with_reason else ""
            pin_lines.append(f"    {pin.key_name} = {pinned_value_text}{reason_suffix}")
    return pin_lines


def _print_migration_report(migration_result: ConfigMigrationResult, *, dry_run: bool) -> None:
    """打印钉子清理报告；含 ``[表名]`` 的行一律关闭 rich markup，避免被当成样式标签吞掉。"""
    removed_verb = "Would remove" if dry_run else "Removed"
    if migration_result.removed_pins:
        console.print(
            f"{removed_verb} {len(migration_result.removed_pins)} value(s) pinned by an older "
            f"`kc init` in {migration_result.config_path}:",
            markup=False,
            soft_wrap=True,
        )
        for pin_line in _format_pin_lines(migration_result.removed_pins, with_reason=False):
            console.print(pin_line, markup=False, soft_wrap=True)
    if migration_result.kept_pins:
        console.print(
            f"Kept {len(migration_result.kept_pins)} value(s) that match an older `kc init` "
            "but were left alone:",
            markup=False,
            soft_wrap=True,
        )
        for pin_line in _format_pin_lines(migration_result.kept_pins, with_reason=True):
            console.print(pin_line, markup=False, soft_wrap=True)
    if dry_run and migration_result.changed:
        console.print("", markup=False)
        for diff_line in difflib.unified_diff(
            migration_result.original_text.splitlines(),
            migration_result.migrated_text.splitlines(),
            fromfile=str(migration_result.config_path),
            tofile=f"{migration_result.config_path} (migrated)",
            lineterm="",
        ):
            console.print(diff_line, markup=False, highlight=False, soft_wrap=True)

    # 结尾几行带路径：soft_wrap 让长路径保持完整，方便复制。
    if not migration_result.changed and not migration_result.kept_pins:
        console.print(
            f"[dim]Nothing to migrate: {migration_result.config_path} has no values pinned "
            "by an older `kc init`.[/]",
            soft_wrap=True,
        )
    elif not migration_result.changed:
        console.print("[dim]Nothing was changed.[/]")
    elif dry_run:
        console.print("[cyan]Dry run: nothing was written.[/]")
    else:
        console.print(
            f"[green]Wrote migrated config:[/] {migration_result.config_path}", soft_wrap=True
        )


def _print_state_home_report(state_result: StateHomeMigrationResult, *, dry_run: bool) -> None:
    """打印本机状态目录这一步的结论。"""
    for plan_line in state_result.plan_lines:
        console.print(f"  {plan_line}", markup=False, soft_wrap=True)
    if state_result.rewritten_config_path is not None:
        verb = "would rewrite" if dry_run else "rewrote"
        console.print(
            f"  {verb} {state_result.rewritten_value_count} path value(s) in "
            f"{state_result.rewritten_config_path}",
            markup=False,
            soft_wrap=True,
        )
    if state_result.legacy_path_file_count:
        console.print(
            f"  {state_result.legacy_path_file_count} other file(s) under the state directory "
            "still mention the old path (listed only, not rewritten).",
            markup=False,
            soft_wrap=True,
        )
    console.print(state_result.message, markup=False, soft_wrap=True)


def _print_rename_report(rename_result: ConfigRenameResult) -> None:
    """打印仓库配置文件改名这一步的结论。"""
    console.print(rename_result.message, markup=False, soft_wrap=True)


def _resolve_target_repository(
    ctx: ParsedCommandContext, *, dry_run: bool
) -> tuple[Path | None, bool]:
    """解析本次要处理的仓库，返回 ``(仓库根或 None, 是否显式指定了仓库)``。

    不在 Git 仓库里时：显式 ``--repo`` 是「找不到目标」（3），隐式执行只是没有仓库
    步骤可做（本机步骤照做）。
    """
    repo_explicit = ctx.repo_override is not None
    start_path = Path(ctx.repo_override) if repo_explicit else Path.cwd()
    try:
        return detect_git_repository_root(start_path, ctx.process_runner), repo_explicit
    except ValueError as exc:
        if repo_explicit:
            raise CliError(
                str(exc),
                code=ExitCode.NOT_FOUND,
                suggestion="kc config migrate --repo ." if not dry_run else "kc registry add .",
            ) from exc
        return None, repo_explicit


def _state_home_exit_code(state_result: StateHomeMigrationResult) -> int | None:
    """把状态目录结论映射成退出码；``None`` 表示这一步可以继续。"""
    if state_result.outcome in CONFLICT_OUTCOMES:
        return int(ExitCode.CONFLICT)
    if state_result.outcome in FAILURE_OUTCOMES:
        return int(ExitCode.GENERAL)
    return 0


def run_config_migrate_command(ctx: ParsedCommandContext) -> int:
    """``kc config migrate``：迁移本机状态目录，并整理当前仓库的本地配置。"""
    if ctx.repo_id is not None:
        raise CliError(
            "kc config migrate edits one repository's .kedacode.toml plus your local state "
            "directory; use --repo <path> or run it inside the repository "
            "(--repo-id is not supported).",
            code=ExitCode.USAGE,
            suggestion="kc config migrate --repo .",
        )

    dry_run = bool(getattr(ctx.parsed, "dry_run", False))
    repo_root_path, _ = _resolve_target_repository(ctx, dry_run=dry_run)

    # ── 预检：两步共用，全部通过才动手 ─────────────────────────────────────────
    rename_plan = (
        rename_repository_local_config_file(repo_root_path, dry_run=True)
        if repo_root_path is not None
        else None
    )
    if rename_plan is not None and rename_plan.outcome == "conflict":
        console.print("[red]Refusing to migrate:[/]")
        _print_rename_report(rename_plan)
        return int(ExitCode.CONFLICT)

    state_result = migrate_state_home(dry_run=dry_run)
    exit_code = _state_home_exit_code(state_result)
    if exit_code != 0:
        console.print("[red]Refusing to migrate the local state directory:[/]")
        _print_state_home_report(state_result, dry_run=dry_run)
        return exit_code
    _print_state_home_report(state_result, dry_run=dry_run)

    if repo_root_path is None or rename_plan is None:
        # 不在仓库里隐式执行：本机步骤做完即可，仓库步骤留给 --repo 或仓库内执行。
        console.print(
            "[dim]Not inside a repository: only the local state directory was handled. "
            "Run this command inside a repository, or pass --repo <path>[/]",
            soft_wrap=True,
        )
        if dry_run:
            console.print("[cyan]Dry run: nothing was written.[/]")
        return exit_code

    target_repository_path = repo_root_path
    # 预演沿用预检得到的计划，正式执行才真的改名；两步都已通过预检才会走到这里。
    rename_result = (
        rename_plan if dry_run else rename_repository_local_config_file(target_repository_path)
    )
    _print_rename_report(rename_result)
    if rename_result.outcome == "move_failed":
        return int(ExitCode.GENERAL)
    if rename_result.outcome == "absent":
        console.print("[dim]Skipping repository cleanup: no repository-local config.[/]")
        if dry_run:
            console.print("[cyan]Dry run: nothing was written.[/]")
        return exit_code

    try:
        migration_result = migrate_repository_local_config(target_repository_path, dry_run=dry_run)
    except ValueError as exc:
        # 迁移校验失败（ConfigMigrationError）是未分类失败；未初始化
        # （IARRepositoryNotInitializedError）不是 ValueError，交给统一入口给
        # `kc init` 提示与退出码 3。错误正文可能含方括号，关闭 markup 单独打印。
        error_console.print("[red]kc config migrate failed:[/]")
        error_console.print(str(exc), markup=False)
        return 1
    _print_migration_report(migration_result, dry_run=dry_run)
    if dry_run:
        console.print("[cyan]Dry run: nothing was written.[/]")
    return exit_code


__all__ = ["run_config_migrate_command"]
