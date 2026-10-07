"""Issue 直发标签（``direct-pr``）的档位解析、准入、消费与清理恢复。

直发档原本只由 ``kc run --direct-pr`` 决定，只影响调用它的那台机器那一次运行。把
选择放到 Issue 上之后，任何认领方（另一台电脑、批量队列、守护进程）都必须读到同一个
决定，于是本模块成为**唯一的 Issue 级档位与准入实现**：调用方只负责把它的异常映射成
自己的错误表面，不再各自复刻规则。

三条不变量：

1. **认领后才读**：档位由认领后的 fresh 读取决定，旧队列快照不能放行旁路。
2. **标签不是锁**：本模块只做只读判定与消费写入，认领归属仍由既有 CAS 仲裁决定；
   只有走到发布链的赢家才会消费标签。
3. **两次写不原子**：Draft PR 与标签移除是两次独立 GitHub 写入，因此消费必须建立在
   「确认过同次发布的 PR」之上；确认不了就保留标签并显式报告待恢复，绝不宣称完整成功。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.publish_stage import PublishStage
from backend.core.use_cases.agent_runner_direct_pr_round import DirectPrPublicationCandidate
from backend.core.use_cases.agent_runner_feedback import extract_prd_path

_logger = logging.getLogger(__name__)

#: 档位来源（写进日志，用于跨机器追责「这次直发是谁决定的」）。
ORIGIN_RUN_FLAG = "--direct-pr run flag"
ORIGIN_ISSUE_LABEL = "issue direct-pr label"
ORIGIN_RUN_FLAG_AND_LABEL = "--direct-pr run flag + issue direct-pr label"
ORIGIN_CALLER_REQUEST = "caller request"


class DirectPrPolicyError(RuntimeError):
    """直发策略拒绝执行：不跑 builder、不建 PR、不消费标签。"""


class DirectPrFreshReadError(DirectPrPolicyError):
    """认领后读不到 Issue：无法证明直发前提，fail-closed 不执行。"""


class DirectPrStageConflictError(DirectPrPolicyError):
    """调用档位为 FAST 而 Issue 带直发标签：两个档位旁路范围不同，不猜优先级。"""


class DirectPrNotEligibleError(DirectPrPolicyError):
    """目标不满足直发准入（例如带 PRD 锚点）：拒绝直发，不静默降级成完整档继续跑。"""


class DirectPrLabelCleanupPendingError(RuntimeError):
    """Draft PR 已发布但标签清理未完成：不算完整成功，下一轮认领只补清理。"""


@dataclass(frozen=True)
class PublishStageDecision:
    """一个 Issue 在本次认领里的有效档位。

    Attributes:
        publish_stage: 本次运行实际生效的档位（旁路范围的唯一事实源）。
        origin: 档位来源说明，只用于日志与追责。
        direct_pr_label: 命中的直发标签名；``None`` 表示本次发布不需要消费标签
            （例如纯 CLI 旗标直发，Issue 上没有标签）。
        issue: fresh 读取后的 Issue（正文用于准入判定）。
    """

    publish_stage: PublishStage
    origin: str
    direct_pr_label: str | None
    issue: IssueSummary
    requested_stage: PublishStage | None = None


@dataclass
class PublishStageSelection:
    """单 Issue 一次执行中的选择缓存，NORMAL/FAST 不增加远端写入。"""

    decision: PublishStageDecision | None = None


@dataclass(frozen=True)
class PublishStageSelectionRequest:
    """fresh 读取与当前执行选择的共同上下文。"""

    requested_stage: PublishStage
    issue: IssueSummary
    config: AppConfig
    github_client: IGitHubClient
    selection: PublishStageSelection | None = None


def resolve_claim_publish_stage(request: PublishStageSelectionRequest) -> PublishStageDecision:
    """fallback 复用首轮档位，但 fresh 正文仍用于新工作准入。

    Args:
        request: 每 Issue 的请求档位、GitHub 端口和选择缓存。

    Returns:
        保留已选择档位、采用 fresh 正文的决定。

    Raises:
        DirectPrFreshReadError: fresh Issue 读取失败。
    """
    if request.selection is None or request.selection.decision is None:
        return resolve_publish_stage(
            requested_stage=request.requested_stage,
            issue=request.issue,
            config=request.config,
            github_client=request.github_client,
        )
    try:
        fresh_issue = request.github_client.get_issue(request.issue.number)
    except Exception as exc:
        raise DirectPrFreshReadError(
            f"Cannot refresh Issue #{request.issue.number}: {exc}"
        ) from exc
    return replace(request.selection.decision, issue=fresh_issue)


def configured_direct_pr_label(config: AppConfig) -> str | None:
    """返回配置指定的直发标签名；配置成空串时视为禁用标签路径。"""
    label = (config.labels.direct_pr or "").strip()
    return label or None


def issue_carries_direct_pr_label(issue: IssueSummary, config: AppConfig) -> bool:
    """判断 Issue 的标签集合里是否有配置的直发标签（用于快照预筛，不做放行判定）。"""
    label = configured_direct_pr_label(config)
    return label is not None and label in issue.labels


def resolve_publish_stage(
    *,
    requested_stage: PublishStage,
    issue: IssueSummary,
    config: AppConfig,
    github_client: IGitHubClient,
) -> PublishStageDecision:
    """认领后 fresh 读取 Issue，解析该 Issue 的有效发布档位。

    解析表（逐 Issue 独立，绝不把结果共享给兄弟 Issue）：命中直发标签时
    ``NORMAL`` 与 ``DIRECT`` 都得到 ``DIRECT``，``FAST`` 明确拒绝冲突；未命中时沿用
    调用档位。标签只表达档位选择，不改变队列资格、优先级或认领归属。

    Args:
        requested_stage: 调用侧档位（CLI 旗标折算结果；守护进程与批量恒为 ``NORMAL``）。
        issue: 队列快照里的 Issue，仅用于定位；所有新选择必须 fresh 读取。
        config: 应用配置（提供标签名）。
        github_client: GitHub 端口，用于 fresh 读取。

    Returns:
        本次认领的有效档位决定。

    Raises:
        DirectPrFreshReadError: fresh Issue 读取失败，无论快照标签或请求档位都拒绝选择。
        DirectPrStageConflictError: FAST 调用档位与直发标签冲突。
    """
    label = configured_direct_pr_label(config)
    from backend.core.use_cases.agent_runner_direct_pr_round import read_direct_pr_round

    pending = read_direct_pr_round(github_client, issue)
    try:
        fresh_issue = github_client.get_issue(issue.number)
    except Exception as exc:
        raise DirectPrFreshReadError(
            f"Issue #{issue.number} could not be re-read after the claim; "
            f"refusing to select a publish stage (fail-closed): {exc}"
        ) from exc
    if pending is not None and not pending.handoff_complete and not pending.abandoned:
        _logger.info(
            "Issue #%d restoring DIRECT round %s (origin: unfinished publication).",
            issue.number,
            pending.round,
        )
        # 已开始的选择跨 fallback/恢复保持；标签删除后仍能完成原 DIRECT 交接。
        return PublishStageDecision(
            publish_stage=PublishStage.DIRECT,
            origin="unfinished direct-pr publication round",
            direct_pr_label=pending.label,
            issue=fresh_issue,
            requested_stage=requested_stage,
        )

    label_hit = label is not None and label in fresh_issue.labels
    if not label_hit:
        origin = (
            ORIGIN_RUN_FLAG if requested_stage is PublishStage.DIRECT else ORIGIN_CALLER_REQUEST
        )
        decision = PublishStageDecision(
            publish_stage=requested_stage,
            origin=origin,
            direct_pr_label=None,
            issue=fresh_issue,
        )
    elif requested_stage is PublishStage.FAST:
        raise DirectPrStageConflictError(
            f"Issue #{issue.number} carries the '{label}' label while the request asked for "
            "the fast track; the direct label widens the bypass beyond what --fast-merge "
            "declares, so neither is chosen. Remove the label to run the fast track, or "
            "rerun without --fast-merge to publish on the direct track."
        )
    else:
        origin = (
            ORIGIN_RUN_FLAG_AND_LABEL
            if requested_stage is PublishStage.DIRECT
            else ORIGIN_ISSUE_LABEL
        )
        decision = PublishStageDecision(
            publish_stage=PublishStage.DIRECT,
            origin=origin,
            direct_pr_label=label,
            issue=fresh_issue,
        )

    _logger.info(
        "Issue #%d publish stage resolved: %s (origin: %s).",
        issue.number,
        decision.publish_stage.value,
        decision.origin,
    )
    return decision


def ensure_direct_pr_admission(*, decision: PublishStageDecision) -> None:
    """直发准入：``DIRECT`` 必须是无 PRD 锚点的 Issue，规则与 CLI 入口同源。

    PRD-backed Issue 要走 PRD 交付门并归档 PRD，不能被「出 PR 快一点」旁路；标签不
    扩大旁路范围。拒绝时不执行 builder、不创建 PR、也不消费标签。

    Raises:
        DirectPrNotEligibleError: 目标带 PRD 锚点。
    """
    if decision.publish_stage is not PublishStage.DIRECT:
        return
    if (
        decision.requested_stage is PublishStage.FAST
        and decision.direct_pr_label in decision.issue.labels
    ):
        raise DirectPrStageConflictError(
            f"Issue #{decision.issue.number} carries a direct label while FAST was requested; "
            "only already-published current-round cleanup may bypass this conflict."
        )
    prd_path = extract_prd_path(decision.issue.body)
    if prd_path is None:
        return
    raise DirectPrNotEligibleError(
        f"Issue #{decision.issue.number} is PRD-backed (anchor: `{prd_path}`); the direct "
        "track is only defined for Issues without a PRD anchor, because a PRD-backed Issue "
        "must pass the PRD delivery gate and archive its PRD. Remove the "
        f"'{decision.direct_pr_label}' label to run it through the full gates."
    )


def ensure_direct_pr_dependencies_ready(
    decision: PublishStageDecision, config: AppConfig, github_client: IGitHubClient
) -> None:
    """开始新 DIRECT 工作前复用现有依赖门禁，已发布交接不调用本函数。

    Args:
        decision: 采用 fresh 正文的选择。
        config: 标签及依赖配置。
        github_client: 依赖读取端口。

    Returns:
        None。

    Raises:
        DirectPrNotEligibleError: fresh 正文中的依赖未满足。
    """
    from backend.core.use_cases.agent_runner_dependencies import (
        evaluate_dependencies,
        parse_dependency_marker,
    )

    if decision.publish_stage is not PublishStage.DIRECT:
        return
    declaration = parse_dependency_marker(decision.issue.body)
    if declaration is not None:
        verdict = evaluate_dependencies(declaration, github_client, config.labels)
        if not verdict.satisfied:
            raise DirectPrNotEligibleError(
                f"Issue #{decision.issue.number} has unresolved dependencies; DIRECT does not bypass them."
            )


def apply_direct_pr_label_policy(
    *,
    requested_stage: PublishStage,
    issue: IssueSummary,
    config: AppConfig,
    github_client: IGitHubClient,
) -> PublishStageDecision:
    """认领后的共同入口：fresh 解析档位并校验直发准入。

    三个入口（显式定向、批量队列、守护进程）都调用本函数，因此不存在逐入口复刻规则
    的漂移空间。
    """
    decision = resolve_publish_stage(
        requested_stage=requested_stage,
        issue=issue,
        config=config,
        github_client=github_client,
    )
    ensure_direct_pr_admission(decision=decision)
    return decision


def find_published_direct_pr(
    *,
    github_client: IGitHubClient,
    issue_number: int,
    branch: str,
    head_sha: str,
) -> str | None:
    """返回当前未完成检查点关联的 PR URL，历史同 head PR 不能提供消费授权。

    校验仓库、Issue、轮次的创建前基线、分支、最终 head、原 DIRECT marker 与已知
    URL，关联确认写入失败同样留待恢复。没有当前未完成检查点时返回 ``None``。
    """
    from backend.core.use_cases.agent_runner_direct_pr_round import (
        DirectPrPublicationCandidate,
        associated_direct_pr,
        read_direct_pr_round,
    )

    if not head_sha:
        return None
    fresh_issue = github_client.get_issue(issue_number)
    record = read_direct_pr_round(github_client, fresh_issue)
    if record is None or record.handoff_complete or record.abandoned:
        return None
    return associated_direct_pr(
        github_client, record, DirectPrPublicationCandidate(branch=branch, head=head_sha)
    )


def consume_direct_pr_label(
    *,
    github_client: IGitHubClient,
    issue_number: int,
    label: str,
    pr_url: str,
) -> None:
    """确认同次 PR 后消费直发标签：只移除该标签，其他标签一概不动。

    Raises:
        DirectPrLabelCleanupPendingError: 移除失败。PR 已经发布，因此报告「发布成功、
            标签清理待恢复」并保留 PR URL，交由下一轮认领补清理。
    """
    try:
        fresh_issue = github_client.get_issue(issue_number)
        if label not in fresh_issue.labels:
            _logger.info(
                "Issue #%d direct label was already consumed; continuing handoff.", issue_number
            )
            return
        github_client.edit_issue_labels(issue_number, add=[], remove=[label])
        confirmed_issue = github_client.get_issue(issue_number)
        if label in confirmed_issue.labels:
            raise RuntimeError("label removal was not visible in a fresh Issue read")
    except Exception as exc:
        raise DirectPrLabelCleanupPendingError(
            f"Draft PR {pr_url} for Issue #{issue_number} is published on the direct track, "
            f"but removing or confirming removal of the '{label}' label failed: {exc}. The next "
            "claim confirms the label state and finishes cleanup without republishing; "
            "do not treat this run as "
            "fully successful."
        ) from exc
    _logger.info(
        "Consumed the '%s' label on Issue #%d after confirming Draft PR %s.",
        label,
        issue_number,
        pr_url,
    )


@dataclass(frozen=True)
class DirectPrLabelPublicationRequest:
    """发布与恢复共用的标签收尾上下文。

    Attributes:
        github_client: GitHub 客户端。
        issue_number: 要完成交接的 Issue 编号。
        direct_pr_label: 当前轮次的直发标签；纯 CLI 选择为 None。
        candidate: 当前工作树的候选分支与提交。
        pr_url: 新发布时需核对的 PR URL；补交接时可省略。
    """

    github_client: IGitHubClient
    issue_number: int
    direct_pr_label: str | None
    candidate: DirectPrPublicationCandidate
    pr_url: str | None = None


def consume_label_after_publication(request: DirectPrLabelPublicationRequest) -> None:
    """发布链收尾：确认同次直发 PR 后消费标签。

    档位来自 CLI 旗标（Issue 上没有标签，``direct_pr_label`` 为 ``None``）时不写任何
    标签，保持旗标路径的原有副作用范围。确认不了 PR 与本次发布同次时同样抛「待恢复」，
    宁可保留标签让下一轮补清理，也不带着未验证的关联宣称成功。

    Args:
        request: 当前轮次的标签、候选提交及已发布 PR 上下文。

    Raises:
        DirectPrLabelCleanupPendingError: 同轮 PR 无法确认或标签消费失败。
    """
    github_client = request.github_client
    issue_number = request.issue_number
    direct_pr_label = request.direct_pr_label
    branch = request.candidate.branch
    head_sha = request.candidate.head
    pr_url = request.pr_url
    if direct_pr_label is None:
        return
    published_pr_url = find_published_direct_pr(
        github_client=github_client,
        issue_number=issue_number,
        branch=branch,
        head_sha=head_sha,
    )
    if published_pr_url is None or published_pr_url != pr_url:
        raise DirectPrLabelCleanupPendingError(
            f"Issue #{issue_number} was published with the '{direct_pr_label}' label (PR "
            f"{pr_url}), but no open PR on branch {branch} at head {head_sha} carries the "
            "matching `iar:direct-pr` marker, so the label is kept and this run is not a full "
            "success; the next claim confirms the published PR and only finishes the cleanup."
        )
    consume_direct_pr_label(
        github_client=github_client,
        issue_number=issue_number,
        label=direct_pr_label,
        pr_url=published_pr_url,
    )


def finish_label_cleanup_if_already_published(
    request: DirectPrLabelPublicationRequest,
) -> str | None:
    """崩溃或删除失败后的补清理：已有同次直发 PR 时只移除标签并返回该 PR URL。

    命中即返回，意味着本轮既不重新构建、也不重新创建 PR，也不启动一次新的 DIRECT
    执行；即使 Issue 内容在此期间被改动（例如补上了 PRD 锚点）也照常只补清理，因为
    要旁路的门禁根本不会运行。返回 ``None`` 表示没有已发布的同次 PR，调用方继续正常
    执行链（此时直发准入照常生效）。

    Args:
        request: 当前轮次的标签与候选提交上下文。

    Returns:
        已确认并完成标签消费的 PR URL；没有同轮已发布 PR 时为 None。

    Raises:
        DirectPrLabelCleanupPendingError: 已发布 PR 的标签消费失败。
    """
    github_client = request.github_client
    issue_number = request.issue_number
    direct_pr_label = request.direct_pr_label
    branch = request.candidate.branch
    head_sha = request.candidate.head
    if direct_pr_label is None:
        return None
    published_pr_url = find_published_direct_pr(
        github_client=github_client,
        issue_number=issue_number,
        branch=branch,
        head_sha=head_sha,
    )
    if published_pr_url is None:
        return None
    consume_direct_pr_label(
        github_client=github_client,
        issue_number=issue_number,
        label=direct_pr_label,
        pr_url=published_pr_url,
    )
    _logger.info(
        "Issue #%d already had its direct-track Draft PR %s published; only the '%s' label "
        "cleanup was performed, with no rebuild and no new PR.",
        issue_number,
        published_pr_url,
        direct_pr_label,
    )
    return published_pr_url


__all__ = [
    "ORIGIN_CALLER_REQUEST",
    "ORIGIN_ISSUE_LABEL",
    "ORIGIN_RUN_FLAG",
    "ORIGIN_RUN_FLAG_AND_LABEL",
    "DirectPrFreshReadError",
    "DirectPrLabelCleanupPendingError",
    "DirectPrLabelPublicationRequest",
    "DirectPrNotEligibleError",
    "DirectPrPolicyError",
    "DirectPrStageConflictError",
    "PublishStageDecision",
    "apply_direct_pr_label_policy",
    "configured_direct_pr_label",
    "consume_direct_pr_label",
    "consume_label_after_publication",
    "ensure_direct_pr_admission",
    "find_published_direct_pr",
    "finish_label_cleanup_if_already_published",
    "issue_carries_direct_pr_label",
    "resolve_publish_stage",
]
