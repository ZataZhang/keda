"""``kc peers`` command for repository-scoped machine presence."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.table import Table

from backend.api.cli_console import console
from backend.api.cli_output import emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_utils import format_local_display_time
from backend.core.use_cases.agent_runner_claim_lease import latest_claim_record
from backend.core.use_cases.machine_presence import (
    PresenceEntry,
    list_presence_machines,
    presence_entry_is_online,
)


def run_peers_command(ctx: ParsedCommandContext) -> int:
    """显示本仓库的机器状态和未上报状态的认领持有者。"""
    from backend.api.cli_parsed_commands.runner import _resolve_initialized_repository_contexts

    contexts = _resolve_initialized_repository_contexts(ctx)
    if len(contexts) != 1:
        from backend.api.cli_exit_codes import ExitCode
        from backend.api.cli_output import CliError

        raise CliError(
            "kc peers requires exactly one repository target.",
            code=ExitCode.USAGE,
            suggestion="kc peers --repo-id <repo>",
        )
    repository_context = contexts[0]
    github_client = ctx.github_client_factory(repository_context.repo_path)
    effective_now = datetime.now(timezone.utc)
    presence_issue, presence_entries = list_presence_machines(
        github_client=github_client,
        now=effective_now,
    )
    machine_ids = {entry.machine_id for entry in presence_entries}
    hostnames = {entry.hostname for entry in presence_entries}
    running_issues = github_client.list_issues_by_label(
        repository_context.config.labels.running,
        100,
        state="open",
    )
    unreported_claims: list[dict[str, object]] = []
    for running_issue in running_issues:
        comment_records = github_client.list_issue_comment_records(
            running_issue.number,
            trusted_only=True,
            body_contains="<!-- kc:claim ",
            require_success=True,
        )
        parsed_claim = latest_claim_record(comment_records)
        if parsed_claim is None:
            continue
        claim_record, claim_detail, lease = parsed_claim
        holder_visible = (
            lease.machine_id in machine_ids if lease is not None else claim_detail.host in hostnames
        )
        if holder_visible:
            continue
        unreported_claims.append(
            {
                "issue_number": running_issue.number,
                "hostname": claim_detail.host,
                "machine_id": lease.machine_id if lease is not None else None,
                "agent": claim_detail.agent,
                "claim_updated_at": claim_record.updated_at.astimezone(timezone.utc).isoformat(),
                "status": "持有者未上报状态（可能是旧版本）",
            }
        )

    machines_payload = [
        {
            "machine_id": entry.machine_id,
            "hostname": entry.hostname,
            "version": entry.version,
            "role": entry.role,
            "status": "online" if presence_entry_is_online(entry, now=effective_now) else "offline",
            "last_reported_at": entry.updated_at.astimezone(timezone.utc).isoformat(),
            "issues": list(entry.issues),
        }
        for entry in presence_entries
    ]
    payload = {
        "repo_id": repository_context.repo_id,
        "presence_issue": presence_issue.number if presence_issue is not None else None,
        **({"message": "该仓库还没有机器上报状态"} if presence_issue is None else {}),
        "machines": machines_payload,
        "unreported_claims": unreported_claims,
    }
    emit(
        payload,
        fmt=ctx.output_format,
        human_renderer=lambda: _render_peers_table(
            repository_context.repo_id,
            presence_issue.number if presence_issue is not None else None,
            presence_entries,
            unreported_claims,
            now=effective_now,
        ),
    )
    return 0


def _render_peers_table(
    repo_id: str,
    presence_issue_number: int | None,
    entries: list[PresenceEntry],
    unreported_claims: list[dict[str, object]],
    *,
    now: datetime,
) -> None:
    """渲染人读机器状态表，时间按本机时区展示。"""
    if presence_issue_number is None:
        console.print("该仓库还没有机器上报状态")
        _render_unreported_claims(unreported_claims)
        return
    table = Table(title=f"Machines for {repo_id} (Issue #{presence_issue_number})")
    table.add_column("Host")
    table.add_column("kc version")
    table.add_column("Status")
    table.add_column("Last report")
    table.add_column("Working on")
    for entry in entries:
        activities = []
        for issue_row in entry.issues:
            number = issue_row.get("number")
            if number is None:
                continue
            activity = f"#{number} · {issue_row.get('agent') or '—'}"
            stage = issue_row.get("stage")
            if stage:
                activity += f" · {stage}"
            started_at = issue_row.get("started_at")
            if started_at:
                activity += f" · {format_local_display_time(str(started_at))}"
            activities.append(activity)
        table.add_row(
            entry.hostname,
            entry.version,
            "online" if presence_entry_is_online(entry, now=now) else "offline",
            entry.updated_at.astimezone().isoformat(timespec="seconds"),
            ", ".join(activities) or "idle",
        )
    console.print(table)
    _render_unreported_claims(unreported_claims)


def _render_unreported_claims(unreported_claims: list[dict[str, object]]) -> None:
    """显示没有对应机器状态评论的远端认领。"""
    if unreported_claims:
        console.print("\nClaim holders without presence status:")
        for claim_row in unreported_claims:
            console.print(
                f"- Issue #{claim_row['issue_number']}: {claim_row['hostname']} "
                f"({claim_row['status']})"
            )


__all__ = ["run_peers_command"]
