"""Agent Runner 发布流程。

本模块包含 Issue 实现完成后的发布逻辑，包括：
- 评论构建函数
- 发布完成处理（新实现路径和恢复路径）
- 本地 commit 复用逻辑

主要函数：
- `build_implementation_complete_comment`: 构建实现完成评论
- `build_draft_pr_created_comment`: 构建 Draft PR 创建评论
- `_reuse_existing_local_commit`: 复用已有的本地 commit
- `_finish_implementation_publication`: 完成新实现的发布流程
- `_finish_existing_commit_publication`: 完成已存在 commit 的恢复发布流程
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.core.agent.memory import (
    distill_skill,
    find_similar_draft,
    promote_draft_to_skills,
    save_skill_draft,
    should_auto_promote,
)
from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    CommandResult,
    FailureType,
    IssueSummary,
    PublishFailureCategory,
)
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_review import run_pre_pr_review
from backend.core.use_cases.agent_runner_final_verification import (
    FinalVerificationRequest,
    ensure_final_verifier_verdict,
)
from backend.core.use_cases.agent_runner_direct_pr_round import complete_direct_pr_round
from backend.core.use_cases.agent_runner_direct_pr_round import DirectPrPublicationCandidate
from backend.core.use_cases.agent_runner_direct_pr_label import (
    consume_label_after_publication,
    DirectPrLabelPublicationRequest,
)
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.agent_runner_failure import PublishFailureError
from backend.core.use_cases.agent_runner_publish import (
    DraftPRCreationError,
    create_draft_pr,
    push_changes,
)
from backend.core.use_cases.agent_runner_validation import (
    ensure_validation_evidence_ready,
    publish_validation_evidence_best_effort,
)
from backend.core.use_cases.agent_runner_workflow import workflow_state_labels
from backend.core.use_cases.run_agent_once import (
    ensure_prd_delivery_ready,
    ensure_verification_passed,
    format_attempt_history,
    get_head_sha,
    has_changes,
    resolve_supervisor_agent,
    run_verification,
)
from backend.core.use_cases.run_verifier_agent import (
    ValidationVerdict,
    apply_verifier_verdict_to_pr,
)

_logger = logging.getLogger(__name__)


def build_implementation_complete_comment(
    *,
    agent: str,
    branch: str,
    head_sha: str,
    verification_results: list[CommandResult],
    attempt_results: list[AttemptResult] | None = None,
) -> str:
    """构建 Agent 实现完成后的 Issue 评论。

    Args:
        agent: 执行的 AI Agent 名称（codex/claude/auto）
        branch: 功能分支名
        head_sha: 实现完成后的 HEAD commit SHA
        verification_results: 验证命令的执行结果列表
        attempt_results: Agent 尝试历史（用于恢复路径显示）

    Returns:
        格式化的 Markdown 评论文本，包含事件标记和验证结果摘要
    """

    marker = format_event_marker(
        phase="implementation_complete",
        cycle=1,
        head_sha=head_sha,
    )
    verification_lines = "\n".join(
        f"- `{' '.join(result.command)}`: exit {result.return_code}"
        for result in verification_results
    )
    lines = [
        marker,
        "",
        "## Agent Runner Implementation Complete",
        "",
        f"- Agent: `{agent}`",
        f"- Branch: `{branch}`",
        f"- Head SHA: `{head_sha}`",
        "",
        "Verification:",
        verification_lines,
    ]
    if attempt_results:
        lines.append("")
        lines.append(format_attempt_history(attempt_results))
    return "\n".join(lines)


def build_draft_pr_created_comment(
    *,
    pr_url: str,
    branch: str,
    head_sha: str,
) -> str:
    """构建 Draft PR 创建后的 Issue 评论。

    Args:
        pr_url: 生成的 Draft PR 链接
        branch: 功能分支名
        head_sha: PR 头部的 commit SHA

    Returns:
        格式化的 Markdown 评论文本
    """
    marker = format_event_marker(
        phase="draft_pr_created",
        cycle=1,
        head_sha=head_sha,
        pr_branch=branch,
    )
    return "\n".join(
        [
            marker,
            "",
            "## Agent Runner Draft PR Created",
            "",
            f"- Branch: `{branch}`",
            f"- Draft PR: {pr_url}",
            f"- Head SHA: `{head_sha}`",
        ]
    )


def _push_changes_with_recovery_context(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    expected_branch: str,
) -> str:
    """Push the branch and preserve recovery context on push failures.

    Used both for the initial post-implementation push and for reviewer
    patches during the pre-PR review loop. Reviewer patches call this via a
    closure that pins the same ``expected_branch`` and configuration.
    """
    try:
        return push_changes(
            issue,
            worktree_path,
            config,
            process_runner,
            expected_branch=expected_branch,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise PublishFailureError(
            str(exc),
            worktree_path=worktree_path,
            failure_category=PublishFailureCategory.PUSH,
        ) from exc


def _create_draft_pr_with_recovery_context(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    expected_branch: str,
    content_generator: IContentGenerator | None,
    publish_stage: PublishStage = PublishStage.NORMAL,
) -> tuple[str, str]:
    """Create the draft PR (or reuse an existing one) and preserve context.

    Only invoked once the pre-PR review gate has converged, so any failure
    here is a PR-creation problem and is reported as ``PR_CREATE``.
    """
    try:
        return create_draft_pr(
            issue,
            worktree_path,
            config,
            github_client,
            process_runner,
            expected_branch=expected_branch,
            content_generator=content_generator,
            publish_stage=publish_stage,
        )
    except DraftPRCreationError as exc:
        raise PublishFailureError(
            str(exc),
            worktree_path=worktree_path,
            failure_category=PublishFailureCategory.PR_CREATE,
        ) from exc
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise PublishFailureError(
            str(exc),
            worktree_path=worktree_path,
            failure_category=PublishFailureCategory.PR_CREATE,
        ) from exc


def _make_push_callback(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    expected_branch: str,
) -> Callable[[], None]:
    """Build the push callback used by :func:`run_pre_pr_review`.

    The closure is intentionally parameterless so the review loop can call it
    after every successful reviewer commit without exposing publication
    internals. On push failure the callback raises :class:`PublishFailureError`
    so the runner exits the review cycle with a recoverable context.
    """

    def _push() -> None:
        _push_changes_with_recovery_context(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            process_runner=process_runner,
            expected_branch=expected_branch,
        )

    return _push


def _edit_issue_labels_after_publish(
    *,
    issue_number: int,
    add_labels: list[str],
    remove_labels: list[str],
    worktree_path: Path,
    github_client: IGitHubClient,
) -> None:
    """Update labels and preserve recovery context on GitHub failures."""
    try:
        github_client.edit_issue_labels(
            issue_number,
            add=add_labels,
            remove=remove_labels,
        )
    except Exception as exc:  # noqa: BLE001 - surface category for recovery.
        raise PublishFailureError(
            f"Failed to update labels after publish: {exc}",
            worktree_path=worktree_path,
            failure_category=PublishFailureCategory.LABEL_UPDATE,
        ) from exc


def _comment_issue_after_publish(
    *,
    issue_number: int,
    comment_body: str,
    worktree_path: Path,
    github_client: IGitHubClient,
) -> None:
    """Post a publish comment and preserve recovery context on GitHub failures."""
    try:
        github_client.comment_issue(issue_number, comment_body)
    except Exception as exc:  # noqa: BLE001 - surface category for recovery.
        raise PublishFailureError(
            f"Failed to post draft PR comment: {exc}",
            worktree_path=worktree_path,
            failure_category=PublishFailureCategory.COMMENT_UPDATE,
        ) from exc


def _publish_validation_evidence_after_pr(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    pr_url: str,
) -> None:
    """Upload evidence and post the PR evidence comment after PR creation.

    Best-effort (see :func:`publish_validation_evidence_best_effort`): the
    push/PR/label transitions above this call have already succeeded, so a
    failure here must not roll the Issue back to ``agent/failed``.
    """
    publish_validation_evidence_best_effort(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        github_client=github_client,
        process_runner=process_runner,
        pr_url=pr_url,
        head_sha=get_head_sha(worktree_path, process_runner),
    )


@dataclass(frozen=True)
class _PublicationReviewRequest:
    """两条发布路径共用的审核与最终复核上下文。"""

    verification_request: FinalVerificationRequest
    github_client: IGitHubClient
    expected_branch: str
    verification_results: list[CommandResult]
    push_callback: Callable[[], None]
    content_generator: IContentGenerator | None


def _review_verify_create_pr(request: _PublicationReviewRequest) -> _VerifiedPrPublication:
    """审核、复核最终提交，随后创建 Draft PR。"""
    verification_request = request.verification_request
    # 发布路径本身不变（决策一）：pre-PR review 照常执行（其开关仍是
    # config.pre_pr_review.enabled）；快速通道旁路的是 verification_request
    # 携带的两道验证门禁与 PR 标注。直发档再往前一步，连 reviewer agent 也不跑。
    if verification_request.publish_stage.skips_review_and_repo_verification:
        _logger.info(
            "Direct-pr track (PublishStage.DIRECT): skipping the pre-PR review agent "
            "for Issue #%d; CI on the Draft PR is the gate.",
            verification_request.issue.number,
        )
    else:
        run_pre_pr_review(
            issue=verification_request.issue,
            worktree_path=verification_request.worktree_path,
            config=verification_request.config,
            github_client=request.github_client,
            process_runner=verification_request.process_runner,
            selected_agent=verification_request.selected_agent,
            head_sha_before=verification_request.verified_sha
            or get_head_sha(
                verification_request.worktree_path, verification_request.process_runner
            ),
            expected_branch=request.expected_branch,
            verification_results=request.verification_results,
            push_callback=request.push_callback,
        )
    final_verdict = ensure_final_verifier_verdict(verification_request)
    branch, pr_url = _create_draft_pr_with_recovery_context(
        issue=verification_request.issue,
        worktree_path=verification_request.worktree_path,
        config=verification_request.config,
        github_client=request.github_client,
        process_runner=verification_request.process_runner,
        expected_branch=request.expected_branch,
        content_generator=request.content_generator,
        publish_stage=verification_request.publish_stage,
    )
    return _VerifiedPrPublication(
        verification_request=verification_request,
        github_client=request.github_client,
        branch=branch,
        pr_url=pr_url,
        verdict=final_verdict,
    )


@dataclass(frozen=True)
class _VerifiedPrPublication:
    """创建 PR 后发布最终复核结论和证据所需的上下文。"""

    verification_request: FinalVerificationRequest
    github_client: IGitHubClient
    branch: str
    pr_url: str
    verdict: ValidationVerdict | None


def _publish_verified_pr(request: _VerifiedPrPublication) -> None:
    """统一发布 verifier 结论、Issue 回链和 RV 证据。"""
    verification_request = request.verification_request
    apply_verifier_verdict_to_pr(
        pr_url=request.pr_url,
        verdict=request.verdict,
        issue_number=verification_request.issue.number,
        verifier_passed_label=verification_request.config.labels.verifier_passed,
        github_client=request.github_client,
    )
    publish_sha = get_head_sha(
        verification_request.worktree_path, verification_request.process_runner
    )
    _comment_issue_after_publish(
        issue_number=verification_request.issue.number,
        comment_body=build_draft_pr_created_comment(
            pr_url=request.pr_url,
            branch=request.branch,
            head_sha=publish_sha,
        ),
        worktree_path=verification_request.worktree_path,
        github_client=request.github_client,
    )
    _publish_validation_evidence_after_pr(
        issue=verification_request.issue,
        worktree_path=verification_request.worktree_path,
        config=verification_request.config,
        github_client=request.github_client,
        process_runner=verification_request.process_runner,
        pr_url=request.pr_url,
    )


def _count_local_commits_since_base(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> int:
    """计算 worktree 相对于远程 base 分支的本地提交数量。

    使用 git rev-list --count 计算 base..HEAD 之间的提交数。

    Args:
        worktree_path: worktree 目录
        config: 应用配置
        process_runner: 进程运行器

    Returns:
        本地提交数量
    """
    base_ref_name = f"{config.git.remote}/{config.git.base_branch}"
    ahead_result = process_runner.run(
        ["git", "rev-list", "--count", f"{base_ref_name}..HEAD"],
        cwd=worktree_path,
        check=False,
    )
    if ahead_result.return_code != 0:
        return 0
    try:
        return int(ahead_result.stdout.strip() or "0")
    except ValueError:
        return 0


def _reuse_existing_local_commit(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> AgentCommitResult | None:
    """检查并复用 worktree 中已有的本地提交。

    用于恢复路径：当 worktree 已存在干净的本地 commit 时，
    直接复用而不重新调用 Agent。

    复用条件：
    1. 有超过 base 分支的本地提交
    2. 工作区没有未提交的变更
    3. 验证通过
    4. PRD 交付物就绪

    Args:
        issue: Issue 对象
        worktree_path: worktree 目录
        config: 应用配置
        process_runner: 进程运行器

    Returns:
        包含验证结果的 AgentCommitResult，或 None（不满足复用条件）
    """
    return _reuse_local_commit(
        LocalCommitReuseRequest(issue, worktree_path, config, process_runner)
    )


@dataclass(frozen=True)
class LocalCommitReuseRequest:
    """已有提交复用的档位与机械安全检查上下文。"""

    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    process_runner: IProcessRunner
    publish_stage: PublishStage = PublishStage.NORMAL


def _reuse_local_commit(request: LocalCommitReuseRequest) -> AgentCommitResult | None:
    """DIRECT 只跳验证，保留有提交、干净工作树和禁改路径检查。"""
    issue = request.issue
    worktree_path = request.worktree_path
    config = request.config
    process_runner = request.process_runner
    local_commit_count = _count_local_commits_since_base(worktree_path, config, process_runner)
    if local_commit_count <= 0 or has_changes(worktree_path, process_runner):
        return None

    from backend.core.use_cases.agent_runner_publish import validate_safe_changes

    validate_safe_changes(worktree_path, config, process_runner)
    verification_results: list[CommandResult] = []
    if not request.publish_stage.skips_review_and_repo_verification:
        verification_results = run_verification(worktree_path, config, process_runner)
        ensure_verification_passed(verification_results)
        ensure_prd_delivery_ready(issue, worktree_path, process_runner)
        ensure_validation_evidence_ready(issue, worktree_path, config)

    # 二次检查：验证后可能有新变更
    if has_changes(worktree_path, process_runner):
        return None

    base_ref_name = f"{config.git.remote}/{config.git.base_branch}"
    head_sha = get_head_sha(worktree_path, process_runner)
    _logger.info(
        "Reusing %d existing local commit(s) for Issue #%d at %s.",
        local_commit_count,
        issue.number,
        head_sha,
    )
    return AgentCommitResult(
        verification_results=verification_results,
        attempt_results=[
            AttemptResult(
                attempt_number=1,
                failure_type=FailureType.SUCCESS,
                recovered=True,
                detail=(
                    f"Reused {local_commit_count} existing local commit(s) "
                    f"already ahead of {base_ref_name}; agent was not invoked."
                ),
            )
        ],
    )


def _try_distill_skill_after_success(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    commit_result: AgentCommitResult,
) -> None:
    """Best-effort skill distillation at the start of publication.

    Runs only when ``config.memory.enabled`` is true and at least one
    attempt succeeded. Any failure (project-specific filter, write error,
    promotion error) is logged and swallowed so push / pre-PR review /
    draft PR creation never block on the skill pipeline.
    """
    if not config.memory.enabled:
        return
    if not commit_result.attempt_results:
        return
    successful_attempts = [
        attempt for attempt in commit_result.attempt_results if attempt.recovered or attempt.detail
    ]
    if not successful_attempts:
        return
    diff_summary = "\n".join(
        f"- attempt {a.attempt_number}: {a.failure_type.value} ({'recovered' if a.recovered else 'first-shot'})"
        for a in commit_result.attempt_results
    )
    recovery_history = "\n".join(
        f"- attempt {a.attempt_number} ({a.failure_type.value}): {a.detail[:300]}"
        for a in commit_result.attempt_results
    )
    try:
        candidate = distill_skill(
            issue=issue,
            diff_summary=diff_summary,
            recovery_history=recovery_history,
            worktree_path=worktree_path,
            memory_config=config.memory,
        )
    except Exception as exc:  # noqa: BLE001 - distillation must not block publication.
        _logger.warning(
            "Skill distillation raised for Issue #%d: %s",
            issue.number,
            exc,
        )
        return
    if candidate is None:
        return
    skill_store = _build_skill_store(worktree_path, config.memory)
    try:
        saved_path = save_skill_draft(
            candidate,
            config.memory,
            worktree_path,
            skill_store,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "Failed to save skill draft for Issue #%d: %s",
            issue.number,
            exc,
        )
        return
    _logger.info(
        "Distilled skill draft for Issue #%d at %s.",
        issue.number,
        saved_path,
    )
    if not config.memory.auto_promote:
        return
    try:
        existing = find_similar_draft(candidate, config.memory, worktree_path, skill_store)
        if existing is None:
            return
        if not should_auto_promote(existing, config.memory):
            return
        promote_draft_to_skills(existing, config.memory, worktree_path, skill_store)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "Auto-promote failed for Issue #%d: %s",
            issue.number,
            exc,
        )


def _build_skill_store(worktree_path: Path, memory_config):
    """Construct the skill-store protocol adapter for distillation calls.

    Kept as a tiny shim so the publication hook stays decoupled from
    ``infrastructure/memory`` specifics; returns ``None`` when memory is
    disabled (the caller skips distillation in that case). The actual
    composition lives in ``core/agent/memory/_composition.py`` which
    dynamically loads the ``infrastructure/`` implementations, preserving
    the strict ``core -> infrastructure`` ban.
    """
    from backend.core.agent.memory._composition import (
        build_default_memory_services,
    )

    services = build_default_memory_services(worktree_path, memory_config)
    return services.skill


def _should_run_post_pr_supervisor(
    config: AppConfig,
    publish_stage: PublishStage,
    issue_number: int,
) -> bool:
    """判断本次发布是否进入 PR 后监督循环。

    直发档（``--direct-pr``）不进监督：那是 Draft PR 之后的又一次 agent 调用，而该
    档位的承诺是「建完 PR 只剩机械状态」，因此与未启用监督走同一分支（直接落到
    ``agent/review``），并留下旗标来源可追溯的审计日志。

    Args:
        config: 应用配置（提供 ``post_pr_supervisor.enabled`` 与 workflow 标签）。
        publish_stage: 本次运行的发布档位。
        issue_number: 目标 Issue 编号（仅用于日志）。

    Returns:
        是否启动 PR 后监督循环。
    """
    if not config.post_pr_supervisor.enabled:
        return False
    if publish_stage.skips_review_and_repo_verification:
        _logger.info(
            "Direct-pr track (PublishStage.DIRECT): skipping the post-PR supervisor "
            "for Issue #%d; the Draft PR goes straight to %s and CI on it is the gate.",
            issue_number,
            config.labels.review,
        )
        return False
    return True


def _finish_implementation_publication(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    selected_agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    expected_branch: str,
    commit_result: AgentCommitResult,
    content_generator: IContentGenerator | None = None,
    publish_stage: PublishStage = PublishStage.NORMAL,
    direct_pr_label: str | None = None,
) -> None:
    """完成新实现的发布流程（完整路径）。

    处理流程：
    1. 评论实现完成信息和验证结果
    2. 立即 push 实现 commit 到远程（不再被 review 阻塞）
    3. 运行 pre-PR code review（reviewer 修复会即时 push）
    4. review 收敛后才创建 Draft PR
    5. 消费 Issue 上的直发标签（命中过标签时）
    6. 启动 PR 后监督循环（或直接进入 review 标签）

    Args:
        issue: Issue 对象
        worktree_path: worktree 目录
        config: 应用配置
        selected_agent: 选定的 AI Agent
        github_client: GitHub 客户端
        process_runner: 进程运行器
        expected_branch: 预期的分支名
        commit_result: Agent 提交结果
        content_generator: 可选的 AI 内容生成器（用于 PR description）
        publish_stage: 发布档位：非 ``NORMAL`` 时跳过发布前的最终 RV / verifier
            复核，PR 正文打未验证标注；``DIRECT`` 连 pre-PR review agent 也不跑。
        direct_pr_label: 本次认领命中的直发标签名；确认 Draft PR 与本次发布同次后由
            认领赢家移除该标签。``None`` 表示档位来自调用侧（如 CLI 旗标）而非标签，
            此时不写任何标签。
    """
    # 导入监督循环（避免循环导入）
    from backend.core.use_cases.agent_runner_supervisor import (
        _run_supervisor_with_repair_loop,
    )

    verification_results = commit_result.verification_results
    after_sha = get_head_sha(worktree_path, process_runner)

    # 步骤 0: 蒸馏 skill 草稿。失败仅记录日志，不阻塞后续 push / review / PR。
    _try_distill_skill_after_success(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        commit_result=commit_result,
    )

    # 步骤 1: 评论实现完成信息
    github_client.comment_issue(
        issue.number,
        build_implementation_complete_comment(
            agent=selected_agent,
            branch=expected_branch,
            head_sha=after_sha,
            verification_results=verification_results,
            attempt_results=commit_result.attempt_results,
        ),
    )

    # 步骤 2: 实现 commit 完成后立即 push，使 feature branch 可见。
    # 后续 reviewer 修复也通过 push_callback 走同一条 push 路径。
    _push_changes_with_recovery_context(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )

    push_callback = _make_push_callback(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )

    # 步骤 3: 审核后仅对改变的 HEAD 重新取 RV 与 verifier 结论（快速通道跳过）。
    final_verification_request = FinalVerificationRequest(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        selected_agent=selected_agent,
        verified_sha=after_sha,
        verifier_verdict=commit_result.verifier_verdict,
        publish_stage=publish_stage,
    )
    reviewed_pr = _review_verify_create_pr(
        _PublicationReviewRequest(
            verification_request=final_verification_request,
            github_client=github_client,
            expected_branch=expected_branch,
            verification_results=verification_results,
            push_callback=push_callback,
            content_generator=content_generator,
        )
    )

    # 直发标签的消费点：Draft PR 已确认（同仓、同 Issue 分支、同 head、正文带指认本
    # Issue 的 `iar:direct-pr` marker）才移除标签。放在 workflow 标签切换之前——删除
    # 失败时 Issue 仍停在 running，下一轮认领会确认同一个 PR 并只补清理，而不是重复
    # 构建或重复建 PR。
    consume_label_after_publication(
        DirectPrLabelPublicationRequest(
            github_client=github_client,
            issue_number=issue.number,
            direct_pr_label=direct_pr_label,
            candidate=DirectPrPublicationCandidate(
                branch=reviewed_pr.branch, head=get_head_sha(worktree_path, process_runner)
            ),
            pr_url=reviewed_pr.pr_url,
        )
    )

    # 切换标签：running → supervising，并清理其他 workflow labels。
    _edit_issue_labels_after_publish(
        issue_number=issue.number,
        add_labels=[config.labels.supervising],
        remove_labels=[
            label for label in workflow_state_labels(config) if label != config.labels.supervising
        ],
        worktree_path=worktree_path,
        github_client=github_client,
    )

    _publish_verified_pr(reviewed_pr)
    if direct_pr_label is not None:
        complete_direct_pr_round(github_client, issue)

    # 步骤 4: PR 后监督（可选；直发档不进监督）
    if _should_run_post_pr_supervisor(config, publish_stage, issue.number):
        # 获取 PR 上下文（如果已存在）
        pr_context = github_client.get_pull_request_context(reviewed_pr.branch)
        if pr_context is None:
            _logger.warning(
                "Deferring post-PR supervisor for Issue #%d branch %s: "
                "complete PR context is unavailable.",
                issue.number,
                reviewed_pr.branch,
            )
        else:
            supervisor_agent = resolve_supervisor_agent(
                issue,
                config,
                "auto",
                fallback_agent=selected_agent,
            )
            # 启动监督修复循环
            _run_supervisor_with_repair_loop(
                issue=issue,
                worktree_path=worktree_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                pr_context=pr_context,
                supervisor_agent=supervisor_agent,
                executor_agent=selected_agent,
            )
    else:
        # 未启用监督（或直发档旁路）时直接进入 review 标签
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=[config.labels.review],
            remove_labels=[
                label for label in workflow_state_labels(config) if label != config.labels.review
            ],
            worktree_path=worktree_path,
            github_client=github_client,
        )

    _logger.info(
        "Published Issue #%d from %s at %s after implementation head %s.",
        issue.number,
        reviewed_pr.branch,
        get_head_sha(worktree_path, process_runner),
        after_sha,
    )


def _finish_existing_commit_publication(
    *,
    publish_stage: PublishStage = PublishStage.NORMAL,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    selected_agent: str,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    expected_branch: str,
    commit_result: AgentCommitResult,
    content_generator: IContentGenerator | None = None,
    direct_pr_label: str | None = None,
) -> None:
    """完成已存在本地 commit 的恢复发布流程。

    与 _finish_implementation_publication 类似，但不调用 Agent。
    用于 runner 重启后发现 worktree 已有干净的本地提交的情况。

    处理流程：
    1. 评论实现完成信息（标记为 recovered）
    2. 立即 push 本地 commit 到远程
    3. 运行 pre-PR code review（reviewer 修复会即时 push）
    4. review 收敛后才创建 Draft PR
    5. 消费 Issue 上的直发标签（命中过标签时）
    6. 启动 PR 后监督循环（或直接进入 review 标签）

    Args:
        publish_stage: 恢复发布路径下的发布档位：非 ``NORMAL`` 时同样跳过发布前 RV /
            verifier 复核并打未验证标注。
        direct_pr_label: 本次认领命中的直发标签名；确认 Draft PR 与本次发布同次后由
            认领赢家移除。``None`` 表示档位来自调用侧旗标，不写任何标签。
        issue: Issue 对象
        worktree_path: worktree 目录
        config: 应用配置
        selected_agent: 选定的 AI Agent
        github_client: GitHub 客户端
        process_runner: 进程运行器
        expected_branch: 预期的分支名
        commit_result: 已存在的提交结果
        content_generator: 可选的 AI 内容生成器
    """
    # 导入监督循环（避免循环导入）
    from backend.core.use_cases.agent_runner_supervisor import (
        _run_supervisor_with_repair_loop,
    )

    verification_results = commit_result.verification_results
    head_sha = get_head_sha(worktree_path, process_runner)

    # 步骤 1: 评论实现完成信息（attempt_results 会显示 recovered=True）
    github_client.comment_issue(
        issue.number,
        build_implementation_complete_comment(
            agent=selected_agent,
            branch=expected_branch,
            head_sha=head_sha,
            verification_results=verification_results,
            attempt_results=commit_result.attempt_results,
        ),
    )

    # 步骤 2: 立即 push 已存在 commit 到远程（不再被 review 阻塞）
    _push_changes_with_recovery_context(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )

    push_callback = _make_push_callback(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )

    # 步骤 3: 复用提交也要审核，并在最终 HEAD 上取得复核结论（快速通道跳过）。
    final_verification_request = FinalVerificationRequest(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        process_runner=process_runner,
        selected_agent=selected_agent,
        verified_sha=None,
        verifier_verdict=commit_result.verifier_verdict,
        publish_stage=publish_stage,
    )
    reviewed_pr = _review_verify_create_pr(
        _PublicationReviewRequest(
            verification_request=final_verification_request,
            github_client=github_client,
            expected_branch=expected_branch,
            verification_results=verification_results,
            push_callback=push_callback,
            content_generator=content_generator,
        )
    )
    # 恢复路径同样只在确认同次 PR 之后消费标签（判据与完整路径完全一致）。
    consume_label_after_publication(
        DirectPrLabelPublicationRequest(
            github_client=github_client,
            issue_number=issue.number,
            direct_pr_label=direct_pr_label,
            candidate=DirectPrPublicationCandidate(
                branch=reviewed_pr.branch, head=get_head_sha(worktree_path, process_runner)
            ),
            pr_url=reviewed_pr.pr_url,
        )
    )

    # 切换标签：从 workflow state labels → supervising
    _edit_issue_labels_after_publish(
        issue_number=issue.number,
        add_labels=[config.labels.supervising],
        remove_labels=workflow_state_labels(config),
        worktree_path=worktree_path,
        github_client=github_client,
    )
    _publish_verified_pr(reviewed_pr)
    if direct_pr_label is not None:
        complete_direct_pr_round(github_client, issue)

    # 步骤 4: PR 后监督（可选；直发档不进监督）
    if _should_run_post_pr_supervisor(config, publish_stage, issue.number):
        pr_context = github_client.get_pull_request_context(reviewed_pr.branch)
        if pr_context is None:
            _logger.warning(
                "Deferring post-PR supervisor for Issue #%d branch %s: "
                "complete PR context is unavailable.",
                issue.number,
                reviewed_pr.branch,
            )
        else:
            supervisor_agent = resolve_supervisor_agent(
                issue,
                config,
                "auto",
                fallback_agent=selected_agent,
            )
            _run_supervisor_with_repair_loop(
                issue=issue,
                worktree_path=worktree_path,
                config=config,
                github_client=github_client,
                process_runner=process_runner,
                pr_context=pr_context,
                supervisor_agent=supervisor_agent,
                executor_agent=selected_agent,
            )
    else:
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=[config.labels.review],
            remove_labels=[
                label for label in workflow_state_labels(config) if label != config.labels.review
            ],
            worktree_path=worktree_path,
            github_client=github_client,
        )

    _logger.info(
        "Recovered publication for Issue #%d from %s at %s.",
        issue.number,
        reviewed_pr.branch,
        get_head_sha(worktree_path, process_runner),
    )
