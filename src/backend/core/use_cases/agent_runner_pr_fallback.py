"""用提交事实与已跟踪证据构造不依赖 AI 的 PR 正文。"""

from pathlib import Path
from urllib.parse import quote

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.use_cases.generated_content import PrContext


def build_pr_fallback_body(
    context: PrContext,
    evidence_relpath: str,
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> str:
    """生成事实型发布正文，不推断测试结果或独立 verifier 结论。

    Args:
        context: Issue 与分支提交上下文。
        evidence_relpath: 该任务证据目录的仓库相对路径。
        worktree_path: 发布工作树。
        process_runner: Git 查询执行器。

    Returns:
        包含关联 Issue、变更范围、证据入口与风险说明的 Markdown。
    """
    head_result = process_runner.run(["git", "rev-parse", "HEAD"], cwd=worktree_path, check=False)
    publication_head = head_result.stdout.strip()
    # 查询同一个发布快照，不能把 index 或工作树中的文件链接到 HEAD。
    tracked_report_result = process_runner.run(
        ["git", "ls-tree", "-r", "--name-only", publication_head or "HEAD", "--", evidence_relpath],
        cwd=worktree_path,
        check=False,
    )
    report_paths = [
        report_path
        for report_path in tracked_report_result.stdout.splitlines()
        if report_path.endswith(
            (".verification-plan.md", ".evidence-report.md", ".verifier-report.md")
        )
    ]
    evidence_lines = [
        f"- [{Path(report_path).name}]({context.repo_url}/blob/{publication_head}/{quote(report_path)})"
        if context.repo_url and publication_head
        else f"- Repository path: `{report_path}`"
        for report_path in report_paths
    ]
    if not evidence_lines:
        evidence_lines = [
            "- No tracked validation reports were found for this task. "
            "Review the runner evidence comments and CI before merging."
        ]
    return "\n".join(
        [
            f"Closes #{context.issue_number}",
            "",
            "## Summary",
            "",
            context.issue_title,
            "",
            "Commit subjects (change descriptions, not validation results):",
            "",
            context.commit_log or "Commit log was not collected.",
            "",
            "```text",
            context.diff_stat or "Diff statistics were not collected.",
            "```",
            "",
            "## Validation",
            "",
            f"Publication HEAD: `{head_result.stdout.strip() or 'unavailable'}`.",
            *evidence_lines,
            "",
            "This generated description does not assert PASS. Check report conclusions "
            "and their recorded Git tree against the final PR head.",
            "",
            "## Risk",
            "",
            "Risk assessment is not inferred from commit messages. Review the linked "
            "Issue, evidence reports, and final diff for limitations and outstanding failures.",
            "",
            "## Reviewer Notes",
            "",
            "Confirm scope and validation evidence before merging. Human sign-off remains unchecked.",
            "",
        ]
    )
