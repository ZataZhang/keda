"""Tests for the verification recovery loop driven by ``run_once``.

Covers retry-until-success, max-retries exhaustion, the pre-commit lint
recovery scenarios, attempt history reporting and the KeyboardInterrupt
checkpoint on the way out."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AttemptResult,
    CommandResult,
    FailureType,
    PullRequestContext,
)
from backend.core.use_cases.agent_runner_failure import ProviderCapacityError
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import (
    config_with_review_disabled,
    git_remote_command,
    git_remote_result,
    is_bash_wrapped_verification_call,
    make_prd_issue,
    make_ready_issue,
    worktree_path_response,
    write_commit_request,
)


def test_recovery_loop_success_on_second_attempt(tmp_path: Path) -> None:
    """Runner should succeed when recovery agent fixes the issue on attempt 2."""
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()

    class _RecoverySuccessRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self._sha_calls = 0
            self._agent_calls = 0
            self._committed = False

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            label=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                result = self.responses[command_tuple]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                return result
            if command_tuple[:1] == ("codex",):
                self._agent_calls += 1
                if self._agent_calls == 1:
                    # First attempt: produce no commits
                    return CommandResult(command_tuple, 0, "", "")
                if self._agent_calls == 2:
                    # Recovery: write commit request and succeed
                    write_commit_request(worktree_path, "agent: recovered")
                    return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "rev-parse", "HEAD"):
                self._sha_calls += 1
                sha = "after-sha" if self._sha_calls > 1 else "before-sha"
                return CommandResult(command_tuple, 0, f"{sha}\n", "")
            if command_tuple == ("git", "branch", "--show-current"):
                return CommandResult(command_tuple, 0, "issue-123\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                stdout = "" if self._committed else " M file.txt\n"
                return CommandResult(command_tuple, 0, stdout, "")
            if command_tuple == ("git", "commit", "-m", "agent: recovered"):
                self._committed = True
                return CommandResult(command_tuple, 0, "", "")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _RecoverySuccessRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(worktree_path)

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    exit_code = run_once(
        repo_path=Path("."),
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=fake_client,
        process_runner=fake_runner,
    )

    assert exit_code == 0
    commands = [tuple(command) for command in fake_runner.calls]
    agent_commands = [command for command in commands if command[:1] == ("codex",)]
    assert len(agent_commands) == 2
    comment_calls = [c for c in fake_client.calls if c["method"] == "comment_issue"]
    implementation_comment = [
        c for c in comment_calls if "Implementation Complete" in c.get("body", "")
    ]
    assert len(implementation_comment) == 1
    assert "Attempt History" in implementation_comment[0]["body"]


def test_recovery_loop_exhausted_raises_max_retries(tmp_path: Path) -> None:
    """Runner should fail with MaxRetriesExceededError when all attempts fail."""
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()

    class _ExhaustedRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self._sha_calls = 0

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            label=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                return self.responses[command_tuple]
            if command_tuple[:1] == ("codex",):
                # Always produce no commits
                return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "rev-parse", "HEAD"):
                self._sha_calls += 1
                return CommandResult(command_tuple, 0, "same-sha\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                return CommandResult(command_tuple, 0, "", "")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _ExhaustedRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(
        worktree_path, max_recovery_attempts=1, recovery_retry_delay_seconds=0
    )

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    exit_code = run_once(
        repo_path=Path("."),
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=fake_client,
        process_runner=fake_runner,
    )

    assert exit_code == 1
    failed_calls = [
        c
        for c in fake_client.calls
        if c["method"] == "edit_issue_labels" and config.labels.failed in c.get("add", [])
    ]
    assert len(failed_calls) == 1
    comment_calls = [c for c in fake_client.calls if c["method"] == "comment_issue"]
    failure_comment = comment_calls[-1]
    assert "Attempt History" in failure_comment["body"]
    assert "Failed after 2 attempts" in failure_comment["body"]
    assert "no_commits" in failure_comment["body"]


def test_attempt_history_in_issue_comment(tmp_path: Path) -> None:
    """Successful run should include Attempt History in the implementation comment."""
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()

    class _HistoryRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self._sha_calls = 0
            self._agent_calls = 0

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            label=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                result = self.responses[command_tuple]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                return result
            if command_tuple[:1] == ("codex",):
                self._agent_calls += 1
                if self._agent_calls == 1:
                    # First attempt fails verification
                    return CommandResult(command_tuple, 0, "", "")
                if self._agent_calls == 2:
                    # Recovery succeeds
                    write_commit_request(worktree_path, "agent: fix")
                    return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "rev-parse", "HEAD"):
                self._sha_calls += 1
                sha = "after-sha" if self._sha_calls > 1 else "before-sha"
                return CommandResult(command_tuple, 0, f"{sha}\n", "")
            if command_tuple == ("git", "branch", "--show-current"):
                return CommandResult(command_tuple, 0, "issue-123\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                stdout = " M file.txt\n" if self._agent_calls < 2 else ""
                return CommandResult(command_tuple, 0, stdout, "")
            if command_tuple == ("git", "commit", "-m", "agent: fix"):
                return CommandResult(command_tuple, 0, "", "")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _HistoryRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(worktree_path, "echo ok")

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    exit_code = run_once(
        repo_path=Path("."),
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=fake_client,
        process_runner=fake_runner,
    )

    assert exit_code == 0
    comment_calls = [c for c in fake_client.calls if c["method"] == "comment_issue"]
    implementation_comment = [
        c for c in comment_calls if "Implementation Complete" in c.get("body", "")
    ]
    assert len(implementation_comment) == 1
    body = implementation_comment[0]["body"]
    assert "Attempt History" in body
    assert "success" in body
    assert "| 1 |" in body
    assert "| 2 |" in body


def test_scenario_b_precommit_lint_failure_recovery(tmp_path: Path) -> None:
    """Scene B: Agent committed, just lint failed, recovery fixed, 2nd pass.

    Steps:
    1. Agent writes commit-request (runner will stage and commit on its behalf).
    2. Runner stages with ``git add -A``.
    3. ``just lint`` returns non-zero -> VERIFICATION_FAILED.
    4. Runner injects stderr into recovery prompt.
    5. Recovery agent fixes and writes new commit-request.
    6. Runner re-stages, re-runs ``just lint`` -> passes.
    7. Runner commits and publishes.
    """
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    write_commit_request(worktree_path, "agent: initial attempt")

    class _LintRecoveryRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self._sha_calls = 0
            self._lint_calls = 0
            self._committed = False

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            label=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                result = self.responses[command_tuple]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                return result
            if command_tuple[:1] == ("codex",):
                prompt = command_tuple[-1]
                if "Recovery attempt: 1/2" in prompt:
                    assert (
                        "Verification after runner staged changes with git add -A failed" in prompt
                    )
                    assert "lint stdout" in prompt
                    assert "lint stderr" in prompt
                    write_commit_request(worktree_path, "agent: fix lint")
                return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "rev-parse", "HEAD"):
                self._sha_calls += 1
                sha = "after-sha" if self._sha_calls > 1 else "before-sha"
                return CommandResult(command_tuple, 0, f"{sha}\n", "")
            if command_tuple == ("git", "branch", "--show-current"):
                return CommandResult(command_tuple, 0, "issue-123\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                stdout = "" if self._committed else " M file.txt\n"
                return CommandResult(command_tuple, 0, stdout, "")
            if command_tuple == ("just", "lint") or is_bash_wrapped_verification_call(
                command, ("just", "lint")
            ):
                self._lint_calls += 1
                if self._lint_calls == 2:
                    return CommandResult(
                        command_tuple,
                        1,
                        "lint stdout\n",
                        "lint stderr\n",
                    )
                return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "commit", "-m", "agent: fix lint"):
                self._committed = True
                return CommandResult(command_tuple, 0, "", "")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _LintRecoveryRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(worktree_path, "just lint")

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    exit_code = run_once(
        repo_path=Path("."),
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=fake_client,
        process_runner=fake_runner,
    )

    assert exit_code == 0
    commands = [tuple(command) for command in fake_runner.calls]
    add_indices = [
        index for index, command in enumerate(commands) if command == ("git", "add", "-A")
    ]
    lint_indices = [
        index
        for index, command in enumerate(commands)
        if is_bash_wrapped_verification_call(command, ("just", "lint"))
    ]
    reset_index = commands.index(("git", "reset", "--mixed"))
    recovery_prompt = [command[-1] for command in commands if command[:1] == ("codex",)][2]

    # Two staging rounds (initial + recovery)
    assert len(add_indices) == 2
    # With the Fix Agent layer there is one extra verification run after the
    # Fix Agent attempt before falling back to the full recovery agent.
    assert len(lint_indices) == 5
    assert add_indices[0] < lint_indices[1] < reset_index
    assert reset_index < add_indices[1] < lint_indices[4]
    assert "Verification after runner staged changes with git add -A failed" in recovery_prompt
    assert "lint stdout" in recovery_prompt
    assert "lint stderr" in recovery_prompt
    assert ("git", "commit", "-m", "agent: fix lint") in commands

    # Verify attempt history records the failed attempt then success.
    comment_calls = [c for c in fake_client.calls if c["method"] == "comment_issue"]
    implementation_comment = [
        c for c in comment_calls if "Implementation Complete" in c.get("body", "")
    ]
    assert len(implementation_comment) == 1
    body = implementation_comment[0]["body"]
    assert "Attempt History" in body
    assert "verification_failed" in body
    assert "success" in body


def test_scenario_e_lint_exhausted_max_retries(tmp_path: Path) -> None:
    """Scene E: staged verification fails on all 3 attempts, MaxRetriesExceededError.

    Steps:
    1. Attempt 0: Agent writes commit-request, runner stages, ``just lint`` fails.
    2. Attempt 1 (recovery): Agent fixes, runner re-stages, ``just lint`` still fails.
    3. Attempt 2 (recovery): Agent fixes again, runner re-stages, ``just lint`` still fails.
    4. All attempts exhausted → runner marks issue as failed.
    5. Issue comment contains Attempt History with 3 rows of ``verification_failed``.
    """
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()

    class _LintExhaustedRunner(FakeProcessRunner):
        def __init__(self) -> None:
            super().__init__()
            self._sha_calls = 0
            self._agent_calls = 0

        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            label=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                result = self.responses[command_tuple]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                return result
            if command_tuple[:1] == ("codex",):
                self._agent_calls += 1
                write_commit_request(worktree_path, f"agent: attempt {self._agent_calls}")
                return CommandResult(command_tuple, 0, "", "")
            if command_tuple == ("git", "rev-parse", "HEAD"):
                self._sha_calls += 1
                sha = "after-sha" if self._sha_calls > 1 else "before-sha"
                return CommandResult(command_tuple, 0, f"{sha}\n", "")
            if command_tuple == ("git", "branch", "--show-current"):
                return CommandResult(command_tuple, 0, "issue-123\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                return CommandResult(command_tuple, 0, " M file.txt\n", "")
            if command_tuple == ("git", "status", "--porcelain", "-z"):
                return CommandResult(command_tuple, 0, " M file.txt\0", "")
            if command_tuple == ("just", "lint") or is_bash_wrapped_verification_call(
                command, ("just", "lint")
            ):
                return CommandResult(command_tuple, 1, "lint stdout\n", "lint stderr\n")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _LintExhaustedRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(worktree_path, "just lint")

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    exit_code = run_once(
        repo_path=Path("."),
        config=config,
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=fake_client,
        process_runner=fake_runner,
    )

    assert exit_code == 1
    commands = [tuple(command) for command in fake_runner.calls]
    lint_indices = [
        index
        for index, command in enumerate(commands)
        if is_bash_wrapped_verification_call(command, ("just", "lint"))
    ]
    add_indices = [
        index
        for index, command in enumerate(commands)
        if command == ("git", "add", "--", "file.txt")
    ]
    reset_indices = [
        index for index, command in enumerate(commands) if command == ("git", "reset", "--mixed")
    ]

    # just lint 在 3 次尝试的预 staging 验证都失败，正常流程从不进入 commit proxy。
    # 重试耗尽后，runner 把在途改动 checkpoint 成一个 WIP commit（只 stage 非禁改
    # 路径 git add -- file.txt + git commit --no-verify），供下次 claim 续作。
    assert len(lint_indices) == 3
    assert len(add_indices) == 1
    assert len(reset_indices) == 0
    checkpoint_commits = [
        command
        for command in commands
        if command[:2] == ("git", "commit") and "--no-verify" in command
    ]
    assert len(checkpoint_commits) == 1

    failed_calls = [
        c
        for c in fake_client.calls
        if c["method"] == "edit_issue_labels" and config.labels.failed in c.get("add", [])
    ]
    assert len(failed_calls) == 1
    comment_calls = [c for c in fake_client.calls if c["method"] == "comment_issue"]
    failure_comment = comment_calls[-1]
    assert "Attempt History" in failure_comment["body"]
    assert "Failed after 3 attempts" in failure_comment["body"]
    assert "verification_failed" in failure_comment["body"]
    assert "| 1 |" in failure_comment["body"]
    assert "| 2 |" in failure_comment["body"]
    assert "| 3 |" in failure_comment["body"]


def test_keyboard_interrupt_checkpoints_in_flight_work_before_exit(
    tmp_path: Path,
) -> None:
    """Ctrl-C (KeyboardInterrupt) during a run checkpoints the safe in-flight
    work, then propagates so the interrupt still exits the process."""
    fake_client = FakeGitHubClient()
    issue = make_ready_issue()
    fake_client.list_ready_issues = lambda ready_label, limit: [issue]
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()

    class _InterruptingRunner(FakeProcessRunner):
        def run(
            self,
            command,
            *,
            cwd,
            check=True,
            timeout=None,
            inactivity_timeout=None,
            capture_output=True,
            input_text=None,
            label=None,
            output_sink=None,
            output_protocol=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple in self.responses:
                result = self.responses[command_tuple]
                if check and result.return_code != 0:
                    raise RuntimeError(f"Command failed: {command}")
                return result
            if command_tuple[:1] in {("codex",), ("claude",), ("kimi",)}:
                # The operator hits Ctrl-C while the agent is running.
                raise KeyboardInterrupt()
            if command_tuple == ("git", "rev-parse", "HEAD"):
                return CommandResult(command_tuple, 0, "before-sha\n", "")
            if command_tuple == ("git", "branch", "--show-current"):
                return CommandResult(command_tuple, 0, "issue-123\n", "")
            if command_tuple == ("git", "status", "--porcelain"):
                return CommandResult(command_tuple, 0, " M src/wip.py\n", "")
            if command_tuple == ("git", "status", "--porcelain", "-z"):
                return CommandResult(command_tuple, 0, " M src/wip.py\0", "")
            return CommandResult(command_tuple, 0, "", "")

    fake_runner = _InterruptingRunner()
    path_command, path_result = worktree_path_response(worktree_path)
    fake_runner.responses = {
        path_command: path_result,
        git_remote_command(): git_remote_result("origin"),
    }
    config = config_with_review_disabled(worktree_path, "just lint")

    from backend.core.use_cases.agent_runner_orchestrate import run_once

    with pytest.raises(KeyboardInterrupt):
        run_once(
            repo_path=Path("."),
            config=config,
            dry_run=False,
            agent="auto",
            max_issues=1,
            github_client=fake_client,
            process_runner=fake_runner,
        )

    commands = [tuple(command) for command in fake_runner.calls]
    # The safe in-flight file was checkpointed before the interrupt propagated.
    assert ("git", "add", "--", "src/wip.py") in commands
    checkpoint_commits = [
        command
        for command in commands
        if command[:2] == ("git", "commit") and "--no-verify" in command
    ]
    assert len(checkpoint_commits) == 1


# ── rv-1 / rv-3：recovery 耗尽后的跨 claim 交接与失败 Draft PR ──────────────────
#
# 被测的是 ``agent_runner_issue_handlers`` 耗尽分支新增的三步：写交接记录、有安全
# commit 时发布同源的 Draft PR、把回链固定附到 PR 正文尾部；以及另两类异常
# （ProviderCapacityError / KeyboardInterrupt）必须零副作用。

_VERDICT_MARKER_RED = "<!-- iar:verifier-verdict risk=red -->"

_VERIFIER_RED_DETAIL = "\n".join(
    [
        "Realistic Validation evidence check failed.",
        "Independent verifier (agent 'codex') returned RED for issue #123: it could "
        "not independently confirm the change does what the issue asks. Findings:",
        f"rv-3 not satisfied: the PRD detail panel stays blank. {_VERDICT_MARKER_RED}",
    ]
)

_VERIFIER_NO_VERDICT_DETAIL = "\n".join(
    [
        "Realistic Validation evidence check failed.",
        "Independent verifier (agent 'codex') produced NO verdict marker for issue "
        "#123, so the fail-safe protocol blocks it like RED. This is a verifier-side "
        "protocol failure, NOT a proven defect in the change. Raw output head:",
        "I could not run the real entry points: the review sandbox had no credentials.",
    ]
)


def _handoff_attempt_results(detail_text: str) -> list[AttemptResult]:
    """造一轮"verifier 门禁判红后耗尽"的 attempt 历史。

    生产路径上 verifier 的判定文本由 ``_classify_and_record_gate_failure`` 写进
    ``AttemptResult.detail`` 并随 ``MaxRetriesExceededError.attempt_results`` 上抛,
    这里按同一形状构造,使交接记录读到的是真实的判定来源。
    """
    return [
        AttemptResult(
            attempt_number=attempt_number,
            failure_type=FailureType.VERIFICATION_FAILED,
            recovered=False,
            detail=detail_text,
            agent="codex",
            started_at="2026-09-22T00:00:00+00:00",
            duration_seconds=10.0,
        )
        for attempt_number in (1, 2, 3)
    ]


def _prepare_exhaustion_handler(
    monkeypatch: pytest.MonkeyPatch,
    worktree_path: Path,
    *,
    failure_exc: BaseException,
    checkpoint_sha: str | None,
) -> dict[str, object]:
    """把 ``_process_ready_issue`` 打到"agent 耗尽 → checkpoint → 交接"这一步。

    沿用仓库既有的补丁中枢：延迟导入的名字打在 ``run_agent_once`` 上,模块级名字打在
    ``agent_runner_issue_handlers`` 上。``publish_changes`` 打在 handlers 上,使断言
    能读到它实际收到的 ``require_prd_archived`` 取值。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases import run_agent_once

    monkeypatch.setattr(handlers, "_reuse_existing_local_commit", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "create_or_reuse_worktree", lambda *a, **k: worktree_path)
    monkeypatch.setattr(handlers, "get_head_sha", lambda *a, **k: "before-sha")
    monkeypatch.setattr(handlers, "get_current_branch", lambda *a, **k: "issue-123")

    def _raise_exhausted(**_kwargs: object) -> None:
        raise failure_exc

    monkeypatch.setattr(run_agent_once, "run_agent_until_committed", _raise_exhausted)

    def _fake_checkpoint(*_args: object, **_kwargs: object) -> str | None:
        return checkpoint_sha

    monkeypatch.setattr(run_agent_once, "checkpoint_uncommitted_progress", _fake_checkpoint)

    publish_calls: list[dict[str, object]] = []

    def _fake_publish_changes(
        _issue: object,
        _worktree: object,
        _config: object,
        _github_client: object,
        _process_runner: object,
        **kwargs: object,
    ) -> tuple[str, str]:
        publish_calls.append(dict(kwargs))
        return "issue-123", "https://github.com/example/repo/pull/14"

    monkeypatch.setattr(handlers, "publish_changes", _fake_publish_changes)
    return {"publish_calls": publish_calls}


def _handoff_comments(fake_client: FakeGitHubClient) -> list[str]:
    """取出 Issue 上带 ``iar:failure-context`` marker 的评论正文。"""
    return [
        call["body"]
        for call in fake_client.calls
        if call["method"] == "comment_issue"
        and isinstance(call.get("body"), str)
        and "iar:failure-context" in call["body"]
    ]


@pytest.mark.parametrize(
    ("detail_text", "expected_verifier_state"),
    [
        (_VERIFIER_RED_DETAIL, "red"),
        (_VERIFIER_NO_VERDICT_DETAIL, "no-verdict"),
    ],
)
def test_exhaustion_writes_failure_context_handoff_and_draft_pr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    detail_text: str,
    expected_verifier_state: str,
) -> None:
    """rv-1 ①②：verifier 判红与未形成结论两种耗尽都写出交接记录并发布 Draft PR。

    两种场景共用同一条链路,差别只在措辞：判红转述 verifier 的发现,未形成结论如实
    说"没有形成结论";任何场景都不得声称通过,且 PR 不带 ``validation/verifier-passed``。
    """
    from backend.core.shared.models.agent_runner import PullRequestContext
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    fake_client.set_pr_context(
        "issue-123",
        PullRequestContext(
            pr_url="https://github.com/example/repo/pull/14",
            branch="issue-123",
            head_sha="wip-sha",
            base_sha="base-sha",
            number=14,
            body="Closes #123\n\nGenerated by issue-agent-runner.\n",
        ),
    )
    captured = _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=MaxRetriesExceededError(_handoff_attempt_results(detail_text)),
        checkpoint_sha="a1b2c3d4",
    )

    with pytest.raises(MaxRetriesExceededError):
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    handoff_bodies = _handoff_comments(fake_client)
    assert len(handoff_bodies) == 1
    handoff_body = handoff_bodies[0]
    # marker 可被下一轮确定性定位,且带 checkpoint SHA / attempt 数 / 证据目录。
    assert (
        f"<!-- iar:failure-context checkpoint=a1b2c3d4 attempts=3 "
        f"verifier={expected_verifier_state}"
    ) in handoff_body
    assert "evidence=" in handoff_body
    assert "WIP checkpoint" in handoff_body
    assert "`validation/verifier-passed` is **absent**" in handoff_body
    # 快照性质必须说清,否则 reviewer 会把中途快照读成完成品。
    assert "not** a finished implementation" in handoff_body
    # attempt 历史复用既有渲染,不另起一套表。
    assert "### Attempt History" in handoff_body
    # 绝不冒充通过。
    assert "verifier passed" not in handoff_body.lower()
    assert "acceptance passed" not in handoff_body.lower()

    if expected_verifier_state == "red":
        assert "rv-3 not satisfied" in handoff_body
        assert "formed an explicit **red** verdict" in handoff_body
    else:
        assert "**no verdict was formed**" in handoff_body
        assert "formed an explicit **red** verdict" not in handoff_body

    # 发布走既有原语,且本 PRD 唯一放宽项只在这里显式传入。
    publish_calls = captured["publish_calls"]
    assert isinstance(publish_calls, list) and len(publish_calls) == 1
    assert publish_calls[0]["require_prd_archived"] is False
    assert publish_calls[0]["expected_branch"] == "issue-123"

    # PR 正文尾部固定附上指向交接评论的回链(评论 ID 由 fake 自增分配)。
    body_updates = [
        call for call in fake_client.calls if call["method"] == "update_pull_request_body"
    ]
    assert len(body_updates) == 1
    assert body_updates[0]["pr_number"] == 14
    updated_body = body_updates[0]["body"]
    assert "#issuecomment-" in updated_body
    assert "Closes #123" in updated_body
    assert "validation/verifier-passed" in updated_body


def test_exhaustion_without_safe_commit_writes_handoff_but_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """rv-3：checkpoint 返回 None（空工作树 / 分支不符 / 全为禁改路径）时不发布 PR。

    "无 commit 也要交班"是必须覆盖的一格：交接记录仍然写出,并说明本轮没有可发布的
    快照,供下一轮接手。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    captured = _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=MaxRetriesExceededError(_handoff_attempt_results(_VERIFIER_RED_DETAIL)),
        checkpoint_sha=None,
    )

    with pytest.raises(MaxRetriesExceededError):
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    handoff_bodies = _handoff_comments(fake_client)
    assert len(handoff_bodies) == 1
    assert "checkpoint=none" in handoff_bodies[0]
    assert "Checkpoint commit: none" in handoff_bodies[0]
    assert captured["publish_calls"] == []
    assert [c for c in fake_client.calls if c["method"] == "create_draft_pr"] == []
    assert [c for c in fake_client.calls if c["method"] == "update_pull_request_body"] == []


def test_handoff_publish_failure_does_not_mask_original_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-11：发布/回链写挂只记日志,原始 MaxRetriesExceededError 必须照常上抛。

    否则"验证没过"会被改写成"报告写挂了",反而更难排查。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=MaxRetriesExceededError(_handoff_attempt_results(_VERIFIER_RED_DETAIL)),
        checkpoint_sha="a1b2c3d4",
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gh push rejected: remote is read-only")

    monkeypatch.setattr(handlers, "publish_changes", _boom)

    with pytest.raises(MaxRetriesExceededError) as exc_info:
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    assert "Failed after 3 attempts" in str(exc_info.value)
    # 交接记录仍然写出——它是唯一事实源,不因发布失败而丢失。
    assert len(_handoff_comments(fake_client)) == 1


@pytest.mark.parametrize(
    ("exception_factory", "exception_type"),
    [
        (
            lambda: ProviderCapacityError("provider at capacity", []),
            ProviderCapacityError,
        ),
        (lambda: KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_capacity_and_interrupt_exhaustion_branch_have_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception_factory: object,
    exception_type: type,
) -> None:
    """rv-1 场景③：限流与用户中断走同一个 except 分支,但不得触发交接与发布。

    KeyboardInterrupt 尤其关键：它没有 ``attempt_results``,若交接挂在整个 except
    元组上,一次 Ctrl-C 会抛 AttributeError 把用户的中断变成一条假故障。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    captured = _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=exception_factory(),  # type: ignore[operator]
        checkpoint_sha="a1b2c3d4",
    )

    with pytest.raises(exception_type):
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    assert _handoff_comments(fake_client) == []
    assert captured["publish_calls"] == []
    # 步骤 1 的 claim 评论是既有行为,必须照常存在;本功能的三个出口都得是空的。
    issue_comments = [c["body"] for c in fake_client.calls if c["method"] == "comment_issue"]
    assert any("Agent Runner Claimed" in str(body) for body in issue_comments)
    assert not any("Recovery Budget Exhausted" in str(body) for body in issue_comments)
    assert [c for c in fake_client.calls if c["method"] == "create_draft_pr"] == []
    assert [c for c in fake_client.calls if c["method"] == "update_pull_request_body"] == []


def test_handoff_render_failure_still_preserves_original_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-11：连**渲染**都写挂时，原始 MaxRetriesExceededError 仍然必须照常上抛。

    只把评论/发布/回链三步包进 try 是不够的：``attempt_results`` 取值、证据路径解析与
    ``format_failure_context_comment`` 内部的 verdict 解析都可能抛。漏掉这一层会让
    "验证没过"变成"报告写挂了"，并且因为抛的是别的类型，agent fallback 阶梯也接不住。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    captured = _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=MaxRetriesExceededError(_handoff_attempt_results(_VERIFIER_RED_DETAIL)),
        checkpoint_sha="a1b2c3d4",
    )

    def _explode(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("renderer blew up on an unexpected attempt shape")

    monkeypatch.setattr(handlers, "format_failure_context_comment", _explode)

    with pytest.raises(MaxRetriesExceededError) as exc_info:
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    assert "Failed after 3 attempts" in str(exc_info.value)
    # 上抛的仍是耗尽本身而不是渲染器的 RuntimeError（两者同为 RuntimeError 子类，所以
    # 要比具体类型而非 isinstance），且没有任何下游副作用被半路触发。
    assert type(exc_info.value) is MaxRetriesExceededError
    assert captured["publish_calls"] == []
    assert _handoff_comments(fake_client) == []


def test_handoff_recovers_verifier_red_recorded_on_an_earlier_attempt(
    tmp_path: Path,
) -> None:
    """verifier 真判红之后，若末轮被更早的一道门禁拦住，交接仍要报 verifier=red。

    只看 ``attempt_results[-1]`` 会把"上一轮明确判红"误写成"没有形成结论"，把接手方
    支去查一个并不存在的问题——这正是本 PRD 要消除的那类误导。
    """
    from backend.core.use_cases.agent_runner_failure import format_failure_context_comment

    red_attempt = AttemptResult(
        attempt_number=1,
        failure_type=FailureType.VERIFICATION_FAILED,
        recovered=False,
        detail=_VERIFIER_RED_DETAIL,
        agent="codex",
    )
    later_non_verifier_gate = AttemptResult(
        attempt_number=2,
        failure_type=FailureType.VERIFICATION_FAILED,
        recovered=False,
        detail="Realistic Validation evidence check failed.\nmissing rv-2-evidence.txt",
        agent="codex",
    )

    body = format_failure_context_comment(
        RuntimeError("Failed after 2 attempts."),
        [red_attempt, later_non_verifier_gate],
        issue_number=128,
        checkpoint_sha="a1b2c3d4",
        evidence_dir="tasks/evidence/issue-128",
    )

    assert "verifier=red" in body.splitlines()[0]
    assert "formed an explicit **red** verdict" in body
    assert "**no verdict was formed**" not in body
    # 措辞要把"判定来自哪一轮"说清，不让人以为末轮就是 verifier。
    assert "attempt 1" in body and "attempt 2" in body


def test_handoff_lists_deliverables_named_by_the_gate_reports(tmp_path: Path) -> None:
    """FR-2 的"缺失呈递物"要有独立字段：原样摘出门禁点名的 rv 条目，不判断其真伪。"""
    from backend.core.use_cases.agent_runner_failure import format_failure_context_comment

    attempt = AttemptResult(
        attempt_number=1,
        failure_type=FailureType.VERIFICATION_FAILED,
        recovered=False,
        detail="Realistic Validation evidence check failed.\nmissing rv-3-prd-render.png",
        agent="codex",
    )

    body = format_failure_context_comment(
        RuntimeError("Failed after 1 attempts."),
        [attempt],
        issue_number=128,
        checkpoint_sha=None,
        evidence_dir="tasks/evidence/issue-128",
    )

    assert "`rv-3-prd-render.png`" in body
    assert "does not judge whether each one is actually missing" in body


def test_exhaustion_never_grants_the_verifier_passed_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-9：失败交接路径不得给 Issue 或 PR 加 ``validation/verifier-passed``。

    只断言 PR 正文里提了这句还不够——判据是标签与当前 tree 的证据，所以要看**标签调用**
    上有没有真的出现它。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    worktree_path.mkdir()
    fake_client = FakeGitHubClient()
    fake_client.set_pr_context(
        "issue-123",
        PullRequestContext(
            pr_url="https://github.com/example/repo/pull/14",
            branch="issue-123",
            head_sha="a1b2c3d4",
            base_sha="base-sha",
            number=14,
            body="Closes #123\n",
        ),
    )
    _prepare_exhaustion_handler(
        monkeypatch,
        worktree_path,
        failure_exc=MaxRetriesExceededError(_handoff_attempt_results(_VERIFIER_RED_DETAIL)),
        checkpoint_sha="a1b2c3d4",
    )

    with pytest.raises(MaxRetriesExceededError):
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config_with_review_disabled(worktree_path),
            agent="auto",
            github_client=fake_client,
            process_runner=FakeProcessRunner(),
        )

    added_labels = [
        label
        for call in fake_client.calls
        if call["method"] == "edit_issue_labels"
        for label in call.get("add", [])
    ]
    assert "validation/verifier-passed" not in added_labels
    assert all("verifier-passed" not in label for label in added_labels)


def test_exhaustion_with_only_forbidden_changes_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """rv-3 的"只含 forbidden paths"一格要走**真实**安全筛选，而不是 fake 返回 None。

    PRD 的 mock_boundary 要求安全筛选为真：只有让真实的
    ``checkpoint_uncommitted_progress`` 在一个只改了禁改路径的 git 仓上自己判定，
    才能抓到"禁改内容被误当安全快照推出去"这类缺陷。
    """
    from backend.core.use_cases import agent_runner_issue_handlers as handlers
    from backend.core.use_cases import run_agent_once
    from backend.core.use_cases.run_agent_once import MaxRetriesExceededError
    from backend.infrastructure.process_runner import SubprocessRunner
    from tests.support.agent_runner import init_git_repo, run_git

    issue = make_prd_issue("tasks/pending/example.md")
    worktree_path = tmp_path / "issue-123"
    init_git_repo(worktree_path)
    run_git(worktree_path, "checkout", "-b", "issue-123")
    (worktree_path / ".env").write_text("SECRET=leaked\n", encoding="utf-8")

    monkeypatch.setattr(handlers, "_reuse_existing_local_commit", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "create_or_reuse_worktree", lambda *a, **k: worktree_path)
    monkeypatch.setattr(handlers, "get_head_sha", lambda *a, **k: "before-sha")
    monkeypatch.setattr(handlers, "get_current_branch", lambda *a, **k: "issue-123")

    def _raise_exhausted(**_kwargs: object) -> None:
        raise MaxRetriesExceededError(_handoff_attempt_results(_VERIFIER_RED_DETAIL))

    monkeypatch.setattr(run_agent_once, "run_agent_until_committed", _raise_exhausted)

    publish_calls: list[dict[str, object]] = []

    def _fake_publish_changes(*_args: object, **kwargs: object) -> tuple[str, str]:
        publish_calls.append(dict(kwargs))
        return "issue-123", "https://github.com/example/repo/pull/14"

    monkeypatch.setattr(handlers, "publish_changes", _fake_publish_changes)

    fake_client = FakeGitHubClient()
    real_runner = SubprocessRunner()
    config = config_with_review_disabled(worktree_path)

    with pytest.raises(MaxRetriesExceededError):
        handlers._process_ready_issue(
            issue=issue,
            repo_path=Path("."),
            config=config,
            agent="auto",
            github_client=fake_client,
            process_runner=real_runner,
        )

    # 真实安全筛选必须把禁改内容留在工作区、不入任何历史提交。
    committed_paths_across_all_refs = run_git(
        worktree_path, "log", "--all", "--name-only", "--format="
    ).split()
    assert ".env" not in committed_paths_across_all_refs
    assert "?? .env" in run_git(worktree_path, "status", "--porcelain")

    handoff_bodies = _handoff_comments(fake_client)
    assert len(handoff_bodies) == 1
    assert "checkpoint=none" in handoff_bodies[0]
    assert publish_calls == []
    assert [c for c in fake_client.calls if c["method"] == "create_draft_pr"] == []
