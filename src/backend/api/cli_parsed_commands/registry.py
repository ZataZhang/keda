"""``kc registry *`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_registry import (
    _run_registry_list_command,
    _run_registry_reinit_command,
    _run_registry_remove_command,
    _run_registry_start_command,
    _run_registry_stop_command,
)
from backend.core.use_cases.agent_runner_factory import create_registry_editor, logger
from backend.core.use_cases.agent_runner_repository_local import discover_iar_repositories


def _scan_root_error(command: str, exc: ValueError) -> CliError:
    """``registry scan/sync`` 的扫描根目录不存在：语义退出码 ``3``。"""
    return CliError(
        f"kc registry {command} failed: {exc}",
        code=ExitCode.NOT_FOUND,
        suggestion="kc registry list",
    )


def run_registry_scan_command(ctx: ParsedCommandContext) -> int:
    """``kc registry scan``: discover KedaCode-initialized repos under a path."""
    try:
        entries = discover_iar_repositories(
            scan_root=Path(ctx.parsed.scan_root),
            editor=create_registry_editor(),
        )
    except ValueError as exc:
        raise _scan_root_error("scan", exc) from exc
    if not entries:
        console.print("[yellow]No KedaCode repositories found.[/]")
        return 0
    for entry in entries:
        status = "registered" if entry.already_registered else "new"
        print(f"[{entry.repo_id}] {entry.path} ({status})")
    return 0


def run_registry_sync_command(ctx: ParsedCommandContext) -> int:
    """``kc registry sync``: discover and register all KedaCode repositories."""
    try:
        entries = discover_iar_repositories(
            scan_root=Path(ctx.parsed.scan_root),
            editor=create_registry_editor(),
        )
    except ValueError as exc:
        raise _scan_root_error("sync", exc) from exc
    new_entries = [entry for entry in entries if not entry.already_registered]
    if not new_entries:
        console.print("[green]No new KedaCode repositories to register.[/]")
        return 0
    if ctx.parsed.dry_run:
        console.print("[cyan]Would register:[/]")
        for entry in new_entries:
            console.print(f"  {entry.repo_id}: {entry.path}")
        return 0
    editor = create_registry_editor()
    added = 0
    for entry in new_entries:
        try:
            editor.add_repository(
                repo_id=entry.repo_id,
                path=entry.path,
                display_name=entry.display_name,
            )
        except ValueError as exc:
            logger.warning("Skipping %s: %s", entry.repo_id, exc)
            continue
        added += 1
        console.print(f"[green]Registered:[/] {entry.repo_id}")
    console.print(f"[green]Registered {added} repository(s).[/]")
    return 0


def run_registry_reinit_command(ctx: ParsedCommandContext) -> int:
    return _run_registry_reinit_command(ctx.parsed, ctx.process_runner)


def run_registry_remove_command(ctx: ParsedCommandContext) -> int:
    return _run_registry_remove_command(ctx.parsed, ctx.process_runner)


def run_registry_list_command(ctx: ParsedCommandContext) -> int:
    return _run_registry_list_command(ctx.process_runner, fmt=ctx.output_format)


def run_registry_start_command(ctx: ParsedCommandContext) -> int:
    return _run_registry_start_command(ctx.parsed, ctx.process_runner)


def run_registry_stop_command(ctx: ParsedCommandContext) -> int:
    return _run_registry_stop_command(ctx.parsed, ctx.process_runner)


__all__ = [
    "run_registry_list_command",
    "run_registry_reinit_command",
    "run_registry_remove_command",
    "run_registry_scan_command",
    "run_registry_start_command",
    "run_registry_stop_command",
    "run_registry_sync_command",
]
