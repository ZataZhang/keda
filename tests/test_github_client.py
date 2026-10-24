"""Tests for the GitHub CLI infrastructure adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.shared.models.agent_runner import CommandResult, LabelConfig
from backend.infrastructure.github_client import (
    GitHubCliClient,
    sanitize_github_body,
)
from tests.conftest import FakeProcessRunner as _FakeProcessRunner


class FakeProcessRunner(_FakeProcessRunner):
    """模拟固定 Git remote 和空 Issue 响应的 GitHub CLI 假执行器。"""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None) -> None:
        super().__init__(responses)

    def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
        if tuple(command) == ("git", "remote", "-v"):
            return CommandResult(
                command=tuple(command),
                return_code=0,
                stdout=(
                    "origin https://github.com/owner/repo.git (fetch)\n"
                    "origin https://github.com/owner/repo.git (push)\n"
                ),
                stderr="",
            )
        result = super().run(command, cwd=cwd, check=check, **kwargs)
        command_list = list(command)
        if (
            not result.stdout
            and command_list[:4] == ["gh", "api", "-X", "GET"]
            and len(command_list) > 4
        ):
            endpoint_parts = command_list[4].split("/")
            issue_number = endpoint_parts[-1]
            if "issues" in endpoint_parts and issue_number.isdigit():
                return CommandResult(
                    command=tuple(command),
                    return_code=0,
                    stdout=json.dumps(
                        {
                            "number": int(issue_number),
                            "title": "",
                            "html_url": f"https://github.com/owner/repo/issues/{issue_number}",
                            "labels": [],
                            "body": "",
                            "state": "open",
                        }
                    ),
                    stderr="",
                )
        return result


class _BodyCapturingRunner(FakeProcessRunner):
    """记录文件字段中正文内容的进程执行器。"""

    def __init__(self) -> None:
        super().__init__()
        self.body_files: list[str] = []

    def run(self, command, *, cwd, **kwargs):  # type: ignore[override]
        command_list = list(command)
        if "--body-file" in command_list:
            body_path = command_list[command_list.index("--body-file") + 1]
            self.body_files.append(Path(body_path).read_text(encoding="utf-8"))
        else:
            body_field = next(
                (token for token in command_list if token.startswith("body=@")),
                None,
            )
            if body_field is not None:
                self.body_files.append(
                    Path(body_field.removeprefix("body=@")).read_text(encoding="utf-8")
                )
        return super().run(command, cwd=cwd, **kwargs)


def test_sanitize_github_body_strips_request_breaking_control_characters() -> None:
    """Control characters that trigger GitHub's 400 must be removed."""
    raw_body = "ok\x00 line\x1b[31m colored\x07 bell\x7f del\ttab\nnewline\r"

    sanitized = sanitize_github_body(raw_body)

    assert "\x00" not in sanitized
    assert "\x1b" not in sanitized
    assert "\x07" not in sanitized
    assert "\x7f" not in sanitized
    # Tabs, newlines and carriage returns are valid Markdown and preserved.
    assert "\ttab" in sanitized
    assert "\nnewline" in sanitized
    assert sanitized.endswith("\r")


def test_sanitize_github_body_truncates_oversized_body_keeping_head_and_tail() -> None:
    """Oversized bodies are middle-truncated below GitHub's size limit."""
    raw_body = "H" * 50 + "M" * 100_000 + "T" * 50

    sanitized = sanitize_github_body(raw_body, max_length=2000)

    assert len(sanitized) <= 2000
    assert sanitized.startswith("H" * 50)
    assert sanitized.endswith("T" * 50)
    assert "truncated to fit GitHub's size limit" in sanitized


def test_comment_issue_sanitizes_body_before_posting(tmp_path: Path) -> None:
    """A failure comment with raw control characters must be scrubbed.

    Regression for Issue #84: agent CLI output embedded raw control bytes,
    so the GitHub comment endpoint got a 400 and the failure reason was never posted.
    """
    capturing_runner = _BodyCapturingRunner()
    github_client = GitHubCliClient(tmp_path, capturing_runner)

    github_client.comment_issue(84, "## Agent Runner Failed\x00\x1b bad bytes")

    assert capturing_runner.body_files == ["## Agent Runner Failed bad bytes"]


def test_edit_issue_comment_passes_body_as_gh_api_file_field(tmp_path: Path) -> None:
    """Editing a comment must read the body via ``gh api -F body=@<file>``.

    Regression for Issue #89: the field was built as ``body@<file>`` (missing the
    ``=``), so ``gh api`` rejected it with ``invalid key`` on every edit. Only the
    first write to a comment survived (that path creates via the Issue comments REST endpoint),
    so the in-place-edited attempt-history comment silently froze at its first row.
    """
    fake_runner = FakeProcessRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.edit_issue_comment(5029728609, "## Attempt History\n| body |")

    api_calls = [call for call in fake_runner.calls if "api" in call and "-F" in call]
    assert len(api_calls) == 1, fake_runner.calls
    argv = api_calls[0]
    assert "PATCH" in argv
    assert "repos/owner/repo/issues/comments/5029728609" in argv
    field_value = argv[argv.index("-F") + 1]
    # ``gh api`` reads a field value from a file only with the ``key=@file`` form;
    # the buggy ``key@file`` form is parsed as an invalid key and rejected.
    assert field_value.startswith("body=@"), field_value


def test_list_issue_comments_requests_comments_field(tmp_path: Path) -> None:
    """Issue 评论读取应解析 REST 分页响应。"""
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/23/comments",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout=json.dumps(
                    [
                        [
                            {"id": 1, "body": "first"},
                            {"id": 2, "body": ""},
                            {"id": 3, "body": "second"},
                        ]
                    ]
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    comments = github_client.list_issue_comments(23)

    assert comments == ["first", "second"]
    assert fake_runner.calls == [
        list(command),
    ]


def test_strict_issue_comment_query_raises_on_gh_failure(tmp_path: Path) -> None:
    """快照扫描严格读取评论时，REST 请求失败不能伪装成空列表。"""
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/23/comments",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=1,
                stdout="",
                stderr="GitHub unavailable",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    assert github_client.list_issue_comments(23) == []
    with pytest.raises(RuntimeError, match="Cannot read Issue #23 comments"):
        github_client.list_issue_comments(23, require_success=True)


@pytest.mark.parametrize(
    ("method_name", "state"),
    [("find_open_pr_by_head", "open"), ("find_merged_pr_by_head", "merged")],
)
def test_strict_pr_head_queries_raise_on_gh_failure(
    tmp_path: Path, method_name: str, state: str
) -> None:
    """严格 REST 查询 PR 状态时，API 失败不能伪装成 PR 不存在。"""
    query_state = "open" if state == "open" else "closed"
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        f"repos/owner/repo/pulls?state={query_state}&head=owner%3Aissue-23",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=1,
                stdout="",
                stderr="GitHub unavailable",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)
    github_client._owner_repo_cache = "owner/repo"

    with pytest.raises(RuntimeError):
        getattr(github_client, method_name)("issue-23", require_success=True)


@pytest.mark.parametrize(
    ("method_name", "state"),
    [("find_open_pr_by_head", "open"), ("find_merged_pr_by_head", "merged")],
)
def test_strict_pr_head_queries_reject_empty_success_response(
    tmp_path: Path, method_name: str, state: str
) -> None:
    """严格 PR 查询的空 stdout 不能被当成成功的无结果响应。"""
    query_state = "open" if state == "open" else "closed"
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        f"repos/owner/repo/pulls?state={query_state}&head=owner%3Aissue-23",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout="",
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)
    github_client._owner_repo_cache = "owner/repo"

    with pytest.raises(RuntimeError, match="empty response"):
        getattr(github_client, method_name)("issue-23", require_success=True)


def test_list_pr_comments_requests_comments_field(tmp_path: Path) -> None:
    """PR conversation comment loading should use the REST comments endpoint."""
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/26/comments",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout=json.dumps([[{"body": "review"}, {"body": None}, {"body": "done"}]]),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)
    github_client._owner_repo_cache = "owner/repo"

    comments = github_client.list_pr_comments(26)

    assert comments == ["review", "done"]
    assert fake_runner.calls == [list(command)]


def test_get_pull_request_context_uses_supported_rollup_field(
    tmp_path: Path,
) -> None:
    """PR context loading should combine REST PR data and check status endpoints."""
    pr_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/pulls?state=open&head=owner%3Aissue-28",
        "-F",
        "per_page=100",
    )
    check_runs_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/check-runs",
        "-F",
        "per_page=100",
    )
    statuses_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/status",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            pr_command: CommandResult(
                command=pr_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "html_url": "https://github.com/example/repo/pull/28",
                            "number": 28,
                            "body": "",
                            "head": {
                                "ref": "issue-28",
                                "sha": "a" * 40,
                            },
                            "base": {"sha": "b" * 40},
                            "mergeable": False,
                            "draft": True,
                        }
                    ]
                ),
                stderr="",
            ),
            check_runs_command: CommandResult(
                command=check_runs_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "total_count": 1,
                            "check_runs": [
                                {
                                    "name": "lint",
                                    "status": "completed",
                                    "conclusion": "failure",
                                    "details_url": "https://checks.example/lint",
                                }
                            ],
                        }
                    ]
                ),
                stderr="",
            ),
            statuses_command: CommandResult(
                command=statuses_command,
                return_code=0,
                stdout=json.dumps(
                    {
                        "state": "success",
                        "total_count": 1,
                        "statuses": [{"context": "unit", "state": "success"}],
                    }
                ),
                stderr="",
            ),
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)
    github_client._owner_repo_cache = "owner/repo"

    pr_context = github_client.get_pull_request_context("issue-28")

    assert pr_context is not None
    assert pr_context.pr_url == "https://github.com/example/repo/pull/28"
    assert pr_context.is_draft is True
    assert pr_context.mergeable is False
    assert pr_context.checks_state == "FAILURE"
    assert pr_context.checks_summary == (
        "lint (status=COMPLETED, conclusion=FAILURE) https://checks.example/lint",
    )
    assert fake_runner.calls == [
        list(pr_command),
        list(check_runs_command),
        list(statuses_command),
    ]


def test_get_pull_request_context_empty_rollup_has_no_checks_state(
    tmp_path: Path,
) -> None:
    """Empty REST check endpoints should stay compatible with repositories without CI."""
    pr_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/pulls?state=open&head=owner%3Aissue-1",
        "-F",
        "per_page=100",
    )
    check_runs_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/commits/cccccccccccccccccccccccccccccccccccccccc/check-runs",
        "-F",
        "per_page=100",
    )
    statuses_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/commits/cccccccccccccccccccccccccccccccccccccccc/status",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            pr_command: CommandResult(
                command=pr_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "html_url": "https://github.com/example/repo/pull/1",
                            "number": 1,
                            "body": "",
                            "head": {
                                "ref": "issue-1",
                                "sha": "c" * 40,
                            },
                            "base": {"sha": "d" * 40},
                            "mergeable": True,
                            "draft": False,
                        }
                    ]
                ),
                stderr="",
            ),
            check_runs_command: CommandResult(
                command=check_runs_command,
                return_code=0,
                stdout=json.dumps([{"total_count": 0, "check_runs": []}]),
                stderr="",
            ),
            statuses_command: CommandResult(
                command=statuses_command,
                return_code=0,
                stdout=json.dumps({"state": "pending", "total_count": 0, "statuses": []}),
                stderr="",
            ),
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)
    github_client._owner_repo_cache = "owner/repo"

    pr_context = github_client.get_pull_request_context("issue-1")

    assert pr_context is not None
    assert pr_context.mergeable is True
    assert pr_context.is_draft is False
    assert pr_context.checks_state is None
    assert pr_context.checks_summary == ()
    assert fake_runner.calls == [
        list(pr_command),
        list(check_runs_command),
        list(statuses_command),
    ]


def test_mark_pull_request_ready_uses_gh_pr_ready(tmp_path: Path) -> None:
    """GitHub adapter 使用 gh 的 Draft-to-Ready 命令。"""
    command = ("gh", "pr", "ready", "281")
    fake_runner = FakeProcessRunner(
        responses={command: CommandResult(command=command, return_code=0, stdout="", stderr="")}
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.mark_pull_request_ready(281)

    assert fake_runner.calls == [list(command)]


def test_mark_pull_request_ready_raises_when_gh_rejects_transition(tmp_path: Path) -> None:
    """GitHub 拒绝 Ready 转换时 adapter 必须报告失败。"""
    command = ("gh", "pr", "ready", "281")
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=1,
                stdout="",
                stderr="GitHub unavailable",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    with pytest.raises(RuntimeError, match="gh pr ready failed for PR #281"):
        github_client.mark_pull_request_ready(281)


def test_edit_issue_labels_only_removes_attached_labels(tmp_path: Path) -> None:
    """标签更新不应请求 REST 删除 Issue 上不存在的标签。"""
    get_issue_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues/27",
    )
    add_label_command = (
        "gh",
        "api",
        "-X",
        "POST",
        "repos/owner/repo/issues/27/labels",
        "-f",
        "labels[]=agent/failed",
    )
    remove_label_command = (
        "gh",
        "api",
        "-X",
        "DELETE",
        "repos/owner/repo/issues/27/labels/agent%2Frunning",
    )
    fake_runner = FakeProcessRunner(
        responses={
            get_issue_command: CommandResult(
                command=get_issue_command,
                return_code=0,
                stdout=json.dumps(
                    {
                        "number": 27,
                        "state": "open",
                        "labels": [
                            {"name": "agent/running"},
                            {"name": "source/prd"},
                        ],
                    }
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.edit_issue_labels(
        27,
        add=["agent/failed"],
        remove=["agent/ready", "agent/running", "agent/supervising"],
    )

    assert fake_runner.calls == [
        list(get_issue_command),
        list(add_label_command),
        list(remove_label_command),
    ]


def test_edit_issue_labels_skips_noop_update(tmp_path: Path) -> None:
    """标签更新无变化时不应调用 REST 修改端点。"""
    get_issue_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues/27",
    )
    fake_runner = FakeProcessRunner(
        responses={
            get_issue_command: CommandResult(
                command=get_issue_command,
                return_code=0,
                stdout=json.dumps(
                    {"number": 27, "state": "open", "labels": [{"name": "agent/failed"}]}
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.edit_issue_labels(
        27,
        add=["agent/failed"],
        remove=["agent/ready", "agent/running"],
    )

    assert fake_runner.calls == [
        list(get_issue_command),
    ]


def test_list_review_candidate_issues_uses_or_label_semantics(
    tmp_path: Path,
) -> None:
    """Review candidate query must combine results across labels (OR semantics)."""
    supervising_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues",
        "-f",
        "state=open",
        "-F",
        "per_page=20",
        "-f",
        "labels=agent/supervising",
    )
    review_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues",
        "-f",
        "state=open",
        "-F",
        "per_page=20",
        "-f",
        "labels=agent/automated-review-passed",
    )
    fake_runner = FakeProcessRunner(
        responses={
            supervising_command: CommandResult(
                command=supervising_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 90,
                            "title": "supervising only",
                            "url": "https://example/90",
                            "labels": [{"name": "agent/supervising"}],
                            "body": "",
                        },
                        {
                            "number": 92,
                            "title": "both labels",
                            "url": "https://example/92",
                            "labels": [
                                {"name": "agent/supervising"},
                                {"name": "agent/automated-review-passed"},
                            ],
                            "body": "",
                        },
                    ]
                ),
                stderr="",
            ),
            review_command: CommandResult(
                command=review_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 91,
                            "title": "review only",
                            "url": "https://example/91",
                            "labels": [{"name": "agent/automated-review-passed"}],
                            "body": "",
                        },
                        {
                            "number": 92,
                            "title": "both labels",
                            "url": "https://example/92",
                            "labels": [
                                {"name": "agent/supervising"},
                                {"name": "agent/automated-review-passed"},
                            ],
                            "body": "",
                        },
                    ]
                ),
                stderr="",
            ),
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    candidates = github_client.list_review_candidate_issues(
        ["agent/supervising", "agent/automated-review-passed"], 20
    )

    candidate_numbers = {candidate.number for candidate in candidates}
    assert candidate_numbers == {90, 91, 92}
    # 92 must appear exactly once even though it matches both labels.
    assert len(candidates) == 3
    assert fake_runner.calls == [
        list(supervising_command),
        list(review_command),
    ]


def test_sync_labels_creates_rework_prd_label(tmp_path: Path) -> None:
    """sync_labels must register agent/rework-prd, the Issue->PRD trigger label.

    Regression: agent/rework-prd drives create_prd_from_issue, but it was
    missing from sync_labels' specs, so ``iar init`` / ``iar labels sync`` never
    created it and the Issue-driven PRD workflow could not be triggered.
    """
    fake_runner = FakeProcessRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.sync_labels(LabelConfig())

    created_labels = [
        call[call.index("create") + 1]
        for call in fake_runner.calls
        if call[:3] == ["gh", "label", "create"]
    ]
    assert "agent/rework-prd" in created_labels


def test_list_rework_prd_issues_filters_label(tmp_path: Path) -> None:
    """list_rework_prd_issues 应通过 REST 按给定标签和数量上限查询。"""
    command = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues",
        "-f",
        "state=open",
        "-F",
        "per_page=5",
        "-f",
        "labels=agent/rework-prd",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 7,
                            "title": "Rework PRD",
                            "html_url": "https://example/7",
                            "labels": [{"name": "agent/rework-prd"}],
                            "body": "",
                            "state": "open",
                        }
                    ]
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    issues = github_client.list_rework_prd_issues("agent/rework-prd", 5)

    assert len(issues) == 1
    assert issues[0].number == 7
    assert fake_runner.calls == [
        list(command),
    ]


def test_edit_issue_body_writes_via_file(tmp_path: Path) -> None:
    """edit_issue_body 应通过 REST 文件字段提交正文。"""

    class CapturingRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self.captured_body: str | None = None

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            capture_output=True,
            input_text=None,
            output_protocol=None,
        ):
            command_list = list(command)
            body_field = next(
                (token for token in command_list if token.startswith("body=@")),
                None,
            )
            if body_field is not None:
                body_file_path = Path(body_field.removeprefix("body=@"))
                self.captured_body = body_file_path.read_text(encoding="utf-8")
            return super().run(
                command_list,
                cwd=cwd,
                check=check,
                timeout=timeout,
                capture_output=capture_output,
                input_text=input_text,
                output_protocol=output_protocol,
            )

    runner = CapturingRunner()
    github_client = GitHubCliClient(tmp_path, runner)

    github_client.edit_issue_body(9, "New body text.")

    patch_call = next(
        call for call in runner.calls if call[:2] == ["gh", "api"] and "PATCH" in call
    )
    assert patch_call[:5] == ["gh", "api", "-X", "PATCH", "repos/owner/repo/issues/9"]
    assert any(token.startswith("body=@") for token in patch_call)
    assert runner.captured_body == "New body text."


class _FlakyProcessRunner(FakeProcessRunner):
    """Process runner that fails a fixed number of times before succeeding."""

    def __init__(self, fail_count: int, error_text: str) -> None:
        super().__init__()
        self.fail_count = fail_count
        self.error_text = error_text
        self.attempts = 0

    def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
        self.attempts += 1
        if self.attempts <= self.fail_count:
            exc = subprocess.CalledProcessError(
                returncode=1,
                cmd=list(command),
                output="",
                stderr=self.error_text,
            )
            if check:
                raise exc
            return CommandResult(
                command=tuple(command),
                return_code=1,
                stdout="",
                stderr=self.error_text,
            )
        return CommandResult(
            command=tuple(command),
            return_code=0,
            stdout="https://github.com/org/repo/issues/42",
            stderr="",
        )


def test_create_issue_retries_on_transient_tls_timeout(tmp_path: Path) -> None:
    """create_issue should retry when gh fails with a TLS handshake timeout."""
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text='Post "https://api.github.com/graphql": net/http: TLS handshake timeout',
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep") as mock_sleep:
        issue_url = github_client.create_issue(title="title", body="body", labels=["type/feature"])

    assert issue_url == "https://github.com/org/repo/issues/42"
    assert runner.attempts == 2
    mock_sleep.assert_called_once()


def test_create_issue_does_not_retry_permanent_errors(tmp_path: Path) -> None:
    """create_issue should fail immediately on non-transient errors."""
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text="HTTP 401: Bad credentials",
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep") as mock_sleep:
        with pytest.raises(subprocess.CalledProcessError):
            github_client.create_issue(title="title", body="body", labels=[])

    assert runner.attempts == 1
    mock_sleep.assert_not_called()


def test_create_issue_gives_up_after_max_retries(tmp_path: Path) -> None:
    """create_issue should stop retrying after the maximum number of attempts."""
    runner = _FlakyProcessRunner(
        fail_count=10,
        error_text="net/http: TLS handshake timeout",
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep"):
        with pytest.raises(subprocess.CalledProcessError):
            github_client.create_issue(title="title", body="body", labels=[])

    assert runner.attempts == 3


def test_create_issue_retries_on_client_closed_request(tmp_path: Path) -> None:
    """create_issue should retry on HTTP 499 (client/edge closed the connection)."""
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text='non-200 OK status code: 499  body: ""',
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep") as mock_sleep:
        issue_url = github_client.create_issue(title="title", body="body", labels=["type/feature"])

    assert issue_url == "https://github.com/org/repo/issues/42"
    assert runner.attempts == 2
    mock_sleep.assert_called_once()


def test_create_issue_retries_on_edge_whoa_there_400(tmp_path: Path) -> None:
    """create_issue should retry GitHub's generic edge 'Whoa there!' 400 page.

    This is the exact failure shape seen in production: a sanitized comment
    body still tripped GitHub's edge-level abuse/validation page, which
    permanently stranded the Issue in ``agent/failed`` because nothing
    retried it. Whether the underlying cause is transient (worth retrying)
    or a deterministic content issue, retrying is safe: it either recovers
    or falls through to the exact same failure after a couple of seconds.
    """
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text=(
            'non-200 OK status code: 400 Bad Request body: "\\r\\n<html>\\r\\n'
            "  <head>\\r\\n    <title>Bad request &middot; GitHub</title>\\r\\n"
            '  </head>\\r\\n  <body>\\r\\n    <div class=\\"c\\">\\r\\n'
            "      <h1>Whoa there!</h1>\\r\\n      <p>You have sent an invalid "
            'request.</p>\\r\\n    </div>\\r\\n  </body>\\r\\n</html>\\r\\n"'
        ),
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep") as mock_sleep:
        issue_url = github_client.create_issue(title="title", body="body", labels=["type/feature"])

    assert issue_url == "https://github.com/org/repo/issues/42"
    assert runner.attempts == 2
    mock_sleep.assert_called_once()


def test_create_issue_does_not_retry_structured_400(tmp_path: Path) -> None:
    """A normal structured 400 (real validation error) must not be retried.

    Negative control for the "Whoa there!" pattern above: a clean JSON-style
    API rejection is a deterministic client-side bug, not a transient edge
    error, and must still fail fast rather than being masked by retries.
    """
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text='non-200 OK status code: 400 Bad Request body: "{\\"message\\":\\"Validation Failed\\"}"',
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep") as mock_sleep:
        with pytest.raises(subprocess.CalledProcessError):
            github_client.create_issue(title="title", body="body", labels=[])

    assert runner.attempts == 1
    mock_sleep.assert_not_called()


def test_create_issue_creates_missing_label_and_retries(tmp_path: Path) -> None:
    """``gh issue create`` failure on a missing label should auto-create + retry.

    Real-world scenario: a freshly registered repository whose GitHub label
    set does not yet include the workflow labels (``type/feature`` etc.) makes
    Issue creation crash with gh's raw ``could not add label: ... not found``
    error. Instead of failing, iar should ``gh label create --force`` the
    missing labels and retry the create, mirroring ``edit_issue_labels``.
    """

    class _MissingCreateLabelRunner(FakeProcessRunner):
        """First ``gh issue create`` fails with a not-found stderr; the retry succeeds."""

        def __init__(self) -> None:
            super().__init__()
            self.create_attempts = 0

        def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
            command_list = list(command)
            if command_list[:3] == ["gh", "issue", "create"]:
                self.create_attempts += 1
                if self.create_attempts == 1:
                    exc = subprocess.CalledProcessError(
                        returncode=1,
                        cmd=command_list,
                        output="",
                        stderr="could not add label: 'type/feature' not found",
                    )
                    if check:
                        raise exc
                result = super().run(command, cwd=cwd, check=check, **kwargs)
                return CommandResult(
                    command=result.command,
                    return_code=0,
                    stdout="https://github.com/org/repo/issues/42",
                    stderr="",
                )
            return super().run(command, cwd=cwd, check=check, **kwargs)

    fake_runner = _MissingCreateLabelRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    issue_url = github_client.create_issue(
        title="title",
        body="body",
        labels=["type/feature", "status/backlog", "source/prd"],
    )

    assert issue_url == "https://github.com/org/repo/issues/42"
    assert fake_runner.create_attempts == 2
    created_labels = [
        call[call.index("create") + 1]
        for call in fake_runner.calls
        if call[:3] == ["gh", "label", "create"]
    ]
    assert created_labels == ["type/feature", "status/backlog", "source/prd"]


def test_create_issue_does_not_create_label_on_unrelated_error(tmp_path: Path) -> None:
    """Non-missing-label failures must propagate without touching label state.

    A 401 auth failure must not be masked by the missing-label fallback:
    creating labels and retrying would hide the real cause.
    """
    runner = _FlakyProcessRunner(
        fail_count=1,
        error_text="HTTP 401: Bad credentials",
    )
    github_client = GitHubCliClient(tmp_path, runner)

    with patch("backend.infrastructure.github_client.time.sleep"):
        with pytest.raises(subprocess.CalledProcessError):
            github_client.create_issue(title="title", body="body", labels=["type/feature"])

    assert not any(call[:3] == ["gh", "label", "create"] for call in runner.calls)


def test_list_pull_requests_for_issue_normalises_states(tmp_path: Path) -> None:
    """list_pull_requests_for_issue maps gh states to the 4-bucket view model."""
    command = (
        "gh",
        "pr",
        "list",
        "--repo",
        "owner/repo",
        "--search",
        "closes:#7 OR fixes:#7 OR resolves:#7 OR refs:#7",
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,title,state,url,isDraft,mergedAt",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 42,
                            "title": "merged PR",
                            "state": "MERGED",
                            "url": "https://example/pr/42",
                            "isDraft": False,
                            "mergedAt": "2026-05-01T00:00:00Z",
                        },
                        {
                            "number": 43,
                            "title": "draft PR",
                            "state": "OPEN",
                            "url": "https://example/pr/43",
                            "isDraft": True,
                            "mergedAt": "",
                        },
                        {
                            "number": 44,
                            "title": "closed PR",
                            "state": "CLOSED",
                            "url": "https://example/pr/44",
                            "isDraft": False,
                            "mergedAt": "",
                        },
                    ]
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    pulls = github_client.list_pull_requests_for_issue("owner/repo", 7)

    states = [pull.state for pull in pulls]
    # Open/draft sort before closed, merged last.
    assert states == ["draft", "closed", "merged"]
    assert pulls[0].is_draft is True
    assert pulls[-1].merged is True
    assert fake_runner.calls == [list(command)]


def test_list_pull_requests_for_issue_empty_when_no_prs(tmp_path: Path) -> None:
    """An empty gh response is reported as an empty list."""
    command = (
        "gh",
        "pr",
        "list",
        "--repo",
        "owner/repo",
        "--search",
        "closes:#7 OR fixes:#7 OR resolves:#7 OR refs:#7",
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,title,state,url,isDraft,mergedAt",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command: CommandResult(
                command=command,
                return_code=0,
                stdout="[]",
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    pulls = github_client.list_pull_requests_for_issue("owner/repo", 7)

    assert pulls == []
    assert fake_runner.calls == [list(command)]


def test_list_issues_by_label_omits_label_flag_when_none(tmp_path: Path) -> None:
    """label 为 None 时 REST 查询应省略 labels 参数。"""
    command_without_label = (
        "gh",
        "api",
        "-X",
        "GET",
        "repos/owner/repo/issues",
        "-f",
        "state=all",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            command_without_label: CommandResult(
                command=command_without_label,
                return_code=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 1,
                            "title": "all-issues",
                            "html_url": "https://example/1",
                            "labels": [],
                            "body": "",
                            "state": "OPEN",
                        }
                    ]
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    issues = github_client.list_issues_by_label(label=None, limit=100, state="all")

    assert len(issues) == 1
    assert issues[0].number == 1
    assert fake_runner.calls == [
        list(command_without_label),
    ]


def test_list_repository_items_reads_all_pages_and_includes_pull_requests(
    tmp_path: Path,
) -> None:
    """显式迁移使用完整 REST 分页，并保留 Issue 和 PR 正文。"""
    repository_items_command = (
        "gh",
        "api",
        "-X",
        "GET",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues",
        "-f",
        "state=all",
        "-F",
        "per_page=100",
    )
    fake_runner = FakeProcessRunner(
        responses={
            repository_items_command: CommandResult(
                command=repository_items_command,
                return_code=0,
                stdout=json.dumps(
                    [
                        [
                            {
                                "number": 12,
                                "title": "issue",
                                "html_url": "https://github.com/owner/repo/issues/12",
                                "labels": [],
                                "body": "issue body",
                                "state": "open",
                            }
                        ],
                        [
                            {
                                "number": 13,
                                "title": "pull request",
                                "html_url": "https://github.com/owner/repo/pull/13",
                                "labels": [],
                                "body": "pull request body",
                                "state": "closed",
                                "pull_request": {"url": "https://api.github.com/pulls/13"},
                            }
                        ],
                    ]
                ),
                stderr="",
            )
        }
    )
    github_client = GitHubCliClient(tmp_path, fake_runner)

    repository_items = github_client.list_repository_items(state="all")

    assert [repository_item.number for repository_item in repository_items] == [12, 13]
    assert [repository_item.body for repository_item in repository_items] == [
        "issue body",
        "pull request body",
    ]
    assert fake_runner.calls == [list(repository_items_command)]


def test_sync_labels_creates_verifier_passed_label(tmp_path: Path) -> None:
    """sync_labels must register validation/verifier-passed so post-PR verdicts can tag.

    Regression: when ``validation/verifier-passed`` was missing from the
    spec table, fresh repositories ran ``gh issue edit --add-label`` and
    GitHub rejected the call with ``'validation/verifier-passed' not
    found``, which surfaced as ``agent/failed`` during publication.
    """
    fake_runner = FakeProcessRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.sync_labels(LabelConfig())

    created_labels = [
        call[call.index("create") + 1]
        for call in fake_runner.calls
        if call[:3] == ["gh", "label", "create"]
    ]
    assert "validation/verifier-passed" in created_labels


def test_edit_issue_labels_creates_missing_label_and_retries(tmp_path: Path) -> None:
    """REST 标签更新遇到缺失标签时应先创建，再重试。

    新仓库未运行 ``labels sync``，缺少 ``validation/verifier-passed`` 时，验证通过后
    runner 仍应创建该标签并重试更新，而不是直接失败。
    """

    class _MissingLabelRunner(FakeProcessRunner):
        """首个 REST 标签添加请求返回缺失标签错误，其他请求成功。"""

        def __init__(self) -> None:
            super().__init__()
            self.edit_attempts = 0

        def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
            command_list = list(command)
            if (
                command_list[:4] == ["gh", "api", "-X", "POST"]
                and command_list[4] == "repos/owner/repo/issues/24/labels"
            ):
                self.edit_attempts += 1
                if self.edit_attempts == 1:
                    exc = subprocess.CalledProcessError(
                        returncode=1,
                        cmd=command_list,
                        output="",
                        stderr=(
                            "failed to update "
                            "https://github.com/GetRichTogether/TransMaster/pull/24: "
                            "'validation/verifier-passed' not found"
                        ),
                    )
                    if check:
                        raise exc
            return super().run(command, cwd=cwd, check=check, **kwargs)

    fake_runner = _MissingLabelRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    github_client.edit_issue_labels(24, add=["validation/verifier-passed"])

    # Order: Issue read → label creation → REST label-add retry.
    # The first REST label-add raises before being recorded in
    # ``calls`` (the FakeProcessRunner subclass raises before delegating to
    # ``super().run``, mirroring how a real subprocess ``CalledProcessError``
    # surfaces).
    assert fake_runner.calls == [
        [
            "gh",
            "api",
            "-X",
            "GET",
            "repos/owner/repo/issues/24",
        ],
        [
            "gh",
            "label",
            "create",
            "validation/verifier-passed",
            "--force",
        ],
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/owner/repo/issues/24/labels",
            "-f",
            "labels[]=validation/verifier-passed",
        ],
    ]
    assert fake_runner.edit_attempts == 2


def test_edit_issue_labels_does_not_swallow_unrelated_errors(tmp_path: Path) -> None:
    """Failures that do not look like missing-label errors must propagate as-is.

    We simulate a TLS timeout — transient infrastructure failure. The
    fallback must not attempt ``gh label create`` because that would mask
    the real cause and could even succeed on a non-existent label just to
    hide the underlying network failure.
    """

    class _TransientErrorRunner(FakeProcessRunner):
        """REST 标签添加请求因 TLS 错误抛出 CalledProcessError。"""

        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
            command_list = list(command)
            if (
                command_list[:4] == ["gh", "api", "-X", "POST"]
                and command_list[4] == "repos/owner/repo/issues/24/labels"
            ):
                self.attempts += 1
                exc = subprocess.CalledProcessError(
                    returncode=1,
                    cmd=command_list,
                    output="",
                    stderr="Post https://api.github.com/repos/owner/repo/issues/24/labels: "
                    "net/http: TLS handshake timeout",
                )
                if check:
                    raise exc
            return super().run(command, cwd=cwd, check=check, **kwargs)

    fake_runner = _TransientErrorRunner()
    github_client = GitHubCliClient(tmp_path, fake_runner)

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        github_client.edit_issue_labels(24, add=["validation/verifier-passed"])
    assert "TLS handshake timeout" in (excinfo.value.stderr or "")

    ensure_calls = [call for call in fake_runner.calls if call[:3] == ["gh", "label", "create"]]
    assert ensure_calls == []
    # The failed label add is not recorded because the fake raises before delegating.
    add_calls = [call for call in fake_runner.calls if call[:4] == ["gh", "api", "-X", "POST"]]
    assert add_calls == []
