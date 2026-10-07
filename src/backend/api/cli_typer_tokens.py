"""Token 用量 CLI 查询（``kc tokens``）。

只读命令：读取 console 账本（``history_db_path`` 指向的 SQLite），复用
既有聚合口径输出按流程 / 按 agent / 按 PRD（Issue）的 token 汇总，并支持
``--issue`` 下钻到单个 Issue。CLI 内不做任何口径换算——口径唯一事实源在
聚合模块（前置 PRD：
``tasks/archive/P1-FEAT-20260930-212702-agent-token-usage-stats.md``）。
"""

from __future__ import annotations

from dataclasses import asdict
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
from backend.core.use_cases.agent_runner_factory import create_console_store
from backend.core.use_cases.agent_runner_lifecycle import build_prd_lifecycle_stats
from backend.core.use_cases.agent_runner_token_stats import (
    build_token_usage_by_prd,
    build_token_usage_stats_for_issue,
)

#: 表格渲染专用 Rich Console（CliRunner 场景下随 sys.stdout 重定向）。
_rich_console = Console()

#: flow 标识 -> 中文标签；未识别的 flow 原样展示（与前端一致）。
_FLOW_LABELS: dict[str, str] = {
    "implement": "实现",
    "verify": "验证",
    "supervise": "评审",
    "fix": "修复",
    "closeout": "收尾",
}

_DAYS_MIN = 1
_DAYS_MAX = 365


def _clamp_days(days: int) -> int:
    """把天数收敛到合法区间（与 stats 端点同一规则）。"""
    return min(max(days, _DAYS_MIN), _DAYS_MAX)


def _format_token_count(count: int) -> str:
    """紧凑数字格式：千位以上保留一位小数加 ``k``。"""
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


def _cache_hit_rate(input_tokens: int, cache_read: int, cache_creation: int) -> str:
    """缓存命中率文本（命中 ÷ 输入侧实际处理量）；无缓存数据显「—」。"""
    input_side = input_tokens + cache_read + cache_creation
    if input_side <= 0:
        return "—"
    return f"{round(cache_read / input_side * 100)}%"


def _render_group_table(title: str, group_totals: dict) -> Table:
    """渲染一张分组汇总表（按总量降序）。"""
    table = Table(title=title, show_lines=False)
    table.add_column("分组")
    table.add_column("总量", style="bold")
    table.add_column("输入")
    table.add_column("输出")
    table.add_column("缓存读")
    table.add_column("缓存写")
    table.add_column("命中率")
    table.add_column("调用数", justify="right")
    for key, totals in sorted(
        group_totals.items(), key=lambda item: item[1].total_tokens, reverse=True
    ):
        label = _FLOW_LABELS.get(key, key)
        table.add_row(
            label,
            _format_token_count(totals.total_tokens),
            _format_token_count(totals.input_tokens),
            _format_token_count(totals.output_tokens),
            _format_token_count(totals.cache_read_input_tokens),
            _format_token_count(totals.cache_creation_input_tokens),
            _cache_hit_rate(
                totals.input_tokens,
                totals.cache_read_input_tokens,
                totals.cache_creation_input_tokens,
            ),
            str(totals.usage_count),
        )
    return table


def _render_prd_table(entries: list) -> Table:
    """渲染「按 PRD（Issue）」汇总表（按总量降序，与分组表同列口径）。

    PRD 路径只显示文件名（全路径过宽）；未关联 PRD 的 run 显示「—」。
    """
    table = Table(title="按 PRD（Issue）", show_lines=False)
    table.add_column("Issue", justify="right")
    table.add_column("PRD")
    table.add_column("总量", style="bold")
    table.add_column("输入")
    table.add_column("输出")
    table.add_column("缓存读")
    table.add_column("缓存写")
    table.add_column("命中率")
    table.add_column("调用数", justify="right")
    for entry in entries:
        totals = entry.totals
        issue_label = "—" if entry.issue_number is None else f"#{entry.issue_number}"
        prd_label = "—" if not entry.prd_path else PurePosixPath(entry.prd_path).name
        table.add_row(
            issue_label,
            prd_label,
            _format_token_count(totals.total_tokens),
            _format_token_count(totals.input_tokens),
            _format_token_count(totals.output_tokens),
            _format_token_count(totals.cache_read_input_tokens),
            _format_token_count(totals.cache_creation_input_tokens),
            _cache_hit_rate(
                totals.input_tokens,
                totals.cache_read_input_tokens,
                totals.cache_creation_input_tokens,
            ),
            str(totals.usage_count),
        )
    return table


@app.command(name="tokens")
def tokens(
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="仓库标识；省略表示全部仓库。"),
    ] = None,
    days: Annotated[
        int,
        typer.Option("--days", help="时间窗口天数（钳制到 1–365），默认 30。"),
    ] = 30,
    issue: Annotated[
        int | None,
        typer.Option(
            "--issue",
            help="只看某个 Issue（PRD）的消耗：按流程/按 agent 表收窄到该 Issue。",
        ),
    ] = None,
    output: OutputOption = OutputFormat.table,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="输出与 stats 端点同构的 JSON（供脚本消费）。"),
    ] = False,
) -> None:
    """查看 agent 调用的 token 消耗汇总（按流程 / 按 agent / 按 PRD，与 Stats 页同源同口径）。"""
    fmt = output_format_of(output=_enum_value(output), as_json=as_json)
    if fmt == OUTPUT_FORMAT_JSON:
        # 本命令直接从 Typer app emit JSON，不经 cli.py 的中央改绑点，
        # 必须在机器模式下自行把日志改绑到 stderr，否则一条日志就污染 stdout。
        route_logs_to_stderr()
    bounded_days = _clamp_days(days)
    # 可用性探针：建库与裸读都放进保护块——build_prd_lifecycle_stats 对读取
    # 失败静默降级为空数据，这里负责把"账本不可用"与"真的没数据"区分开
    # （verifier MEDIUM：构造函数也必须入保护块，坏库路径才不逃逸 traceback）。
    try:
        store = create_console_store()
        store.list_lifecycle_runs(repo_id=repo_id, since="2000-01-01T00:00:00+00:00")
    except Exception as exc:  # noqa: BLE001 - CLI 需要单行错误而非 traceback。
        exit_code = render_cli_error(
            CliError(
                f"token 查询失败：账本不可用（{exc}）",
                code=ExitCode.GENERAL,
                suggestion="kc logs --lines 200",
            ),
            fmt=fmt,
        )
        raise typer.Exit(code=exit_code) from exc

    if issue is not None:
        usage = build_token_usage_stats_for_issue(
            store=store, repo_id=repo_id, days=bounded_days, issue_number=issue
        )
    else:
        usage = build_prd_lifecycle_stats(
            store=store, repo_id=repo_id, days=bounded_days
        ).token_usage
    prd_entries = build_token_usage_by_prd(
        store=store, repo_id=repo_id, days=bounded_days, issue_number=issue
    )

    if fmt == OUTPUT_FORMAT_JSON:
        emit(
            {
                "repo_id": repo_id,
                "days": bounded_days,
                "issue_number": issue,
                "token_usage": asdict(usage),
                "by_prd": [asdict(entry) for entry in prd_entries],
            },
            fmt=fmt,
        )
        return

    if not usage.by_flow and not usage.by_agent and not prd_entries:
        typer.echo("暂无 token 用量数据（agent 未上报 usage 或所选范围无记录）。")
        return

    typer.echo(
        f"Token 用量汇总（最近 {bounded_days} 天"
        + (f"，仓库 {repo_id}" if repo_id else "，全部仓库")
        + (f"，Issue #{issue}" if issue is not None else "")
        + "；总量 = 输入 + 输出 + 缓存读 + 缓存写）"
    )
    _rich_console.print(_render_group_table("按流程", usage.by_flow))
    _rich_console.print(_render_group_table("按 agent", usage.by_agent))
    _rich_console.print(_render_prd_table(prd_entries))


__all__ = ["tokens"]
