"""Typer 命令：``kc schema``（只读运行时自省）。

单一命令，直接从根 Typer app 的真实 click 命令树派生，不维护第二份命令表：
agent 需要先问"这条命令接受什么参数、哪些是枚举、默认是什么"时读它，而不是
读可能过期的文档。
"""

from __future__ import annotations


from backend.api.cli_console import console
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    OutputFormat,
    emit,
    output_format_of,
    route_logs_to_stderr,
)
from backend.api.cli_schema import build_command_schema, render_schema_human
from backend.api.cli_typer_app import JsonOutputOption, OutputOption, app


@app.command("schema")
def schema_command(
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """Introspect the real command tree: commands, options, types, enums, defaults."""
    fmt = output_format_of(output=output, as_json=as_json)
    if fmt == OUTPUT_FORMAT_JSON:
        # 本命令直接从 Typer app emit JSON，不经 cli.py 的中央改绑点，
        # 必须在机器模式下自行把日志改绑到 stderr，否则一条日志就污染 stdout。
        route_logs_to_stderr()
    schema = build_command_schema(app)
    emit(
        schema,
        fmt=fmt,
        human_renderer=lambda: console.print(
            render_schema_human(schema), markup=False, soft_wrap=True
        ),
    )
    return 0


__all__ = ["schema_command"]
