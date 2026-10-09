"""Pull-request-side operations for the GitHub CLI client.

Module-level helper functions that drive ``gh pr ...`` invocations. They
take a client-like first argument so the main
:class:`backend.infrastructure.github_client.GitHubCliClient` can keep its
method-level public surface while delegating the actual ``gh`` command
construction here.

Backward compatibility: ``GitHubCliClient.create_draft_pr`` etc. continue
to exist on the class as thin pass-throughs.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, Sequence

from backend.infrastructure.github_helpers import (
    _STATE_ORDER,
    _aggregate_status_check_rollup,
    _normalize_mergeable,
    _parse_pr_summary,
)
from backend.infrastructure.github_models import PullRequestContext, PullRequestSummary

_logger = logging.getLogger(__name__)


class _ClientProtocol(Protocol):
    """Duck-typed interface expected by the PR-side helpers.

    Implemented by :class:`GitHubCliClient`; the helpers only touch the
    methods needed to drive ``gh`` invocations.
    """

    repo_path: Path

    def _run_with_retry(
        self, command: Sequence[str], *, cwd: Path, check: bool = True
    ) -> object: ...

    def _write_body_file(self, temp_dir: str, filename: str, body: str) -> Path: ...


def create_draft_pr(
    client: _ClientProtocol,
    *,
    title: str,
    body: str,
    base_branch: str,
    cwd: Path,
) -> str:
    """Create a draft pull request from the current branch."""
    with tempfile.TemporaryDirectory(prefix="kc-pr-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "pr.md", body)
        result = client._run_with_retry(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                base_branch,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ],
            cwd=cwd,
        )
    return result.stdout.strip().splitlines()[-1]


def get_pull_request_context(
    client: _ClientProtocol, branch: str, *, require_success: bool = False
) -> PullRequestContext | None:
    """读取开放 PR 上下文，可要求成功查询才能证明 PR 不存在。

    Args:
        client: 当前仓库的 GitHub CLI 客户端。
        branch: 要查询的远端 head 分支。
        require_success: 是否拒绝查询失败、空响应及无法确认身份的返回结构。

    Returns:
        开放 PR 上下文；成功查询返回空列表时为 None。默认模式保留失败返回 None。

    Raises:
        RuntimeError: 严格模式不能确认查询成功或返回的 PR 身份。
    """
    result = client._run_with_retry(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url,number,body,headRefName,headRefOid,baseRefOid,mergeable,statusCheckRollup",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        if require_success:
            raise RuntimeError(f"Cannot establish open PR context for branch {branch}.")
        _logger.warning(
            "Unable to load full PR context for branch %s: %s",
            branch,
            result.stderr.strip() or f"gh exited with status {result.return_code}",
        )
        return None
    if require_success and not result.stdout.strip():
        raise RuntimeError("Authoritative PR query returned an empty response.")
    try:
        raw_prs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        if require_success:
            raise RuntimeError("Authoritative PR query returned malformed JSON.") from exc
        raise
    if require_success:
        if not isinstance(raw_prs, list) or len(raw_prs) > 1:
            raise RuntimeError("Authoritative PR query returned an ambiguous or invalid list.")
        if raw_prs:
            raw_pr = raw_prs[0]
            if (
                not isinstance(raw_pr, dict)
                or not isinstance(raw_pr.get("url"), str)
                or not raw_pr["url"].startswith("https://")
                or type(raw_pr.get("number")) is not int
                or raw_pr["number"] <= 0
                or raw_pr.get("headRefName") != branch
                or not isinstance(raw_pr.get("headRefOid"), str)
                or len(raw_pr["headRefOid"]) != 40
                or any(
                    character not in "0123456789abcdefABCDEF" for character in raw_pr["headRefOid"]
                )
                or not isinstance(raw_pr.get("body"), str)
            ):
                raise RuntimeError("Authoritative PR query returned invalid PR identity metadata.")
    if not raw_prs:
        return None
    raw_pr = raw_prs[0]
    checks_state, checks_summary = _aggregate_status_check_rollup(raw_pr.get("statusCheckRollup"))
    raw_pr_number = raw_pr.get("number")
    return PullRequestContext(
        pr_url=str(raw_pr.get("url", "")),
        branch=str(raw_pr.get("headRefName", branch)),
        head_sha=str(raw_pr.get("headRefOid", "")),
        base_sha=str(raw_pr.get("baseRefOid", "")),
        mergeable=_normalize_mergeable(raw_pr.get("mergeable")),
        checks_state=checks_state,
        checks_summary=checks_summary,
        number=int(raw_pr_number) if raw_pr_number is not None else None,
        body=str(raw_pr.get("body", "") or ""),
    )


def comment_pr(client: _ClientProtocol, pr_number: int, body: str) -> None:
    """Post a Markdown comment to a Pull Request."""
    with tempfile.TemporaryDirectory(prefix="kc-pr-comment-") as temp_dir:
        comment_path = client._write_body_file(temp_dir, "comment.md", body)
        client._run_with_retry(
            [
                "gh",
                "pr",
                "comment",
                str(pr_number),
                "--body-file",
                str(comment_path),
            ],
            cwd=client.repo_path,
        )


def update_pull_request_body(client: _ClientProtocol, pr_number: int, body: str) -> None:
    """Replace the description body of a Pull Request."""
    with tempfile.TemporaryDirectory(prefix="kc-pr-body-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "body.md", body)
        client._run_with_retry(
            [
                "gh",
                "pr",
                "edit",
                str(pr_number),
                "--body-file",
                str(body_path),
            ],
            cwd=client.repo_path,
        )


def set_pull_request_base(client: _ClientProtocol, pr_number: int, base_branch: str) -> None:
    """Retarget a Pull Request's base branch.

    Used by stack convergence: once the upstream branch merges into the
    mainline, the downstream PR's base is moved from ``issue-<upstream>`` to the
    mainline before it is rebased and merged.

    Args:
        pr_number: Target Pull Request number.
        base_branch: New base branch name (e.g. ``main``).
    """
    client._run_with_retry(
        [
            "gh",
            "pr",
            "edit",
            str(pr_number),
            "--base",
            base_branch,
        ],
        cwd=client.repo_path,
    )


def merge_pull_request(client: _ClientProtocol, pr_number: int, *, method: str = "squash") -> None:
    """Merge a Pull Request using ``gh pr merge`` with the requested method.

    ``method`` only accepts ``"squash"`` for now. Squashing gives a single
    revert-friendly commit on the base branch and matches the merge queue
    PRD's hard requirement.

    Already-merged responses are treated as idempotent success so the
    merge queue can safely re-enter after a daemon crash without throwing.

    Args:
        pr_number: Target Pull Request number.
        method: Merge method; must be ``"squash"``.

    Raises:
        ValueError: When ``method`` is not ``"squash"``.
        RuntimeError: When ``gh pr merge`` exits non-zero with anything
            other than the idempotent-already-merged case.
    """
    if method != "squash":
        raise ValueError(f"merge_pull_request method must be 'squash'; got {method!r}.")
    try:
        client._run_with_retry(
            ["gh", "pr", "merge", str(pr_number), "--squash"],
            cwd=client.repo_path,
            check=False,
        )
    except subprocess.CalledProcessError as exc:
        combined_output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        if "Already merged" in combined_output or "already merged" in combined_output:
            _logger.info(
                "PR #%d is already merged; treating merge request as no-op.",
                pr_number,
            )
            return
        raise RuntimeError(
            f"gh pr merge failed for PR #{pr_number}: "
            f"{(exc.stderr or '').strip() or (exc.stdout or '').strip()}"
        ) from exc


def list_pr_comments(client: _ClientProtocol, pr_number: int) -> list[str]:
    """Return raw comment bodies for a PR."""
    result = client._run_with_retry(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--comments",
            "--json",
            "comments",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        return []
    raw_data = json.loads(result.stdout or "{}")
    comments = raw_data.get("comments", [])
    return [str(c.get("body", "")) for c in comments if c.get("body")]


def find_open_pr_by_head(
    client: _ClientProtocol, branch: str, *, require_success: bool = False
) -> str | None:
    """若分支存在开放 PR，则返回其 URL。"""
    return _find_pr_by_head_state(client, branch, state="open", require_success=require_success)


def find_merged_pr_by_head(
    client: _ClientProtocol, branch: str, *, require_success: bool = False
) -> str | None:
    """若分支存在已合并 PR，则返回其 URL。"""
    return _find_pr_by_head_state(client, branch, state="merged", require_success=require_success)


def _find_pr_by_head_state(
    client: _ClientProtocol,
    branch: str,
    *,
    state: str,
    require_success: bool,
) -> str | None:
    """按来源分支和 PR 状态查询 URL，并可要求响应足以证明查询成功。"""
    result = client._run_with_retry(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            state,
            "--json",
            "url",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        if require_success:
            raise RuntimeError(f"Cannot query {state} PRs for branch {branch}.")
        return None
    if require_success and not result.stdout.strip():
        raise RuntimeError(
            f"{state.title()} PR query returned an empty response for branch {branch}."
        )
    raw_prs = json.loads(result.stdout or "[]")
    if require_success and (
        not isinstance(raw_prs, list)
        or any(
            not isinstance(raw_pr, dict)
            or not isinstance(raw_pr.get("url"), str)
            or not raw_pr["url"].startswith("https://")
            for raw_pr in raw_prs
        )
    ):
        raise RuntimeError(
            f"{state.title()} PR query returned an incomplete response for branch {branch}."
        )
    if not isinstance(raw_prs, list):
        return None
    if not raw_prs:
        return None
    return str(raw_prs[0].get("url", ""))


def get_remote_base_sha(client: _ClientProtocol, remote: str, base_branch: str) -> str:
    """Return the SHA of the remote base branch."""
    result = client._run_with_retry(
        [
            "git",
            "rev-parse",
            f"{remote}/{base_branch}",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        return ""
    return result.stdout.strip()


def list_pull_requests_for_issue(
    client: _ClientProtocol, repo: str, issue_number: int
) -> list[PullRequestSummary]:
    """List PRs that reference or close the given Issue.

    Uses ``gh pr list --search`` to find PRs whose body or commits
    mention the Issue via closing keywords. State is normalised to
    one of ``"open"`` / ``"draft"`` / ``"merged"`` / ``"closed"``.
    """
    search_query = (
        f"closes:#{issue_number} OR fixes:#{issue_number} "
        f"OR resolves:#{issue_number} OR refs:#{issue_number}"
    )
    command = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--search",
        search_query,
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,title,state,url,isDraft,mergedAt",
    ]
    result = client._run_with_retry(command, cwd=client.repo_path)
    raw_prs = json.loads(result.stdout or "[]")
    pulls = [_parse_pr_summary(raw_pr) for raw_pr in raw_prs]
    pulls.sort(key=lambda pull: (_STATE_ORDER.get(pull.state, 99), pull.number))
    return pulls


__all__ = [
    "comment_pr",
    "create_draft_pr",
    "find_merged_pr_by_head",
    "find_open_pr_by_head",
    "get_pull_request_context",
    "get_remote_base_sha",
    "list_pr_comments",
    "list_pull_requests_for_issue",
    "merge_pull_request",
    "update_pull_request_body",
]
