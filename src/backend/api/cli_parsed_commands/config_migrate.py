"""``iar config migrate`` handler.

Removes the generated-content defaults an older ``iar init`` pinned into a
repository's ``.iar.toml`` so the repository inherits the current defaults
again. The rewrite itself lives in the engines layer; this module resolves the
target repository and prints the report.
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
    PinDecision,
    migrate_repository_local_config,
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
    """打印迁移报告；含 ``[表名]`` 的行一律关闭 rich markup，避免被当成样式标签吞掉。"""
    removed_verb = "Would remove" if dry_run else "Removed"
    if migration_result.removed_pins:
        console.print(
            f"{removed_verb} {len(migration_result.removed_pins)} value(s) pinned by an older "
            f"`iar init` in {migration_result.config_path}:",
            markup=False,
            soft_wrap=True,
        )
        for pin_line in _format_pin_lines(migration_result.removed_pins, with_reason=False):
            console.print(pin_line, markup=False, soft_wrap=True)
    if migration_result.kept_pins:
        console.print(
            f"Kept {len(migration_result.kept_pins)} value(s) that match an older `iar init` "
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
            "by an older `iar init`.[/]",
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


def run_config_migrate_command(ctx: ParsedCommandContext) -> int:
    """``iar config migrate``: drop scaffold-pinned generated_content values from ``.iar.toml``."""
    if ctx.repo_id is not None:
        raise CliError(
            "iar config migrate edits one repository's .iar.toml; use --repo <path> "
            "or run it inside the repository (--repo-id is not supported).",
            code=ExitCode.USAGE,
            suggestion="iar config migrate --repo .",
        )

    dry_run = bool(getattr(ctx.parsed, "dry_run", False))
    start_path = Path(ctx.repo_override) if ctx.repo_override is not None else Path.cwd()
    try:
        repo_root_path = detect_git_repository_root(start_path, ctx.process_runner)
    except ValueError as exc:
        # 不在 Git 仓库里是"找不到目标"，不是用法错误：给 3 + 可跑的下一步。
        raise CliError(str(exc), code=ExitCode.NOT_FOUND, suggestion="iar registry list") from exc
    try:
        migration_result = migrate_repository_local_config(repo_root_path, dry_run=dry_run)
    except ValueError as exc:
        # 迁移校验失败（ConfigMigrationError）是未分类失败；未初始化
        # （IARRepositoryNotInitializedError）不是 ValueError，交给统一入口给
        # `iar init` 提示。错误正文可能含方括号，关闭 markup 单独打印。
        error_console.print("[red]iar config migrate failed:[/]")
        error_console.print(str(exc), markup=False)
        return 1

    _print_migration_report(migration_result, dry_run=dry_run)
    return 0


__all__ = ["run_config_migrate_command"]
