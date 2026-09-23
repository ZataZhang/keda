"""Issue 状态处理器：按 Issue 所处状态分派到对应的处理路径。

本模块承接原先落在 ``agent_runner_orchestrate.py`` 的四个分派处理器：

- :func:`_process_ready_issue` —— 新 Issue → 完整 Agent 执行 → 评审 → 发布
- :func:`_process_running_rework` —— 已运行 Issue → 检出 rework 标记 → 修复 → 评审
- :func:`_process_running_publish_recovery` —— 已运行 Issue → 有本地 commit → 直接评审 → 发布
- :func:`_process_blocked_resolution` —— ``agent/blocked`` Issue → 在既有 worktree 上继续解阻

调用方是 :mod:`backend.core.use_cases.agent_runner_orchestration_runtime` 的
派发层；它按名字从 ``agent_runner_orchestrate`` 的命名空间取这些处理器与协作者
（见 :data:`RUNTIME_DEPENDENCY_NAMES` 与
``agent_runner_orchestrate._orchestration_runtime_module``），因此本模块的
运行时依赖保持可替换：orchestrate 在每次派发前把它们同步过来。
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AttemptResult,
    CommandResult,
    IssueSummary,
    ReviewEventMarker,
)
from backend.core.use_cases.agent_runner_blocked_claim import (
    _acquire_blocked_claim_lock,
    _release_blocked_claim_lock,
    worktree_claim_lock_path,
)
from backend.core.use_cases.agent_runner_events import (
    find_latest_failure_context_comment,
    has_failure_context_marker,
)
from backend.core.use_cases.agent_runner_failure import (
    MaxRetriesExceededError,
    format_failure_context_comment,
)
from backend.core.use_cases.agent_runner_git import (
    get_current_branch,
    has_changes,
)
from backend.core.use_cases.agent_runner_publication import (
    _finish_existing_commit_publication,
    _finish_implementation_publication,
    _reuse_existing_local_commit,
)
from backend.core.use_cases.agent_runner_publish import publish_changes
from backend.core.use_cases.agent_runner_reclaim import format_claim_marker
from backend.core.use_cases.agent_runner_rework import build_missing_worktree_comment
from backend.core.use_cases.agent_runner_supervisor import _run_supervisor_with_repair_loop
from backend.core.use_cases.agent_runner_validation import (
    ValidationEvidenceError,
    publish_validation_evidence_best_effort,
    resolve_issue_evidence_relpath,
)
from backend.core.use_cases.agent_runner_workflow import (
    find_latest_unconsumed_marker,
    transition_issue_workflow_state,
)
from backend.core.use_cases.agent_runner_worktree_branch import _ensure_worktree_branch
from backend.core.use_cases.agent_runner_worktree_probe import _find_worktree_path_for_issue
from backend.core.use_cases.pr_supervisor import (
    build_rebase_repair_complete_comment,
    execute_rebase,
    execute_repair,
)
from backend.core.use_cases.pr_supervisor_findings import _load_previous_findings
from backend.core.use_cases.run_agent_once import (
    choose_agent,
    create_or_reuse_worktree,
    get_head_sha,
    resolve_repair_agent,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "_guard_blocked_issue_has_resolution",
    "_process_blocked_resolution",
    "_process_ready_issue",
    "_process_running_publish_recovery",
    "_process_running_rework",
]

# 在被派发前由 ``agent_runner_orchestrate._orchestration_runtime_module``
# 从 orchestrate 的命名空间同步过来的执行侧协作者：git 读取、worktree 供给与分支
# 对齐、agent 选择、发布与 blocked claim。测试与集成方仍在
# ``agent_runner_orchestrate`` 上替换它们（与
# ``agent_runner_orchestration_runtime.RUNTIME_DEPENDENCY_NAMES`` 同一套机制，
# 保持单一补丁中枢，避免同一处替换要打两个模块）。
RUNTIME_DEPENDENCY_NAMES = (
    "_acquire_blocked_claim_lock",
    "_ensure_worktree_branch",
    "_find_worktree_path_for_issue",
    "_finish_existing_commit_publication",
    "_finish_implementation_publication",
    "_release_blocked_claim_lock",
    "_reuse_existing_local_commit",
    "_run_supervisor_with_repair_loop",
    "choose_agent",
    "create_or_reuse_worktree",
    "execute_rebase",
    "get_current_branch",
    "get_head_sha",
)


_BLOCKED_RESOLUTION_COMPLETION_PHASES = {
    "implementation_complete",
    "draft_pr_created",
    "publish_recovered",
    "rebase_repair_complete",
    "blocked_resolution_complete",
}


def _guard_blocked_issue_has_resolution(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> ReviewEventMarker | None:
    """检测 blocked Issue 是否包含未消费的 blocked_resolution_requested marker。

    Args:
        issue: Issue 对象
        github_client: GitHub 客户端

    Returns:
        未消费的 blocked_resolution marker，或 None
    """
    comments = github_client.list_issue_comments(issue.number)
    return find_latest_unconsumed_marker(
        comments,
        phase="blocked_resolution_requested",
        completion_phases=_BLOCKED_RESOLUTION_COMPLETION_PHASES,
    )


def _process_blocked_resolution(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    marker: ReviewEventMarker,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
) -> None:
    """处理带 blocked_resolution marker 的 blocked Issue。

    在现有 worktree 上发送 continuation prompt，让 Agent 继续完成剩余任务。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
        marker: blocked_resolution_requested 事件标记
    """
    from backend.core.use_cases.agent_runner_publish import validate_safe_changes
    from backend.core.use_cases.run_agent_once import (
        build_blocked_continuation_prompt,
        run_agent_until_committed,
    )

    selected_agent = choose_agent(issue, config, agent)

    # 定位 worktree 并确认分支
    worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    expected_branch = f"issue-{issue.number}"
    _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)
    current_branch = get_current_branch(worktree_path, process_runner)
    if current_branch != expected_branch:
        raise RuntimeError(
            f"Blocked resolution aborted: on branch {current_branch}, expected {expected_branch}"
        )

    # worktree 必须是 clean 的
    if has_changes(worktree_path, process_runner):
        raise RuntimeError(
            "Blocked resolution aborted: worktree has uncommitted changes. "
            "Please commit or stash them before continuing."
        )

    # 再次检查无 forbidden paths
    validate_safe_changes(worktree_path, config, process_runner)

    # 原子锁：防止多个 runner 同时处理同一个 blocked Issue 的 worktree
    lock_path = worktree_claim_lock_path(worktree_path)
    _acquire_blocked_claim_lock(lock_path, issue.number)
    try:
        # 构建并发送 continuation prompt
        continuation_prompt = build_blocked_continuation_prompt(
            issue, worktree_path, marker.blocked_paths
        )
        before_sha = get_head_sha(worktree_path, process_runner)

        commit_result = run_agent_until_committed(
            selected_agent=selected_agent,
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            before_sha=before_sha,
            expected_branch=current_branch,
            prompt_override=continuation_prompt,
            on_attempt_recorded=on_attempt_recorded,
        )

        # 完成发布流程
        _finish_implementation_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=current_branch,
            commit_result=commit_result,
            content_generator=content_generator,
        )
    finally:
        _release_blocked_claim_lock(lock_path)


# Draft PR 正文里指向交接评论的那一段的锚点。有了它，重复耗尽时改写的是同一段而
# 不是越叠越长；也让"正文只是快照、评论才是事实源"这条边界在 PR 上写明。
_PR_HANDOFF_REF_MARKER = "<!-- iar:failure-context-ref -->"


def _build_pr_handoff_ref_block(
    handoff_url: str,
    *,
    attempt_count: int,
    handoff_comment_posted: bool,
) -> str:
    """构造 Draft PR 正文尾部固定附上的交接回链段。

    这一段只是**指向**交接评论的快照说明，不复述结论：交接评论是唯一事实源，PR
    正文可能落后于它。它也不改变任何状态——能不能签核、合并、归档仍由既有门禁按
    ``validation/verifier-passed`` 与当前 tree 的证据判定。

    ``handoff_comment_posted`` 为 ``False`` 时（评论写入自身失败）不能再把它写成
    "权威交接记录"，否则 PR 会指向一条根本不存在的评论；此时退化为指向 Issue 的
    评论列表，措辞如实降级。
    """
    record_line = (
        f"- Authoritative handoff record (latest round): <{handoff_url}>"
        if handoff_comment_posted
        else (
            "- Handoff record could not be posted to this Issue; see the Issue "
            f"comment list for whatever the runner managed to report: <{handoff_url}>"
        )
    )
    return "\n".join(
        [
            _PR_HANDOFF_REF_MARKER,
            "",
            "## Previous Round Handoff",
            "",
            f"This Draft PR was published from a **WIP checkpoint** after the runner "
            f"exhausted its recovery budget ({attempt_count} attempts). The commits "
            "here are mid-progress work, not a finished implementation, and the "
            "existence of this PR is not an acceptance result.",
            "",
            record_line,
            "- `validation/verifier-passed` is **absent**, so the existing sign-off, "
            "merge and archive gates keep refusing this PR by design. Nothing in this "
            "section changes that, and the gates are not modified by this feature.",
            "",
        ]
    )


def _with_pr_handoff_ref(existing_pr_body: str, handoff_ref_block: str) -> str:
    """把交接回链段固定附到 PR 正文尾部，重复写入时替换而非追加。"""
    body_without_previous_ref = existing_pr_body
    if _PR_HANDOFF_REF_MARKER in existing_pr_body:
        body_without_previous_ref = existing_pr_body.split(_PR_HANDOFF_REF_MARKER, 1)[0]
    stripped_body = body_without_previous_ref.rstrip()
    if not stripped_body:
        return handoff_ref_block
    return f"{stripped_body}\n\n{handoff_ref_block}"


def _resolve_handoff_comment_url(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> str:
    """定位刚写下的交接评论并返回其 permalink。

    端口上的 :meth:`IGitHubClient.comment_issue` 不返回评论 ID，因此回读评论列表按
    marker 找最近一条。读不到时退回 Issue URL——回链的目的是让人顺路找到最新结论，
    落到 Issue 上仍然找得到，不能因为拿不到 permalink 就放弃发布。
    """
    try:
        comment_entries = github_client.list_issue_comment_entries(issue.number)
    except Exception as entries_exc:  # noqa: BLE001 - 回链尽力而为，不阻断发布
        _logger.warning(
            "Cannot resolve handoff comment permalink for Issue #%d: %s",
            issue.number,
            entries_exc,
        )
        return issue.url
    for comment_id, comment_body in reversed(comment_entries):
        if comment_id and has_failure_context_marker(comment_body):
            return f"{issue.url}#issuecomment-{comment_id}"
    return issue.url


def _attach_handoff_ref_to_draft_pr(
    *,
    issue: IssueSummary,
    github_client: IGitHubClient,
    branch: str,
    handoff_url: str,
    attempt_count: int,
    handoff_comment_posted: bool,
) -> None:
    """把交接回链固定附到该分支的 PR 正文尾部。

    内容生成被关闭（回落到 ``fallback_body``）或复用既有 PR 未改写正文时，
    ``create_draft_pr`` 都不会带上这段上下文，因此回链在发布之后单独补一次，
    使 FR-6 的"无论哪种形态人都能顺链找到最新结论"成立。
    """
    pr_context = github_client.get_pull_request_context(branch)
    if pr_context is None or pr_context.number is None:
        _logger.warning(
            "Skipping handoff back-link on the Draft PR for Issue #%d: "
            "PR context for branch %s is unavailable.",
            issue.number,
            branch,
        )
        return
    github_client.update_pull_request_body(
        pr_context.number,
        _with_pr_handoff_ref(
            pr_context.body,
            _build_pr_handoff_ref_block(
                handoff_url,
                attempt_count=attempt_count,
                handoff_comment_posted=handoff_comment_posted,
            ),
        ),
    )


def _read_previous_failure_context(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> str:
    """按 latest-wins 取上一轮的交接记录正文，供续作 prompt 回灌。

    只取最近一条、不取全部评论：Issue 历史里既有噪音也有早已修复的旧失败，注入越多
    下一轮越可能被过期信息带偏。读不到或解析不出时返回空串（fail-closed 到"没有上一
    轮上下文"），既不阻断续作也不伪造上下文。
    """
    try:
        comment_bodies = github_client.list_issue_comments(issue.number)
    except Exception as comments_exc:  # noqa: BLE001 - 缺上下文照常续作
        _logger.warning(
            "Cannot read Issue #%d comments for failure-context reflow: %s",
            issue.number,
            comments_exc,
        )
        return ""
    return find_latest_failure_context_comment(comment_bodies) or ""


def _record_failure_handoff(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    failure_exc: MaxRetriesExceededError,
    checkpoint_sha: str | None,
    expected_branch: str,
    content_generator: IContentGenerator | None,
) -> None:
    """recovery 耗尽时写交接记录，并在存在安全 commit 时发布同源的 Draft PR。

    处理顺序是刻意排定的：先由调用方 checkpoint、再写交接记录（**无论有没有快照**，
    因为"无 commit 也要交班"是必须覆盖的一格）、有快照才发布 PR。

    整层 best-effort：**包括渲染本身在内的任何一步抛错都只记日志、绝不上抛**。交接
    写挂了不能把"验证没过"变成"报告写挂了"，原始 ``MaxRetriesExceededError`` 必须
    照常由调用方抛出。渲染之所以也要包进来，是因为它并不比评论调用更安全：
    ``attempt_results`` 的形状、证据路径解析、以及 :func:`format_failure_context_comment`
    里对 verifier verdict 的解析都可能抛；只包评论会留下这条缺口。
    """
    try:
        _write_failure_handoff(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            failure_exc=failure_exc,
            checkpoint_sha=checkpoint_sha,
            expected_branch=expected_branch,
            content_generator=content_generator,
        )
    except Exception as handoff_exc:  # noqa: BLE001 - 交接失败绝不掩盖原始失败
        _logger.error(
            "Failed to record the failure handoff for Issue #%d: %s "
            "(the original recovery exhaustion is still being raised).",
            issue.number,
            handoff_exc,
        )


def _write_failure_handoff(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    failure_exc: MaxRetriesExceededError,
    checkpoint_sha: str | None,
    expected_branch: str,
    content_generator: IContentGenerator | None,
) -> None:
    """交接记录与失败 Draft PR 的实际写入步骤，由 :func:`_record_failure_handoff` 包护。

    评论 / 发布 / 回链三步各自吞异常，好让"评论写挂了但仍然发布了 PR"这类部分成功
    如实留在日志里；渲染与快照分支留在外层守卫内。
    """
    attempt_results = list(failure_exc.attempt_results)
    handoff_body = format_failure_context_comment(
        failure_exc,
        attempt_results,
        issue_number=issue.number,
        checkpoint_sha=checkpoint_sha,
        evidence_dir=resolve_issue_evidence_relpath(config, issue),
    )
    # 评论没写成功时不能继续宣称"权威交接记录"，否则 PR 正文会指向一条不存在的评论。
    handoff_url = issue.url
    handoff_comment_posted = False
    try:
        github_client.comment_issue(issue.number, handoff_body)
    except Exception as comment_exc:  # noqa: BLE001 - 保留原始失败
        _logger.error(
            "Failed to write the failure handoff comment on Issue #%d: %s",
            issue.number,
            comment_exc,
        )
    else:
        handoff_comment_posted = True
        handoff_url = _resolve_handoff_comment_url(issue, github_client)

    if checkpoint_sha is None:
        _logger.info(
            "Issue #%d exhausted recovery without a safely publishable commit; "
            "handoff record written, no Draft PR published.",
            issue.number,
        )
        return

    # 本 PRD 唯一放宽的安全检查是 require_prd_archived=False，且只在这条耗尽路径上
    # 显式传入。注意此刻在途改动已被 checkpoint 提交，push 侧的 forbidden/evidence
    # 检查按未提交状态取变更集、对已提交内容实际是空转；真正的禁改路径隔离发生在
    # checkpoint 的 staging（checkpoint_uncommitted_progress 显式排除禁改路径）。放宽
    # 的只有归档这一项，其余检查调用照旧执行。
    try:
        branch, _pr_url = publish_changes(
            issue,
            worktree_path,
            config,
            github_client,
            process_runner,
            expected_branch=expected_branch,
            content_generator=content_generator,
            require_prd_archived=False,
        )
    except Exception as publish_exc:  # noqa: BLE001 - 发布失败不掩盖原始失败
        _logger.error(
            "Failed to publish the failure Draft PR for Issue #%d: %s",
            issue.number,
            publish_exc,
        )
        return

    try:
        _attach_handoff_ref_to_draft_pr(
            issue=issue,
            github_client=github_client,
            branch=branch,
            handoff_url=handoff_url,
            attempt_count=len(attempt_results),
            handoff_comment_posted=handoff_comment_posted,
        )
    except Exception as ref_exc:  # noqa: BLE001 - 回链失败不掩盖原始失败
        _logger.error(
            "Failed to attach the handoff back-link to the Draft PR for Issue #%d: %s",
            issue.number,
            ref_exc,
        )


def _process_ready_issue(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
) -> None:
    """处理 ready 状态的 Issue（完整实现路径）。

    ready Issue 是新 claim 的 Issue，需要完整处理：
    1. 标记为 running 并评论声明
    2. 创建或复用 worktree
    3. 检查是否有已存在的本地 commit（恢复路径）
    4. 如无本地 commit 则运行 Agent 实现
    5. 完成发布流程

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖（auto/codex/claude）
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
    """
    from backend.core.use_cases.run_agent_once import (
        MaxRetriesExceededError,
        PrdDeliveryError,
        ProviderCapacityError,
        VerificationFailedError,
        build_progress_continuation_prompt,
        checkpoint_uncommitted_progress,
        run_agent_until_committed,
    )

    selected_agent = choose_agent(issue, config, agent)

    # 步骤 1: 声明 Issue
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.running)
    claim_host = socket.gethostname()
    claim_pid = os.getpid()
    claim_started_at = datetime.now(timezone.utc)
    github_client.comment_issue(
        issue.number,
        "## Agent Runner Claimed\n\n"
        f"- Host: `{claim_host}`\n"
        f"- PID: `{claim_pid}`\n"
        f"- Agent: `{selected_agent}`\n"
        f"- Started at: `{claim_started_at.isoformat()}`\n\n"
        f"{format_claim_marker(claim_host, claim_pid, started_at=claim_started_at)}",
    )

    # 步骤 2: 准备 worktree
    worktree_path = create_or_reuse_worktree(repo_path, issue, config, process_runner)
    before_sha = get_head_sha(worktree_path, process_runner)
    expected_branch = get_current_branch(worktree_path, process_runner)

    # 步骤 3: 检查恢复路径
    #
    # 已有本地提交分三种情况：
    # - 完全达到交付标准 → 直接发布，不调用 agent。
    # - 存在提交但门禁未过（上一次 claim 的 WIP checkpoint / 部分进度）→ 不硬失败，
    #   在已提交进度上重跑 agent 续作（continuation prompt）。
    # - 无本地提交 → 全新实现。
    continuation_prompt: str | None = None
    try:
        commit_result = _reuse_existing_local_commit(issue, worktree_path, config, process_runner)
    except (VerificationFailedError, PrdDeliveryError, ValidationEvidenceError) as exc:
        _logger.info(
            "Issue #%d has partial local commits not yet delivery-ready (%s); "
            "re-running agent to continue from committed progress.",
            issue.number,
            exc.__class__.__name__,
        )
        commit_result = None
        verification_results: list[CommandResult] | None = None
        failure_summary = str(exc)
        if isinstance(exc, VerificationFailedError):
            verification_results = exc.verification_results
        continuation_prompt = build_progress_continuation_prompt(
            issue,
            worktree_path,
            failure_summary=failure_summary,
            verification_results=verification_results,
            previous_failure_context=_read_previous_failure_context(issue, github_client),
        )

    if commit_result is not None:
        # 有已存在的本地 commit 且已达交付标准 → 恢复路径
        _finish_existing_commit_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=expected_branch,
            commit_result=commit_result,
            content_generator=content_generator,
        )
        return

    # 步骤 4: 无可发布的本地 commit → Agent 执行（首跑，或在 checkpoint 上续作）。
    #
    # 失败前把 agent 的在途进度提交成 WIP checkpoint，使其能被下一次 claim 复用、
    # 继续推进；否则体量较大的 PRD 会在每次 claim 从零开始、永远收敛不了。
    try:
        new_commit_result = run_agent_until_committed(
            selected_agent=selected_agent,
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            before_sha=before_sha,
            expected_branch=expected_branch,
            prompt_override=continuation_prompt,
            on_attempt_recorded=on_attempt_recorded,
        )
    except (
        MaxRetriesExceededError,
        ProviderCapacityError,
        KeyboardInterrupt,
    ) as in_flight_failure_exc:
        # 切换 agent 前、或被 Ctrl-C / SIGINT 优雅打断时,先把在途进度 checkpoint：
        # 让 fallback 链上的下一个 agent、或重新 claim 时能在已提交进度上续作,而不是
        # 从零重来。KeyboardInterrupt 同样 checkpoint 后再抛出,让中断照常退出。
        # best-effort：checkpoint 自身异常不得掩盖原始失败/中断（禁改路径已被
        # checkpoint 内部隔离,不再整块放弃）。
        checkpoint_sha: str | None = None
        try:
            checkpoint_sha = checkpoint_uncommitted_progress(
                issue,
                worktree_path,
                config,
                process_runner,
                expected_branch=expected_branch,
            )
        except Exception as checkpoint_exc:  # noqa: BLE001 - 不能掩盖原始失败
            _logger.warning(
                "Failed to checkpoint in-progress work for Issue #%d: %s",
                issue.number,
                checkpoint_exc,
            )
        else:
            if checkpoint_sha is not None:
                _logger.info(
                    "Checkpointed in-progress work for Issue #%d at %s "
                    "for the next claim to continue.",
                    issue.number,
                    checkpoint_sha,
                )

        # 交接只属于"这一轮没通过验收"，所以显式收窄到 MaxRetriesExceededError：
        # ProviderCapacityError 是限流/容量，不代表本轮工作未通过；KeyboardInterrupt
        # 是用户主动中断，且它根本没有 attempt_results。挂在整个 except 元组上会让
        # 一次 Ctrl-C 发出一条假的失败结论、甚至一个 Draft PR，并因访问
        # attempt_results 抛 AttributeError 把用户的中断变成一条假故障。
        if isinstance(in_flight_failure_exc, MaxRetriesExceededError):
            _record_failure_handoff(
                issue=issue,
                worktree_path=worktree_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                failure_exc=in_flight_failure_exc,
                checkpoint_sha=checkpoint_sha,
                expected_branch=expected_branch,
                content_generator=content_generator,
            )
        raise

    # 步骤 5: 完成发布流程
    _finish_implementation_publication(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        selected_agent=selected_agent,
        github_client=github_client,
        process_runner=process_runner,
        expected_branch=expected_branch,
        commit_result=new_commit_result,
        content_generator=content_generator,
    )


def _process_running_rework(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    marker: ReviewEventMarker,
    **kwargs: object,
) -> None:
    """处理带 rework 标记的 running Issue。

    当 Issue 有 post_pr_rework_requested 事件标记时进入此路径。
    监督者之前已请求修复，现在执行修复操作。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        github_client: GitHub 客户端
        process_runner: 进程运行器
        marker: 事件标记（包含动作类型和分支信息）
    """
    pr_branch = marker.pr_branch
    if pr_branch is None:
        raise RuntimeError("Rework marker missing pr_branch")

    # 定位 worktree；缺失时进入 blocked 并给出可操作的恢复说明。
    try:
        worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    except FileNotFoundError as exc:
        message = str(exc)
        prefix = "(path_command output): "
        suffix = ". path_command return_code="
        expected_path = message
        if prefix in message and suffix in message:
            expected_path = message.split(prefix, 1)[1].split(suffix, 1)[0]
        github_client.comment_issue(
            issue.number,
            build_missing_worktree_comment(
                issue=issue,
                pr_branch=pr_branch,
                expected_path=expected_path,
            ),
        )
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.blocked)
        return

    # worktree 可能因上一次 runner 在 rebase 中途中断而停在 detached HEAD；
    # 先治愈回目标分支再校验，避免对中断状态直接硬失败、把 Issue 打成 failed。
    _ensure_worktree_branch(worktree_path, pr_branch, issue, config, process_runner)

    current_branch = get_current_branch(worktree_path, process_runner)
    if current_branch != pr_branch:
        raise RuntimeError(f"Rework aborted: on branch {current_branch}, expected {pr_branch}")

    expected_head = marker.head_sha or get_head_sha(worktree_path, process_runner)
    action = marker.action or "repair_pr_branch"
    supervisor_agent = choose_agent(issue, config, agent)
    # rework 路径拿不到本次实现者，``repair_agent='executor'`` 会按 Issue 标签
    # 回落（解析器内部记录来源）；findings 取跨 cycle 落盘的未解决清单。
    repair_agent = resolve_repair_agent(
        config.post_pr_supervisor.repair_agent,
        issue=issue,
        config=config,
        reviewing_agent=supervisor_agent,
    )

    # 执行修复或 rebase
    if action == "rebase_pr_branch":
        verification_results = execute_rebase(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            pr_branch=pr_branch,
            expected_head=expected_head,
            supervisor_agent=supervisor_agent,
        )
        rebase_sha = get_head_sha(worktree_path, process_runner)
        github_client.comment_issue(
            issue.number,
            build_rebase_repair_complete_comment(
                action=action,
                head_sha=rebase_sha,
                verification_passed=all(result.return_code == 0 for result in verification_results),
            ),
        )
    else:
        verification_results = execute_repair(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            pr_branch=pr_branch,
            expected_head=expected_head,
            repair_agent=repair_agent,
            findings=_load_previous_findings(worktree_path, config, issue.number),
        )
        repair_sha = get_head_sha(worktree_path, process_runner)
        github_client.comment_issue(
            issue.number,
            build_rebase_repair_complete_comment(
                action=action,
                head_sha=repair_sha,
                verification_passed=all(result.return_code == 0 for result in verification_results),
            ),
        )

    # 修复后刷新验证证据：新 head 需要新证据与新一轮人工签收
    # best-effort：见 publish_validation_evidence_best_effort docstring。
    rework_pr_url = github_client.find_open_pr_by_head(pr_branch)
    if rework_pr_url is not None:
        publish_validation_evidence_best_effort(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            pr_url=rework_pr_url,
            head_sha=get_head_sha(worktree_path, process_runner),
        )

    # 标记为 supervising 并获取 PR 上下文
    transition_issue_workflow_state(github_client, issue.number, config, config.labels.supervising)

    # 修复后再次运行监督循环
    if config.post_pr_supervisor.enabled:
        pr_context = github_client.get_pull_request_context(pr_branch)
        if pr_context is None:
            _logger.warning(
                "Deferring post-rework supervisor for Issue #%d branch %s: "
                "complete PR context is unavailable.",
                issue.number,
                pr_branch,
            )
            return
        _run_supervisor_with_repair_loop(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            pr_context=pr_context,
            supervisor_agent=supervisor_agent,
        )
    else:
        transition_issue_workflow_state(github_client, issue.number, config, config.labels.review)


def _process_running_publish_recovery(
    *,
    issue: IssueSummary,
    repo_path: Path,
    config: AppConfig,
    agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    **kwargs: object,
) -> None:
    """恢复 running Issue 的发布流程。

    用于 runner 重启后发现 running Issue 已有本地 commit 的情况。
    通过复用已有 commit 来完成发布，无需重新运行 Agent。

    Args:
        issue: Issue 对象
        repo_path: 仓库根目录
        config: 应用配置
        agent: Agent 覆盖
        github_client: GitHub 客户端
        process_runner: 进程运行器
        content_generator: 可选的 AI 内容生成器
    """
    selected_agent = choose_agent(issue, config, agent)

    # 定位 worktree 并确认分支
    worktree_path = _find_worktree_path_for_issue(repo_path, issue, config, process_runner)
    expected_branch = f"issue-{issue.number}"

    # 原子锁：恢复路径会对 worktree 做 rebase 治愈与发布等写操作，必须与其他
    # runner（含 blocked 恢复）在同一 worktree 上互斥，否则并发 git 操作会互相
    # 破坏一个本就脆弱的 mid-rebase 工作区。锁被活进程持有时抛
    # BlockedWorktreeClaimedError，由 run_once 调度循环记日志后跳过。
    lock_path = worktree_claim_lock_path(worktree_path)
    _acquire_blocked_claim_lock(lock_path, issue.number)
    try:
        _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)

        # 检查是否有可复用的本地 commit
        commit_result = _reuse_existing_local_commit(issue, worktree_path, config, process_runner)
        if commit_result is None:
            raise RuntimeError(
                f"Issue #{issue.number} has no clean local commit ready for publication."
            )

        # 完成发布流程（恢复路径）
        _finish_existing_commit_publication(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            selected_agent=selected_agent,
            github_client=github_client,
            process_runner=process_runner,
            expected_branch=expected_branch,
            commit_result=commit_result,
            content_generator=content_generator,
        )
    finally:
        _release_blocked_claim_lock(lock_path)
