"""Post-PR review polling — single pass across supervising and review issues."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.interfaces.runner_console import IRunHistoryStore
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    PullRequestContext,
    TokenUsage,
)
from backend.core.use_cases.agent_runner_events import (
    parse_latest_event_marker,
)
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    record_lifecycle_event,
    record_lifecycle_terminal,
)
from backend.core.use_cases.agent_runner_merge_queue import (
    process_merge_queue,
)
from backend.core.use_cases.pr_supervisor import (
    build_rework_intent_comment,
    guard_supervisor_action_for_pr_state,
    run_post_pr_supervisor_cycle,
)
from backend.core.use_cases.agent_runner_workflow import (
    LEGACY_REVIEW_LABEL,
    find_latest_unconsumed_marker,
    synchronize_human_review_label,
    transition_issue_workflow_state,
    workflow_state_labels,
)
from backend.core.use_cases.agent_runner_git import (
    has_changes,
    pop_worktree_stash,
    stash_worktree_changes,
)
from backend.core.use_cases.agent_runner_issue_control import (
    autopilot_hold_blocks,
)
from backend.core.use_cases.lifecycle_agent_resolution import attach_prd_lifecycle_overrides
from backend.core.use_cases.run_agent_once import (
    create_or_reuse_worktree,
    get_head_sha,
    resolve_supervisor_agent,
)

_logger = logging.getLogger(__name__)


def _lifecycle_prd_path(issue: IssueSummary) -> str:
    """从 Issue 正文解析 PRD 相对路径；失败返回空串（观测降级，不阻断审核）。"""
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    try:
        return extract_prd_path(issue.body) or ""
    except Exception:  # noqa: BLE001 - observation only.
        return ""


def _record_agent_usage_event(
    *,
    run_history_store: IRunHistoryStore | None,
    repo_id: str,
    issue: IssueSummary,
    flow: str,
    agent_name: str,
    usage: TokenUsage,
) -> None:
    """把一次 agent 调用的 token 用量落成生命周期观测事件（旁路）。

    观测事件（``agent_token_usage``）不推进阶段、不占时长；无账本能力时静默
    跳过，写入失败由 :func:`record_lifecycle_event` 内部容错。
    """
    record_lifecycle_event(
        store=run_history_store,
        repo_id=repo_id,
        prd_path=_lifecycle_prd_path(issue),
        issue_number=issue.number,
        trigger="review_once",
        event_type=LifecycleEventType.AGENT_TOKEN_USAGE,
        actor="supervisor",
        event_key=f"agent-token-usage:{flow}:{datetime.now(timezone.utc).isoformat()}",
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


def _record_review_outcome(
    *,
    run_history_store: IRunHistoryStore | None,
    repo_id: str,
    issue: IssueSummary,
    outcome: str,
    actor: str,
) -> None:
    """把审核候选的单次结果映射为生命周期事件（旁路）。

    只记录明确的阶段推进：通过、失败、仍在等待；``skipped_*`` / ``deferred_*``
    不改变阶段，因此不写事件，避免用“没发生的事”制造时间线噪声。
    """
    if outcome.startswith(("skipped_", "deferred_")):
        return
    if outcome == "approved_for_human_review":
        event_type = LifecycleEventType.REVIEW_PASSED
    elif outcome.startswith("blocked_") or outcome == "marked_failed":
        event_type = LifecycleEventType.REVIEW_FAILED
    else:
        event_type = LifecycleEventType.REVIEW_STARTED
    record_lifecycle_event(
        store=run_history_store,
        repo_id=repo_id,
        prd_path=_lifecycle_prd_path(issue),
        issue_number=issue.number,
        trigger="review_once",
        event_type=event_type,
        actor=actor,
        event_key=f"{event_type.value}:{issue.number}:{outcome}",
        detail={"outcome": outcome},
    )


def _record_merge_outcomes(
    *,
    run_history_store: IRunHistoryStore | None,
    repo_id: str,
    outcomes: list,
    candidates_by_number: dict[int, IssueSummary],
) -> None:
    """把合并队列结果映射为生命周期事件（旁路）。"""
    for outcome in outcomes:
        issue = candidates_by_number.get(outcome.issue_number)
        if issue is None:
            continue
        action = getattr(outcome, "action", "")
        if action.startswith("skipped_"):
            continue
        detail = {"merge_action": action}
        if action == "merged":
            record_lifecycle_terminal(
                store=run_history_store,
                repo_id=repo_id,
                prd_path=_lifecycle_prd_path(issue),
                issue_number=issue.number,
                trigger="review_once",
                event_type=LifecycleEventType.MERGED,
                outcome="completed",
                actor="merge_queue",
                detail=detail,
            )
        elif action == "blocked_forbidden":
            record_lifecycle_terminal(
                store=run_history_store,
                repo_id=repo_id,
                prd_path=_lifecycle_prd_path(issue),
                issue_number=issue.number,
                trigger="review_once",
                event_type=LifecycleEventType.BLOCKED,
                outcome="blocked",
                actor="merge_queue",
                detail=detail,
            )
        else:
            record_lifecycle_event(
                store=run_history_store,
                repo_id=repo_id,
                prd_path=_lifecycle_prd_path(issue),
                issue_number=issue.number,
                trigger="review_once",
                event_type=LifecycleEventType.MERGE_STARTED,
                actor="merge_queue",
                event_key=f"merge_started:{issue.number}:{action}",
                detail=detail,
            )


def _context_changed_wide(
    pr_context: PullRequestContext,
    last_marker,
    base_sha_remote: str,
    issue_comments_count: int,
    pr_comments_count: int,
) -> bool:
    """Return whether PR context has changed since the last supervisor event."""
    if last_marker is None:
        return True
    # mark_failed 意味着上一轮 supervision 没有产出有效评审结论（例如 agent
    # 基础设施崩溃重试耗尽，或输出不可解析触发 fail-closed）。Issue 能再次
    # 进入评审队列说明人工已把 label 从 agent/failed 拨回，这本身就是明确的
    # 重试请求，不应被"上下文未变化"的去重机制拦下
    if last_marker.action == "mark_failed":
        return True
    if last_marker.head_sha != pr_context.head_sha:
        return True
    if base_sha_remote and last_marker.base_sha != base_sha_remote:
        return True
    if last_marker.checks_state is not None and last_marker.checks_state != pr_context.checks_state:
        return True
    if last_marker.mergeable is not None and last_marker.mergeable != pr_context.mergeable:
        return True
    if (
        last_marker.issue_comments_count is not None
        and last_marker.issue_comments_count != issue_comments_count
    ):
        return True
    if (
        last_marker.pr_comments_count is not None
        and last_marker.pr_comments_count != pr_comments_count
    ):
        return True
    return False


def _extract_pr_branch_from_comments(comments: list[str]) -> str | None:
    """Extract the latest known PR branch from Issue comments."""
    for comment_body in reversed(comments):
        marker = parse_latest_event_marker([comment_body])
        if marker is not None and marker.pr_branch:
            return marker.pr_branch

        branch_patterns = (
            r"PR Branch:\s*`([^`]+)`",
            r"Branch:\s*`([^`]+)`",
        )
        for branch_pattern in branch_patterns:
            branch_match = re.search(branch_pattern, comment_body)
            if branch_match:
                return branch_match.group(1)
    return None


def _process_review_candidate(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    run_history_store: IRunHistoryStore | None = None,
    repo_id: str = "",
) -> str:
    """Run supervisor cycle for a single review candidate.

    ``repo_id`` 必须是**注册表**仓库 id：接管控制行按注册表 id 登记，传目录名或留空
    都查不到那一行，会把正在被人工接管的 Issue 放回监督流程（FR-5）。
    """
    # 人工接管闸门（Issue #278）：held Issue 一律不进 review 监督，无论
    # require_verifier_pass 如何配置——闸门只由同 head 的全新 GREEN 清除。
    if autopilot_hold_blocks(repo_id=repo_id or None, issue_number=issue.number):
        _logger.info(
            "Issue #%d is under manual takeover hold; skipping review supervision.",
            issue.number,
        )
        return "skipped_manual_takeover_hold"
    comments = github_client.list_issue_comments(issue.number)

    # Guard: if Issue is effectively in agent/running with a pending rework
    # marker, do not run review; let run consume the marker to avoid overwriting
    # the pending repair/rebase request with a stale approval.
    if config.labels.running in issue.labels:
        pending_rework = find_latest_unconsumed_marker(
            comments,
            phase="post_pr_rework_requested",
            completion_phases={
                "implementation_complete",
                "draft_pr_created",
                "publish_recovered",
                "rebase_repair_complete",
            },
        )
        if pending_rework is not None:
            _logger.info(
                "Issue #%d has pending rework marker; skipping review to let run process it.",
                issue.number,
            )
            return "skipped_pending_rework"

    last_marker = parse_latest_event_marker(comments)

    # Find the linked PR context
    pr_branch = _extract_pr_branch_from_comments(comments)
    if pr_branch is None:
        _logger.warning("Issue #%d has no identifiable PR branch; skipping.", issue.number)
        return "skipped_no_pr_branch"

    pr_context = github_client.get_pull_request_context(pr_branch)
    if pr_context is None:
        open_pr_url = github_client.find_open_pr_by_head(pr_branch)
        if open_pr_url:
            _logger.warning(
                "Issue #%d branch %s has an open PR but complete PR context is "
                "unavailable; deferring supervision to avoid unsafe approval.",
                issue.number,
                pr_branch,
            )
            return "deferred_pr_context_unavailable"
        else:
            _logger.warning(
                "Issue #%d branch %s has no open PR; skipping.",
                issue.number,
                pr_branch,
            )
            return "skipped_no_open_pr"

    pr_number_match = re.search(r"/pull/(\d+)", pr_context.pr_url)
    pr_comments: list[str] = []
    if pr_number_match:
        pr_comments = github_client.list_pr_comments(int(pr_number_match.group(1)))

    base_sha_remote = github_client.get_remote_base_sha(config.git.remote, config.git.base_branch)

    if not _context_changed_wide(
        pr_context,
        last_marker,
        base_sha_remote,
        issue_comments_count=len(comments),
        pr_comments_count=len(pr_comments),
    ):
        supervisor_action_states = {
            "approve_for_human_review": config.labels.review,
            "wait_for_checks": config.labels.supervising,
            "request_human_input": config.labels.blocked,
            "repair_pr_branch": config.labels.running,
            "rebase_pr_branch": config.labels.running,
            "resolve_conflict": config.labels.running,
        }
        target_state = (
            supervisor_action_states.get(last_marker.action or "")
            if last_marker is not None
            else None
        )
        if target_state is not None and target_state not in issue.labels:
            transition_issue_workflow_state(
                github_client,
                issue.number,
                config,
                target_state,
            )
            _logger.info(
                "Issue #%d context is unchanged; reconciled workflow state to %s from "
                "the last supervisor action %s.",
                issue.number,
                target_state,
                last_marker.action if last_marker is not None else None,
            )
            return "skipped_context_unchanged_reconciled"
        _logger.info(
            "Issue #%d context unchanged since last supervisor event; skipping.",
            issue.number,
        )
        return "skipped_context_unchanged"

    # Move to supervising if currently in review, using shared transition helper
    # so other durable workflow labels (including stale running/failed) are cleaned.
    if config.labels.review in issue.labels:
        transition_issue_workflow_state(
            github_client, issue.number, config, config.labels.supervising
        )

    worktree_path = create_or_reuse_worktree(repo_path, issue, config, process_runner)
    # PRD 级覆盖随 Issue 流动：从该 Issue 引用的 PRD 文件头部解析 lifecycle_agents
    # 并回填，使 `kc supervise` / supervise-daemon 这条路径也能读到 PRD 的 supervisor 覆盖
    # （与发布路径共用同一解析规则）。
    issue = attach_prd_lifecycle_overrides(issue, repo_path)
    # 命令行 --agent > PRD 覆盖 > post_pr_supervisor.supervisor_agent > Issue 标签路由，
    # 与发布路径共用同一解析规则（此前这里只按标签走，配置的 supervisor 被忽略）。
    supervisor_agent = resolve_supervisor_agent(issue, config, agent)
    cycle = (last_marker.cycle + 1) if last_marker else 1

    stashed = False
    if has_changes(worktree_path, process_runner):
        stashed = stash_worktree_changes(worktree_path, process_runner, cycle)
        if not stashed:
            github_client.comment_issue(
                issue.number,
                "## Agent Runner Review Blocked\n\n"
                "Worktree has uncommitted changes before read-only supervisor cycle. "
                "Could not auto-stash; moving to blocked.",
            )
            transition_issue_workflow_state(
                github_client, issue.number, config, config.labels.blocked
            )
            return "blocked_dirty_worktree_before_supervisor"

    try:
        action_result = run_post_pr_supervisor_cycle(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            pr_context=pr_context,
            supervisor_agent=supervisor_agent,
            # `kc supervise` 入口拿不到本次实现者，回退候选不排除 builder。
            builder_agent=None,
            cycle=cycle,
        )
    except Exception:
        # Supervisor cycle 异常时先恢复 stash，避免变更丢失在 worktree 里。
        if stashed:
            process_runner.run(
                ["git", "stash", "pop"],
                cwd=worktree_path,
                check=False,
            )
        raise
    action_result = guard_supervisor_action_for_pr_state(action_result, pr_context)
    # 生命周期观测（旁路）：supervisor agent 调用的 token 用量落一条观测事件；
    # 不推进阶段、不占时长，写入失败不阻断审核。
    if action_result.token_usage is not None:
        _record_agent_usage_event(
            run_history_store=run_history_store,
            # 记录键与闸门查询键分离：repo_id 是注册表原值（可能为空），观测事件
            # 沿用目录名回落；闸门只认注册表 id（空值走 Issue 编号兜底）。
            repo_id=repo_id or repo_path.name,
            issue=issue,
            flow="supervise",
            agent_name=supervisor_agent,
            usage=action_result.token_usage,
        )

    if action_result.action == "approve_for_human_review":
        if stashed:
            pop_worktree_stash(worktree_path, process_runner)
        # 只读 supervisor cycle 后若留下未提交变更，不能 approve 进入 human review。
        if has_changes(worktree_path, process_runner):
            github_client.comment_issue(
                issue.number,
                "## Agent Runner Review Blocked\n\n"
                "Read-only supervisor left uncommitted changes. "
                "Moving to blocked.",
            )
            transition_issue_workflow_state(
                github_client, issue.number, config, config.labels.blocked
            )
            return "blocked_dirty_read_only_supervisor"
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.review)
        return "approved_for_human_review"

    if action_result.action == "wait_for_checks":
        # PR checks are still pending; keep supervising and wait for a later
        # state change without consuming a repair attempt.
        # Stashed changes stay on the stash so the next cycle starts clean.
        _logger.info(
            "Issue #%d checks still pending; staying in %s.",
            issue.number,
            config.labels.supervising,
        )
        return "waiting_for_checks"

    if action_result.action in ("request_human_input",):
        if stashed:
            pop_worktree_stash(worktree_path, process_runner)
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.blocked)
        return "blocked_human_input"

    if action_result.action == "mark_failed":
        if stashed:
            pop_worktree_stash(worktree_path, process_runner)
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.failed)
        return "marked_failed"

    if action_result.action in (
        "repair_pr_branch",
        "rebase_pr_branch",
        "resolve_conflict",
    ):
        if stashed:
            pop_worktree_stash(worktree_path, process_runner)
        if action_result.action == "repair_pr_branch":
            # CI 修复受独立开关约束，纯代码审查修复只受共同轮数预算约束。
            from backend.core.use_cases.backlog_ci_delivery import (
                AutoRepairDecisionContext,
                enqueue_approved_auto_repair,
                gate_auto_repair_decision,
            )

            allowed, gate_reason = gate_auto_repair_decision(
                config=config,
                github_client=github_client,
                context=AutoRepairDecisionContext(
                    issue_number=issue.number,
                    pr_context=pr_context,
                    repair_scope=action_result.repair_scope,
                ),
            )
            if not allowed:
                _logger.info(
                    "Issue #%d auto CI repair blocked by policy: %s",
                    issue.number,
                    gate_reason,
                )
                return "ci_repair_policy_blocked"
            enqueue_approved_auto_repair(
                issue=issue,
                pr_context=pr_context,
                config=config,
                github_client=github_client,
                pr_branch=pr_branch,
            )
            return f"queued_{action_result.action}"
        head_sha = get_head_sha(worktree_path, process_runner)
        github_client.comment_issue(
            issue.number,
            build_rework_intent_comment(
                action=action_result.action,
                pr_branch=pr_branch,
                head_sha=head_sha,
            ),
        )
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.running)
        return f"queued_{action_result.action}"

    # Unknown action: block
    if stashed:
        pop_worktree_stash(worktree_path, process_runner)
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.blocked)
    return "blocked_unknown_action"


def review_once(
    *,
    repo_path: Path,
    config: AppConfig,
    dry_run: bool,
    agent: str,
    max_issues: int,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    run_history_store: IRunHistoryStore | None = None,
    repo_id: str | None = None,
) -> int:
    """Run one review polling pass.

    Args:
        repo_path: Target repository path.
        config: Application configuration.
        dry_run: If True, only list candidates without processing.
        agent: Agent override (auto, codex, claude).
        max_issues: Maximum issues to process.
        github_client: Client for interacting with GitHub.
        process_runner: Runner for executing subprocess commands.
        run_history_store: Optional PRD lifecycle side-channel store; ``None``
            keeps the previous behaviour with no lifecycle events.
        repo_id: 注册表仓库 id。用于**两件事**：生命周期事件的记录键，以及人工接管
            闸门（Issue #278）的查询键。记录键缺省回落到仓库目录名；闸门查询键
            **不**跟着回落——控制行按注册表 id 登记，用目录名去查会查不到行、把正在
            被人工接管的 Issue 放回监督与合并队列（FR-5）。调用方没给 repo_id 时，
            闸门按 Issue 编号兜底并 fail closed。

    Returns:
        Exit code (0 on success, 1 if any issue failed).
    """
    effective_repo_id = repo_id or repo_path.name
    candidates = github_client.list_review_candidate_issues(
        [config.labels.supervising, config.labels.review, LEGACY_REVIEW_LABEL], max_issues
    )
    if not candidates:
        _logger.info(
            "No open Issues found with labels %s or %s.",
            config.labels.supervising,
            config.labels.review,
        )
        return 0

    exit_code = 0
    for issue in candidates:
        if dry_run:
            _logger.info(
                "DRY RUN: would review Issue #%d: %s",
                issue.number,
                issue.title,
            )
            continue
        try:
            if LEGACY_REVIEW_LABEL in issue.labels:
                issue = github_client.get_issue(issue.number)
                if issue.state.upper() != "OPEN":
                    continue
            if LEGACY_REVIEW_LABEL in issue.labels:
                other_states = [
                    label
                    for label in workflow_state_labels(config)
                    if label not in {LEGACY_REVIEW_LABEL, config.labels.review}
                    and label in issue.labels
                ]
                if len(other_states) > 1:
                    _logger.warning(
                        "Issue #%d has legacy review plus conflicting workflow labels %s; "
                        "leaving it unchanged.",
                        issue.number,
                        other_states,
                    )
                    continue
                target_state = other_states[0] if other_states else config.labels.review
                transition_issue_workflow_state(github_client, issue.number, config, target_state)
                if target_state not in {config.labels.review, config.labels.supervising}:
                    continue
                issue = github_client.get_issue(issue.number)
            if config.labels.review in issue.labels:
                synchronize_human_review_label(github_client, issue, config)
            outcome = _process_review_candidate(
                issue=issue,
                repo_path=repo_path,
                config=config,
                agent=agent,
                github_client=github_client,
                process_runner=process_runner,
                run_history_store=run_history_store,
                repo_id=repo_id or "",
            )
            _logger.info(
                "Review outcome for Issue #%d: %s (%s)",
                issue.number,
                outcome,
                issue.title,
            )
            _record_review_outcome(
                run_history_store=run_history_store,
                repo_id=effective_repo_id,
                issue=issue,
                outcome=outcome,
                actor="review_once",
            )
        except Exception as exc:  # noqa: BLE001 - report queue failures and continue.
            exit_code = 1
            transition_issue_workflow_state(
                github_client, issue.number, config, config.labels.failed
            )
            github_client.comment_issue(
                issue.number,
                f"## Agent Runner Review Failed\n\n```text\n{exc}\n```\n",
            )
            _logger.error("Review failed for Issue #%d: %s", issue.number, exc)
            _record_review_outcome(
                run_history_store=run_history_store,
                repo_id=effective_repo_id,
                issue=issue,
                outcome="marked_failed",
                actor="review_once",
            )

    # Autopilot 启用时运行合并队列：全局 auto_merge 开启则沿用原双开关行为；
    # 否则队列只处理带单 PRD 显式授权标签的 Issue。
    if config.autopilot.enabled and not dry_run:
        try:
            merge_exit_code, merge_outcomes = process_merge_queue(
                repo_path=repo_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                supervisor_agent=agent,
                repo_id=repo_id,
            )
            if merge_exit_code != 0:
                exit_code = merge_exit_code
            _record_merge_outcomes(
                run_history_store=run_history_store,
                repo_id=effective_repo_id,
                outcomes=list(merge_outcomes),
                candidates_by_number={candidate.number: candidate for candidate in candidates},
            )
        except Exception as exc:  # noqa: BLE001 - merge queue failures must not crash review.
            _logger.error("Merge queue crashed: %s", exc)
            exit_code = 1

    return exit_code
