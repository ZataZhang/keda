"""Typer commands under ``kc registry``.

Holds every command that operates on the global repository registry
(:func:`registry_scan_command`, :func:`registry_sync_command`,
:func:`registry_reinit_command`, :func:`registry_remove_command`,
:func:`registry_list_command`, :func:`registry_start_command`,
:func:`registry_stop_command`).
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError, render_cli_error
from backend.api.cli_typer_app import (
    JsonOutputOption,
    OutputFormat,
    OutputOption,
    _enum_value,
    _run_typer_command,
    registry_app,
)


def _registry_selector_error(message: str, suggestion: str) -> int:
    """registry start/stop 的旗标用法错误：envelope 或等价文本 + 语义退出码 2。"""
    return render_cli_error(CliError(message, code=ExitCode.USAGE, suggestion=suggestion))


@registry_app.command("scan")
def registry_scan_command(
    scan_root: Annotated[str, typer.Argument(help="Directory to scan.")] = ".",
) -> int:
    """Discover KedaCode-initialized git repositories under a path."""
    return _run_typer_command(
        "registry scan",
        scan_root=scan_root,
    )


@registry_app.command("sync")
def registry_sync_command(
    scan_root: Annotated[str, typer.Argument(help="Directory to scan.")] = ".",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print candidates without writing."),
    ] = False,
) -> int:
    """Discover and register all KedaCode repositories under a path."""
    return _run_typer_command(
        "registry sync",
        scan_root=scan_root,
        dry_run=dry_run,
    )


@registry_app.command("reinit")
def registry_reinit_command(
    repo_id: Annotated[str, typer.Option("--repo-id", help="Registry identifier to reinitialize.")],
    remote: Annotated[str, typer.Option("--remote", help="Git remote name to write.")] = "origin",
    base_branch: Annotated[
        str | None, typer.Option("--base-branch", help="Base branch to write.")
    ] = None,
    start_daemons: Annotated[
        bool,
        typer.Option("--start-daemons", help="Restart daemon processes."),
    ] = False,
) -> int:
    """Re-initialize an already registered repository's local config."""
    return _run_typer_command(
        "registry reinit",
        repo_id=repo_id,
        remote=remote,
        base_branch=base_branch,
        start_daemons=start_daemons,
    )


@registry_app.command("remove")
def registry_remove_command(
    repo_id: Annotated[str, typer.Option("--repo-id", help="Registry identifier to remove.")],
) -> int:
    """Remove a repository from the registry and stop its daemons."""
    return _run_typer_command(
        "registry remove",
        repo_id=repo_id,
    )


@registry_app.command("list")
def registry_list_command(
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """List registered repositories and their daemon status."""
    return _run_typer_command("registry list", output=_enum_value(output), as_json=as_json)


@registry_app.command("start")
def registry_start_command(
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="Registry identifier to start daemons for."),
    ] = None,
    all: Annotated[
        bool,
        typer.Option("--all", help="Start daemons for all enabled repositories."),
    ] = False,
    no_review_daemon: Annotated[
        bool,
        typer.Option(
            "--no-supervise-daemon",
            "--no-review-daemon",
            help="Only start the agent daemon, skip post-PR supervision.",
        ),
    ] = False,
) -> int:
    """Start daemon and supervise-daemon for registered repositories."""
    if not repo_id and not all:
        return _registry_selector_error(
            "Either --repo-id or --all is required for kc registry start.",
            "kc registry start --all",
        )
    if repo_id and all:
        return _registry_selector_error(
            "--repo-id and --all are mutually exclusive for kc registry start.",
            "kc registry start --repo-id <id>",
        )
    return _run_typer_command(
        "registry start",
        repo_id=repo_id,
        all=all,
        no_review_daemon=no_review_daemon,
    )


@registry_app.command("stop")
def registry_stop_command(
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="Registry identifier to stop daemons for."),
    ] = None,
    all: Annotated[
        bool,
        typer.Option("--all", help="Stop daemons for all repositories with running processes."),
    ] = False,
    no_review_daemon: Annotated[
        bool,
        typer.Option(
            "--no-supervise-daemon",
            "--no-review-daemon",
            help="Only stop the agent daemon, leave post-PR supervision running.",
        ),
    ] = False,
) -> int:
    """Stop daemon and supervise-daemon for registered repositories."""
    if not repo_id and not all:
        return _registry_selector_error(
            "Either --repo-id or --all is required for kc registry stop.",
            "kc registry stop --all",
        )
    if repo_id and all:
        return _registry_selector_error(
            "--repo-id and --all are mutually exclusive for kc registry stop.",
            "kc registry stop --repo-id <id>",
        )
    return _run_typer_command(
        "registry stop",
        repo_id=repo_id,
        all=all,
        no_review_daemon=no_review_daemon,
    )


__all__ = [
    "registry_list_command",
    "registry_reinit_command",
    "registry_remove_command",
    "registry_scan_command",
    "registry_start_command",
    "registry_stop_command",
    "registry_sync_command",
]
