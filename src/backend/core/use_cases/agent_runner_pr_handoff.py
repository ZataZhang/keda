"""失败交接信息在 Draft PR 正文中的格式化。"""

from __future__ import annotations

# Draft PR 正文里指向交接评论的那一段的锚点。有了它，重复耗尽时改写的是同一段而
# 不是越叠越长；也让"正文只是快照、评论才是事实源"这条边界在 PR 上写明。
_PR_HANDOFF_REF_MARKER = "<!-- kc:failure-context-ref -->"


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
            (
                f"This Draft PR was published from a **WIP checkpoint** after the runner "
                f"exhausted its recovery budget ({attempt_count} attempts). The commits "
                "here are mid-progress work, not a finished implementation, and the "
                "existence of this PR is not an acceptance result."
            ),
            "",
            record_line,
            (
                "- `validation/verifier-passed` is **absent**, so the existing sign-off, "
                "merge and archive gates keep refusing this PR by design. Nothing in this "
                "section changes that, and the gates are not modified by this feature."
            ),
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
