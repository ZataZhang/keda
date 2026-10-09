"""Issue-side operations for the GitHub CLI client.

Module-level helper functions that drive ``gh issue ...`` and
``gh api repos/.../issues/comments/...`` invocations. They take a
client-like first argument (``_ClientProtocol``) so the main
:class:`backend.infrastructure.github_client.GitHubCliClient` can keep its
method-level public surface while delegating the actual ``gh`` command
construction here.

Backward compatibility: ``GitHubCliClient.list_ready_issues`` etc. continue
to exist on the class as thin pass-throughs.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Sequence
from urllib.parse import quote

from backend.infrastructure.github_helpers import _extract_comment_id_from_url
from backend.infrastructure.github_models import IssueSummary

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


class _ClientProtocol(Protocol):
    """Duck-typed interface expected by the issue-side helpers.

    Implemented by :class:`GitHubCliClient`; the helpers only touch the
    methods needed to drive ``gh`` invocations.
    """

    repo_path: Path

    def _run_with_retry(
        self, command: Sequence[str], *, cwd: Path, check: bool = True
    ) -> object: ...

    def _write_body_file(self, temp_dir: str, filename: str, body: str) -> Path: ...

    def _get_owner_repo(self) -> str: ...

    def _list_issue_label_names(self, issue_number: int) -> set[str]: ...


def _build_issue_summary(raw_issue: dict[str, object]) -> IssueSummary:
    """Map a ``gh issue ... --json`` row into an :class:`IssueSummary`."""
    return IssueSummary(
        number=int(raw_issue["number"]),
        title=str(raw_issue.get("title", "")),
        url=str(raw_issue.get("url", "")),
        body=str(raw_issue.get("body", "") or ""),
        labels=tuple(
            str(raw_label.get("name", ""))
            for raw_label in raw_issue.get("labels", [])
            if raw_label.get("name")
        ),
        state=str(raw_issue.get("state", "OPEN") or "OPEN"),
    )


def list_ready_issues(client: _ClientProtocol, ready_label: str, limit: int) -> list[IssueSummary]:
    """List open Issues with the ready label."""
    result = client._run_with_retry(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            ready_label,
            "--limit",
            str(limit),
            "--json",
            "number,title,url,labels,body,state",
        ],
        cwd=client.repo_path,
    )
    raw_issues = json.loads(result.stdout or "[]")
    return [_build_issue_summary(raw_issue) for raw_issue in raw_issues]


def list_rework_prd_issues(
    client: _ClientProtocol, rework_prd_label: str, limit: int
) -> list[IssueSummary]:
    """List open Issues with the rework-prd label."""
    result = client._run_with_retry(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            rework_prd_label,
            "--limit",
            str(limit),
            "--json",
            "number,title,url,labels,body,state",
        ],
        cwd=client.repo_path,
    )
    raw_issues = json.loads(result.stdout or "[]")
    return [_build_issue_summary(raw_issue) for raw_issue in raw_issues]


def list_review_candidate_issues(
    client: _ClientProtocol, labels: Sequence[str], limit: int
) -> list[IssueSummary]:
    """List open Issues with any of the given labels."""
    seen_numbers: set[int] = set()
    candidates: list[IssueSummary] = []
    for label in labels:
        result = client._run_with_retry(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                label,
                "--limit",
                str(limit),
                "--json",
                "number,title,url,labels,body,state",
            ],
            cwd=client.repo_path,
        )
        raw_issues = json.loads(result.stdout or "[]")
        for raw_issue in raw_issues:
            number = int(raw_issue["number"])
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            candidates.append(_build_issue_summary(raw_issue))
    return candidates


def list_issues_by_label(
    client: _ClientProtocol, label: str | None, limit: int, state: str = "all"
) -> list[IssueSummary]:
    """List Issues by label across open and closed states.

    When ``label`` is ``None``, the ``--label`` flag is omitted so
    the listing returns issues regardless of label.
    """
    command: list[str] = [
        "gh",
        "issue",
        "list",
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,url,labels,body,state",
    ]
    if label is not None:
        command[3:3] = ["--label", label]
    result = client._run_with_retry(command, cwd=client.repo_path)
    raw_issues = json.loads(result.stdout or "[]")
    return [_build_issue_summary(raw_issue) for raw_issue in raw_issues]


def get_issue(client: _ClientProtocol, issue_number: int) -> IssueSummary:
    """Return the Issue summary for the given issue number."""
    result = client._run_with_retry(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "number,title,url,labels,body,state",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        raise RuntimeError(
            f"Failed to fetch Issue #{issue_number}: {result.stderr.strip() or result.stdout}"
        )
    raw_issue = json.loads(result.stdout or "{}")
    return _build_issue_summary(raw_issue)


def edit_issue_labels(
    client: _ClientProtocol,
    issue_number: int,
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
) -> None:
    """Add and remove Issue labels.

    On ``gh issue edit`` failure caused by a missing repository label (not
    a network or auth error), the missing labels are created with
    ``gh label create --force`` and the edit is retried. This guards against
    fresh repositories whose label set does not yet include workflow labels
    such as ``validation/verifier-passed`` that the runner only attaches in
    specific code paths.
    """
    current_labels = client._list_issue_label_names(issue_number)
    labels_to_add = [label for label in add if label not in current_labels]
    requested_add_labels = set(add)
    labels_to_remove = [
        label for label in remove if label in current_labels and label not in requested_add_labels
    ]
    if not labels_to_add and not labels_to_remove:
        return

    command = _build_edit_labels_command(issue_number, labels_to_add, labels_to_remove)
    try:
        client._run_with_retry(command, cwd=client.repo_path)
        return
    except subprocess.CalledProcessError as exc:
        if not _is_missing_label_error(exc) or not labels_to_add:
            raise
        _logger.info(
            "gh issue edit reported missing label(s) on #%d; creating %s and retrying.",
            issue_number,
            ", ".join(labels_to_add),
        )
        for label in labels_to_add:
            client._run_with_retry(
                _build_ensure_label_command(label),
                cwd=client.repo_path,
            )
        client._run_with_retry(command, cwd=client.repo_path)


def _build_edit_labels_command(
    issue_number: int,
    labels_to_add: Sequence[str],
    labels_to_remove: Sequence[str],
) -> list[str]:
    """Build the ``gh issue edit`` argv for the requested label deltas."""
    command = ["gh", "issue", "edit", str(issue_number)]
    for label in labels_to_add:
        command.extend(["--add-label", label])
    for label in labels_to_remove:
        command.extend(["--remove-label", label])
    return command


def _is_missing_label_error(exc: subprocess.CalledProcessError) -> bool:
    """Return True when a failed ``gh issue edit`` looks like a missing-label error.

    GitHub surfaces the missing-label case in two shapes depending on the
    CLI version:

    - ``'validation/verifier-passed' not found``
    - ``Could not resolve to a RepositoryLabel with the name '...'``

    Both indicate the same root cause: the repository does not yet have the
    label. Other failures (network, auth, permission) bubble up unchanged.
    """
    combined = (exc.stderr or "") + "\n" + (exc.output or "")
    lowered = combined.lower()
    return "not found" in lowered or "could not resolve to a repositorylabel" in lowered.replace(
        " ", ""
    )


def _build_ensure_label_command(label: str) -> list[str]:
    """Build the ``gh label create --force`` argv that idempotently creates a label."""
    return [
        "gh",
        "label",
        "create",
        label,
        "--force",
    ]


def list_issue_label_names(client: _ClientProtocol, issue_number: int) -> set[str]:
    """Return the set of label names currently on an Issue."""
    result = client._run_with_retry(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "labels",
        ],
        cwd=client.repo_path,
    )
    raw_issue = json.loads(result.stdout or "{}")
    return {
        str(raw_label.get("name", ""))
        for raw_label in raw_issue.get("labels", [])
        if raw_label.get("name")
    }


def comment_issue(client: _ClientProtocol, issue_number: int, body: str) -> None:
    """Post a Markdown comment to an Issue."""
    with tempfile.TemporaryDirectory(prefix="kc-comment-") as temp_dir:
        comment_path = client._write_body_file(temp_dir, "comment.md", body)
        client._run_with_retry(
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--body-file",
                str(comment_path),
            ],
            cwd=client.repo_path,
        )


def edit_issue_body(client: _ClientProtocol, issue_number: int, body: str) -> None:
    """Replace the body of an Issue."""
    with tempfile.TemporaryDirectory(prefix="kc-issue-body-") as temp_dir:
        body_path = Path(temp_dir) / "issue_body.md"
        body_path.write_text(body, encoding="utf-8")
        client._run_with_retry(
            [
                "gh",
                "issue",
                "edit",
                str(issue_number),
                "--body-file",
                str(body_path),
            ],
            cwd=client.repo_path,
        )


def create_issue(
    client: _ClientProtocol,
    *,
    title: str,
    body: str,
    labels: Sequence[str],
) -> str:
    """Create a GitHub Issue and return its URL.

    On ``gh issue create`` failure caused by a missing repository label (a
    fresh repository that never ran ``kc labels sync``), the missing labels
    are created with ``gh label create --force`` and the create is retried.
    This mirrors :func:`edit_issue_labels` so both Issue write paths survive
    an unprovisioned label set instead of failing with gh's raw
    ``could not add label: '...' not found`` error. Other failures (network,
    auth, validation) bubble up unchanged.
    """
    with tempfile.TemporaryDirectory(prefix="kc-issue-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "issue.md", body)
        command = [
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body-file",
            str(body_path),
        ]
        for label in labels:
            command.extend(["--label", label])
        try:
            result = client._run_with_retry(command, cwd=client.repo_path)
        except subprocess.CalledProcessError as exc:
            if not _is_missing_label_error(exc) or not labels:
                raise
            _logger.info(
                "gh issue create reported missing label(s); creating %s and retrying.",
                ", ".join(labels),
            )
            for label in labels:
                client._run_with_retry(
                    _build_ensure_label_command(label),
                    cwd=client.repo_path,
                )
            result = client._run_with_retry(command, cwd=client.repo_path)
    return result.stdout.strip().splitlines()[-1]


def list_issue_comments(
    client: _ClientProtocol, issue_number: int, *, require_success: bool = False
) -> list[str]:
    """复用评论条目查询，返回非空正文并保留默认尽力读取语义。

    Args:
        client: 当前仓库的 GitHub CLI 客户端。
        issue_number: Issue 编号。
        require_success: 要求查询成功且评论列表结构完整。

    Returns:
        非空评论正文；默认模式下查询失败时为列表空值。

    Raises:
        RuntimeError: 严格模式下查询失败或响应结构不完整。
    """
    return [
        body
        for _comment_id, body in list_issue_comment_entries(
            client, issue_number, require_success=require_success
        )
        if body
    ]


def list_issue_comment_entries(
    client: _ClientProtocol,
    issue_number: int,
    *,
    trusted_only: bool = False,
    body_contains: str | None = None,
    require_success: bool = False,
) -> list[tuple[int, str]]:
    """读取评论 ID 与正文，可选仅返回可授权直发检查点的可信作者。

    Args:
        client: 当前仓库的 GitHub CLI 客户端。
        issue_number: Issue 编号。
        trusted_only: 为 True 时要求作者是当前调用者或当前仓库有 triage 及以上权限。
        body_contains: 可选正文子串过滤，在查询作者权限前排除无关评论。
        require_success: 要求评论查询成功且响应结构完整。

    Returns:
        按服务端顺序排列的评论 ID 与正文；默认保留原有尽力读取语义。

    Raises:
        RuntimeError: 严格模式无法读取或确认评论结构，或可信模式无法确认作者权限。
    """
    result = client._run_with_retry(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--comments",
            "--json",
            "comments",
        ],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        if trusted_only or require_success:
            raise RuntimeError(f"Cannot read Issue #{issue_number} comments.")
        return []
    raw_data = json.loads(result.stdout or "{}")
    if (trusted_only or require_success) and (
        not isinstance(raw_data, dict) or not isinstance(raw_data.get("comments"), list)
    ):
        raise RuntimeError("Issue comment query has no complete comments list.")
    comments = raw_data.get("comments", [])
    author_permissions: dict[str, bool] = {}
    entries: list[tuple[int, str]] = []
    for raw_comment in comments:
        if body_contains is not None:
            if not isinstance(raw_comment, dict) or not isinstance(raw_comment.get("body"), str):
                raise RuntimeError("Filtered comment query has malformed body metadata.")
            if body_contains not in raw_comment["body"]:
                continue
        if require_success and (
            not isinstance(raw_comment, dict) or not isinstance(raw_comment.get("body"), str)
        ):
            raise RuntimeError("Issue comment query has malformed body metadata.")
        if trusted_only:
            if not isinstance(raw_comment, dict):
                raise RuntimeError("Trusted comment query has malformed comment metadata.")
            if not _is_trusted_comment_author(client, raw_comment, author_permissions):
                continue
        url = str(raw_comment.get("url", ""))
        comment_id = _extract_comment_id_from_url(url) or 0
        if trusted_only and (comment_id <= 0 or not isinstance(raw_comment.get("body"), str)):
            raise RuntimeError("Trusted comment query has invalid comment identity or body.")
        body = str(raw_comment.get("body", "") or "")
        entries.append((comment_id, body))
    return entries


def edit_issue_comment(client: _ClientProtocol, comment_id: int, body: str) -> None:
    """Edit an existing Issue comment."""
    owner_repo = client._get_owner_repo()
    with tempfile.TemporaryDirectory(prefix="kc-comment-edit-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "comment.md", body)
        client._run_with_retry(
            [
                "gh",
                "api",
                f"repos/{owner_repo}/issues/comments/{comment_id}",
                "-X",
                "PATCH",
                "-F",
                f"body=@{body_path}",
            ],
            cwd=client.repo_path,
        )


__all__ = [
    "comment_issue",
    "create_issue",
    "edit_issue_body",
    "edit_issue_comment",
    "edit_issue_labels",
    "get_issue",
    "list_issue_comment_entries",
    "list_issue_comments",
    "list_issue_label_names",
    "list_issues_by_label",
    "list_ready_issues",
    "list_rework_prd_issues",
    "list_review_candidate_issues",
]


def _is_trusted_comment_author(
    client: _ClientProtocol, raw_comment: dict, author_permissions: dict[str, bool]
) -> bool:
    """使用当前调用者或实时仓库权限，不用 authorAssociation 推断授权。"""
    viewer_did_author = raw_comment.get("viewerDidAuthor")
    author = raw_comment.get("author")
    if type(viewer_did_author) is not bool or not isinstance(author, dict):
        raise RuntimeError("Trusted comment query has missing author metadata.")
    login = author.get("login")
    if not isinstance(login, str) or not login:
        raise RuntimeError("Trusted comment query has no author login.")
    if viewer_did_author:
        return True
    if login not in author_permissions:
        author_permissions[login] = _can_manage_repository_issues(client, login)
    return author_permissions[login]


def _can_manage_repository_issues(client: _ClientProtocol, login: str) -> bool:
    """每次评论查询独立核验其他机器认领者的当前仓库权限。"""
    owner_repo = client._get_owner_repo()
    result = client._run_with_retry(
        ["gh", "api", f"repos/{owner_repo}/collaborators/{quote(login, safe='')}/permission"],
        cwd=client.repo_path,
        check=False,
    )
    if result.return_code != 0:
        raise RuntimeError(f"Cannot establish repository permission for comment author {login}.")
    permission_payload = json.loads(result.stdout or "{}")
    if not isinstance(permission_payload, dict):
        raise RuntimeError("Comment author permission query has malformed metadata.")
    permission = permission_payload.get("permission")
    role_name = permission_payload.get("role_name")
    if permission not in ("none", "read", "triage", "write", "maintain", "admin"):
        raise RuntimeError("Comment author permission query has unknown permission.")
    user = permission_payload.get("user", {})
    if not isinstance(user, dict):
        raise RuntimeError("Comment author permission query has malformed user metadata.")
    permissions = user.get("permissions", {})
    if not isinstance(permissions, dict) or any(
        type(flag) is not bool for flag in permissions.values()
    ):
        raise RuntimeError("Comment author permission query has malformed permission flags.")
    if role_name is not None and not isinstance(role_name, str):
        raise RuntimeError("Comment author permission query has malformed repository role.")
    if permission in ("triage", "write", "maintain", "admin") or role_name in (
        "triage",
        "write",
        "maintain",
        "admin",
    ):
        return True
    return any(permissions.get(flag, False) for flag in ("triage", "push", "maintain", "admin"))
