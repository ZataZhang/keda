"""Typer commands under ``kc backlog``.

Currently a single command — ``kc backlog advance`` — which runs one
continuous-scheduling pass (reconcile + promote + discover) for the target
repository. The same logic runs automatically inside the fast-lane daemon.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import _run_typer_repository_command, backlog_app


@backlog_app.command("advance")
def backlog_advance_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Report the plan for this pass without writing anything.",
        ),
    ] = False,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Target repository path."),
    ] = None,
    repo_id: Annotated[
        str | None,
        typer.Option("--repo-id", help="Target configured repository ID."),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option(
            "--config", help="Deprecated: config is loaded from config.toml and env vars."
        ),
    ] = None,
) -> int:
    """Run one continuous-scheduling pass for the target repository."""
    return _run_typer_repository_command(
        ctx,
        "backlog advance",
        repo=repo,
        repo_id=repo_id,
        config=config,
        dry_run=dry_run,
    )


__all__ = ["backlog_advance_command", "ci_app"]


# ── `kc backlog ci` 子命令组 ────────────────────────────────────────────────
# 与 Console 共用同一批 core 用例的薄封装；`status --json` 复用 Console
# `ci_delivery` DTO，数据走 stdout、进度与警告走 stderr。
ci_app = typer.Typer(
    help="Observe and control CI/CD delivery for backlog PRDs.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

_PrdOption = Annotated[
    str | None,
    typer.Option("--prd", help="Target PRD path (relative to repository root)."),
]
_CiRepoOption = Annotated[str | None, typer.Option("--repo", help="Target repository path.")]
_CiRepoIdOption = Annotated[
    str | None, typer.Option("--repo-id", help="Target configured repository ID.")
]
_CiConfigOption = Annotated[
    str | None,
    typer.Option("--config", help="Deprecated: config is loaded from config.toml and env vars."),
]


@ci_app.command("status")
def backlog_ci_status_command(
    ctx: typer.Context,
    prd: _PrdOption = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print the same ci_delivery DTO as the Console API (stdout, pure JSON).",
        ),
    ] = False,
    repo: _CiRepoOption = None,
    repo_id: _CiRepoIdOption = None,
    config: _CiConfigOption = None,
) -> int:
    """Show CI/CD delivery state for backlog PRDs (read-only)."""
    return _run_typer_repository_command(
        ctx,
        "backlog ci status",
        repo=repo,
        repo_id=repo_id,
        config=config,
        prd=prd,
        json_output=json_output,
    )


@ci_app.command("policy")
def backlog_ci_policy_command(
    ctx: typer.Context,
    value: Annotated[
        str,
        typer.Argument(help="Policy value: on / off for --global; inherit / on / off for --prd."),
    ],
    global_target: Annotated[
        bool,
        typer.Option(
            "--global",
            help="Set the repository-level default (post_pr_supervisor.auto_repair_ci).",
        ),
    ] = False,
    prd: _PrdOption = None,
    repo: _CiRepoOption = None,
    repo_id: _CiRepoIdOption = None,
    config: _CiConfigOption = None,
) -> int:
    """Set the CI auto-repair policy for the repository or a single PRD."""
    return _run_typer_repository_command(
        ctx,
        "backlog ci policy",
        repo=repo,
        repo_id=repo_id,
        config=config,
        policy_target="global" if global_target else "prd",
        value=value,
        prd=prd,
    )


backlog_app.add_typer(ci_app, name="ci")


@ci_app.command("repair")
def backlog_ci_repair_command(
    ctx: typer.Context,
    prd: Annotated[str, typer.Option("--prd", help="Target PRD path.")],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Report what would happen without any side effect.",
        ),
    ] = False,
    repo: _CiRepoOption = None,
    repo_id: _CiRepoIdOption = None,
    config: _CiConfigOption = None,
) -> int:
    """Explicitly request one manual repair (idempotent, gated)."""
    return _run_typer_repository_command(
        ctx,
        "backlog ci repair",
        repo=repo,
        repo_id=repo_id,
        config=config,
        prd=prd,
        dry_run=dry_run,
    )
