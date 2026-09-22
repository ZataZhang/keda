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
    get_head_sha,
    publish_changes,
    validate_safe_changes,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import (
    git_remote_command,
    git_remote_result,
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
            draft_pr=GeneratedContentTargetConfig(enabled=True, mode="agent", output="json"),
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
