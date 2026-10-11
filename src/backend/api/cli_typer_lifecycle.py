"""只读查询 Issue 生命周期的 ``kc lifecycle`` 命令。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    emit,
    output_format_of,
    render_cli_error,
    route_logs_to_stderr,
)
from backend.api.cli_typer_app import OutputFormat, OutputOption, _enum_value, app
from backend.core.shared.models.backlog import PrdLifecycleDetail
from backend.core.use_cases.agent_runner_factory import create_console_store
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    LifecyclePhase,
    LifecycleQuery,
    list_issue_lifecycle_details,
)

#: 表格渲染专用 Rich Console（CliRunner 场景下随 sys.stdout 重定向）。
_rich_console = Console()
_LifecycleMilestoneChoice = Enum(
    "LifecycleMilestoneChoice",
    {
        event.name: event.value
        for event in LifecycleEventType
        if event is not LifecycleEventType.AGENT_TOKEN_USAGE
    },
    type=str,
)


def _render_lifecycle_table(lifecycle_details: Sequence[PrdLifecycleDetail]) -> Table:
    """渲染生命周期列表摘要表。"""
    table = Table(title="Issue 生命周期", show_lines=False)
    table.add_column("仓库")
    table.add_column("Issue", justify="right")
    table.add_column("PRD")
    table.add_column("当前阶段")
    table.add_column("最新事件")
    table.add_column("更新时间")
    table.add_column("账本")
    for lifecycle_detail in lifecycle_details:
        issue_label = (
            "—" if lifecycle_detail.issue_number is None else f"#{lifecycle_detail.issue_number}"
        )
        prd_label = PurePosixPath(lifecycle_detail.prd_path).name or "—"
        last_event = lifecycle_detail.events[-1] if lifecycle_detail.events else None
        table.add_row(
            lifecycle_detail.repo_id,
            issue_label,
            prd_label,
            lifecycle_detail.current_phase,
            last_event.event_type if last_event else "—",
            last_event.occurred_at if last_event else (lifecycle_detail.started_at or "—"),
            "完整" if lifecycle_detail.history_complete else "有缺口",
        )
    return table


def _render_lifecycle_detail(lifecycle_details: Sequence[PrdLifecycleDetail]) -> None:
    """渲染一个或多个 Issue 的生命周期摘要与事件时间线。"""
    for detail_index, lifecycle_detail in enumerate(lifecycle_details):
        if detail_index:
            typer.echo("")
        issue_label = (
            "—" if lifecycle_detail.issue_number is None else f"#{lifecycle_detail.issue_number}"
        )
        lifecycle_outcome = (
            "进行中" if lifecycle_detail.in_progress else lifecycle_detail.outcome or "已结束"
        )
        typer.echo(
            f"{lifecycle_detail.repo_id} {issue_label} · "
            f"{lifecycle_detail.current_phase} · {lifecycle_outcome}"
        )
        typer.echo(f"PRD: {lifecycle_detail.prd_path or '—'}")
        typer.echo("账本: " + ("完整" if lifecycle_detail.history_complete else "存在记录缺口"))
        if not lifecycle_detail.events:
            typer.echo("暂无生命周期事件。")
            continue
        event_table = Table(show_lines=False)
        event_table.add_column("时间")
        event_table.add_column("事件")
        event_table.add_column("阶段")
        event_table.add_column("状态")
        event_table.add_column("来源")
        for lifecycle_event in lifecycle_detail.events:
            event_table.add_row(
                lifecycle_event.occurred_at,
                lifecycle_event.event_type,
                lifecycle_event.phase,
                lifecycle_event.status,
                lifecycle_event.actor,
            )
        _rich_console.print(event_table)


@app.command("lifecycle", help="查看 Issue 当前生命周期，或按里程碑事件筛选。")
def lifecycle_command(
    issue: Annotated[
        int | None,
        typer.Option("--issue", help="只查看指定 Issue 的当前阶段与事件时间线。"),
    ] = None,
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="仓库标识；省略时查询所有本地账本仓库。"),
    ] = None,
    phase: Annotated[
        LifecyclePhase | None,
        typer.Option(
            "--phase",
            help="按当前阶段精确筛选。",
        ),
    ] = None,
    reached: Annotated[
        _LifecycleMilestoneChoice | None,
        typer.Option(
            "--reached",
            help="只显示历史上到达过该里程碑事件的 Issue（含后续阶段）。",
        ),
    ] = None,
    output: OutputOption = OutputFormat.table,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="输出完整生命周期详情 JSON。"),
    ] = False,
) -> int:
    """查看 Issue 当前生命周期，或按阶段与已到达里程碑筛选。

    Args:
        ctx: Typer 命令上下文。
        issue: 可选 Issue 编号。
        repo_id: 可选仓库标识。
        phase: 可选当前阶段精确筛选值。
        reached: 可选历史里程碑事件筛选值。
        output: 人类表格或 JSON 输出格式。
        as_json: 为真时输出完整 JSON 生命周期记录。

    Returns:
        int: 成功返回 ``0``；查询错误通过 CLI 语义退出码退出。
    """
    output_format = output_format_of(output=_enum_value(output), as_json=as_json)
    if output_format == OUTPUT_FORMAT_JSON:
        route_logs_to_stderr()

    lifecycle_query = LifecycleQuery(
        repo_id=repo_id,
        issue_number=issue,
        phase=phase.value if phase is not None else None,
        reached_event=_enum_value(reached) if reached is not None else None,
    )
    try:
        console_store = create_console_store()
        lifecycle_details = list_issue_lifecycle_details(
            store=console_store,
            query=lifecycle_query,
        )
    except ValueError as exc:
        exit_code = render_cli_error(CliError(str(exc), code=ExitCode.USAGE), fmt=output_format)
        raise typer.Exit(code=exit_code) from exc
    except Exception as exc:  # noqa: BLE001 - CLI 输出单行错误，不泄露 traceback。
        exit_code = render_cli_error(
            CliError(
                f"生命周期查询失败：本地账本不可用（{exc}）",
                code=ExitCode.GENERAL,
                suggestion="kc logs --lines 200",
            ),
            fmt=output_format,
        )
        raise typer.Exit(code=exit_code) from exc

    if output_format == OUTPUT_FORMAT_JSON:
        emit(
            {
                "repo_id": repo_id,
                "issue_number": issue,
                "filters": {
                    "phase": phase.value if phase is not None else None,
                    "reached": _enum_value(reached) if reached is not None else None,
                },
                "lifecycles": [asdict(lifecycle_detail) for lifecycle_detail in lifecycle_details],
            },
            fmt=output_format,
        )
        return 0

    if not lifecycle_details:
        if issue is not None:
            typer.echo(f"本地账本没有 Issue #{issue} 的生命周期记录。")
        else:
            typer.echo("没有符合条件的 Issue 生命周期记录。")
        return 0

    if issue is not None:
        _render_lifecycle_detail(lifecycle_details)
    else:
        _rich_console.print(_render_lifecycle_table(lifecycle_details))
    return 0


__all__ = ["lifecycle_command"]
