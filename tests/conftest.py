"""Pytest configuration for local imports."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Iterator
from dataclasses import replace
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
    IRoadmapStore,
    RoadmapQueueEntry,
    RoadmapSettingsEntry,
)
from backend.core.shared.models.agent_runner import (
    CommandResult,
    IssueSummary,
    LabelConfig,
    PullRequestSummary,
)


def _ensure_project_root_on_path() -> None:
    """Ensure project root is on sys.path for local package imports."""
    project_root_path = Path(__file__).resolve().parents[1]
    if str(project_root_path) not in sys.path:
        sys.path.insert(0, str(project_root_path))


_ensure_project_root_on_path()

_PRD_SKILL_FIXTURE_PATH = Path(__file__).parent / "support" / "prd_skill_fixtures" / "SKILL_v3.md"


@pytest.fixture(autouse=True, scope="session")
def _prd_skill_contract_env() -> Iterator[Path]:
    """会话级 prd skill 桩：让 Machine Contract 启动预检在测试里确定性通过。

    daemon 开跑前的预检（``run_preflight_checks``）要求能解析到带
    ``Machine-Contract-Version`` 标记的 prd skill；本 fixture 用优先级最高的
    ``IAR_PRD_SKILL_PATH`` 环境变量把解析指向仓库内的 fixture 文件，与执行机器
    上是否安装了真实 skill 解耦。要触发预检失败路径的用例用
    ``monkeypatch.setenv("IAR_PRD_SKILL_PATH", ...)`` 覆盖即可——monkeypatch 在
    用例结束后会还原到这里的会话级取值。
    """
    previous_value = os.environ.get("IAR_PRD_SKILL_PATH")
    os.environ["IAR_PRD_SKILL_PATH"] = str(_PRD_SKILL_FIXTURE_PATH)
    try:
        yield _PRD_SKILL_FIXTURE_PATH
    finally:
        if previous_value is None:
            os.environ.pop("IAR_PRD_SKILL_PATH", None)
        else:
            os.environ["IAR_PRD_SKILL_PATH"] = previous_value


class FakeGitHubClient(IGitHubClient):
    """In-memory GitHub client for tests."""

    def __init__(self, issue_url: str = "https://github.com/example/repo/issues/42") -> None:
        self._issue_url = issue_url
        self.calls: list[dict] = []
        self._issue_comments: dict[int, list[str]] = {}
        self._issue_comment_entries: dict[int, list[tuple[int, str]]] = {}
        self._next_comment_id = 1
        self._issue_bodies: dict[int, str] = {}
        self._pr_bodies: dict[int, str] = {}
        self._pr_comments: dict[int, list[str]] = {}
        self._pr_contexts: dict[str, object | None] = {}
        self._open_prs: dict[str, str | None] = {}
        self._merged_prs: dict[str, str | None] = {}
        self._remote_base_sha: str = "remote-base-sha"
        self._issue_states: dict[int, str] = {}
        self._issue_title: str | None = None
        self._issue_labels: dict[int, tuple[str, ...]] = {}
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
        self.calls.append(
            {
                "method": "create_draft_pr",
                "title": title,
                "body": body,
                "base_branch": base_branch,
            }
        )
        return "https://github.com/example/repo/pull/1"

    def list_review_candidate_issues(self, labels: Sequence[str], limit: int) -> list[IssueSummary]:
        self.calls.append(
            {
                "method": "list_review_candidate_issues",
                "labels": list(labels),
                "limit": limit,
            }
        )
        return []

    def get_pull_request_context(self, branch: str) -> object | None:
        self.calls.append({"method": "get_pull_request_context", "branch": branch})
        return self._pr_contexts.get(branch)

    def set_pr_context(self, branch: str, context: object | None) -> None:
        """Inject a deterministic ``get_pull_request_context`` response for tests."""
        self._pr_contexts[branch] = context

    def set_pr_body_for_issue(self, issue_number: int, body: str) -> None:
        """Make ``update_pull_request_body`` reflect the latest body on read."""
        self._pr_bodies[issue_number] = body

    def get_pr_body_for_issue(self, issue_number: int) -> str | None:
        return self._pr_bodies.get(issue_number)

    def list_issue_comments(self, issue_number: int) -> list[str]:
        self.calls.append({"method": "list_issue_comments", "issue_number": issue_number})
        return list(self._issue_comments.get(issue_number, []))

    def list_issue_comment_entries(self, issue_number: int) -> list[tuple[int, str]]:
        self.calls.append(
            {
                "method": "list_issue_comment_entries",
                "issue_number": issue_number,
            }
        )
        return list(self._issue_comment_entries.get(issue_number, []))

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        self.calls.append({"method": "edit_issue_comment", "comment_id": comment_id, "body": body})
        for entries in self._issue_comment_entries.values():
            for index, (existing_id, _) in enumerate(entries):
                if existing_id == comment_id:
                    entries[index] = (comment_id, body)
                    return

    def list_pr_comments(self, pr_number: int) -> list[str]:
        self.calls.append({"method": "list_pr_comments", "pr_number": pr_number})
        return list(self._pr_comments.get(pr_number, []))

    def find_open_pr_by_head(self, branch: str) -> str | None:
        self.calls.append({"method": "find_open_pr_by_head", "branch": branch})
        return self._open_prs.get(branch)

    def find_merged_pr_by_head(self, branch: str) -> str | None:
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
        title = self._issue_title if self._issue_title is not None else f"Issue #{issue_number}"
        return IssueSummary(
            number=issue_number,
            title=title,
            url=f"https://github.com/example/repo/issues/{issue_number}",
            body="",
            labels=self._issue_labels.get(issue_number, ()),
            state=self._issue_states.get(issue_number, "OPEN"),
        )

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
    ) -> CommandResult:
        self.calls.append([agent_name, prompt[:50]])
        self.prompts.append(prompt)
        return CommandResult(
            command=("generate", agent_name),
            return_code=0,
            stdout=self.response,
            stderr="",
        )


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
    ) -> CommandResult:
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


class FakeRoadmapStore(IRoadmapStore):
    """In-memory roadmap queue store that records every write.

    Shared by the roadmap tests so a scheduling pass can be asserted on both
    its resulting queue state and the exact number of writes it performed
    (idempotency and dry-run guarantees depend on the latter).
    """

    def __init__(self, *, repo_id: str = "keda-test", max_parallel: int = 1) -> None:
        self.repo_id = repo_id
        self._entries: list[RoadmapQueueEntry] = []
        self._next_entry_id = 1
        self.settings = RoadmapSettingsEntry(
            repo_id=repo_id,
            max_parallel=max_parallel,
            default_view="list",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.enqueue_calls: list[RoadmapQueueEntry] = []
        self.update_calls: list[dict] = []
        self.clear_calls = 0
        self.audits: list[AuditEntry] = []

    # -- reads ---------------------------------------------------------------
    def get_roadmap_settings(self, repo_id: str) -> RoadmapSettingsEntry | None:
        if repo_id != self.repo_id:
            return None
        return self.settings

    def list_roadmap_queue(
        self, *, repo_id: str | None = None, status: str | None = None
    ) -> list[RoadmapQueueEntry]:
        entries = list(self._entries)
        if repo_id is not None:
            entries = [entry for entry in entries if entry.repo_id == repo_id]
        if status is not None:
            entries = [entry for entry in entries if entry.status == status]
        return entries

    # -- writes --------------------------------------------------------------
    def save_roadmap_settings(self, settings: RoadmapSettingsEntry) -> None:
        self.settings = settings

    def enqueue_roadmap(self, entry: RoadmapQueueEntry) -> int:
        self.enqueue_calls.append(entry)
        stored_entry = replace(entry, entry_id=self._next_entry_id)
        self._next_entry_id += 1
        self._entries.append(stored_entry)
        return int(stored_entry.entry_id)

    def update_roadmap_queue_status(
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

    def clear_roadmap_queue(self, *, repo_id: str | None = None) -> None:
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
            RoadmapQueueEntry(
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

    def entry_for(self, prd_path: str) -> RoadmapQueueEntry:
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
