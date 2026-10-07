"""``kc init``, ``kc workflow install``, and ``kc takeover`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher. Each handler takes a :class:`ParsedCommandContext` and
returns an int exit code.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import _handle_not_initialized_error
from backend.api.cli_init import (
    _print_workflow_config_plan,
    _run_init_command,
)
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_takeover import _run_takeover_command
from backend.core.use_cases.agent_runner_factory import logger
from backend.core.use_cases.agent_runner_init_assets import (
    ExistingFileRefusedError,
    UnknownWorkflowError,
    WorkflowInstallOptions,
    install_workflow,
)
from backend.core.use_cases.agent_runner_repository_local import (
    IARRepositoryNotInitializedError,
)


def run_init_command(ctx: ParsedCommandContext) -> int:
    """``kc init``: create repository-local .kedacode.toml config."""
    if ctx.repo_id is not None or ctx.repo_override is not None:
        raise CliError(
            "kc init uses the current Git repository; omit --repo/--repo-id.",
            code=ExitCode.USAGE,
            suggestion="kc init",
        )
    return _run_init_command(ctx.parsed, ctx.process_runner)


def run_workflow_install_command(ctx: ParsedCommandContext) -> int:
    """``kc workflow install``: bundle a workflow template into the repo."""
    if ctx.repo_id is not None or ctx.repo_override is not None or ctx.parsed.config is not None:
        raise CliError(
            "kc workflow install uses the current Git repository; "
            "omit --repo/--repo-id/--config.",
            code=ExitCode.USAGE,
            suggestion="kc workflow install --help",
        )
    try:
        install_result = install_workflow(
            WorkflowInstallOptions(
                cwd=Path.cwd(),
                name=ctx.parsed.name,
                force=ctx.parsed.force,
                dry_run=ctx.parsed.dry_run,
            ),
            ctx.process_runner,
        )
    except UnknownWorkflowError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.NOT_FOUND,
            suggestion="kc workflow install --help",
        ) from exc
    except ExistingFileRefusedError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.CONFLICT,
            suggestion=f"kc workflow install {ctx.parsed.name} --force",
        ) from exc
    except IARRepositoryNotInitializedError as exc:
        return _handle_not_initialized_error(exc, fmt=ctx.output_format)
    except ValueError as exc:
        logger.error("kc workflow install failed: %s", exc)
        return 1
    if ctx.parsed.dry_run:
        console.print("[cyan]Would install workflow:[/] %s" % install_result.name)
        for plan in install_result.template_file_plans:
            marker = (
                "[yellow]would overwrite[/]" if plan.exists_on_disk else "[green]would write[/]"
            )
            console.print("  %s %s (%d bytes)" % (marker, plan.target_path, plan.bytes_to_write))
        _print_workflow_config_plan(install_result.config_toml_plan, dry_run=True)
        return 0

    for plan in install_result.template_file_plans:
        if plan.exists_on_disk and install_result.refused_template_paths:
            continue
        console.print(
            "%s %s"
            % (
                "[green]Wrote[/]" if not plan.exists_on_disk else "[yellow]Overwrote[/]",
                plan.target_path,
            )
        )
    _print_workflow_config_plan(install_result.config_toml_plan, dry_run=False)
    return 0


def run_takeover_command(ctx: ParsedCommandContext) -> int:
    """``kc takeover``: bulk import + register GitHub repositories."""
    return _run_takeover_command(ctx.parsed, ctx.process_runner)


__all__ = ["run_init_command", "run_takeover_command", "run_workflow_install_command"]
