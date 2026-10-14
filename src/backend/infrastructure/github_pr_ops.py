"""Pull-request-side operations for the GitHub CLI client.

Module-level helper functions that drive ``gh api`` and ``gh pr ...`` calls. They
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
from urllib.parse import urlencode

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
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

    def _get_owner_repo(self) -> str: ...

    def _run_with_retry(
        self, command: Sequence[str], *, cwd: Path, check: bool = True
    ) -> object: ...

    def _write_body_file(self, temp_dir: str, filename: str, body: str) -> Path: ...


def _request_rest_json(
    client: _ClientProtocol,
    endpoint: str,
    *,
    paginate: bool = False,
    per_page: bool = False,
) -> object:
    """通过 REST API 读取 JSON，并在查询失败时保留可诊断错误。"""
    command = ["gh", "api", "-X", "GET"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    command.append(endpoint)
    if per_page or paginate:
        command.extend(["-F", "per_page=100"])
    result = client._run_with_retry(command, cwd=client.repo_path, check=False)
    if result.return_code != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "GitHub REST query failed"
        )
    if not result.stdout.strip():
        raise RuntimeError("GitHub REST query returned an empty response.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub REST query returned malformed JSON.") from exc


def _flatten_rest_pages(raw_pages: object, *, response_key: str | None = None) -> list[dict]:
    """合并 ``gh api --paginate --slurp`` 返回的分页内容。"""
    if not isinstance(raw_pages, list):
        raise RuntimeError("GitHub REST pagination returned an invalid page list.")
    entries: list[dict] = []
    for raw_page in raw_pages:
        page_entries = raw_page
        if response_key is not None:
            if not isinstance(raw_page, dict):
                raise RuntimeError("GitHub REST pagination returned an invalid page object.")
            page_entries = raw_page.get(response_key)
        if not isinstance(page_entries, list):
            raise RuntimeError("GitHub REST pagination returned an invalid entry list.")
        entries.extend(entry for entry in page_entries if isinstance(entry, dict))
    return entries


def _load_rest_check_rollup(
    client: _ClientProtocol,
    head_sha: str,
    *,
    require_success: bool,
) -> tuple[str | None, tuple[str, ...]]:
    """从 REST check-runs 与 commit statuses 汇总当前 head 的验证状态。"""
    owner_repo = client._get_owner_repo()
    try:
        check_pages = _request_rest_json(
            client,
            f"repos/{owner_repo}/commits/{head_sha}/check-runs",
            paginate=True,
        )
        check_runs = _flatten_rest_pages(check_pages, response_key="check_runs")
        combined_status = _request_rest_json(
            client,
            f"repos/{owner_repo}/commits/{head_sha}/status",
            per_page=True,
        )
        if not isinstance(combined_status, dict) or not isinstance(
            combined_status.get("statuses"), list
        ):
            raise RuntimeError("GitHub REST commit status response is incomplete.")
        commit_statuses = [
            status for status in combined_status["statuses"] if isinstance(status, dict)
        ]
    except GitHubApiRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - unknown checks stay unknown outside strict reads.
        if require_success:
            raise RuntimeError(f"Cannot load checks for PR head {head_sha}: {exc}") from exc
        _logger.warning("Unable to load REST checks for PR head %s: %s", head_sha, exc)
        return None, ()

    normalized_checks = [
        {
            "name": check.get("name"),
            "status": str(check.get("status") or "").upper(),
            "conclusion": str(check.get("conclusion") or "").upper(),
            "detailsUrl": check.get("details_url"),
        }
        for check in check_runs
    ]
    normalized_statuses = [
        {
            "context": status.get("context"),
            "state": str(status.get("state") or "").upper(),
            "targetUrl": status.get("target_url"),
        }
        for status in commit_statuses
    ]
    return _aggregate_status_check_rollup(normalized_checks + normalized_statuses)


def _build_pull_request_context(
    client: _ClientProtocol,
    raw_pr: object,
    *,
    require_success: bool,
) -> PullRequestContext | None:
    """把 GitHub REST PR 资源转换为 runner 使用的上下文。"""
    if not isinstance(raw_pr, dict):
        if require_success:
            raise RuntimeError("Authoritative REST PR query returned an invalid object.")
        return None
    raw_head = raw_pr.get("head")
    raw_base = raw_pr.get("base")
    head = raw_head if isinstance(raw_head, dict) else {}
    base = raw_base if isinstance(raw_base, dict) else {}
    pr_url = raw_pr.get("html_url")
    branch = head.get("ref")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    pr_number = raw_pr.get("number")
    body = raw_pr.get("body")
    is_draft = raw_pr.get("draft")
    if require_success and (
        not isinstance(pr_url, str)
        or not pr_url.startswith("https://")
        or type(pr_number) is not int
        or pr_number <= 0
        or not isinstance(branch, str)
        or not isinstance(head_sha, str)
        or len(head_sha) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in head_sha)
        or not isinstance(base_sha, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
    ):
        raise RuntimeError("Authoritative REST PR query returned incomplete identity metadata.")
    normalized_head_sha = str(head_sha or "")
    if normalized_head_sha:
        checks_state, checks_summary = _load_rest_check_rollup(
            client,
            normalized_head_sha,
            require_success=require_success,
        )
    else:
        checks_state, checks_summary = None, ()
    return PullRequestContext(
        pr_url=str(pr_url or ""),
        branch=str(branch or ""),
        head_sha=normalized_head_sha,
        base_sha=str(base_sha or ""),
        mergeable=_normalize_mergeable(raw_pr.get("mergeable")),
        checks_state=checks_state,
        checks_summary=checks_summary,
        number=pr_number if type(pr_number) is int else None,
        body=str(body or ""),
        is_draft=is_draft if isinstance(is_draft, bool) else None,
    )


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
    try:
        owner_repo = client._get_owner_repo()
        owner = owner_repo.split("/", 1)[0]
        query = urlencode({"state": "open", "head": f"{owner}:{branch}"})
        raw_prs = _request_rest_json(
            client,
            f"repos/{owner_repo}/pulls?{query}",
            per_page=True,
        )
    except GitHubApiRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - non-strict callers keep their best-effort behavior.
        if require_success:
            raise RuntimeError(
                f"Cannot establish open PR context for branch {branch}: {exc}"
            ) from exc
        _logger.warning("Unable to load full PR context for branch %s: %s", branch, exc)
        return None
    if not isinstance(raw_prs, list):
        if require_success:
            raise RuntimeError("Authoritative REST PR query returned an invalid list.")
        return None
    if require_success:
        if not isinstance(raw_prs, list) or len(raw_prs) > 1:
            raise RuntimeError("Authoritative PR query returned an ambiguous or invalid list.")
    if not raw_prs:
        return None
    pr_context = _build_pull_request_context(client, raw_prs[0], require_success=require_success)
    if require_success and pr_context is not None and pr_context.branch != branch:
        raise RuntimeError("Authoritative REST PR query returned a different head branch.")
    return pr_context


def get_pull_request_context_by_number(
    client: _ClientProtocol, pr_number: int, *, require_success: bool = False
) -> PullRequestContext | None:
    """按编号读取开放或已关闭 PR 的身份、head/base 与检查状态。

    Args:
        client: 当前仓库的 GitHub CLI 客户端。
        pr_number: 要读取的 PR 编号。
        require_success: 是否拒绝查询失败或不完整身份响应。

    Returns:
        完整 PR 上下文；非严格模式下查询失败时返回 ``None``。

    Raises:
        RuntimeError: 严格读取不能确认该 PR 身份时。
    """
    try:
        raw_pr = _request_rest_json(
            client,
            f"repos/{client._get_owner_repo()}/pulls/{pr_number}",
        )
    except GitHubApiRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - non-strict callers keep their best-effort behavior.
        if require_success:
            raise RuntimeError(f"Cannot establish PR context for PR #{pr_number}: {exc}") from exc
        return None
    pr_context = _build_pull_request_context(client, raw_pr, require_success=require_success)
    if require_success and pr_context is not None and pr_context.number != pr_number:
        raise RuntimeError(
            f"Authoritative REST PR query returned invalid identity for PR #{pr_number}."
        )
    return pr_context


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


def close_pull_request(
    client: _ClientProtocol,
    pr_number: int,
    *,
    comment: str | None = None,
) -> None:
    """关闭 PR，并可选地留下取代说明。

    批次聚合在总 Draft PR 发布并通过完整树验证后调用；PR 页面、评论、审查历史与
    来源分支仍保留。已关闭的响应按幂等成功处理，便于重试部分失败的收尾操作。

    Args:
        client: 当前仓库的 GitHub CLI 客户端。
        pr_number: 要关闭的 PR 编号。
        comment: 可选 Markdown 评论，例如包含总 PR 链接的取代说明。

    Raises:
        RuntimeError: ``gh pr close`` 失败且不是“已经关闭”的幂等情况时。
    """
    command = ["gh", "pr", "close", str(pr_number)]
    if comment is not None:
        command.extend(["--comment", comment])
    result = client._run_with_retry(command, cwd=client.repo_path, check=False)
    if result.return_code != 0:
        combined_output = f"{result.stdout}\n{result.stderr}"
        if "already closed" in combined_output.lower():
            _logger.info("PR #%d is already closed; treating close request as no-op.", pr_number)
            return
        raise RuntimeError(
            f"gh pr close failed for PR #{pr_number}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def list_pr_comments(client: _ClientProtocol, pr_number: int) -> list[str]:
    """读取 PR 对话区中的评论正文。"""
    try:
        owner_repo = client._get_owner_repo()
        raw_pages = _request_rest_json(
            client,
            f"repos/{owner_repo}/issues/{pr_number}/comments",
            paginate=True,
        )
        comments = _flatten_rest_pages(raw_pages)
    except GitHubApiRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - preserve best-effort comment reads.
        _logger.warning("Unable to load comments for PR #%d: %s", pr_number, exc)
        return []
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
    owner_repo = client._get_owner_repo()
    owner = owner_repo.split("/", 1)[0]
    rest_state = "open" if state == "open" else "closed"
    query = urlencode({"state": rest_state, "head": f"{owner}:{branch}"})
    try:
        raw_prs = _request_rest_json(
            client,
            f"repos/{owner_repo}/pulls?{query}",
            per_page=True,
        )
    except GitHubApiRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - preserve the existing strict/best-effort split.
        if require_success:
            raise RuntimeError(f"Cannot query {state} PRs for branch {branch}: {exc}") from exc
        return None
    if not isinstance(raw_prs, list):
        if require_success:
            raise RuntimeError(
                f"{state.title()} PR query returned an invalid list for branch {branch}."
            )
        return None
    if require_success and (
        not isinstance(raw_prs, list)
        or any(
            not isinstance(raw_pr, dict)
            or not isinstance(raw_pr.get("html_url"), str)
            or not raw_pr["html_url"].startswith("https://")
            for raw_pr in raw_prs
        )
    ):
        raise RuntimeError(
            f"{state.title()} PR query returned an incomplete response for branch {branch}."
        )
    if not isinstance(raw_prs, list):
        return None
    matching_prs = [
        raw_pr
        for raw_pr in raw_prs
        if isinstance(raw_pr, dict)
        and (state == "open" or (state == "merged" and raw_pr.get("merged_at") is not None))
    ]
    if not matching_prs:
        return None
    return str(matching_prs[0].get("html_url", ""))


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
    "get_pull_request_context_by_number",
    "get_remote_base_sha",
    "list_pr_comments",
    "list_pull_requests_for_issue",
    "merge_pull_request",
    "update_pull_request_body",
]
