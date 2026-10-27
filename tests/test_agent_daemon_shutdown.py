"""Agent daemon 停机中断回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    PrePrReviewConfig,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.support.agent_runner import make_ready_issue


def test_shutdown_during_unparseable_review_keeps_issue_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停机时 review 无结论应保留 running 标签且不发布失败评论。"""
    from backend.core.shared.interfaces.runner_live_view import NoOpRunnerLiveView
    from backend.core.use_cases import agent_runner_orchestration_runtime as runtime
    from backend.core.use_cases.agent_daemon_shutdown import (
        mark_agent_daemon_stopping,
        reset_agent_daemon_stopping,
    )
    from backend.core.use_cases.agent_review import run_pre_pr_review
    from backend.core.use_cases.agent_runner_output_routing import _OutputRoutedProcessRunner

    issue = make_ready_issue()
    running_labels = ("agent/running", "agent/codex")
    fake_client = FakeGitHubClient()
    fake_client.set_issue_labels(issue.number, running_labels)
    config = AppConfig(pre_pr_review=PrePrReviewConfig(enabled=True, max_attempts=1))
    worktree_path = tmp_path / f"issue-{issue.number}"
    worktree_path.mkdir()

    class _ShutdownReviewRunner(FakeProcessRunner):
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
            attempt_key=None,
            cancel_check=None,
        ):
            command_tuple = tuple(command)
            self.calls.append(list(command))
            if command_tuple and command_tuple[0] not in {"git", "gh", "python", "python3"}:
                mark_agent_daemon_stopping()
                return CommandResult(
                    command=command_tuple,
                    return_code=0,
                    stdout="",
                    stderr="",
                )
            return CommandResult(
                command=command_tuple,
                return_code=0,
                stdout="",
                stderr="",
            )

    def _run_review_stage(**kwargs: object) -> None:
        run_pre_pr_review(
            issue=kwargs["issue"],
            worktree_path=worktree_path,
            config=config,
            github_client=fake_client,
            process_runner=kwargs["process_runner"],
            selected_agent="codex",
            head_sha_before="before-sha",
            expected_branch=f"issue-{issue.number}",
            verification_results=[],
        )

    def _run_with_agent_fallback(*, process_for_agent, **kwargs: object) -> str:
        process_for_agent(agent="codex")
        return "codex"

    monkeypatch.setattr(runtime, "_process_running_publish_recovery", _run_review_stage)
    monkeypatch.setattr(runtime, "run_issue_with_agent_fallback", _run_with_agent_fallback)
    routed_runner = _OutputRoutedProcessRunner(_ShutdownReviewRunner(), lambda _chunk: None)
    reset_agent_daemon_stopping()
    try:
        exit_code = runtime._process_single_issue(
            issue,
            "running_publish_recovery",
            repo_path=tmp_path,
            config=config,
            agent="auto",
            github_client=fake_client,
            process_runner=routed_runner,
            content_generator=None,
            run_history_store=None,
            run_trigger="cli_daemon",
            effective_repo_id="test-repo",
            output_view=NoOpRunnerLiveView(),
        )
    finally:
        reset_agent_daemon_stopping()

    assert exit_code == 0
    assert set(fake_client._issue_labels[issue.number]) == set(running_labels)
    assert not any(
        call["method"] == "edit_issue_labels" and config.labels.failed in call.get("add", [])
        for call in fake_client.calls
    )
    assert not any(call["method"] == "comment_issue" for call in fake_client.calls)
