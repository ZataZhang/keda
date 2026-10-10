"""Agent Runner 的单轮队列调度实现。"""

from __future__ import annotations
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
from backend.core.use_cases.agent_runner_blocked_claim import BlockedWorktreeClaimedError
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimArbitrationLost
from backend.core.use_cases.agent_runner_dependencies import (
    clear_dependency_waiting,
    evaluate_dependencies,
    mark_dependency_waiting,
    parse_dependency_marker,
)
from backend.core.use_cases.agent_runner_failure_marking import (
    _mark_issue_blocked,
    _mark_issue_failed,
)
from backend.core.use_cases.agent_runner_orchestrate import (
    AppConfig,
    AttemptResult,
    ForbiddenBlockedError,
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
    IRunHistoryStore,
    IRunnerLiveView,
    IssueSummary,
    _READY_DISCOVERY_LIMIT,
    _append_run_record_locked,
    _guard_blocked_issue_has_resolution,
    _guard_running_issue_is_rework,
    _logger,
    _persist_attempt_result,
    _process_blocked_resolution,
    _process_ready_issue,
    _process_running_publish_recovery,
    _process_running_rework,
    choose_agent,
    create_or_reuse_worktree,
    run_issue_with_agent_fallback,
)
from backend.core.shared.models.agent_runner import TokenUsage
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_invocation_tracing import (
    bound_invocation_trace_context,
    build_invocation_trace_context,
)
from backend.core.use_cases.agent_runner_direct_pr_label import PublishStageSelection
from backend.core.use_cases.agent_runner_output_routing import (
    _OutputRoutedProcessRunner,
    issue_output_routing,
)
from backend.core.use_cases.agent_runner_queue import issue_priority, sort_ready_issues
from backend.core.use_cases.agent_runner_validation_gate import process_validation_gate
from backend.core.use_cases.agent_runner_workflow import claim_blocked_issue
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_attempt_event_detail,
    record_lifecycle_event,
    record_lifecycle_terminal,
)
from backend.core.use_cases.lifecycle_agent_resolution import attach_prd_lifecycle_overrides
from backend.core.use_cases.agent_runner_worktree_probe import (
    _has_existing_local_commit_ready_for_publish,
    _worktree_needs_rebase_recovery,
)
from backend.core.use_cases.agent_runner_prd_activity import (
    PrdActivityConflictError,
    PrdActivityLease,
)
from backend.core.use_cases.create_prd_from_issue import (
    CreatePrdFromIssueRequest,
    create_prd_from_issue,
)
from backend.core.use_cases.run_target_admission import has_non_ready_workflow_label

RUNTIME_DEPENDENCY_NAMES = (
    "_process_ready_issue",
    "_process_running_rework",
    "_process_blocked_resolution",
    "_process_running_publish_recovery",
    "choose_agent",
    "create_or_reuse_worktree",
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


@dataclass(frozen=True)
class PrdReworkRequest:
    """一轮 PRD rework Issue 处理请求。"""

    repo_path: Path
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    content_generator: IContentGenerator | None = None
    max_issues: int = 1


def process_prd_rework_issues(request: PrdReworkRequest) -> None:
    """处理标记为 PRD rework 的 Issue。

    在正常的 ready Issue 执行之前调用：为每个 Issue 建/复用 ``issue-<N>``
    worktree，在 worktree 内生成或重写 PRD、commit 进 ``issue-<N>`` 分支并经
    draft PR 落地，随后更新 Issue body/labels/comments。主工作树保持干净。
    单个 Issue 失败时记录错误并继续处理后续 Issue，不让 PRD 生成阶段污染
    ready Issue 执行阶段。

    Args:
        repo_path: 目标仓库路径。
        config: 应用配置。
        github_client: GitHub 客户端。
        process_runner: git 命令执行器（建/复用 worktree、commit、push）。
        content_generator: 可选的 AI 内容生成器。
        max_issues: 本轮最多处理的 rework-prd Issue 数量。
    """
    repo_path = request.repo_path
    config = request.config
    github_client = request.github_client
    process_runner = request.process_runner
    content_generator = request.content_generator
    issues = github_client.list_rework_prd_issues(
        config.labels.rework_prd,
        limit=request.max_issues,
    )
    for issue in issues:
        _logger.info("Processing PRD rework for Issue #%d: %s", issue.number, issue.title)
        try:
            worktree_path = create_or_reuse_worktree(repo_path, issue, config, process_runner)
            create_prd_from_issue(
                request=CreatePrdFromIssueRequest(
                    repo_path=repo_path,
                    issue=issue,
                    config=config,
                    generated_content_config=config.generated_content,
                    content_generator=content_generator,
                    queue_ready=True,
                    worktree_path=worktree_path,
                    process_runner=process_runner,
                ),
                github_client=github_client,
            )
        except Exception as exc:  # noqa: BLE001 - isolate PRD rework failures.
            _logger.exception("PRD rework failed for Issue #%d", issue.number)
            try:
                github_client.edit_issue_labels(
                    issue.number,
                    add=[config.labels.failed],
                    remove=[config.labels.rework_prd],
                )
            except Exception as label_exc:  # noqa: BLE001 - best-effort label update.
                _logger.error(
                    "Failed to mark Issue #%d as %s: %s",
                    issue.number,
                    config.labels.failed,
                    label_exc,
                )
            try:
                github_client.comment_issue(
                    issue.number,
                    f"PRD generation failed: {exc}\n\n"
                    "Please review the error and re-add the "
                    f"`{config.labels.rework_prd}` label to retry.",
                )
            except Exception as comment_exc:  # noqa: BLE001 - best-effort comment.
                _logger.error(
                    "Failed to comment on Issue #%d PRD failure: %s",
                    issue.number,
                    comment_exc,
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
        detail={"agent": selected_agent, "issue_kind": issue_kind},
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
        detail={"agent": selected_agent, "issue_kind": issue_kind},
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
                ),
            )
        elif issue_kind == "blocked_resolution":
            marker = _guard_blocked_issue_has_resolution(issue, github_client)
            if marker is None:
                output_view.update_status(issue.number, "skipped")
                return 0
            claimed = claim_blocked_issue(github_client, issue.number, config)
            if not claimed:
                _logger.info(
                    "Issue #%d already claimed by another runner, skipping.",
                    issue.number,
                )
                output_view.update_status(issue.number, "skipped")
                return 0
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
    except ForbiddenBlockedError as exc:
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


@dataclass(frozen=True)
class RunOnceRequest:
    """Agent Runner 单轮队列调度请求。"""

    repo_path: Path
    config: AppConfig
    dry_run: bool
    agent: str
    max_issues: int
    github_client: IGitHubClient
    process_runner: IProcessRunner
    content_generator: IContentGenerator | None = None
    run_history_store: IRunHistoryStore | None = None
    run_trigger: str = "cli_run"
    repo_id: str | None = None
    concurrency: int = 1
    output_view: IRunnerLiveView | None = None
    #: 定向目标 Issue 编号（``kc run --issue``）。非 ``None`` 时只处理该
    #: Issue（仍走依赖门禁与 claim）；``None`` 保持"按优先级捞队列"行为。
    target_issue: int | None = None
    #: 发布档位（``kc run --fast-merge`` / ``--direct-pr``）：本次运行执行 agent
    #: 之后还剩多少门禁与第二个 agent。``NORMAL`` 即默认全量路径；daemon 与其余
    #: 调用方传的仍是 ``NORMAL``（默认值），但它是**调用侧请求档位**——认领后
    #: core 会用 Issue 上的 ``direct-pr`` 标签把它升级为 ``DIRECT``（逐 Issue 独立）。
    publish_stage: PublishStage = PublishStage.NORMAL


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
        ready_issues = github_client.list_ready_issues(config.labels.ready, ready_discovery_limit)
    processed_count = 0
    issues_to_process: list[tuple[IssueSummary, str]] = []

    for issue in sort_ready_issues(ready_issues):
        if processed_count >= effective_max_issues:
            break
        declaration = parse_dependency_marker(issue.body)
        if declaration is not None:
            if _has_published_direct_pr_handoff(github_client, issue):
                issues_to_process.append((issue, "direct_pr_cleanup"))
                processed_count += 1
                continue
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
            running_candidates = github_client.list_review_candidate_issues(
                [config.labels.running], remaining
            )
        for issue in running_candidates:
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

    if not issues_to_process:
        _logger.info(
            "No open Issues found with label %s, eligible running rework, or blocked resolution.",
            config.labels.ready,
        )
        return 0

    # DRY RUN：仅列出将处理的 Issue，不实际处理（串行、零副作用）。
    if dry_run:
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

    # 串行路径：concurrency<=1 时逐个处理。与历史行为的差异有二——每个
    # Issue 的可见输出同时落到 ``logs/agent-runner/issues/<repo_id>/`` 下
    # 的 per-Issue 文件（供第二终端 / Console 按 Issue 续读），且原启动终端
    # 经 ``console_sink`` 收到的是路由 sink 的那一份文本，因此每行行首带
    # ``[HH:MM:SS]``：与未经路由时的终端实时视图逐行一致（Issue #223），
    # TTY 与重定向两种场景相同。
    if concurrency <= 1:
        noop_view = NoOpRunnerLiveView()
        log_base = repo_path / "logs"

        def _console_mirror(chunk: str) -> None:
            print(chunk, end="", flush=True, file=sys.stdout)

        def _process_serial(item: tuple[IssueSummary, str]) -> int:
            issue, issue_kind = item
            try:
                with issue_output_routing(
                    repo_id=effective_repo_id,
                    issue_number=issue.number,
                    log_base=log_base,
                    output_view=noop_view,
                    console_sink=_console_mirror,
                ) as sink:
                    scoped_runner = _OutputRoutedProcessRunner(process_runner, sink)
                    with bound_invocation_trace_context(
                        build_invocation_trace_context(
                            repo_id=effective_repo_id,
                            issue_number=issue.number,
                            run_history_store=run_history_store,
                        )
                    ):
                        return _process_single_issue(
                            issue,
                            issue_kind,
                            process_runner=scoped_runner,
                            output_view=noop_view,
                            **process_kwargs,
                        )
            except Exception as exc:  # noqa: BLE001 - 单个 Issue 的 I/O 不应中断本轮。
                _logger.error("Serial routing failed for Issue #%d: %s", issue.number, exc)
                return 1

        exit_code = 0
        for issue, issue_kind in issues_to_process:
            exit_code |= _process_serial((issue, issue_kind))
        return exit_code

    # 并行路径：线程池同一轮并行处理多个 Issue。每个 Issue 的 agent 输出经
    # output_sink 路由到独立日志文件与（可选）独立看板列，互不交错。
    active_view = output_view or NoOpRunnerLiveView()
    log_base = repo_path / "logs"

    def _process_with_routing(item: tuple[IssueSummary, str]) -> int:
        issue, issue_kind = item
        try:
            with issue_output_routing(
                repo_id=effective_repo_id,
                issue_number=issue.number,
                log_base=log_base,
                output_view=active_view,
            ) as sink:
                scoped_runner = _OutputRoutedProcessRunner(process_runner, sink)
                # 观测上下文绑在 contextvar 上：线程池的每个 worker 有各自的上下文，
                # 因此并行处理多个 Issue 时调用身份天然隔离，不靠相邻文本推断归属。
                with bound_invocation_trace_context(
                    build_invocation_trace_context(
                        repo_id=effective_repo_id,
                        issue_number=issue.number,
                        run_history_store=run_history_store,
                    )
                ):
                    return _process_single_issue(
                        issue,
                        issue_kind,
                        process_runner=scoped_runner,
                        output_view=active_view,
                        **process_kwargs,
                    )
        except Exception as exc:  # noqa: BLE001 - one Issue's I/O must not kill the pass.
            _logger.error("Parallel routing failed for Issue #%d: %s", issue.number, exc)
            return 1

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(_process_with_routing, issues_to_process))
    finally:
        active_view.close()
    return 1 if any(results) else 0
