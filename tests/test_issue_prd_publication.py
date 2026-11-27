"""PRD 分支发布、恢复与 runner 读取的真实 Git 验证。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.api.cli import main
from backend.core.shared.models.agent_runner import (
    AppConfig,
    GeneratedContentConfig,
    GitConfig,
    IssueSummary,
    LabelConfig,
    RunnerConfig,
    WorktreeConfig,
)
from backend.core.use_cases.agent_runner_agent_selection import choose_agent
from backend.core.use_cases.agent_runner_worktree_create import create_or_reuse_worktree
from backend.core.use_cases.create_issue_from_prd import (
    IssueFromPrdRequest,
    create_issue_from_prd,
)
from backend.core.use_cases.issue_prd_publication import (
    IssuePrdPublicationRequest,
    _publish_prd_commit,
    read_published_issue_prd_text,
    recover_issue_prd_publication,
    validate_issue_prd_path,
)
from backend.core.use_cases.lifecycle_agent_resolution import attach_prd_lifecycle_overrides
from backend.infrastructure.process_runner import SubprocessRunner
from tests.conftest import FakeGitHubClient


def _run_git(
    repo_path: Path,
    *git_args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """在测试仓库中执行 Git 并以 UTF-8 捕获输出。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _create_git_remote(repo_path: Path, remote_path: Path) -> None:
    """准备以 GitHub URL 作为 origin、实际落到本地 bare 仓库的 main。"""
    repo_path.mkdir(parents=True)
    _run_git(repo_path, "init", "--initial-branch=main")
    _run_git(repo_path, "config", "user.name", "Publication Test")
    _run_git(repo_path, "config", "user.email", "publication-test@example.com")
    (repo_path / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "initial")

    remote_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(remote_path.parent, "init", "--bare", "--initial-branch=main", str(remote_path))
    _run_git(repo_path, "remote", "add", "origin", "https://github.com/example/repo.git")
    _run_git(
        repo_path,
        "config",
        "--add",
        f"url.{remote_path.as_uri()}.insteadOf",
        "https://github.com/example/repo.git",
    )
    _run_git(repo_path, "push", "-u", "origin", "main")


def _set_protected_branch_hook(remote_path: Path, *, reject_issue_branch: bool = False) -> Path:
    """安装 bare remote 钩子，拒绝 main 和可选的 kc/example/repo/issue-42 写入。"""
    hook_path = remote_path / "hooks" / "pre-receive"
    reject_issue_rule = (
        '  case "$ref" in refs/heads/kc/example/repo/issue-42) echo "task branch rejected" >&2; exit 1;; esac\n'
        if reject_issue_branch
        else ""
    )
    hook_path.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  case "$ref" in refs/heads/main) echo "protected main" >&2; exit 1;; esac\n'
        f"{reject_issue_rule}"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o755)
    return hook_path


class _StatefulGitHubClient(FakeGitHubClient):
    """记录创建的 Issue 与之后的正文、标签写入，模拟 GitHub 持久状态。"""

    def create_issue(self, *, title: str, body: str, labels: tuple[str, ...] | list[str]) -> str:
        issue_url = super().create_issue(title=title, body=body, labels=labels)
        self._issue_get_bodies[42] = body
        self._issue_labels[42] = tuple(labels)
        self._issue_title = title
        return issue_url

    def edit_issue_body(self, issue_number: int, body: str) -> None:
        super().edit_issue_body(issue_number, body)
        self._issue_get_bodies[issue_number] = body


def _cli_context(repo_path: Path) -> AppConfig:
    """返回 CLI 中禁用生成式内容的固定配置。"""
    return AppConfig(generated_content=GeneratedContentConfig(enabled=False))


def _run_issue_cli(
    repo_path: Path,
    github_client: _StatefulGitHubClient,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> int:
    """通过真实 kc CLI 路由命令，保留 Git 子进程与 GitHub 测试替身。"""
    from types import SimpleNamespace

    from backend.api import cli

    repo_context = SimpleNamespace(repo_path=repo_path, config=_cli_context(repo_path))
    monkeypatch.chdir(repo_path)
    monkeypatch.setattr(cli, "resolve_issue_from_prd_target", lambda *args, **kwargs: repo_context)
    monkeypatch.setattr(cli, "require_repository_initialized", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_ensure_gh_auth_or_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "create_github_client", lambda *args, **kwargs: github_client)
    return main(["issue", "create", *arguments])


def _remote_ref_sha(remote_path: Path, branch: str) -> str:
    """读取 bare remote 分支提交。"""
    result = subprocess.run(
        ["git", "--git-dir", str(remote_path), "rev-parse", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_real_cli_publishes_issue_branch_when_main_rejects_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """真实 kc CLI 在 main 被保护时发布 issue 分支，并延后 ready 写入。"""
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    _set_protected_branch_hook(remote_path)

    base_sha = _remote_ref_sha(remote_path, "main")
    probe_repo = tmp_path / "protected-probe"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote_path), str(probe_repo)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _run_git(probe_repo, "config", "user.name", "Publication Test")
    _run_git(probe_repo, "config", "user.email", "publication-test@example.com")
    (probe_repo / "probe.txt").write_text("protected\n", encoding="utf-8")
    _run_git(probe_repo, "add", "probe.txt")
    _run_git(probe_repo, "commit", "-m", "verify protected main")
    rejected_main_push = _run_git(probe_repo, "push", "origin", "main", check=False)
    assert rejected_main_push.returncode != 0
    assert "protected main" in rejected_main_push.stderr
    assert _remote_ref_sha(remote_path, "main") == base_sha

    prd_path = repo_path / "tasks" / "pending" / "feature.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: Branch publication\n\n## Acceptance Checklist\n\n- [ ] Publish only this PRD\n",
        encoding="utf-8",
    )
    github_client = _StatefulGitHubClient()

    exit_code = _run_issue_cli(
        repo_path,
        github_client,
        monkeypatch,
        ["tasks/pending/feature.md", "--publish-to-issue-branch"],
    )

    assert exit_code == 0
    assert _remote_ref_sha(remote_path, "main") == base_sha
    issue_branch_sha = _remote_ref_sha(remote_path, "kc/example/repo/issue-42")
    changed_paths = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote_path),
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            issue_branch_sha,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    assert changed_paths == ["tasks/pending/feature.md"]
    created_call = next(call for call in github_client.calls if call["method"] == "create_issue")
    assert "agent/ready" not in created_call["labels"]
    call_methods = [call["method"] for call in github_client.calls]
    assert call_methods.index("edit_issue_body") < call_methods.index("edit_issue_labels")
    assert "agent/ready" in github_client._issue_labels[42]
    final_body = github_client._issue_get_bodies[42]
    assert (
        f"state=published queue_ready=true branch=kc/example/repo/issue-42 commit={issue_branch_sha}"
        in final_body
    )
    assert f"/blob/{issue_branch_sha}/tasks/pending/feature.md" in final_body
    assert "Created GitHub Issue" in capsys.readouterr().out


def test_recovery_reuses_issue_is_idempotent_and_rejects_remote_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """恢复入口复用原 Issue、重试同内容，并拒绝覆盖分支冲突。"""
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    hook_path = _set_protected_branch_hook(remote_path, reject_issue_branch=True)
    prd_path = repo_path / "tasks" / "pending" / "feature.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: Recovery\n\n## Acceptance Checklist\n\n- [ ] Recover this PRD\n",
        encoding="utf-8",
    )
    github_client = _StatefulGitHubClient()

    failed_exit_code = _run_issue_cli(
        repo_path,
        github_client,
        monkeypatch,
        ["tasks/pending/feature.md", "--publish-to-issue-branch"],
    )
    failed_output = capsys.readouterr()
    assert failed_exit_code != 0
    assert "Issue #42" in f"{failed_output.out}\n{failed_output.err}"
    assert "Resume this Issue with:" in caplog.text
    assert "--recover-issue 42" in caplog.text
    assert "agent/ready" not in github_client._issue_labels[42]
    assert (
        _run_git(
            repo_path, "ls-remote", "--heads", "origin", "refs/heads/kc/example/repo/issue-42"
        ).stdout
        == ""
    )

    hook_path.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        '  case "$ref" in refs/heads/main) echo "protected main" >&2; exit 1;; esac\n'
        "done\nexit 0\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o755)
    recovery_args = ["tasks/pending/feature.md", "--recover-issue", "42"]
    first_recovery = _run_issue_cli(repo_path, github_client, monkeypatch, recovery_args)
    assert first_recovery == 0
    initial_issue_branch_sha = _remote_ref_sha(remote_path, "kc/example/repo/issue-42")
    second_recovery = _run_issue_cli(repo_path, github_client, monkeypatch, recovery_args)
    assert second_recovery == 0
    assert _remote_ref_sha(remote_path, "kc/example/repo/issue-42") == initial_issue_branch_sha

    prd_path.write_text(
        f"{prd_path.read_text(encoding='utf-8')}\nLocal conflicting edit.\n",
        encoding="utf-8",
    )
    conflict_exit_code = _run_issue_cli(repo_path, github_client, monkeypatch, recovery_args)
    conflict_output = capsys.readouterr()
    conflict_text = f"{conflict_output.out}\n{conflict_output.err}"
    assert conflict_exit_code != 0
    assert "different content" in conflict_text
    assert "remote content was preserved" in conflict_text
    assert _remote_ref_sha(remote_path, "kc/example/repo/issue-42") == initial_issue_branch_sha
    assert sum(call["method"] == "create_issue" for call in github_client.calls) == 1


def test_recovery_preserves_pinned_prd_commit_after_branch_advances(tmp_path: Path) -> None:
    """分支后续提交不应让同内容恢复改写 PRD 的固定提交链接。"""
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    prd_relative_path = Path("tasks/pending/feature.md")
    issue_url = "https://github.com/example/repo/issues/42"
    prd_path = repo_path / prd_relative_path
    prd_path.parent.mkdir(parents=True)
    prd_text = f"# PRD: Pinned publication\n\n- GitHub Issue: {issue_url}\n"

    _run_git(repo_path, "checkout", "-b", "issue-42")
    prd_path.write_text(prd_text, encoding="utf-8")
    _run_git(repo_path, "add", prd_relative_path.as_posix())
    _run_git(repo_path, "commit", "-m", "publish PRD")
    _run_git(repo_path, "push", "origin", "issue-42")
    publication_commit_sha = _run_git(repo_path, "rev-parse", "HEAD").stdout.strip()
    _run_git(repo_path, "checkout", "main")
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(prd_text, encoding="utf-8")

    github_client = _StatefulGitHubClient()
    github_client._issue_get_bodies[42] = (
        f"- PRD path: `{prd_relative_path.as_posix()}` "
        f"（[在 GitHub 打开](https://github.com/example/repo/blob/{publication_commit_sha}/"
        f"{prd_relative_path.as_posix()})）\n\n"
        "<!-- kc:prd-publication version=1 state=published queue_ready=true "
        f"branch=issue-42 commit={publication_commit_sha} -->\n"
    )
    github_client._issue_labels[42] = (LabelConfig().ready,)

    branch_writer_path = tmp_path / "branch-writer"
    subprocess.run(
        ["git", "clone", "--branch", "issue-42", str(remote_path), str(branch_writer_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _run_git(branch_writer_path, "config", "user.name", "Publication Test")
    _run_git(branch_writer_path, "config", "user.email", "publication-test@example.com")
    (branch_writer_path / "implementation.txt").write_text(
        "implementation progress\n", encoding="utf-8"
    )
    _run_git(branch_writer_path, "add", "implementation.txt")
    _run_git(branch_writer_path, "commit", "-m", "record implementation progress")
    _run_git(branch_writer_path, "push", "origin", "issue-42")
    advanced_branch_sha = _run_git(branch_writer_path, "rev-parse", "HEAD").stdout.strip()

    recovery_result = recover_issue_prd_publication(
        request=IssuePrdPublicationRequest(
            repo_path=repo_path,
            prd_path=prd_relative_path,
            issue_number=42,
            git_remote="origin",
            git_base_branch="main",
        ),
        github_client=github_client,
        process_runner=SubprocessRunner(),
    )

    assert advanced_branch_sha != publication_commit_sha
    assert recovery_result.commit_sha == publication_commit_sha
    assert _remote_ref_sha(remote_path, "issue-42") == advanced_branch_sha
    final_issue_body = github_client._issue_get_bodies[42]
    assert f"/blob/{publication_commit_sha}/{prd_relative_path.as_posix()}" in final_issue_body
    assert f"commit={publication_commit_sha}" in final_issue_body


def test_publication_base_isolated_from_concurrent_fetch_head_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """另一个并发 fetch 改写 FETCH_HEAD 时仍从指定 base 分支发布。"""
    from collections.abc import Sequence

    from backend.core.shared.models.agent_runner import CommandResult

    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    base_sha = _remote_ref_sha(remote_path, "main")
    _run_git(repo_path, "checkout", "-b", "issue-decoy")
    (repo_path / "decoy.txt").write_text("unrelated branch\n", encoding="utf-8")
    _run_git(repo_path, "add", "decoy.txt")
    _run_git(repo_path, "commit", "-m", "create competing branch")
    _run_git(repo_path, "push", "origin", "issue-decoy")
    _run_git(repo_path, "checkout", "main")

    prd_relative_path = Path("tasks/pending/feature.md")
    (repo_path / prd_relative_path).parent.mkdir(parents=True)
    prd_text = "# PRD: Isolated fetch ref\n"
    (repo_path / prd_relative_path).write_text(prd_text, encoding="utf-8")
    subprocess_runner = SubprocessRunner()
    original_run = subprocess_runner.run
    fetch_head_was_interleaved = False

    def run_with_competing_fetch(
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> CommandResult:
        nonlocal fetch_head_was_interleaved
        command_result = original_run(command, cwd=cwd, check=check)
        if (
            any("refs/heads/main" in argument for argument in command)
            and not fetch_head_was_interleaved
        ):
            fetch_head_was_interleaved = True
            original_run(
                ["git", "fetch", "origin", "refs/heads/issue-decoy"],
                cwd=cwd,
                check=False,
            )
        return command_result

    monkeypatch.setattr(subprocess_runner, "run", run_with_competing_fetch)
    _publish_prd_commit(
        repo_path=repo_path,
        relative_prd_path=prd_relative_path,
        prd_text=prd_text,
        issue_number=42,
        issue_branch="issue-42",
        git_remote="origin",
        git_base_branch="main",
        process_runner=subprocess_runner,
    )

    assert fetch_head_was_interleaved
    published_changed_paths = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote_path),
            "diff",
            "--name-only",
            "refs/heads/main",
            "refs/heads/issue-42",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    assert published_changed_paths == [prd_relative_path.as_posix()]
    assert _remote_ref_sha(remote_path, "main") == base_sha


def test_runner_reads_published_branch_prd_before_selecting_agent_and_creates_worktree(
    tmp_path: Path,
) -> None:
    """真实 runner 编排从干净 checkout 读取分支 PRD 并在任务 worktree 中执行。"""
    from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
    from backend.core.use_cases import agent_runner_orchestration_runtime as orchestration_runtime

    remote_path = tmp_path / "remote.git"
    publisher_path = tmp_path / "publisher"
    _create_git_remote(publisher_path, remote_path)
    prd_relative_path = Path("tasks/pending/routing.md")
    prd_path = publisher_path / prd_relative_path
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: Routing\n\n- lifecycle_agents:\n  - implementation: kimi\n\n## Introduction\nBranch copy.\n",
        encoding="utf-8",
    )
    _run_git(publisher_path, "checkout", "-b", "issue-42")
    _run_git(publisher_path, "add", prd_relative_path.as_posix())
    _run_git(publisher_path, "commit", "-m", "publish issue 42 PRD")
    _run_git(publisher_path, "push", "origin", "issue-42")
    published_sha = _run_git(publisher_path, "rev-parse", "HEAD").stdout.strip()

    clean_repo_path = tmp_path / "clean-checkout"
    subprocess.run(
        [
            "git",
            "clone",
            "--single-branch",
            "--branch",
            "main",
            str(remote_path),
            str(clean_repo_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert not (clean_repo_path / prd_relative_path).exists()
    assert (
        _run_git(
            clean_repo_path,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/remotes/origin/issue-42",
            check=False,
        ).returncode
        != 0
    )
    issue_body = (
        f"- PRD path: `{prd_relative_path.as_posix()}`\n\n"
        "<!-- kc:prd-publication version=1 state=published queue_ready=true "
        f"branch=issue-42 commit={published_sha} -->\n"
    )
    issue = IssueSummary(
        number=42,
        title="Routing",
        url="https://github.com/example/repo/issues/42",
        body=issue_body,
        labels=("agent/ready",),
    )
    runner = SubprocessRunner()
    config = AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        runner=RunnerConfig(max_agent_switches=0),
        worktree=WorktreeConfig(
            create_command="git worktree add --detach {worktree_path} {base_branch}".format(
                worktree_path=tmp_path / "worktrees" / "issue-42",
                base_branch="{base_branch}",
            ),
            reuse_command="true",
            path_command=f"echo {tmp_path / 'worktrees' / 'issue-42'}",
            provision_database=False,
        ),
    )
    selected_agents: list[str] = []
    worktree_paths: list[Path] = []

    def _stub_ready_issue(
        *,
        issue: IssueSummary,
        agent: str,
        repo_path: Path,
        config: AppConfig,
        process_runner: SubprocessRunner,
        on_claimed=None,
        **kwargs: object,
    ) -> None:
        del kwargs
        selected_agents.append(agent)
        if on_claimed is not None:
            on_claimed()
        worktree_paths.append(create_or_reuse_worktree(repo_path, issue, config, process_runner))

    original_issue_handler = orchestration_runtime._process_ready_issue
    orchestration_runtime._process_ready_issue = _stub_ready_issue
    try:
        result = orchestration_runtime._process_single_issue(
            issue,
            "ready",
            repo_path=clean_repo_path,
            config=config,
            agent="auto",
            github_client=FakeGitHubClient(),
            process_runner=runner,
            content_generator=None,
            run_history_store=None,
            run_trigger="test",
            effective_repo_id="publication-test",
            output_view=NoOpRunnerLiveView(),
        )
    finally:
        orchestration_runtime._process_ready_issue = original_issue_handler

    assert result == 0
    assert not (clean_repo_path / prd_relative_path).exists()
    assert selected_agents == ["kimi"]
    assert worktree_paths[0].joinpath(prd_relative_path).is_file()
    assert "Branch copy." in worktree_paths[0].joinpath(prd_relative_path).read_text(
        encoding="utf-8"
    )
    assert (
        choose_agent(
            attach_prd_lifecycle_overrides(
                issue,
                clean_repo_path,
                process_runner=runner,
                git_remote="origin",
            ),
            config,
            "auto",
        )
        == "kimi"
    )


def test_read_published_issue_prd_text_uses_marker_commit_after_branch_advances(
    tmp_path: Path,
) -> None:
    """任务分支前进后仍按 Issue marker 固定的提交读取 PRD。"""
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    prd_relative_path = Path("tasks/pending/feature.md")
    prd_path = repo_path / prd_relative_path
    prd_path.parent.mkdir(parents=True)

    _run_git(repo_path, "checkout", "-b", "issue-42")
    pinned_prd_text = (
        "# PRD: Pinned source\n\n"
        "- lifecycle_agents:\n  - implementation: kimi\n\n"
        "Published snapshot.\n"
    )
    prd_path.write_text(pinned_prd_text, encoding="utf-8")
    _run_git(repo_path, "add", prd_relative_path.as_posix())
    _run_git(repo_path, "commit", "-m", "publish PRD")
    _run_git(repo_path, "push", "-u", "origin", "issue-42")
    published_commit_sha = _run_git(repo_path, "rev-parse", "HEAD").stdout.strip()

    branch_tip_prd_text = pinned_prd_text.replace("kimi", "codebuddy").replace(
        "Published snapshot.", "Branch tip snapshot."
    )
    prd_path.write_text(branch_tip_prd_text, encoding="utf-8")
    _run_git(repo_path, "add", prd_relative_path.as_posix())
    _run_git(repo_path, "commit", "-m", "update branch PRD")
    _run_git(repo_path, "push", "origin", "issue-42")

    issue = IssueSummary(
        number=42,
        title="Pinned source",
        url="https://github.com/example/repo/issues/42",
        body=(
            f"- PRD path: `{prd_relative_path.as_posix()}`\n\n"
            "<!-- kc:prd-publication version=1 state=published queue_ready=true "
            f"branch=issue-42 commit={published_commit_sha} -->\n"
        ),
        labels=(),
    )

    published_prd_text = read_published_issue_prd_text(
        issue=issue,
        repo_path=repo_path,
        git_remote="origin",
        process_runner=SubprocessRunner(),
    )

    assert published_prd_text == pinned_prd_text
    assert published_prd_text != branch_tip_prd_text


def test_recovery_explicit_no_ready_removes_existing_ready_label(tmp_path: Path) -> None:
    """恢复显式 --no-ready 时会校正已经发布 Issue 的 ready 标签。"""
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    _create_git_remote(repo_path, remote_path)
    prd_path = repo_path / "tasks" / "pending" / "feature.md"
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(
        "# PRD: No ready\n\n- GitHub Issue: https://github.com/example/repo/issues/42\n",
        encoding="utf-8",
    )
    github_client = _StatefulGitHubClient()
    github_client._issue_get_bodies[42] = (
        "- PRD path: `tasks/pending/feature.md`\n\n"
        "<!-- kc:prd-publication version=1 state=pending queue_ready=true -->\n"
    )
    github_client._issue_labels[42] = (LabelConfig().ready,)

    result = recover_issue_prd_publication(
        request=IssuePrdPublicationRequest(
            repo_path=repo_path,
            prd_path=Path("tasks/pending/feature.md"),
            issue_number=42,
            git_remote="origin",
            git_base_branch="main",
            queue_ready=False,
        ),
        github_client=github_client,
        process_runner=SubprocessRunner(),
    )

    assert result.ready is False
    assert LabelConfig().ready not in github_client._issue_labels[42]
    assert "queue_ready=false" in github_client._issue_get_bodies[42]


@pytest.mark.parametrize("prd_relative_path", ["feature.md", "tasks/pending/feature name.md"])
def test_publication_rejects_paths_the_runner_cannot_parse(
    tmp_path: Path,
    prd_relative_path: str,
) -> None:
    """任务分支发布在创建 Issue 前拒绝 runner 锚点无法解析的 PRD 路径。"""
    repo_path = tmp_path / "repo"
    prd_path = repo_path / prd_relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text("# PRD: Invalid anchor path\n", encoding="utf-8")

    with pytest.raises(ValueError, match="runner can parse its Issue PRD anchor"):
        validate_issue_prd_path(repo_path, Path(prd_relative_path))


def test_core_rejects_force_before_creating_issue_for_branch_publication(tmp_path: Path) -> None:
    """core API 在副作用前拒绝 force 与分支发布组合，防止留下不可恢复 Issue。"""
    prd_path = tmp_path / "feature.md"
    prd_path.write_text(
        "# PRD: Existing link\n\n- GitHub Issue: https://github.com/example/repo/issues/7\n",
        encoding="utf-8",
    )
    github_client = _StatefulGitHubClient()

    with pytest.raises(ValueError, match="cannot be combined with publish_to_issue_branch"):
        create_issue_from_prd(
            request=IssueFromPrdRequest(
                repo_path=tmp_path,
                prd_path=prd_path,
                issue_type="feature",
                force=True,
                publish_to_issue_branch=True,
            ),
            github_client=github_client,
        )

    assert not any(call["method"] == "create_issue" for call in github_client.calls)
