"""blocked 与发布恢复共用的认领、fresh 准入及只补交接选择。"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid
from backend.core.use_cases.agent_runner_direct_pr_label import (
    DirectPrLabelPublicationRequest,
    PublishStageDecision,
    PublishStageSelectionRequest,
    ensure_direct_pr_admission,
    ensure_direct_pr_dependencies_ready,
    finish_label_cleanup_if_already_published,
    resolve_claim_publish_stage,
)
from backend.core.use_cases.agent_runner_direct_pr_round import (
    DirectPrPublicationCandidate,
    complete_direct_pr_round,
    save_direct_pr_selection,
)


@dataclass(frozen=True)
class RecoverySelectionRequest:
    """保持处理器协作者可替换的恢复选择请求。

    Attributes:
        selection: 当前 Issue 的 fresh 选择及 fallback 缓存上下文。
        claim: 处理器提供的既有 CAS 仲裁入口。
        candidate: 仅在 DIRECT 恢复时读取当前分支与提交。
        handoff: 处理器提供的同轮 PR 交接操作。
        cleanup_only: 发现阶段已经证明成功 PR 时，禁止转成新的发布工作。
    """

    selection: PublishStageSelectionRequest
    claim: Callable[[], ClaimBid]
    candidate: Callable[[], DirectPrPublicationCandidate]
    handoff: Callable[[PublishStageDecision, DirectPrPublicationCandidate], bool]
    cleanup_only: bool = False


def resolve_recovery_selection(request: RecoverySelectionRequest) -> PublishStageDecision | None:
    """复用认领后 fresh 判定，并在已发布轮次交接完成后终止恢复链。

    Args:
        request: 认领、选择与交接所需的上下文。

    Returns:
        需要继续恢复的选择；已完成同轮 PR 交接时为 None。

    Raises:
        RuntimeError: 只补交接请求在持锁后不能再证明原成功轮次。
    """
    selection = request.selection
    decision = resolve_claim_publish_stage(selection)
    if decision.publish_stage is PublishStage.DIRECT:
        claim_bid = request.claim()
        decision = resolve_claim_publish_stage(replace(selection, issue=decision.issue))
        if request.handoff(decision, request.candidate()):
            return None
        if request.cleanup_only:
            raise RuntimeError(
                "Pending Direct PR handoff changed after discovery; no new publication is allowed."
            )
        admit_claimed_selection(selection, decision, claim_bid.comment_id)
    elif request.cleanup_only:
        raise RuntimeError(
            "Pending Direct PR handoff disappeared after discovery; no new publication is allowed."
        )
    if selection.selection is not None and selection.selection.decision is None:
        selection.selection.decision = decision
    return decision


@dataclass(frozen=True)
class DirectPrHandoffRequest:
    """同轮 PR 交接上下文，保留处理器的 workflow 协作者替换边界。

    Attributes:
        github_client: GitHub 评论及标签端口。
        decision: 当前 fresh Issue 与已冻结的轮次选择。
        candidate: 当前分支与提交。
        transition: 切换 supervising workflow 的既有操作。
    """

    github_client: IGitHubClient
    decision: PublishStageDecision
    candidate: DirectPrPublicationCandidate
    transition: Callable[[], None]


def finish_direct_pr_handoff(request: DirectPrHandoffRequest) -> bool:
    """确认同轮 PR 后依次消费标签、交接 workflow 并完成持久检查点。

    Args:
        request: 当前轮次及处理器提供的 workflow 操作。

    Returns:
        已完成交接时为 True；未找到同轮成功 PR 时为 False。
    """
    from backend.core.use_cases.agent_runner_publication import build_draft_pr_created_comment

    decision = request.decision
    candidate = request.candidate
    pr_url = finish_label_cleanup_if_already_published(
        DirectPrLabelPublicationRequest(
            request.github_client, decision.issue.number, decision.direct_pr_label, candidate
        )
    )
    if pr_url is None:
        return False
    request.transition()
    request.github_client.comment_issue(
        decision.issue.number,
        build_draft_pr_created_comment(
            pr_url=pr_url, branch=candidate.branch, head_sha=candidate.head
        ),
    )
    complete_direct_pr_round(request.github_client, decision.issue)
    return True


def admit_claimed_selection(
    selection: PublishStageSelectionRequest, decision: PublishStageDecision, claim_comment_id: int
) -> None:
    """复用 fresh 准入并在赢家开始工作前持久化标签选择。

    Args:
        selection: 配置、GitHub 端口与当前执行缓存。
        decision: 认领后 fresh 的发布选择。
        claim_comment_id: 本轮赢家的 CAS 评论 ID。
    """
    ensure_direct_pr_admission(decision=decision)
    ensure_direct_pr_dependencies_ready(decision, selection.config, selection.github_client)
    if selection.selection is not None and selection.selection.decision is None:
        selection.selection.decision = decision
    if decision.direct_pr_label is not None:
        save_direct_pr_selection(
            selection.github_client,
            decision.issue,
            decision.direct_pr_label,
            claim_comment_id=claim_comment_id,
        )
