"""``iar roadmap advance`` handler.

Runs exactly one continuous-scheduling pass for a single target repository:
reconcile finished/failed queue entries, then promote queued PRDs (and PRDs
newly discovered in ``tasks/pending/``) up to ``max_parallel``.

This is the manual entry point for the same logic the fast-lane daemon runs
every pass, so ``--dry-run`` doubles as the "what would the next pass do?"
probe.
"""

from __future__ import annotations

from backend.api.cli_console import console, error_console
from backend.api.cli_helpers import _resolve_cli_repository_targets
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api import cli as _cli


def _print_advance_report(report) -> None:
    """Print a human-readable summary of one scheduling pass.

    Args:
        report: The ``RoadmapAdvanceReport`` returned by the use case.
    """
    mode = "dry-run" if report.dry_run else "applied"
    console.print(f"[bold]roadmap advance[/] ({mode}) repo={report.repo_id}")
    console.print(
        f"max_parallel={report.max_parallel} free_slots={report.free_slots} "
        f"running_after={report.max_parallel - report.free_slots}"
    )
    if report.reconciled_completed:
        console.print(f"[green]completed[/] {report.reconciled_completed}")
    if report.reconciled_failed:
        console.print(f"[red]failed (parked)[/] {report.reconciled_failed}")
    for item in report.started:
        issue_reference = (
            f"#{item.issue_number}" if item.issue_number is not None else "(new Issue)"
        )
        action = "would promote" if report.dry_run else "promoted"
        console.print(f"[cyan]{action}[/] {item.prd_path} -> {issue_reference}")
    if report.queued:
        console.print(f"[yellow]queued[/] {report.queued}")
    for entry in report.skipped:
        console.print(f"[red]skipped[/] {entry}")
    if not any(
        (
            report.reconciled_completed,
            report.reconciled_failed,
            report.started,
            report.queued,
            report.skipped,
        )
    ):
        console.print("[dim]nothing to do[/]")


def run_roadmap_advance_command(ctx: ParsedCommandContext) -> int:
    """``iar roadmap advance``: run one scheduling pass, optionally dry-run."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        error_console.print("[red]roadmap advance requires exactly one target repository.[/]")
        error_console.print("Use --repo or --repo-id to select it.")
        return 1

    context = contexts[0]
    dry_run = bool(getattr(ctx.parsed, "dry_run", False))
    report = _cli.advance_roadmap_queue(
        context=context,
        github_client=ctx.github_client_factory(context.repo_path),
        store=_cli.create_roadmap_store(),
        process_runner=ctx.process_runner,
        dry_run=dry_run,
    )
    _print_advance_report(report)
    return 0


__all__ = ["run_roadmap_advance_command"]
