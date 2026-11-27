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
import time
from functools import partial
from pathlib import Path
from typing import Protocol, Sequence
from urllib.parse import urlencode

from backend.core.shared.interfaces.agent_runner import (
    GitHubApiPermissionError,
    GitHubApiRateLimitError,
    GitHubApiUnavailableError,
)
from backend.core.shared.interfaces.runner_recovery import IRunnerRecoveryStore
from backend.core.shared.models.runner_recovery_policy import classify_recovery_failure
from backend.core.shared.models.runner_recovery_operations import (
    build_operation_intent,
    execute_journaled_write,
)
from backend.core.shared.models.runner_recovery import RecoveryFailureCategory
from backend.infrastructure.github_helpers import sanitize_github_body
from backend.infrastructure.github_issue_ops import list_issue_comment_entries
from backend.infrastructure.github_operation_recovery import (
    operation_marker,
    post_comment_with_recovery,
    send_github_write_command,
)
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
    recovery_store: IRunnerRecoveryStore | None

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
        diagnostic_text = result.stderr.strip() or result.stdout.strip()
        if not diagnostic_text:
            raise GitHubApiUnavailableError(
                "GitHub REST query failed without a diagnostic; remote state remains unknown."
            )
        failure_decision = classify_recovery_failure(
            diagnostic_text=diagnostic_text,
            is_write=False,
        )
        if failure_decision.category is RecoveryFailureCategory.AUTH:
            raise GitHubApiPermissionError(
                "GitHub API permission was denied while reading authoritative recovery state."
            )
        if failure_decision.category is RecoveryFailureCategory.TRANSIENT_NETWORK:
            raise GitHubApiUnavailableError(
                f"GitHub API read remained unavailable after bounded retries: {diagnostic_text}"
            )
        raise RuntimeError(diagnostic_text)
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
    except (GitHubApiPermissionError, GitHubApiRateLimitError, GitHubApiUnavailableError):
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
    issue_number: int = 0,
) -> str:
    """Create a draft PR with a stable operation marker and conservative recovery."""
    owner_repo = client._get_owner_repo()
    recovery_store = getattr(client, "recovery_store", None)
    branch_result = client._run_with_retry(["git", "branch", "--show-current"], cwd=cwd)
    branch = branch_result.stdout.strip()
    if not branch:
        raise RuntimeError("Cannot identify the branch for a journaled Draft PR.")
    safe_body = sanitize_github_body(body)
    intent = build_operation_intent(
        repository_id=owner_repo,
        issue_number=issue_number,
        operation_kind="draft_pull_request_create",
        target=f"repos/{owner_repo}/pulls:{branch}:{base_branch}",
        payload_text=json.dumps(
            [title, safe_body, base_branch, branch], ensure_ascii=False, separators=(",", ":")
        ),
    )
    marker = operation_marker(intent.operation_key) if recovery_store is not None else ""
    request_body = f"{safe_body}\n\n{marker}" if marker else safe_body
    with tempfile.TemporaryDirectory(prefix="kc-pr-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "pr.md", request_body)
        command = [
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
        ]

        def send_pr_creation() -> str | None:
            command_output = send_github_write_command(client, command, cwd=cwd)
            lines = (command_output or "").strip().splitlines()
            return lines[-1] if lines else None

        def reconcile_pr_creation() -> str | None:
            if not marker:
                return None
            query = urlencode(
                {
                    "state": "all",
                    "head": f"{owner_repo.split('/', 1)[0]}:{branch}",
                    "base": base_branch,
                }
            )
            raw_pr_pages = _request_rest_json(
                client,
                f"repos/{owner_repo}/pulls?{query}",
                paginate=True,
            )
            matching_prs = [
                raw_pr
                for raw_pr in _flatten_rest_pages(raw_pr_pages)
                if marker in str(raw_pr.get("body", ""))
            ]
            if len(matching_prs) > 1:
                raise RuntimeError("Draft PR operation marker matches multiple remote PRs.")
            if not matching_prs:
                if _flatten_rest_pages(raw_pr_pages):
                    raise RuntimeError(
                        "A PR exists for this head/base without the operation marker; "
                        "creation outcome is ambiguous."
                    )
                return None
            pr_url = matching_prs[0].get("html_url")
            if not isinstance(pr_url, str) or not pr_url.startswith("https://"):
                raise RuntimeError("Matched Draft PR lacks an authoritative URL.")
            return pr_url

        def confirm_pr_creation_absence() -> str | None:
            # 第二次完整分页核对用于吸收服务端短暂的 PR 可见性延迟。
            time.sleep(1)
            return reconcile_pr_creation()

        if recovery_store is None:
            pr_url = send_pr_creation()
        else:
            pr_url = execute_journaled_write(
                recovery_store=recovery_store,
                intent=intent,
                send=send_pr_creation,
                reconcile=reconcile_pr_creation,
                # PR 列表没有标记仍可能是传播延迟；结果未知时不自动重发。
                # 传输层能证明请求尚未发送时，execute_journaled_write 会直接安全重试。
                safe_to_retry_after_confirmed_absence=False,
                reconcile_after_absence=confirm_pr_creation_absence,
            )
    if not pr_url:
        raise RuntimeError("GitHub Draft PR creation returned no URL.")
    return pr_url


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
    except (GitHubApiPermissionError, GitHubApiRateLimitError, GitHubApiUnavailableError):
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


def get_pull_request_monitor_state(client: _ClientProtocol, pr_number: int) -> dict:
    """读取单个 PR 的轻量看板投影，保留已知关联。"""
    raw_pr = _request_rest_json(client, f"repos/{client._get_owner_repo()}/pulls/{pr_number}")
    if not isinstance(raw_pr, dict) or raw_pr.get("number") != pr_number:
        raise RuntimeError(f"Invalid GitHub response for PR #{pr_number}.")
    return {
        "number": pr_number,
        "url": raw_pr["html_url"],
        "is_draft": raw_pr["draft"],
        "branch": raw_pr["head"]["ref"],
        "head_sha": raw_pr["head"]["sha"],
        "base_sha": raw_pr["base"]["sha"],
        "mergeable": raw_pr.get("mergeable"),
        "state": raw_pr["state"],
        "merged": raw_pr.get("merged", False),
    }


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
    except (GitHubApiPermissionError, GitHubApiRateLimitError, GitHubApiUnavailableError):
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
    """向 PR 发布带稳定身份的 Markdown 评论。"""
    owner_repo = client._get_owner_repo()
    safe_body = sanitize_github_body(body)
    post_comment_with_recovery(
        client=client,
        repository_id=owner_repo,
        issue_number=pr_number,
        operation_kind="pull_request_comment",
        target=f"repos/{owner_repo}/issues/{pr_number}/comments",
        body=safe_body,
        command_for_body_file=lambda comment_path: [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--body-file",
            str(comment_path),
        ],
        comment_entries_for_marker=lambda marker: list_issue_comment_entries(
            client,
            pr_number,
            trusted_only=True,
            body_contains=marker,
            require_success=True,
        ),
    )


def update_pull_request_body(client: _ClientProtocol, pr_number: int, body: str) -> None:
    """更新 PR 正文，并在结果不确定时只读核对远端正文。"""
    owner_repo = client._get_owner_repo()
    safe_body = sanitize_github_body(body)
    recovery_store = getattr(client, "recovery_store", None)
    with tempfile.TemporaryDirectory(prefix="kc-pr-body-") as temp_dir:
        body_path = client._write_body_file(temp_dir, "body.md", safe_body)
        command = ["gh", "pr", "edit", str(pr_number), "--body-file", str(body_path)]

        send_body_update = partial(send_github_write_command, client, command)

        def reconcile_body_update() -> str | None:
            raw_pr = _request_rest_json(
                client,
                f"repos/{owner_repo}/pulls/{pr_number}",
            )
            if not isinstance(raw_pr, dict) or raw_pr.get("number") != pr_number:
                raise RuntimeError("PR body reconciliation returned an invalid identity.")
            return f"pr:{pr_number}" if raw_pr.get("body") == safe_body else None

        if recovery_store is None:
            send_body_update()
        else:
            execute_journaled_write(
                recovery_store=recovery_store,
                intent=build_operation_intent(
                    repository_id=owner_repo,
                    issue_number=pr_number,
                    operation_kind="pull_request_body_update",
                    target=f"repos/{owner_repo}/pulls/{pr_number}:body",
                    payload_text=safe_body,
                ),
                send=send_body_update,
                reconcile=reconcile_body_update,
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
    owner_repo = client._get_owner_repo()
    recovery_store = getattr(client, "recovery_store", None)
    command = ["gh", "pr", "edit", str(pr_number), "--base", base_branch]

    send_base_update = partial(send_github_write_command, client, command)

    def reconcile_base_update() -> str | None:
        raw_pr = _request_rest_json(client, f"repos/{owner_repo}/pulls/{pr_number}")
        if not isinstance(raw_pr, dict) or raw_pr.get("number") != pr_number:
            raise RuntimeError("PR base reconciliation returned an invalid identity.")
        base = raw_pr.get("base")
        return (
            f"pr:{pr_number}" if isinstance(base, dict) and base.get("ref") == base_branch else None
        )

    if recovery_store is None:
        send_base_update()
    else:
        execute_journaled_write(
            recovery_store=recovery_store,
            intent=build_operation_intent(
                repository_id=owner_repo,
                issue_number=pr_number,
                operation_kind="pull_request_base_update",
                target=f"repos/{owner_repo}/pulls/{pr_number}:base",
                payload_text=base_branch,
            ),
            send=send_base_update,
            reconcile=reconcile_base_update,
        )


def mark_pull_request_ready(client: _ClientProtocol, pr_number: int) -> None:
    """将 Draft Pull Request 转为 Ready for review。"""
    owner_repo = client._get_owner_repo()
    recovery_store = getattr(client, "recovery_store", None)

    ready_command = ["gh", "pr", "ready", str(pr_number)]
    send_ready_update = partial(
        send_github_write_command,
        client,
        ready_command,
        accept_failure=lambda _: False,
    )

    def reconcile_ready_update() -> str | None:
        pr_state = get_pull_request_monitor_state(client, pr_number)
        return f"pr:{pr_number}" if pr_state["is_draft"] is False else None

    if recovery_store is None:
        try:
            send_ready_update()
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.output or "").strip()
            raise RuntimeError(f"gh pr ready failed for PR #{pr_number}: {detail or exc}") from exc
    else:
        execute_journaled_write(
            recovery_store=recovery_store,
            intent=build_operation_intent(
                repository_id=owner_repo,
                issue_number=pr_number,
                operation_kind="pull_request_ready",
                target=f"repos/{owner_repo}/pulls/{pr_number}:draft",
                payload_text="draft=false",
            ),
            send=send_ready_update,
            reconcile=reconcile_ready_update,
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
    owner_repo = client._get_owner_repo()
    recovery_store = getattr(client, "recovery_store", None)
    command = ["gh", "pr", "merge", str(pr_number), "--squash"]

    send_merge = partial(
        send_github_write_command,
        client,
        command,
        accept_failure=lambda diagnostic_text: "already merged" in diagnostic_text.lower(),
    )

    def reconcile_merge() -> str | None:
        pr_state = get_pull_request_monitor_state(client, pr_number)
        return f"pr:{pr_number}:merged" if pr_state["merged"] is True else None

    if recovery_store is None:
        send_merge()
        return
    execute_journaled_write(
        recovery_store=recovery_store,
        intent=build_operation_intent(
            repository_id=owner_repo,
            issue_number=pr_number,
            operation_kind="pull_request_merge",
            target=f"repos/{owner_repo}/pulls/{pr_number}:merge",
            payload_text=method,
        ),
        send=send_merge,
        reconcile=reconcile_merge,
    )


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
    if comment is not None:
        comment_pr(client, pr_number, comment)
    owner_repo = client._get_owner_repo()
    recovery_store = getattr(client, "recovery_store", None)
    command = ["gh", "pr", "close", str(pr_number)]

    send_close = partial(
        send_github_write_command,
        client,
        command,
        accept_failure=lambda diagnostic_text: "already closed" in diagnostic_text.lower(),
    )

    def reconcile_close() -> str | None:
        pr_state = get_pull_request_monitor_state(client, pr_number)
        return (
            f"pr:{pr_number}:closed"
            if pr_state["state"] == "closed" or pr_state["merged"] is True
            else None
        )

    if recovery_store is None:
        send_close()
        return
    execute_journaled_write(
        recovery_store=recovery_store,
        intent=build_operation_intent(
            repository_id=owner_repo,
            issue_number=pr_number,
            operation_kind="pull_request_close",
            target=f"repos/{owner_repo}/pulls/{pr_number}:state",
            payload_text="closed",
        ),
        send=send_close,
        reconcile=reconcile_close,
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
    except (GitHubApiPermissionError, GitHubApiRateLimitError, GitHubApiUnavailableError):
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
    except (GitHubApiPermissionError, GitHubApiRateLimitError, GitHubApiUnavailableError):
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
