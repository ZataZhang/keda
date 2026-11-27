"""跨执行器的按需浏览器动作入口。"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    JsonOutputOption,
    OutputFormat,
    OutputOption,
    _enum_value,
    _run_typer_command,
    app,
)


@app.command("browser", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def browser_command(
    arguments: Annotated[list[str], typer.Argument(help="Browser action and its arguments.")],
    filename: Annotated[
        str | None, typer.Option(help="Screenshot output inside this worktree.")
    ] = None,
    full_page: Annotated[
        bool, typer.Option("--full-page", help="Capture the full scrollable page.")
    ] = False,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """按需操作当前调用的浏览器。支持 open、goto、snapshot、find、click、fill、type、
    press、hover、check、uncheck、select、resize、reload、go-back、go-forward、tab-list、
    tab-new、tab-select、tab-close、dialog-accept、dialog-dismiss、screenshot、status、close。
    screenshot 支持 --filename <工作树内路径> 与 --full-page。仅 kc 管理的可写 Agent
    调用拥有宿主通道；首次 open 启动浏览器，调用结束自动回收。
    """
    forwarded = arguments.copy()
    if filename is not None:
        forwarded.extend(["--filename", filename])
    if full_page:
        forwarded.append("--full-page")
    return _run_typer_command(
        "browser", browser_args=forwarded, output=_enum_value(output), as_json=as_json
    )
