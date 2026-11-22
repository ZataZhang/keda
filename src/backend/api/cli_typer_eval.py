"""本地评测的原生 kc 命令，校准、运行和报告共用同一用例。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode, translate_exit_code
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    OutputFormat,
    emit,
    output_format_of,
    render_cli_error,
    route_logs_to_stderr,
)
from backend.api.cli_typer_app import JsonOutputOption, OutputOption, eval_app
from backend.core.shared.models.platform_support import UnsupportedPlatformFeatureError
from backend.core.use_cases.model_benchmark.matrix import (
    DEFAULT_PRESET,
    DEFAULT_SUITE,
    BenchmarkInputError,
    BenchmarkRequest,
    list_benchmark_suites,
    run_benchmark,
    summarize,
)

RepoPath = Annotated[
    Path | None, typer.Option("--repo", help="评测集所在 Git 检出，默认当前目录。")
]
Cases = Annotated[list[str] | None, typer.Option("--case", help="按题号选择，可重复指定。")]
Suite = Annotated[str, typer.Option("--suite", help="评测集 ID。")]
OutputDirectory = Annotated[Path, typer.Option("--output-dir", help="新建的本地评测产物目录。")]
Concurrency = Annotated[int, typer.Option("--concurrency", min=1, max=4, help="同时执行的格数。")]

_ERRORS = (
    OSError,
    ValueError,
    LookupError,
    BenchmarkInputError,
    UnsupportedPlatformFeatureError,
    subprocess.CalledProcessError,
)


def _eval_error(failure: Exception, fmt: str) -> int:
    if isinstance(failure, BenchmarkInputError):
        code = {
            "not_found": ExitCode.NOT_FOUND,
            "conflict": ExitCode.CONFLICT,
            "invalid": ExitCode.USAGE,
        }.get(failure.kind, ExitCode.GENERAL)
    elif isinstance(failure, (ValueError, LookupError, UnsupportedPlatformFeatureError)):
        code = ExitCode.USAGE
    else:
        code = ExitCode(translate_exit_code(failure))
    return render_cli_error(CliError(str(failure), code=code), fmt=fmt)


def _output_format(output: OutputFormat, as_json: bool) -> str:
    fmt = output_format_of(output=output, as_json=as_json)
    if fmt == OUTPUT_FORMAT_JSON:
        route_logs_to_stderr()
    return fmt


def _show_summary(summary: dict, output_dir: Path, fmt: str) -> None:
    def render() -> None:
        table = Table("组别", "有效判分", "补丁通过", "通过且交付", "中位秒")
        for arm, counts in summary["arms"].items():
            seconds = counts["median_seconds"]
            table.add_row(
                arm,
                f"{counts['valid_scores']}/{counts['completed_cells']}",
                str(counts["accepted_patches"]),
                str(counts["delivered_and_accepted"]),
                f"{seconds:.1f}" if seconds is not None else "—",
            )
        console.print(table)
        typer.echo(f"结果 {summary['completed_cells']}/{summary['planned_cells']} 格")
        typer.echo(f"报告 {output_dir / 'report.md'}")

    emit({**summary, "output_dir": str(output_dir)}, fmt=fmt, human_renderer=render)


def _run_matrix(request: BenchmarkRequest, fmt: str) -> int:
    def progress(row: dict) -> None:
        detail = row["error"] or f"补丁通过={row['accepted']}"
        typer.echo(f"{row['case']} · {row['arm']} · 第 {row['repeat']} 次 · {detail}", err=True)

    try:
        summary, successful = run_benchmark(
            request, progress=None if fmt == OUTPUT_FORMAT_JSON else progress
        )
    except _ERRORS as failure:
        return _eval_error(failure, fmt)
    _show_summary(summary, request.output.expanduser().resolve(), fmt)
    if not successful:
        return render_cli_error(
            CliError(
                "校准未通过，请查看逐格结果"
                if request.mode == "calibrate"
                else "评测有缺失或判分故障，请查看逐格结果"
            ),
            fmt=fmt,
        )
    return int(ExitCode.SUCCESS)


@eval_app.command("suites")
def eval_suites_command(
    repo: RepoPath = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """列出当前检出的评测集及其校验状态。"""
    fmt = _output_format(output, as_json)
    try:
        suites = list_benchmark_suites(repo or Path.cwd())
    except _ERRORS as failure:
        return _eval_error(failure, fmt)

    def render() -> None:
        table = Table("题库", "版本", "题数", "状态")
        for suite in suites:
            table.add_row(
                suite.get("suite_id", suite["file"]),
                suite.get("version", "—"),
                str(len(suite.get("cases", []))),
                "格式有效" if suite["valid"] else "；".join(suite["errors"]),
            )
        console.print(table)

    emit({"suites": suites}, fmt=fmt, human_renderer=render)
    return int(ExitCode.SUCCESS)


@eval_app.command("calibrate")
def eval_calibrate_command(
    output_dir: OutputDirectory,
    suite: Suite = DEFAULT_SUITE,
    case: Cases = None,
    concurrency: Concurrency = 3,
    repo: RepoPath = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """对每题各跑三次空补丁和参考补丁，不调用模型。"""
    return _run_matrix(
        BenchmarkRequest(
            mode="calibrate",
            repo=repo or Path.cwd(),
            output=output_dir,
            suite=suite,
            cases=tuple(case or ()),
            concurrency=concurrency,
        ),
        _output_format(output, as_json),
    )


@eval_app.command("run")
def eval_run_command(
    output_dir: OutputDirectory,
    calibration: Annotated[Path, typer.Option("--calibration", help="已完成的校准产物目录。")],
    live: Annotated[bool, typer.Option("--live", help="明确启动真实模型调用。")] = False,
    suite: Suite = DEFAULT_SUITE,
    case: Cases = None,
    preset: Annotated[
        str, typer.Option("--preset", help="三组使用的 Codex 实现预设。")
    ] = DEFAULT_PRESET,
    review_preset: Annotated[
        str | None, typer.Option("--review-preset", help="增加一组使用此预设的审查对照。")
    ] = None,
    seconds: Annotated[int, typer.Option("--seconds", min=1, help="每格共享的总执行秒数。")] = 1200,
    concurrency: Concurrency = 3,
    repeats: Annotated[int, typer.Option("--repeats", min=1, max=3, help="每题每组重复次数。")] = 1,
    repo: RepoPath = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """运行原生 Codex、Keda 无审查和 Keda 有审查的等时限对照。"""
    return _run_matrix(
        BenchmarkRequest(
            mode="run",
            repo=repo or Path.cwd(),
            output=output_dir,
            suite=suite,
            cases=tuple(case or ()),
            concurrency=concurrency,
            seconds=seconds,
            repeats=repeats,
            calibration=calibration,
            live=live,
            preset=preset,
            review_preset=review_preset,
        ),
        _output_format(output, as_json),
    )


@eval_app.command("report")
def eval_report_command(
    run_dir: Annotated[Path, typer.Argument(help="已有的评测或校准产物目录。")],
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """从已落盘的记录重新生成 Markdown 与 JSON 报告。"""
    fmt = _output_format(output, as_json)
    root = run_dir.expanduser().resolve()
    try:
        summary = summarize(root)
    except _ERRORS as failure:
        return _eval_error(failure, fmt)
    _show_summary(summary, root, fmt)
    return int(ExitCode.SUCCESS)
