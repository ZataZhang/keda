"""Local Issue queue runner — daemon mode."""

from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_runner import (
    IAgentTranscriptRunner,
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import IBacklogStore, IRunHistoryStore
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases import agent_runner_reconcile
from backend.core.use_cases.agent_runner_orchestrate import (
    process_prd_rework_issues,
    run_once,
)
from backend.core.use_cases.agent_runner_deliberation_issues import run_deliberation_phase
from backend.core.use_cases.backlog_actions import advance_backlog_queue
from backend.core.use_cases.hosted_maintenance import (
    DiskWatermarkAdmissionGate,
    cleanup_expired_issue_logs,
)
from backend.core.use_cases.worktree_cleanup import (
    WorktreeCleanupRequest,
    WorktreeCleanupStatus,
    cleanup_iar_worktrees,
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
    max_issues: int,
    process_runner: IProcessRunner,
    github_client_factory: Callable[[Path], IGitHubClient],
    content_generator_factory: Callable[[Path], IContentGenerator] | None = None,
    run_history_store: IRunHistoryStore | None = None,
    run_trigger: str = "cli_daemon",
    max_prd_issues: int = 1,
    transcript_runner_factory: Callable[[Path], IAgentTranscriptRunner] | None = None,
    max_deliberation_issues: int = 1,
    concurrency: int = 1,
    output_view: IRunnerLiveView | None = None,
    reconcile_stale_attempts: bool = False,
    reclaim_ttl_seconds: int | None = None,
    backlog_store_factory: Callable[[], IBacklogStore] | None = None,
    autopilot_override: bool | None = None,
) -> None:
    """Run the queue poller forever across all target repositories.

    Args:
        contexts: Resolved repository targets with merged configurations.
        interval: Seconds between polling passes.
        agent: Agent override (auto, codex, claude).
        max_issues: Maximum issues to process per pass per repository.
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
        concurrency: Issues processed in parallel within each repository's
            Phase 2 pass. ``1`` keeps the sequential path (zero regression).
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
            未传旗标，每轮热读配置。该覆盖**不**影响 review 侧自动合并——
            合并仍由 ``safety.auto_merge`` + ``autopilot.enabled`` 双开关决定。
    """
    previous_sigterm_handler: Any = signal.getsignal(signal.SIGTERM)

    def _handle_sigterm(signum: int, frame: Any) -> None:
        """优雅停机：先整组终止在途 agent 子进程树，再退出主循环。"""
        _logger.info("Daemon received SIGTERM; terminating in-flight agent trees before exit.")
        _terminate_descendant_process_trees()
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError):  # noqa: BLE001 - 非主线程（如测试）时不装钩子。
        _logger.debug("SIGTERM shutdown hook not installed (not main thread or no permission).")

    try:
        _run_daemon_loop(
            contexts=contexts,
            interval=interval,
            agent=agent,
            max_issues=max_issues,
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
        )
    finally:
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
        cleanup_result = cleanup_iar_worktrees(
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


def _run_daemon_loop(
    *,
    contexts: list[RepositoryRunContext],
    interval: int,
    agent: str,
    max_issues: int,
    process_runner: IProcessRunner,
    github_client_factory: Callable[[Path], IGitHubClient],
    content_generator_factory: Callable[[Path], IContentGenerator] | None = None,
    run_history_store: IRunHistoryStore | None = None,
    run_trigger: str = "cli_daemon",
    max_prd_issues: int = 1,
    transcript_runner_factory: Callable[[Path], IAgentTranscriptRunner] | None = None,
    max_deliberation_issues: int = 1,
    concurrency: int = 1,
    output_view: IRunnerLiveView | None = None,
    reconcile_stale_attempts: bool = False,
    reclaim_ttl_seconds: int | None = None,
    backlog_store_factory: Callable[[], IBacklogStore] | None = None,
    autopilot_override: bool | None = None,
) -> None:
    """daemon 主循环（由 :func:`run_agent_daemon` 包装信号钩子后调用）。"""
    disk_admission_gates = {
        context.repo_id: DiskWatermarkAdmissionGate(
            repo_path=context.repo_path,
            low_watermark_bytes=context.config.daemon.disk_low_watermark_bytes,
            resume_watermark_bytes=context.config.daemon.disk_resume_watermark_bytes,
        )
        for context in contexts
        if context.config.daemon.hosted_maintenance_enabled
    }
    while True:
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
            content_generator = (
                content_generator_factory(context.repo_path)
                if content_generator_factory is not None
                else None
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

            # 调度阶段：仅在 ``backlog.auto_advance`` 开启时持续推进 Backlog，先处理
            # 已完成/失败的 PRD，再将队列补到 max_parallel 并标记 agent/ready。
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

            try:
                # Phase 2: Ready issue execution.
                run_once(
                    repo_path=context.repo_path,
                    config=context.config,
                    dry_run=False,
                    agent=agent,
                    max_issues=max_issues,
                    github_client=github_client,
                    process_runner=process_runner,
                    content_generator=content_generator,
                    run_history_store=run_history_store,
                    run_trigger=run_trigger,
                    repo_id=context.repo_id,
                    concurrency=concurrency,
                    output_view=output_view,
                    issue_admission_check=disk_admission_gate,
                )
            except Exception as exc:  # noqa: BLE001 - daemon should survive unexpected errors.
                _logger.error(
                    "Daemon pass failed for repository '%s': %s",
                    context.repo_id,
                    exc,
                )
            if context.config.daemon.hosted_maintenance_enabled:
                _run_hosted_worktree_cleanup(
                    context=context,
                    github_client=github_client,
                    process_runner=process_runner,
                )
                _run_hosted_issue_log_cleanup(context)
        _logger.info("Sleeping for %d seconds before next poll.", interval)
        time.sleep(interval)
