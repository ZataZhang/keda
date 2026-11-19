"""``kc run`` / ``kc daemon`` / ``kc supervise`` / ``kc supervise-daemon``
/ ``kc recover`` / ``kc blocked-continue`` handlers.

Extracted from :mod:`backend.api.cli`'s monolithic ``_run_parsed_command``
dispatcher.
"""

from __future__ import annotations

from dataclasses import replace
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
from backend.api.cli_parsed_commands.runner_publish_guards import (
    _reject_direct_pr_on_prd_backed_issue,
    _reject_fast_merge_on_direct_pr_label,
    _reject_fast_merge_on_stack_issue,
    _require_explicit_target_claimable,
    _resolve_publish_stage,
)
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_run_progress import RunProgressStream
from backend.api.cli_registry import _run_daemon_status_command
from backend.api.agent_runner_views.runner_live_view import create_runner_live_view
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.shared.models.agent_runner_config import AppConfig
from backend.core.shared.models.publish_stage import PublishStage
from backend.api import cli as _cli
from backend.core.shared.interfaces.runner_console import RunnerProcessKind
from backend.core.use_cases.agent_daemon_loops import DaemonReviewStage
from backend.core.use_cases.agent_runner_factory import (
    create_machine_identity_store,
    create_process_supervisor,
    create_registry_editor,
    load_fresh_agent_runner_settings,
    logger,
)


#: ``kc run`` / ``kc supervise`` / ``kc supervise-daemon`` 未传 ``--max-issues`` 时
#: 本次最多处理几个 Issue。配置项 ``runner.max_issues`` 已退役：常驻 daemon 的
#: 并发只看并行上限（``--concurrency`` > Backlog 页面设置 > ``max_concurrent_issues``）。
DEFAULT_BATCH_MAX_ISSUES = 1


def _start_coordination_heartbeat(ctx: ParsedCommandContext, contexts: list, *, role: str):
    """为真实运行入口启动机器状态与认领租约服务。"""
    from backend.api.cli_coordination import start_coordination_heartbeat

    return start_coordination_heartbeat(
        contexts=contexts,
        github_client_factory=ctx.github_client_factory,
        process_runner=ctx.process_runner,
        role=role,
    )


def _resolve_initialized_repository_contexts(
    ctx: ParsedCommandContext, *, anchored_stage: str | None = None
) -> list[RepositoryRunContext]:
    """解析仓库目标、可选锚定模型预设，并检查每个仓库已初始化。"""
    contexts = _resolve_cli_repository_targets(
        parsed=ctx.parsed,
        runner_settings=ctx.runner_settings,
        repo_id=ctx.repo_id,
        repo_override=ctx.repo_override,
    )
    if anchored_stage is not None:
        from backend.api.cli_model_preset_anchor import apply_cli_model_preset

        contexts = apply_cli_model_preset(contexts, ctx.parsed, anchored_stage=anchored_stage)
    for context in contexts:
        _cli.require_repository_initialized(context.repo_path, ctx.process_runner)
    return contexts


def _with_lifecycle_reload(
    context: RepositoryRunContext, ctx: ParsedCommandContext
) -> RepositoryRunContext:
    """保留启动目标，在阶段边界读取同一仓库的当前配置。"""
    from backend.api.cli_model_preset_anchor import CLI_PRESET_NAME

    startup_preset = context.config.agent_presets.get(CLI_PRESET_NAME)

    def load_stage_config() -> AppConfig:
        """读取新配置并恢复启动时的 CLI 预设覆盖。"""
        fresh_contexts = _resolve_cli_repository_targets(
            parsed=ctx.parsed,
            runner_settings=load_fresh_agent_runner_settings(),
            repo_id=ctx.repo_id if not getattr(ctx.parsed, "all_repositories", False) else None,
            repo_override=ctx.repo_override,
        )
        for fresh_context in fresh_contexts:
            if (fresh_context.repo_id, fresh_context.repo_path) == (
                context.repo_id,
                context.repo_path,
            ):
                fresh_config = fresh_context.config
                if startup_preset is None:
                    return fresh_config
                return replace(
                    fresh_config,
                    agent_presets={**fresh_config.agent_presets, CLI_PRESET_NAME: startup_preset},
                    lifecycle_presets=replace(
                        fresh_config.lifecycle_presets,
                        global_layer=context.config.lifecycle_presets.global_layer,
                    ),
                )
        raise ValueError(f"Cannot reload configuration for repository '{context.repo_id}'.")

    return replace(
        context, config=replace(context.config, lifecycle_config_loader=load_stage_config)
    )


def _validate_aggregate_run_options(parsed: object, configured_max_issues: int) -> None:
    """在任何仓库解析或 client 创建前校验自动聚合参数组合。"""
    if not getattr(parsed, "aggregate_pr", False):
        return
    max_issues = getattr(parsed, "max_issues", None)
    max_issues = configured_max_issues if max_issues is None else max_issues
    suggestions = "kc run --all-ready --aggregate-pr --max-issues 2"
    if not getattr(parsed, "all_ready", False):
        message = "--aggregate-pr is only available with --all-ready."
    elif getattr(parsed, "issue", None) is not None or getattr(parsed, "prd_path", None):
        message = "--aggregate-pr cannot be combined with a targeted Issue or PRD path."
    elif getattr(parsed, "direct_pr", False) or getattr(parsed, "fast_merge", False):
        message = "--aggregate-pr cannot be combined with --direct-pr or --fast-merge."
    elif getattr(parsed, "all_repositories", False):
        message = "--aggregate-pr requires one explicitly selected repository."
        suggestions = "kc run --repo-id <repo> --all-ready --aggregate-pr --max-issues 2"
    elif max_issues < 2:
        message = "--aggregate-pr requires --max-issues of at least 2."
    else:
        return
    raise CliError(message, code=ExitCode.USAGE, suggestion=suggestions)


def _dry_run_preview(
    contexts: list,
    *,
    agent: str,
    max_issues: int,
    target_issue: int | None = None,
    all_ready: bool = False,
    fast_merge: bool = False,
    aggregate_pr: bool = False,
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
        "aggregate_pr": aggregate_pr,
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
    # --json 的 stdout 只定义在 --dry-run 预览组合下；真实执行的结构化进度走
    # 单独的 stderr 事件流，避免与 Agent 原始 stdout 混合。在解析仓库目标前 fail fast。
    if ctx.output_format == OUTPUT_FORMAT_JSON and not ctx.parsed.dry_run:
        raise CliError(
            "--json/--output json 只在 --dry-run 预览下有定义；真实执行请用 --progress "
            "获取结构化状态事件。",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --progress",
        )
    parsed = ctx.parsed
    progress_events_enabled = bool(getattr(parsed, "progress_events", False))
    if progress_events_enabled and parsed.dry_run:
        raise CliError(
            "--progress 用于真实前台执行，不与 --dry-run 预览合用。",
            code=ExitCode.USAGE,
            suggestion="kc run --issue <N> --progress",
        )
    prd_path = getattr(parsed, "prd_path", None)
    target_issue = getattr(parsed, "issue", None)
    all_ready = getattr(parsed, "all_ready", False)
    takeover = getattr(parsed, "takeover", False)
    assume_yes = getattr(parsed, "yes", False)
    fast_merge = getattr(parsed, "fast_merge", False)
    direct_pr = getattr(parsed, "direct_pr", False)
    aggregate_pr = getattr(parsed, "aggregate_pr", False)
    publish_stage = _resolve_publish_stage(fast_merge=fast_merge, direct_pr=direct_pr)

    if aggregate_pr:
        _validate_aggregate_run_options(parsed, DEFAULT_BATCH_MAX_ISSUES)

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
    if aggregate_pr and len(contexts) != 1:
        raise CliError(
            "--aggregate-pr requires exactly one repository target.",
            code=ExitCode.USAGE,
            suggestion="kc run --repo-id <repo> --all-ready --aggregate-pr --max-issues 2",
        )
    # CLI --preset 一次性锚定（implementation 阶段）；未传旗标时原样返回。
    from backend.api.cli_model_preset_anchor import apply_cli_model_preset

    contexts = apply_cli_model_preset(contexts, parsed, anchored_stage="implementation")
    for context in contexts:
        _cli.require_repository_initialized(context.repo_path, ctx.process_runner)
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

    # 显式定向始终检查目标认领。takeover 前的预检允许本机 daemon 当前持有目标，
    # 但不能跳过远端有效租约；daemon 停止后再做一次普通准入，挡住仍存活的本机 kc run。
    if target_issue is not None and not parsed.dry_run:
        _require_explicit_target_claimable(
            ctx,
            contexts=contexts,
            target_issue=target_issue,
            ignore_active_local_claim=takeover,
        )

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
        if target_issue is not None and not parsed.dry_run:
            _require_explicit_target_claimable(ctx, contexts=contexts, target_issue=target_issue)
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

    heartbeat = None if parsed.dry_run else _start_coordination_heartbeat(ctx, contexts, role="run")
    progress_stream = (
        RunProgressStream(target_issue=target_issue) if progress_events_enabled else None
    )
    try:
        if progress_stream is not None:
            progress_stream.start()
        exit_code = _cli.run_agent_repositories_once(
            contexts=contexts,
            dry_run=parsed.dry_run,
            agent=parsed.agent,
            max_issues=parsed.max_issues or DEFAULT_BATCH_MAX_ISSUES,
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
            aggregate_pr=aggregate_pr,
            progress_callback=(
                progress_stream.emit_lifecycle_event if progress_stream is not None else None
            ),
        )
    except KeyboardInterrupt:
        if progress_stream is not None:
            progress_stream.finish(
                status="interrupted",
                exit_code=130,
            )
        raise
    except Exception as exc:  # noqa: BLE001 - 终态事件旁路后保留既有异常处理。
        if progress_stream is not None:
            progress_stream.finish(
                status="failed",
                exit_code=_cli.translate_exit_code(exc),
            )
        raise
    else:
        if progress_stream is not None:
            progress_stream.finish(
                status="succeeded" if exit_code == 0 else "failed",
                exit_code=exit_code,
            )
    finally:
        if heartbeat is not None:
            heartbeat.stop()
    if not parsed.dry_run or ctx.output_format != OUTPUT_FORMAT_JSON:
        return exit_code
    # 机器模式的 dry-run：stdout 只放执行计划预览（逐 Issue 明细已由日志走
    # stderr），dry-run 通过用退出码 10 与"真跑了并成功"的 0 区分开。
    emit(
        _dry_run_preview(
            contexts,
            agent=parsed.agent,
            max_issues=parsed.max_issues or DEFAULT_BATCH_MAX_ISSUES,
            target_issue=target_issue,
            all_ready=all_ready,
            fast_merge=fast_merge,
            aggregate_pr=aggregate_pr,
            publish_stage=publish_stage.value,
        ),
        fmt=OUTPUT_FORMAT_JSON,
    )
    if exit_code:
        return exit_code
    return int(ExitCode.DRY_RUN_OK)


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
    machine_id = create_machine_identity_store().load_or_create().machine_id

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
            machine_id=machine_id,
        )
        console.print(
            f"[green]Daemon stopped ({takeover_result.final_signal}); reclaimed "
            f"{len(takeover_result.reclaimed_issues)} in-flight Issue(s).[/]"
        )


def _is_review_daemon_running(repo_id: str) -> bool:
    """探测本机是否已有该仓库的 supervise-daemon；探测失败按已运行处理。"""
    try:
        supervisor = create_process_supervisor()
        process_records = [
            *supervisor.list_processes(),
            *supervisor.list_unmanaged_processes(create_registry_editor().list_repositories()),
        ]
    except Exception:  # noqa: BLE001 - 探测失败时宁可跳过，也不与值守进程并发推进。
        logger.warning(
            "读取 supervise-daemon 进程失败，本轮跳过 daemon 内置 PR 值守", exc_info=True
        )
        return True
    return any(
        record.repo_id == repo_id
        and record.kind == RunnerProcessKind.REVIEW_DAEMON
        and record.status == "running"
        for record in process_records
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
    if getattr(ctx.parsed, "max_issues", None) is not None:
        raise CliError(
            "kc daemon no longer accepts --max-issues: the daemon keeps each repository "
            "at its parallel limit and backfills every polling interval.",
            code=ExitCode.USAGE,
            suggestion="kc daemon run --concurrency <N>",
        )
    contexts = _resolve_initialized_repository_contexts(ctx, anchored_stage="implementation")
    contexts = [_with_lifecycle_reload(context, ctx) for context in contexts]
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.run_interval_seconds
    )
    # --concurrency 是进程级覆盖；未传时由 daemon 按各目标仓库的合并配置解析容量。
    # 只要任一目标仓库会并行执行，就创建共享实时视图；串行目标不会使用它。
    daemon_concurrency_override = getattr(ctx.parsed, "concurrency", None)
    if daemon_concurrency_override is not None:
        target_repository_concurrency_values = [daemon_concurrency_override] * len(contexts)
    else:
        configured_default = getattr(ctx.runner_settings.runner, "max_concurrent_issues", 1)
        if not isinstance(configured_default, int) or isinstance(configured_default, bool):
            configured_default = 1
        target_repository_concurrency_values = []
        for context in contexts:
            context_runner_settings = getattr(getattr(context, "config", None), "runner", None)
            repository_concurrency = getattr(
                context_runner_settings,
                "max_concurrent_issues",
                configured_default,
            )
            if not isinstance(repository_concurrency, int) or isinstance(
                repository_concurrency, bool
            ):
                repository_concurrency = configured_default
            target_repository_concurrency_values.append(repository_concurrency)
    daemon_output_view = (
        create_runner_live_view()
        if any(value > 1 for value in target_repository_concurrency_values)
        else None
    )

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
    heartbeat = None
    try:
        heartbeat = _start_coordination_heartbeat(ctx, contexts, role="daemon")
        _cli.run_agent_daemon(
            contexts=contexts,
            interval=interval,
            agent=ctx.parsed.agent,
            process_runner=ctx.process_runner,
            github_client_factory=ctx.github_client_factory,
            content_generator_factory=content_generator_factory,
            run_history_store=_create_run_history_store_or_none(),
            run_trigger=_resolve_run_trigger("daemon"),
            max_prd_issues=1,
            transcript_runner_factory=transcript_runner_factory,
            max_deliberation_issues=ctx.runner_settings.daemon.max_deliberation_issues,
            concurrency=daemon_concurrency_override,
            output_view=daemon_output_view,
            reconcile_stale_attempts=ctx.runner_settings.daemon.reconcile_stale_attempts,
            reclaim_ttl_seconds=ctx.runner_settings.daemon.reclaim_ttl_seconds,
            # Continuous backlog scheduling: injected as a factory so core never
            # constructs infrastructure objects itself. Only repositories with
            # backlog.auto_advance enabled run the stage.
            # --autopilot/--no-autopilot 的按次覆盖（None = 未传，热读配置）。
            backlog_store_factory=_cli.create_backlog_store,
            autopilot_override=getattr(ctx.parsed, "autopilot_override", None),
            # 普通 daemon 承载 managed_by=agent-daemon 的 persistent loop；
            # loop-daemon 仍只推进默认的 loop-daemon recipe。
            loop_state_store_factory=_cli.create_loop_state_store,
            loop_clock=_cli.create_loop_clock(),
            # 与 Monitor、CLI 共用同一份额度账本：撞限流或额度见底时全员暂停到重置。
            api_budget=_cli.create_github_api_budget(),
            # 实际生效的并行上限写到单实例锁旁，管理终端与 kc daemon status 据此显示
            # 包含 --concurrency 在内的真实值。
            parallel_limit_status_dir=daemon_locks_dir,
            # daemon 内置 PR 值守：发 PR 后的 supervising Issue 由 daemon 自己推进；
            # supervise-daemon 降为可选，本机开着它时 daemon 让出该阶段。
            review_stage=DaemonReviewStage(
                interval_seconds=ctx.runner_settings.daemon.review_interval_seconds,
                max_issues=DEFAULT_BATCH_MAX_ISSUES,
                review_daemon_running=_is_review_daemon_running,
            ),
        )
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        _cli.release_daemon_locks(acquired_daemon_locks)
    return 0


def run_review_command(ctx: ParsedCommandContext) -> int:
    """运行一轮 post-PR supervisor 检查。"""
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
        _cli.require_repository_initialized(context.repo_path, ctx.process_runner)
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
                max_issues=ctx.parsed.max_issues or DEFAULT_BATCH_MAX_ISSUES,
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
    """持续运行 post-PR supervisor 检查。"""
    contexts = _resolve_initialized_repository_contexts(ctx, anchored_stage="supervisor")
    contexts = [_with_lifecycle_reload(context, ctx) for context in contexts]
    if contexts:
        _ensure_gh_auth_or_prompt(contexts[0].repo_path, ctx.process_runner)
    interval = (
        ctx.parsed.interval
        if ctx.parsed.interval is not None
        else ctx.runner_settings.daemon.review_interval_seconds
    )
    heartbeat = _start_coordination_heartbeat(ctx, contexts, role="supervise-daemon")
    try:
        _cli.run_review_daemon(
            contexts=contexts,
            interval=interval,
            agent=ctx.parsed.agent,
            max_issues=ctx.parsed.max_issues or DEFAULT_BATCH_MAX_ISSUES,
            process_runner=ctx.process_runner,
            github_client_factory=ctx.github_client_factory,
            run_history_store=_create_run_history_store_or_none(),
        )
    finally:
        heartbeat.stop()
    return 0


def run_recover_command(ctx: ParsedCommandContext) -> int:
    """``kc recover``: resume a failed publish operation for an Issue."""
    from backend.core.use_cases.recover_publish import (
        PublishRecoveryError,
        PublishRecoveryRequest,
        recover_publish_issue,
    )

    contexts = _resolve_initialized_repository_contexts(ctx)
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
    heartbeat = _start_coordination_heartbeat(ctx, contexts, role="recover")
    try:
        result = recover_publish_issue(
            request=request,
            repo_path=context.repo_path,
            config=context.config,
            github_client=github_client,
            process_runner=ctx.process_runner,
            content_generator=content_generator,
            repo_id=context.repo_id,
        )
    except PublishRecoveryError as exc:
        logger.error(
            "Publish recovery failed (category=%s): %s",
            exc.failure_category,
            exc,
        )
        return 1
    finally:
        heartbeat.stop()
    logger.info(
        "Publish recovered for Issue #%d: %s",
        result.issue_number,
        result.pr_url,
    )
    console.print(f"[green]Publish recovered for Issue #{result.issue_number}:[/] {result.pr_url}")
    return 0


def run_blocked_continue_command(ctx: ParsedCommandContext) -> int:
    """``kc blocked-continue``: resume a blocked Issue after fixing paths."""
    from backend.core.use_cases.blocked_continue import (
        BlockedContinueError,
        blocked_continue_issue,
    )
    from backend.api.cli_console import error_console

    contexts = _resolve_initialized_repository_contexts(ctx)
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
            repo_id=context.repo_id,
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
