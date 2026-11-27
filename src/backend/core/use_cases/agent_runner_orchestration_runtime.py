"""Agent Runner 的单轮队列调度实现。"""

from __future__ import annotations

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from backend.core.shared.interfaces.agent_runner import (
    GitHubApiPermissionError,
    GitHubApiRateLimitError,
    GitHubApiUnavailableError,
    GitHubOperationOutcomeUnknownError,
)
from backend.core.shared.interfaces.runner_claim_guard import clear_active_runner_claim_guard
from backend.core.shared.models.runner_recovery import (
    RecoveryFailureCategory,
    RecoveryFailureRecord,
    RecoveryRunRecord,
)
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_runner import TokenUsage
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_batch_aggregate_queue import (
    AggregateQueueCompletionRequest,
    check_aggregate_candidate_shortfall,
    complete_aggregate_queue,
    log_aggregate_dry_run_preview,
)
from backend.core.use_cases.agent_daemon_shutdown import (
    AgentDaemonShutdownInterrupt,
    _skip_issue_on_daemon_shutdown,
)
from backend.core.use_cases.agent_runner_blocked_claim import BlockedWorktreeClaimedError
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
from backend.core.use_cases.agent_runner_claim_lease import ClaimLeaseLost
from backend.core.use_cases.coordination_heartbeat import active_coordination_heartbeat
from backend.core.use_cases.agent_runner_dependencies import (
    clear_dependency_waiting,
    evaluate_dependencies,
    mark_dependency_waiting,
    parse_dependency_marker,
)
from backend.core.use_cases.agent_runner_direct_pr_label import PublishStageSelection
from backend.core.use_cases.agent_runner_issue_admission import (
    _has_published_direct_pr_handoff,
    _process_issue_if_admitted,
)
from backend.core.use_cases.agent_runner_failure_marking import (
    _mark_issue_blocked,
    _mark_issue_failed,
)
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_attempt_event_detail,
    record_lifecycle_event,
    record_lifecycle_terminal,
    resolve_lifecycle_prd_path,
)
from backend.core.use_cases.agent_runner_orchestration_routing import (
    _IssueRoutingContext,
    _mirror_to_console,
    _process_routed_issue,
)
from backend.core.use_cases.agent_runner_orchestrate import (
    _READY_DISCOVERY_LIMIT,
    AppConfig,
    AttemptResult,
    DependencyEnvironmentBlockedError,
    ForbiddenBlockedError,
    HumanActionRequiredError,
    IssueBlockedDuringNoChangeError,
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
    IRunHistoryStore,
    IRunnerLiveView,
    IssueSummary,
    _append_run_record_locked,
    _find_worktree_path_for_issue,
    _guard_blocked_issue_has_resolution,
    _guard_running_issue_is_rework,
    _logger,
    _persist_attempt_result,
    _process_blocked_resolution,
    _process_ready_issue,
    _process_running_publish_recovery,
    _process_running_rework,
    choose_agent,
    run_issue_with_agent_fallback,
)
from backend.core.use_cases.agent_runner_orchestration_models import (
    RunOnceRequest,
)
from backend.core.use_cases.agent_runner_prd_activity import (
    PrdActivityConflictError,
    PrdActivityLease,
)
from backend.core.use_cases.agent_runner_queue import issue_priority, sort_ready_issues
from backend.core.use_cases.agent_runner_validation_gate import process_validation_gate
from backend.core.use_cases.agent_runner_workflow import claim_blocked_issue
from backend.core.use_cases.agent_runner_recovery_policy import classify_recovery_failure
from backend.core.use_cases.issue_retry_overrides import (
    IssueRetryOverrideRequest,
    prepare_issue_retry_override,
)
from backend.core.use_cases.agent_runner_worktree_probe import (
    _has_existing_local_commit_ready_for_publish,
    _worktree_needs_rebase_recovery,
)
from backend.core.use_cases.backlog_concurrency import count_live_running_issues
from backend.core.use_cases.lifecycle_agent_resolution import attach_prd_lifecycle_overrides
from backend.core.use_cases.run_target_admission import has_non_ready_workflow_label
from backend.core.use_cases.worktree_dependency_context import (
    escalate_dependency_environment_failure,
)

RUNTIME_DEPENDENCY_NAMES = (
    "_process_ready_issue",
    "_process_running_rework",
    "_process_blocked_resolution",
    "_process_running_publish_recovery",
    "choose_agent",
    "_find_worktree_path_for_issue",
    "_worktree_needs_rebase_recovery",
    "_has_existing_local_commit_ready_for_publish",
)

_RECOVERY_BUDGET_SECONDS = 30 * 60
_RECOVERY_MAX_ATTEMPTS = 6


def _sanitize_recovery_diagnostic(diagnostic: str) -> str:
    """移除常见凭据形式并限制恢复报告中的诊断长度。"""
    sanitized = re.sub(
        r"(?i)(authorization:\s*bearer\s+|(?:gh[pousr]_|github_pat_))[A-Za-z0-9_./-]+",
        "[REDACTED]",
        diagnostic,
    )
    return sanitized[:1500]


def _recovery_retry_after(
    *,
    category: RecoveryFailureCategory,
    occurred_at: datetime,
    previous_attempts: int,
    github_client: IGitHubClient,
) -> datetime:
    """优先遵从共享 API 预算中的服务端重置时间。"""
    retry_delay_seconds = min(60 * (2**previous_attempts), 300)
    if category is RecoveryFailureCategory.RATE_LIMITED:
        api_budget = getattr(github_client, "_api_budget", None)
        pause_remaining_seconds = getattr(api_budget, "pause_remaining_seconds", None)
        if callable(pause_remaining_seconds):
            try:
                remaining_seconds = pause_remaining_seconds()
            except (OSError, ValueError):
                remaining_seconds = 0
            if isinstance(remaining_seconds, (int, float)) and remaining_seconds > 0:
                retry_delay_seconds = max(retry_delay_seconds, remaining_seconds)
    return occurred_at + timedelta(seconds=retry_delay_seconds)


def _local_recovery_report_path(
    *,
    repo_path: Path,
    issue_number: int,
    stored_path: str | None = None,
) -> Path | None:
    """只接受仓库内固定位置的报告，防止损坏记录把任意文件作为通知正文。"""
    expected_path = repo_path / ".agent-runner" / "recovery" / f"issue-{issue_number}.md"
    if stored_path:
        candidate_path = Path(stored_path)
        if not candidate_path.is_absolute():
            candidate_path = repo_path / candidate_path
        if candidate_path.resolve(strict=False) != expected_path.resolve(strict=False):
            return None
    try:
        repository_root = repo_path.resolve(strict=True)
        report_parent = expected_path.parent.resolve(strict=False)
        if not report_parent.is_relative_to(repository_root) or expected_path.is_symlink():
            return None
    except OSError:
        return None
    return expected_path


def _write_recovery_report(
    *,
    repo_path: Path,
    issue: IssueSummary,
    issue_kind: str,
    recovery_run: RecoveryRunRecord,
    recovery_store: object,
    storage_error: str | None = None,
) -> tuple[Path, str]:
    """持久化可执行的本地故障交接报告。"""
    issue_path_parts = [part for part in urlparse(issue.url).path.split("/") if part]
    if len(issue_path_parts) >= 4 and issue_path_parts[-2] == "issues":
        operation_repository_id = "/".join(issue_path_parts[-4:-2])
    else:
        operation_repository_id = recovery_run.repository_id
    run_key = f"repo:{operation_repository_id}:issue:{issue.number}"
    unresolved_operations = []
    if storage_error is None:
        list_unresolved = getattr(recovery_store, "list_unresolved_operations", None)
        if callable(list_unresolved):
            unresolved_operations = list_unresolved(run_key)
    report_path = _local_recovery_report_path(
        repo_path=repo_path,
        issue_number=issue.number,
    )
    if report_path is None:
        raise OSError("本地恢复报告路径不在仓库内或经过符号链接。")
    if storage_error is not None:
        status_line = "⏸️ 本地恢复账本不可用；已停止后续外部写入。"
        next_step = (
            "修复本机存储空间或 SQLite 文件后，先核对 GitHub 上的现存对象，再使用 `kc run --issue "
        )
        f"{issue.number}` 继续。"
    elif recovery_run.recovery_state == "needs_human":
        status_line = "⏸️ 自动恢复预算耗尽或需要人工处理；业务写入已停止。"
        next_step = _recovery_next_step(recovery_run.failure_category, issue.number)
    else:
        resume_at_utc = (
            recovery_run.resume_at.astimezone(timezone.utc).isoformat()
            if recovery_run.resume_at
            else "下次 daemon 调度"
        )
        status_line = f"⏳ 暂停至 {resume_at_utc} UTC；现有成果与操作账本已保留。"
        next_step = "daemon 到期后会从失败阶段继续；显式运行 `kc run --issue "
        f"{issue.number}` 也会先对账未决写入。"
    operation_lines = [
        f"- `{record.intent.operation_kind}` `{_sanitize_recovery_diagnostic(record.intent.target)}`："
        f"`{record.state.value}`，操作键 `{record.intent.operation_key}`"
        for record in unresolved_operations
    ] or ["- 无未决操作记录；仍需先确认远端目标状态。"]
    diagnostic = _sanitize_recovery_diagnostic(storage_error or recovery_run.error_summary)
    report_text = "\n".join(
        [
            f"# Agent Runner Issue #{issue.number} 恢复报告",
            "",
            f"- 状态：{status_line}",
            f"- 失败步骤：`{issue_kind}` / `{recovery_run.failure_category.value}`",
            f"- 诊断：{diagnostic}",
            "- 已完成成果：保留故障前已落盘的 worktree、commit 与通过验证的阶段；未持久化的内容不会被视为已完成。",
            "- 未知或未完成的对外写入：",
            *operation_lines,
            "- 已尝试处理：单次 gh 调用最多 5 次 / 60 秒；恢复总预算最多 30 分钟、6 次调度恢复。",
            f"- 下一步：{next_step}",
            f"- 恢复入口：`kc run --issue {issue.number}`；发布恢复也可用 `kc recover --issue {issue.number}`。",
            "- 通知状态：待投递；本地报告已保存，通知成功前不会标记为已送达。",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(f"{report_path.suffix}.tmp")
    temp_path.write_text(report_text, encoding="utf-8")
    temp_path.replace(report_path)
    return report_path, report_text


def _recovery_next_step(category: RecoveryFailureCategory, issue_number: int) -> str:
    """根据故障类别给出人工可执行的恢复建议。"""
    if category is RecoveryFailureCategory.AUTH:
        return (
            "检查 `gh auth status`、组织 SSO 与仓库写权限；恢复权限后先查看未决操作，"
            f"再运行 `kc run --issue {issue_number}`。"
        )
    if category is RecoveryFailureCategory.OUTCOME_UNKNOWN:
        return (
            "按操作键核对 GitHub 对端对象；确认已创建则保留现状，确认未创建且目标可安全补发后，"
            f"运行 `kc run --issue {issue_number}`。"
        )
    if category is RecoveryFailureCategory.FATAL_STATE:
        return (
            "检查恢复 SQLite 文件和磁盘状态；先备份现场并核对未知写入，修复存储后再运行 "
            f"`kc run --issue {issue_number}`。"
        )
    return (
        "检查 GitHub 服务状态、网络与限流恢复时间；故障恢复后运行 "
        f"`kc run --issue {issue_number}`，runner 会先对账未决操作。"
    )


def _process_single_issue(
    issue: IssueSummary,
    issue_kind: str,
    *,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None,
    run_history_store: IRunHistoryStore | None,
    run_trigger: str,
    effective_repo_id: str,
    output_view: IRunnerLiveView,
    publish_stage: PublishStage = PublishStage.NORMAL,
) -> int:
    """Process one discovered Issue end-to-end.

    Extracted from :func:`run_once` so it can run either sequentially or inside
    a thread pool. All failures are caught and recorded here; the function never
    raises, so the caller treats the return value as this Issue's exit-code
    contribution.

    Returns:
        ``0`` on success or skip, ``1`` on a recorded failure/block.
    """
    # 同步 worker 线程可能复用；先清除上一条 Issue 的上下文认领核验。
    clear_active_runner_claim_guard()
    # 在 worktree 创建前从 Issue 引用的 PRD 解析 lifecycle_agents 覆盖。
    issue = attach_prd_lifecycle_overrides(
        issue,
        repo_path,
        process_runner=process_runner,
        git_remote=config.git.remote,
    )
    issue, retry_override_claim = prepare_issue_retry_override(
        IssueRetryOverrideRequest(
            issue=issue,
            issue_kind=issue_kind,
            repo_id=effective_repo_id,
            config=config,
            run_history_store=run_history_store,
        )
    )
    lifecycle_prd_path = resolve_lifecycle_prd_path(issue)
    selected_agent = choose_agent(issue, config, agent)
    if retry_override_claim is not None:
        retry_override_claim.validate_selected_executor(selected_agent)
    output_view.register_issue(issue.number, selected_agent)
    run_started_at = datetime.now(timezone.utc)
    used_agent = selected_agent
    lifecycle_start_detail = {
        "agent": selected_agent,
        "issue_kind": issue_kind,
        "run_trigger": run_trigger,
        "issue_url": issue.url,
    }
    recovery_store = getattr(github_client, "recovery_store", None)
    recovery_run_key = f"repo:{effective_repo_id}:issue:{issue.number}"
    storage_write_failed = False
    release_claim_on_exit = True

    # run id 与 backlog 启动侧一致，避免重复创建生命周期记录。
    for event_type in (LifecycleEventType.STARTED, LifecycleEventType.CLAIMED):
        record_lifecycle_event(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=event_type,
            actor="runner",
            occurred_at=run_started_at.isoformat(),
            detail=lifecycle_start_detail,
        )

    def _on_attempt_recorded(result: AttemptResult, attempt_results: list[AttemptResult]) -> None:
        _persist_attempt_result(
            result=result,
            attempt_results=attempt_results,
            repo_id=effective_repo_id,
            issue_number=issue.number,
            github_client=github_client,
            run_history_store=run_history_store,
        )
        # 生命周期观测（旁路）：每次 Agent attempt 落一条事件；重试与恢复作为
        # 独立事件追加，后续成功不覆盖早先的失败历史。detail 形状唯一事实源
        # 见 :func:`build_attempt_event_detail`（含 token_usage，如可用）。
        attempt_detail = build_attempt_event_detail(result)
        record_lifecycle_event(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=LifecycleEventType.ATTEMPT,
            actor="runner",
            occurred_at=result.started_at or None,
            event_key=f"attempt:{result.agent}:{result.attempt_number}:{result.started_at}",
            detail=attempt_detail,
        )
        if result.attempt_number > 1:
            record_lifecycle_event(
                store=run_history_store,
                repo_id=effective_repo_id,
                prd_path=lifecycle_prd_path,
                issue_number=issue.number,
                trigger=run_trigger,
                event_type=LifecycleEventType.RETRY,
                actor="runner",
                occurred_at=result.started_at or None,
                event_key=f"retry:{result.agent}:{result.attempt_number}",
                detail=attempt_detail,
            )
        if result.recovered:
            record_lifecycle_event(
                store=run_history_store,
                repo_id=effective_repo_id,
                prd_path=lifecycle_prd_path,
                issue_number=issue.number,
                trigger=run_trigger,
                event_type=LifecycleEventType.RECOVERED,
                actor="runner",
                occurred_at=result.finished_at or None,
                event_key=f"recovered:{result.agent}:{result.attempt_number}",
                detail=attempt_detail,
            )

    def _emit_agent_usage_event(flow: str, agent_name: str, usage: TokenUsage) -> None:
        """生命周期观测（旁路）：非 attempt 主体的 agent 调用用量落一条观测事件。

        观测事件不推进阶段、不占时长（lifecycle 侧过滤）；写入失败不阻断主流程
        （record_lifecycle_event 内部容错）。
        """
        record_lifecycle_event(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=LifecycleEventType.AGENT_TOKEN_USAGE,
            actor="runner",
            event_key=(f"agent-token-usage:{flow}:{datetime.now(timezone.utc).isoformat()}"),
            detail={
                "flow": flow,
                "agent": agent_name,
                "token_usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                },
            },
        )

    def _record_recovery_failure(
        exc: Exception,
    ) -> tuple[RecoveryRunRecord | None, Path | None]:
        """严格保存外部故障预算与人工交接报告。"""
        nonlocal storage_write_failed, release_claim_on_exit
        if recovery_store is None:
            return None, None
        occurred_at = datetime.now(timezone.utc)
        safe_diagnostic = _sanitize_recovery_diagnostic(str(exc))
        if isinstance(exc, GitHubApiRateLimitError):
            category = RecoveryFailureCategory.RATE_LIMITED
        elif isinstance(exc, GitHubApiPermissionError):
            category = RecoveryFailureCategory.AUTH
        elif isinstance(exc, GitHubOperationOutcomeUnknownError):
            category = RecoveryFailureCategory.OUTCOME_UNKNOWN
        elif isinstance(exc, sqlite3.Error) or "conflict" in safe_diagnostic.lower():
            category = RecoveryFailureCategory.FATAL_STATE
        else:
            category = classify_recovery_failure(
                diagnostic_text=safe_diagnostic,
                is_write=True,
            ).category
        report_path = repo_path / ".agent-runner" / "recovery" / f"issue-{issue.number}.md"
        try:
            previous_run = recovery_store.get_run(recovery_run_key)
            previous_attempts = (
                previous_run.recovery_attempts
                if previous_run is not None and previous_run.recovery_state == "waiting"
                else 0
            )
            retry_after = (
                _recovery_retry_after(
                    category=category,
                    occurred_at=occurred_at,
                    previous_attempts=previous_attempts,
                    github_client=github_client,
                )
                if category
                in {
                    RecoveryFailureCategory.TRANSIENT_NETWORK,
                    RecoveryFailureCategory.RATE_LIMITED,
                    RecoveryFailureCategory.OUTCOME_UNKNOWN,
                }
                else None
            )
            recovery_run = recovery_store.record_failure(
                RecoveryFailureRecord(
                    run_key=recovery_run_key,
                    repository_id=effective_repo_id,
                    issue_number=issue.number,
                    occurred_at=occurred_at,
                    retry_after=retry_after,
                    max_attempts=_RECOVERY_MAX_ATTEMPTS,
                    budget_seconds=_RECOVERY_BUDGET_SECONDS,
                    category=category,
                    error_summary=safe_diagnostic,
                    report_path=report_path.as_posix(),
                )
            )
            saved_path, report_text = _write_recovery_report(
                repo_path=repo_path,
                issue=issue,
                issue_kind=issue_kind,
                recovery_run=recovery_run,
                recovery_store=recovery_store,
            )
        except sqlite3.Error as store_error:
            storage_write_failed = True
            release_claim_on_exit = False
            diagnostic = _sanitize_recovery_diagnostic(str(store_error))
            fallback_run = RecoveryRunRecord(
                run_key=recovery_run_key,
                repository_id=effective_repo_id,
                issue_number=issue.number,
                first_failure_at=occurred_at,
                budget_deadline=occurred_at,
                resume_at=None,
                recovery_attempts=0,
                recovery_state="needs_human",
                failure_category=RecoveryFailureCategory.FATAL_STATE,
                error_summary=diagnostic,
                report_path=report_path.as_posix(),
            )
            try:
                saved_path, _ = _write_recovery_report(
                    repo_path=repo_path,
                    issue=issue,
                    issue_kind=issue_kind,
                    recovery_run=fallback_run,
                    recovery_store=recovery_store,
                    storage_error=diagnostic,
                )
            except OSError as report_error:
                _logger.error(
                    "Issue #%d recovery storage and local report both failed: %s",
                    issue.number,
                    report_error,
                )
                return fallback_run, None
            _logger.error(
                "Issue #%d strict recovery storage failed; business writes stopped. Report: %s",
                issue.number,
                saved_path,
            )
            return fallback_run, saved_path
        except OSError as report_error:
            storage_write_failed = True
            release_claim_on_exit = False
            _logger.error(
                "Issue #%d recovery state was saved but local report could not be written: %s",
                issue.number,
                report_error,
            )
            return recovery_run, None

        _logger.warning(
            "Issue #%d recovery state=%s category=%s report=%s",
            issue.number,
            recovery_run.recovery_state,
            recovery_run.failure_category.value,
            saved_path,
        )
        if (
            recovery_run.recovery_state == "needs_human"
            and recovery_run.failure_category is not RecoveryFailureCategory.FATAL_STATE
        ):
            release_claim_on_exit = False
            try:
                github_client.comment_issue(issue.number, report_text)
            except Exception as notification_error:  # noqa: BLE001 - 同一报告留待后续续投。
                _logger.warning(
                    "Issue #%d handoff notification was not delivered; local report remains at %s: %s",
                    issue.number,
                    saved_path,
                    notification_error,
                )
        return recovery_run, saved_path

    prd_activity_lease = PrdActivityLease(
        repo_path, lifecycle_prd_path, issue.number, selected_agent
    )

    def _on_issue_claimed() -> None:
        if retry_override_claim is not None:
            retry_override_claim.consume()
        prd_activity_lease.start()

    stage_selection = PublishStageSelection()
    try:
        if recovery_store is not None:
            persisted_recovery = recovery_store.get_run(recovery_run_key)
            is_daemon_trigger = "daemon" in run_trigger.lower()
            if (
                persisted_recovery is not None
                and persisted_recovery.recovery_state == "needs_human"
            ):
                if is_daemon_trigger:
                    safe_report_path = _local_recovery_report_path(
                        repo_path=repo_path,
                        issue_number=issue.number,
                        stored_path=persisted_recovery.report_path,
                    )
                    if (
                        safe_report_path is not None
                        and persisted_recovery.failure_category
                        is not RecoveryFailureCategory.FATAL_STATE
                    ):
                        try:
                            report_body = safe_report_path.read_text(encoding="utf-8")
                            github_client.comment_issue(issue.number, report_body)
                        except Exception as notification_error:  # noqa: BLE001 - 本地报告保留续投。
                            _logger.warning(
                                "Issue #%d handoff report remains undelivered at %s: %s",
                                issue.number,
                                persisted_recovery.report_path,
                                notification_error,
                            )
                    elif safe_report_path is None:
                        _logger.error(
                            "Issue #%d recovery report path is invalid; refusing to read or post it.",
                            issue.number,
                        )
                    output_view.update_status(issue.number, "blocked")
                    return 0
                # 显式 CLI 重跑代表人已处理阻塞；预算开启新周期，未决副作用身份不变，
                # 各写入口仍须先对账旧 operation_key。
                recovery_store.finish_run(recovery_run_key)
            elif (
                persisted_recovery is not None
                and persisted_recovery.recovery_state == "waiting"
                and persisted_recovery.resume_at is not None
                and persisted_recovery.resume_at > datetime.now(timezone.utc)
                and is_daemon_trigger
            ):
                output_view.update_status(issue.number, "deferred")
                return 0
        # Ready Issue 先完成 claim election，再取得 PRD 锁。并发的 daemon 与
        # 定向 run 只有赢家会持锁，落败者不会误报失败或释放赢家的锁。
        if issue_kind != "ready":
            prd_activity_lease.start()
        if issue_kind == "ready":
            try:
                used_agent = run_issue_with_agent_fallback(
                    issue=issue,
                    config=config,
                    agent=agent,
                    process_for_agent=partial(
                        _process_ready_issue,
                        issue=issue,
                        repo_path=repo_path,
                        config=config,
                        github_client=github_client,
                        process_runner=process_runner,
                        content_generator=content_generator,
                        publish_stage=publish_stage,
                        stage_selection=stage_selection,
                        repo_id=effective_repo_id,
                        on_claimed=_on_issue_claimed,
                    ),
                    on_attempt_recorded=_on_attempt_recorded,
                    on_agent_usage=_emit_agent_usage_event,
                )
            except Exception as execution_exc:  # noqa: BLE001 - 只做归属判断后原样抛出
                # 依赖准备是「可恢复的准备 + 诊断」，绝不提前终止 Issue；因此当执行
                # 确实失败且能归因到记录在案的环境缺口（缺可执行文件 / 验证命令 127）
                # 时，改判为可恢复阻塞，而不是 agent/failed。
                dependency_block_exc: BaseException = execution_exc
                try:
                    claimed_worktree_path = _find_worktree_path_for_issue(
                        repo_path, issue, config, process_runner
                    )
                except Exception as locate_exc:  # noqa: BLE001 - 定位不到 worktree 就不做归属
                    _logger.debug(
                        "Issue #%d: worktree path not locatable, skipping dependency "
                        "environment attribution: %s",
                        issue.number,
                        locate_exc,
                    )
                else:
                    dependency_block_exc = escalate_dependency_environment_failure(
                        execution_exc, claimed_worktree_path
                    )
                if dependency_block_exc is not execution_exc:
                    raise dependency_block_exc from execution_exc
                raise
        elif issue_kind == "running_rework":
            _, marker = _guard_running_issue_is_rework(issue, config, github_client)
            if marker is None:
                output_view.update_status(issue.number, "skipped")
                return 0
            # 生命周期观测：rework 标记未被消费说明这是同一 PRD 的一次重做。
            record_lifecycle_event(
                store=run_history_store,
                repo_id=effective_repo_id,
                prd_path=lifecycle_prd_path,
                issue_number=issue.number,
                trigger=run_trigger,
                event_type=LifecycleEventType.RETRY,
                actor="runner",
                occurred_at=run_started_at.isoformat(timespec="seconds"),
                event_key=f"rework:{issue.number}:{marker.phase}:{marker.cycle}",
                detail={"marker_phase": marker.phase, "cycle": marker.cycle},
            )
            used_agent = run_issue_with_agent_fallback(
                issue=issue,
                config=config,
                agent=agent,
                process_for_agent=partial(
                    _process_running_rework,
                    issue=issue,
                    repo_path=repo_path,
                    config=config,
                    github_client=github_client,
                    process_runner=process_runner,
                    marker=marker,
                    repo_id=effective_repo_id,
                ),
            )
        elif issue_kind == "blocked_resolution":
            marker = _guard_blocked_issue_has_resolution(issue, github_client)
            if marker is None:
                output_view.update_status(issue.number, "skipped")
                return 0
            claimed_issue_snapshot = claim_blocked_issue(github_client, issue.number, config)
            if claimed_issue_snapshot is None:
                _logger.info(
                    "Issue #%d already claimed by another runner, skipping.",
                    issue.number,
                )
                output_view.update_status(issue.number, "skipped")
                return 0
            # 认领后的新鲜快照是「无改动交接」判定的基线（标签已翻成 running），
            # 但它来自 get_issue，不带入口处回填的 PRD 级 lifecycle 覆盖。直接整体
            # 赋值会让 review / supervisor 这类只读 Issue 上覆盖字段的阶段静默丢绑定，
            # 因此以新鲜快照为底、把三个覆盖字段带过来（含一次性执行器覆盖，后续阶段仍要生效）。
            issue = replace(
                claimed_issue_snapshot,
                lifecycle_overrides=issue.lifecycle_overrides,
                lifecycle_preset_overrides=issue.lifecycle_preset_overrides,
                explicit_executor_overrides=issue.explicit_executor_overrides,
            )
            # 生命周期观测：阻塞 Issue 被重新领取，说明阻塞已解除并进入新一轮执行。
            record_lifecycle_event(
                store=run_history_store,
                repo_id=effective_repo_id,
                prd_path=lifecycle_prd_path,
                issue_number=issue.number,
                trigger=run_trigger,
                event_type=LifecycleEventType.UNBLOCKED,
                actor="runner",
                occurred_at=run_started_at.isoformat(timespec="seconds"),
                detail={"issue_kind": issue_kind},
            )
            used_agent = run_issue_with_agent_fallback(
                issue=issue,
                config=config,
                agent=agent,
                process_for_agent=partial(
                    _process_blocked_resolution,
                    issue=issue,
                    repo_path=repo_path,
                    config=config,
                    github_client=github_client,
                    process_runner=process_runner,
                    content_generator=content_generator,
                    marker=marker,
                    publish_stage=publish_stage,
                    stage_selection=stage_selection,
                    repo_id=effective_repo_id,
                ),
                on_attempt_recorded=_on_attempt_recorded,
                on_agent_usage=_emit_agent_usage_event,
            )
        else:
            used_agent = run_issue_with_agent_fallback(
                issue=issue,
                config=config,
                agent=agent,
                process_for_agent=partial(
                    _process_running_publish_recovery,
                    cleanup_only=issue_kind == "direct_pr_cleanup",
                    issue=issue,
                    repo_path=repo_path,
                    config=config,
                    github_client=github_client,
                    process_runner=process_runner,
                    content_generator=content_generator,
                    publish_stage=publish_stage,
                    stage_selection=stage_selection,
                    repo_id=effective_repo_id,
                ),
            )
        _logger.info("Completed Issue #%d: %s", issue.number, issue.title)
        output_view.update_status(issue.number, "completed")
        record_lifecycle_event(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=LifecycleEventType.IMPLEMENTATION_COMPLETED,
            actor="runner",
            detail={"agent": used_agent},
        )
        _append_run_record_locked(
            run_history_store=run_history_store,
            repo_id=effective_repo_id,
            repo_path=repo_path,
            issue=issue,
            trigger=run_trigger,
            agent=used_agent,
            outcome="completed",
            error_summary=None,
            started_at=run_started_at,
        )
        if recovery_store is not None:
            recovery_store.finish_run(recovery_run_key)
        return 0
    except AgentDaemonShutdownInterrupt:
        _skip_issue_on_daemon_shutdown(issue.number, output_view)
        return 0
    except (GitHubApiRateLimitError, GitHubApiUnavailableError, GitHubApiPermissionError) as exc:
        if _skip_issue_on_daemon_shutdown(issue.number, output_view):
            return 0
        recovery_run, report_path = _record_recovery_failure(exc)
        if recovery_run is None:
            if isinstance(exc, GitHubApiPermissionError):
                _logger.error(
                    "Blocked Issue #%d on GitHub permission failure: %s", issue.number, exc
                )
                output_view.update_status(issue.number, "blocked")
                return 1
            _logger.warning("Deferred Issue #%d after GitHub API failure: %s", issue.number, exc)
            output_view.update_status(issue.number, "deferred")
            return 0
        if report_path is None:
            storage_write_failed = True
            release_claim_on_exit = False
        needs_human = recovery_run.recovery_state == "needs_human"
        output_view.update_status(issue.number, "blocked" if needs_human else "deferred")
        _logger.warning(
            "Issue #%d %s; recovery report: %s",
            issue.number,
            "requires human action" if needs_human else "deferred for recovery",
            report_path or "unavailable",
        )
        return 1 if needs_human else 0

    except (
        ForbiddenBlockedError,
        HumanActionRequiredError,
        IssueBlockedDuringNoChangeError,
        DependencyEnvironmentBlockedError,
    ) as exc:
        # 这些情况都是「等人」而非「执行失败」：Issue 进入可恢复的 agent/blocked，
        # 人给出决定后 `kc blocked-continue` 继续，不占用 agent/failed 语义。
        _mark_issue_blocked(
            issue=issue,
            config=config,
            github_client=github_client,
            exc=exc,
        )
        _logger.error("Blocked Issue #%d: %s", issue.number, exc)
        output_view.update_status(issue.number, "blocked")
        record_lifecycle_terminal(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=LifecycleEventType.BLOCKED,
            outcome="blocked",
            actor="runner",
            detail={"error_summary": str(exc)},
        )
        _append_run_record_locked(
            run_history_store=run_history_store,
            repo_id=effective_repo_id,
            repo_path=repo_path,
            issue=issue,
            trigger=run_trigger,
            agent=selected_agent,
            outcome="blocked",
            error_summary=str(exc),
            started_at=run_started_at,
        )
        return 1

    except PrdActivityConflictError as exc:
        # Fresh lock means another execution path owns the PRD. Skip without
        # marking the shared Issue failed or changing the holder's workflow label.
        _logger.info("Issue #%d PRD activity lock is held; skipping: %s", issue.number, exc)
        output_view.update_status(issue.number, "skipped")
        return 0

    except ClaimArbitrationLost as exc:
        # 首次领取仲裁落败：Issue 归更早的认领者。这里既不能标 failed 也不能改
        # 标签（会把赢家的 running 覆盖掉），按 skip 返回 0。
        _logger.info("Issue #%d claim arbitration lost, skipping: %s", issue.number, exc)
        output_view.update_status(issue.number, "skipped")
        return 0

    except ClaimLeaseLost as exc:
        # 租约失效时标签归新持有者；只交班，不写失败评论或工作流标签。
        _logger.warning("Issue #%d claim lease lost; abandoning delivery: %s", issue.number, exc)
        output_view.update_status(issue.number, "skipped")
        return 0

    except BlockedWorktreeClaimedError as exc:
        _logger.info(
            "Issue #%d worktree already claimed by another runner, skipping: %s",
            issue.number,
            exc,
        )
        output_view.update_status(issue.number, "skipped")
        return 0
    except sqlite3.Error as exc:
        # 严格恢复账本不可写时禁止继续用 best-effort GitHub 标记掩盖故障。
        storage_write_failed = True
        release_claim_on_exit = False
        _record_recovery_failure(exc)
        _logger.error(
            "Issue #%d stopped because strict recovery storage is unavailable: %s",
            issue.number,
            exc,
        )
        output_view.update_status(issue.number, "blocked")
        return 1
    except Exception as exc:  # noqa: BLE001 - report queue failures and continue.
        if _skip_issue_on_daemon_shutdown(issue.number, output_view):
            return 0
        _mark_issue_failed(
            issue=issue,
            config=config,
            github_client=github_client,
            exc=exc,
        )
        _logger.error("Failed Issue #%d: %s", issue.number, exc)
        output_view.update_status(issue.number, "failed")
        record_lifecycle_terminal(
            store=run_history_store,
            repo_id=effective_repo_id,
            prd_path=lifecycle_prd_path,
            issue_number=issue.number,
            trigger=run_trigger,
            event_type=LifecycleEventType.FAILED,
            outcome="failed",
            actor="runner",
            detail={"error_summary": str(exc)},
        )
        _append_run_record_locked(
            run_history_store=run_history_store,
            repo_id=effective_repo_id,
            repo_path=repo_path,
            issue=issue,
            trigger=run_trigger,
            agent=selected_agent,
            outcome="failed",
            error_summary=str(exc),
            started_at=run_started_at,
        )
        return 1

    finally:
        clear_active_runner_claim_guard()
        prd_activity_lease.close()
        heartbeat = active_coordination_heartbeat()
        if heartbeat is not None and release_claim_on_exit and not storage_write_failed:
            heartbeat.release_claim(github_client=github_client, issue_number=issue.number)


def run_once(request: RunOnceRequest) -> int:
    """执行一次轮询处理。

    本函数是 Agent Runner 的入口点，在每次轮询间隔调用。
    发现并处理 ready 和 running 状态的 Issue。

    Issue 发现逻辑：
    1. 扫描 ready 标签的 Issue，跳过依赖未满足的条目后最多处理
       ``max(max_issues, concurrency)`` 个
    2. 对 remaining 配额，从 running 标签 Issue 中筛选候选：
       - 有 rework 标记 → running_rework
       - 有已就绪的本地 commit → running_publish_recovery
       - 否则跳过

    并发处理：``concurrency <= 1`` 时逐个串行处理（与历史行为逐字节一致）；
    ``concurrency > 1`` 时用线程池同一轮并行处理多个 Issue，每个 Issue 的
    agent 输出经 ``output_view`` 与每 Issue 日志文件分流，互不交错。

    Args:
        repo_path: 目标仓库路径
        config: 应用配置
        dry_run: 若为 True，仅列出待处理 Issue 不实际处理
        agent: Agent 覆盖（auto/codex/claude）
        max_issues: 每次轮询最多处理的 Issue 数量
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
        run_history_store: 可选的运行历史旁路存储；为 ``None`` 时零行为变化
        run_trigger: 写入运行记录的触发来源（如 cli_run / console_daemon）
        repo_id: 写入运行记录的仓库 ID；缺省取 ``repo_path.name``
        concurrency: 单轮并行处理的 Issue 数量；``1`` 为串行（默认，零回归）。
            实际领取上限取 ``max(max_issues, concurrency)``。
        output_view: 并行时每 Issue 的实时输出视图；为 ``None`` 时不展示看板
            （仍写每 Issue 日志文件）。串行路径忽略该参数。

    Returns:
        退出码（0 成功，1 有 Issue 处理失败）
    """
    from backend.core.use_cases.run_agent_once import run_preflight_checks

    repo_path = request.repo_path
    config = request.config
    dry_run = request.dry_run
    agent = request.agent
    max_issues = request.max_issues
    github_client = request.github_client
    process_runner = request.process_runner
    content_generator = request.content_generator
    run_history_store = request.run_history_store
    run_trigger = request.run_trigger
    repo_id = request.repo_id
    concurrency = request.concurrency
    output_view = request.output_view
    effective_repo_id = repo_id or repo_path.name

    if not dry_run:
        try:
            run_preflight_checks(repo_path, config, process_runner)
        except Exception as exc:  # noqa: BLE001 - report preflight failure cleanly.
            _logger.error("Agent runner preflight failed: %s", exc)
            return 1

    # Realistic Validation 软门禁：维护 review 阶段 Issue 的勾选状态
    # label、重置过期签收并清理已关闭 Issue 的证据分支。
    # 与 Issue 领取相互独立，失败不影响本轮处理。
    if not dry_run:

        def _on_validation(
            validation_issue: IssueSummary,
            event_type: LifecycleEventType,
            event_key: str,
            detail: dict,
        ) -> None:
            record_lifecycle_event(
                store=run_history_store,
                repo_id=effective_repo_id,
                prd_path=resolve_lifecycle_prd_path(validation_issue),
                issue_number=validation_issue.number,
                trigger=run_trigger,
                event_type=event_type,
                actor="validation_gate",
                event_key=event_key,
                detail=detail,
            )

        try:
            process_validation_gate(
                repo_path=repo_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                on_validation=_on_validation,
            )
        except Exception as gate_exc:  # noqa: BLE001 - gate must not break polling.
            _logger.error("Validation gate pass failed: %s", gate_exc)

    # 发现 ready Issue。并行时单轮领取上限抬到 max(max_issues, concurrency)，
    # 使单独一个 --concurrency N 即可领到并跑 N 个，无需另调 --max-issues。
    # 定向模式（target_issue 非 None）只保留目标 Issue：直接 get_issue 取最新
    # 状态，按标签决定它进入哪条候选通道；其余 ready/running/blocked Issue
    # 一律不动。
    effective_max_issues = max(max_issues, concurrency)
    # daemon 补位模式：本轮能接的 Issue 数 = 并行上限 − 本机在执行数；本机在执行的
    # Issue 在下面所有通道里都被跳过，保证同一个 Issue 只由一个线程推进。
    execution_slots = request.execution_slots
    in_flight_numbers: frozenset[int] = frozenset()
    if execution_slots is not None:
        in_flight_numbers = execution_slots.in_flight_issue_numbers()
        effective_max_issues = max(0, concurrency - len(in_flight_numbers))
    ready_discovery_limit = max(effective_max_issues, _READY_DISCOVERY_LIMIT)
    target_issue_number = request.target_issue
    target_issue_summary: IssueSummary | None = None
    if target_issue_number is not None:
        try:
            target_issue_summary = github_client.get_issue(target_issue_number)
        except Exception as exc:  # noqa: BLE001 - 目标取不到即无候选可处理。
            _logger.error("Targeted Issue #%d could not be fetched: %s", target_issue_number, exc)
            return 1

    if target_issue_summary is not None:
        # 显式定向不再要求就绪标记：人点名即准入，没标记的 Issue 直接进 ready 通道
        # （也正因为没标记，别的机器的守护进程不会来抢）。已经带有其他 durable
        # workflow 状态（review / failed / supervising 等）的目标仍按原通道处理，
        # 不被放宽成「当新任务重跑」。守护进程侧（无 target）判定逐字不变。
        ready_issues = (
            [target_issue_summary]
            if not has_non_ready_workflow_label(target_issue_summary.labels, config)
            else []
        )
    else:
        ready_issues = [
            issue
            for issue in github_client.list_ready_issues(config.labels.ready, ready_discovery_limit)
            if issue.number not in in_flight_numbers
        ]
        if request.pass_activity is not None:
            request.pass_activity.ready_found = len(ready_issues)

    # 统一并发闸门：仅当调用方解析出生效上限且非定向模式时约束 ready 通道的
    # 新认领。running / blocked / cleanup 通道处理的是已在途或恢复性工作，
    # 不新增并发，保持原配额。计数失败按 fail-closed 处理：本轮不认领新 Issue，
    # 宁可不跑也不超发。
    ready_claim_budget = effective_max_issues
    running_count_failed = False
    live_running_count_for_budget = 0
    live_running_count: int | None = None
    if request.execution_ceiling is not None and target_issue_summary is None:
        ceiling = request.execution_ceiling
        try:
            live_running_count = count_live_running_issues(
                github_client,
                config.labels.running,
                ceiling,
            )
            # 标签更新有延迟：本机刚提交的 Issue 可能还没带上 running 标签。
            live_running_count_for_budget = max(live_running_count, len(in_flight_numbers))
        except Exception as count_exc:  # noqa: BLE001 - 计数失败即不认领（fail-closed）。
            _logger.warning(
                "Failed to count live '%s' Issues for the concurrency ceiling: %s; "
                "claiming zero new Issues this pass (fail-closed).",
                config.labels.running,
                count_exc,
            )
            running_count_failed = True
            live_running_count_for_budget = ceiling
        ready_claim_budget = min(
            effective_max_issues,
            max(0, ceiling - live_running_count_for_budget),
        )
        running_count_label = (
            "unknown"
            if live_running_count is None
            else (
                f"at least {live_running_count}"
                if live_running_count > ceiling
                else str(live_running_count)
            )
        )
        _logger.info(
            "Concurrency ceiling: ceiling=%d running=%s ready_budget=%d",
            ceiling,
            running_count_label,
            ready_claim_budget,
        )

    processed_count = 0
    ready_claimed_count = 0
    issues_to_process: list[tuple[IssueSummary, str]] = []

    for issue in sort_ready_issues(ready_issues):
        if len(issues_to_process) >= effective_max_issues:
            break
        declaration = parse_dependency_marker(issue.body)
        if declaration is not None:
            if _has_published_direct_pr_handoff(github_client, issue):
                issues_to_process.append((issue, "direct_pr_cleanup"))
                processed_count += 1
                continue
        if ready_claimed_count >= ready_claim_budget:
            continue
        if declaration is not None:
            verdict = evaluate_dependencies(declaration, github_client, config.labels)
            if not verdict.satisfied:
                mark_dependency_waiting(
                    issue=issue,
                    verdict=verdict,
                    github_client=github_client,
                    labels_config=config.labels,
                    dry_run=dry_run,
                )
                if dry_run:
                    _logger.info(
                        "DRY RUN: Issue #%d blocked by dependencies: %s",
                        issue.number,
                        ", ".join(
                            f"{b.blocker_type}:{b.target}({b.current_state})"
                            for b in verdict.blockers
                        ),
                    )
                continue
            clear_dependency_waiting(
                issue=issue,
                github_client=github_client,
                labels_config=config.labels,
                dry_run=dry_run,
            )
        issues_to_process.append((issue, "ready"))
        processed_count += 1
        ready_claimed_count += 1

    # 发现 running Issue（使用剩余配额）；定向模式只考虑目标 Issue。
    remaining = effective_max_issues - processed_count
    if remaining > 0:
        if target_issue_summary is not None:
            running_candidates = (
                [target_issue_summary]
                if config.labels.running in target_issue_summary.labels
                else []
            )
        else:
            try:
                # 本机在执行的 Issue 也带 running 标签：多查这么多个，避免它们占满候选窗口。
                running_candidates = github_client.list_review_candidate_issues(
                    [config.labels.running], remaining + len(in_flight_numbers)
                )
            except Exception as discovery_exc:
                if not running_count_failed:
                    raise
                _logger.warning(
                    "Could not query '%s' recovery candidates after the running-count "
                    "failure (%s); retrying with a bounded open-Issue scan.",
                    config.labels.running,
                    discovery_exc,
                )
                try:
                    open_issues = github_client.list_issues_by_label(
                        None, _READY_DISCOVERY_LIMIT, state="open"
                    )
                    running_candidates = [
                        issue for issue in open_issues if config.labels.running in issue.labels
                    ][:remaining]
                except Exception as fallback_exc:  # noqa: BLE001 - preserve the daemon pass.
                    _logger.warning(
                        "Could not discover in-flight '%s' recovery candidates after the "
                        "running-count failure (%s); skipping running recovery this pass.",
                        config.labels.running,
                        fallback_exc,
                    )
                    running_candidates = []
        for issue in running_candidates:
            if issue.number in in_flight_numbers:
                continue
            if len(issues_to_process) >= effective_max_issues:
                break
            if _has_published_direct_pr_handoff(github_client, issue):
                issues_to_process.append((issue, "direct_pr_cleanup"))
                continue
            is_rework, marker = _guard_running_issue_is_rework(issue, config, github_client)
            if is_rework and marker is not None:
                issues_to_process.append((issue, "running_rework"))
            elif _has_existing_local_commit_ready_for_publish(
                issue=issue,
                repo_path=repo_path,
                config=config,
                process_runner=process_runner,
            ) or _worktree_needs_rebase_recovery(
                issue=issue,
                repo_path=repo_path,
                config=config,
                process_runner=process_runner,
            ):
                issues_to_process.append((issue, "running_publish_recovery"))
            else:
                _logger.info(
                    "Skipping Issue #%d with label %s: no rework marker, no clean "
                    "local commit ready to publish, and no recoverable "
                    "rebase/detached worktree.",
                    issue.number,
                    config.labels.running,
                )

    # 发现 blocked Issue（使用剩余配额）；定向模式只考虑目标 Issue。
    remaining = effective_max_issues - len(issues_to_process)
    if remaining > 0:
        if target_issue_summary is not None:
            blocked_candidates = (
                [target_issue_summary]
                if config.labels.blocked in target_issue_summary.labels
                else []
            )
        else:
            blocked_candidates = github_client.list_review_candidate_issues(
                [config.labels.blocked], remaining
            )
        for issue in blocked_candidates:
            if issue.number in in_flight_numbers:
                continue
            marker = _guard_blocked_issue_has_resolution(issue, github_client)
            if marker is not None:
                issues_to_process.append((issue, "blocked_resolution"))
            elif _has_published_direct_pr_handoff(github_client, issue):
                issues_to_process.append((issue, "direct_pr_cleanup"))
            else:
                _logger.info(
                    "Skipping Issue #%d with label %s: no blocked_resolution_requested marker.",
                    issue.number,
                    config.labels.blocked,
                )

    if request.aggregate_pr:
        candidate_shortfall_exit_code = check_aggregate_candidate_shortfall(len(issues_to_process))
        if candidate_shortfall_exit_code is not None:
            return candidate_shortfall_exit_code

    if not issues_to_process:
        _logger.info(
            "No open Issues found with label %s, eligible running rework, or blocked resolution.",
            config.labels.ready,
        )
        return 0

    # DRY RUN：仅列出将处理的 Issue，不实际处理（串行、零副作用）。
    if dry_run:
        if request.aggregate_pr:
            log_aggregate_dry_run_preview([issue for issue, _ in issues_to_process])
        # 定向模式的候选来自 get_issue（单个目标），不是 ready 列表的 limit 宽度，
        # 因此不能套用同一句「覆盖 N 个候选」的措辞。
        if target_issue_summary is not None:
            _logger.info(
                "DRY RUN: targeted mode covers Issue #%d only (no ready-list discovery).",
                target_issue_number,
            )
        else:
            _logger.info(
                "DRY RUN: ready Issue ordering covers the %d candidates returned by GitHub.",
                ready_discovery_limit,
            )
        for issue, issue_kind in issues_to_process:
            selected_agent = choose_agent(issue, config, agent)
            _logger.info(
                "DRY RUN: would process Issue #%d (%s) with %s: %s [priority=%s]",
                issue.number,
                issue_kind,
                selected_agent,
                issue.title,
                issue_priority(issue) or "unset (after P3)",
            )
            if issue_kind == "blocked_resolution":
                marker = _guard_blocked_issue_has_resolution(issue, github_client)
                if marker is None:
                    _logger.info(
                        "DRY RUN: Issue #%d blocked_resolution marker not found, skipping.",
                        issue.number,
                    )
        return 0

    process_kwargs = {
        "repo_path": repo_path,
        "config": config,
        "agent": agent,
        "content_generator": content_generator,
        "run_history_store": run_history_store,
        "run_trigger": run_trigger,
        "effective_repo_id": effective_repo_id,
        "publish_stage": request.publish_stage,
    }

    # 串行（并行上限 1）时只有一个 Issue 在跑，它的输出同时镜像到启动终端；每个
    # Issue 的可见输出都落到 ``logs/agent-runner/issues/<repo_id>/`` 下的 per-Issue
    # 文件，行首带 ``[HH:MM:SS]``（Issue #223）。并行时输出分流到实时看板的独立列，
    # 互不交错。观测上下文绑在 contextvar 上，每个线程的调用身份天然隔离。
    is_serial = concurrency <= 1
    active_view = NoOpRunnerLiveView() if is_serial else (output_view or NoOpRunnerLiveView())
    routing = _IssueRoutingContext(
        effective_repo_id=effective_repo_id,
        log_base=request.log_base or repo_path / "logs",
        output_view=active_view,
        console_sink=_mirror_to_console if is_serial else None,
        github_client=github_client,
        process_runner=process_runner,
        run_history_store=run_history_store,
        progress_callback=request.progress_callback,
        process_kwargs=process_kwargs,
        process_issue=partial(
            _process_issue_if_admitted,
            request=request,
            process_issue=_process_single_issue,
        ),
    )
    process_routed = partial(_process_routed_issue, routing=routing)

    # daemon 补位模式：提交到常驻执行名单后立即返回，下一次轮询再清理与补位。
    # 实时看板随 daemon 进程存活，不在这里关闭。
    if execution_slots is not None:
        for issue, issue_kind in issues_to_process:
            execution_slots.submit(issue.number, partial(process_routed, (issue, issue_kind)))
        return 0

    if is_serial:
        results = [process_routed(item) for item in issues_to_process]
    else:
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                results = list(pool.map(process_routed, issues_to_process))
        finally:
            active_view.close()
    return complete_aggregate_queue(
        AggregateQueueCompletionRequest(
            run_request=request,
            issue_numbers=tuple(issue.number for issue, _ in issues_to_process),
            exit_codes=tuple(results),
        )
    )
