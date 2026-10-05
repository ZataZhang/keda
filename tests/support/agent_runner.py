"""Shared builders and fake-runner helpers for agent runner tests.

Extracted from the historical ``tests/test_run_agent.py`` monolith so the
per-module agent runner test files can reuse one definition instead of
copying fixtures around.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    PostPrSupervisorConfig,
    PrePrReviewConfig,
    RunnerConfig,
    WorktreeConfig,
)
from backend.core.shared.prd_checklist import parse_prd_checklist
from backend.infrastructure.process_runner import CommandFailedError


def make_ready_issue() -> IssueSummary:
    """Return the canonical ``agent/ready`` Issue #123 used by runner tests."""
    return IssueSummary(
        number=123,
        title="Example",
        url="https://github.com/example/repo/issues/123",
        body="Example body",
        labels=("agent/ready", "agent/codex"),
    )


def make_prd_issue(
    prd_path: str = "tasks/pending/example.md",
) -> IssueSummary:
    """Return a ready Issue whose body carries a backtick-quoted PRD anchor."""
    return IssueSummary(
        number=123,
        title="Example",
        url="https://github.com/example/repo/issues/123",
        body=f"PRD path: `{prd_path}`",
        labels=("agent/ready", "agent/codex"),
    )


def config_with_review_disabled(
    worktree_path: Path | None = None,
    *verification_commands: str,
    max_recovery_attempts: int = 2,
    recovery_retry_delay_seconds: int = 0,
    agent_fallback_order: tuple[str, ...] = (),
) -> AppConfig:
    """Return a config with pre-PR review and post-PR supervisor disabled.

    Cross-agent fallback is disabled by default so that tests of the single-agent
    recovery loop are not affected by the global default fallback chain.
    """
    commands = verification_commands or ("just test",)
    worktree_cfg = (
        WorktreeConfig(path_command=f"echo {worktree_path}") if worktree_path else WorktreeConfig()
    )
    return AppConfig(
        runner=RunnerConfig(
            max_recovery_attempts=max_recovery_attempts,
            recovery_retry_delay_seconds=recovery_retry_delay_seconds,
            verification_commands=commands,
            agent_fallback_order=agent_fallback_order,
        ),
        worktree=worktree_cfg,
        pre_pr_review=PrePrReviewConfig(enabled=False),
        post_pr_supervisor=PostPrSupervisorConfig(enabled=False),
    )


def worktree_path_response(
    worktree_path: Path,
) -> tuple[tuple[str, ...], CommandResult]:
    """Return the ``worktree.path_command`` argv and its canned stdout result."""
    command = ("echo", str(worktree_path))
    return command, CommandResult(
        command=command,
        return_code=0,
        stdout=f"{worktree_path}\n",
        stderr="",
    )


def default_worktree_path_response(
    issue_number: int,
) -> dict[tuple[str, ...], CommandResult]:
    """Return a response for the default ``worktree.path_command`` template.

    The default ``path_command`` is ``"iar worktree path --branch
    issue-{issue_number}"``. Tests that keep the default config but still
    resolve a worktree must register this response: path resolution now rejects
    empty stdout instead of silently falling back to the process cwd. The
    emitted ``"."`` anchors to the caller's ``repo_path`` (which the tests
    control and which exists).
    """
    command = ("iar", "worktree", "path", "--branch", f"issue-{issue_number}")
    return {
        command: CommandResult(
            command=command,
            return_code=0,
            stdout=".\n",
            stderr="",
        )
    }


def git_remote_command() -> tuple[str, ...]:
    """Return the argv the runner uses to probe configured git remotes."""
    return ("git", "remote")


def git_remote_result(*remote_names: str) -> CommandResult:
    """Return a ``git remote`` result listing the given remote names."""
    command = git_remote_command()
    return CommandResult(
        command=command,
        return_code=0,
        stdout="".join(f"{remote_name}\n" for remote_name in remote_names),
        stderr="",
    )


def write_commit_request(worktree_path: Path, commit_message: str) -> None:
    """Write the agent's ``.agent-runner/commit-request.json`` proxy request."""
    request_path = worktree_path / ".agent-runner" / "commit-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        f'{{"commit_message": "{commit_message}"}}\n',
        encoding="utf-8",
    )


# 验收状态横幅（prd skill Machine Contract v5）三种状态的原文行。交付与发布门禁会
# 核对横幅与清单是否一致，所以凡是期望"能归档 / 能发布"的夹具都要带上对的那一行。
NOT_STARTED_BANNER = "> ⬜ **验收状态**：未开工。"
AWAITING_HUMAN_BANNER = (
    "> 🧍 **验收状态**：待人工验收 — 执行侧已完成，仅剩 1 项 Human-Confirmed 未确认，证据包见 §9。"
)
ACCEPTED_BANNER = "> ✅ **验收状态**：已验收 — 验收清单已全部完成。"


def build_acceptance_prd(
    banner_line: str | None,
    *,
    execution_marks: Sequence[str] = ("x",),
    human_marks: Sequence[str] = (),
) -> str:
    """Build a PRD with an acceptance status banner and a grouped Acceptance Checklist.

    Args:
        banner_line: 横幅原文行；``None`` 表示不写横幅。
        execution_marks: 执行侧分组里每个条目的复选框标记（``"x"`` / ``" "`` / ``"~"``）。
        human_marks: Human-Confirmed 分组里每个条目的标记；为空时不写该分组。

    Returns:
        PRD 全文。
    """
    prd_lines = ["# PRD: Example", ""]
    if banner_line is not None:
        prd_lines += [banner_line, ""]
    prd_lines += ["## 9. Acceptance Checklist", "", "### Validation Acceptance", ""]
    prd_lines += [
        f"- [{item_mark}] rv-{item_number}: executor-owned item {item_number}"
        for item_number, item_mark in enumerate(execution_marks, start=1)
    ]
    if human_marks:
        prd_lines += ["", "### Human-Confirmed", ""]
        prd_lines += [
            f"- [{item_mark}] decision {item_number}: answered by a human"
            for item_number, item_mark in enumerate(human_marks, start=1)
        ]
    prd_lines.append("")
    return "\n".join(prd_lines)


def require_banner_aware_prd_skill() -> None:
    """横幅类断言的前置：测试进程读到的 prd skill 必须报告验收状态横幅（v5 起）。

    v3/v4 skill 不报告 ``acceptance_status``，门禁会按设计跳过横幅检查，横幅类断言
    要么莫名变红、要么没跨过横幅判据就变绿。这里直接以可行动的信息失败，指明该先
    装 v5 skill（发版顺序见归档语义 PRD 的 §8 Notes (c)）。
    """
    if parse_prd_checklist(build_acceptance_prd(ACCEPTED_BANNER)).acceptance_status is None:
        pytest.fail(
            "the prd skill used by this test run does not report `acceptance_status` "
            "(Machine Contract < 5); install the v5 prd skill (`iar init`) or point "
            "IAR_PRD_SKILL_PATH at it before running banner assertions"
        )


def write_complete_prd(worktree_path: Path, relative_path: str = "tasks/example.md") -> None:
    """Write a PRD whose Acceptance Checklist is fully checked."""
    prd_path = worktree_path / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(
        "\n".join(
            [
                "# PRD: Example",
                "",
                ACCEPTED_BANNER,
                "",
                "## 7. Acceptance Checklist",
                "",
                "- [x] item 1",
                "- [x] item 2",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_incomplete_prd(worktree_path: Path, relative_path: str = "tasks/example.md") -> None:
    """Write a PRD whose Acceptance Checklist still has an unchecked item."""
    prd_path = worktree_path / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(
        "\n".join(
            [
                "# PRD: Example",
                "",
                "## 7. Acceptance Checklist",
                "",
                "- [x] item 1",
                "- [ ] item 2",
                "",
            ]
        ),
        encoding="utf-8",
    )


def is_bash_wrapped_verification_call(
    command: Sequence[str], expected_inner: tuple[str, ...]
) -> bool:
    """Match either the legacy flat argv or the ``bash -lc <cmd>`` wrap.

    ``run_verification`` wraps each command in ``bash -lc`` so that shell
    metacharacters (command substitution, globs, pipes, env var
    interpolation) are honored. Custom ``FakeProcessRunner`` subclasses in
    the agent runner tests register responses keyed on the inner command
    tuple (e.g. ``("just", "test")``); they need a stable way to recognize
    the wrap so that pre- and post-wrap test assertions keep matching.
    """
    if tuple(command) == expected_inner:
        return True
    if len(command) == 3 and command[0] == "bash" and command[1] == "-lc":
        import shlex as _shlex

        try:
            return tuple(_shlex.split(command[2])) == expected_inner
        except ValueError:
            return False
    return False


def run_git(repo_path: Path, *git_args: str) -> str:
    """Run a real ``git`` command inside ``repo_path`` and return its stdout.

    Real-git tests need an arrange step that git itself performs (``git mv``
    staging a rename, ``git rm`` staging a deletion); mock runners cannot
    reproduce the resulting index states.
    """
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def init_bare_git_repo(path: Path) -> Path:
    """Create a bare repository at ``path`` to act as a test remote."""
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def init_git_repo(path: Path) -> Path:
    """Create a repository at ``path`` on ``main`` with a deterministic identity."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    return path


def create_commit(path: Path, message: str) -> str:
    """Commit staged changes (empty commits allowed) and return the new HEAD sha."""
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", message],
        check=True,
        capture_output=True,
    )
    sha_result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sha_result.stdout.strip()


def transient_command_error() -> CommandFailedError:
    """Return an agent failure whose output matches the transient-retry predicate."""
    return CommandFailedError(
        1,
        ["claude", "--dangerously-skip-permissions", "-p", "PROMPT"],
        output=("[agent error] API Error: The socket connection was closed unexpectedly."),
        stderr="",
    )


def worktree_site(root: Path, issue_number: int) -> Path:
    """默认 ``iar worktree`` 布局下 Issue worktree 的落点（不创建目录）。"""
    return root / ".iar-worktrees" / f"issue-{issue_number}"


def worktree_site_response(root: Path, issue_number: int) -> dict[tuple[str, ...], CommandResult]:
    """造一个真实存在的 worktree 现场（含 ``.git`` 指针）并返回 path_command 响应。

    崩溃对账判"现场可解析"要求目录与 ``.git`` 都在（缺 ``.git`` 的目录不是可用
    工作树），因此对账测试不能用 :func:`default_worktree_path_response` 的 ``"."``。
    """
    site = worktree_site(root, issue_number)
    site.mkdir(parents=True, exist_ok=True)
    (site / ".git").write_text("gitdir: /nonexistent/worktrees/issue-1/.git\n", encoding="utf-8")
    command = ("iar", "worktree", "path", "--branch", f"issue-{issue_number}")
    return {
        command: CommandResult(
            command=command,
            return_code=0,
            stdout=f"{site}\n",
            stderr="",
        )
    }
