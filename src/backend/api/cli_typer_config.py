"""Typer commands under ``kc config``.

Currently a single command — ``kc config migrate`` — which moves the local state
directory from the legacy ``~/.iar`` (legacy-alias) to ``~/.kedacode``, renames the
repository's legacy ``.iar.toml`` (legacy-alias) to ``.kedacode.toml``, and removes
the generated-content defaults an older ``kc init`` pinned into it.
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
            help="Show the whole plan (state directory, rename, diff) without writing anything.",
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
    """Move ~/.iar (legacy-alias) to ~/.kedacode, rename .iar.toml, drop pinned values."""
    return _run_typer_repository_command(
        ctx,
        "config migrate",
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
    )


__all__ = ["config_migrate_command"]
