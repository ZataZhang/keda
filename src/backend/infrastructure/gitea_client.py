"""基于 Gitea REST API 的代码平台客户端。"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, unquote, urlsplit

import requests

from backend.core.shared.interfaces.agent_runner import (
    GitHubApiRateLimitError,
    GitHubApiUnavailableError,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.models.agent_labels import standard_label_specs
from backend.core.shared.models.agent_runner import AgentSpec, IssueCommentRecord
from backend.core.shared.models.product_identity import state_home
from backend.infrastructure.file_lock import file_lock
from backend.infrastructure.gitea_api_models import (
    GiteaDraftPullRequest,
    GiteaRequestOptions,
)
from backend.infrastructure.github_models import (
    IssueSummary,
    LabelConfig,
    PullRequestContext,
    PullRequestSummary,
)
from backend.infrastructure.process_runner import CommandResult, SubprocessRunner

from .gitea_pr_ops import (
    _build_issue_summary,
    close_pull_request as _close_pull_request,
    comment_pr as _comment_pr,
    create_draft_pr as _create_draft_pr,
    find_merged_pr_by_head as _find_merged_pr_by_head,
    find_open_pr_by_head as _find_open_pr_by_head,
    get_pull_request_context as _get_pull_request_context,
    get_pull_request_context_by_number as _get_pull_request_context_by_number,
    get_remote_base_sha as _get_remote_base_sha,
    list_pr_comments as _list_pr_comments,
    list_pull_requests_for_issue as _list_pull_requests_for_issue,
    mark_pull_request_ready as _mark_pull_request_ready,
    merge_pull_request as _merge_pull_request,
    set_pull_request_base as _set_pull_request_base,
    update_pull_request_body as _update_pull_request_body,
)

_logger = logging.getLogger(__name__)
_MAX_REQUEST_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0
_DEFAULT_REQUEST_TIMEOUT = (5, 30)
_API_PAGE_SIZE = 50
_OWNER_REPO_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class GiteaApiError(RuntimeError):
    """Gitea 返回非成功 HTTP 状态时携带的脱敏错误。"""

    def __init__(self, *, status_code: int, message: str) -> None:
        """初始化 API 错误。

        Args:
            status_code: Gitea HTTP 状态码。
            message: 不含认证头和令牌值的服务端错误摘要。
        """
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GiteaRepositoryBinding:
    """单个 Gitea 仓库与机器登记实例的绑定信息。"""

    repo_path: Path
    base_url: str
    host: str
    owner_repo: str


def parse_gitea_remote_url(remote_url: str) -> tuple[str, str] | None:
    """解析远端 URL 的主机名和 ``owner/repo``。"""
    normalized_remote_url = remote_url.strip()
    if not normalized_remote_url:
        return None
    if "://" in normalized_remote_url:
        parsed_remote_url = urlsplit(normalized_remote_url)
        if parsed_remote_url.scheme.casefold() not in {"http", "https", "ssh"}:
            return None
        remote_hostname = (parsed_remote_url.hostname or "").casefold()
        remote_path = parsed_remote_url.path
    else:
        scp_remote_match = re.fullmatch(
            r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)",
            normalized_remote_url,
        )
        if scp_remote_match is None:
            return None
        remote_hostname = scp_remote_match.group("host").casefold()
        remote_path = scp_remote_match.group("path")
    path_segments = [unquote(path_segment) for path_segment in remote_path.strip("/").split("/")]
    if path_segments:
        path_segments[-1] = path_segments[-1].removesuffix(".git")
    if not remote_hostname or len(path_segments) < 2:
        return None
    owner_and_repository = path_segments[-2:]
    if any(
        not _OWNER_REPO_SEGMENT_PATTERN.fullmatch(path_segment) or path_segment in {".", ".."}
        for path_segment in owner_and_repository
    ):
        return None
    return remote_hostname, "/".join(owner_and_repository)


class GiteaApiClient(IGitHubClient):
    """通过 Gitea REST API 实现现有代码平台客户端操作。"""

    def __init__(
        self,
        repository: GiteaRepositoryBinding,
        token: str,
        process_runner: IProcessRunner | None = None,
    ) -> None:
        """初始化绑定到单一登记实例与仓库的客户端。

        Args:
            repository: 本地 Git 仓库、机器级登记地址与远端仓库身份。
            token: 从机器级配置指定的环境变量读取的令牌。
            process_runner: 可选 Git 命令执行器。
        """
        self.repo_path = repository.repo_path
        self.base_url = repository.base_url.rstrip("/")
        self.host = repository.host.casefold()
        self.owner_repo = self._validated_owner_repo(repository.owner_repo)
        self._runner = process_runner or SubprocessRunner()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"token {token}",
            }
        )
        self._authenticated_login: str | None = None

    def __repr__(self) -> str:
        """返回不包含令牌或请求头的诊断表示。"""
        return (
            f"GiteaApiClient(host={self.host!r}, owner_repo={self.owner_repo!r}, "
            f"base_url={self.base_url!r})"
        )

    @staticmethod
    def _validated_owner_repo(owner_repo: str) -> str:
        """校验仓库标识为两个安全路径段。"""
        owner_repo_parts = owner_repo.split("/")
        if len(owner_repo_parts) != 2 or any(
            not _OWNER_REPO_SEGMENT_PATTERN.fullmatch(owner_repo_part)
            or owner_repo_part in {".", ".."}
            for owner_repo_part in owner_repo_parts
        ):
            raise ValueError("Gitea repository identity must have the form 'owner/repo'.")
        return "/".join(owner_repo_parts)

    def _repository_api_path(self, suffix: str) -> str:
        """构造转义后的 repository API 路径。"""
        repository_api_path = f"repos/{quote(self.owner_repo, safe='/')}"
        normalized_suffix = suffix.lstrip("/")
        return (
            f"{repository_api_path}/{normalized_suffix}"
            if normalized_suffix
            else repository_api_path
        )

    def _request_json(
        self,
        method: str,
        endpoint_path: str,
        options: GiteaRequestOptions | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """发送经过登记实例固定的 API 请求并处理失败语义。"""
        request_options = options or GiteaRequestOptions()
        request_url = f"{self.base_url}/api/v1/{endpoint_path.lstrip('/')}"
        for request_attempt in range(_MAX_REQUEST_ATTEMPTS):
            try:
                http_response = self._session.request(
                    method,
                    request_url,
                    json=request_options.json_body,
                    params=request_options.params,
                    timeout=_DEFAULT_REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if request_attempt + 1 < _MAX_REQUEST_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise GitHubApiUnavailableError(
                    f"Gitea at {self.host} is unavailable after {_MAX_REQUEST_ATTEMPTS} attempts."
                ) from exc
            except requests.RequestException as exc:
                raise GitHubApiUnavailableError(
                    f"Gitea request to {self.host} failed: {type(exc).__name__}."
                ) from exc

            if http_response.status_code == 429:
                raise GitHubApiRateLimitError(
                    f"Gitea at {self.host} returned HTTP 429; the operation was deferred."
                )
            if 500 <= http_response.status_code <= 599:
                if request_attempt + 1 < _MAX_REQUEST_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise GitHubApiUnavailableError(
                    f"Gitea at {self.host} returned HTTP {http_response.status_code} "
                    f"after {_MAX_REQUEST_ATTEMPTS} attempts."
                )
            if request_options.allow_not_found and http_response.status_code == 404:
                return None
            if not 200 <= http_response.status_code < 300:
                raise self._build_api_error(http_response)
            if http_response.status_code == 204 or not http_response.content:
                return None
            try:
                response_payload = http_response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Gitea {method} {endpoint_path} returned invalid JSON."
                ) from exc
            if not isinstance(response_payload, (dict, list)):
                raise RuntimeError(
                    f"Gitea {method} {endpoint_path} returned an invalid JSON shape."
                )
            return response_payload
        raise GitHubApiUnavailableError(f"Gitea at {self.host} is unavailable.")

    def _build_api_error(self, http_response: requests.Response) -> GiteaApiError:
        """从服务端错误体提取消息并确保令牌不会进入异常文本。"""
        try:
            response_payload = http_response.json()
        except ValueError:
            response_payload = {}
        response_message = (
            response_payload.get("message") if isinstance(response_payload, dict) else None
        )
        safe_message = str(response_message or f"HTTP {http_response.status_code}")
        authorization_header = self._session.headers.get("Authorization", "")
        authorization_token = authorization_header.removeprefix("token ")
        if authorization_token:
            safe_message = safe_message.replace(authorization_token, "[redacted]")
        return GiteaApiError(
            status_code=http_response.status_code,
            message=(
                f"Gitea {http_response.request.method} {http_response.request.url} "
                f"returned HTTP {http_response.status_code}: {safe_message}"
            ),
        )

    def _list_pages(
        self,
        endpoint_path: str,
        *,
        params: dict[str, str | int | bool] | None = None,
    ) -> list[dict[str, Any]]:
        """分页读取完整列表，遇到错误或无效页时拒绝返回部分数据。"""
        accumulated_entries: list[dict[str, Any]] = []
        for page_number in range(1, 1001):
            page_params = dict(params or {})
            page_params.update({"limit": _API_PAGE_SIZE, "page": page_number})
            response_payload = self._request_json(
                "GET",
                endpoint_path,
                GiteaRequestOptions(params=page_params),
            )
            if not isinstance(response_payload, list):
                raise RuntimeError(f"Gitea GET {endpoint_path} returned an invalid page.")
            if any(not isinstance(page_entry, dict) for page_entry in response_payload):
                raise RuntimeError(f"Gitea GET {endpoint_path} returned a malformed entry.")
            accumulated_entries.extend(response_payload)
            if len(response_payload) < _API_PAGE_SIZE:
                return accumulated_entries
        raise RuntimeError(f"Gitea GET {endpoint_path} exceeded the pagination safety limit.")

    def _run_git(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> CommandResult:
        """通过现有进程端口执行 Git 命令。"""
        return self._runner.run(command, cwd=cwd, check=check)

    def _current_branch(self, cwd: Path) -> str:
        """读取指定 worktree 当前分支，detached HEAD 返回空串。"""
        branch_result = self._run_git(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            check=False,
        )
        return branch_result.stdout.strip() if branch_result.return_code == 0 else ""

    def _load_repository_labels(self) -> list[dict[str, Any]]:
        """读取仓库内全部标签。"""
        return self._list_pages(self._repository_api_path("labels"))

    def _ensure_label(self, label_name: str) -> dict[str, Any]:
        """确保标签存在并返回带服务端 ID 的标签。"""
        existing_label = next(
            (
                label_payload
                for label_payload in self._load_repository_labels()
                if label_payload.get("name") == label_name
            ),
            None,
        )
        if existing_label is not None:
            return existing_label
        try:
            created_label = self._request_json(
                "POST",
                self._repository_api_path("labels"),
                GiteaRequestOptions(
                    json_body={"name": label_name, "color": "ededed", "description": ""}
                ),
            )
        except GiteaApiError as exc:
            if exc.status_code != 409:
                raise
            existing_label = next(
                (
                    label_payload
                    for label_payload in self._load_repository_labels()
                    if label_payload.get("name") == label_name
                ),
                None,
            )
            if existing_label is None:
                raise
            return existing_label
        if not isinstance(created_label, dict):
            raise RuntimeError(f"Gitea did not return the created label {label_name!r}.")
        return created_label

    def _label_ids(self, label_names: Sequence[str]) -> list[int]:
        """将标签名转换为 Gitea 数字标签 ID。"""
        label_ids: list[int] = []
        for label_name in label_names:
            label_payload = self._ensure_label(label_name)
            label_id = label_payload.get("id")
            if type(label_id) is not int or label_id <= 0:
                raise RuntimeError(f"Gitea label {label_name!r} has no numeric ID.")
            label_ids.append(label_id)
        return label_ids

    def _list_issue_label_names(self, issue_number: int) -> set[str]:
        """读取 Issue 当前标签名集合。"""
        issue_payload = self._request_json(
            "GET",
            self._repository_api_path(f"issues/{issue_number}"),
        )
        if not isinstance(issue_payload, dict):
            raise RuntimeError(f"Gitea Issue #{issue_number} response is incomplete.")
        raw_labels = issue_payload.get("labels")
        if not isinstance(raw_labels, list):
            raise RuntimeError(f"Gitea Issue #{issue_number} has no labels list.")
        return {
            str(label_payload["name"])
            for label_payload in raw_labels
            if isinstance(label_payload, dict) and label_payload.get("name")
        }

    def _load_comment_entries(
        self,
        issue_number: int,
        *,
        trusted_only: bool,
        body_contains: str | None,
        require_success: bool,
    ) -> list[dict[str, Any]]:
        """读取 Issue/PR 评论并按接口要求做信任过滤。"""
        try:
            comment_payloads = self._list_pages(
                self._repository_api_path(f"issues/{issue_number}/comments")
            )
        except (GitHubApiRateLimitError, GitHubApiUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001 - 严格读取不得把故障伪装为空评论。
            if require_success or trusted_only:
                raise RuntimeError(
                    f"Cannot read Gitea Issue #{issue_number} comments: {exc}"
                ) from exc
            _logger.warning("Unable to load Gitea Issue #%d comments.", issue_number)
            return []

        selected_comments: list[dict[str, Any]] = []
        current_login: str | None = None
        if trusted_only:
            current_login = self._get_authenticated_login()
        author_permissions: dict[str, bool] = {}
        for raw_comment in comment_payloads:
            body_value = raw_comment.get("body")
            if body_contains is not None:
                if not isinstance(body_value, str):
                    raise RuntimeError("Gitea comment query returned malformed body metadata.")
                if body_contains not in body_value:
                    continue
            if trusted_only:
                raw_user = raw_comment.get("user")
                author_login = raw_user.get("login") if isinstance(raw_user, dict) else None
                if not isinstance(author_login, str) or not author_login:
                    raise RuntimeError("Gitea trusted comment has no author login.")
                if author_login != current_login and author_login not in author_permissions:
                    author_permissions[author_login] = self._author_can_write_issues(author_login)
                if author_login != current_login and not author_permissions[author_login]:
                    continue
            if (require_success or trusted_only) and (
                type(raw_comment.get("id")) is not int
                or raw_comment["id"] <= 0
                or not isinstance(body_value, str)
            ):
                raise RuntimeError("Gitea comment query returned malformed identity metadata.")
            selected_comments.append(raw_comment)
        return selected_comments

    def _get_authenticated_login(self) -> str:
        """读取当前 token 对应的用户 login。"""
        if self._authenticated_login is not None:
            return self._authenticated_login
        user_payload = self._request_json("GET", "user")
        if not isinstance(user_payload, dict) or not isinstance(user_payload.get("login"), str):
            raise RuntimeError("Cannot establish the authenticated Gitea user.")
        self._authenticated_login = user_payload["login"]
        return self._authenticated_login

    def _author_can_write_issues(self, author_login: str) -> bool:
        """核验评论作者在仓库中的权限；无法确认时拒绝信任。"""
        permission_payload = self._request_json(
            "GET",
            self._repository_api_path(f"collaborators/{quote(author_login, safe='')}/permission"),
            GiteaRequestOptions(allow_not_found=True),
        )
        if permission_payload is None:
            return False
        if not isinstance(permission_payload, dict) or permission_payload.get("permission") not in {
            "none",
            "read",
            "write",
            "admin",
            "owner",
        }:
            raise RuntimeError("Cannot establish Gitea comment author permissions.")
        return permission_payload["permission"] in {"write", "admin", "owner"}

    @staticmethod
    def _build_lock_component(lock_text: str) -> str:
        """把实例与仓库身份转换为安全的锁文件名。"""
        safe_lock_component = re.sub(r"[^a-z0-9._-]+", "-", lock_text.casefold()).strip("-")
        return safe_lock_component or "gitea-repository"

    def sync_labels(
        self,
        labels: LabelConfig,
        agent_registry: dict[str, AgentSpec] | None = None,
    ) -> None:
        """创建或更新 runner 使用的标准标签。"""
        existing_label_payloads = self._load_repository_labels()
        labels_by_name = {
            str(label_payload.get("name")): label_payload
            for label_payload in existing_label_payloads
            if label_payload.get("name")
        }
        for label_spec in standard_label_specs(labels, agent_registry):
            existing_label = labels_by_name.get(label_spec.effective_name)
            label_payload = {
                "name": label_spec.effective_name,
                "color": label_spec.color,
                "description": label_spec.description,
            }
            if existing_label is None:
                created_label = self._request_json(
                    "POST",
                    self._repository_api_path("labels"),
                    GiteaRequestOptions(json_body=label_payload),
                )
                if isinstance(created_label, dict):
                    labels_by_name[label_spec.effective_name] = created_label
                continue
            label_id = existing_label.get("id")
            if type(label_id) is not int:
                raise RuntimeError(f"Gitea label {label_spec.effective_name!r} has no numeric ID.")
            self._request_json(
                "PATCH",
                self._repository_api_path(f"labels/{label_id}"),
                GiteaRequestOptions(json_body=label_payload),
            )

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        """列出带就绪标签的开放 Issue。"""
        return self.list_issues_by_label(ready_label, limit, state="open")

    def list_rework_prd_issues(self, rework_prd_label: str, limit: int) -> list[IssueSummary]:
        """列出带 rework-prd 标签的开放 Issue。"""
        return self.list_issues_by_label(rework_prd_label, limit, state="open")

    def list_review_candidate_issues(
        self,
        labels: Sequence[str],
        limit: int,
    ) -> list[IssueSummary]:
        """列出命中任一给定标签的开放 Issue 并按编号去重。"""
        candidates_by_number: dict[int, IssueSummary] = {}
        for label_name in labels:
            for issue_summary in self.list_issues_by_label(label_name, limit, state="open"):
                candidates_by_number.setdefault(issue_summary.number, issue_summary)
        return list(candidates_by_number.values())[:limit]

    def list_issues_by_label(
        self,
        label: str | None,
        limit: int,
        state: str = "all",
    ) -> list[IssueSummary]:
        """按标签与状态列出 Issue。"""
        if state not in {"open", "closed", "all"}:
            raise ValueError("state must be 'open', 'closed', or 'all'.")
        if limit < 1:
            return []
        query_params: dict[str, str | int | bool] = {"state": state}
        if label is not None:
            self._ensure_label(label)
            # Gitea 1.21 的 issues.list 接口按标签名称过滤；标签 ID 会被忽略。
            query_params["labels"] = label
        issue_payloads = self._list_pages(self._repository_api_path("issues"), params=query_params)
        issue_summaries = [
            _build_issue_summary(issue_payload)
            for issue_payload in issue_payloads
            if not issue_payload.get("pull_request")
        ]
        return issue_summaries[:limit]

    def list_repository_items(self, *, state: str = "open") -> list[IssueSummary]:
        """列出仓库全部 Issue 与 PR，保留正文和状态。"""
        if state not in {"open", "closed", "all"}:
            raise ValueError("state must be 'open', 'closed', or 'all'.")
        issue_payloads = self._list_pages(
            self._repository_api_path("issues"),
            params={"state": state},
        )
        return [_build_issue_summary(issue_payload) for issue_payload in issue_payloads]

    def get_issue(self, issue_number: int) -> IssueSummary:
        """读取指定 Issue 摘要。"""
        issue_payload = self._request_json(
            "GET",
            self._repository_api_path(f"issues/{issue_number}"),
        )
        if not isinstance(issue_payload, dict):
            raise RuntimeError(f"Gitea Issue #{issue_number} response is incomplete.")
        return _build_issue_summary(issue_payload)

    def edit_issue_labels(
        self,
        issue_number: int,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
    ) -> None:
        """为 Issue 添加或移除标签。"""
        current_label_names = self._list_issue_label_names(issue_number)
        label_ids_to_add = self._label_ids(
            [label_name for label_name in add if label_name not in current_label_names]
        )
        labels_requested_for_add = set(add)
        labels_to_remove = [
            label_name
            for label_name in remove
            if label_name in current_label_names and label_name not in labels_requested_for_add
        ]
        if label_ids_to_add:
            self._request_json(
                "POST",
                self._repository_api_path(f"issues/{issue_number}/labels"),
                GiteaRequestOptions(json_body={"labels": label_ids_to_add}),
            )
        for label_name in labels_to_remove:
            label_payload = next(
                label_payload
                for label_payload in self._load_repository_labels()
                if label_payload.get("name") == label_name
            )
            label_id = label_payload.get("id")
            if type(label_id) is not int:
                raise RuntimeError(f"Gitea label {label_name!r} has no numeric ID.")
            self._request_json(
                "DELETE",
                self._repository_api_path(f"issues/{issue_number}/labels/{label_id}"),
            )

    def comment_issue(self, issue_number: int, body: str) -> None:
        """向 Issue 发布 Markdown 评论。"""
        self._request_json(
            "POST",
            self._repository_api_path(f"issues/{issue_number}/comments"),
            GiteaRequestOptions(json_body={"body": body}),
        )

    def edit_issue_body(self, issue_number: int, body: str) -> None:
        """整体替换 Issue 正文。"""
        self._request_json(
            "PATCH",
            self._repository_api_path(f"issues/{issue_number}"),
            GiteaRequestOptions(json_body={"body": body}),
        )

    def presence_creation_lock(self) -> AbstractContextManager[None]:
        """按 Gitea 主机与仓库名锁定本机状态板初始化。

        Returns:
            文件锁上下文管理器。
        """
        repository_lock_name = self._build_lock_component(f"{self.host}-{self.owner_repo}")
        return file_lock(state_home() / "presence-locks" / f"{repository_lock_name}.lock")

    @contextmanager
    def issue_supervision_lock(self, issue_number: int) -> Iterator[bool]:
        """按 Gitea 主机、仓库与 Issue 编号争用本机监督权。"""
        repository_lock_name = self._build_lock_component(f"{self.host}-{self.owner_repo}")
        issue_lock_path = (
            state_home() / "supervision-locks" / repository_lock_name / f"{issue_number}.lock"
        )
        with ExitStack() as lock_stack:
            try:
                lock_stack.enter_context(file_lock(issue_lock_path, timeout_seconds=0))
            except TimeoutError:
                yield False
                return
            yield True

    def close_issue(self, issue_number: int) -> None:
        """关闭指定 Issue。"""
        self._request_json(
            "PATCH",
            self._repository_api_path(f"issues/{issue_number}"),
            GiteaRequestOptions(json_body={"state": "closed"}),
        )

    def create_issue(self, *, title: str, body: str, labels: Sequence[str]) -> str:
        """创建 Issue 并返回 Gitea 网页 URL。"""
        label_ids = self._label_ids(labels)
        issue_payload = self._request_json(
            "POST",
            self._repository_api_path("issues"),
            GiteaRequestOptions(json_body={"title": title, "body": body, "labels": label_ids}),
        )
        if not isinstance(issue_payload, dict) or not isinstance(
            issue_payload.get("html_url"), str
        ):
            raise RuntimeError("Gitea did not return the created Issue URL.")
        return issue_payload["html_url"]

    def list_issue_comments(
        self,
        issue_number: int,
        *,
        require_success: bool = False,
    ) -> list[str]:
        """读取 Issue 中的非空评论正文。"""
        return [
            str(comment_payload.get("body") or "")
            for comment_payload in self._load_comment_entries(
                issue_number,
                trusted_only=False,
                body_contains=None,
                require_success=require_success,
            )
            if comment_payload.get("body")
        ]

    def list_issue_comment_entries(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[tuple[int, str]]:
        """读取 Issue 评论 ID 与正文，可选过滤可信作者。"""
        return [
            (int(comment_payload["id"]), str(comment_payload.get("body") or ""))
            for comment_payload in self._load_comment_entries(
                issue_number,
                trusted_only=trusted_only,
                body_contains=body_contains,
                require_success=require_success,
            )
            if type(comment_payload.get("id")) is int
        ]

    def list_issue_comment_records(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[IssueCommentRecord]:
        """读取评论 ID、正文、更新时间与作者。"""
        comment_records: list[IssueCommentRecord] = []
        for comment_payload in self._load_comment_entries(
            issue_number,
            trusted_only=trusted_only,
            body_contains=body_contains,
            require_success=require_success,
        ):
            try:
                comment_id = comment_payload.get("id")
                raw_updated_at = comment_payload.get("updated_at")
                raw_user = comment_payload.get("user")
                author_login = raw_user.get("login") if isinstance(raw_user, dict) else None
                if (
                    type(comment_id) is not int
                    or comment_id <= 0
                    or not isinstance(raw_updated_at, str)
                    or not isinstance(author_login, str)
                    or not author_login
                ):
                    raise ValueError("comment identity is incomplete")
                updated_at = datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
                if updated_at.tzinfo is None:
                    raise ValueError("comment timestamp has no timezone")
            except (AttributeError, TypeError, ValueError) as exc:
                if require_success or trusted_only:
                    raise RuntimeError(
                        f"Gitea Issue #{issue_number} comment metadata is incomplete."
                    ) from exc
                continue
            comment_records.append(
                IssueCommentRecord(
                    comment_id=comment_id,
                    body=str(comment_payload.get("body") or ""),
                    updated_at=updated_at.astimezone(timezone.utc),
                    author=author_login,
                )
            )
        return comment_records

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        """整体替换指定评论正文。"""
        self._request_json(
            "PATCH",
            f"repos/{quote(self.owner_repo, safe='/')}/issues/comments/{comment_id}",
            GiteaRequestOptions(json_body={"body": body}),
        )

    def delete_issue_comment(self, comment_id: int) -> None:
        """删除指定评论。"""
        self._request_json(
            "DELETE",
            f"repos/{quote(self.owner_repo, safe='/')}/issues/comments/{comment_id}",
        )

    def create_draft_pr(
        self,
        *,
        title: str,
        body: str,
        base_branch: str,
        cwd: Path,
    ) -> str:
        """从当前分支创建带 WIP 标题前缀的 Gitea 草稿 PR。"""
        return _create_draft_pr(
            self,
            GiteaDraftPullRequest(
                title=title,
                body=body,
                base_branch=base_branch,
                cwd=cwd,
            ),
        )

    def get_pull_request_context(
        self,
        branch: str,
        *,
        require_success: bool = False,
    ) -> PullRequestContext | None:
        """读取来源分支上的开放 PR 上下文。"""
        return _get_pull_request_context(self, branch, require_success=require_success)

    def get_pull_request_context_by_number(
        self,
        pr_number: int,
        *,
        require_success: bool = False,
    ) -> PullRequestContext | None:
        """按编号读取开放或已关闭 PR 上下文。"""
        return _get_pull_request_context_by_number(
            self,
            pr_number,
            require_success=require_success,
        )

    def comment_pr(self, pr_number: int, body: str) -> None:
        """向 PR conversation 发布评论。"""
        _comment_pr(self, pr_number, body)

    def set_pull_request_base(self, pr_number: int, base_branch: str) -> None:
        """更新 PR 目标分支。"""
        _set_pull_request_base(self, pr_number, base_branch)

    def mark_pull_request_ready(self, pr_number: int) -> None:
        """移除 WIP 前缀，将草稿 PR 转为可审阅状态。"""
        _mark_pull_request_ready(self, pr_number)

    def close_pull_request(self, pr_number: int, *, comment: str | None = None) -> None:
        """关闭 PR，并可选写入一条说明评论。"""
        _close_pull_request(self, pr_number, comment=comment)

    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        """整体更新 PR 正文。"""
        _update_pull_request_body(self, pr_number, body)

    def merge_pull_request(self, pr_number: int, *, method: str = "squash") -> None:
        """按 squash 方式合并 PR。"""
        _merge_pull_request(self, pr_number, method=method)

    def list_pr_comments(self, pr_number: int) -> list[str]:
        """读取 PR conversation 评论正文。"""
        return _list_pr_comments(self, pr_number)

    def find_open_pr_by_head(
        self,
        branch: str,
        *,
        require_success: bool = False,
    ) -> str | None:
        """按来源分支查找开放 PR URL。"""
        return _find_open_pr_by_head(self, branch, require_success=require_success)

    def find_merged_pr_by_head(
        self,
        branch: str,
        *,
        require_success: bool = False,
    ) -> str | None:
        """按来源分支查找已合并 PR URL。"""
        return _find_merged_pr_by_head(self, branch, require_success=require_success)

    def get_remote_base_sha(self, remote: str, base_branch: str) -> str:
        """通过 Git 读取远端跟踪分支 SHA。"""
        return _get_remote_base_sha(self, remote, base_branch)

    def list_pull_requests_for_issue(
        self,
        repo: str,
        issue_number: int,
    ) -> list[PullRequestSummary]:
        """列出正文引用指定 Issue 的 PR。

        Args:
            repo: ``owner/repo`` 格式的仓库标识。
            issue_number: 被引用的 Issue 编号。

        Returns:
            按 PR 状态与编号排序的 Pull Request 摘要列表。
        """
        return _list_pull_requests_for_issue(self, repo, issue_number)


__all__ = ["GiteaApiClient", "GiteaApiError", "parse_gitea_remote_url"]
