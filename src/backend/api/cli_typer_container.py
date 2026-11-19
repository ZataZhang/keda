"""Typer commands under ``kc container``.

本模块只负责 Typer 参数解析与命令分发，**不**直接 import ``backend.engines.*``
（保持 ``api → core`` 依赖方向）；具体编排通过 ``_run_typer_command`` 进入
:mod:`backend.api.cli_parsed_commands.container`，由那里的 core facade 真正
调用 engines 模块。
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    RepoIdOption,
    RepoOption,
    _run_typer_command,
    _typer_selector_options,
    auth_app,
    container_app,
)


@auth_app.command("import")
def container_auth_import_command(
    ctx: typer.Context,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Snapshot host agent CLI auth + skills into ``~/.kedacode/container-auth/``."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "container auth import",
        **selector_options,
    )


@container_app.command("up")
def container_up_command(
    ctx: typer.Context,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help="Absolute path to the target Git repository to mount.",
        ),
    ] = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    gh_token: Annotated[
        str | None,
        typer.Option(
            "--gh-token",
            help="GitHub token passed to the container's gh CLI. Defaults to $GH_TOKEN.",
        ),
    ] = None,
    build: Annotated[
        bool,
        typer.Option(
            "--build",
            help="Pass --build to `docker compose up` to rebuild the runner image.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the docker compose plan without invoking Docker.",
        ),
    ] = False,
) -> int:
    """Start the KedaCode runner container for the target repository."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "container up",
        **selector_options,
        gh_token=gh_token,
        build=build,
        dry_run=dry_run,
    )


@container_app.command("down")
def container_down_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the docker compose plan without invoking Docker.",
        ),
    ] = False,
) -> int:
    """Stop and remove the KedaCode runner container."""
    return _run_typer_command("container down", config=None, dry_run=dry_run)


@container_app.command("logs")
def container_logs_command(
    ctx: typer.Context,
    no_follow: Annotated[
        bool,
        typer.Option(
            "--no-follow",
            help="Dump existing logs and exit instead of streaming.",
        ),
    ] = False,
) -> int:
    """Stream the KedaCode runner container's logs to the terminal."""
    return _run_typer_command("container logs", config=None, follow=not no_follow)


@container_app.command("gc")
def container_gc_command(
    ctx: typer.Context,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Delete only the resources reported as eligible."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview cleanup without deleting (the default)."),
    ] = False,
    cache_retention_days: Annotated[
        int,
        typer.Option(
            "--cache-retention-days", min=1, help="Keep build cache used within this many days."
        ),
    ] = 90,
) -> int:
    """Preview or explicitly apply safe host Docker image and cache cleanup."""
    return _run_typer_command(
        "container gc",
        config=None,
        apply=apply,
        dry_run=dry_run,
        cache_retention_days=cache_retention_days,
    )


__all__ = [
    "container_auth_import_command",
    "container_down_command",
    "container_gc_command",
    "container_logs_command",
    "container_up_command",
]
