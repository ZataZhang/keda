"""Local Issue queue runner — daemon mode."""

from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_runner import (
    IAgentTranscriptRunner,
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.github_api_budget import IGitHubApiBudget
from backend.core.shared.interfaces.runner_console import IBacklogStore, IRunHistoryStore
from backend.core.shared.interfaces.loop_scheduler import ILoopClock, ILoopStateStore
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.shared.models.pass_activity import PassActivity
from backend.core.use_cases.agent_daemon_pacing import (
    IDLE_BACKOFF_MAX_FACTOR,
    USAGE_LOG_INTERVAL_SECONDS,
    budget_pause_sleep_seconds,
    format_usage_summary,
    next_poll_interval,
)
from backend.core.use_cases.agent_daemon_loops import (
    _AgentDaemonLoopRequest,
    AgentDaemonLoopPassRequest,
    run_agent_daemon_loop_pass,
)
from backend.core.use_cases import agent_runner_reconcile
from backend.core.use_cases.agent_runner_orchestrate import (
    process_prd_rework_issues,
    run_once,
)
from backend.core.use_cases.agent_runner_deliberation_issues import run_deliberation_phase
from backend.core.use_cases.backlog_actions import advance_backlog_queue
from backend.core.use_cases.backlog_concurrency import (
    CEILING_SOURCE_CLI_OVERRIDE,
    describe_ceiling_source,
    read_policy_max_parallel,
    resolve_execution_ceiling,
)
from backend.core.use_cases.hosted_maintenance import (
    DiskWatermarkAdmissionGate,
    cleanup_expired_issue_logs,
)
from backend.core.use_cases.worktree_cleanup import (
    WorktreeCleanupRequest,
    WorktreeCleanupStatus,
    cleanup_managed_worktrees,
)
from backend.core.use_cases.daemon_execution_slots import DaemonExecutionSlots
from backend.core.use_cases.agent_daemon_shutdown import (
    mark_agent_daemon_stopping,
    reset_agent_daemon_stopping,
)
from backend.core.use_cases.daemon_parallel_limit_status import (
    DaemonParallelLimitStatus,
    clear_daemon_parallel_limit_status,
    write_daemon_parallel_limit_status,
)

_logger = logging.getLogger(__name__)

#: SIGTERM 后等待在途 agent 进程组自行退出的秒数，超时升级 SIGKILL
#: （与 :class:`PidfileProcessSupervisor` 的停止语义一致，禁止把 SIGKILL
#: 当作停 daemon 的首手段）。
_DESCENDANT_TERM_GRACE_SECONDS = 10.0


def _import_psutil() -> Any:
    """按需导入 psutil；缺失时返回 ``None``（清理降级为 no-op）。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001 - psutil 是可选依赖。
        return None
    return psutil


def _collect_descendant_group_ids() -> set[int]:
    """收集本进程全部后代进程的进程组 ID（排除自己的组与 init 组）。

    agent 子进程由 ``process_group=0`` 放进独立进程组，向组发信号即可
    整组回收（含 agent 派生的后台进程），不误伤 daemon 自己所在的组。

    Returns:
        可安全 kill 的进程组 ID 集合；psutil 不可用或扫描失败时为空集。
    """
    psutil = _import_psutil()
    if psutil is None:
        return set()
    try:
        own_group_id = os.getpgid(0)
        descendants = psutil.Process().children(recursive=True)
    except Exception:  # noqa: BLE001 - 扫描失败时放弃组收集，靠 reclaim 兜底。
        return set()
    group_ids: set[int] = set()
    for descendant in descendants:
        try:
            descendant_group_id = os.getpgid(descendant.pid)
        except OSError:
            continue
        if descendant_group_id > 1 and descendant_group_id != own_group_id:
            group_ids.add(descendant_group_id)
    return group_ids


def _terminate_descendant_process_trees() -> None:
    """SIGTERM 后清理在途 agent 子进程树（尽力而为）。

    停止顺序与进程监管器一致：整组 SIGTERM → 等待宽限期 → 仍存活则
    SIGKILL。任何一步失败都不抛出——shutdown 路径必须能走完。
    """
    group_ids = _collect_descendant_group_ids()
    if not group_ids:
        return
    for group_id in group_ids:
        try:
            os.killpg(group_id, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + _DESCENDANT_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        alive_group_ids: set[int] = set()
        for group_id in group_ids:
            try:
                os.killpg(group_id, 0)  # 信号 0 只探活，不发送。
            except OSError:
                continue
            alive_group_ids.add(group_id)
        if not alive_group_ids:
            break
        time.sleep(0.1)
    for group_id in group_ids:
        try:
            os.killpg(group_id, signal.SIGKILL)
        except OSError:
            pass


def run_agent_daemon(
    *,
    contexts: list[RepositoryRunContext],
    interval: int,
    agent: str,
    process_runner: IProcessRunner,
    github_client_factory: Callable[[Path], IGitHubClient],
    content_generator_factory: Callable[[Path], IContentGenerator] | None = None,
    run_history_store: IRunHistoryStore | None = None,
    run_trigger: str = "cli_daemon",
    max_prd_issues: int = 1,
    transcript_runner_factory: Callable[[Path], IAgentTranscriptRunner] | None = None,
    max_deliberation_issues: int = 1,
    concurrency: int | None = None,
    output_view: IRunnerLiveView | None = None,
    reconcile_stale_attempts: bool = False,
    reclaim_ttl_seconds: int | None = None,
    backlog_store_factory: Callable[[], IBacklogStore] | None = None,
    autopilot_override: bool | None = None,
    loop_state_store_factory: Callable[[], ILoopStateStore] | None = None,
    loop_clock: ILoopClock | None = None,
    api_budget: IGitHubApiBudget | None = None,
    idle_backoff_max_factor: int = IDLE_BACKOFF_MAX_FACTOR,
    parallel_limit_status_dir: Path | None = None,
) -> None:
    """Run the queue poller forever across all target repositories.

    Args:
        contexts: Resolved repository targets with merged configurations.
        interval: Seconds between polling passes.
        agent: Agent override (auto, codex, claude).
        process_runner: Runner for executing subprocess commands.
        github_client_factory: Factory that creates an IGitHubClient for a repo path.
        content_generator_factory: Optional factory that creates an IContentGenerator
            for a repo path. When omitted, PRD rework uses template/fallback mode.
        run_history_store: Optional side-channel run history store.
        run_trigger: Trigger source recorded with each run record.
        max_prd_issues: Maximum rework-prd issues to process per pass per repository.
        transcript_runner_factory: Optional factory that returns an
            :class:`IAgentTranscriptRunner` for a repo path. When omitted, the
            Phase 0 deliberation queue is skipped entirely (zero regression for
            callers that do not assemble a runner).
        max_deliberation_issues: Maximum ``agent/deliberate`` Issues to process
            per Phase 0 pass. Defaults to 1 to bound multi-agent cost.
        concurrency: ``kc daemon run --concurrency`` 的进程级覆盖：每个目标仓库最多
            同时运行几个 Issue。省略时每个仓库按 Backlog 页面设置 > 配置文件
            ``max_concurrent_issues`` 解析；执行期间每个轮询间隔都会补位。
        output_view: Optional live view for parallel runs; each Issue's agent
            output goes to its own panel. ``None`` shows no dashboard (per-Issue
            log files are still written).
        reconcile_stale_attempts: Whether to run the Phase -1 crash-reconciliation
            pass before polling. Off (default) leaves the phase inert — a zombie
            ``agent/running`` Issue stays untouched, which is the pre-feature
            behaviour the PRD's negative control pins down.
        reclaim_ttl_seconds: Optional claim-age threshold for the reconcile pass.
        backlog_store_factory: Optional factory returning an
            :class:`IBacklogStore`. When provided *and* the repository has
            ``backlog.auto_advance``, each pass runs a continuous-scheduling stage
            before Phase 2 so finished PRDs release their slot and the next
            queued PRD is promoted in the same pass. When omitted, the stage is
            skipped entirely (zero regression for existing callers).
        autopilot_override: ``kc daemon --autopilot / --no-autopilot`` 的按次
            覆盖，只作用于 Backlog 自动推进（``True``/``False``），优先级
            ``flag > repo .kedacode.toml > 全局`` 并锁定本次常驻进程；``None`` 表示
            未传旗标，每轮热读配置。该覆盖**不**影响 review 侧合并队列：
            全仓自动合并仍由 ``safety.auto_merge`` + ``autopilot.enabled`` 双开关决定；
            单 PRD 授权不依赖此 Backlog 调度覆盖。
        loop_state_store_factory: 普通 agent daemon 承载的 persistent loop 状态存储工厂；
            省略时不运行 loop 阶段。
        loop_clock: 可选 agent-daemon loop 阶段使用的时钟。
        api_budget: 跨进程共享的 GitHub API 额度预算；额度暂停期内整轮跳过轮询，
            避免发出注定失败的请求。省略时不做额度闸门。
        idle_backoff_max_factor: 连续空闲时轮询间隔最多拉长到基础间隔的几倍；
            ``1`` 关闭退避。
        parallel_limit_status_dir: daemon 单实例锁目录。传入时每次轮询把每个仓库实际
            生效的并行上限写到锁旁边的状态文件，供管理终端与 ``kc daemon status``
            读取（命令行 ``--concurrency`` 只有 daemon 自己知道）；退出时删除。
    """
    previous_sigterm_handler: Any = signal.getsignal(signal.SIGTERM)
    reset_agent_daemon_stopping()

    def _handle_sigterm(signum: int, frame: Any) -> None:
        """优雅停机：先整组终止在途 agent 子进程树，再退出主循环。"""
        mark_agent_daemon_stopping()
        _logger.info("Daemon received SIGTERM; terminating in-flight agent trees before exit.")
        _terminate_descendant_process_trees()
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError):  # noqa: BLE001 - 非主线程（如测试）时不装钩子。
        _logger.debug("SIGTERM shutdown hook not installed (not main thread or no permission).")

    try:
        _run_daemon_loop(
            _AgentDaemonLoopRequest(
                contexts=contexts,
                interval=interval,
                agent=agent,
                process_runner=process_runner,
                github_client_factory=github_client_factory,
                content_generator_factory=content_generator_factory,
                run_history_store=run_history_store,
                run_trigger=run_trigger,
                max_prd_issues=max_prd_issues,
                transcript_runner_factory=transcript_runner_factory,
                max_deliberation_issues=max_deliberation_issues,
                concurrency=concurrency,
                output_view=output_view,
                reconcile_stale_attempts=reconcile_stale_attempts,
                reclaim_ttl_seconds=reclaim_ttl_seconds,
                backlog_store_factory=backlog_store_factory,
                autopilot_override=autopilot_override,
                loop_state_store_factory=loop_state_store_factory,
                loop_clock=loop_clock,
                api_budget=api_budget,
                idle_backoff_max_factor=idle_backoff_max_factor,
                parallel_limit_status_dir=parallel_limit_status_dir,
            )
        )
    finally:
        if parallel_limit_status_dir is not None:
            clear_daemon_parallel_limit_status(
                parallel_limit_status_dir, [context.repo_id for context in contexts]
            )
        try:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
        except (ValueError, OSError):
            pass


def _resolve_reconcile_settings(
    config: AppConfig,
    *,
    default_enabled: bool,
    default_ttl_seconds: int | None,
) -> tuple[bool, int | None]:
    """把仓库级 ``[agent_runner.daemon]`` 对账开关解析成生效值。

    ``.kedacode.toml`` 显式写了某项时该仓单独生效（daemon 可同时服务多个仓库，
    开关必须按仓判定）；没写时沿用调用方传入的全局默认，行为与本特性前一致。

    Args:
        config: 该仓库合并后的运行配置。
        default_enabled: 调用方（CLI / 测试）传入的对账主开关默认值。
        default_ttl_seconds: 调用方传入的 claim 老化阈值默认值。

    Returns:
        ``(是否对账, 生效的 TTL 秒数)``。
    """
    daemon_config = config.daemon
    enabled = (
        daemon_config.reconcile_stale_attempts
        if daemon_config.reconcile_stale_attempts is not None
        else default_enabled
    )
    ttl_seconds = (
        daemon_config.reclaim_ttl_seconds
        if daemon_config.reclaim_ttl_seconds is not None
        else default_ttl_seconds
    )
    return enabled, ttl_seconds


def _run_hosted_worktree_cleanup(
    *,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> None:
    """在 daemon worker 全部退出后复用安全清理器回收托管 worktree。"""
    try:
        cleanup_result = cleanup_managed_worktrees(
            WorktreeCleanupRequest(
                repo_path=context.repo_path,
                remote=context.config.git.remote,
                base_branch=context.config.git.base_branch,
                dry_run=False,
                force=False,
                active_issue_label=context.config.labels.running,
            ),
            github_client=github_client,
            process_runner=process_runner,
        )
    except Exception:  # noqa: BLE001 - 维护失败不能中断 daemon 后续轮询。
        _logger.exception("Hosted worktree cleanup failed for '%s'.", context.repo_id)
        return

    if cleanup_result.branches:
        _logger.info(
            "Hosted worktree cleanup for '%s': scanned=%d deleted=%d skipped=%d failed=%d",
            context.repo_id,
            len(cleanup_result.branches),
            cleanup_result.deleted_count,
            cleanup_result.skipped_count,
            cleanup_result.failed_count,
        )
    for branch_result in cleanup_result.branches:
        if branch_result.status is WorktreeCleanupStatus.FAILED:
            _logger.warning(
                "Hosted worktree cleanup failed for '%s' (%s): %s",
                context.repo_id,
                branch_result.branch,
                branch_result.reason,
            )
        elif branch_result.status is WorktreeCleanupStatus.SKIPPED:
            _logger.debug(
                "Hosted worktree cleanup skipped for '%s' (%s): %s",
                context.repo_id,
                branch_result.branch,
                branch_result.reason,
            )


def _run_hosted_issue_log_cleanup(context: RepositoryRunContext) -> None:
    """在当前仓库全部 Issue worker 退出后回收 14 天前的原始输出。"""
    try:
        cleanup_result = cleanup_expired_issue_logs(context.repo_path, context.repo_id)
    except Exception:  # noqa: BLE001 - maintenance must not stop the daemon.
        _logger.exception("Hosted Issue log cleanup failed for '%s'.", context.repo_id)
        return
    _logger.info(
        "Hosted Issue log cleanup for '%s' (retention_days=14): "
        "scanned=%d eligible=%d deleted=%d skipped=%d failed=%d",
        context.repo_id,
        cleanup_result.scanned_count,
        cleanup_result.eligible_count,
        cleanup_result.deleted_count,
        cleanup_result.skipped_count,
        cleanup_result.failed_count,
    )


def _run_daemon_loop(request: _AgentDaemonLoopRequest) -> None:
    """daemon 主循环（由 :func:`run_agent_daemon` 包装信号钩子后调用）。"""
    contexts = request.contexts
    interval = request.interval
    agent = request.agent
    process_runner = request.process_runner
    github_client_factory = request.github_client_factory
    content_generator_factory = request.content_generator_factory
    run_history_store = request.run_history_store
    run_trigger = request.run_trigger
    max_prd_issues = request.max_prd_issues
    transcript_runner_factory = request.transcript_runner_factory
    max_deliberation_issues = request.max_deliberation_issues
    concurrency = request.concurrency
    output_view = request.output_view
    reconcile_stale_attempts = request.reconcile_stale_attempts
    reclaim_ttl_seconds = request.reclaim_ttl_seconds
    backlog_store_factory = request.backlog_store_factory
    autopilot_override = request.autopilot_override
    loop_state_store_factory = request.loop_state_store_factory
    loop_clock = request.loop_clock
    api_budget = request.api_budget
    disk_admission_gates = {
        context.repo_id: DiskWatermarkAdmissionGate(
            repo_path=context.repo_path,
            low_watermark_bytes=context.config.daemon.disk_low_watermark_bytes,
            resume_watermark_bytes=context.config.daemon.disk_resume_watermark_bytes,
        )
        for context in contexts
        if context.config.daemon.hosted_maintenance_enabled
    }
    idle_streak = 0
    last_usage_log_monotonic = time.monotonic()
    # 每个仓库一份常驻执行名单：领到的 Issue 在后台线程里跑，主循环不等它们结束。
    execution_slots_by_repo: dict[str, DaemonExecutionSlots] = {}
    loop_state_store = (
        loop_state_store_factory()
        if loop_state_store_factory is not None and loop_clock is not None
        else None
    )
    if loop_state_store_factory is not None and loop_clock is None:
        _logger.warning("Agent-daemon loop stage is disabled because no loop clock was provided.")
    while True:
        pause_sleep_seconds = budget_pause_sleep_seconds(api_budget)
        if pause_sleep_seconds > 0:
            _logger.warning(
                "GitHub API budget is paused; skipping this round and re-checking in %.0fs.",
                pause_sleep_seconds,
            )
            time.sleep(pause_sleep_seconds)
            continue
        round_ready_found = 0
        round_failed = False
        for context in contexts:
            disk_admission_gate = disk_admission_gates.get(context.repo_id)
            _logger.info(
                "Daemon pass for repository '%s' (%s).",
                context.repo_id,
                context.display_name,
            )
            if context.config.daemon.hosted_maintenance_enabled and run_history_store is not None:
                summary_cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
                try:
                    removed_runs, removed_attempts = run_history_store.prune_expired_summaries(
                        cutoff=summary_cutoff
                    )
                    if removed_runs or removed_attempts:
                        _logger.info(
                            "Hosted maintenance pruned run summaries for '%s': "
                            "runs=%d attempts=%d cutoff=%s",
                            context.repo_id,
                            removed_runs,
                            removed_attempts,
                            summary_cutoff,
                        )
                except Exception:  # noqa: BLE001 - maintenance must not stop the daemon.
                    _logger.exception(
                        "Hosted run-history maintenance failed for '%s'.", context.repo_id
                    )
            github_client = github_client_factory(context.repo_path)
            execution_slots = execution_slots_by_repo.setdefault(
                context.repo_id, DaemonExecutionSlots(context.repo_id)
            )
            execution_slots.reap()
            content_generator = (
                content_generator_factory(context.repo_path)
                if content_generator_factory is not None
                else None
            )

            # Agent-daemon loop 在普通队列阶段前推进：本轮刚创建的 Issue 可以
            # 直接进入 Phase 2，daemon 停止期间则不会产生下一轮 loop Issue。
            if loop_state_store is not None and loop_clock is not None:
                try:
                    loop_outcomes = run_agent_daemon_loop_pass(
                        AgentDaemonLoopPassRequest(
                            repo_id=context.repo_id,
                            repo_path=context.repo_path,
                            state_store=loop_state_store,
                            github_client=github_client,
                            process_runner=process_runner,
                            clock=loop_clock,
                            content_generator=content_generator,
                            labels_config=context.config.labels,
                        )
                    )
                    if loop_outcomes:
                        _logger.info(
                            "Agent-daemon loop pass for '%s': %s",
                            context.repo_id,
                            ", ".join(f"{loop_id}={status}" for loop_id, status in loop_outcomes),
                        )
                except Exception as exc:  # noqa: BLE001 - daemon must survive loop faults.
                    _logger.error(
                        "Agent-daemon loop phase failed for repository '%s': %s",
                        context.repo_id,
                        exc,
                    )

            # Phase -1: 崩溃对账。daemon 被 SIGKILL / 崩溃 / 关机打断时，Issue 会
            # 停在 agent/running 而无人回收。这里扫描本机认领、认领进程已死（或
            # claim 超 TTL）的僵尸，逐个判成「续传恢复 / 重新入队 / 判失败」三出口
            # 之一并留下对账 comment；前两个出口回到 agent/ready，正好被本轮
            # Phase 2 领取（先对账后领取）。保守规则与原 reclaim 同源，因此绝不
            # 打扰在途运行。开关关闭时整轮空转，僵尸保持 agent/running 不被触碰。
            # 仓库层开关优先：``.kedacode.toml`` 的 [agent_runner.daemon] 显式写了这两个
            # 键时按仓库生效，没写才沿用调用方传入的全局默认（daemon 可同时服务多仓）。
            reconcile_enabled, reconcile_ttl_seconds = _resolve_reconcile_settings(
                context.config,
                default_enabled=reconcile_stale_attempts,
                default_ttl_seconds=reclaim_ttl_seconds,
            )
            if reconcile_enabled:
                try:
                    reconcile_outcomes = agent_runner_reconcile.reconcile_stale_attempts(
                        repo_path=context.repo_path,
                        config=context.config,
                        github_client=github_client,
                        process_runner=process_runner,
                        ttl_seconds=reconcile_ttl_seconds,
                        # 本机正在执行的 Issue 由它自己的线程推进；长任务的认领
                        # 可能超过 TTL，绝不能被对账当成僵尸回收后再领一次。
                        exclude_issue_numbers=execution_slots.in_flight_issue_numbers(),
                    )
                    applied_numbers = [
                        outcome.issue_number for outcome in reconcile_outcomes if outcome.applied
                    ]
                    if applied_numbers:
                        _logger.info(
                            "Reconciled %d stale attempt(s) for '%s': %s",
                            len(applied_numbers),
                            context.repo_id,
                            applied_numbers,
                        )
                except Exception as exc:  # noqa: BLE001 - daemon must survive reconcile faults.
                    _logger.error(
                        "Stale-attempt reconcile failed for repository '%s': %s",
                        context.repo_id,
                        exc,
                    )

            # Phase 0: Asynchronous Issue-comment deliberation on Issues that
            # explicitly opt in via the ``agent/deliberate`` label. Skipped
            # when no transcript runner factory was injected so existing
            # callers (tests, ad-hoc scripts) keep their previous behaviour.
            if transcript_runner_factory is not None:
                run_deliberation_phase(
                    repo_path=context.repo_path,
                    config=context.config,
                    github_client=github_client,
                    transcript_runner_factory=transcript_runner_factory,
                    max_issues=max_deliberation_issues,
                    stale_rounds_before_hint=context.config.deliberation.stale_rounds_before_hint,
                    repo_id=context.repo_id,
                    issue_admission_check=disk_admission_gate,
                )

            try:
                # Phase 1: PRD rework before normal ready-issue execution.
                if disk_admission_gate is None or disk_admission_gate():
                    process_prd_rework_issues(
                        repo_path=context.repo_path,
                        config=context.config,
                        github_client=github_client,
                        process_runner=process_runner,
                        content_generator=content_generator,
                        max_issues=max_prd_issues,
                    )
            except Exception as exc:  # noqa: BLE001 - daemon should survive unexpected errors.
                _logger.error("PRD rework phase failed: %s", exc)

            # 并行上限：--concurrency > Backlog 页面设置 > 配置文件 max_concurrent_issues。
            # 补位闸门与认领闸门共用该值，页面数字因此与调度行为一致；页面设置读取
            # 失败时按未保存处理，回落到配置文件。
            if concurrency is not None:
                execution_ceiling = max(1, concurrency)
                ceiling_source = CEILING_SOURCE_CLI_OVERRIDE
            else:
                policy_max_parallel: int | None = None
                if backlog_store_factory is not None:
                    try:
                        policy_max_parallel = read_policy_max_parallel(
                            backlog_store_factory(), context.repo_id
                        )
                    except Exception as exc:  # noqa: BLE001 - 读页面设置失败按未保存回退。
                        _logger.warning(
                            "Could not read the Backlog parallel limit for repository '%s' "
                            "(%s); this pass uses the config value.",
                            context.repo_id,
                            exc,
                        )
                execution_ceiling = resolve_execution_ceiling(
                    policy_max_parallel, max(1, context.config.runner.max_concurrent_issues)
                )
                ceiling_source = describe_ceiling_source(policy_max_parallel)
            in_flight_count = len(execution_slots.in_flight_issue_numbers())
            _logger.info(
                "Parallel limit for '%s': limit=%d source=%s in_flight=%d",
                context.repo_id,
                execution_ceiling,
                ceiling_source,
                in_flight_count,
            )
            if request.parallel_limit_status_dir is not None:
                write_daemon_parallel_limit_status(
                    request.parallel_limit_status_dir,
                    DaemonParallelLimitStatus(
                        repo_id=context.repo_id,
                        pid=os.getpid(),
                        parallel_limit=execution_ceiling,
                        source=ceiling_source,
                        in_flight=in_flight_count,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    ),
                )

            # 调度阶段：仅在 ``backlog.auto_advance`` 开启时持续推进 Backlog，先处理
            # 已完成/失败的 PRD，再将队列补到生效上限并标记 agent/ready。
            # 在 Phase 2 前运行可让本轮晋升的 PRD 立即启动；调度异常只记录日志，
            # 不会终止 daemon。
            # --autopilot/--no-autopilot 的按次覆盖优先于配置；未传旗标时每轮
            # 热读配置（flag > repo .kedacode.toml > 全局，锁定本次常驻进程）。
            backlog_auto_advance = (
                autopilot_override
                if autopilot_override is not None
                else context.config.backlog.auto_advance
            )
            if backlog_store_factory is not None and backlog_auto_advance:
                try:
                    advance_report = advance_backlog_queue(
                        context=context,
                        github_client=github_client,
                        store=backlog_store_factory(),
                        process_runner=process_runner,
                        execution_ceiling=execution_ceiling,
                    )
                    if (
                        advance_report.started
                        or advance_report.queued
                        or advance_report.reconciled_completed
                        or advance_report.reconciled_failed
                    ):
                        _logger.info(
                            "Backlog advance for '%s': started=%s queued=%s completed=%s failed=%s",
                            context.repo_id,
                            [item.prd_path for item in advance_report.started],
                            advance_report.queued,
                            advance_report.reconciled_completed,
                            advance_report.reconciled_failed,
                        )
                except Exception as exc:  # noqa: BLE001 - daemon must survive scheduling faults.
                    _logger.error(
                        "Backlog advance phase failed for repository '%s': %s",
                        context.repo_id,
                        exc,
                    )

            pass_activity = PassActivity()
            try:
                # Phase 2: Ready issue execution.
                run_once(
                    repo_path=context.repo_path,
                    config=context.config,
                    dry_run=False,
                    agent=agent,
                    max_issues=execution_ceiling,
                    github_client=github_client,
                    process_runner=process_runner,
                    content_generator=content_generator,
                    run_history_store=run_history_store,
                    run_trigger=run_trigger,
                    repo_id=context.repo_id,
                    concurrency=execution_ceiling,
                    output_view=output_view,
                    execution_ceiling=execution_ceiling,
                    issue_admission_check=disk_admission_gate,
                    pass_activity=pass_activity,
                    execution_slots=execution_slots,
                )
                round_ready_found += pass_activity.ready_found
            except Exception as exc:  # noqa: BLE001 - daemon should survive unexpected errors.
                round_failed = True
                _logger.error(
                    "Daemon pass failed for repository '%s': %s",
                    context.repo_id,
                    exc,
                )
            has_in_flight_issues = bool(execution_slots.in_flight_issue_numbers())
            if context.config.daemon.hosted_maintenance_enabled and has_in_flight_issues:
                _logger.info(
                    "Hosted maintenance deferred for '%s' while Issue workers remain active: %s",
                    context.repo_id,
                    sorted(execution_slots.in_flight_issue_numbers()),
                )
            elif context.config.daemon.hosted_maintenance_enabled:
                _run_hosted_worktree_cleanup(
                    context=context,
                    github_client=github_client,
                    process_runner=process_runner,
                )
                _run_hosted_issue_log_cleanup(context)
        if (
            api_budget is not None
            and time.monotonic() - last_usage_log_monotonic >= USAGE_LOG_INTERVAL_SECONDS
        ):
            last_usage_log_monotonic = time.monotonic()
            _logger.info("GitHub API usage this hour: %s", format_usage_summary(api_budget))
        # 有活、出错或还有 Issue 在本机执行都算“不空闲”：出错时状态未知，执行中
        # 要按基础间隔及时补位，都不应该借机拉长间隔。
        has_in_flight_issues = any(
            slots.in_flight_issue_numbers() for slots in execution_slots_by_repo.values()
        )
        is_busy = round_failed or round_ready_found > 0 or has_in_flight_issues
        idle_streak = 0 if is_busy else idle_streak + 1
        sleep_seconds = next_poll_interval(interval, idle_streak, request.idle_backoff_max_factor)
        _logger.info(
            "Sleeping for %d seconds before next poll (idle rounds: %d).",
            sleep_seconds,
            idle_streak,
        )
        time.sleep(sleep_seconds)
