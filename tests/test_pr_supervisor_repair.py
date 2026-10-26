"""测试 Post-PR 修复分支的重试、耗尽与索引清理收尾。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

import backend.core.use_cases.agent_runner_git as agent_runner_git
import backend.core.use_cases.pr_supervisor_repair as pr_supervisor_repair
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    PostPrSupervisorConfig,
)
from backend.core.use_cases.agent_runner_commit import EmptyCommitRequestError
from backend.core.use_cases.agent_runner_feedback import VerificationFailedError
from tests.conftest import FakeProcessRunner

_PUSH_COMMAND = ("git", "push", "origin", "issue-317")
_RESET_COMMAND = ("git", "reset", "--mixed")
_COMMIT_REQUEST_RELATIVE_PATH = Path(".agent-runner") / "commit-request.json"

# 每轮的提交分支：写出提交请求但零改动 / 提交时验证失败 / 提交成功 / 没写提交请求。
RepairCommitBranch = Literal["empty", "failed_commit", "committed", "no_request"]


@dataclass
class _RepairRecord:
    """替身记录下来的每轮提示词、提交代理异常与被验证的 worktree。"""

    prompts: list[str] = field(default_factory=list)
    commit_errors: list[Exception] = field(default_factory=list)
    verified_worktrees: list[Path] = field(default_factory=list)


def _issue() -> IssueSummary:
    return IssueSummary(
        number=317,
        title="Nightly CI patrol",
        url="https://github.com/example/repo/issues/317",
        body="",
        labels=(),
    )


def _passing_result(stdout: str = "passed") -> CommandResult:
    return CommandResult(command=("just", "test"), return_code=0, stdout=stdout, stderr="")


def _failing_result(stdout: str = "failing check output") -> CommandResult:
    return CommandResult(command=("just", "test"), return_code=1, stdout=stdout, stderr="")


def _install_repair_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    attempts: list[tuple[RepairCommitBranch, list[CommandResult]]],
) -> _RepairRecord:
    """把 execute_repair 的外部协作替换成按脚本逐轮驱动的替身。

    Args:
        monkeypatch: pytest 打桩入口。
        attempts: 每轮的 ``(提交分支, 该轮验证结果)``；轮数超出脚本时复用最后一项，
            方便只写需要差异化的那一轮。

    Returns:
        记录每轮修复提示词、提交代理抛出的异常与验证 worktree 的 :class:`_RepairRecord`。
    """
    record = _RepairRecord()

    def current_attempt() -> tuple[RepairCommitBranch, list[CommandResult]]:
        index = min(len(record.prompts) - 1, len(attempts) - 1)
        return attempts[index]

    def report_expected_head(
        _repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
    ) -> str:
        return "head-317"

    def report_expected_branch(
        _repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
    ) -> str:
        return "issue-317"

    def write_repair_request(
        _repair_agent: str,
        repair_prompt: str,
        repair_worktree_path: Path,
        _process_runner: FakeProcessRunner,
        **_kwargs: object,
    ) -> CommandResult:
        record.prompts.append(repair_prompt)
        branch, _results = current_attempt()
        if branch != "no_request":
            request_path = repair_worktree_path / _COMMIT_REQUEST_RELATIVE_PATH
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text('{"commit_message": "repair Issue #317"}', encoding="utf-8")
        return CommandResult(command=("noop",), return_code=0, stdout="", stderr="")

    def report_commit_request(
        _issue: IssueSummary,
        repair_worktree_path: Path,
        _config: AppConfig,
        _process_runner: FakeProcessRunner,
        *,
        expected_branch: str,
    ) -> list[CommandResult]:
        assert expected_branch == "issue-317"
        branch, verification_results = current_attempt()
        (repair_worktree_path / _COMMIT_REQUEST_RELATIVE_PATH).unlink()
        if branch == "empty":
            # 元数据修复只改 GitHub 上的 PR，不产生本地提交。
            error: Exception = EmptyCommitRequestError()
        elif branch == "failed_commit":
            error = VerificationFailedError(verification_results)
        else:
            return verification_results
        record.commit_errors.append(error)
        raise error

    def record_verification(
        repair_worktree_path: Path,
        _config: AppConfig,
        _process_runner: FakeProcessRunner,
    ) -> list[CommandResult]:
        branch, verification_results = current_attempt()
        assert branch in {"empty", "no_request"}
        record.verified_worktrees.append(repair_worktree_path)
        return verification_results

    monkeypatch.setattr(agent_runner_git, "get_head_sha", report_expected_head)
    monkeypatch.setattr(agent_runner_git, "get_current_branch", report_expected_branch)
    monkeypatch.setattr(pr_supervisor_repair, "run_agent_with_prompt", write_repair_request)
    monkeypatch.setattr(pr_supervisor_repair, "commit_requested_changes", report_commit_request)
    monkeypatch.setattr(pr_supervisor_repair, "run_verification", record_verification)
    monkeypatch.setattr(pr_supervisor_repair, "has_changes", lambda _path, _runner: False)
    return record


def _execute_repair(
    *,
    worktree_path: Path,
    process_runner: FakeProcessRunner,
    max_repair_attempts: int,
) -> list[CommandResult]:
    return pr_supervisor_repair.execute_repair(
        issue=_issue(),
        worktree_path=worktree_path,
        config=AppConfig(
            post_pr_supervisor=PostPrSupervisorConfig(max_repair_attempts=max_repair_attempts)
        ),
        process_runner=process_runner,
        pr_branch="issue-317",
        expected_head="head-317",
        repair_agent="codex",
    )


def _commands(process_runner: FakeProcessRunner) -> list[tuple[str, ...]]:
    return [tuple(call) for call in process_runner.calls]


def _repair_worktree(tmp_path: Path) -> Path:
    worktree_path = tmp_path / "issue-317"
    worktree_path.mkdir()
    return worktree_path


def test_execute_repair_verifies_and_returns_after_empty_commit_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空提交请求仍验证当前 tree 并回到监督循环，不把 Issue 判为执行失败。"""
    worktree_path = _repair_worktree(tmp_path)
    passing_result = _passing_result()
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("empty", [passing_result])],
    )

    verification_results = _execute_repair(
        worktree_path=worktree_path,
        process_runner=fake_runner,
        max_repair_attempts=2,
    )

    assert verification_results == [passing_result]
    assert record.verified_worktrees == [worktree_path]
    # 验证通过的干净 tree 既不需要 unstage，也不需要多跑一轮修复者。
    assert _commands(fake_runner) == [_PUSH_COMMAND]


def test_execute_repair_retries_with_recovery_prompt_after_empty_commit_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空提交 + 验证失败：unstage 索引、把失败摘要回喂下一轮，通过后才 push。"""
    worktree_path = _repair_worktree(tmp_path)
    failing_result = _failing_result("blocked: expected exactly one commit")
    passing_result = _passing_result()
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("empty", [failing_result]), ("empty", [passing_result])],
    )

    verification_results = _execute_repair(
        worktree_path=worktree_path,
        process_runner=fake_runner,
        max_repair_attempts=2,
    )

    assert verification_results == [passing_result]
    assert len(record.prompts) == 2
    assert "Recovery attempt" not in record.prompts[0]
    assert "Recovery attempt: 1/2" in record.prompts[1]
    assert "Verification failed before repair commit." in record.prompts[1]
    assert "blocked: expected exactly one commit" in record.prompts[1]
    # 只有失败的那一轮清理索引，且清理发生在恢复重试之前、push 之前。
    assert _commands(fake_runner) == [_RESET_COMMAND, _PUSH_COMMAND]


def test_execute_repair_exhausts_empty_commit_verification_failure_without_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用尽尝试时抛出本轮捕获的验证结果，绝不 push 失败 tree。"""
    worktree_path = _repair_worktree(tmp_path)
    failing_result = _failing_result("stale evidence manifest")
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("empty", [failing_result])],
    )

    with pytest.raises(VerificationFailedError) as excinfo:
        _execute_repair(
            worktree_path=worktree_path,
            process_runner=fake_runner,
            max_repair_attempts=1,
        )

    assert excinfo.value.verification_results == [failing_result]
    assert len(record.prompts) == 1
    # 最后一轮不再清理索引、不再补提示词，也不 push。
    commands = _commands(fake_runner)
    assert _RESET_COMMAND not in commands
    assert _PUSH_COMMAND not in commands


def test_execute_repair_retries_with_captured_results_after_commit_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交代理里捕获的验证失败走同一重试路径：捕获结果进恢复提示词，第二轮成功才 push。"""
    worktree_path = _repair_worktree(tmp_path)
    failing_result = _failing_result("assert in repaired coverage test")
    passing_result = _passing_result()
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("failed_commit", [failing_result]), ("committed", [passing_result])],
    )

    verification_results = _execute_repair(
        worktree_path=worktree_path,
        process_runner=fake_runner,
        max_repair_attempts=2,
    )

    assert verification_results == [passing_result]
    caught_error = record.commit_errors[-1]
    assert isinstance(caught_error, VerificationFailedError)
    assert caught_error.verification_results == [failing_result]
    assert len(record.prompts) == 2
    assert "Recovery attempt: 1/2" in record.prompts[1]
    assert "assert in repaired coverage test" in record.prompts[1]
    assert _commands(fake_runner) == [_RESET_COMMAND, _PUSH_COMMAND]


def test_execute_repair_raises_last_captured_results_when_commit_failures_exhaust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交验证失败耗尽尝试时由验证门禁抛出本轮结果，而不是回手重抛捕获的异常。"""
    worktree_path = _repair_worktree(tmp_path)
    first_failure = _failing_result("first cycle failure")
    last_failure = _failing_result("final cycle failure")
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("failed_commit", [first_failure]), ("failed_commit", [last_failure])],
    )

    with pytest.raises(VerificationFailedError) as excinfo:
        _execute_repair(
            worktree_path=worktree_path,
            process_runner=fake_runner,
            max_repair_attempts=2,
        )

    raised_error = excinfo.value
    assert raised_error.verification_results == [last_failure]
    assert "final cycle failure" in str(raised_error)
    assert "first cycle failure" not in str(raised_error)
    # 抛的是共享失败块经 ensure_verification_passed 新建的错误，不是提交代理那一条。
    assert raised_error is not record.commit_errors[-1]
    assert len(record.prompts) == 2
    commands = _commands(fake_runner)
    assert commands.count(_RESET_COMMAND) == 1
    assert _PUSH_COMMAND not in commands


def test_execute_repair_resets_index_and_retries_without_commit_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没写提交请求的轮次验证失败也共用同一收尾：unstage 索引 + 恢复提示词，通过才 push。"""
    worktree_path = _repair_worktree(tmp_path)
    failing_result = _failing_result("PRD archive still pending")
    passing_result = _passing_result()
    fake_runner = FakeProcessRunner()
    record = _install_repair_doubles(
        monkeypatch,
        attempts=[("no_request", [failing_result]), ("no_request", [passing_result])],
    )

    verification_results = _execute_repair(
        worktree_path=worktree_path,
        process_runner=fake_runner,
        max_repair_attempts=2,
    )

    assert verification_results == [passing_result]
    assert record.commit_errors == []
    assert "Recovery attempt: 1/2" in record.prompts[1]
    assert "PRD archive still pending" in record.prompts[1]
    assert _commands(fake_runner) == [_RESET_COMMAND, _PUSH_COMMAND]


def test_execute_repair_keeps_recovery_loop_when_index_reset_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """索引重置是 best-effort：git 抖动失败也不能炸掉剩下的恢复轮次。"""
    worktree_path = _repair_worktree(tmp_path)
    failing_result = _failing_result("verification gate failed")
    passing_result = _passing_result()
    fake_runner = FakeProcessRunner(
        responses={
            _RESET_COMMAND: CommandResult(
                command=_RESET_COMMAND,
                return_code=128,
                stdout="",
                stderr="fatal: Unable to create '.git/index.lock': File exists.",
            )
        }
    )
    _install_repair_doubles(
        monkeypatch,
        attempts=[("empty", [failing_result]), ("empty", [passing_result])],
    )

    verification_results = _execute_repair(
        worktree_path=worktree_path,
        process_runner=fake_runner,
        max_repair_attempts=2,
    )

    assert verification_results == [passing_result]
    assert _commands(fake_runner) == [_RESET_COMMAND, _PUSH_COMMAND]
