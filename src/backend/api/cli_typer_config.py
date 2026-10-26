"""Typer commands under ``kc config``.

Currently a single command — ``kc config migrate`` — which previews or applies local
state, repository configuration, worktree directory, and GitHub marker migrations.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import _run_typer_repository_command, config_app


@config_app.command("migrate")
def config_migrate_command(
    ctx: typer.Context,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply the migration after previewing it."),
    ] = False,
    include_closed: Annotated[
        bool,
        typer.Option("--include-closed", help="Also migrate closed Issues and PRs."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview the migration without writing anything (default).",
        ),
    ] = False,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help="Target repository path (default: the current repository; outside one, "
            "only the local state directory is handled).",
        ),
    ] = None,
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="Not supported here; use --repo."),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option(
            "--config", help="Deprecated: config is loaded from config.toml and env vars."
        ),
    ] = None,
) -> int:
    """Preview or apply local configuration and GitHub marker migration."""
    return _run_typer_repository_command(
        ctx,
        "config migrate",
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
        apply=apply,
        include_closed=include_closed,
    )


__all__ = ["config_migrate_command"]
