"""Typer commands under ``kc evidence``.

只读自检入口：按交付门禁同一份判定（manifest 解析、复跑超时、输出断言）在本地
复跑 ``evidence.json`` 中的命令，把全部失败一次列出并给出断言差在哪个词。
不修改证据、代码或断言，也不改变门禁的通过判据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    CliError,
    OutputFormat,
    emit,
    output_format_of,
    render_cli_error,
    route_logs_to_stderr,
)
from backend.api.cli_typer_app import JsonOutputOption, OutputOption, evidence_app
from backend.core.use_cases.agent_runner_evidence_check import (
    EvidenceCheckRequest,
    EvidenceDirectoryResolutionError,
    resolve_check_evidence_dir,
    run_evidence_check,
)
from backend.core.use_cases.agent_runner_factory import (
    create_process_runner,
    get_agent_runner_settings,
    resolve_repository_targets,
)


@evidence_app.command("check")
def evidence_check_command(
    evidence: Annotated[
        str | None,
        typer.Option(
            "--evidence",
            help="Evidence directory holding evidence.json (default: the only "
            "candidate under the configured evidence dir).",
        ),
    ] = None,
    worktree: Annotated[
        str | None,
        typer.Option("--worktree", help="Worktree to re-run commands in (default: cwd)."),
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option(
            "--timeout",
            help="Local per-command timeout in seconds for fast iteration; "
            "must not exceed the gate limit.",
        ),
    ] = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
) -> int:
    """Re-run evidence commands locally; list every failure in one pass."""
    output_format = output_format_of(output=output, as_json=as_json)
    if output_format == OUTPUT_FORMAT_JSON:
        route_logs_to_stderr()

    worktree_path = Path(worktree).expanduser().resolve() if worktree else Path.cwd()
    if not worktree_path.is_dir():
        return render_cli_error(
            CliError(
                f"Worktree `{worktree_path}` is not a directory.",
                code=ExitCode.USAGE,
                suggestion="kc evidence check --worktree <path>",
            ),
            fmt=output_format,
        )

    # 与交付门禁一致：使用仓库级合并后的有效配置（.kedacode.toml 可覆盖
    # evidence_dir / reexecute_timeout_seconds），而不是全局配置视图。
    try:
        repository_contexts = resolve_repository_targets(
            get_agent_runner_settings(),
            fallback_path=str(worktree_path),
        )
    except ValueError as resolution_error:
        return render_cli_error(
            CliError(str(resolution_error), code=ExitCode.USAGE),
            fmt=output_format,
        )
    app_config = repository_contexts[0].config
    gate_timeout_seconds = app_config.validation.reexecute_timeout_seconds
    effective_timeout_seconds = gate_timeout_seconds if timeout is None else timeout
    if effective_timeout_seconds < 1 or effective_timeout_seconds > gate_timeout_seconds:
        return render_cli_error(
            CliError(
                f"--timeout must be between 1 and {gate_timeout_seconds} seconds "
                f"(the delivery gate limit); got {effective_timeout_seconds}.",
                code=ExitCode.USAGE,
                suggestion=f"kc evidence check --timeout {gate_timeout_seconds}",
            ),
            fmt=output_format,
        )

    try:
        evidence_dir = resolve_check_evidence_dir(
            worktree_path,
            app_config,
            explicit_evidence=evidence,
        )
    except EvidenceDirectoryResolutionError as resolution_error:
        return render_cli_error(
            CliError(
                str(resolution_error),
                code=ExitCode.USAGE,
                suggestion="kc evidence check --evidence <dir>",
            ),
            fmt=output_format,
        )

    report = run_evidence_check(
        worktree_path,
        app_config,
        create_process_runner(),
        EvidenceCheckRequest(
            evidence_dir=evidence_dir,
            timeout_seconds=effective_timeout_seconds,
            gate_timeout_seconds=gate_timeout_seconds,
        ),
    )
    emit(
        report.to_payload(),
        fmt=output_format,
        human_renderer=lambda: console.print(
            report.render_human(), markup=False, highlight=False, soft_wrap=True
        ),
    )
    return int(ExitCode.SUCCESS) if report.passed else int(ExitCode.GENERAL)


__all__ = ["evidence_check_command"]
