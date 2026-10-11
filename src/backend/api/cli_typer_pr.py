"""``kc pr`` 的 Typer 命令。"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    RepoIdOption,
    RepoOption,
    _run_typer_repository_command,
    pr_app,
)


@pr_app.command("aggregate")
def aggregate_pr_command(
    ctx: typer.Context,
    issues: Annotated[
        list[int],
        typer.Option(
            "--issue",
            "-i",
            help="Source Issue number; repeat this option for each batch member.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Resolve and show the source batch without creating branches or PRs.",
        ),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """将已完成 Issue 的来源 PR 聚合为总 Draft PR，或重试收尾。"""
    return _run_typer_repository_command(
        ctx,
        "pr aggregate",
        repo=repo,
        repo_id=repo_id,
        config=config,
        issues=issues,
        dry_run=dry_run,
    )
