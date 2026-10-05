"""``iar backlog`` 的手动入口处理器。

``advance`` 跑一次持续调度（reconcile + promote + discover），与 fast-lane daemon
每轮做的事完全一致，因此 ``--dry-run`` 同时是"下一轮会做什么"的探针。

``ci status|policy|repair`` 是 Post-PR CI/CD 观察与控制的 CLI 面：三个命令都是
:mod:`backend.core.use_cases.backlog_ci_delivery` 的薄封装，与 Console API 共用同一
状态投影、同一配置 writer 与同一手动修复用例——CLI 不计算 effective policy，不解析
marker，也不另存一份状态。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from backend.api.cli_console import console, error_console
from backend.api.cli_helpers import _resolve_cli_repository_targets
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.serialization import serialize_value
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.shared.models.backlog import BacklogCiRepairPolicy, CiDelivery
from backend.core.use_cases.backlog_ci_delivery import BacklogCiError
from backend.api import cli as _cli


def _print_advance_report(report) -> None:
    """Print a human-readable summary of one scheduling pass.

    Args:
        report: The ``BacklogAdvanceReport`` returned by the use case.
    """
    mode = "dry-run" if report.dry_run else "applied"
    console.print(f"[bold]backlog advance[/] ({mode}) repo={report.repo_id}")
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


def run_backlog_advance_command(ctx: ParsedCommandContext) -> int:
    """``iar backlog advance``: run one scheduling pass, optionally dry-run."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        error_console.print("[red]backlog advance requires exactly one target repository.[/]")
        error_console.print("Use --repo or --repo-id to select it.")
        return 1

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


# ─────────────────────────────────────────────────────────────────────────────
# iar backlog ci status|policy|repair
#
# 三个命令都只做参数转换 + 既有 core 用例调用：状态投影、effective policy、marker
# 解析、幂等判定与修复门禁全在 core，本层不重复实现，也不缓存。
# ─────────────────────────────────────────────────────────────────────────────


#: 单 PRD 三态策略的合法取值（``--global`` 只接受 on/off）。
_PRD_POLICY_VALUES = frozenset(member.value for member in BacklogCiRepairPolicy)


def _single_ci_context(ctx: ParsedCommandContext, command: str) -> RepositoryRunContext | None:
    """CI 命令一律针对单个仓库：解析出唯一目标，否则报错并返回 ``None``。"""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if len(contexts) != 1:
        error_console.print(f"[red]{command} requires exactly one target repository.[/]")
        error_console.print("Use --repo or --repo-id to select it.")
        return None
    return contexts[0]


def _fresh_contexts_loader(
    ctx: ParsedCommandContext,
) -> Callable[[], Sequence[RepositoryRunContext]]:
    """构造"从磁盘重新解析生效配置"的加载器（写回后的成功判据不能用内存值）。"""

    def load_fresh() -> Sequence[RepositoryRunContext]:
        return _resolve_cli_repository_targets(
            parsed=ctx.parsed,
            runner_settings=_cli.load_fresh_agent_runner_settings(),
            repo_id=ctx.repo_id,
            repo_override=ctx.repo_override,
        )

    return load_fresh


def _emit_json(payload: Any) -> None:
    """机读输出：纯 JSON 走 stdout，人类可读信息一律走 stderr。"""
    print(json.dumps(serialize_value(payload), ensure_ascii=False, indent=2))


def _policy_label(delivery: CiDelivery) -> str:
    """把三态存储值、全局值与生效值合成一行，避免调用方自行推断 effective 值。"""
    return (
        f"stored={delivery.stored_policy.value} "
        f"global={'on' if delivery.global_auto_repair else 'off'} "
        f"effective={'on' if delivery.effective_auto_repair else 'off'}"
    )


def _print_delivery_human(delivery: CiDelivery) -> None:
    """打印单个 PRD 的 CI/CD 投影（原始 checks 与策略结论分开呈现）。"""
    console.print(f"[bold]{delivery.prd_path}[/] #{delivery.pr_number or '-'}")
    console.print(
        f"status={delivery.status.value} checks_state={delivery.checks_state or '-'} "
        f"head={delivery.head_sha or '-'}"
    )
    console.print(
        f"rounds={delivery.repair_rounds}/{delivery.max_repair_attempts} "
        f"{'exhausted ' if delivery.repair_exhausted else ''}"
        f"last_decision={delivery.last_decision or '-'}"
    )
    console.print(_policy_label(delivery))
    if delivery.supervisor_action:
        console.print(f"supervisor_action={delivery.supervisor_action}")
    for problem in delivery.problems:
        location = f" {problem.url}" if problem.url else ""
        console.print(
            f"[red]problem[/] [{problem.kind}] {problem.name}: {problem.detail}{location}"
        )
    if delivery.detail:
        console.print(f"[dim]{delivery.detail}[/]")
    console.print(f"[dim]last_synced_at={delivery.last_synced_at}[/]")


def _deliveries_for_repository(
    context: RepositoryRunContext, github_client: Any
) -> list[CiDelivery]:
    """复用 Backlog 列表的同一条 core 投影链路，取所有 PRD 的 CI/CD 状态。"""
    scan_result = _cli.scan_backlog_prds(context.repo_path, include_archived=False)
    prds = scan_result.prds
    resolved = _cli.resolve_backlog_states(
        prds,
        github_client=github_client,
        config=context.config,
        block_reasons=_cli.evaluate_backlog_dependencies(prds, github_client=github_client),
    )
    return [prd.ci_delivery for prd in resolved if prd.ci_delivery is not None]


def run_backlog_ci_status_command(ctx: ParsedCommandContext) -> int:
    """``iar backlog ci status``：只读观察 CI/CD 交付状态（``--json`` 复用 DTO）。"""
    context = _single_ci_context(ctx, "backlog ci status")
    if context is None:
        return 1
    prd_path = getattr(ctx.parsed, "ci_prd_path", None)
    json_output = bool(getattr(ctx.parsed, "ci_json", False))
    github_client = ctx.github_client_factory(context.repo_path)

    if prd_path:
        issue_number = _cli.resolve_prd_issue_number(context.repo_path, prd_path)
        if issue_number is None:
            error_console.print(f"[red]PRD {prd_path} 还没有关联 Issue，CI/CD 状态不适用。[/]")
            if json_output:
                _emit_json(None)
                return 0
            return 1
        delivery = _cli.build_prd_ci_delivery(
            prd_path=prd_path,
            issue_number=issue_number,
            github_client=github_client,
            config=context.config,
        )
        if json_output:
            _emit_json(delivery)
            return 0
        if delivery is None:
            console.print("[dim]该 PRD 还没有关联 PR，本功能不适用。[/]")
            return 0
        _print_delivery_human(delivery)
        return 0

    deliveries = _deliveries_for_repository(context, github_client)
    if json_output:
        _emit_json(deliveries)
        return 0
    state = _cli.load_ci_auto_repair_state(
        repo_id=context.repo_id,
        contexts=(context,),
        editor=_cli.create_repository_autopilot_settings_editor(),
    )
    console.print(
        f"[bold]{context.repo_id}[/] auto_repair_ci="
        f"{'on' if state.auto_repair_ci else 'off'} "
        f"max_repair_attempts={state.max_repair_attempts} ({state.config_source})"
    )
    visible = [delivery for delivery in deliveries if delivery.pr_number is not None]
    if not visible:
        console.print("[dim]当前没有已发布 PR 的 PRD。[/]")
        return 0
    for index, delivery in enumerate(visible):
        if index:
            console.print("")
        _print_delivery_human(delivery)
    return 0


def run_backlog_ci_policy_command(ctx: ParsedCommandContext) -> int:
    """``iar backlog ci policy``：写仓库级全局值或单 PRD 三态覆盖（两者互斥）。"""
    context = _single_ci_context(ctx, "backlog ci policy")
    if context is None:
        return 1
    global_value = getattr(ctx.parsed, "ci_global_policy", None)
    prd_path = getattr(ctx.parsed, "ci_prd_path", None)
    prd_value = getattr(ctx.parsed, "ci_policy_value", None)
    if (global_value is None) == (prd_value is None):
        error_console.print(
            "[red]policy 需要且只需要一个目标：--global on|off 或 --prd <path> inherit|on|off[/]"
        )
        return 1
    if global_value is not None and str(global_value) not in {"on", "off"}:
        error_console.print("[red]--global 只接受 on 或 off。[/]")
        return 1
    if prd_value is not None and not prd_path:
        error_console.print("[red]单 PRD 策略需要配合 --prd <path> 使用。[/]")
        return 1
    if prd_value is not None and str(prd_value) not in _PRD_POLICY_VALUES:
        error_console.print(f"[red]非法策略值 '{prd_value}'，只接受 inherit / on / off。[/]")
        return 1

    try:
        if global_value is not None:
            enabled = str(global_value) == "on"
            state = _cli.set_ci_auto_repair_enabled(
                repo_id=context.repo_id,
                enabled=enabled,
                editor=_cli.create_repository_autopilot_settings_editor(),
                contexts_loader=_fresh_contexts_loader(ctx),
            )
            console.print(
                f"[green]仓库全局 auto_repair_ci[/] = {'on' if state.auto_repair_ci else 'off'} "
                f"（写入 {state.config_source}，max_repair_attempts="
                f"{state.max_repair_attempts}）"
            )
            return 0

        issue_number = _cli.resolve_prd_issue_number(context.repo_path, str(prd_path))
        if issue_number is None:
            error_console.print(
                f"[red]PRD {prd_path} 还没有关联 Issue，无法设置单 PRD 策略；"
                "只能跟随仓库全局值。[/]"
            )
            return 1
        stored = _cli.set_prd_ci_repair_policy(
            github_client=ctx.github_client_factory(context.repo_path),
            issue_number=issue_number,
            policy=BacklogCiRepairPolicy(str(prd_value)),
        )
        console.print(f"[green]PRD {prd_path}[/] #{issue_number} stored policy = {stored.value}")
        return 0
    except BacklogCiError as exc:
        error_console.print(f"[red]策略写入失败：{exc}[/]")
        return 1


def run_backlog_ci_repair_command(ctx: ParsedCommandContext) -> int:
    """``iar backlog ci repair``：显式发起一次手动修复（与问题卡同一用例）。"""
    context = _single_ci_context(ctx, "backlog ci repair")
    if context is None:
        return 1
    prd_path = getattr(ctx.parsed, "ci_prd_path", None)
    if not prd_path:
        error_console.print("[red]repair 需要 --prd <path>。[/]")
        return 1
    issue_number = _cli.resolve_prd_issue_number(context.repo_path, str(prd_path))
    if issue_number is None:
        error_console.print(f"[red]PRD {prd_path} 还没有关联 Issue，无法发起修复。[/]")
        return 1
    try:
        result = _cli.request_manual_ci_repair(
            issue_number=issue_number,
            repo_path=context.repo_path,
            config=context.config,
            github_client=ctx.github_client_factory(context.repo_path),
            process_runner=ctx.process_runner,
            dry_run=bool(getattr(ctx.parsed, "dry_run", False)),
        )
    except BacklogCiError as exc:
        error_console.print(f"[red]修复请求被拒绝：{exc}[/]")
        return 1
    label = (
        "dry-run"
        if getattr(ctx.parsed, "dry_run", False)
        else ("已排队" if result.accepted else "被拒绝")
    )
    console.print(
        f"[{'green' if result.accepted else 'red'}]{label}[/] "
        f"{result.prd_path} #{issue_number} decision={result.decision} "
        f"head={result.head_sha or '-'} digest={result.failure_key or '-'}"
    )
    console.print(f"[dim]{result.detail}[/]")
    return 0 if result.accepted else 1


__all__ = [
    "run_backlog_advance_command",
    "run_backlog_ci_policy_command",
    "run_backlog_ci_repair_command",
    "run_backlog_ci_status_command",
]
