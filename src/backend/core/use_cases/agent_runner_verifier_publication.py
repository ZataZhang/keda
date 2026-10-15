"""独立发布 verifier 结论并检查当前 PR / HEAD 是否仍然匹配。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.use_cases.agent_runner_final_verification import FinalVerificationRequest
from backend.core.use_cases.run_verifier_agent import ValidationVerdict

# 拆分前发布侧的告警由 ``agent_runner_publication`` 记录，操作者按 docs/guides/agent-runner.md
# 的排查顺序 grep 那个名字找「verdict 不再可发布」这类行；保持 logger 身份不随模块搬家漂走。
_logger = logging.getLogger("backend.core.use_cases.agent_runner_publication")


@dataclass(frozen=True)
class _VerifiedPrPublication:
    """创建 PR 后发布最终复核结论和证据所需的上下文。"""

    verification_request: FinalVerificationRequest
    github_client: IGitHubClient
    branch: str
    pr_url: str
    verdict: ValidationVerdict | None


@dataclass(frozen=True)
class VerifierPublicationDependencies:
    """verdict 发布阶段的业务与副作用依赖。"""

    get_head_sha: Callable[..., str]
    apply_verifier_verdict_to_pr: Callable[..., None]
    clear_verifier_hold_after_green: Callable[..., bool]
    discard_stale_verdict: Callable[..., bool]
    mark_verifier_pr_merged: Callable[..., bool]
    comment_issue_after_publish: Callable[..., None]
    draft_pr_created_comment: Callable[..., str]
    publish_validation_evidence_after_pr: Callable[..., None]


def _remove_stale_verifier_passed_label(
    request: _VerifiedPrPublication, *, pr_number: int | None, reason: str
) -> None:
    """HEAD/PR 变化后尽力清理可能残留的 GREEN 标签。"""
    if pr_number is None or request.verdict is None or request.verdict.risk != "green":
        return
    try:
        request.github_client.edit_issue_labels(
            pr_number,
            remove=(request.verification_request.config.labels.verifier_passed,),
        )
    except Exception as cleanup_exc:  # noqa: BLE001 - 清理失败只记录，控制面仍落闸。
        _logger.warning(
            "Issue #%d: could not remove verifier-passed label from PR #%s %s (%s).",
            request.verification_request.issue.number,
            pr_number,
            reason,
            cleanup_exc,
        )


def publish_verified_pr(
    request: _VerifiedPrPublication,
    dependencies: VerifierPublicationDependencies,
) -> bool:
    """发布 verifier 结论、Issue 回链和 RV 证据。

    verdict 副作用前后都用严格 GitHub fresh-read（Issue #278 PRD §7.6）：只有 PR
    仍开放且 HEAD 等于实际 verifier 输入提交才允许写
    ``validation/verifier-passed``。若期间合并或漂移，清理过期标签、绑定当前 run
    落闸并返回 ``False``；调用方据此停止后续自动阶段。GREEN 只有在第二次 fresh-read
    仍匹配后才能清除 hold。

    Args:
        request: 发布上下文。
        dependencies: 发布阶段的共享控制与外部副作用函数。

    Returns:
        是否允许继续后续发布阶段。
    """
    verification_request = request.verification_request
    publish_sha = dependencies.get_head_sha(
        verification_request.worktree_path, verification_request.process_runner
    )
    if request.verdict is not None:
        verdict_input_sha = (
            request.verdict.verifier_input_sha or verification_request.verified_sha or publish_sha
        )
        verifier_run_id = request.verdict.verifier_run_id
        pr_context = request.github_client.get_pull_request_context(
            request.branch, require_success=True
        )
        if pr_context is None or pr_context.head_sha != verdict_input_sha:
            _logger.warning(
                "Skipping verifier verdict side effects for Issue #%d: the PR for "
                "branch %s is no longer open at the verified HEAD %s (takeover race "
                "or external change); the verdict is stale and must not be published.",
                verification_request.issue.number,
                request.branch,
                verdict_input_sha[:8],
            )
            _remove_stale_verifier_passed_label(
                request,
                pr_number=pr_context.number if pr_context is not None else None,
                reason="before verdict publication",
            )
            if pr_context is None:
                merged_pr_url = request.github_client.find_merged_pr_by_head(
                    request.branch, require_success=True
                )
            else:
                merged_pr_url = None
            if merged_pr_url:
                dependencies.mark_verifier_pr_merged(
                    repo_id=verification_request.repo_id,
                    issue_number=verification_request.issue.number,
                    verifier_run_id=verifier_run_id,
                )
            else:
                dependencies.discard_stale_verdict(
                    repo_id=verification_request.repo_id,
                    issue_number=verification_request.issue.number,
                    verifier_run_id=verifier_run_id,
                    verdict_input_sha=verdict_input_sha,
                )
            return False
        dependencies.apply_verifier_verdict_to_pr(
            pr_url=request.pr_url,
            verdict=request.verdict,
            issue_number=verification_request.issue.number,
            verifier_passed_label=verification_request.config.labels.verifier_passed,
            github_client=request.github_client,
        )
        after_apply = request.github_client.get_pull_request_context(
            request.branch, require_success=True
        )
        if after_apply is None or after_apply.head_sha != verdict_input_sha:
            # 应用副作用窗口内 PR 被合并或 HEAD 漂移：刚写下的结论标签即刻过期。
            _remove_stale_verifier_passed_label(
                request,
                pr_number=pr_context.number,
                reason="after post-apply drift",
            )
            merged_pr_url = (
                request.github_client.find_merged_pr_by_head(request.branch, require_success=True)
                if after_apply is None
                else None
            )
            if merged_pr_url:
                dependencies.mark_verifier_pr_merged(
                    repo_id=verification_request.repo_id,
                    issue_number=verification_request.issue.number,
                    verifier_run_id=verifier_run_id,
                )
            else:
                dependencies.discard_stale_verdict(
                    repo_id=verification_request.repo_id,
                    issue_number=verification_request.issue.number,
                    verifier_run_id=verifier_run_id,
                    verdict_input_sha=verdict_input_sha,
                )
            _logger.warning(
                "Issue #%d: PR merged or HEAD drifted during verdict side effects; "
                "stale verdict discarded and automatic stages held.",
                verification_request.issue.number,
            )
            return False
        if request.verdict.risk == "green":
            dependencies.clear_verifier_hold_after_green(
                repo_id=verification_request.repo_id,
                issue_number=verification_request.issue.number,
                verifier_run_id=verifier_run_id,
                verifier_input_sha=verdict_input_sha,
            )
    dependencies.comment_issue_after_publish(
        issue_number=verification_request.issue.number,
        comment_body=dependencies.draft_pr_created_comment(
            pr_url=request.pr_url,
            branch=request.branch,
            head_sha=publish_sha,
        ),
        worktree_path=verification_request.worktree_path,
        github_client=request.github_client,
    )
    dependencies.publish_validation_evidence_after_pr(
        issue=verification_request.issue,
        worktree_path=verification_request.worktree_path,
        config=verification_request.config,
        github_client=request.github_client,
        process_runner=verification_request.process_runner,
        pr_url=request.pr_url,
    )
    return True
