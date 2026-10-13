"""verdict 的 PR 侧副作用：GREEN 置 label、YELLOW 贴警告评论。

pre-PR 门禁只产出 verdict；这些副作用必须在 PR 创建**之后**才落地，所以与门禁
分开放——读门禁时不必混着 GitHub 客户端的调用形状。PR number 解析失败时记日志
并跳过，不阻断发布。
"""

from __future__ import annotations

import logging
import re

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.use_cases.agent_runner_verdict import ValidationVerdict

# 拆分前这些诊断由 ``run_verifier_agent`` 记录，操作者的 grep 与 docs/guides/agent-runner.md
# 的排查顺序都按那个名字找；保持 logger 身份不随模块搬家漂走。
_logger = logging.getLogger("backend.core.use_cases.run_verifier_agent")

_PR_URL_NUMBER_PATTERN = re.compile(r"/pull/(?P<number>\d+)")


def _extract_pr_number(pr_url: str) -> int | None:
    """Extract the PR number from a GitHub PR URL.

    Returns ``None`` when the URL does not contain a parseable PR number,
    so callers can skip the post-PR verifier side effects gracefully.
    """
    match = _PR_URL_NUMBER_PATTERN.search(pr_url)
    if match is None:
        return None
    return int(match.group("number"))


def build_verifier_yellow_comment(verdict: ValidationVerdict, issue_number: int) -> str:
    """Build the PR warning comment for a yellow verifier verdict.

    ``yellow`` 不阻断发布,但要在 PR 上贴一条可见的警告评论,把 verifier 的
    findings 交给人审者——避免"verifier 提了风险但没人看到"。评论带 hidden
    marker,后续可幂等更新或清除。
    """
    return "\n".join(
        [
            "<!-- iar:verifier-warning risk=yellow -->",
            "",
            "## ⚠️ Independent verifier returned YELLOW",
            "",
            f"The independent verifier ran for issue #{issue_number} and found a "
            "non-blocking risk. Review the findings below before merging:",
            "",
            "<details>",
            "<summary>Verifier findings</summary>",
            "",
            verdict.findings.strip() or "(no findings emitted)",
            "",
            "</details>",
        ]
    )


def apply_verifier_verdict_to_pr(
    pr_url: str,
    verdict: ValidationVerdict | None,
    issue_number: int,
    *,
    verifier_passed_label: str,
    github_client: IGitHubClient,
) -> None:
    """Apply the verifier verdict to the PR after it is created.

    pre-PR verifier 跑完后,verdict 的副作用在 PR 创建后落地:

    - ``green`` → 置 ``validation/verifier-passed`` label,为后续 autopilot
      合并队列提供显式状态位。
    - ``yellow`` → 贴警告评论(findings 给人审者看),不阻断发布。
    - ``None`` (verifier 未启用 / issue 不要求验证) → 无操作。
    - ``red`` 不会到这里(pre-PR 阶段已抛 ``ValidationEvidenceError`` 进 recovery)。

    PR number 从 ``pr_url`` 解析;解析失败时记日志并跳过,不阻断发布。
    """
    if verdict is None:
        return
    pr_number = _extract_pr_number(pr_url)
    if pr_number is None:
        _logger.warning(
            "Could not parse PR number from URL %r; skipping verifier verdict "
            "side effects for issue #%d.",
            pr_url,
            issue_number,
        )
        return
    if verdict.risk == "green":
        github_client.edit_issue_labels(pr_number, add=(verifier_passed_label,))
        _logger.info(
            "Verifier GREEN for issue #%d: set label %r on PR #%d.",
            issue_number,
            verifier_passed_label,
            pr_number,
        )
    elif verdict.risk == "yellow":
        github_client.comment_pr(pr_number, build_verifier_yellow_comment(verdict, issue_number))
        _logger.info(
            "Verifier YELLOW for issue #%d: posted warning comment on PR #%d.",
            issue_number,
            pr_number,
        )
