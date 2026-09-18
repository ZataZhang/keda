"""pre-PR review 结果评论的渲染。

从 ``agent_review.py`` 拆出：review gate 主体已接近单文件非空行上限，而"把一轮
审核结果渲染成 Issue 评论"是一块与编排无关的纯展示逻辑——包括本轮审核者/修复者
署名，以及审核者越权（写提交请求、只读却改文件）时的点名行。
"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import ReviewFinding
from backend.core.use_cases.agent_runner_events import format_event_marker


def build_pre_pr_review_result_comment(
    *,
    verdict: str,
    reviewer: str,
    head_before: str,
    head_after: str,
    verification_passed: bool,
    findings_high: int,
    findings_medium: int,
    findings_low: int,
    action_summary: str,
    cycle: int,
    findings: tuple[ReviewFinding, ...] = (),
    findings_critical: int = 0,
    repairer: str | None = None,
    reviewer_patch_ignored: bool = False,
    reviewer_left_changes: bool = False,
) -> str:
    """Build the human-readable comment for a pre-PR review result.

    ``repairer`` 仅在"审-修分工"模式（``pre_pr_review.repair_agent`` 非 ``self``）
    下传入，让操作者在 Issue 上一眼看本轮谁审、谁修；``reviewer_patch_ignored``
    用于点名审核者越权写出的提交请求已被丢弃，``reviewer_left_changes`` 用于点名
    审核者虽然只读却改了文件（这些改动不回滚，并入本轮修复者的提交）。
    """
    marker = format_event_marker(
        phase="pre_pr_review",
        cycle=cycle,
        head_sha=head_after,
    )
    verification_line = "passed" if verification_passed else "failed"
    counts_line = (
        f"- Findings: {findings_critical} critical, {findings_high} high, "
        f"{findings_medium} medium, {findings_low} low"
    )
    repair_lines: list[str] = []
    if repairer is not None:
        repair_lines.append(f"- Repairer: {repairer}")
    sections = [
        marker,
        "",
        "## Agent Runner Pre-PR Review",
        "",
        f"- Verdict: {verdict}",
        f"- Reviewer: {reviewer}",
        *repair_lines,
        f"- Head Before: `{head_before}`",
        f"- Head After: `{head_after}`",
        f"- Verification: {verification_line}",
        counts_line,
        f"- Action: {action_summary}",
    ]
    if reviewer_patch_ignored:
        sections.extend(
            [
                "",
                "Reviewer ran in read-only mode but wrote a commit request; it was "
                "discarded (no commit was made from it).",
            ]
        )
    if reviewer_left_changes:
        sections.extend(
            [
                "",
                "Reviewer edited files despite read-only mode; those edits were not "
                "rolled back and are carried into this cycle's repair commit.",
            ]
        )
    if findings:
        sections.append("")
        sections.append("### Findings")
        sections.append("")
        sections.append("| Severity | Category | File | Line | Title | Recommendation |")
        sections.append("|---|---|---|---|---|---|")
        for finding in findings:
            sections.append(
                "| {sev} | {cat} | {file} | {line} | {title} | {rec} |".format(
                    sev=_escape_cell(finding.severity or "-"),
                    cat=_escape_cell(finding.category or "-"),
                    file=_escape_cell(finding.file or "-"),
                    line=finding.line if finding.line else "-",
                    title=_escape_cell(finding.title or "-"),
                    rec=_escape_cell(finding.recommendation or "-"),
                )
            )
    return "\n".join(sections)


def _escape_cell(value: str) -> str:
    """Escape a value so it can be safely embedded in a markdown table cell."""
    return value.replace("|", "\\|").replace("\n", " ").strip() or "-"


__all__ = [
    "build_pre_pr_review_result_comment",
]
