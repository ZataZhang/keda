"""GitHub CLI client implementation.

Public surface:

- :class:`GitHubCliClient` — small wrapper around the GitHub CLI that
  satisfies the ``IGitHubClient`` interface from
  ``backend.core.shared.interfaces.agent_runner`` via duck typing.
- :func:`sanitize_github_body` — body scrubber that strips C0 control
  characters and middle-truncates Markdown to stay under GitHub's
  POST size limit.

The actual ``gh`` command construction lives in focused helper modules:

- :mod:`backend.infrastructure.github_models` — frozen dataclasses and
  constants for return types.
- :mod:`backend.infrastructure.github_helpers` — pure module-level helpers
  (sanitisation, check rollup parsing, PR state normalisation).
- :mod:`backend.infrastructure.github_labels` — ``sync_labels`` and the
  static label spec table.
- :mod:`backend.infrastructure.github_issue_ops` — ``gh issue ...``
  invocations.
- :mod:`backend.infrastructure.github_pr_ops` — ``gh pr ...`` invocations.

The class methods below are 1-line delegations to those helpers. They
exist so callers can keep using the historical
``client.<domain_method>(...)`` calling convention.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
from backend.core.shared.interfaces.github_api_budget import (
    API_KIND_GRAPHQL,
    API_KIND_REST,
    IGitHubApiBudget,
)
from backend.core.shared.models.agent_spec import AgentSpec
from backend.infrastructure.github_helpers import (
    _GITHUB_API_RATE_LIMIT_PATTERN,
    _RETRYABLE_GH_ERROR_PATTERNS,
    sanitize_github_body,
)
from backend.infrastructure.github_issue_ops import (
    comment_issue,
    create_issue,
    edit_issue_body,
    edit_issue_comment,
    edit_issue_labels,
    get_issue,
    list_issue_comment_entries,
    list_issue_comment_records,
    list_issue_comments,
    list_issue_label_names,
    list_issues_by_label,
    list_repository_items,
    list_ready_issues,
    list_review_candidate_issues,
    list_rework_prd_issues,
)
from backend.infrastructure.github_labels import sync_labels
from backend.infrastructure.github_models import (
    GhAuthStatus,
    IssueSummary,
    LabelConfig,
    PullRequestContext,
    PullRequestSummary,
)
from backend.infrastructure.github_pr_ops import (
    close_pull_request,
    comment_pr,
    create_draft_pr,
    find_merged_pr_by_head,
    find_open_pr_by_head,
    get_pull_request_context,
    get_pull_request_context_by_number,
    get_remote_base_sha,
    list_pr_comments,
    list_pull_requests_for_issue,
    mark_pull_request_ready,
    merge_pull_request,
    set_pull_request_base,
    update_pull_request_body,
)
from backend.infrastructure.process_runner import CommandResult, SubprocessRunner

_logger = logging.getLogger(__name__)

# Re-export data shapes from :mod:`backend.infrastructure.github_models`
# so existing ``from backend.infrastructure.github_client import GhAuthStatus``
# imports continue to work after the line-split refactor.
__all__ = [
    "GhAuthStatus",
    "GitHubCliClient",
    "IssueSummary",
    "LabelConfig",
    "PullRequestContext",
    "PullRequestSummary",
    "sanitize_github_body",
]

# Number of attempts for transient GitHub CLI network failures.
_MAX_GH_RETRIES = 3

# Delay between retries in seconds.
_GH_RETRY_DELAY_SECONDS = 1.0

# ``gh api rate_limit`` 的资源名与额度类型的对应关系（REST 在 GitHub 那边叫 ``core``）。
_RATE_LIMIT_RESOURCE_BY_KIND = {API_KIND_GRAPHQL: "graphql", API_KIND_REST: "core"}
# 读不到重置时间时的保守暂停时长。
_FALLBACK_RATE_LIMIT_PAUSE = timedelta(minutes=10)
_RATE_LIMIT_PROBE_TIMEOUT_SECONDS = 15

# ``gh api`` 里消耗下一个参数作为取值的选项；识别端点路径时要跳过它们。
_GH_API_VALUE_OPTIONS = frozenset(
    {
        "-X",
        "--method",
        "-f",
        "-F",
        "--field",
        "--raw-field",
        "-H",
        "--header",
        "-q",
        "--jq",
        "--input",
        "-t",
        "--template",
        "--cache",
        "--hostname",
    }
)
_REPO_PREFIX_PATTERN = re.compile(r"^repos/[^/]+/[^/]+/")


def _gh_operation_label(command: Sequence[str]) -> str:
    """把 ``gh`` 命令压成不含编号与仓库名的操作标签，用于用量账本聚合。

    ``gh issue create ...`` → ``issue create``；
    ``gh api -X GET repos/o/r/issues/12/comments`` → ``api GET issues/N/comments``。
    """
    if len(command) < 2 or command[0] != "gh":
        return "other"
    if command[1] != "api":
        return (
            " ".join(command[1:3])
            if len(command) > 2 and not command[2].startswith("-")
            else command[1]
        )
    method = "GET"
    endpoint = ""
    arguments = iter(command[2:])
    for argument in arguments:
        if argument in {"-X", "--method"}:
            method = next(arguments, "GET").upper()
        elif argument in _GH_API_VALUE_OPTIONS:
            next(arguments, None)
        elif not argument.startswith("-") and not endpoint:
            endpoint = argument
    endpoint = _REPO_PREFIX_PATTERN.sub("", endpoint.split("?", 1)[0])
    endpoint = re.sub(r"\d+", "N", endpoint)
    return f"api {method} {endpoint}".strip()


def _owner_repo_from_remote_url(remote_url: str) -> str | None:
    """从 origin remote URL 提取 owner/repository，避免为 REST 路径调用 GraphQL。"""
    normalized_url = remote_url.strip()
    if not normalized_url:
        return None
    if "://" in normalized_url:
        remote_path = urlsplit(normalized_url).path
    elif "@" in normalized_url and ":" in normalized_url.split("@", 1)[1]:
        remote_path = normalized_url.split(":", 1)[1]
    else:
        remote_path = normalized_url
    path_parts = [part for part in remote_path.strip("/").removesuffix(".git").split("/") if part]
    if len(path_parts) < 2:
        return None
    return "/".join(path_parts[-2:])


def _normalize_owner_repo(value: object) -> str | None:
    """校验配置中的 GitHub 仓库名是否为 ``owner/name``。"""
    if not isinstance(value, str):
        return None
    owner_repo_parts = [part for part in value.strip().strip("/").split("/") if part]
    if len(owner_repo_parts) != 2:
        return None
    return "/".join(owner_repo_parts)


class GitHubCliClient:
    """Small wrapper around the GitHub CLI.

    Implements the ``IGitHubClient`` interface from
    ``backend.core.shared.interfaces.agent_runner`` via duck typing.
    """

    def __init__(
        self,
        repo_path: Path,
        process_runner: SubprocessRunner | None = None,
        api_budget: IGitHubApiBudget | None = None,
        *,
        bypass_budget_pause: bool = False,
    ) -> None:
        """Create the client.

        Args:
            repo_path: Target repository path.
            process_runner: Optional process runner to use for gh commands.
            api_budget: 跨进程共享的额度预算；省略时不记账也不受暂停闸门约束。
            bypass_budget_pause: 心跳续期等安全收尾请求继续计费，但忽略共享暂停闸门。
        """
        self.repo_path = repo_path
        self._runner = process_runner or SubprocessRunner()
        self._api_budget = api_budget
        self._bypass_budget_pause = bypass_budget_pause
        self._github_api_rate_limited: set[str] = set()
        self._owner_repo_cache: str | None = None

    # --- core invocation primitives -------------------------------------

    def _is_retryable_gh_error(self, exc: subprocess.CalledProcessError) -> bool:
        """Return True when a failed ``gh`` call looks like a transient network error."""
        combined_output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        return any(pattern.search(combined_output) for pattern in _RETRYABLE_GH_ERROR_PATTERNS)

    @staticmethod
    def _github_api_kind(command: Sequence[str]) -> str | None:
        """按 GitHub CLI 命令识别它使用的 API 配额类型。"""
        if len(command) < 2 or command[0] != "gh":
            return None
        if command[1] == "api":
            uses_graphql_endpoint = any(
                token.lower() == "graphql" or token.lower().endswith("/graphql")
                for token in command[2:]
            )
            return "graphql" if uses_graphql_endpoint else "rest"
        if command[1] in {"issue", "pr", "repo", "search"}:
            return "graphql"
        if command[1] in {"label"}:
            return "rest"
        return None

    def _raise_if_rate_limited(self, output_text: str, command: Sequence[str]) -> None:
        """将 GitHub 明确报告的 API 限流转成用例层可识别的延期信号。"""
        if _GITHUB_API_RATE_LIMIT_PATTERN.search(output_text):
            api_kind = self._github_api_kind(command)
            if api_kind is not None:
                self._github_api_rate_limited.add(api_kind)
                self._note_budget_exhausted(api_kind)
            raise GitHubApiRateLimitError(
                "GitHub API rate limit exceeded; the Issue was deferred for a later poll."
            )

    def _fetch_rate_limit_resources(self) -> dict[str, dict[str, object]] | None:
        """读取 ``gh api rate_limit``；该端点本身不消耗额度，限流期间也能调用。"""
        try:
            probe_result = self._runner.run(
                ["gh", "api", "rate_limit"],
                cwd=self.repo_path,
                check=False,
                timeout=_RATE_LIMIT_PROBE_TIMEOUT_SECONDS,
            )
            if probe_result.return_code != 0:
                return None
            resources = json.loads(probe_result.stdout or "{}").get("resources")
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        return resources if isinstance(resources, dict) else None

    def _note_budget_exhausted(self, api_kind: str) -> None:
        """限流时让所有进程暂停到真实重置时间；读不到时用保守的固定时长。"""
        if self._api_budget is None:
            return
        resume_at = datetime.now(timezone.utc) + _FALLBACK_RATE_LIMIT_PAUSE
        resources = self._fetch_rate_limit_resources()
        resource = (resources or {}).get(_RATE_LIMIT_RESOURCE_BY_KIND.get(api_kind, ""))
        if isinstance(resource, dict) and isinstance(resource.get("reset"), int):
            resume_at = datetime.fromtimestamp(resource["reset"], tz=timezone.utc)
        try:
            self._api_budget.record_rate_limited(api_kind, resume_at)
        except Exception:  # noqa: BLE001 - 记账失败不得盖住限流延期信号。
            _logger.warning("Could not record the %s rate-limit pause.", api_kind, exc_info=True)

    def _probe_budget_if_due(self) -> None:
        """每隔一段时间（跨进程合计）免费探测一次额度，见底就提前整体暂停。"""
        if self._api_budget is None or not self._api_budget.probe_due():
            return
        resources = self._fetch_rate_limit_resources()
        if resources is None:
            return
        for api_kind, resource_name in _RATE_LIMIT_RESOURCE_BY_KIND.items():
            resource = resources.get(resource_name)
            if not isinstance(resource, dict):
                continue
            remaining, limit, reset = (
                resource.get("remaining"),
                resource.get("limit"),
                resource.get("reset"),
            )
            if isinstance(remaining, int) and isinstance(limit, int) and isinstance(reset, int):
                self._api_budget.record_probe(
                    api_kind,
                    remaining=remaining,
                    limit=limit,
                    reset_at=datetime.fromtimestamp(reset, tz=timezone.utc),
                )

    def _run_with_retry(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        """执行 ``gh`` 命令，对瞬时网络错误有限重试并按 API 类型隔离限流。

        GitHub 明确返回限流时立即抛出延期信号；GraphQL 配额耗尽不会阻断 REST 恢复请求。
        """

        api_kind = self._github_api_kind(command)
        if api_kind in self._github_api_rate_limited and not self._bypass_budget_pause:
            raise GitHubApiRateLimitError(
                "GitHub API rate limit was reached earlier in this pass; "
                f"remaining {api_kind} operations were deferred."
            )

        if self._api_budget is not None and api_kind is not None and not self._bypass_budget_pause:
            # 先探测再判断：探测免费，且能撤销已经过期的暂停标记。
            self._probe_budget_if_due()
            self._api_budget.ensure_available(api_kind)

        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(1, _MAX_GH_RETRIES + 1):
            if self._api_budget is not None and api_kind is not None:
                self._api_budget.record_call(api_kind, _gh_operation_label(command))
            try:
                command_result = self._runner.run(
                    command,
                    cwd=cwd,
                    check=check,
                    timeout=timeout,
                    capture_output=capture_output,
                    input_text=input_text,
                )
                if command_result.return_code != 0:
                    self._raise_if_rate_limited(
                        (command_result.stdout or "") + "\n" + (command_result.stderr or ""),
                        command,
                    )
                return command_result
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                combined_output = (exc.stdout or "") + "\n" + (exc.stderr or "")
                try:
                    self._raise_if_rate_limited(combined_output, command)
                except GitHubApiRateLimitError as rate_limit_exc:
                    raise rate_limit_exc from exc
                if not check or attempt >= _MAX_GH_RETRIES or not self._is_retryable_gh_error(exc):
                    raise
                _logger.warning(
                    "GitHub CLI transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    _MAX_GH_RETRIES,
                    _GH_RETRY_DELAY_SECONDS,
                    exc.stderr.strip() if exc.stderr else str(exc),
                )
                time.sleep(_GH_RETRY_DELAY_SECONDS)
        # pragma: no cover - loop always returns or raises before exhausting.
        raise last_exc  # type: ignore[misc]

    def _write_body_file(self, temp_dir: str, filename: str, body: str) -> Path:
        """清理 Markdown 正文并写入临时文件，供 GitHub CLI 文件字段读取。

        所有正文都经过 :func:`sanitize_github_body`，避免控制字符或超长内容触发 GitHub
        ``400 Bad Request`` 拒绝。
        """
        body_path = Path(temp_dir) / filename
        body_path.write_text(sanitize_github_body(body), encoding="utf-8")
        return body_path

    def _get_owner_repo(self) -> str:
        """从仓库配置或 Git remote 读取 ``owner/name`` 并缓存结果。"""
        if self._owner_repo_cache is not None:
            return self._owner_repo_cache

        owner_repo = self._get_owner_repo_from_repository_config()
        if owner_repo is None:
            owner_repo = self._get_owner_repo_from_git_remotes()
        if owner_repo is not None:
            self._owner_repo_cache = owner_repo
            return owner_repo
        raise RuntimeError(
            "Unable to determine repository owner/name: set "
            "[agent_runner.repository].github_repo in .kedacode.toml or add a GitHub remote."
        )

    def _get_owner_repo_from_repository_config(self) -> str | None:
        """读取仓库级 KedaCode 配置中的 GitHub 仓库名。"""
        config_path = self.repo_path / ".kedacode.toml"
        try:
            raw_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        runner_config = raw_config.get("agent_runner")
        if not isinstance(runner_config, dict):
            return None
        repository_config = runner_config.get("repository")
        if not isinstance(repository_config, dict):
            return None
        return _normalize_owner_repo(repository_config.get("github_repo"))

    def _get_owner_repo_from_git_remotes(self) -> str | None:
        """从任意本地 Git remote 读取仓库名，兼容 remote 不叫 origin 的仓库。"""
        result = self._runner.run(
            ["git", "remote", "-v"],
            cwd=self.repo_path,
            check=False,
        )
        for remote_line in (result.stdout or "").splitlines():
            remote_fields = remote_line.split()
            if len(remote_fields) < 2:
                continue
            owner_repo = _owner_repo_from_remote_url(remote_fields[1])
            if owner_repo is not None:
                return owner_repo
        return None

    def _list_issue_label_names(self, issue_number: int) -> set[str]:
        """Convenience wrapper retained for backward compatibility."""
        return list_issue_label_names(self, issue_number)

    # --- auth + labels --------------------------------------------------

    def check_auth_status(self) -> GhAuthStatus:
        """Run ``gh auth status`` and parse the result.

        Returns:
            GhAuthStatus indicating whether the user is authenticated.
        """
        result = self._run_with_retry(
            ["gh", "auth", "status", "--hostname", "github.com"],
            cwd=self.repo_path,
            check=False,
        )
        combined_output = (result.stdout or "") + "\n" + (result.stderr or "")

        if "✓ Logged in" in combined_output:
            account: str | None = None
            for line in combined_output.splitlines():
                if "✓ Logged in" in line and " as " in line:
                    account = line.split(" as ", 1)[1].strip().split()[0]
                    break
            return GhAuthStatus(authenticated=True, account=account)

        failure_reason: str | None = None
        for line in combined_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("X Failed to log in"):
                failure_reason = stripped
                break
            if "invalid" in stripped.lower() or "expired" in stripped.lower():
                failure_reason = stripped

        if not failure_reason:
            failure_reason = "GitHub CLI 认证失败"

        return GhAuthStatus(authenticated=False, failure_reason=failure_reason)

    def sync_labels(
        self,
        labels: LabelConfig,
        agent_registry: dict[str, AgentSpec] | None = None,
    ) -> None:
        """Create or update standard labels."""
        sync_labels(self, labels, agent_registry)

    # --- issue delegations ---------------------------------------------

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        return list_ready_issues(self, ready_label, limit)

    def list_rework_prd_issues(self, rework_prd_label: str, limit: int) -> list[IssueSummary]:
        return list_rework_prd_issues(self, rework_prd_label, limit)

    def list_review_candidate_issues(self, labels: Sequence[str], limit: int) -> list[IssueSummary]:
        return list_review_candidate_issues(self, labels, limit)

    def list_issues_by_label(
        self, label: str | None, limit: int, state: str = "all"
    ) -> list[IssueSummary]:
        return list_issues_by_label(self, label, limit, state)

    def list_repository_items(self, *, state: str = "open") -> list[IssueSummary]:
        return list_repository_items(self, state=state)

    def get_issue(self, issue_number: int) -> IssueSummary:
        return get_issue(self, issue_number)

    def edit_issue_labels(
        self,
        issue_number: int,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
    ) -> None:
        edit_issue_labels(self, issue_number, add=add, remove=remove)

    def comment_issue(self, issue_number: int, body: str) -> None:
        comment_issue(self, issue_number, body)

    def edit_issue_body(self, issue_number: int, body: str) -> None:
        edit_issue_body(self, issue_number, body)

    def create_issue(self, *, title: str, body: str, labels: Sequence[str]) -> str:
        return create_issue(self, title=title, body=body, labels=labels)

    def list_issue_comments(self, issue_number: int, *, require_success: bool = False) -> list[str]:
        return list_issue_comments(self, issue_number, require_success=require_success)

    def list_issue_comment_entries(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[tuple[int, str]]:
        """读取评论，可按正文过滤并只保留可管理 Issue 的作者。

        Args:
            issue_number: Issue 编号。
            trusted_only: 是否核验当前作者或当前仓库 triage 及以上权限。
            body_contains: 可选正文子串过滤，避免对无关作者查询权限。

        Returns:
            评论 ID 与正文条目。

        Raises:
            RuntimeError: 可信模式无法确认读取结果或作者权限。
        """
        return list_issue_comment_entries(
            self,
            issue_number,
            trusted_only=trusted_only,
            body_contains=body_contains,
            require_success=require_success,
        )

    def list_issue_comment_records(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ):
        """读取评论及服务端更新时间和作者。"""
        return list_issue_comment_records(
            self,
            issue_number,
            trusted_only=trusted_only,
            body_contains=body_contains,
            require_success=require_success,
        )

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        edit_issue_comment(self, comment_id, body)

    # --- PR delegations -------------------------------------------------

    def create_draft_pr(self, *, title: str, body: str, base_branch: str, cwd: Path) -> str:
        return create_draft_pr(self, title=title, body=body, base_branch=base_branch, cwd=cwd)

    def get_pull_request_context(
        self, branch: str, *, require_success: bool = False
    ) -> PullRequestContext | None:
        """读取 PR 上下文，可选要求权威成功查询才返回不存在。

        Args:
            branch: 远端 head 分支。
            require_success: 是否拒绝查询失败和身份不完整的返回结构。

        Returns:
            开放 PR 上下文；没有 PR 时为 None。

        Raises:
            RuntimeError: 严格查询无法证明 PR 状态。
        """
        return get_pull_request_context(self, branch, require_success=require_success)

    def get_pull_request_context_by_number(
        self, pr_number: int, *, require_success: bool = False
    ) -> PullRequestContext | None:
        """按编号读取开放或已关闭 PR 的上下文。"""
        return get_pull_request_context_by_number(self, pr_number, require_success=require_success)

    def comment_pr(self, pr_number: int, body: str) -> None:
        comment_pr(self, pr_number, body)

    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        update_pull_request_body(self, pr_number, body)

    def set_pull_request_base(self, pr_number: int, base_branch: str) -> None:
        set_pull_request_base(self, pr_number, base_branch)

    def mark_pull_request_ready(self, pr_number: int) -> None:
        """将 Draft PR 转为可审阅状态。"""
        mark_pull_request_ready(self, pr_number)

    def merge_pull_request(self, pr_number: int, *, method: str = "squash") -> None:
        merge_pull_request(self, pr_number, method=method)

    def close_pull_request(self, pr_number: int, *, comment: str | None = None) -> None:
        """关闭 PR，并可选地留下取代说明。"""
        close_pull_request(self, pr_number, comment=comment)

    def list_pr_comments(self, pr_number: int) -> list[str]:
        return list_pr_comments(self, pr_number)

    def find_open_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        return find_open_pr_by_head(self, branch, require_success=require_success)

    def find_merged_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        return find_merged_pr_by_head(self, branch, require_success=require_success)

    def get_remote_base_sha(self, remote: str, base_branch: str) -> str:
        return get_remote_base_sha(self, remote, base_branch)

    def list_pull_requests_for_issue(
        self, repo: str, issue_number: int
    ) -> list[PullRequestSummary]:
        return list_pull_requests_for_issue(self, repo, issue_number)
