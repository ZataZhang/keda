"""Gitea Pull Request、状态检查与远端分支操作。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from backend.core.shared.interfaces.agent_runner import (
    GitHubApiRateLimitError,
    GitHubApiUnavailableError,
)
from backend.infrastructure.gitea_api_models import (
    GiteaDraftPullRequest,
    GiteaRequestOptions,
)
from backend.infrastructure.github_models import (
    IssueSummary,
    PullRequestContext,
    PullRequestSummary,
)

if TYPE_CHECKING:
    from backend.infrastructure.gitea_client import GiteaApiClient

_FAILED_STATUS_STATES = frozenset({"failure", "error", "errored"})
_PENDING_STATUS_STATES = frozenset({"pending", "queued", "running"})
_SUCCESS_STATUS_STATES = frozenset({"success"})
_PR_STATE_ORDER = {"open": 0, "draft": 1, "closed": 2, "merged": 3}
_ISSUE_REFERENCE_PATTERN = re.compile(
    r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?|refs?|references)\s+"
    r"(?:[\w.-]+/[\w.-]+)?#(?P<number>\d+)\b",
    re.IGNORECASE,
)


def _request_dict(
    client: GiteaApiClient,
    method: str,
    endpoint_path: str,
    options: GiteaRequestOptions | None = None,
) -> dict[str, Any] | None:
    """请求一个预期返回 JSON object 的端点。"""
    response_object = client._request_json(method, endpoint_path, options)
    if response_object is None and options is not None and options.allow_not_found:
        return None
    if not isinstance(response_object, dict):
        raise RuntimeError(f"Gitea {method} {endpoint_path} returned an incomplete object.")
    return response_object


def _issue_state(raw_issue: dict[str, Any]) -> str:
    """把 Gitea 的 state 归一化为接口现有的 OPEN/CLOSED 字段。"""
    state_text = str(raw_issue.get("state") or "").casefold()
    return "CLOSED" if state_text == "closed" else "OPEN"


def _build_issue_summary(raw_issue: dict[str, Any]) -> IssueSummary:
    """将 Gitea Issue JSON 转换为现有共享 Issue 摘要。"""
    raw_labels = raw_issue.get("labels")
    labels = (
        tuple(
            str(label_payload.get("name", ""))
            for label_payload in raw_labels
            if isinstance(label_payload, dict) and label_payload.get("name")
        )
        if isinstance(raw_labels, list)
        else ()
    )
    number = raw_issue.get("number")
    issue_url = raw_issue.get("html_url")
    if type(number) is not int or number <= 0 or not isinstance(issue_url, str):
        raise RuntimeError("Gitea Issue response is missing its number or web URL.")
    return IssueSummary(
        number=number,
        title=str(raw_issue.get("title") or ""),
        url=issue_url,
        body=str(raw_issue.get("body") or ""),
        labels=labels,
        state=_issue_state(raw_issue),
    )


def _is_draft_title(title: str) -> bool:
    """按 Gitea 的 WIP 标题前缀识别草稿 PR。"""
    return title.lstrip().casefold().startswith("wip:")


def _pull_request_state(raw_pull_request: dict[str, Any], title: str) -> str:
    """归一化 Gitea PR 的合并、关闭、草稿与开放状态。"""
    if raw_pull_request.get("merged_at"):
        return "merged"
    if str(raw_pull_request.get("state") or "").casefold() == "closed":
        return "closed"
    if _is_draft_title(title):
        return "draft"
    return "open"


def _build_pull_request_summary(raw_pull_request: dict[str, Any]) -> PullRequestSummary:
    """将 Gitea PR JSON 转换为现有共享 PR 摘要。"""
    pull_number = raw_pull_request.get("number")
    pull_url = raw_pull_request.get("html_url")
    pull_title = str(raw_pull_request.get("title") or "")
    if type(pull_number) is not int or pull_number <= 0 or not isinstance(pull_url, str):
        raise RuntimeError("Gitea Pull Request response is missing its number or web URL.")
    return PullRequestSummary(
        number=pull_number,
        state=_pull_request_state(raw_pull_request, pull_title),
        url=pull_url,
        is_draft=_is_draft_title(pull_title),
        merged=bool(raw_pull_request.get("merged_at")),
        title=pull_title,
    )


def _summarize_commit_statuses(
    status_payload: dict[str, Any],
) -> tuple[str | None, tuple[str, ...]]:
    """将 Gitea head commit 的状态汇总为共享 checks 字段。"""
    statuses = status_payload.get("statuses")
    if not isinstance(statuses, list) or not statuses:
        return None, ()

    failure_summaries: list[str] = []
    pending_summaries: list[str] = []
    has_unrecognized_status = False
    for status_payload_item in statuses:
        if not isinstance(status_payload_item, dict):
            has_unrecognized_status = True
            continue
        status_name = str(status_payload_item.get("context") or "unnamed check")
        status_value = str(
            status_payload_item.get("status") or status_payload_item.get("state") or ""
        ).casefold()
        detail_text = str(status_payload_item.get("description") or status_value or "unknown")
        summary_line = f"{status_name}: {detail_text}"
        if status_value in _FAILED_STATUS_STATES:
            failure_summaries.append(summary_line)
        elif status_value in _PENDING_STATUS_STATES:
            pending_summaries.append(summary_line)
        elif status_value not in _SUCCESS_STATUS_STATES:
            has_unrecognized_status = True

    if failure_summaries:
        return "FAILURE", tuple(failure_summaries)
    if pending_summaries:
        return "PENDING", tuple(pending_summaries)
    if has_unrecognized_status:
        return None, ()
    return "SUCCESS", ()


def _load_commit_statuses(
    client: GiteaApiClient,
    head_sha: str,
    *,
    require_success: bool,
) -> tuple[str | None, tuple[str, ...]]:
    """读取并汇总 PR head 的 commit status；读取失败时 fail closed。"""
    try:
        status_payload = _request_dict(
            client,
            "GET",
            f"repos/{client.owner_repo}/commits/{quote(head_sha, safe='')}/status",
            GiteaRequestOptions(allow_not_found=True),
        )
        if status_payload is None:
            return None, ()
        raw_statuses = status_payload.get("statuses")
        if raw_statuses is None:
            return None, ()
        if not isinstance(raw_statuses, list):
            raise RuntimeError("Gitea commit status response has no statuses list.")
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001 - 读取失败不能被解释成检查通过。
        if require_success:
            raise RuntimeError(f"Cannot load checks for PR head {head_sha}: {exc}") from exc
        return None, ()
    return _summarize_commit_statuses(status_payload)


def _build_pull_request_context(
    client: GiteaApiClient,
    raw_pull_request: dict[str, Any],
    *,
    require_success: bool,
) -> PullRequestContext:
    """将 Gitea PR 响应映射到既有 runner PR 上下文。"""
    raw_head = raw_pull_request.get("head")
    raw_base = raw_pull_request.get("base")
    head = raw_head if isinstance(raw_head, dict) else {}
    base = raw_base if isinstance(raw_base, dict) else {}
    pr_url = raw_pull_request.get("html_url")
    branch = head.get("ref")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    pr_number = raw_pull_request.get("number")
    body = raw_pull_request.get("body")
    title = str(raw_pull_request.get("title") or "")
    required_strings = (pr_url, branch, head_sha, base_sha, body)
    if require_success and (
        not all(isinstance(value, str) for value in required_strings)
        or type(pr_number) is not int
        or pr_number <= 0
        or not re.fullmatch(r"[0-9a-fA-F]{40}", str(head_sha or ""))
        or not re.fullmatch(r"[0-9a-fA-F]{40}", str(base_sha or ""))
    ):
        raise RuntimeError("Authoritative Gitea Pull Request response has incomplete identity.")
    normalized_head_sha = str(head_sha or "")
    checks_state, checks_summary = (
        _load_commit_statuses(client, normalized_head_sha, require_success=require_success)
        if normalized_head_sha
        else (None, ())
    )
    mergeable_value = raw_pull_request.get("mergeable")
    mergeable = mergeable_value if isinstance(mergeable_value, bool) else None
    is_draft = _is_draft_title(title)
    if is_draft:
        # Gitea 1.21 reports mergeable=false while a WIP PR is still draft;
        # that value does not distinguish the intentional WIP gate from a conflict.
        mergeable = None
    return PullRequestContext(
        pr_url=str(pr_url or ""),
        branch=str(branch or ""),
        head_sha=normalized_head_sha,
        base_sha=str(base_sha or ""),
        mergeable=mergeable,
        checks_state=checks_state,
        checks_summary=checks_summary,
        number=pr_number if type(pr_number) is int else None,
        body=str(body or ""),
        is_draft=is_draft,
    )


def _best_effort_context(
    client: GiteaApiClient,
    raw_pull_request: dict[str, Any],
    *,
    require_success: bool,
) -> PullRequestContext | None:
    """构造 PR 上下文，并按 require_success 保留兼容的容错语义。"""
    try:
        return _build_pull_request_context(
            client, raw_pull_request, require_success=require_success
        )
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception:  # noqa: BLE001 - 非严格读取保持旧适配器的尽力语义。
        if require_success:
            raise
        return None


def _list_pull_requests(
    client: GiteaApiClient,
    *,
    state: str,
) -> list[dict[str, Any]]:
    """按状态读取仓库所有 PR。"""
    return client._list_pages(f"repos/{client.owner_repo}/pulls", params={"state": state})


def get_pull_request_context(
    client: GiteaApiClient,
    branch: str,
    *,
    require_success: bool = False,
) -> PullRequestContext | None:
    """查询来源分支上的开放 PR。"""
    try:
        matching_pulls = [
            raw_pull_request
            for raw_pull_request in _list_pull_requests(client, state="open")
            if isinstance(raw_pull_request.get("head"), dict)
            and raw_pull_request["head"].get("ref") == branch
        ]
        if len(matching_pulls) > 1 and require_success:
            raise RuntimeError(f"Multiple open Pull Requests use head branch {branch!r}.")
        if not matching_pulls:
            return None
        return _best_effort_context(
            client,
            matching_pulls[0],
            require_success=require_success,
        )
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001 - 分支查询失败不等同于没有 PR。
        if require_success:
            raise RuntimeError(
                f"Cannot establish open PR context for branch {branch}: {exc}"
            ) from exc
        return None


def get_pull_request_context_by_number(
    client: GiteaApiClient,
    pr_number: int,
    *,
    require_success: bool = False,
) -> PullRequestContext | None:
    """按 PR 编号读取开放、关闭或已合并的上下文。"""
    try:
        raw_pull_request = _request_dict(
            client,
            "GET",
            f"repos/{client.owner_repo}/pulls/{pr_number}",
            GiteaRequestOptions(allow_not_found=True),
        )
        if raw_pull_request is None:
            return None
        return _best_effort_context(
            client,
            raw_pull_request,
            require_success=require_success,
        )
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001 - 非严格模式保留最佳努力读取。
        if require_success:
            raise RuntimeError(f"Cannot establish PR context for PR #{pr_number}: {exc}") from exc
        return None


def create_draft_pr(
    client: GiteaApiClient,
    draft_pull_request: GiteaDraftPullRequest,
) -> str:
    """以 WIP 标题前缀创建 Gitea 草稿 PR。"""
    source_branch = client._current_branch(draft_pull_request.cwd)
    if not source_branch:
        raise RuntimeError("Cannot create a Pull Request from a detached HEAD.")
    normalized_title = (
        draft_pull_request.title
        if _is_draft_title(draft_pull_request.title)
        else f"WIP: {draft_pull_request.title}"
    )
    created_pull_request = _request_dict(
        client,
        "POST",
        f"repos/{client.owner_repo}/pulls",
        GiteaRequestOptions(
            json_body={
                "title": normalized_title,
                "body": draft_pull_request.body,
                "head": source_branch,
                "base": draft_pull_request.base_branch,
            }
        ),
    )
    if created_pull_request is None or not isinstance(created_pull_request.get("html_url"), str):
        raise RuntimeError("Gitea did not return the created Pull Request URL.")
    return created_pull_request["html_url"]


def _load_pull_request(client: GiteaApiClient, pr_number: int) -> dict[str, Any]:
    """读取并校验指定 PR。"""
    pull_request_payload = _request_dict(
        client,
        "GET",
        f"repos/{client.owner_repo}/pulls/{pr_number}",
    )
    if pull_request_payload is None:
        raise RuntimeError(f"Gitea Pull Request #{pr_number} was not found.")
    return pull_request_payload


def mark_pull_request_ready(client: GiteaApiClient, pr_number: int) -> None:
    """移除 WIP 前缀，将 Gitea 草稿 PR 转为可审阅状态。"""
    pull_request_payload = _load_pull_request(client, pr_number)
    pull_title = str(pull_request_payload.get("title") or "")
    if not _is_draft_title(pull_title):
        return
    ready_title = re.sub(r"^\s*WIP:\s*", "", pull_title, count=1, flags=re.IGNORECASE)
    _request_dict(
        client,
        "PATCH",
        f"repos/{client.owner_repo}/pulls/{pr_number}",
        GiteaRequestOptions(json_body={"title": ready_title}),
    )


def update_pull_request_body(client: GiteaApiClient, pr_number: int, body: str) -> None:
    """整体更新 Pull Request 正文。"""
    _request_dict(
        client,
        "PATCH",
        f"repos/{client.owner_repo}/pulls/{pr_number}",
        GiteaRequestOptions(json_body={"body": body}),
    )


def set_pull_request_base(client: GiteaApiClient, pr_number: int, base_branch: str) -> None:
    """修改 Pull Request 的目标分支。"""
    _request_dict(
        client,
        "PATCH",
        f"repos/{client.owner_repo}/pulls/{pr_number}",
        GiteaRequestOptions(json_body={"base": base_branch}),
    )


def comment_pr(client: GiteaApiClient, pr_number: int, body: str) -> None:
    """向 PR conversation 写入 Markdown 评论。"""
    client.comment_issue(pr_number, body)


def list_pr_comments(client: GiteaApiClient, pr_number: int) -> list[str]:
    """读取 PR conversation 中的非空评论正文。"""
    return client.list_issue_comments(pr_number)


def close_pull_request(
    client: GiteaApiClient,
    pr_number: int,
    *,
    comment: str | None = None,
) -> None:
    """关闭 PR；已经关闭的 PR 按成功空操作处理。"""
    pull_request_payload = _load_pull_request(client, pr_number)
    if str(pull_request_payload.get("state") or "").casefold() == "closed":
        return
    if comment:
        comment_pr(client, pr_number, comment)
    _request_dict(
        client,
        "PATCH",
        f"repos/{client.owner_repo}/issues/{pr_number}",
        GiteaRequestOptions(json_body={"state": "closed"}),
    )


def merge_pull_request(
    client: GiteaApiClient,
    pr_number: int,
    *,
    method: str = "squash",
) -> None:
    """按接口支持的 squash 方式合并 PR，并允许崩溃后幂等重入。"""
    if method != "squash":
        raise ValueError("method must be 'squash'.")
    pull_request_payload = _load_pull_request(client, pr_number)
    if pull_request_payload.get("merged_at"):
        return
    if str(pull_request_payload.get("state") or "").casefold() == "closed":
        raise RuntimeError(f"Gitea Pull Request #{pr_number} is closed and not merged.")
    try:
        client._request_json(
            "POST",
            f"repos/{client.owner_repo}/pulls/{pr_number}/merge",
            GiteaRequestOptions(json_body={"Do": "squash"}),
        )
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception:
        refreshed_pull_request = _load_pull_request(client, pr_number)
        if refreshed_pull_request.get("merged_at"):
            return
        raise


def _find_pull_request_by_branch(
    client: GiteaApiClient,
    branch: str,
    *,
    state: str,
    require_success: bool,
) -> str | None:
    """按来源分支与 open/merged 状态查找 PR URL。"""
    try:
        raw_pull_requests = _list_pull_requests(
            client, state="open" if state == "open" else "closed"
        )
        for raw_pull_request in raw_pull_requests:
            raw_head = raw_pull_request.get("head")
            if not isinstance(raw_head, dict) or raw_head.get("ref") != branch:
                continue
            is_merged = bool(raw_pull_request.get("merged_at"))
            if state == "merged" and not is_merged:
                continue
            if state == "open" and str(raw_pull_request.get("state") or "").casefold() != "open":
                continue
            pull_url = raw_pull_request.get("html_url")
            if isinstance(pull_url, str) and pull_url:
                return pull_url
            if require_success:
                raise RuntimeError(f"Gitea PR for branch {branch!r} has no web URL.")
        return None
    except (GitHubApiRateLimitError, GitHubApiUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001 - 严格读取不得将 API 故障当作 PR 不存在。
        if require_success:
            raise RuntimeError(f"Cannot query {state} Gitea PR for branch {branch}: {exc}") from exc
        return None


def find_open_pr_by_head(
    client: GiteaApiClient,
    branch: str,
    *,
    require_success: bool = False,
) -> str | None:
    """若来源分支存在开放 PR，返回其 URL。"""
    return _find_pull_request_by_branch(
        client,
        branch,
        state="open",
        require_success=require_success,
    )


def find_merged_pr_by_head(
    client: GiteaApiClient,
    branch: str,
    *,
    require_success: bool = False,
) -> str | None:
    """若来源分支存在已合并 PR，返回其 URL。"""
    return _find_pull_request_by_branch(
        client,
        branch,
        state="merged",
        require_success=require_success,
    )


def get_remote_base_sha(client: GiteaApiClient, remote: str, base_branch: str) -> str:
    """通过 git remote-tracking ref 读取基线提交 SHA。"""
    git_result = client._run_git(
        ["git", "rev-parse", f"{remote}/{base_branch}"],
        cwd=client.repo_path,
        check=False,
    )
    return git_result.stdout.strip() if git_result.return_code == 0 else ""


def list_pull_requests_for_issue(
    client: GiteaApiClient,
    repo: str,
    issue_number: int,
) -> list[PullRequestSummary]:
    """返回正文含关闭或引用关键字的 Issue 关联 PR。"""
    repo_path = client._validated_owner_repo(repo)
    all_pull_requests = client._list_pages(f"repos/{repo_path}/pulls", params={"state": "all"})
    linked_pulls: list[PullRequestSummary] = []
    for raw_pull_request in all_pull_requests:
        body_text = str(raw_pull_request.get("body") or "")
        if any(
            int(issue_reference_match.group("number")) == issue_number
            for issue_reference_match in _ISSUE_REFERENCE_PATTERN.finditer(body_text)
        ):
            linked_pulls.append(_build_pull_request_summary(raw_pull_request))
    linked_pulls.sort(
        key=lambda pull_summary: (
            _PR_STATE_ORDER.get(pull_summary.state, 99),
            pull_summary.number,
        )
    )
    return linked_pulls


__all__ = [
    "close_pull_request",
    "comment_pr",
    "create_draft_pr",
    "find_merged_pr_by_head",
    "find_open_pr_by_head",
    "get_pull_request_context",
    "get_pull_request_context_by_number",
    "get_remote_base_sha",
    "list_pr_comments",
    "list_pull_requests_for_issue",
    "mark_pull_request_ready",
    "merge_pull_request",
    "set_pull_request_base",
    "update_pull_request_body",
]
