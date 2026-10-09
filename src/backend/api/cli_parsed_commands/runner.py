"""``kc run`` / ``kc daemon`` / ``kc review`` / ``kc review-daemon``
/ ``kc recover`` / ``kc blocked-continue`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_console import console
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_helpers import (
    _create_run_history_store_or_none,
    _ensure_gh_auth_or_prompt,
    _resolve_cli_repository_targets,
    _resolve_run_trigger,
)
from backend.api.cli_output import OUTPUT_FORMAT_JSON, CliError, emit
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_registry import _run_daemon_status_command
from backend.api.agent_runner_views.runner_live_view import create_runner_live_view
from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.models.publish_stage import PublishStage
from backend.api import cli as _cli
from backend.core.use_cases.agent_runner_factory import logger


def _dry_run_preview(
    contexts: list,
    *,
    agent: str,
    max_issues: int,
    target_issue: int | None = None,
    all_ready: bool = False,
    fast_merge: bool = False,
    publish_stage: str = PublishStage.NORMAL.value,
) -> dict:
    """组装 ``kc run --dry-run`` 的机读预览（本轮执行计划，逐 Issue 明细在 stderr 日志）。"""
    return {
        "dry_run": True,
        "agent": agent,
        "max_issues": max_issues,
        "target_issue": target_issue,
        "all_ready": all_ready,
        "fast_merge": fast_merge,
        "publish_stage": publish_stage,
        "repositories": [
            {"repo_id": context.repo_id, "repo_path": str(context.repo_path)}
            for context in contexts
        ],
    }


def run_run_command(ctx: ParsedCommandContext) -> int:
    """``kc run``: run one agent-runner polling cycle (a target is required).

    目标必填（FR-1）：``--issue <N>``、PRD 路径（解析回链 Issue）或显式
    ``--all-ready``（等价旧的"捞 ready 队列"行为，FR-2）。同仓已有 daemon
    时默认拒绝（FR-4），``--takeover`` 在强警告 + 确认下优雅停 daemon、
    终止其 agent 子进程树、reclaim 在途 Issue 后接管（FR-5）。
    """
    # 机器输出只定义在 --dry-run 预览组合下：真实执行的过程输出是流式文本，
    # 混进 JSON 只会产出不可解析的垃圾。在解析任何仓库目标之前 fail fast。
    if ctx.output_format == OUTPUT_FORMAT_JSON and not ctx.parsed.dry_run:
        raise CliError(
            "--json/--output json 只在 --dry-run 预览下有定义；真实执行的过程输出是流式文本。",
            code=ExitCode.USAGE,
            suggestion="kc run --dry-run --json",
        )
    parsed = ctx.parsed
    prd_path = getattr(parsed, "prd_path", None)
    target_issue = getattr(parsed, "issue", None)
    all_ready = getattr(parsed, "all_ready", False)
    takeover = getattr(parsed, "takeover", False)
    assume_yes = getattr(parsed, "yes", False)
    fast_merge = getattr(parsed, "fast_merge", False)
    direct_pr = getattr(parsed, "direct_pr", False)
    publish_stage = _resolve_publish_stage(fast_merge=fast_merge, direct_pr=direct_pr)

    if direct_pr and all_ready:
        raise CliError(
            "--direct-pr is defined for a single targeted Issue; it does not combine "
            "with --all-ready (an unreviewed burst across the whole queue is exactly "
            "what the flag must not enable).",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --direct-pr",
        )
    if fast_merge and all_ready:
        raise CliError(
            "--fast-merge is defined for a single targeted Issue; it does not combine "
            "with --all-ready (a queue-wide unverified burst is exactly what the flag "
            "must not enable).",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --fast-merge · kc run <PRD_PATH> --fast-merge",
        )

    if target_issue is not None and prd_path:
        raise CliError(
            "--issue and a PRD path are mutually exclusive targets; pick one.",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --repo-id <repo>",
        )
    if target_issue is None and not prd_path and not all_ready:
        raise CliError(
            "kc run requires a target: pass --issue <N>, a PRD path, or --all-ready.",
            code=ExitCode.USAGE,
            suggestion=(
                "kc run --issue <N> --repo-id <repo> · "
                "kc run tasks/pending/<prd>.md · kc run --all-ready"
            ),
        )
    contexts = _resolve_cli_repository_targets(
        parsed=parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（implementation 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, parsed, anchored_stage="implementation")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    # PRD 路径目标只在单仓语义下成立：多个仓库无法共享一份 PRD 回链。
    if prd_path:
        from backend.api.cli_helpers import require_single_repository_target
        from backend.core.use_cases.run_target_resolve import (
            RunTargetResolveError,
            resolve_prd_target_issue_number,
        )

        target_context = require_single_repository_target("run", contexts)
        try:
            target_issue = resolve_prd_target_issue_number(
                repo_path=target_context.repo_path, prd_path=prd_path
            )
        except RunTargetResolveError as exc:
            raise CliError(
                str(exc),
                code=ExitCode.USAGE,
                suggestion="kc issue create tasks/pending/<prd>.md",
            ) from exc

    # 快速通道 stack 门禁（决策二）：声明了 stack 顺序依赖的 Issue 在启动任何
    # agent（乃至 takeover 停 daemon）之前以用法错误拒绝——未验证的上游会顺着
    # fork 基污染整条下游链，检查失败时一律拒绝放行（fail-closed）。
    if fast_merge and target_issue is not None:
        _reject_fast_merge_on_stack_issue(ctx, contexts=contexts, target_issue=target_issue)
        # 直发标签与快速通道跳的门禁范围不同，同时命中时明确拒绝而非静默取更强者，
        # 免得调用方以为只跳了验证门禁、实际却把审核 agent 也跳了。
        _reject_fast_merge_on_direct_pr_label(ctx, contexts=contexts, target_issue=target_issue)

    # 直发档适用范围门禁（FR-16）：--direct-pr 只对**没有 PRD 锚点**的 Issue 有效。
    # PRD 路径目标本身就是 PRD-backed，不必读 Issue 即可拒绝；--issue 目标必须读正文
    # 证明它无锚点，读不到同样拒绝（fail-closed），否则旁路会留下未归档、未校验的 PRD。
    if direct_pr and prd_path:
        raise CliError(
            "--direct-pr is only defined for Issues without a PRD anchor; the target "
            "here is a PRD file, so the Issue is PRD-backed and must pass the PRD "
            "delivery gate (and archive its PRD).",
            code=ExitCode.USAGE,
            suggestion="kc run <PRD_PATH> --fast-merge",
        )
    if direct_pr and target_issue is not None:
        _reject_direct_pr_on_prd_backed_issue(ctx, contexts=contexts, target_issue=target_issue)

    # 显式定向准入（FR-21）：人点名 Issue 时，「领不领得到」必须响亮回报，而不是像
    # 守护进程那样把不合条件的目标静默跳过。--takeover 本身就是「强制回收在途
    # Issue」的入口，因此跳过本判定，由 takeover 的 reclaim 决定能不能领。
    if target_issue is not None and not takeover:
        _require_explicit_target_claimable(ctx, contexts=contexts, target_issue=target_issue)

    # 默认互斥（FR-4 / FR-24）：同仓 daemon 在跑时**只拒绝队列轮询**——显式单目标
    # 与守护进程共存（工作树按 Issue 隔离），同目标的排他改由上面的认领状态准入
    # 与首次领取 CAS 承担。--takeover 仍可显式停 daemon 并接管。
    # dry-run 预览保留互斥报错（无副作用），但不真正停 daemon。
    if takeover:
        _confirm_and_take_over_daemons(
            ctx,
            contexts=contexts,
            assume_yes=assume_yes,
        )
    elif target_issue is None:
        from backend.core.use_cases.daemon_single_instance import find_live_daemon_pid

        lock_dir = _cli.daemon_lock_dir(ctx.runner_settings.console.process_registry_path)
        for context in contexts:
            live_daemon_pid = find_live_daemon_pid(lock_dir, context.repo_id)
            if live_daemon_pid is not None:
                raise CliError(
                    f"A daemon for repository '{context.repo_id}' is already running "
                    f"(PID {live_daemon_pid}); refusing to double-claim the ready queue.",
                    code=ExitCode.CONFLICT,
                    suggestion=(
                        "kc registry stop --repo-id "
                        f"{context.repo_id} (or stop the daemon), or rerun with "
                        "--takeover to stop the daemon and take over."
                    ),
                )
    content_generator = _cli.create_content_generator(
        ctx.process_runner, config=contexts[0].config if contexts else None
    )
    config_by_repo_path = {context.repo_path: context.config for context in contexts}

    def transcript_runner_factory(repo_path: Path) -> object:
        return _cli.create_transcript_runner(config=config_by_repo_path.get(repo_path))

    exit_code = _cli.run_agent_repositories_once(
        contexts=contexts,
        dry_run=parsed.dry_run,
        agent=parsed.agent,
        max_issues=parsed.max_issues or ctx.runner_settings.runner.max_issues,
        process_runner=ctx.process_runner,
        github_client_factory=ctx.github_client_factory,
        content_generator=content_generator,
        run_history_store=_create_run_history_store_or_none(),
        run_trigger=_resolve_run_trigger("run"),
        max_prd_issues=1,
        transcript_runner_factory=transcript_runner_factory,
        max_deliberation_issues=ctx.runner_settings.daemon.max_deliberation_issues,
        target_issue=target_issue,
        publish_stage=publish_stage,
    )
    if not parsed.dry_run or ctx.output_format != OUTPUT_FORMAT_JSON:
        return exit_code
    # 机器模式的 dry-run：stdout 只放执行计划预览（逐 Issue 明细已由日志走
    # stderr），dry-run 通过用退出码 10 与"真跑了并成功"的 0 区分开。
    emit(
        _dry_run_preview(
            contexts,
            agent=parsed.agent,
            max_issues=parsed.max_issues or ctx.runner_settings.runner.max_issues,
            target_issue=target_issue,
            all_ready=all_ready,
            fast_merge=fast_merge,
            publish_stage=publish_stage.value,
        ),
        fmt=OUTPUT_FORMAT_JSON,
    )
    if exit_code:
        return exit_code
    return int(ExitCode.DRY_RUN_OK)


def _resolve_publish_stage(*, fast_merge: bool, direct_pr: bool) -> PublishStage:
    """把 ``kc run`` 的两个旁路旗标折算成一个发布档位。

    两个旗标刻意互斥而不「取更强者」：它们的存在感不同（一个跳独立验证，一个连
    reviewer 与仓库验证一起跳），静默升级会掩盖调用者的真实意图。

    Args:
        fast_merge: 是否给出 ``--fast-merge``。
        direct_pr: 是否给出 ``--direct-pr``。

    Returns:
        本次运行的发布档位。

    Raises:
        CliError: 两个旗标同时给出。
    """
    if fast_merge and direct_pr:
        raise CliError(
            "--fast-merge and --direct-pr are mutually exclusive; --direct-pr already "
            "includes everything --fast-merge skips, so combining them is ambiguous "
            "rather than stronger.",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --fast-merge · kc run --issue <N> --direct-pr",
        )
    if direct_pr:
        return PublishStage.DIRECT
    if fast_merge:
        return PublishStage.FAST
    return PublishStage.NORMAL


def _target_issue_details(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
    flag_name: str,
    check_purpose: str,
) -> list:
    """读取每个目标仓库里该 Issue 的详情；读不到即以用法错误拒绝旁路档位。

    两个旁路档位的前置检查都是 fail-closed 的：无法从 Issue 详情证明条件成立，就不能
    放行跳过门禁的运行。

    Args:
        ctx: 已解析命令上下文（提供 Issue 读取客户端工厂）。
        contexts: 目标仓库列表（同仓只读一次）。
        target_issue: 目标 Issue 编号。
        flag_name: 触发本次检查的旗标名，用于报错与修复建议。
        check_purpose: 读取详情要回答的问题（用于报错信息）。

    Returns:
        每个去重后仓库的 Issue 详情（含正文与标签）。

    Raises:
        CliError: Issue 无法读取。
    """
    details: list = []
    for context in _unique_repository_contexts(contexts):
        github_client = ctx.github_client_factory(context.repo_path)
        try:
            details.append(github_client.get_issue(target_issue))
        except Exception as exc:  # noqa: BLE001 - 无法证明前置条件即拒绝旁路
            raise CliError(
                f"{flag_name} requires reading Issue #{target_issue} to check "
                f"{check_purpose}, but the lookup failed: {exc}",
                code=ExitCode.USAGE,
                suggestion=f"Verify the Issue number and repository access, "
                f"or rerun without {flag_name}.",
            ) from exc
    return details


def _target_issue_bodies(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
    flag_name: str,
    check_purpose: str,
) -> list[str]:
    """读取每个目标仓库里该 Issue 的正文（``_target_issue_details`` 的正文投影）。"""
    return [
        issue_detail.body
        for issue_detail in _target_issue_details(
            ctx,
            contexts=contexts,
            target_issue=target_issue,
            flag_name=flag_name,
            check_purpose=check_purpose,
        )
    ]


def _unique_repository_contexts(contexts: list) -> list:
    """按仓库路径去重的目标上下文列表（多仓 ``--repo`` 时同一仓只查一次）。"""
    unique: list = []
    seen_paths: set[Path] = set()
    for context in contexts:
        if context.repo_path in seen_paths:
            continue
        seen_paths.add(context.repo_path)
        unique.append(context)
    return unique


def _require_explicit_target_claimable(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """显式定向准入：目标不可领取时给出响亮错误与非零退出码（FR-21）。

    错误原因由 core 层给出（core 不引用 ``ExitCode``），这里映射成退出码：
    读不到 / 非 open → ``NOT_FOUND (3)``，被他人活跃认领或 blocked 未解除 →
    ``CONFLICT (5)``。

    Raises:
        CliError: 目标 Issue 当前不可被本进程领取。
    """
    from backend.core.use_cases.run_target_admission import (
        TARGET_UNCLAIMABLE_NOT_FOUND,
        TargetNotClaimableError,
        require_explicit_target_claimable,
    )

    for context in _unique_repository_contexts(contexts):
        try:
            require_explicit_target_claimable(
                issue_number=target_issue,
                github_client=ctx.github_client_factory(context.repo_path),
                config=context.config,
            )
        except TargetNotClaimableError as exc:
            raise CliError(
                str(exc),
                code=(
                    ExitCode.NOT_FOUND
                    if exc.reason == TARGET_UNCLAIMABLE_NOT_FOUND
                    else ExitCode.CONFLICT
                ),
                suggestion=exc.suggestion,
            ) from exc


def _has_existing_direct_pr_cleanup(
    github_client: IGitHubClient, issue_detail: IssueSummary
) -> bool:
    """只读核验成功同轮 PR，让 core 认领后补交接而非重新构建。"""
    from backend.core.use_cases.agent_runner_direct_pr_round import has_readonly_direct_pr_cleanup

    try:
        return has_readonly_direct_pr_cleanup(github_client, issue_detail)
    except Exception as exc:
        raise CliError(
            f"Cannot establish existing Direct PR handoff for Issue #{issue_detail.number}: {exc}",
            code=ExitCode.USAGE,
        ) from exc


def _reject_fast_merge_on_stack_issue(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """快速通道 stack 门禁：目标 Issue 声明 ``mode="stack"`` 依赖时拒绝放行。

    检查发生在任何 agent 启动与 daemon 接管之前；Issue 取不到时同样拒绝
    （无法证明不是 stack 就不走旁路，fail-closed）。

    Raises:
        CliError: 目标 Issue 声明 stack 顺序依赖，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker

    for context in _unique_repository_contexts(contexts):
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--fast-merge",
            check_purpose="its dependency declaration",
        ):
            declaration = parse_dependency_marker(issue_detail.body)
            if declaration is None or declaration.sequence != "stack":
                continue
            if _has_existing_direct_pr_cleanup(
                ctx.github_client_factory(context.repo_path), issue_detail
            ):
                continue
            upstream = ", ".join(f"#{number}" for number in declaration.issue_numbers)
            raise CliError(
                f"Issue #{target_issue} declares a stack dependency (upstream: {upstream}); "
                "--fast-merge is rejected because an unverified upstream would poison "
                "every fork on the chain.",
                code=ExitCode.USAGE,
                suggestion=f"Run Issue #{target_issue} without --fast-merge, or "
                "fast-merge the upstream Issue first.",
            )


def _reject_fast_merge_on_direct_pr_label(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """快速通道与直发标签的冲突门禁：目标 Issue 带 ``direct-pr`` 标签时拒绝放行。

    两个旁路的跳入门禁范围不同（直发连审核 agent 与仓内验证一起跳），同时给出时不猜
    优先级，明确报冲突。core 在认领后还会再判一次，这里让单定向的调用方在启动任何
    agent（乃至 takeover 停 daemon）之前就听到错误。Issue 读不到时同样拒绝（fail-closed）。

    Raises:
        CliError: 目标 Issue 带直发标签，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_direct_pr_label import configured_direct_pr_label

    for context in _unique_repository_contexts(contexts):
        label = configured_direct_pr_label(context.config)
        if label is None:
            continue
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--fast-merge",
            check_purpose=f"whether it carries the '{label}' label",
        ):
            if label in issue_detail.labels:
                if _has_existing_direct_pr_cleanup(
                    ctx.github_client_factory(context.repo_path), issue_detail
                ):
                    continue
                raise CliError(
                    f"Issue #{target_issue} carries the '{label}' label while --fast-merge "
                    "was requested; the two bypass tiers skip different gates, so neither "
                    "is silently chosen.",
                    code=ExitCode.USAGE,
                    suggestion=f"kc run --issue {target_issue} (drops --fast-merge and honors the "
                    f"Issue direct track) · or remove '{label}' from Issue #{target_issue} to run "
                    "the fast track.",
                )


def _reject_direct_pr_on_prd_backed_issue(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    target_issue: int,
) -> None:
    """直发档范围门禁：目标 Issue 带 PRD 锚点时拒绝放行（FR-16）。

    PRD-backed Issue 必须走 PRD 交付门（校验验收清单、归档 PRD），那是 PRD 卫生的
    一部分，不能被「快速出 PR」旁路。Issue 正文读不到时同样拒绝（fail-closed）。

    Raises:
        CliError: 目标 Issue 带 PRD 锚点，或 Issue 无法读取。
    """
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    for context in _unique_repository_contexts(contexts):
        for issue_detail in _target_issue_details(
            ctx,
            contexts=[context],
            target_issue=target_issue,
            flag_name="--direct-pr",
            check_purpose="whether it carries a PRD anchor",
        ):
            prd_path = extract_prd_path(issue_detail.body)
            if prd_path is None or _has_existing_direct_pr_cleanup(
                ctx.github_client_factory(context.repo_path), issue_detail
            ):
                continue
            raise CliError(
                f"Issue #{target_issue} is PRD-backed (anchor: `{prd_path}`); --direct-pr "
                "is only defined for Issues without a PRD anchor, because a PRD-backed "
                "Issue must pass the PRD delivery gate and archive its PRD.",
                code=ExitCode.USAGE,
                suggestion=f"kc run --issue {target_issue} --fast-merge (keeps the "
                "reviewer and repository verification, and the PRD gate still applies) "
                f"· or rerun --direct-pr without a PRD-anchored Issue.",
            )


def _confirm_and_take_over_daemons(
    ctx: ParsedCommandContext,
    *,
    contexts: list,
    assume_yes: bool,
) -> None:
    """执行 ``--takeover``：强警告 + 确认，然后逐仓优雅停 daemon 并 reclaim。

    Raises:
        CliError: 机器输出模式下未显式 ``--yes``（不可交互确认），或用户拒绝。
    """
    from backend.core.use_cases.daemon_single_instance import find_live_daemon_pid

    lock_dir = _cli.daemon_lock_dir(ctx.runner_settings.console.process_registry_path)
    targets: list[tuple[object, int]] = []
    for context in contexts:
        live_daemon_pid = find_live_daemon_pid(lock_dir, context.repo_id)
        if live_daemon_pid is not None:
            targets.append((context, live_daemon_pid))
    if not targets:
        return

    # 机器模式没有交互确认的余地：必须显式 --yes。
    if ctx.output_format == OUTPUT_FORMAT_JSON and not assume_yes:
        raise CliError(
            "--takeover in machine (--json) mode requires --yes: it stops the "
            "daemon and interrupts all of its in-flight Issues.",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --takeover --yes --repo-id <repo>",
        )

    github_clients: dict[Path, object] = {}
    for context, daemon_pid in targets:
        github_client = github_clients.setdefault(
            context.repo_path, ctx.github_client_factory(context.repo_path)
        )
        running_issues = github_client.list_issues_by_label(context.config.labels.running, limit=50)
        console.print("[bold red]⚠️  TAKEOVER — destructive action[/]")
        console.print(
            f"- Repository: [bold]{context.repo_id}[/] · daemon PID [bold]{daemon_pid}[/] "
            "will be stopped gracefully"
        )
        console.print(
            f"- In-flight Issues that will be interrupted and reclaimed: "
            f"[bold]{len(running_issues)}[/] ({', '.join(f'#{issue.number}' for issue in running_issues) or 'none'})"
        )
    if not assume_yes:
        from rich.prompt import Confirm

        if not Confirm.ask("Stop the daemon(s) and take over now?", default=False):
            raise CliError(
                "Takeover aborted by the user; the daemon was left untouched.",
                code=ExitCode.USAGE,
                suggestion="kc run --issue <N> --repo-id <repo> --takeover --yes",
            )
    from backend.api.cli_run_takeover import take_over_daemon

    for context, daemon_pid in targets:
        takeover_result = take_over_daemon(
            repo_id=context.repo_id,
            daemon_pid=daemon_pid,
            config=context.config,
            github_client=github_clients[context.repo_path],
            process_registry_path=ctx.runner_settings.console.process_registry_path,
            process_log_dir=ctx.runner_settings.console.process_log_dir,
        )
        console.print(
            f"[green]Daemon stopped ({takeover_result.final_signal}); reclaimed "
            f"{len(takeover_result.reclaimed_issues)} in-flight Issue(s).[/]"
        )


def run_daemon_command(ctx: ParsedCommandContext) -> int:
    """``kc daemon``: run daemon continuously or report status."""
    daemon_command = getattr(ctx.parsed, "daemon_command", "run")
    if daemon_command == "status":
        return _run_daemon_status_command(
            parsed=ctx.parsed,
            process_runner=ctx.process_runner,
            runner_settings=ctx.runner_settings,
            repo_id=ctx.repo_id,
            repo_override=ctx.repo_override,
        )
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（implementation 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="implementation")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.run_interval_seconds
    )
    # Parallel execution: --concurrency overrides the toml default
    # ([agent_runner.runner].max_concurrent_issues; 1 = sequential).
    # A per-Issue live view (Rich on a TTY, plain otherwise) is created
    # only when running >1 in parallel; the sequential path is unchanged.
    daemon_concurrency = (
        getattr(ctx.parsed, "concurrency", None) or ctx.runner_settings.runner.max_concurrent_issues
    )
    daemon_output_view = create_runner_live_view() if daemon_concurrency > 1 else None

    def content_generator_factory(repo_path: Path):
        return _cli.create_content_generator(
            ctx.process_runner, config=config_by_repo_path.get(repo_path)
        )

    config_by_repo_path = {context.repo_path: context.config for context in contexts}

    def transcript_runner_factory(repo_path: Path) -> object:
        return _cli.create_transcript_runner(config=config_by_repo_path.get(repo_path))

    # Single-instance guard: a second daemon for an already-served
    # repository would double the queue polling and agent spawns, so
    # refuse to start rather than pile up duplicate daemons.
    daemon_repo_ids = [context.repo_id for context in contexts]
    daemon_locks_dir = _cli.daemon_lock_dir(ctx.runner_settings.console.process_registry_path)
    try:
        acquired_daemon_locks = _cli.acquire_daemon_locks(daemon_locks_dir, daemon_repo_ids)
    except _cli.DaemonAlreadyRunningError as already_running:
        raise CliError(
            str(already_running),
            code=ExitCode.CONFLICT,
            suggestion="kc daemon status",
        ) from already_running
    try:
        _cli.run_agent_daemon(
            contexts=contexts,
            interval=interval,
            agent=ctx.parsed.agent,
            max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
            process_runner=ctx.process_runner,
            github_client_factory=ctx.github_client_factory,
            content_generator_factory=content_generator_factory,
            run_history_store=_create_run_history_store_or_none(),
            run_trigger=_resolve_run_trigger("daemon"),
            max_prd_issues=1,
            transcript_runner_factory=transcript_runner_factory,
            max_deliberation_issues=ctx.runner_settings.daemon.max_deliberation_issues,
            concurrency=daemon_concurrency,
            output_view=daemon_output_view,
            reconcile_stale_attempts=ctx.runner_settings.daemon.reconcile_stale_attempts,
            reclaim_ttl_seconds=ctx.runner_settings.daemon.reclaim_ttl_seconds,
            # Continuous backlog scheduling: injected as a factory so core never
            # constructs infrastructure objects itself. Only repositories with
            # backlog.auto_advance enabled run the stage.
            # --autopilot/--no-autopilot 的按次覆盖（None = 未传，热读配置）。
            backlog_store_factory=_cli.create_backlog_store,
            autopilot_override=getattr(ctx.parsed, "autopilot_override", None),
        )
    finally:
        _cli.release_daemon_locks(acquired_daemon_locks)
    return 0


def run_review_command(ctx: ParsedCommandContext) -> int:
    """``kc review``: one supervisor review polling cycle."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（supervisor 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    review_contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="supervisor")
    for context in review_contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if review_contexts:
        _ensure_gh_auth_or_prompt(review_contexts[0].repo_path, ctx.process_runner)
    aggregated_exit_code = 0
    for context in review_contexts:
        github_client = ctx.github_client_factory(context.repo_path)
        try:
            repo_exit_code = _cli.review_once(
                repo_path=context.repo_path,
                config=context.config,
                dry_run=ctx.parsed.dry_run,
                agent=ctx.parsed.agent,
                max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
                github_client=github_client,
                process_runner=ctx.process_runner,
                run_history_store=_create_run_history_store_or_none(),
                repo_id=context.repo_id,
            )
            if repo_exit_code != 0:
                aggregated_exit_code = 1
        except Exception as exc:  # noqa: BLE001
            aggregated_exit_code = 1
            logger.error(
                "Repository '%s' review_once failed: %s",
                context.repo_id,
                exc,
            )
    return aggregated_exit_code


def run_review_daemon_command(ctx: ParsedCommandContext) -> int:
    """``kc review-daemon``: run supervisor review continuously."""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    # CLI --preset 一次性锚定（supervisor 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage="supervisor")
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.review_interval_seconds
    )
    _cli.run_review_daemon(
        contexts=contexts,
        interval=interval,
        agent=ctx.parsed.agent,
        max_issues=ctx.parsed.max_issues or ctx.runner_settings.runner.max_issues,
        process_runner=ctx.process_runner,
        github_client_factory=ctx.github_client_factory,
        run_history_store=_create_run_history_store_or_none(),
    )
    return 0


def run_recover_command(ctx: ParsedCommandContext) -> int:
    """``kc recover``: resume a failed publish operation for an Issue."""
    from backend.core.use_cases.recover_publish import (
        PublishRecoveryError,
        PublishRecoveryRequest,
        recover_publish_issue,
    )

    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        raise CliError(
            "kc recover requires exactly one target repository. "
            "Use --repo or --repo-id to specify.",
            code=ExitCode.USAGE,
            suggestion="kc registry list",
        )
    context = contexts[0]
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    # 恢复发布的 PR 复用正常发布的正文生成，因此需要内容生成器；缺失时正常发布
    # 用例会退回配置的 template / fallback 正文。
    content_generator = _cli.create_content_generator(ctx.process_runner, config=context.config)
    request = PublishRecoveryRequest(
        issue_number=ctx.parsed.issue,
        expected_branch=ctx.parsed.branch,
    )
    try:
        result = recover_publish_issue(
            request=request,
            repo_path=context.repo_path,
            config=context.config,
            github_client=github_client,
            process_runner=ctx.process_runner,
            content_generator=content_generator,
        )
        logger.info(
            "Publish recovered for Issue #%d: %s",
            result.issue_number,
            result.pr_url,
        )
        console.print(
            f"[green]Publish recovered for Issue #{result.issue_number}:[/] {result.pr_url}"
        )
        return 0
    except PublishRecoveryError as exc:
        logger.error(
            "Publish recovery failed (category=%s): %s",
            exc.failure_category,
            exc,
        )
        return 1


def run_blocked_continue_command(ctx: ParsedCommandContext) -> int:
    """``kc blocked-continue``: resume a blocked Issue after fixing paths."""
    from backend.core.use_cases.blocked_continue import (
        BlockedContinueError,
        blocked_continue_issue,
    )
    from backend.api.cli_console import error_console

    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    for context in contexts:
        _cli.require_iar_repository_initialized(context.repo_path, ctx.process_runner)
    if len(contexts) != 1:
        raise CliError(
            "kc blocked-continue requires exactly one target repository. "
            "Use --repo or --repo-id to specify.",
            code=ExitCode.USAGE,
            suggestion="kc registry list",
        )
    context = contexts[0]
    github_client = _cli.create_github_client(context.repo_path, ctx.process_runner)
    try:
        claimed = blocked_continue_issue(
            issue_number=ctx.parsed.issue,
            repo_path=context.repo_path,
            config=context.config,
            agent=ctx.parsed.agent,
            github_client=github_client,
            process_runner=ctx.process_runner,
        )
        if claimed:
            console.print(f"[green]Issue #{ctx.parsed.issue} resumed successfully.[/]")
            return 0
        console.print(f"[yellow]Issue #{ctx.parsed.issue} was claimed by another runner.[/]")
        return 0
    except BlockedContinueError as exc:
        logger.error("blocked-continue failed: %s", exc)
        error_console.print(f"[red]blocked-continue failed:[/] {exc}")
        return 1


__all__ = [
    "run_blocked_continue_command",
    "run_daemon_command",
    "run_recover_command",
    "run_review_command",
    "run_review_daemon_command",
    "run_run_command",
]
