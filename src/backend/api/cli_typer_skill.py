"""Typer commands under ``kc skill``.

Holds :func:`skill_install_command`, the user-level Skill installer/refresh
entry that works independently of the repository-local config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    RepoIdOption,
    RepoOption,
    _run_typer_command,
    _typer_selector_options,
    skill_app,
)


@skill_app.command("install")
def skill_install_command(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Replace user-modified skills and delete legacy-named copies outright.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the install plan for every skill root without writing anything.",
        ),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Install or refresh the packaged and remote-template skills in all user roots."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command("skill install", **selector_options, force=force, dry_run=dry_run)


@skill_app.command("repair-operator")
def skill_repair_operator_command(
    ctx: typer.Context,
    skill_dir: Annotated[
        Path,
        typer.Option(
            "--skill-dir",
            help="Exact absolute path to one installed kedacode-operator skill directory.",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite changed managed files in this directory only.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print this directory's repair plan without writing."),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """只修复一个随包 operator Skill 目录，不触碰其它安装根。"""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "skill repair-operator",
        **selector_options,
        skill_dir=str(skill_dir.expanduser()),
        force=force,
        dry_run=dry_run,
    )


__all__ = ["skill_install_command", "skill_repair_operator_command"]
