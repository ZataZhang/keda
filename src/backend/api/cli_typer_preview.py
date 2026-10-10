"""Typer commands under ``kc preview``.

按需项目预览的 CLI 门面：三个动作（start / status / stop）都不承载策略，只做参数
声明与 dispatch。**只有用户在执行器对话里明确要求预览时才该调用**——这是随包
operator skill 写死的约束，不是本模块能强制的边界。
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    JsonOutputOption,
    OutputFormat,
    OutputOption,
    RepoIdOption,
    RepoOption,
    _enum_value,
    _run_typer_command,
    _typer_selector_options,
    preview_app,
)

_CONFIRM_HELP = (
    'Exact text of the discovered preview candidate (e.g. --confirm "pnpm run dev"). '
    "Required when [agent_session.preview].argv is not declared."
)


@preview_app.command("start")
def preview_start_command(
    ctx: typer.Context,
    confirm: Annotated[
        str | None,
        typer.Option("--confirm", help=_CONFIRM_HELP),
    ] = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Start the repository's preview dev server (only when the user asked for it)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "preview start",
        **selector_options,
        confirm=confirm,
        output=_enum_value(output),
        as_json=as_json,
    )


@preview_app.command("status")
def preview_status_command(
    ctx: typer.Context,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Report the managed preview process state and its loopback URL (read-only)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "preview status",
        **selector_options,
        output=_enum_value(output),
        as_json=as_json,
    )


@preview_app.command("stop")
def preview_stop_command(
    ctx: typer.Context,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Stop the KedaCode-owned preview process group (never a foreign process)."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "preview stop",
        **selector_options,
        output=_enum_value(output),
        as_json=as_json,
    )


__all__ = [
    "preview_start_command",
    "preview_status_command",
    "preview_stop_command",
]
