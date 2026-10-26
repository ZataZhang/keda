"""Pytest configuration for local imports."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import pytest

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import (
    AuditEntry,
    IBacklogStore,
    BacklogQueueEntry,
    BacklogSettingsEntry,
)
from backend.core.shared.models.agent_runner import (
    CommandResult,
    IssueCommentRecord,
    IssueSummary,
    LabelConfig,
    PullRequestContext,
    PullRequestSummary,
)


def _ensure_project_root_on_path() -> None:
    """Ensure project root is on sys.path for local package imports."""
    project_root_path = Path(__file__).resolve().parents[1]
    if str(project_root_path) not in sys.path:
        sys.path.insert(0, str(project_root_path))


_ensure_project_root_on_path()

_PRD_CONTRACT_SCRIPT_RELATIVE_PATH = Path("scripts") / "prd_contract.py"


def _find_usable_prd_skill() -> Path | None:
    """找出本会话可用的 prd skill（**必须**带解析脚本），找不到返回 ``None``。

    keda 不再自带 PRD 格式解析：章节/分组/复选框由 prd skill 的
    ``scripts/prd_contract.py`` 解析。因此仓库内那份只写版本标记的桩 SKILL.md
    已经不够用了——桩文件解析不出任何结构，会让所有走门禁/backlog 的用例失败。
    这里要求一个**真实安装**的 skill（含解析脚本）。

    Returns:
        可用的 ``SKILL.md`` 路径；先按生产解析器的新旧变量优先级解析，
        再检查旧变量指定的兼容候选。
    """
    from backend.core.shared.prd_skill_location import resolve_prd_skill_path

    candidate_skill_paths: list[Path] = [resolve_prd_skill_path()]
    ambient_skill_path = os.environ.get("KEDACODE_PRD_SKILL_PATH")
    if ambient_skill_path:
        candidate_skill_paths.append(Path(ambient_skill_path).expanduser())
    for candidate_skill_path in candidate_skill_paths:
        if (candidate_skill_path.parent / _PRD_CONTRACT_SCRIPT_RELATIVE_PATH).is_file():
            return candidate_skill_path
    return None


@pytest.fixture(autouse=True, scope="session")
def _prd_skill_contract_env() -> Iterator[Path | None]:
    """把会话级 ``KEDACODE_PRD_SKILL_PATH`` 指向真实安装的 prd skill。

    解析到真实 skill 时固定为会话级取值，让依赖 PRD 解析的用例确定性地走同一份
    契约实现；解析不到时不设置变量（保持环境原样），让运行时按用户级安装解析——
    缺失会由 ``prd_contract_client`` 报出可行动的错，而不是在这里静默跳过用例。

    要触发预检失败路径的用例照旧用 ``monkeypatch.setenv("KEDACODE_PRD_SKILL_PATH", ...)``
    覆盖，用例结束后会还原到这里的会话级取值。宿主的新变量仅用于找到真实
    skill；进入测试会话后移除它，防止覆盖单测显式设置的旧变量。双名优先级
    由专门的解析测试显式设置两名验证，会话结束恢复两名宿主取值。
    """
    usable_skill_path = _find_usable_prd_skill()
    if usable_skill_path is None:
        yield None
        return
    previous_values = {
        env_name: os.environ.get(env_name)
        for env_name in ("KEDACODE_PRD_SKILL_PATH", "KEDACODE_PRD_SKILL_PATH")
    }
    os.environ.pop("KEDACODE_PRD_SKILL_PATH", None)
    os.environ["KEDACODE_PRD_SKILL_PATH"] = str(usable_skill_path)
    try:
        yield usable_skill_path
    finally:
        for env_name, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous_value


@pytest.fixture(autouse=True, scope="session")
def _isolate_ambient_iar_config() -> Iterator[None]:
    """在 pytest 会话期间剥离宿主注入的新旧 CONFIG，保证默认配置解析可测。

    托管 runner 向子进程注入 ``KEDACODE_CONFIG`` 与 ``KEDACODE_CONFIG``，指向机器级
    配置。默认解析测试需要从 cwd 查找或临时 HOME 回落，不能被宿主配置覆盖。
    需要覆盖的用例仍用 ``monkeypatch.setenv`` 显式设置；结束恢复两名宿主取值。

    剥离变量只影响**之后重新解析**配置的读取：``settings.config`` 单例在 conftest
    导入 ``backend`` 时（早于任何 fixture）就已按宿主机注入的机器级 ``config.toml``
    定型，``build_app_config()`` 会一直吃到那份漂移值。因此这里在剥离宿主变量后
    重建单例的 ``agent_runner`` 段，让本地/托管 runner 的会话与 CI（无宿主注入、
    按仓库根解析）看到同一份配置。断言仓库根 ``config.toml`` 里某段真实内容的 golden
    用例仍应显式 pin 来源（``tests.support.agent_runner.root_config_app_config()``），
    不依赖本 fixture 的解析结果。
    """
    from backend.infrastructure.config import settings as app_settings_module
    from backend.infrastructure.config.settings import AgentRunnerSettings

    previous_values = {
        env_name: os.environ.pop(env_name, None)
        for env_name in ("KEDACODE_CONFIG", "KEDACODE_CONFIG")
    }
    app_settings_module.config.agent_runner = AgentRunnerSettings()
    try:
        yield
    finally:
        for env_name, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous_value


class FakeGitHubClient(IGitHubClient):
    """In-memory GitHub client for tests."""

    def __init__(self, issue_url: str = "https://github.com/example/repo/issues/42") -> None:
        self._issue_url = issue_url
        self.calls: list[dict] = []
        self._issue_comments: dict[int, list[str]] = {}
        self._issue_comment_entries: dict[int, list[tuple[int, str]]] = {}
        self._issue_comment_updated_at: dict[int, datetime] = {}
        self._next_comment_id = 1
        self._issue_bodies: dict[int, str] = {}
        self._issue_get_bodies: dict[int, str] = {}
        self._get_issue_error: Exception | None = None
        self._pr_bodies: dict[int, str] = {}
        self._pr_comments: dict[int, list[str]] = {}
        self._pr_contexts: dict[str, object | None] = {}
        self._open_prs: dict[str, str | None] = {}
        self._merged_prs: dict[str, str | None] = {}
        self._set_pr_base_error: Exception | None = None
        self._remote_base_sha: str = "remote-base-sha"
        self._issue_states: dict[int, str] = {}
        self._issue_title: str | None = None
        self._issue_labels: dict[int, tuple[str, ...]] = {}
        self._repository_items: list[IssueSummary] = []
        self._rework_prd_issues: list[IssueSummary] = []
        self._prs_by_repo_issue: dict[tuple[str, int], list[PullRequestSummary]] = {}
        self._list_issues_by_label_result: list[IssueSummary] = []

    def sync_labels(
        self, labels: LabelConfig, agent_registry: dict[str, object] | None = None
    ) -> None:
        self.calls.append(
            {"method": "sync_labels", "labels": labels, "agent_registry": agent_registry}
        )

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        self.calls.append(
            {"method": "list_ready_issues", "ready_label": ready_label, "limit": limit}
        )
        return []

    def list_rework_prd_issues(self, rework_prd_label: str, limit: int) -> list[IssueSummary]:
        self.calls.append(
            {
                "method": "list_rework_prd_issues",
                "rework_prd_label": rework_prd_label,
                "limit": limit,
            }
        )
        return self._rework_prd_issues[:limit]

    def set_rework_prd_issues(self, issues: list[IssueSummary]) -> None:
        self._rework_prd_issues = issues

    def set_repository_items(self, repository_items: list[IssueSummary]) -> None:
        """设置显式仓库扫描返回值，用于迁移命令测试。"""
        self._repository_items = list(repository_items)

    def list_repository_items(self, *, state: str = "open") -> list[IssueSummary]:
        self.calls.append({"method": "list_repository_items", "state": state})
        if state == "all":
            repository_items = list(self._repository_items)
        else:
            repository_items = [
                repository_item
                for repository_item in self._repository_items
                if repository_item.state.lower() == state
            ]
        return [
            replace(
                repository_item,
                body=self._issue_bodies.get(repository_item.number, repository_item.body),
            )
            for repository_item in repository_items
        ]

    def edit_issue_labels(
        self, issue_number: int, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        self.calls.append(
            {
                "method": "edit_issue_labels",
                "issue_number": issue_number,
                "add": list(add),
                "remove": list(remove),
            }
        )
        current = set(self._issue_labels.get(issue_number, ()))
        current.update(add)
        current.difference_update(remove)
        self._issue_labels[issue_number] = tuple(current)

    def comment_issue(self, issue_number: int, body: str) -> None:
        self.calls.append({"method": "comment_issue", "issue_number": issue_number, "body": body})
        self._issue_comments.setdefault(issue_number, []).append(body)
        comment_id = self._next_comment_id
        self._next_comment_id += 1
        self._issue_comment_entries.setdefault(issue_number, []).append((comment_id, body))
        self._issue_comment_updated_at[comment_id] = datetime.now(timezone.utc)

    def edit_issue_body(self, issue_number: int, body: str) -> None:
        self.calls.append({"method": "edit_issue_body", "issue_number": issue_number, "body": body})
        self._issue_bodies[issue_number] = body

    def get_issue_body(self, issue_number: int) -> str | None:
        return self._issue_bodies.get(issue_number)

    def comment_pr(self, pr_number: int, body: str) -> None:
        self.calls.append({"method": "comment_pr", "pr_number": pr_number, "body": body})
        self._pr_comments.setdefault(pr_number, []).append(body)

    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        self.calls.append(
            {
                "method": "update_pull_request_body",
                "pr_number": pr_number,
                "body": body,
            }
        )

    def set_pull_request_base(self, pr_number: int, base_branch: str) -> None:
        self.calls.append(
            {
                "method": "set_pull_request_base",
                "pr_number": pr_number,
                "base_branch": base_branch,
            }
        )
        if self._set_pr_base_error is not None:
            raise self._set_pr_base_error

    def set_set_pr_base_error(self, error: Exception | None) -> None:
        """Inject a failure for ``set_pull_request_base`` (stack convergence tests)."""
        self._set_pr_base_error = error

    def mark_pull_request_ready(self, pr_number: int) -> None:
        """记录模拟的 Draft-to-Ready 状态转换。"""
        self.calls.append({"method": "mark_pull_request_ready", "pr_number": pr_number})
        for pr_branch, pr_context in self._pr_contexts.items():
            if isinstance(pr_context, PullRequestContext) and pr_context.number == pr_number:
                self._pr_contexts[pr_branch] = replace(pr_context, is_draft=False)
                return

    def merge_pull_request(self, pr_number: int, *, method: str = "squash") -> None:
        self.calls.append(
            {
                "method": "merge_pull_request",
                "pr_number": pr_number,
                "method_kwarg": method,
            }
        )
        if method != "squash":
            raise ValueError(f"merge_pull_request method must be 'squash'; got {method!r}.")

    def create_issue(self, *, title: str, body: str, labels: Sequence[str]) -> str:
        self.calls.append(
            {
                "method": "create_issue",
                "title": title,
                "body": body,
                "labels": list(labels),
            }
        )
        return self._issue_url

    def create_draft_pr(self, *, title: str, body: str, base_branch: str, cwd: Path) -> str:
        pull_request_url = "https://github.com/example/repo/pull/1"
        self._open_prs[cwd.name] = pull_request_url
        self.calls.append(
            {
                "method": "create_draft_pr",
                "title": title,
                "body": body,
                "base_branch": base_branch,
            }
        )
        return pull_request_url

    def list_review_candidate_issues(self, labels: Sequence[str], limit: int) -> list[IssueSummary]:
        self.calls.append(
            {
                "method": "list_review_candidate_issues",
                "labels": list(labels),
                "limit": limit,
            }
        )
        return []

    def get_pull_request_context(
        self, branch: str, *, require_success: bool = False
    ) -> object | None:
        """返回成功读取的 PR fixture；真实查询失败由 adapter 测试覆盖。

        Args:
            branch: 测试来源分支。
            require_success: 保留真实端口的严格读取选项。

        Returns:
            测试上下文；None 代表成功查询后没有 PR。
        """
        self.calls.append({"method": "get_pull_request_context", "branch": branch})
        return self._pr_contexts.get(branch)

    def get_pull_request_context_by_number(
        self, pr_number: int, *, require_success: bool = False
    ) -> object | None:
        """按编号返回已登记的 PR fixture。"""
        self.calls.append({"method": "get_pull_request_context_by_number", "pr_number": pr_number})
        return next(
            (
                context
                for context in self._pr_contexts.values()
                if getattr(context, "number", None) == pr_number
            ),
            None,
        )

    def set_pr_context(self, branch: str, context: object | None) -> None:
        """Inject a deterministic ``get_pull_request_context`` response for tests."""
        self._pr_contexts[branch] = context

    def set_pr_body_for_issue(self, issue_number: int, body: str) -> None:
        """Make ``update_pull_request_body`` reflect the latest body on read."""
        self._pr_bodies[issue_number] = body

    def get_pr_body_for_issue(self, issue_number: int) -> str | None:
        return self._pr_bodies.get(issue_number)

    def list_issue_comments(self, issue_number: int, *, require_success: bool = False) -> list[str]:
        self.calls.append({"method": "list_issue_comments", "issue_number": issue_number})
        return list(self._issue_comments.get(issue_number, []))

    def list_issue_comment_entries(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[tuple[int, str]]:
        """返回已授权测试 fixture 的评论，权限判定由真实 adapter 测试覆盖。

        Args:
            issue_number: 测试 Issue 编号。
            trusted_only: 保留真实端口选项；fixture 评论均视为已授权。
            body_contains: 可选正文子串筛选。

        Returns:
            测试存储中的评论 ID 与正文列表。
        """
        self.calls.append(
            {
                "method": "list_issue_comment_entries",
                "issue_number": issue_number,
                "trusted_only": trusted_only,
                "body_contains": body_contains,
                "require_success": require_success,
            }
        )
        return [
            (comment_id, body)
            for comment_id, body in self._issue_comment_entries.get(issue_number, [])
            if body_contains is None or body_contains in body
        ]

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        self.calls.append({"method": "edit_issue_comment", "comment_id": comment_id, "body": body})
        for entries in self._issue_comment_entries.values():
            for index, (existing_id, _) in enumerate(entries):
                if existing_id == comment_id:
                    entries[index] = (comment_id, body)
                    self._issue_comment_updated_at[comment_id] = datetime.now(timezone.utc)
                    return

    def delete_issue_comment(self, comment_id: int) -> None:
        self.calls.append({"method": "delete_issue_comment", "comment_id": comment_id})
        for issue_number, entries in self._issue_comment_entries.items():
            for index, (existing_id, body) in enumerate(entries):
                if existing_id != comment_id:
                    continue
                entries.pop(index)
                self._issue_comment_updated_at.pop(comment_id, None)
                issue_comments = self._issue_comments.get(issue_number, [])
                if index < len(issue_comments) and issue_comments[index] == body:
                    issue_comments.pop(index)
                return

    def list_issue_comment_records(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[IssueCommentRecord]:
        """为租约与状态用例返回带时间戳的假评论。"""
        self.calls.append({"method": "list_issue_comment_records", "issue_number": issue_number})
        return [
            IssueCommentRecord(
                comment_id=comment_id,
                body=body,
                updated_at=self._issue_comment_updated_at.get(
                    comment_id, datetime.now(timezone.utc)
                ),
                author="fake-user",
            )
            for comment_id, body in self._issue_comment_entries.get(issue_number, [])
            if body_contains is None or body_contains in body
        ]

    def list_pr_comments(self, pr_number: int) -> list[str]:
        self.calls.append({"method": "list_pr_comments", "pr_number": pr_number})
        return list(self._pr_comments.get(pr_number, []))

    def find_open_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        self.calls.append({"method": "find_open_pr_by_head", "branch": branch})
        return self._open_prs.get(branch)

    def find_merged_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        self.calls.append({"method": "find_merged_pr_by_head", "branch": branch})
        return self._merged_prs.get(branch)

    def get_remote_base_sha(self, remote: str, base_branch: str) -> str:
        self.calls.append(
            {
                "method": "get_remote_base_sha",
                "remote": remote,
                "base_branch": base_branch,
            }
        )
        return self._remote_base_sha

    def get_issue(self, issue_number: int) -> IssueSummary:
        self.calls.append({"method": "get_issue", "issue_number": issue_number})
        if self._get_issue_error is not None:
            raise self._get_issue_error
        title = self._issue_title if self._issue_title is not None else f"Issue #{issue_number}"
        return IssueSummary(
            number=issue_number,
            title=title,
            url=f"https://github.com/example/repo/issues/{issue_number}",
            body=self._issue_get_bodies.get(issue_number, ""),
            labels=self._issue_labels.get(issue_number, ()),
            state=self._issue_states.get(issue_number, "OPEN"),
        )

    def close_issue(self, issue_number: int) -> None:
        """把 Issue 置为 ``CLOSED``，用于布置「执行期间被关闭」这一 GitHub 事实。

        测试只表达「Issue 关了」这个意图，不直接写假对象的内部字段名。
        """
        self._issue_states[issue_number] = "CLOSED"

    def set_issue_labels(self, issue_number: int, labels: Sequence[str]) -> None:
        """注入 Issue 当前标签，不记成 ``edit_issue_labels`` 调用（用于布置初始状态）。"""
        self._issue_labels[issue_number] = tuple(labels)

    def set_issue_body(self, issue_number: int, body: str) -> None:
        """注入 ``get_issue`` 读到的正文（PRD 锚点判定的输入）。

        与 ``edit_issue_body`` 分开存：后者记录「客户端被要求改正文」，这里布置的
        是「GitHub 上当前正文」这一初始状态。
        """
        self._issue_get_bodies[issue_number] = body

    def set_get_issue_error(self, error: Exception | None) -> None:
        """让 ``get_issue`` 抛错，用于 fresh read 的 fail-closed 场景。"""
        self._get_issue_error = error

    def list_issues_by_label(
        self, label: str, limit: int, state: str = "all"
    ) -> list[IssueSummary]:
        self.calls.append(
            {
                "method": "list_issues_by_label",
                "label": label,
                "limit": limit,
                "state": state,
            }
        )
        return list(self._list_issues_by_label_result[:limit])

    def set_list_issues_by_label_result(self, issues: list[IssueSummary]) -> None:
        self._list_issues_by_label_result = list(issues)

    def set_prs_for_repo_issue(
        self, repo: str, issue_number: int, pulls: list[PullRequestSummary]
    ) -> None:
        self._prs_by_repo_issue[(repo, issue_number)] = list(pulls)

    def list_pull_requests_for_issue(
        self, repo: str, issue_number: int
    ) -> list[PullRequestSummary]:
        self.calls.append(
            {
                "method": "list_pull_requests_for_issue",
                "repo": repo,
                "issue_number": issue_number,
            }
        )
        return list(self._prs_by_repo_issue.get((repo, issue_number), []))


class FakeContentGenerator(IContentGenerator):
    """In-memory content generator for tests."""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        cwd: Path,
        timeout: int | None = None,
        model_selection: object = None,
    ) -> CommandResult:
        self.calls.append([agent_name, prompt[:50]])
        self.prompts.append(prompt)
        return CommandResult(
            command=("generate", agent_name),
            return_code=0,
            stdout=self.response,
            stderr="",
        )


class FailingContentGenerator(FakeContentGenerator):
    """``generate`` 直接抛出 ``error``，模拟 agent 超时或 CLI 未安装。"""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        cwd: Path,
        timeout: int | None = None,
        model_selection: object = None,
    ) -> CommandResult:
        self.calls.append([agent_name, prompt[:50]])
        raise self.error


class FakeProcessRunner(IProcessRunner):
    """In-memory process runner for tests."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.raw_calls: list[list[str]] = []
        self.input_texts: list[str | None] = []
        self.labels: list[str | None] = []
        self.timeouts: list[int | None] = []
        self.inactivity_timeouts: list[int | None] = []
        self.output_sinks: list[Callable[[str], None] | None] = []
        self.attempt_keys: list[str | None] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        inactivity_timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
        label: str | None = None,
        output_sink: Callable[[str], None] | None = None,
        output_protocol: str | None = None,
        env_profile: str | None = None,
        env_allow_extra: Sequence[str] = (),
        attempt_key: str | None = None,
    ) -> CommandResult:
        # attempt_key 是停滞监督的登记键（见 IProcessRunner 端口），假实现不登记
        # 进程，只做记录供断言。
        del env_profile, env_allow_extra
        self.attempt_keys.append(attempt_key)
        command_list = list(command)
        # When the runner wraps verification commands in ``bash -lc``,
        # unwrap transparently so test assertions can keep matching the
        # inner command tuple (e.g. ``("just", "lint")``). The recorded
        # call shape stays stable for any test that asserts on
        # ``fake_runner.calls``; ``raw_calls`` preserves the original
        # (wrapped) shape for tests that assert the wrapping itself.
        if len(command_list) == 3 and command_list[0] == "bash" and command_list[1] == "-lc":
            inner_command_text = command_list[2]
            try:
                inner_tokens = shlex.split(inner_command_text)
            except ValueError:
                inner_tokens = [inner_command_text]
            recorded_call = inner_tokens
        else:
            recorded_call = command_list
        self.calls.append(recorded_call)
        self.raw_calls.append(command_list)
        self.input_texts.append(input_text)
        self.labels.append(label)
        self.timeouts.append(timeout)
        self.inactivity_timeouts.append(inactivity_timeout)
        self.output_sinks.append(output_sink)
        key = tuple(command)
        if key in self.responses:
            result = self.responses[key]
            if check and result.return_code != 0:
                raise RuntimeError(f"Command failed: {command}")
            if not capture_output:
                return CommandResult(
                    command=result.command,
                    return_code=result.return_code,
                    stdout="",
                    stderr="",
                )
            return result
        # When the call was a ``bash -lc`` wrap, fall back to matching the
        # inner command tuple against test responses. This keeps the
        # per-test FakeProcessRunner subclasses (``_LintExhaustedRunner``
        # etc.) that register ``("just", "lint")`` working after the
        # runner started wrapping verification commands in bash.
        if recorded_call is not command_list:
            inner_key = tuple(recorded_call)
            if inner_key in self.responses:
                result = self.responses[inner_key]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                if not capture_output:
                    return CommandResult(
                        command=result.command,
                        return_code=result.return_code,
                        stdout="",
                        stderr="",
                    )
                return result
        return CommandResult(command=tuple(command), return_code=0, stdout="", stderr="")


class FakeBacklogStore(IBacklogStore):
    """In-memory backlog queue store that records every write.

    Shared by the backlog tests so a scheduling pass can be asserted on both
    its resulting queue state and the exact number of writes it performed
    (idempotency and dry-run guarantees depend on the latter).
    """

    def __init__(self, *, repo_id: str = "keda-test", max_parallel: int | None = 1) -> None:
        self.repo_id = repo_id
        self._entries: list[BacklogQueueEntry] = []
        self._next_entry_id = 1
        # 「未设置」= 没有设置行：max_parallel 为 None 时不物化行，与 SQLite
        # 侧的行缺失语义一致。
        self.settings: BacklogSettingsEntry | None = (
            None
            if max_parallel is None
            else BacklogSettingsEntry(
                repo_id=repo_id,
                max_parallel=max_parallel,
                default_view="list",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        self.enqueue_calls: list[BacklogQueueEntry] = []
        self.update_calls: list[dict] = []
        self.clear_calls = 0
        self.delete_settings_calls: list[str] = []
        self.save_settings_calls = 0
        self.audits: list[AuditEntry] = []

    # -- reads ---------------------------------------------------------------
    def get_backlog_settings(self, repo_id: str) -> BacklogSettingsEntry | None:
        if repo_id != self.repo_id:
            return None
        return self.settings

    def list_backlog_queue(
        self, *, repo_id: str | None = None, status: str | None = None
    ) -> list[BacklogQueueEntry]:
        entries = list(self._entries)
        if repo_id is not None:
            entries = [entry for entry in entries if entry.repo_id == repo_id]
        if status is not None:
            entries = [entry for entry in entries if entry.status == status]
        return entries

    # -- writes --------------------------------------------------------------
    def save_backlog_settings(self, settings: BacklogSettingsEntry) -> None:
        self.save_settings_calls += 1
        self.settings = settings

    def delete_backlog_settings(self, repo_id: str) -> None:
        self.delete_settings_calls.append(repo_id)
        if repo_id == self.repo_id:
            self.settings = None

    def enqueue_backlog(self, entry: BacklogQueueEntry) -> int:
        self.enqueue_calls.append(entry)
        stored_entry = replace(entry, entry_id=self._next_entry_id)
        self._next_entry_id += 1
        self._entries.append(stored_entry)
        return int(stored_entry.entry_id)

    def update_backlog_queue_status(
        self,
        *,
        entry_id: int,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        self.update_calls.append(
            {
                "entry_id": entry_id,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "error_detail": error_detail,
            }
        )
        for index, entry in enumerate(self._entries):
            if entry.entry_id == entry_id:
                self._entries[index] = replace(
                    entry,
                    status=status,
                    started_at=started_at if started_at is not None else entry.started_at,
                    finished_at=finished_at,
                    error_detail=error_detail,
                )
                return
        raise KeyError(f"unknown queue entry {entry_id}")

    def clear_backlog_queue(self, *, repo_id: str | None = None) -> None:
        self.clear_calls += 1
        if repo_id is None:
            self._entries.clear()
            return
        self._entries = [entry for entry in self._entries if entry.repo_id != repo_id]

    def append_audit(self, audit_entry: AuditEntry) -> None:
        self.audits.append(audit_entry)

    # -- test helpers --------------------------------------------------------
    def seed(self, prd_path: str, status: str) -> int:
        """Insert a queue entry directly and return its id."""
        entry_id = self._next_entry_id
        self._next_entry_id += 1
        self._entries.append(
            BacklogQueueEntry(
                repo_id=self.repo_id,
                prd_path=prd_path,
                status=status,
                trigger="global",
                started_at="2026-01-01T00:00:00+00:00",
                finished_at=None,
                error_detail=None,
                entry_id=entry_id,
            )
        )
        return entry_id

    def entry_for(self, prd_path: str) -> BacklogQueueEntry:
        entry = next(
            (entry for entry in self._entries if entry.prd_path == prd_path),
            None,
        )
        assert entry is not None, f"no queue entry for {prd_path}"
        return entry

    @property
    def write_count(self) -> int:
        """Total number of store writes (enqueue + update + clear)."""
        return len(self.enqueue_calls) + len(self.update_calls) + self.clear_calls


@pytest.fixture(autouse=True, scope="session")
def _isolate_github_api_budget(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """把 GitHub API 额度账本指到临时目录，测试不会写进真实的本机状态目录。

    ``create_github_client`` 现在会给每个 client 接上跨进程共享的额度账本；不隔离的话，
    任何走工厂创建 client 的测试都可能读到（甚至写入）开发机上真实的暂停状态。
    """
    from backend.engines.agent_runner.persistence.github_api_budget_json import (
        BUDGET_PATH_ENV_VAR,
    )

    previous_value = os.environ.get(BUDGET_PATH_ENV_VAR)
    os.environ[BUDGET_PATH_ENV_VAR] = str(
        tmp_path_factory.mktemp("github-api-budget") / "github-api-budget.json"
    )
    try:
        yield
    finally:
        if previous_value is None:
            os.environ.pop(BUDGET_PATH_ENV_VAR, None)
        else:
            os.environ[BUDGET_PATH_ENV_VAR] = previous_value


@pytest.fixture(autouse=True, scope="session")
def _isolate_issue_snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """把认领快照指到临时家目录，测试不会写进真实的本机状态目录。

    ``_process_ready_issue`` 在认领路径上会追加任务输入快照；不隔离的话，任何驱动该
    处理器的测试都会在开发机真实的 ``~/.kedacode/dataset/`` 下留下假 Issue 的快照。
    显式传入 ``home_path`` 的用例不受影响。
    """
    from backend.core.use_cases import agent_runner_issue_snapshot as snapshot_module

    isolated_home_path = tmp_path_factory.mktemp("issue-snapshot-home")
    original_snapshot_file_path = snapshot_module.snapshot_file_path

    def _isolated_snapshot_file_path(
        *, repo_id: str, issue_number: int, home_path: Path | None = None
    ) -> Path:
        return original_snapshot_file_path(
            repo_id=repo_id, issue_number=issue_number, home_path=home_path or isolated_home_path
        )

    session_monkeypatch = pytest.MonkeyPatch()
    session_monkeypatch.setattr(snapshot_module, "snapshot_file_path", _isolated_snapshot_file_path)
    try:
        yield
    finally:
        session_monkeypatch.undo()


@pytest.fixture(autouse=True, scope="session")
def _isolate_machine_identity(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """把机器身份文件指到临时状态目录，测试不会写进真实的 ``~/.kedacode``。

    没有心跳的入口（直接驱动 core use case 的用例）会经持久身份解析机器编号；不隔离
    的话，这些用例会在开发机上创建真实的 ``machine.json`` 与槽位锁。显式传入
    ``machine_file`` 的用例不受影响。
    """
    from backend.infrastructure import machine_identity_store as identity_module

    isolated_state_dir = tmp_path_factory.mktemp("machine-identity")
    session_monkeypatch = pytest.MonkeyPatch()
    session_monkeypatch.setattr(
        identity_module, "state_home", lambda home_path=None: isolated_state_dir
    )
    try:
        yield
    finally:
        session_monkeypatch.undo()


# ---------------------------------------------------------------------------
# 失败现场落盘：偶发失败（testmon 子集、机器负载、常驻 daemon 并发写状态目录）事后
# 无法复现，终端输出又常被 tail 截掉。只要有用例失败，就把完整报错与运行环境写到
# ``logs/test-failures/``（已被 .gitignore 覆盖），不依赖谁、用什么命令跑的测试。
# ---------------------------------------------------------------------------
_FAILURE_LOG_DIR_NAME = "test-failures"
_FAILURE_LOG_KEEP = 30
_DESELECTED_COUNT_KEY = pytest.StashKey[list]()
_FAILED_REPORTS: list[pytest.TestReport] = []


def pytest_configure(config: pytest.Config) -> None:
    """为本次会话准备失败报告与 deselected 计数的暂存区。"""
    config.stash[_DESELECTED_COUNT_KEY] = [0]


def pytest_deselected(items: Sequence[pytest.Item]) -> None:
    """累计被 testmon/-m 过滤掉的用例数，复盘时用来判断这次跑的是不是子集。"""
    # 钩子拿不到 config，借 items 的 session 取暂存区
    if items:
        items[0].config.stash[_DESELECTED_COUNT_KEY][0] += len(items)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """收集 setup/call/teardown 任一阶段的失败报告（含捕获的 stdout/stderr/log）。"""
    if report.failed:
        _FAILED_REPORTS.append(report)


def _describe_competing_processes() -> str:
    """列出本机可能与测试并发写 ~/.iar、~/.kedacode 的 daemon 进程。"""
    import subprocess

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid,etime,command"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"(ps 失败: {error})"
    process_lines = [
        process_line
        for process_line in completed.stdout.splitlines()
        if any(marker in process_line for marker in ("iar daemon", "kc daemon", "iar console"))
        and "ps -axo" not in process_line
    ]
    return "\n".join(process_lines) or "(无)"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """有失败时写一份带时间戳的现场文件，并只保留最近 ``_FAILURE_LOG_KEEP`` 份。"""
    import datetime
    import platform

    failed_reports = list(_FAILED_REPORTS)
    _FAILED_REPORTS.clear()
    if not failed_reports:
        return
    try:
        log_directory = Path(session.config.rootpath) / "logs" / _FAILURE_LOG_DIR_NAME
        log_directory.mkdir(parents=True, exist_ok=True)
        started_at = datetime.datetime.now().astimezone()
        deselected_count = session.config.stash[_DESELECTED_COUNT_KEY][0]
        header_lines = [
            f"time: {started_at.isoformat(timespec='seconds')}",
            f"exitstatus: {int(exitstatus)}",
            f"argv: {shlex.join(sys.argv)}",
            f"invocation_args: {shlex.join(session.config.invocation_params.args)}",
            f"collected: {session.testscollected}  failed: {len(failed_reports)}  "
            f"deselected: {deselected_count}",
            f"python: {platform.python_version()}  platform: {platform.platform()}",
            f"loadavg: {os.getloadavg() if hasattr(os, 'getloadavg') else 'n/a'}",
            f"cpu_count: {os.cpu_count()}",
            "competing daemons:",
            _describe_competing_processes(),
        ]
        body_blocks = [
            f"===== {report.when.upper()} FAILED {report.nodeid} =====\n{report.longreprtext}"
            + "".join(f"\n--- {name} ---\n{content}" for name, content in report.sections)
            for report in failed_reports
        ]
        log_path = (
            log_directory / f"failures-{started_at.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
        )
        log_path.write_text(
            "\n".join(header_lines) + "\n\n" + "\n\n".join(body_blocks) + "\n", encoding="utf-8"
        )
        for stale_path in sorted(log_directory.glob("failures-*.log"))[:-_FAILURE_LOG_KEEP]:
            stale_path.unlink(missing_ok=True)
        terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if terminal_reporter is not None:
            terminal_reporter.write_line(f"失败现场已保存: {log_path}")
    except OSError:
        # 记录失败现场是尽力而为，不能反过来让测试会话报错
        pass
