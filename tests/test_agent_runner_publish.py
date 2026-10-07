"""Tests for publishing agent work to the remote.

Covers ``get_head_sha``, ``validate_safe_changes`` and ``publish_changes``:
remote preflight, branch guards, PR reuse/creation, generated PR bodies and
push-vs-PR failure categorization."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    GitConfig,
    IssueSummary,
    WorktreeConfig,
)
from backend.core.use_cases.agent_runner_publish import create_draft_pr, push_changes
from backend.core.use_cases.run_agent_once import (
    PrdDeliveryError,
    get_head_sha,
    publish_changes,
    validate_safe_changes,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import (
    AWAITING_HUMAN_BANNER,
    build_acceptance_prd,
    git_remote_command,
    git_remote_result,
    make_prd_issue,
)


def test_get_head_sha() -> None:
    """get_head_sha should return the HEAD SHA."""
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "rev-parse", "HEAD"): CommandResult(
                command=("git", "rev-parse", "HEAD"),
                return_code=0,
                stdout="abc123def456\n",
                stderr="",
            ),
        }
    )
    sha = get_head_sha(Path("."), fake_runner)
    assert sha == "abc123def456"


def test_publish_changes_no_git_commit() -> None:
    """publish_changes should not call git add or git commit."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )
    branch, pr_url = publish_changes(issue, Path("."), AppConfig(), fake_client, fake_runner)
    assert branch == "issue-1"
    assert pr_url == "https://github.com/example/repo/pull/1"
    commands = [tuple(c) for c in fake_runner.calls]
    assert ("git", "add", "-A") not in commands
    assert ("git", "commit", "-m", "agent: complete issue #1") not in commands
    assert ("git", "push", "-u", "origin", "issue-1") in commands


# ── rv-4 / rv-7：发布前检查要求 PRD 已归档；两条 ``require_prd_archived=False`` 例外不变 ──

_PUSH_CALL = ("git", "push", "-u", "origin", "issue-123")


def _clean_branch_runner() -> FakeProcessRunner:
    """``push_changes`` 前置检查需要的三条 git 应答：分支 issue-123、工作区干净、远端 origin。"""
    return FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-123\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )


def _write_worktree_prd(worktree_path: Path, prd_relative_path: str, prd_text: str) -> Path:
    """把 PRD 写到 worktree 的给定位置（Issue 里记录的仍是 pending 路径）。"""
    prd_path = worktree_path / prd_relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(prd_text, encoding="utf-8")
    return prd_path


_PUBLISH_GATE_CASES = (
    # (PRD 所在位置, PRD 正文, require_prd_archived=True 时的拒绝信息；None 表示放行)
    pytest.param(
        "tasks/archive/example.md",
        build_acceptance_prd(AWAITING_HUMAN_BANNER, human_marks=(" ",)),
        None,
        id="archived-only-human-open",
    ),
    pytest.param(
        "tasks/pending/example.md",
        build_acceptance_prd(AWAITING_HUMAN_BANNER, human_marks=(" ",)),
        "has not been archived yet",
        id="still-pending",
    ),
    pytest.param(
        "tasks/archive/example.md",
        build_acceptance_prd(AWAITING_HUMAN_BANNER, execution_marks=("x", " "), human_marks=(" ",)),
        "- [ ] rv-2: executor-owned item 2",
        id="archived-executor-open",
    ),
)


@pytest.mark.parametrize(("prd_relative_path", "prd_text", "rejection_text"), _PUBLISH_GATE_CASES)
def test_push_changes_requires_an_archived_prd(
    tmp_path: Path, prd_relative_path: str, prd_text: str, rejection_text: str | None
) -> None:
    """rv-4：正常交付路径发布前 PRD 必须已归档；仍在 pending 的待人审 PRD 不再放行。

    已归档且只剩人审空框 → 推送；仍在 pending → 拒绝并说明尚未归档；已归档但执行侧
    未完成 → 拒绝并点名条目。被拒时一条 ``git push`` 都不能发出。
    """
    _write_worktree_prd(tmp_path, prd_relative_path, prd_text)
    fake_runner = _clean_branch_runner()

    if rejection_text is None:
        assert push_changes(make_prd_issue(), tmp_path, AppConfig(), fake_runner) == "issue-123"
        assert _PUSH_CALL in map(tuple, fake_runner.calls)
        return

    with pytest.raises(PrdDeliveryError) as exc_info:
        push_changes(make_prd_issue(), tmp_path, AppConfig(), fake_runner)

    assert rejection_text in str(exc_info.value)
    assert _PUSH_CALL not in map(tuple, fake_runner.calls)


@pytest.mark.parametrize(("prd_relative_path", "prd_text", "rejection_text"), _PUBLISH_GATE_CASES)
def test_failure_draft_publication_skips_the_archive_gate_and_never_archives(
    tmp_path: Path, prd_relative_path: str, prd_text: str, rejection_text: str | None
) -> None:
    """rv-4 例外 + rv-7 第三组：``require_prd_archived=False``（失败 Draft PR）三种都不拦。

    这条路径不调用交付检查：PRD 原地不动、不发 ``git add`` / ``git mv``，pending 的 PRD
    仍留在 pending——失败交付永不归档。
    """
    del rejection_text  # 例外路径对三种情形一视同仁。
    prd_path = _write_worktree_prd(tmp_path, prd_relative_path, prd_text)
    fake_runner = _clean_branch_runner()

    branch, _pr_url = publish_changes(
        make_prd_issue(),
        tmp_path,
        AppConfig(),
        FakeGitHubClient(),
        fake_runner,
        require_prd_archived=False,
    )

    assert branch == "issue-123"
    issued_commands = [tuple(call) for call in fake_runner.calls]
    assert _PUSH_CALL in issued_commands
    assert not [
        command for command in issued_commands if command[:2] in {("git", "mv"), ("git", "add")}
    ]
    assert prd_path.read_text(encoding="utf-8") == prd_text


def test_publish_changes_reuses_existing_open_pr() -> None:
    """publish_changes should reuse an existing open PR instead of recreating it."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_client._open_prs["issue-1"] = "https://github.com/example/repo/pull/52"
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )
    branch, pr_url = publish_changes(issue, Path("."), AppConfig(), fake_client, fake_runner)
    assert branch == "issue-1"
    assert pr_url == "https://github.com/example/repo/pull/52"
    commands = [tuple(c) for c in fake_runner.calls]
    assert ("git", "push", "-u", "origin", "issue-1") in commands
    method_names = [call["method"] for call in fake_client.calls]
    assert "find_open_pr_by_head" in method_names
    assert "create_draft_pr" not in method_names


def test_publish_changes_creates_pr_when_no_open_pr() -> None:
    """publish_changes should create a draft PR when the branch has no open PR."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )
    branch, pr_url = publish_changes(issue, Path("."), AppConfig(), fake_client, fake_runner)
    assert branch == "issue-1"
    assert pr_url == "https://github.com/example/repo/pull/1"
    method_names = [call["method"] for call in fake_client.calls]
    assert "find_open_pr_by_head" in method_names
    assert "create_draft_pr" in method_names


def test_publish_changes_rejects_missing_configured_remote() -> None:
    """publish_changes should fail instead of guessing another remote."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("zata", "upstream"),
        }
    )

    with pytest.raises(RuntimeError, match="Configured git remote 'origin'"):
        publish_changes(issue, Path("."), AppConfig(), fake_client, fake_runner)

    commands = [tuple(c) for c in fake_runner.calls]
    assert ("git", "push", "-u", "origin", "issue-1") not in commands
    assert ("git", "push", "-u", "zata", "issue-1") not in commands


def test_publish_changes_uses_configured_existing_remote() -> None:
    """publish_changes should push only to the configured remote."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin", "zata"),
        }
    )
    config = AppConfig(git=GitConfig(remote="zata"))

    branch, _ = publish_changes(issue, Path("."), config, fake_client, fake_runner)

    assert branch == "issue-1"
    commands = [tuple(c) for c in fake_runner.calls]
    assert ("git", "push", "-u", "zata", "issue-1") in commands
    assert ("git", "push", "-u", "origin", "issue-1") not in commands


def test_publish_changes_rejects_branch_change() -> None:
    """publish_changes should refuse to push if the worktree branch changed."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="main\n",
                stderr="",
            ),
        }
    )

    with pytest.raises(RuntimeError, match="unexpected branch: main"):
        publish_changes(
            issue,
            Path("."),
            AppConfig(),
            FakeGitHubClient(),
            fake_runner,
            expected_branch="issue-1",
        )

    commands = [tuple(c) for c in fake_runner.calls]
    assert ("git", "status", "--porcelain") not in commands
    assert ("git", "push", "-u", "origin", "main") not in commands


def test_publish_changes_rejects_detached_head(tmp_path: Path) -> None:
    """Publish must refuse to push when the worktree is detached."""
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="",
                stderr="",
            ),
        }
    )
    config = AppConfig(
        worktree=WorktreeConfig(
            create_command="echo",
            reuse_command="echo",
            path_command="echo",
        )
    )

    with pytest.raises(RuntimeError, match="detached HEAD"):
        publish_changes(
            issue=IssueSummary(number=1, title="T", url="U", body="B", labels=()),
            worktree_path=tmp_path / "wt",
            config=config,
            github_client=fake_client,
            process_runner=fake_runner,
        )


def test_publish_failure_category_push_vs_pr_create() -> None:
    """Pre-PR flow: push and PR create are gated separately and report accurately."""
    from backend.core.use_cases.agent_runner_failure import PublishFailureError
    from backend.core.use_cases.agent_runner_publication import (
        _create_draft_pr_with_recovery_context,
        _push_changes_with_recovery_context,
    )
    from backend.core.shared.models.agent_runner import PublishFailureCategory

    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )

    # Scenario 1: git push fails -> category=push
    fake_runner_push_fail = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            ("git", "remote"): CommandResult(
                command=("git", "remote"),
                return_code=0,
                stdout="origin\n",
                stderr="",
            ),
            ("git", "push", "-u", "origin", "issue-1"): CommandResult(
                command=("git", "push", "-u", "origin", "issue-1"),
                return_code=1,
                stdout="",
                stderr="push rejected",
            ),
        }
    )
    with pytest.raises(PublishFailureError) as exc_info:
        _push_changes_with_recovery_context(
            issue=issue,
            worktree_path=Path("."),
            config=AppConfig(),
            process_runner=fake_runner_push_fail,
            expected_branch="issue-1",
        )
    assert exc_info.value.failure_category == PublishFailureCategory.PUSH

    # Scenario 2: PR creation fails -> category=pr_create
    class _PRCreateFailClient(FakeGitHubClient):
        def create_draft_pr(self, **kwargs: object) -> str:
            raise RuntimeError("gh pr create failed")

    fake_runner_pr_fail = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
        }
    )
    with pytest.raises(PublishFailureError) as exc_info:
        _create_draft_pr_with_recovery_context(
            issue=issue,
            worktree_path=Path("."),
            config=AppConfig(),
            github_client=_PRCreateFailClient(),
            process_runner=fake_runner_pr_fail,
            expected_branch="issue-1",
            content_generator=None,
        )
    assert exc_info.value.failure_category == PublishFailureCategory.PR_CREATE


def test_publish_validation_evidence_after_pr_is_best_effort() -> None:
    """Unlike push/PR-create, an evidence-comment failure must not raise.

    Regression for a production incident: push, PR create, and the label
    transition to ``agent/supervising`` had already succeeded; a transient
    GitHub-edge error on the trailing evidence comment then rolled the
    Issue all the way back to ``agent/failed``, discarding that good state
    over what is purely an audit-trail comment.
    """
    from unittest.mock import patch

    from backend.core.use_cases.agent_runner_publication import (
        _publish_validation_evidence_after_pr,
    )

    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )

    with patch(
        "backend.core.use_cases.agent_runner_validation.publish_validation_evidence",
        side_effect=RuntimeError('non-200 OK status code: 499  body: ""'),
    ):
        _publish_validation_evidence_after_pr(
            issue=issue,
            worktree_path=Path("."),
            config=AppConfig(),
            github_client=FakeGitHubClient(),
            process_runner=FakeProcessRunner(),
            pr_url="https://github.com/example/repo/pull/1",
        )


def test_validate_safe_changes_rejects_forbidden_path(tmp_path: Path) -> None:
    """Runner should not publish configured secret-like paths."""
    repo = tmp_path / "repo"
    repo.mkdir()
    from tests.test_create_issue_from_prd import _init_repo

    _init_repo(repo)
    (repo / ".env").write_text("SECRET=value\n", encoding="utf-8")

    fake_runner = FakeProcessRunner(
        responses={
            ("git", "status", "--porcelain", "-z"): CommandResult(
                command=("git", "status", "--porcelain", "-z"),
                return_code=0,
                stdout=" M .env\0",
                stderr="",
            ),
        }
    )

    with pytest.raises(RuntimeError, match="Refusing to publish forbidden paths: .env"):
        validate_safe_changes(repo, AppConfig(), fake_runner)


def test_publish_changes_generated_pr_template_mode() -> None:
    """Template mode should render PR title and body when enabled."""
    issue = IssueSummary(
        number=42,
        title="Test Feature",
        url="https://github.com/example/repo/issues/42",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-42\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
            ("git", "log", "main..HEAD", "--pretty=format:%s"): CommandResult(
                command=("git", "log", "main..HEAD", "--pretty=format:%s"),
                return_code=0,
                stdout="feat: implement feature\n",
                stderr="",
            ),
            ("git", "diff", "--stat", "main...HEAD"): CommandResult(
                command=("git", "diff", "--stat", "main...HEAD"),
                return_code=0,
                stdout="1 file changed, 10 insertions\n",
                stderr="",
            ),
        }
    )
    gc_config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="[Agent] {issue_title}",
            body_template="Closes #{issue_number}\n\n{commit_log}\n\n{diff_stat}",
            include_commit_log=True,
            include_diff_stat=True,
        ),
    )
    from backend.core.shared.models.agent_runner import AppConfig, GitConfig

    app_config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        generated_content=gc_config,
    )

    branch, pr_url = publish_changes(issue, Path("."), app_config, fake_client, fake_runner)

    assert branch == "issue-42"
    pr_calls = [c for c in fake_client.calls if c["method"] == "create_draft_pr"]
    assert len(pr_calls) == 1
    assert pr_calls[0]["title"] == "[Agent] Test Feature"
    assert "Closes #42" in pr_calls[0]["body"]
    assert "feat: implement feature" in pr_calls[0]["body"]


def test_publish_changes_generated_pr_fallback_on_missing_closes() -> None:
    """Generated PR missing Closes anchor should fallback to deterministic template."""
    issue = IssueSummary(
        number=42,
        title="Test Feature",
        url="https://github.com/example/repo/issues/42",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-42\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )
    gc_config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="Title",
            body_template="No closes here.",
        ),
    )
    from backend.core.shared.models.agent_runner import AppConfig, GitConfig

    app_config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        generated_content=gc_config,
    )

    branch, pr_url = publish_changes(issue, Path("."), app_config, fake_client, fake_runner)

    pr_calls = [c for c in fake_client.calls if c["method"] == "create_draft_pr"]
    assert len(pr_calls) == 1
    assert pr_calls[0]["title"] == "[Agent] Test Feature"
    assert "Closes #42" in pr_calls[0]["body"]


def test_publish_changes_disabled_uses_fallback() -> None:
    """When generated content is disabled, deterministic PR body should be used."""
    issue = IssueSummary(
        number=1,
        title="Test",
        url="https://github.com/example/repo/issues/1",
        body="Test body",
        labels=(),
    )
    fake_client = FakeGitHubClient()
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="issue-1\n",
                stderr="",
            ),
            ("git", "status", "--porcelain"): CommandResult(
                command=("git", "status", "--porcelain"),
                return_code=0,
                stdout="",
                stderr="",
            ),
            git_remote_command(): git_remote_result("origin"),
        }
    )
    gc_config = GeneratedContentConfig(enabled=False)
    from backend.core.shared.models.agent_runner import AppConfig, GitConfig

    app_config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        generated_content=gc_config,
    )

    branch, pr_url = publish_changes(issue, Path("."), app_config, fake_client, fake_runner)

    pr_calls = [c for c in fake_client.calls if c["method"] == "create_draft_pr"]
    assert len(pr_calls) == 1
    assert pr_calls[0]["title"] == "[Agent] Test"
    assert "Closes #1" in pr_calls[0]["body"]


def test_draft_pr_uses_prd_content_generation_override() -> None:
    """PRD 头部声明的 ``content_generation`` agent 优先于配置派生的默认值。

    覆盖 PR #147 审核发现的"PRD 覆盖对 content_generation 静默无效"缺口：
    Issue 携带的 ``lifecycle_overrides`` 必须真正改变 PR 正文生成所用的 agent。
    """
    from backend.core.use_cases.agent_runner_publish import create_draft_pr
    from tests.conftest import FakeContentGenerator

    def _runner() -> FakeProcessRunner:
        return FakeProcessRunner(
            responses={
                ("git", "branch", "--show-current"): CommandResult(
                    command=("git", "branch", "--show-current"),
                    return_code=0,
                    stdout="issue-42\n",
                    stderr="",
                ),
                ("git", "log", "main..HEAD", "--pretty=format:%s"): CommandResult(
                    command=("git", "log", "main..HEAD", "--pretty=format:%s"),
                    return_code=0,
                    stdout="feat: implement feature\n",
                    stderr="",
                ),
                ("git", "diff", "--stat", "main...HEAD"): CommandResult(
                    command=("git", "diff", "--stat", "main...HEAD"),
                    return_code=0,
                    stdout="1 file changed, 10 insertions\n",
                    stderr="",
                ),
            }
        )

    def _issue(lifecycle_overrides: tuple[tuple[str, str], ...]) -> IssueSummary:
        return IssueSummary(
            number=42,
            title="Test Feature",
            url="https://github.com/example/repo/issues/42",
            body="Test body",
            labels=(),
            lifecycle_overrides=lifecycle_overrides,
        )

    config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        generated_content=GeneratedContentConfig(
            enabled=True,
            default_agent="codex",
            lifecycle_default_agent="claude",
            draft_pr=GeneratedContentTargetConfig(
                enabled=True, mode="agent", output="json", prompt="Generate PR"
            ),
        ),
    )
    generator_response = '{"title": "[Agent] Test Feature", "body": "Closes #42\\n\\nok"}'

    with_override = FakeContentGenerator(response=generator_response)
    create_draft_pr(
        _issue((("content_generation", "kimi"),)),
        Path("."),
        config,
        FakeGitHubClient(),
        _runner(),
        content_generator=with_override,
    )
    assert with_override.calls[0][0] == "kimi"

    without_override = FakeContentGenerator(response=generator_response)
    create_draft_pr(
        _issue(()),
        Path("."),
        config,
        FakeGitHubClient(),
        _runner(),
        content_generator=without_override,
    )
    assert without_override.calls[0][0] == "claude"


# ── rv-1 / FR-8：``require_prd_archived`` 是本 PRD 唯一放宽的安全检查 ────────────

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "backend"


def test_require_prd_archived_defaults_stay_true_on_publish_primitives() -> None:
    """发布原语的默认值必须是 ``True``，且 ``create_draft_pr`` 根本没有这个参数。

    放宽只能由耗尽路径**显式**传入；一旦默认值被翻成 ``False``，PRD 归档门禁就等于
    对所有人关闭了。签名形态也是契约的一部分：该参数属于 ``push_changes`` /
    ``publish_changes``，把它挂到 ``create_draft_pr`` 上是错的。
    """
    import inspect

    push_params = inspect.signature(push_changes).parameters
    publish_params = inspect.signature(publish_changes).parameters
    draft_params = inspect.signature(create_draft_pr).parameters

    assert push_params["require_prd_archived"].default is True
    assert publish_params["require_prd_archived"].default is True
    assert "require_prd_archived" not in draft_params


def test_require_prd_archived_false_appears_only_on_the_exhaustion_path() -> None:
    """静态审计 ``require_prd_archived=False`` 的落点：只允许出现在白名单里。

    这是 FR-8 的防泄漏网——正常成功路径、recover_publish、existing-commit
    publication、post-merge reconciliation 一旦也传 ``False``，就等于绕过了 PRD
    归档门禁，而本 PRD 只承诺放宽耗尽这一条路径。白名单显式钉住允许点：新增一处就要
    先改这条断言，也就必然要有人为它负责。
    """
    allowed_relaxed_call_sites = {
        # 本 PRD 新增：recovery 耗尽后发布同源的失败 Draft PR（唯一放宽项）。
        "backend/core/use_cases/agent_runner_issue_handlers.py",
        # 既有：PRD rework 发布的是尚在 tasks/pending/ 的提案 PRD，本来就未归档。
        "backend/core/use_cases/create_prd_from_issue.py",
    }

    relaxed_call_sites: set[str] = set()
    for python_path in _SRC_ROOT.rglob("*.py"):
        source_text = python_path.read_text(encoding="utf-8")
        if "require_prd_archived=False" in source_text:
            relaxed_call_sites.add(python_path.relative_to(_SRC_ROOT.parent).as_posix())

    assert relaxed_call_sites == allowed_relaxed_call_sites


def test_pr_handoff_ref_block_replaces_instead_of_stacking() -> None:
    """重复耗尽时回链段被替换而非叠加，且既有正文内容保留。

    交接评论是唯一事实源，PR 正文只是发布那一刻的快照；回链段要保证人顺链找到的是
    **最新**一条记录，而不是一串互相矛盾的旧链接。
    """
    from backend.core.use_cases.agent_runner_issue_handlers import (
        _build_pr_handoff_ref_block,
        _with_pr_handoff_ref,
    )

    first_ref = _build_pr_handoff_ref_block(
        "https://github.com/example/repo/issues/123#issuecomment-1",
        attempt_count=3,
        handoff_comment_posted=True,
    )
    second_ref = _build_pr_handoff_ref_block(
        "https://github.com/example/repo/issues/123#issuecomment-9",
        attempt_count=5,
        handoff_comment_posted=True,
    )

    once = _with_pr_handoff_ref("Closes #123\n\nbody from generator.\n", first_ref)
    twice = _with_pr_handoff_ref(once, second_ref)

    assert "issuecomment-9" in twice
    assert "issuecomment-1" not in twice
    assert twice.count("<!-- iar:failure-context-ref -->") == 1
    assert twice.startswith("Closes #123")


def test_no_new_blocked_label_or_failure_classifier_was_introduced() -> None:
    """本 PRD 刻意不新增标签、状态枚举或失败判定器——这条断言把"刻意"钉成机器约束。

    合并门禁已由"缺 ``validation/verifier-passed`` 即拒绝"覆盖；再加一个
    ``validation/blocked`` 只会造出第二个事实源。"是什么性质的失败"由 Agent 在正文里
    陈述，机器不做判定，因此也不该出现承载判定的枚举类型。
    """
    forbidden_tokens = (
        "validation/blocked",
        "ReviewOutcomeKind",
        "BlockedDraftContext",
        "FailureClassif",
    )
    offenders: list[str] = []
    for python_path in _SRC_ROOT.rglob("*.py"):
        source_text = python_path.read_text(encoding="utf-8")
        for forbidden_token in forbidden_tokens:
            if forbidden_token in source_text:
                offenders.append(f"{python_path.name}:{forbidden_token}")

    assert offenders == []


def test_default_pr_publication_does_not_invoke_content_generator() -> None:
    """默认发布直接生成事实正文，不等待 AI，但不改变其他内容生成目标。"""
    from tests.conftest import FakeContentGenerator

    issue = IssueSummary(
        number=123,
        title="Fast publication",
        url="https://github.com/a/b/issues/123",
        body="",
        labels=(),
    )
    generator = FakeContentGenerator(response="unused")
    github_client = FakeGitHubClient()
    create_draft_pr(
        issue,
        Path("."),
        AppConfig(),
        github_client,
        _clean_branch_runner(),
        content_generator=generator,
    )
    assert generator.prompts == []
    published_body = next(
        call["body"] for call in github_client.calls if call["method"] == "create_draft_pr"
    )
    assert all(
        f"## {section}" in published_body
        for section in ("Summary", "Validation", "Risk", "Reviewer Notes")
    )
    assert "does not assert PASS" in published_body
    assert "No tracked validation reports" in published_body


def test_pr_fallback_links_only_reports_in_publication_head(tmp_path: Path) -> None:
    """真实 Git 负控：staged-only 不可链接，HEAD 已提交但工作树删除仍可链接。"""
    from backend.core.use_cases.agent_runner_pr_fallback import build_pr_fallback_body
    from backend.core.use_cases.generated_content import PrContext
    from backend.infrastructure.process_runner import SubprocessRunner

    runner = SubprocessRunner()
    runner.run(["git", "init", "-b", "main"], cwd=tmp_path)
    evidence_path = tmp_path / "tasks/evidence/issue-123/report.evidence-report.md"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("REJECT", encoding="utf-8")
    runner.run(["git", "add", "."], cwd=tmp_path)
    runner.run(
        [
            "git",
            "-c",
            "user.name=Validation",
            "-c",
            "user.email=validation@example.invalid",
            "commit",
            "-m",
            "test: committed evidence",
        ],
        cwd=tmp_path,
    )
    publication_head = runner.run(["git", "rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    staged_report = evidence_path.parent / "staged-only.verifier-report.md"
    staged_report.write_text("PASS", encoding="utf-8")
    runner.run(["git", "add", str(staged_report)], cwd=tmp_path)
    (evidence_path.parent / "untracked.verifier-report.md").write_text("PASS", encoding="utf-8")
    evidence_path.unlink()
    context = PrContext(
        123,
        "Fix",
        "",
        "issue-123",
        "main",
        "fix: scope",
        "fix: scope",
        "file | 1 +",
        "file | 1 +",
        repo_url="https://github.com/a/b",
    )
    published_body = build_pr_fallback_body(context, "tasks/evidence/issue-123", tmp_path, runner)
    relative_path = evidence_path.relative_to(tmp_path).as_posix()
    assert f"https://github.com/a/b/blob/{publication_head}/{relative_path}" in published_body
    assert "staged-only.verifier-report" not in published_body
    assert "untracked.verifier-report" not in published_body
    assert "does not assert PASS" in published_body
