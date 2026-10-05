"""Typer commands for the agent-runner flow.

Holds the runner-side commands:

- :func:`run_command` (top-level ``iar run``)
- :func:`logs_command` (top-level ``iar logs``) — placed between
  ``run_command`` and ``review_command`` so the historical ``iar --help``
  command order is preserved.
- :func:`review_command` (top-level ``iar review``)
- :func:`review_daemon_command` (top-level ``iar review-daemon``)
- ``daemon_callback`` (default for ``iar daemon`` without subcommand)
- :func:`daemon_run_command` and :func:`daemon_status_command` (under
  ``iar daemon``)

Plus the ``_run_runner_command`` / ``_run_daemon_command`` helpers used
by every command in this module.

The top-level ``iar loop-daemon`` command lives in
:mod:`backend.api.cli_typer_loop` so the historical ``iar --help``
command order is preserved.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError, output_format_of, render_cli_error
from backend.api.cli_typer_app import (
    AllRepositoriesOption,
    ConfigOption,
    ConcurrencyOption,
    DaemonIntervalOption,
    JsonOutputOption,
    LogsKindChoice,
    MaxIssuesOption,
    ModelIdOption,
    ModelPresetOption,
    OutputFormat,
    OutputOption,
    ReasoningEffortOption,
    RepoIdOption,
    RepoOption,
    RunAgentChoice,
    RunAgentOption,
    _enum_value,
    _run_typer_command,
    _run_typer_repository_command,
    _typer_preset_options,
    _typer_selector_options,
    app,
    daemon_app,
)


def _run_runner_command(
    ctx: typer.Context,
    *,
    command: str,
    dry_run: bool,
    agent: RunAgentChoice,
    max_issues: int | None,
    repo: str | None,
    repo_id: str | None,
    config: str | None,
    all_repositories: bool,
    preset: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    output: str | None = None,
    as_json: bool = False,
    **extra_kwargs: Any,
) -> int:
    """Run `run` or `review` through the shared dispatch path."""
    return _run_typer_repository_command(
        ctx,
        command,
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
        agent=_enum_value(agent),
        max_issues=max_issues,
        **_typer_preset_options(preset=preset, model=model, reasoning_effort=reasoning_effort),
        all_repositories=all_repositories,
        output=output,
        as_json=as_json,
        **extra_kwargs,
    )


@app.command("run")
def run_command(
    ctx: typer.Context,
    prd_path: Annotated[
        str | None,
        typer.Argument(
            metavar="[PRD_PATH]",
            help="Target PRD path: runs the Issue linked via the PRD's "
            "'- GitHub Issue:' line. Pass --issue, a PRD path, or --all-ready.",
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview only.")] = False,
    issue: Annotated[
        int | None,
        typer.Option(
            "--issue",
            metavar="N",
            help="Target Issue number: only that Issue is processed this pass.",
        ),
    ] = None,
    all_ready: Annotated[
        bool,
        typer.Option(
            "--all-ready",
            help="Process the ready queue by priority (the historical iar run behavior).",
        ),
    ] = False,
    takeover: Annotated[
        bool,
        typer.Option(
            "--takeover",
            help="When a daemon already serves the repository, stop it gracefully, "
            "reclaim its in-flight Issues, then run. Destructive: interrupts ALL "
            "of the daemon's in-flight Issues.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the takeover confirmation prompt (required with --json)."),
    ] = False,
    fast_merge: Annotated[
        bool,
        typer.Option(
            "--fast-merge",
            help="Fast track for THIS run only: after the builder commits, skip the "
            "validation gates (rv re-exec + independent verifier) and publish the "
            "Draft PR annotated as unverified. Requires a single target "
            "(--issue or a PRD path); Issues with a declared stack dependency "
            "are rejected.",
        ),
    ] = False,
    agent: RunAgentOption = RunAgentChoice.auto,
    max_issues: MaxIssuesOption = None,
    preset: ModelPresetOption = None,
    model: ModelIdOption = None,
    reasoning_effort: ReasoningEffortOption = None,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> int:
    """Run one agent-runner polling cycle (a target is required)."""
    return _run_runner_command(
        ctx,
        command="run",
        dry_run=dry_run,
        agent=agent,
        max_issues=max_issues,
        repo=repo,
        repo_id=repo_id,
        config=config,
        all_repositories=all_repositories,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
        output=_enum_value(output),
        as_json=as_json,
        prd_path=prd_path,
        issue=issue,
        all_ready=all_ready,
        takeover=takeover,
        yes=yes,
        fast_merge=fast_merge,
    )


@app.command("logs")
def logs_command(
    ctx: typer.Context,
    kind: Annotated[
        LogsKindChoice,
        typer.Option("--kind", help="Process kind to tail: daemon or review_daemon."),
    ] = LogsKindChoice.daemon,
    lines: Annotated[
        int,
        typer.Option("--lines", "-n", help="Number of recent lines to print."),
    ] = 200,
    issue: Annotated[
        int | None,
        typer.Option(
            "--issue",
            help="Read the per-Issue agent output log for the given Issue number "
            "(mutually exclusive with --kind).",
        ),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("-f", "--follow", help="Follow log output continuously."),
    ] = False,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """Print the most recent log lines for a managed daemon process.

    By default the command targets the daemon for the repository inferred
    from the current working directory.  Pass --kind review_daemon to tail
    the review-daemon log instead.  Pass --issue <N> to read the per-Issue
    agent output log for one Issue instead of a process log.
    """
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    fmt = output_format_of(output=_enum_value(output), as_json=as_json)
    # Typer 的默认值与显式传参无法区分；``--issue`` 与 ``--kind`` 互斥，
    # 用 ``--issue is not None`` 结合 ``kind != default`` 判断是否冲突，
    # 避免把默认 ``daemon`` 当成用户显式选择。
    kind_explicit = kind != LogsKindChoice.daemon
    if issue is not None and kind_explicit:
        return render_cli_error(
            CliError(
                "--issue and --kind are mutually exclusive: --issue reads the per-Issue "
                "agent output log, --kind selects a daemon process log.",
                code=ExitCode.USAGE,
                suggestion="iar logs --issue 1",
            ),
            fmt=fmt,
        )
    return _run_typer_command(
        "logs",
        **selector_options,
        kind=_enum_value(kind),
        lines=lines,
        follow=follow,
        issue=issue,
        kind_explicit=kind_explicit,
        output=_enum_value(output),
        as_json=as_json,
    )


@app.command("review")
def review_command(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview only.")] = False,
    agent: RunAgentOption = RunAgentChoice.auto,
    max_issues: MaxIssuesOption = None,
    preset: ModelPresetOption = None,
    model: ModelIdOption = None,
    reasoning_effort: ReasoningEffortOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> int:
    """Run one supervisor review polling cycle."""
    return _run_runner_command(
        ctx,
        command="review",
        dry_run=dry_run,
        agent=agent,
        max_issues=max_issues,
        repo=repo,
        repo_id=repo_id,
        config=config,
        all_repositories=all_repositories,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def _run_daemon_command(
    ctx: typer.Context,
    *,
    command: str,
    interval: int | None,
    agent: RunAgentChoice,
    max_issues: int | None,
    repo: str | None,
    repo_id: str | None,
    config: str | None,
    all_repositories: bool,
    concurrency: int | None = None,
    autopilot_override: bool | None = None,
    preset: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> int:
    """Run daemon or review-daemon through the shared dispatch path."""
    return _run_typer_repository_command(
        ctx,
        command,
        repo=repo,
        repo_id=repo_id,
        config=config,
        interval=interval,
        agent=_enum_value(agent),
        max_issues=max_issues,
        all_repositories=all_repositories,
        concurrency=concurrency,
        autopilot_override=autopilot_override,
        **_typer_preset_options(preset=preset, model=model, reasoning_effort=reasoning_effort),
    )


@daemon_app.callback(invoke_without_command=True)
def daemon_callback(
    ctx: typer.Context,
    interval: DaemonIntervalOption = None,
    agent: RunAgentOption = RunAgentChoice.auto,
    max_issues: MaxIssuesOption = None,
    concurrency: ConcurrencyOption = None,
    autopilot: Annotated[
        bool | None,
        typer.Option(
            "--autopilot/--no-autopilot",
            help="Enable/disable the scheduling autopilot for this daemon run, "
            "overriding autopilot.enabled. Scheduling only: it never arms "
            "auto-merge (that stays behind the safety.auto_merge config switch).",
        ),
    ] = None,
    preset: ModelPresetOption = None,
    model: ModelIdOption = None,
    reasoning_effort: ReasoningEffortOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> None:
    """Backward-compatible default: `iar daemon` runs the daemon."""
    if ctx.invoked_subcommand is not None:
        return
    exit_code = _run_daemon_command(
        ctx,
        command="daemon",
        interval=interval,
        agent=agent,
        max_issues=max_issues,
        repo=repo,
        repo_id=repo_id,
        config=config,
        all_repositories=all_repositories,
        concurrency=concurrency,
        autopilot_override=autopilot,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    raise typer.Exit(code=exit_code)


@daemon_app.command("run")
def daemon_run_command(
    ctx: typer.Context,
    interval: DaemonIntervalOption = None,
    agent: RunAgentOption = RunAgentChoice.auto,
    max_issues: MaxIssuesOption = None,
    concurrency: ConcurrencyOption = None,
    autopilot: Annotated[
        bool | None,
        typer.Option(
            "--autopilot/--no-autopilot",
            help="Enable/disable the scheduling autopilot for this daemon run, "
            "overriding autopilot.enabled. Scheduling only: it never arms "
            "auto-merge (that stays behind the safety.auto_merge config switch).",
        ),
    ] = None,
    preset: ModelPresetOption = None,
    model: ModelIdOption = None,
    reasoning_effort: ReasoningEffortOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> int:
    """Run the agent runner continuously.

    Defaults to the current initialized repository; pass --all to target
    every enabled registry entry instead.
    """
    return _run_daemon_command(
        ctx,
        command="daemon",
        interval=interval,
        agent=agent,
        max_issues=max_issues,
        repo=repo,
        repo_id=repo_id,
        config=config,
        all_repositories=all_repositories,
        concurrency=concurrency,
        autopilot_override=autopilot,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
    )


@daemon_app.command("status")
def daemon_status_command(
    ctx: typer.Context,
    output: OutputOption = OutputFormat.table,
    as_json: JsonOutputOption = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> int:
    """Show running daemon and review-daemon processes."""
    selector_options = _typer_selector_options(ctx, repo=repo, repo_id=repo_id, config=config)
    return _run_typer_command(
        "daemon",
        daemon_command="status",
        all_repositories=all_repositories,
        output=_enum_value(output),
        as_json=as_json,
        **selector_options,
    )


@app.command("review-daemon")
def review_daemon_command(
    ctx: typer.Context,
    interval: DaemonIntervalOption = None,
    agent: RunAgentOption = RunAgentChoice.auto,
    max_issues: MaxIssuesOption = None,
    preset: ModelPresetOption = None,
    model: ModelIdOption = None,
    reasoning_effort: ReasoningEffortOption = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
    all_repositories: AllRepositoriesOption = False,
) -> int:
    """Run supervisor review continuously.

    Defaults to the current initialized repository; pass --all to target
    every enabled registry entry instead.
    """
    return _run_daemon_command(
        ctx,
        command="review-daemon",
        interval=interval,
        agent=agent,
        max_issues=max_issues,
        repo=repo,
        repo_id=repo_id,
        config=config,
        all_repositories=all_repositories,
        preset=preset,
        model=model,
        reasoning_effort=reasoning_effort,
    )


# Re-export the ``_typer_selector_options`` helper so ``daemon_status_command``
# can reach it through the same import path the original ``cli_typer`` module
# exposed.

__all__ = [
    "_run_daemon_command",
    "_run_runner_command",
    "daemon_callback",
    "daemon_run_command",
    "daemon_status_command",
    "review_command",
    "review_daemon_command",
    "run_command",
]
