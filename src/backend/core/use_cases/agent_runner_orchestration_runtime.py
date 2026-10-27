"""Agent Runner 的单轮队列调度实现。"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.shared.models.agent_runner import TokenUsage
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_invocation_tracing import (
    bound_invocation_trace_context,
    build_invocation_trace_context,
)
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
from backend.core.use_cases.agent_runner_dependencies import (
    clear_dependency_waiting,
    evaluate_dependencies,
    mark_dependency_waiting,
    parse_dependency_marker,
)
from backend.core.use_cases.agent_runner_direct_pr_label import PublishStageSelection
from backend.core.use_cases.agent_runner_failure_marking import (
    _mark_issue_blocked,
    _mark_issue_failed,
)
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_attempt_event_detail,
    record_lifecycle_event,
    record_lifecycle_terminal,
)
from backend.core.use_cases.agent_runner_orchestrate import (
    _READY_DISCOVERY_LIMIT,
    AppConfig,
    AttemptResult,
    DependencyEnvironmentBlockedError,
    ForbiddenBlockedError,
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
from backend.core.use_cases.agent_runner_output_routing import (
    _OutputRoutedProcessRunner,
    issue_output_routing,
)
from backend.core.use_cases.agent_runner_prd_activity import (
    PrdActivityConflictError,
    PrdActivityLease,
)
from backend.core.use_cases.agent_runner_queue import issue_priority, sort_ready_issues
from backend.core.use_cases.agent_runner_validation_gate import process_validation_gate
from backend.core.use_cases.agent_runner_workflow import claim_blocked_issue
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


def _resolve_lifecycle_prd_path(issue: IssueSummary) -> str:
    """从 Issue 正文解析其引用的 PRD 相对路径；解析失败返回空串。

    返回空串表示 runner 无法定位该 PRD：此时 run id 仍由 Issue 编号决定，
    run 行的 ``prd_path`` 保留 backlog 启动侧已写入的真实路径，不会被空值
    覆盖。观测失败绝不阻断 Issue 处理。
    """
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    try:
        return extract_prd_path(issue.body) or ""
    except Exception:  # noqa: BLE001 - observation must not break the run.
        return ""


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
    # PRD 级覆盖随 Issue 流动：在这里（唯一同时掌握 repo 路径与 Issue 的位置）从该
    # Issue 引用的 PRD 文件解析头部 lifecycle_agents 块并回填，使实现 / 审核 / 监督
    # 等阶段的解析函数无需额外参数即可读到 PRD 级覆盖（最高优先级）。
    issue = attach_prd_lifecycle_overrides(issue, repo_path)
    lifecycle_prd_path = _resolve_lifecycle_prd_path(issue)
    selected_agent = choose_agent(issue, config, agent)
    output_view.register_issue(issue.number, selected_agent)
    run_started_at = datetime.now(timezone.utc)
    used_agent = selected_agent

    # 生命周期观测（旁路）：本轮 runner 领到该 Issue，记为一次执行开始。
    # run id 由 repo_id + Issue 编号确定性推导，与 backlog 启动侧写入的 run
    # 完全一致，因此这里不会新建重复 run。
    record_lifecycle_event(
        store=run_history_store,
        repo_id=effective_repo_id,
        prd_path=lifecycle_prd_path,
        issue_number=issue.number,
        trigger=run_trigger,
        event_type=LifecycleEventType.STARTED,
        actor="runner",
        occurred_at=run_started_at.isoformat(timespec="seconds"),
        detail={"agent": selected_agent, "issue_kind": issue_kind, "run_trigger": run_trigger},
    )
    record_lifecycle_event(
        store=run_history_store,
        repo_id=effective_repo_id,
        prd_path=lifecycle_prd_path,
        issue_number=issue.number,
        trigger=run_trigger,
        event_type=LifecycleEventType.CLAIMED,
        actor="runner",
        occurred_at=run_started_at.isoformat(timespec="seconds"),
        detail={"agent": selected_agent, "issue_kind": issue_kind, "run_trigger": run_trigger},
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

    prd_activity_lease = PrdActivityLease(
        repo_path, lifecycle_prd_path, issue.number, selected_agent
    )
    stage_selection = PublishStageSelection()
    try:
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
                        on_claimed=prd_activity_lease.start,
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
            # 因此以新鲜快照为底、把两个 lifecycle 覆盖字段带过来。
            issue = replace(
                claimed_issue_snapshot,
                lifecycle_overrides=issue.lifecycle_overrides,
                lifecycle_preset_overrides=issue.lifecycle_preset_overrides,
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
        return 0
    except AgentDaemonShutdownInterrupt:
        _skip_issue_on_daemon_shutdown(issue.number, output_view)
        return 0
    except GitHubApiRateLimitError:
        if _skip_issue_on_daemon_shutdown(issue.number, output_view):
            return 0
        output_view.update_status(issue.number, "skipped")
        raise

    except (
        ForbiddenBlockedError,
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

    except BlockedWorktreeClaimedError as exc:
        _logger.info(
            "Issue #%d worktree already claimed by another runner, skipping: %s",
            issue.number,
            exc,
        )
        output_view.update_status(issue.number, "skipped")
        return 0
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
        prd_activity_lease.close()


def _has_published_direct_pr_handoff(github_client: IGitHubClient, issue: IssueSummary) -> bool:
    """只读证明待交接 PR，让发现过滤器不把已发布轮次误当新工作拦截。"""
    from backend.core.use_cases.agent_runner_direct_pr_round import has_readonly_direct_pr_cleanup

    try:
        return has_readonly_direct_pr_cleanup(github_client, issue)
    except Exception as exc:  # noqa: BLE001 - 关联读失败不允许旁路发现门禁。
        _logger.warning(
            "Cannot establish pending Direct PR handoff for Issue #%d: %s", issue.number, exc
        )
        return False


def _is_issue_admitted(request: RunOnceRequest, issue: IssueSummary) -> bool:
    """在启动 worker 前检查可选资源准入门。"""
    if request.issue_admission_check is None or request.issue_admission_check():
        return True
    _logger.warning(
        "Disk admission paused before claiming Issue #%d; the Issue remains queued.",
        issue.number,
    )
    return False


@dataclass(frozen=True)
class _IssueRoutingContext:
    """单个 Issue 执行时的输出路由与观测上下文（同一轮内所有 Issue 共用）。"""

    effective_repo_id: str
    log_base: Path
    output_view: IRunnerLiveView
    console_sink: Callable[[str], None] | None
    process_runner: IProcessRunner
    run_history_store: IRunHistoryStore | None
    process_kwargs: dict[str, Any]
    run_request: RunOnceRequest


def _mirror_to_console(chunk: str) -> None:
    """把串行执行的 Issue 输出镜像到启动终端。"""
    print(chunk, end="", flush=True, file=sys.stdout)


def _process_routed_issue(
    item: tuple[IssueSummary, str],
    *,
    routing: _IssueRoutingContext,
) -> int:
    """在输出路由与观测上下文里处理一个 Issue；路由自身的异常按失败返回。"""
    issue, issue_kind = item
    if not _is_issue_admitted(routing.run_request, issue):
        return 0
    try:
        with issue_output_routing(
            repo_id=routing.effective_repo_id,
            issue_number=issue.number,
            log_base=routing.log_base,
            output_view=routing.output_view,
            console_sink=routing.console_sink,
        ) as sink:
            scoped_runner = _OutputRoutedProcessRunner(routing.process_runner, sink)
            with bound_invocation_trace_context(
                build_invocation_trace_context(
                    repo_id=routing.effective_repo_id,
                    issue_number=issue.number,
                    run_history_store=routing.run_history_store,
                )
            ):
                return _process_single_issue(
                    issue,
                    issue_kind,
                    process_runner=scoped_runner,
                    output_view=routing.output_view,
                    **routing.process_kwargs,
                )
    except Exception as exc:  # noqa: BLE001 - 单个 Issue 的 I/O 不应中断本轮。
        _logger.error("Output routing failed for Issue #%d: %s", issue.number, exc)
        return 1


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

    # 前置检查
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
                prd_path=_resolve_lifecycle_prd_path(validation_issue),
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
        "github_client": github_client,
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
        log_base=repo_path / "logs",
        output_view=active_view,
        console_sink=_mirror_to_console if is_serial else None,
        process_runner=process_runner,
        run_history_store=run_history_store,
        process_kwargs=process_kwargs,
        run_request=request,
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
