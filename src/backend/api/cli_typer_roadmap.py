"""Typer commands under ``iar roadmap``.

Currently a single command — ``iar roadmap advance`` — which runs one
continuous-scheduling pass (reconcile + promote + discover) for the target
repository. The same logic runs automatically inside the fast-lane daemon.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import _run_typer_repository_command, roadmap_app


@roadmap_app.command("advance")
def roadmap_advance_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Report the plan for this pass without writing anything.",
        ),
    ] = False,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Target repository path."),
    ] = None,
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="Target configured repository ID."),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option(
            "--config", help="Deprecated: config is loaded from config.toml and env vars."
        ),
    ] = None,
) -> int:
    """Run one continuous-scheduling pass for the target repository."""
    return _run_typer_repository_command(
        ctx,
        "roadmap advance",
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
    )


__all__ = ["roadmap_advance_command"]
