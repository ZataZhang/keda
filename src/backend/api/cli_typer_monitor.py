"""用于控制台快照的定向刷新命令。"""

from __future__ import annotations

from typing import Annotated, cast

import typer

from backend.api.cli_typer_app import monitor_app
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    create_github_client,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.monitor_issue_refresh import (
    IssueRefreshStore,
    IssueRefreshTarget,
    refresh_monitor_issue,
)


@monitor_app.command("refresh")
def refresh_issue_command(
    repo_id: Annotated[str, typer.Option("--repo-id", help="已注册仓库的 ID。")],
    issue_number: Annotated[int, typer.Option("--issue", min=1, help="要刷新的 Issue 编号。")],
    pr_number: Annotated[
        int | None, typer.Option("--pr", min=1, help="关联的 PR 编号；缺省从快照读取。")
    ] = None,
) -> None:
    """仅刷新指定 Issue 的控制台卡片。"""
    repositories, _failures = resolve_repository_targets_with_diagnostics(
        load_fresh_agent_runner_settings()
    )
    target = next((repo for repo in repositories if repo.repo_id == repo_id), None)
    if target is None:
        raise typer.BadParameter(
            f"Unknown or disabled repository: {repo_id}", param_hint="--repo-id"
        )
    try:
        refresh_monitor_issue(
            cast(IssueRefreshStore, create_console_store()),
            create_github_client(target.repo_path),
            IssueRefreshTarget(target, issue_number, pr_number),
        )
    except Exception as exc:
        typer.echo(f"Could not refresh {repo_id}#{issue_number}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Refreshed {repo_id}#{issue_number} in the local Console snapshot.")
