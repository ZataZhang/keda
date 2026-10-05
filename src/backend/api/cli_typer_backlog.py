"""Typer commands under ``iar backlog``.

``iar backlog advance`` runs one continuous-scheduling pass (reconcile + promote
+ discover) for the target repository — the same logic the fast-lane daemon runs
automatically.

``iar backlog ci status|policy|repair`` is the first-class CLI surface for
post-PR CI/CD observation and control. All three are thin wrappers over the
existing core use cases (``backlog_ci_delivery``) and share the Console's
``ci_delivery`` DTO, settings writer and manual-repair path — the CLI never
recomputes the effective policy, parses markers itself, or keeps its own state.
"""

from __future__ import annotations

from typing import Annotated

import typer

from backend.api.cli_typer_app import (
    ConfigOption,
    RepoIdOption,
    RepoOption,
    _run_typer_repository_command,
    backlog_app,
    backlog_ci_app,
)


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
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
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


__all__ = [
    "backlog_advance_command",
    "backlog_ci_policy_command",
    "backlog_ci_repair_command",
    "backlog_ci_status_command",
]


@backlog_ci_app.command("status")
def backlog_ci_status_command(
    ctx: typer.Context,
    prd: Annotated[
        str | None,
        typer.Option("--prd", help="只观察该 PRD（仓库相对路径）的 CI/CD 状态。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="输出与 Console ci_delivery 同构的纯 JSON（数据走 stdout，提示走 stderr）。",
        ),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """只读观察 Post-PR CI/CD 状态（原始 checks、轮次与三态策略）。"""
    return _run_typer_repository_command(
        ctx,
        "backlog ci status",
        repo=repo,
        repo_id=repo_id,
        config=config,
        ci_prd_path=prd,
        ci_json=json_output,
    )


@backlog_ci_app.command("policy")
def backlog_ci_policy_command(
    ctx: typer.Context,
    value: Annotated[
        str | None,
        typer.Argument(help="单 PRD 策略：inherit / on / off（配合 --prd 使用）。"),
    ] = None,
    global_value: Annotated[
        str | None,
        typer.Option("--global", help="仓库级全局值：on / off。"),
    ] = None,
    prd: Annotated[
        str | None,
        typer.Option("--prd", help="目标 PRD（仓库相对路径）。"),
    ] = None,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """设置 CI 自动修复策略：仓库级全局值或单个 PRD 的三态覆盖。"""
    return _run_typer_repository_command(
        ctx,
        "backlog ci policy",
        repo=repo,
        repo_id=repo_id,
        config=config,
        ci_policy_value=value,
        ci_global_policy=global_value,
        ci_prd_path=prd,
    )


@backlog_ci_app.command("repair")
def backlog_ci_repair_command(
    ctx: typer.Context,
    prd: Annotated[
        str,
        typer.Option("--prd", help="目标 PRD（仓库相对路径），必须是已发布 PR 的 PRD。"),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="只报告将执行的结论，不产生任何副作用。"),
    ] = False,
    repo: RepoOption = None,
    repo_id: RepoIdOption = None,
    config: ConfigOption = None,
) -> int:
    """显式发起一次 CI 修复（与问题卡同一用例：幂等、受上限与安全门禁约束）。"""
    return _run_typer_repository_command(
        ctx,
        "backlog ci repair",
        repo=repo,
        repo_id=repo_id,
        config=config,
        ci_prd_path=prd,
        dry_run=dry_run,
    )
