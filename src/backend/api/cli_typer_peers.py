"""Typer 机器状态查询命令。"""

from __future__ import annotations

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    JsonOutputOption,
    OutputOption,
    RepoIdOption,
    RepoOption,
    _enum_value,
    _run_typer_repository_command,
    app,
)


@app.command("peers")
def peers_command(
    ctx: typer.Context,
    output: OutputOption = "table",
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """显示选定仓库中的机器状态与认领持有者。"""
    return _run_typer_repository_command(
        ctx,
        "peers",
        repo=repo,
        repo_id=repo_id,
        config=config,
        output=_enum_value(output),
        as_json=as_json,
    )


__all__ = ["peers_command"]
