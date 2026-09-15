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
from backend.core.use_cases.agent_runner_git import (
    get_current_branch,
    has_changes,
)
from backend.core.use_cases.agent_runner_publication import (
    _finish_existing_commit_publication,
    _finish_implementation_publication,
    _reuse_existing_local_commit,
)
from backend.core.use_cases.agent_runner_reclaim import format_claim_marker
from backend.core.use_cases.agent_runner_rework import build_missing_worktree_comment
from backend.core.use_cases.agent_runner_supervisor import _run_supervisor_with_repair_loop
from backend.core.use_cases.agent_runner_validation import (
    ValidationEvidenceError,
    publish_validation_evidence_best_effort,
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
from backend.core.use_cases.run_agent_once import (
    choose_agent,
    create_or_reuse_worktree,
    get_head_sha,
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
    except (MaxRetriesExceededError, ProviderCapacityError, KeyboardInterrupt):
        # 切换 agent 前、或被 Ctrl-C / SIGINT 优雅打断时,先把在途进度 checkpoint：
        # 让 fallback 链上的下一个 agent、或重新 claim 时能在已提交进度上续作,而不是
        # 从零重来。KeyboardInterrupt 同样 checkpoint 后再抛出,让中断照常退出。
        # best-effort：checkpoint 自身异常不得掩盖原始失败/中断（禁改路径已被
        # checkpoint 内部隔离,不再整块放弃）。
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
            supervisor_agent=supervisor_agent,
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
