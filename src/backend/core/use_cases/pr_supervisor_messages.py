"""Post-PR supervisor 使用的评论正文与冲突解决提示词。"""

from __future__ import annotations

from typing import Literal

from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.agent_runner_events import format_event_marker


def build_supervisor_result_comment(
    *,
    action: str,
    supervisor: str,
    summary: str,
    findings_counts: dict[str, int],
    verification_status: str,
    head_sha: str | None,
    cycle: int,
    base_sha: str | None = None,
    checks_state: str | None = None,
    mergeable: bool | None = None,
    issue_comments_count: int | None = None,
    pr_comments_count: int | None = None,
) -> str:
    """构造一次 post-PR supervisor 决策的 Issue 评论。

    Args:
        action: supervisor 选择的动作。
        supervisor: 执行审查的 agent 名称。
        summary: 决策摘要。
        findings_counts: 按严重级别统计的 finding 数量。
        verification_status: 本轮验证状态。
        head_sha: 审查时的 PR head。
        cycle: supervisor 周期编号。
        base_sha: 审查时的 PR base。
        checks_state: GitHub checks 的聚合状态。
        mergeable: GitHub 对 PR 可合并性的判断。
        issue_comments_count: Issue 评论游标。
        pr_comments_count: PR 评论游标。

    Returns:
        带有机器可读事件 marker 和人类可读摘要的评论正文。
    """
    marker = format_event_marker(
        phase="post_pr_supervisor",
        cycle=cycle,
        head_sha=head_sha,
        base_sha=base_sha,
        action=action,
        checks_state=checks_state,
        mergeable=mergeable,
        issue_comments_count=issue_comments_count,
        pr_comments_count=pr_comments_count,
    )
    high = findings_counts.get("high", 0)
    medium = findings_counts.get("medium", 0)
    low = findings_counts.get("low", 0)
    return "\n".join(
        [
            marker,
            "",
            "## Agent Runner Post-PR Supervisor",
            "",
            f"- Action: {action}",
            f"- Supervisor: {supervisor}",
            f"- Summary: {summary}",
            f"- Findings: {high} high, {medium} medium, {low} low",
            f"- Verification: {verification_status or 'unknown'}",
            f"- Head SHA: `{head_sha or 'N/A'}`",
        ]
    )


def build_rework_intent_comment(
    *,
    action: str,
    pr_branch: str,
    head_sha: str,
    execution_owner: Literal["runner", "supervisor"] = "runner",
) -> str:
    """构造 post-PR 返工意图评论。

    Args:
        action: 返工动作。
        pr_branch: 目标 PR 分支。
        head_sha: 请求返工时的 PR head。
        execution_owner: 负责执行返工的生命周期阶段。

    Returns:
        带有机器可读 marker 和执行者说明的评论正文。
    """
    marker = format_event_marker(
        phase="post_pr_rework_requested",
        cycle=1,
        head_sha=head_sha,
        pr_branch=pr_branch,
        action=action,
    )
    owner_note = (
        "- The supervisor will run this repair while the Issue stays in `agent/supervising`."
        if execution_owner == "supervisor"
        else "- A runner will pick this up on the next `kc run` pass."
    )
    return "\n".join(
        [
            marker,
            "",
            "## Agent Runner Post-PR Rework Requested",
            "",
            f"- Action: {action}",
            f"- PR Branch: `{pr_branch}`",
            f"- Head SHA: `{head_sha}`",
            owner_note,
        ]
    )


def build_rework_superseded_comment(
    *,
    action: str | None,
    pr_branch: str | None,
    requested_head: str | None,
    current_head: str | None,
) -> str:
    """记录无法安全续跑的返工请求及当前 PR 状态。

    Args:
        action: 原返工动作。
        pr_branch: 目标 PR 分支（若可识别）。
        requested_head: 返工请求绑定的 head。
        current_head: 当前 PR head。

    Returns:
        带有 superseded marker 的 Issue 评论正文。
    """
    marker = format_event_marker(
        phase="post_pr_rework_superseded",
        cycle=1,
        head_sha=current_head,
        pr_branch=pr_branch,
        action=action,
    )
    return "\n".join(
        [
            marker,
            "",
            "## Agent Runner Post-PR Rework Superseded",
            "",
            "The pending repair request could not be safely resumed and was not executed.",
            f"- Action: {action or 'unknown'}",
            f"- PR Branch: `{pr_branch}`" if pr_branch else "- PR Branch unavailable",
            f"- Requested Head: `{requested_head or 'unknown'}`",
            f"- Current Head: `{current_head or 'unknown'}`",
            "- The supervisor will review the current PR context.",
        ]
    )


def build_rebase_repair_complete_comment(
    *,
    action: str,
    head_sha: str,
    verification_passed: bool,
) -> str:
    """构造 rebase 或 repair 完成后的 Issue 评论。

    Args:
        action: 已完成的动作。
        head_sha: 操作后的 PR head。
        verification_passed: 本地验证是否通过。

    Returns:
        带有完成 marker 和验证状态的评论正文。
    """
    marker = format_event_marker(
        phase="rebase_repair_complete",
        cycle=1,
        head_sha=head_sha,
    )
    return "\n".join(
        [
            marker,
            "",
            "## Agent Runner Rebase/Repair Complete",
            "",
            f"- Action: {action}",
            f"- Head SHA: `{head_sha}`",
            f"- Verification: {'passed' if verification_passed else 'failed'}",
        ]
    )


def build_conflict_resolution_prompt(
    issue: IssueSummary,
    pr_branch: str,
    expected_head: str,
    conflicted_files: list[str],
) -> str:
    """构造供 agent 解决 PR 分支 rebase 冲突的提示词。

    Args:
        issue: 关联 Issue。
        pr_branch: 发生冲突的 PR 分支。
        expected_head: 执行 rebase 前预期的 head。
        conflicted_files: Git 标记的冲突文件路径。

    Returns:
        限定操作范围并要求提交请求文件的 agent 提示词。
    """
    files_text = "\n".join(f"- {file_path}" for file_path in conflicted_files) or "(none)"
    return "\n".join(
        [
            f"Resolve rebase conflicts for Issue #{issue.number}: {issue.title}",
            "",
            f"Issue URL: {issue.url}",
            f"PR Branch: `{pr_branch}`",
            f"Expected HEAD: `{expected_head}`",
            "",
            "The rebase onto the remote base branch encountered conflicts in these files:",
            files_text,
            "",
            "Resolve all conflicts and request a commit.",
            "- Only modify conflicted files inside the current worktree.",
            "- Do not switch branches, push, or abort the rebase.",
            "- Do not run `git add` or `git commit`; the runner handles staging.",
            "- After resolving conflicts, write `.agent-runner/commit-request.json` "
            "as JSON with `commit_message`.",
        ]
    )
