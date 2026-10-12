"""recovery 耗尽时的失败交接记录与失败 Draft PR 发布。

本模块从 ``agent_runner_issue_handlers`` 拆出（沿用 #279 的超额文件拆分先例），
承载交接链路的三个副作用步骤：写交接评论 →（有安全 commit 才）以
``require_prd_archived=False`` 发布同源 Draft PR → 把回链附到 PR 正文尾部，
以及续作 prompt 回灌用的上一轮交接读取。

调用方是 :mod:`backend.core.use_cases.agent_runner_issue_handlers` 的
``_process_ready_issue`` 耗尽分支；测试替换本模块协作者（如
``publish_changes``、``format_failure_context_comment``）时，补丁点是**本模块**
——函数在这些名字上的全局解析发生在这里。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
)
from backend.core.use_cases.agent_runner_events import (
    find_latest_failure_context_comment,
    has_failure_context_marker,
)
from backend.core.use_cases.agent_runner_failure import (
    MaxRetriesExceededError,
    format_failure_context_comment,
)
from backend.core.use_cases.agent_runner_pr_handoff import (
    _build_pr_handoff_ref_block,
    _with_pr_handoff_ref,
)
from backend.core.use_cases.agent_runner_publish import publish_changes
from backend.core.use_cases.agent_runner_validation import (
    resolve_issue_evidence_relpath,
)

_logger = logging.getLogger(__name__)


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
