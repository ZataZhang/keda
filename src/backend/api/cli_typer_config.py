"""Typer commands under ``iar config``.

Currently a single command — ``iar config migrate`` — which removes the
generated-content defaults an older ``iar init`` pinned into a repository's
``.iar.toml`` so the repository follows the current defaults again.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import _run_typer_repository_command, config_app


@config_app.command("migrate")
def config_migrate_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be removed (with a diff) without writing anything.",
        ),
    ] = False,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Target repository path (default: the current repository)."),
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
    """Remove generated_content values an older `iar init` pinned into .iar.toml."""
    return _run_typer_repository_command(
        ctx,
        "config migrate",
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
    )


__all__ = ["config_migrate_command"]
