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
from backend.core.use_cases.agent_runner_direct_pr_label import (
    DirectPrLabelPublicationRequest,
    consume_label_after_publication,
)
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    complete_direct_pr_round,
)
from backend.core.use_cases.agent_runner_events import format_event_marker
from backend.core.use_cases.agent_runner_failure import PublishFailureError
from backend.core.use_cases.agent_runner_final_verification import (
    FinalVerificationRequest,
    ensure_final_verifier_verdict,
)
from backend.core.use_cases.agent_runner_publish import (
    DraftPRCreationError,
    create_draft_pr,
    push_changes,
)
from backend.core.use_cases.agent_runner_skill_distillation import (
    _SkillDistillationRequest,
    _try_distill_skill_after_success,
)
from backend.core.use_cases.agent_runner_validation import (
    ensure_validation_evidence_ready,
    publish_validation_evidence_best_effort,
)
from backend.core.use_cases.agent_runner_issue_control import (
    VerifierCancelledByHumanError,
    clear_verifier_hold_after_green,
    discard_stale_verdict,
    finalize_verifier_takeover,
    mark_verifier_pr_merged,
)
from backend.core.use_cases.agent_runner_verifier_publication import (
    VerifierPublicationDependencies,
    _VerifiedPrPublication,
    publish_verified_pr,
)
from backend.core.use_cases.agent_runner_workflow import (
    build_transition_labels,
    issue_requires_human_review,
    workflow_state_labels,
)
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
    apply_verifier_verdict_to_pr,
)

_logger = logging.getLogger(__name__)


def _review_handoff_label_updates(
    issue: IssueSummary,
    config: AppConfig,
) -> tuple[list[str], list[str]]:
    """返回进入自动审查通过状态时要添加和移除的标签。"""
    next_labels = build_transition_labels(
        issue.labels,
        config,
        config.labels.review,
        human_review_required=issue_requires_human_review(issue, config),
    )
    return (
        [label for label in next_labels if label not in issue.labels],
        [label for label in issue.labels if label not in next_labels],
    )


@dataclass(frozen=True)
class _PushChangesRequest:
    """分支 push 与清理旧 verifier 标签所需的发布上下文。"""

    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    expected_branch: str


@dataclass(frozen=True)
class PreVerifierDraftPublicationRequest:
    """独立 verifier 启动前创建 Draft PR 所需的上下文。"""

    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    expected_branch: str
    publish_stage: PublishStage
    content_generator: IContentGenerator | None = None


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


def _push_changes_with_recovery_context(request: _PushChangesRequest) -> str:
    """推送分支并在更新已有 PR 前清除过期的 verifier 通过标签。"""
    try:
        existing_pr_context = request.github_client.get_pull_request_context(
            request.expected_branch, require_success=True
        )
        if existing_pr_context is not None:
            if existing_pr_context.number is None:
                raise RuntimeError(
                    "无法清理分支 "
                    f"{request.expected_branch} 上 PR 的 verifier 状态：GitHub 响应缺少 PR 编号。"
                )
            request.github_client.edit_issue_labels(
                existing_pr_context.number,
                remove=(request.config.labels.verifier_passed,),
            )
        return push_changes(
            request.issue,
            request.worktree_path,
            request.config,
            request.process_runner,
            expected_branch=request.expected_branch,
        )
    except Exception as exc:  # noqa: BLE001 - 标签清理与 push 都是发布边界。
        raise PublishFailureError(
            str(exc),
            worktree_path=request.worktree_path,
            failure_category=PublishFailureCategory.PUSH,
        ) from exc


def publish_draft_pr_before_verifier(request: PreVerifierDraftPublicationRequest) -> None:
    """本地验证通过后先发布 Draft PR；失败时留给最终发布阶段重试。

    Args:
        request: Draft PR 提前发布所需的 Issue、worktree 与发布依赖。
    """
    if request.publish_stage is not PublishStage.NORMAL:
        return
    try:
        _push_changes_with_recovery_context(
            _PushChangesRequest(
                issue=request.issue,
                worktree_path=request.worktree_path,
                config=request.config,
                github_client=request.github_client,
                process_runner=request.process_runner,
                expected_branch=request.expected_branch,
            )
        )
        _create_draft_pr_with_recovery_context(
            issue=request.issue,
            worktree_path=request.worktree_path,
            config=request.config,
            github_client=request.github_client,
            process_runner=request.process_runner,
            expected_branch=request.expected_branch,
            content_generator=request.content_generator,
            publish_stage=request.publish_stage,
        )
    except Exception as exc:  # noqa: BLE001 - 提前发布失败不应改变 verifier 结论。
        _logger.warning(
            "Could not create the pre-verifier Draft PR for Issue #%d; "
            "the final publication stage will retry: %s",
            request.issue.number,
            exc,
        )


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
    """创建或复用 Draft PR，并将失败归类为可恢复的 PR 创建错误。"""
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


def _make_push_callback(request: _PushChangesRequest) -> Callable[[], None]:
    """为 :func:`run_pre_pr_review` 构建无参数 push 回调。

    审查循环会在每次 reviewer commit 后调用它；push 失败时会抛出
    :class:`PublishFailureError`，由 runner 按可恢复发布错误处理。
    """

    def _push() -> None:
        _push_changes_with_recovery_context(request)

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
    # 发布路径本身不变：FAST 旁路两道验证门禁，PRD_AUTO_MERGE 只旁路 verifier；
    # pre-PR review 照常执行（其开关仍是 config.pre_pr_review.enabled）。
    # DIRECT 再往前一步，连 reviewer agent 也不跑。
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


def _review_verify_create_pr_with_takeover_handoff(
    request: _PublicationReviewRequest,
) -> _VerifiedPrPublication | None:
    """审核 + 最终复核 + 建 PR；被人工接管时交班并返回 ``None``（Issue #278）。

    最终复核（:func:`ensure_final_verifier_verdict`）会在 HEAD 变化后**重跑**独立
    verifier，因此它与首跑一样开放接管窗口。取消不是发布流程可以继续消化的失败：
    进程树已回收、控制行已落闸，收敛 GitHub 事实后调用方必须直接返回——绝不建 PR、
    不发结论、不进监督。

    Returns:
        正常发布上下文；本次运行已被人工接管时为 ``None``。
    """
    try:
        return _review_verify_create_pr(request)
    except VerifierCancelledByHumanError:
        verification_request = request.verification_request
        finalize_verifier_takeover(
            repo_id=verification_request.repo_id,
            issue=verification_request.issue,
            config=verification_request.config,
            github_client=request.github_client,
            expected_branch=request.expected_branch,
        )
        _logger.info(
            "Issue #%d: independent verifier taken over by a human during final "
            "verification; publication stopped and handed to manual review.",
            verification_request.issue.number,
        )
        return None


def _publish_verified_pr(request: _VerifiedPrPublication) -> bool:
    """委托独立 verifier 发布用例，保留发布流程的依赖入口。"""
    return publish_verified_pr(
        request,
        VerifierPublicationDependencies(
            get_head_sha=get_head_sha,
            apply_verifier_verdict_to_pr=apply_verifier_verdict_to_pr,
            clear_verifier_hold_after_green=clear_verifier_hold_after_green,
            discard_stale_verdict=discard_stale_verdict,
            mark_verifier_pr_merged=mark_verifier_pr_merged,
            comment_issue_after_publish=_comment_issue_after_publish,
            draft_pr_created_comment=build_draft_pr_created_comment,
            publish_validation_evidence_after_pr=_publish_validation_evidence_after_pr,
        ),
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


def _should_run_post_pr_supervisor(
    config: AppConfig,
    publish_stage: PublishStage,
    issue_number: int,
) -> bool:
    """判断本次发布是否进入 PR 后监督循环。

    直发档（``--direct-pr``）不进监督：那是 Draft PR 之后的又一次 agent 调用，而该
    档位的承诺是「建完 PR 只剩机械状态」，因此与未启用监督走同一分支（直接落到
    ``agent/automated-review-passed``），并留下旗标来源可追溯的审计日志。

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
    repo_id: str | None = None,
) -> None:
    """完成新实现的发布流程（完整路径）。

    处理流程：
    1. 评论实现完成信息和验证结果
    2. verifier 前已尽力发布未签核的 Draft PR；此处再次 push 确保分支最新
    3. 运行 pre-PR code review（reviewer 修复会清除旧标签并即时 push）
    4. review 收敛后复用 Draft PR，并对最终 HEAD 做必要复核
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
        repo_id: 稳定的仓库 registry id，用于读取对应的短期执行记忆。
    """
    # 导入监督循环（避免循环导入）
    from backend.core.use_cases.agent_runner_supervisor import (
        _run_supervisor_with_repair_loop,
    )

    verification_results = commit_result.verification_results
    after_sha = get_head_sha(worktree_path, process_runner)

    # 步骤 0: 蒸馏 skill 草稿。失败仅记录日志，不阻塞后续 push / review / PR。
    _try_distill_skill_after_success(
        request=_SkillDistillationRequest(
            issue=issue,
            worktree_path=worktree_path,
            config=config,
            commit_result=commit_result,
            selected_agent=selected_agent,
            process_runner=process_runner,
            content_generator=content_generator,
            repo_id=repo_id,
        )
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

    # 步骤 2: verifier 前已经尽力发布 Draft PR；再次 push 确认当前分支可见。
    # 后续 reviewer 修复也通过 push_callback 走同一条路径，并先清除旧 verifier 标签。
    push_request = _PushChangesRequest(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        github_client=github_client,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )
    _push_changes_with_recovery_context(push_request)

    push_callback = _make_push_callback(push_request)

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
        repo_id=repo_id,
    )
    reviewed_pr = _review_verify_create_pr_with_takeover_handoff(
        _PublicationReviewRequest(
            verification_request=final_verification_request,
            github_client=github_client,
            expected_branch=expected_branch,
            verification_results=verification_results,
            push_callback=push_callback,
            content_generator=content_generator,
        )
    )
    if reviewed_pr is None:
        # 人工接管（Issue #278）：交班已完成，发布流程到此为止。
        return
    if _publish_verified_pr(reviewed_pr) is False:
        # PR 已被外部合并/关闭或 HEAD 漂移：不切换 Issue 标签、不启动后续阶段。
        return

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

    # NORMAL/FAST 先进入 supervising；DIRECT 留在当前状态直到最终 review 交接成功。
    if publish_stage is not PublishStage.DIRECT:
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=[config.labels.supervising],
            remove_labels=[
                label
                for label in workflow_state_labels(config)
                if label != config.labels.supervising
            ],
            worktree_path=worktree_path,
            github_client=github_client,
        )

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
                repo_id=repo_id,
            )
    else:
        # 未启用监督（或直发档旁路）时直接进入 review 标签
        review_add_labels, review_remove_labels = _review_handoff_label_updates(issue, config)
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=review_add_labels,
            remove_labels=review_remove_labels,
            worktree_path=worktree_path,
            github_client=github_client,
        )

    if direct_pr_label is not None:
        complete_direct_pr_round(github_client, issue)

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
    repo_id: str | None = None,
) -> None:
    """完成已存在本地 commit 的恢复发布流程。

    与 _finish_implementation_publication 类似，但不调用 Agent。
    用于 runner 重启后发现 worktree 已有干净的本地提交的情况。

    处理流程：
    1. 评论实现完成信息（标记为 recovered）
    2. verifier 前已尽力发布未签核的 Draft PR；此处再次 push 确保分支最新
    3. 运行 pre-PR code review（reviewer 修复会清除旧标签并即时 push）
    4. review 收敛后复用 Draft PR，并对最终 HEAD 做必要复核
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
        repo_id: 稳定仓库标识（Issue #278），透传给 verdict 发布前的
            merged/HEAD 复核；``None`` 时复核照常但过期结论不落闸。
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

    # 步骤 2: verifier 前已经尽力发布 Draft PR；再次 push 确认当前分支可见。
    push_request = _PushChangesRequest(
        issue=issue,
        worktree_path=worktree_path,
        config=config,
        github_client=github_client,
        process_runner=process_runner,
        expected_branch=expected_branch,
    )
    _push_changes_with_recovery_context(push_request)

    push_callback = _make_push_callback(push_request)

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
        repo_id=repo_id,
    )
    reviewed_pr = _review_verify_create_pr_with_takeover_handoff(
        _PublicationReviewRequest(
            verification_request=final_verification_request,
            github_client=github_client,
            expected_branch=expected_branch,
            verification_results=verification_results,
            push_callback=push_callback,
            content_generator=content_generator,
        )
    )
    if reviewed_pr is None:
        # 人工接管（Issue #278）：交班已完成，恢复发布流程到此为止。
        return
    if _publish_verified_pr(reviewed_pr) is False:
        # PR 已被外部合并/关闭或 HEAD 漂移：不切换 Issue 标签、不启动后续阶段。
        return
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

    # NORMAL/FAST 恢复先进入 supervising；DIRECT 直接完成最终 review 交接。
    if publish_stage is not PublishStage.DIRECT:
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=[config.labels.supervising],
            remove_labels=workflow_state_labels(config),
            worktree_path=worktree_path,
            github_client=github_client,
        )
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
                repo_id=repo_id,
            )
    else:
        review_add_labels, review_remove_labels = _review_handoff_label_updates(issue, config)
        _edit_issue_labels_after_publish(
            issue_number=issue.number,
            add_labels=review_add_labels,
            remove_labels=review_remove_labels,
            worktree_path=worktree_path,
            github_client=github_client,
        )

    if direct_pr_label is not None:
        complete_direct_pr_round(github_client, issue)

    _logger.info(
        "Recovered publication for Issue #%d from %s at %s.",
        issue.number,
        reviewed_pr.branch,
        get_head_sha(worktree_path, process_runner),
    )
