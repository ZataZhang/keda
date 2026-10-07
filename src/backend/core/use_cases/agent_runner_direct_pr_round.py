"""用既有 Issue 评论保留直发发布的轮次与交接检查点。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.agent_runner_dependencies import parse_direct_pr_marker

_ROUND_MARKER = re.compile(r"<!-- iar:direct-pr-round (\{[^\n]*\}) -->")


class DirectPrRoundError(RuntimeError):
    """无法证明本轮发布关联时保留标签，拒绝创建或完整成功。"""


@dataclass(frozen=True)
class DirectPrRound:
    """一个已认领、尚待发布或交接的标签选择。

    Attributes:
        comment_id: 既有 Issue 评论 ID，用于更新同一检查点。
        repo: 关联仓库，拒绝搬运其他仓库的记录。
        issue: Issue 编号。
        round: 初始认领评论 ID，恢复认领不会改变它。
        label: 当轮选中的标签，之后配置变化不改已开始的发布。
        branch: 候选发布分支。
        head: 候选提交。
        pr_url: 已确认的同轮 PR。
        prior_pr_absent: 创建前已确认分支无旧 PR。
        handoff_complete: 标签消费及 workflow 交接都完成。
        abandoned: 尚无候选发布的新 ready 轮次已取代该选择。
    """

    comment_id: int
    repo: str
    issue: int
    round: str
    label: str
    branch: str = ""
    head: str = ""
    pr_url: str | None = None
    prior_pr_absent: bool = False
    handoff_complete: bool = False
    abandoned: bool = False


@dataclass(frozen=True)
class DirectPrPublicationCandidate:
    """当前要发布的分支与提交。"""

    branch: str
    head: str


def _repo_identity(issue: IssueSummary) -> str:
    """从 Issue 的规范 URL 提取仓库身份。"""
    issue_url_prefix, separator, issue_suffix = issue.url.partition("/issues/")
    if not separator or issue_suffix.split("/", 1)[0] != str(issue.number):
        raise DirectPrRoundError(f"Issue #{issue.number} has no canonical repository URL.")
    return issue_url_prefix.rstrip("/").lower()


def _round_body(record: DirectPrRound) -> str:
    """渲染同一评论中的轻量检查点，不改变原 DIRECT PR marker。"""
    payload = asdict(record)
    payload.pop("comment_id")
    payload["version"] = 1
    payload["stage"] = "DIRECT"
    return (
        "## Agent Runner Direct PR Publication\n\n"
        f"- Round: `{record.round}`\n"
        f"- Publication: {record.pr_url or 'not confirmed'}\n"
        f"- Handoff complete: {record.handoff_complete}\n\n"
        f"<!-- iar:direct-pr-round {json.dumps(payload, ensure_ascii=True, sort_keys=True)} -->"
    )


def read_direct_pr_round(github_client: IGitHubClient, issue: IssueSummary) -> DirectPrRound | None:
    """读取最后一个当前 Issue 的检查点，已完成轮次不提供新发布授权。

    Args:
        github_client: 绑定仓库的 GitHub 端口。
        issue: 当前 Issue 的规范身份。

    Returns:
        最新检查点；未曾使用标签直发时为 ``None``。

    Raises:
        DirectPrRoundError: 评论读取或关联身份无法确认。
    """
    try:
        entries = github_client.list_issue_comment_entries(
            issue.number, trusted_only=True, body_contains="<!-- iar:direct-pr-round "
        )
        for comment_id, comment_body in reversed(entries):
            matched = _ROUND_MARKER.search(comment_body)
            if not matched:
                continue
            payload = json.loads(matched.group(1))
            if (
                payload.pop("version") != 1
                or payload.pop("stage") != "DIRECT"
                or payload.get("repo") != _repo_identity(issue)
                or payload.get("issue") != issue.number
            ):
                raise ValueError("publication checkpoint belongs to another repository or Issue")
            record = DirectPrRound(comment_id=comment_id, **payload)
            if (
                not all(
                    isinstance(value, str)
                    for value in (
                        record.repo,
                        record.round,
                        record.label,
                        record.branch,
                        record.head,
                    )
                )
                or not record.round
                or not record.label
                or not all(
                    type(value) is bool
                    for value in (record.prior_pr_absent, record.handoff_complete, record.abandoned)
                )
                or (record.pr_url is not None and not isinstance(record.pr_url, str))
            ):
                raise ValueError("publication checkpoint has invalid identity or checkpoint fields")
            return record
    except Exception as exc:
        raise DirectPrRoundError(
            f"Cannot read Direct PR checkpoint for Issue #{issue.number}: {exc}"
        ) from exc
    return None


def save_direct_pr_selection(
    github_client: IGitHubClient,
    issue: IssueSummary,
    label: str,
    *,
    claim_comment_id: int | None = None,
) -> DirectPrRound:
    """在准入成功且持有认领后保存选择，fallback 与恢复复用原轮次。

    Args:
        github_client: GitHub 端口。
        issue: 已 fresh 读取且准入成功的 Issue。
        label: 本次选择的标签。
        claim_comment_id: 首次 CAS 赢家的认领评论 ID；恢复入口可使用选择评论本身。

    Returns:
        已回读确认的未完成检查点。

    Raises:
        DirectPrRoundError: 写入或回读失败，不得派发 builder。
    """
    pending = read_direct_pr_round(github_client, issue)
    if pending is not None and not pending.handoff_complete and not pending.abandoned:
        return pending
    record = DirectPrRound(
        comment_id=0,
        repo=_repo_identity(issue),
        issue=issue.number,
        round=str(claim_comment_id) if claim_comment_id else uuid4().hex,
        label=label,
    )
    try:
        github_client.comment_issue(issue.number, _round_body(record))
        persisted = read_direct_pr_round(github_client, issue)
        if persisted is None or persisted.round != record.round:
            raise ValueError("written publication checkpoint was not visible")
        return persisted
    except Exception as exc:
        raise DirectPrRoundError(
            f"Cannot persist Direct PR choice for Issue #{issue.number}: {exc}"
        ) from exc


def _save_round(github_client: IGitHubClient, record: DirectPrRound) -> DirectPrRound:
    """只更新当前评论，任何失败保留未完成语义。"""
    try:
        expected_body = _round_body(record)
        github_client.edit_issue_comment(record.comment_id, expected_body)
        entries = github_client.list_issue_comment_entries(
            record.issue, trusted_only=True, body_contains="<!-- iar:direct-pr-round "
        )
        persisted_body = next(
            (body for comment_id, body in entries if comment_id == record.comment_id), None
        )
        if persisted_body != expected_body:
            raise ValueError("updated publication checkpoint was not visible")
    except Exception as exc:
        raise DirectPrRoundError(f"Cannot update Direct PR round {record.round}: {exc}") from exc
    return record


def lookup_associated_direct_pr(
    github_client: IGitHubClient,
    record: DirectPrRound,
    candidate: DirectPrPublicationCandidate,
) -> str | None:
    """确认当前未完成轮次的 PR；同 head 的历史 PR 不构成关联。

    Args:
        github_client: GitHub 端口。
        record: 当前未完成轮次。
        candidate: 当前本地发布候选。

    Returns:
        匹配的开放 PR URL，或者没有已发布 PR 时的 ``None``。

    Raises:
        DirectPrRoundError: 已有 PR 与当前检查点不一致或读取失败。
    """
    if record.handoff_complete or record.abandoned or not record.branch or not record.head:
        return None
    if record.branch != candidate.branch or record.head != candidate.head:
        raise DirectPrRoundError(
            "Direct PR candidate differs from the unfinished publication round."
        )
    try:
        context = github_client.get_pull_request_context(candidate.branch, require_success=True)
        existing_url = github_client.find_open_pr_by_head(candidate.branch)
    except Exception as exc:
        raise DirectPrRoundError(f"Cannot confirm Direct PR publication: {exc}") from exc
    if context is None:
        if existing_url is not None or record.pr_url is not None:
            raise DirectPrRoundError("Recorded Direct PR is not available as a matching open PR.")
        return None
    if (
        not record.prior_pr_absent
        or parse_direct_pr_marker(context.body) != record.issue
        or context.branch != candidate.branch
        or context.head_sha != candidate.head
        or context.pr_url != existing_url
        or (record.pr_url is not None and record.pr_url != context.pr_url)
        or not context.pr_url.lower().startswith(record.repo + "/pull/")
    ):
        raise DirectPrRoundError("Existing PR does not belong to this unfinished Direct PR round.")
    return context.pr_url


def associated_direct_pr(
    github_client: IGitHubClient, record: DirectPrRound, candidate: DirectPrPublicationCandidate
) -> str | None:
    """认领赢家确认同轮 PR 后持久化 URL，读取与写入失败均保留待恢复语义。

    Args:
        github_client: GitHub 端口。
        record: 当前检查点。
        candidate: 本轮分支与提交。

    Returns:
        已确认并持久化的 PR URL，或 ``None``。

    Raises:
        DirectPrRoundError: PR 不匹配或关联无法持久化。
    """
    pr_url = lookup_associated_direct_pr(github_client, record, candidate)
    if pr_url is not None and record.pr_url is None:
        _save_round(github_client, replace(record, pr_url=pr_url))
    return pr_url


def has_readonly_direct_pr_cleanup(github_client: IGitHubClient, issue: IssueSummary) -> bool:
    """CLI 只读预检仅豁免已发布同轮 PR 的交接，不写记录也不消费标签。

    Args:
        github_client: GitHub 端口。
        issue: fresh Issue。

    Returns:
        是否已有完整匹配且尚未交接的成功 PR。

    Raises:
        DirectPrRoundError: 关联读取失败或记录不一致。
    """
    record = read_direct_pr_round(github_client, issue)
    if record is None or record.handoff_complete or record.abandoned or not record.branch:
        return False
    return (
        lookup_associated_direct_pr(
            github_client, record, DirectPrPublicationCandidate(record.branch, record.head)
        )
        is not None
    )


def prepare_direct_pr_publication(
    github_client: IGitHubClient,
    issue: IssueSummary,
    candidate: DirectPrPublicationCandidate,
) -> str | None:
    """创建 PR 前持久候选及旧 PR 不存在的事实，或恢复已关联 PR。

    Args:
        github_client: GitHub 端口。
        issue: 当前 Issue。
        candidate: 即将创建 PR 的分支与最终提交。

    Returns:
        已发布的同轮 PR URL；否则允许后续创建并返回 ``None``。

    Raises:
        DirectPrRoundError: 存在历史 PR、候选不匹配或检查点无法持久化。
    """
    record = read_direct_pr_round(github_client, issue)
    if record is None or record.handoff_complete or record.abandoned:
        return None
    if record.branch:
        return associated_direct_pr(github_client, record, candidate)
    try:
        previous_context = github_client.get_pull_request_context(
            candidate.branch, require_success=True
        )
        previous_url = github_client.find_open_pr_by_head(candidate.branch)
    except Exception as exc:
        raise DirectPrRoundError(f"Cannot establish absence of a previous PR: {exc}") from exc
    if previous_context is not None or previous_url is not None:
        raise DirectPrRoundError(
            "An older PR already exists; refusing to bind it to a new Direct PR choice."
        )
    _save_round(
        github_client,
        replace(record, branch=candidate.branch, head=candidate.head, prior_pr_absent=True),
    )
    return None


def complete_direct_pr_round(github_client: IGitHubClient, issue: IssueSummary) -> None:
    """标签消费和 workflow 交接均成功后完成当前检查点。

    Args:
        github_client: GitHub 端口。
        issue: 已移交的 Issue。

    Returns:
        None。

    Raises:
        DirectPrRoundError: 已确认的 PR 不存在或完成写入失败，留待下轮补交接。
    """
    record = read_direct_pr_round(github_client, issue)
    if record is None or record.handoff_complete or record.abandoned:
        return
    if not record.pr_url:
        raise DirectPrRoundError("Cannot complete an unconfirmed Direct PR publication.")
    _save_round(github_client, replace(record, handoff_complete=True))


def reset_unstarted_direct_pr_selection(github_client: IGitHubClient, issue: IssueSummary) -> None:
    """显式新 ready 轮次重选尚未到候选发布的旧选择。

    fallback 调用方必须通过本次执行选择缓存跳过这里；host/pid 不能区分同一 daemon
    中不同 ready 轮次，因此不把进程身份当轮次。running/blocked 恢复不调用本函数。

    Args:
        github_client: GitHub 端口。
        issue: 本次 ready 队列快照。

    Returns:
        None。
    """
    record = read_direct_pr_round(github_client, issue)
    if record is None or record.handoff_complete or record.abandoned or record.branch:
        return
    _save_round(github_client, replace(record, abandoned=True))
