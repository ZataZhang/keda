"""``kc backlog advance`` handler.

Runs exactly one continuous-scheduling pass for a single target repository:
reconcile finished/failed queue entries, then promote queued PRDs (and PRDs
newly discovered in ``tasks/pending/``) up to the resolved execution ceiling
(``min(policy, runner capacity)``; unsaved policy inherits capacity).

This is the manual entry point for the same logic the fast-lane daemon runs
every pass, so ``--dry-run`` doubles as the "what would the next pass do?"
probe.
"""

from __future__ import annotations

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import _resolve_cli_repository_targets
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api import cli as _cli
from backend.core.use_cases.agent_runner_events import iter_event_markers
from backend.core.use_cases.backlog_ci_delivery import (
    compute_effective_auto_repair,
    count_ci_repair_rounds,
    read_stored_policy,
    request_manual_ci_repair,
    set_prd_ci_policy,
)
from backend.core.use_cases.review_once import _extract_pr_branch_from_comments
from backend.core.shared.models.agent_runner import IssueSummary


def _print_advance_report(report) -> None:
    """Print a human-readable summary of one scheduling pass.

    Args:
        report: The ``BacklogAdvanceReport`` returned by the use case.
    """
    mode = "dry-run" if report.dry_run else "applied"
    console.print(f"[bold]backlog advance[/] ({mode}) repo={report.repo_id}")
    running_count_label = (
        "unknown"
        if report.running_count is None
        else (
            f"at least {report.running_count}"
            if report.running_count_is_lower_bound
            else str(report.running_count)
        )
    )
    console.print(
        f"ceiling={report.ceiling} source={report.ceiling_source} "
        f"running={running_count_label} free_slots={report.free_slots}"
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


def run_backlog_advance_command(ctx: ParsedCommandContext) -> int:
    """``kc backlog advance``: run one scheduling pass, optionally dry-run."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        raise CliError(
            "backlog advance requires exactly one target repository. "
            "Use --repo or --repo-id to select it.",
            code=ExitCode.USAGE,
            suggestion="kc registry list",
        )

    context = contexts[0]
    dry_run = bool(getattr(ctx.parsed, "dry_run", False))
    report = _cli.advance_backlog_queue(
        context=context,
        github_client=ctx.github_client_factory(context.repo_path),
        store=_cli.create_backlog_store(),
        process_runner=ctx.process_runner,
        dry_run=dry_run,
    )
    _print_advance_report(report)
    return 0


__all__ = ["run_backlog_advance_command"]


# ── `kc backlog ci` 子命令（CI/CD 状态观察 / 策略 / 单次手动修复）──────────
# 与 Console API 共用同一批 core 用例（backlog_ci_delivery / 受限配置编辑器），
# 不复制 effective 策略计算或 repair 实现；`status --json` 复用 Console 的
# `ci_delivery` DTO 结构，数据只走 stdout、进度与警告走 stderr。


def _single_ci_context(ctx: ParsedCommandContext):
    """解析唯一目标仓库上下文（与其他 backlog 子命令同一选择器规则）。"""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        error_console.print("[red]backlog ci requires exactly one target repository.[/]")
        error_console.print("Use --repo or --repo-id to select it.")
        return None
    return contexts[0]


def _scan_prds_with_issues(context):
    """fresh 扫描仓库 PRD 并返回（prd 列表, github_client, config）。"""
    from backend.core.use_cases.backlog_prd_scanner import scan_backlog_prds

    github_client = _cli.create_github_client(context.repo_path)
    scan_result = scan_backlog_prds(context.repo_path, include_archived=False)
    return scan_result.prds, github_client, context.config


def _build_status_rows(prds, github_client, config, prd_filter: str | None):
    """把 PRD 列表投影成 ci_delivery 行（fresh 读取每个 Issue 的评论流）。"""
    from backend.core.use_cases.backlog_ci_delivery import build_ci_delivery

    global_enabled = bool(config.post_pr_supervisor.auto_repair_ci)
    max_rounds = max(0, config.post_pr_supervisor.max_repair_attempts)
    rows = []
    for prd in prds:
        if prd_filter is not None and prd.prd_path != prd_filter:
            continue
        issue_comments: list[str] = []
        pr_context = None
        if prd.issue_number is not None:
            issue_comments = github_client.list_issue_comments(prd.issue_number)
            pr_branch = _extract_pr_branch_from_comments(issue_comments)
            if pr_branch is not None:
                pr_context = github_client.get_pull_request_context(pr_branch)
        rows.append(
            build_ci_delivery(
                prd_path=prd.prd_path,
                issue_number=prd.issue_number,
                comments=issue_comments,
                pr_context=pr_context,
                global_enabled=global_enabled,
                max_rounds=max_rounds,
            )
        )
    return rows


def _print_status_rows(rows) -> None:
    """人类可读输出（信息走 stdout Rich console）。"""
    for row in rows:
        policy = (
            f"stored={row.stored_policy.value} effective={'on' if row.effective_enabled else 'off'}"
        )
        console.print(
            f"{row.prd_path}  status=[bold]{row.status.value}[/]  rounds={row.round_count}/{row.max_rounds}  {policy}"
        )
        if row.exhausted:
            console.print(f"  [red]exhausted[/] {row.exhausted_reason}")
        for problem in row.problems:
            console.print(f"  [red]✕[/] {problem.name}: {problem.summary}")


def _find_ci_target_prd(context, prd_path: str | None):
    """fresh 扫描并定位带 Issue 的目标 PRD；找不到时打印错误并返回 ``None``。"""
    prds, github_client, config = _scan_prds_with_issues(context)
    if prd_path is None:
        return prds, github_client, config, None
    target_prd = next((prd for prd in prds if prd.prd_path == prd_path), None)
    if target_prd is None or target_prd.issue_number is None:
        error_console.print(f"[red]PRD '{prd_path}' 不存在或没有对应的 GitHub Issue。[/]")
        return prds, github_client, config, None
    return prds, github_client, config, target_prd


def run_backlog_ci_status_command(ctx: ParsedCommandContext) -> int:
    """``kc backlog ci status``：只读观察（``--json`` 与 Console DTO 同构）。"""
    context = _single_ci_context(ctx)
    if context is None:
        return 1
    prd_filter = getattr(ctx.parsed, "prd", None)
    as_json = bool(getattr(ctx.parsed, "json_output", False))
    prds, github_client, config = _scan_prds_with_issues(context)
    rows = _build_status_rows(prds, github_client, config, prd_filter)
    if as_json:
        from backend.api.routes.agent_runner_backlog import _serialize
        from backend.api.cli_output import emit_json

        # 机读输出走 cli_output 唯一数据出口（纯 stdout、无 Rich 折行）；
        # 进度与警告仍走 stderr。
        emit_json([_serialize(row) for row in rows])
        return 0
    _print_status_rows(rows)
    return 0


def run_backlog_ci_policy_command(ctx: ParsedCommandContext) -> int:
    """``kc backlog ci policy``：仓库级全局或单 PRD 三态覆盖（二者互斥）。"""
    context = _single_ci_context(ctx)
    if context is None:
        return 1
    target = getattr(ctx.parsed, "policy_target", None)
    value = str(getattr(ctx.parsed, "value", ""))
    prd_path = getattr(ctx.parsed, "prd", None)

    if target == "global":
        from backend.core.use_cases.agent_runner_factory import (
            create_repository_autopilot_settings_editor,
        )

        if prd_path is not None:
            error_console.print("[red]--global 与 --prd 互斥，只能选择一个目标。[/]")
            return 1
        enabled = value == "on"
        editor = create_repository_autopilot_settings_editor()
        try:
            editor.set_auto_repair_ci(context.repo_path, enabled)
        except ValueError as exc:
            error_console.print(f"[red]{exc}[/]")
            return 1
        # fresh 校验直接读目标仓库本地 .kedacode.toml（--repo 传入未注册路径时，
        # 重新解析 registry 不会包含该仓库，不能作为 fresh 依据）；经受限端口
        # 读取（``None`` = 键未设置 = 生效默认 False）。
        from backend.core.use_cases.agent_runner_factory import (
            create_repository_autopilot_settings_editor,
        )

        fresh_value = create_repository_autopilot_settings_editor().read_auto_repair_ci(
            context.repo_path
        )
        if bool(fresh_value) is not enabled:
            error_console.print("[red]写回后 fresh load 与请求值不一致。[/]")
            return 1
        console.print(
            f"global auto_repair_ci={'on' if enabled else 'off'} (repo={context.repo_id})"
        )
        return 0

    # 单 PRD 三态覆盖
    if prd_path is None:
        error_console.print(
            "[red]请用 --prd <path> 指定目标 PRD，或用 --global 设置仓库默认值。[/]"
        )
        return 1
    _prds, github_client, config, target_prd = _find_ci_target_prd(context, prd_path)
    if target_prd is None:
        return 1
    set_prd_ci_policy(
        github_client=github_client,
        issue_number=target_prd.issue_number,
        value=value,
    )
    # 写后 fresh 回读评论流，以 marker 而不是请求回显作为成功判据。
    stored = read_stored_policy(github_client.list_issue_comments(target_prd.issue_number))
    if stored.value != value:
        error_console.print("[red]策略 marker 写回后 fresh 读取与请求值不一致。[/]")
        return 1
    effective = compute_effective_auto_repair(
        stored.value, bool(config.post_pr_supervisor.auto_repair_ci)
    )
    console.print(
        f"{prd_path}  stored={stored.value}  global={'on' if config.post_pr_supervisor.auto_repair_ci else 'off'}  "
        f"effective={'on' if effective else 'off'}"
    )
    return 0


def run_backlog_ci_repair_command(ctx: ParsedCommandContext) -> int:
    """``kc backlog ci repair``：显式请求一次修复（``--dry-run`` 零副作用）。"""
    context = _single_ci_context(ctx)
    if context is None:
        return 1
    prd_path = getattr(ctx.parsed, "prd", None)
    dry_run = bool(getattr(ctx.parsed, "dry_run", False))
    if prd_path is None:
        error_console.print("[red]请用 --prd <path> 指定目标 PRD。[/]")
        return 1
    _prds, github_client, config, target_prd = _find_ci_target_prd(context, prd_path)
    if target_prd is None:
        return 1
    comments = github_client.list_issue_comments(target_prd.issue_number)
    pr_branch = _extract_pr_branch_from_comments(comments)
    pr_context = (
        github_client.get_pull_request_context(pr_branch) if pr_branch is not None else None
    )
    if pr_context is None:
        error_console.print("[red]无法获取当前 PR context，不能发起修复。[/]")
        return 1
    stored = read_stored_policy(comments)
    effective = compute_effective_auto_repair(
        stored.value, bool(config.post_pr_supervisor.auto_repair_ci)
    )
    rounds = count_ci_repair_rounds(comments)
    max_rounds = max(0, config.post_pr_supervisor.max_repair_attempts)
    same_head_requested = any(
        marker.phase == "post_pr_rework_requested"
        and marker.action == "repair_pr_branch"
        and marker.head_sha == pr_context.head_sha
        for marker in iter_event_markers(comments)
    )
    if dry_run:
        console.print(f"[bold]dry-run[/] {prd_path} head={pr_context.head_sha[:12]}")
        console.print(f"  policy: stored={stored.value} effective={'on' if effective else 'off'}")
        console.print(f"  rounds: {rounds}/{max_rounds}  same-head-requested={same_head_requested}")
        if same_head_requested:
            console.print("  would: no-op（同一 head 的修复请求已存在）")
        elif not effective:
            console.print("  would: rejected（自动修复未开启；此命令为显式请求，仍可执行）")
        elif rounds >= max_rounds:
            console.print("  would: rejected（修复轮数已耗尽）")
        else:
            console.print(f"  would: request one manual repair (round {rounds + 1})")
        return 0
    issue = IssueSummary(
        number=target_prd.issue_number,
        title=target_prd.title,
        url=target_prd.issue_url or "",
        body="",
        labels=(),
    )
    requested, detail = request_manual_ci_repair(
        issue=issue,
        pr_context=pr_context,
        config=config,
        github_client=github_client,
    )
    action = "requested" if requested else "noop"
    console.print(f"[cyan]{action}[/] {prd_path}: {detail}")
    return 0
