"""验证停滞取消进入原 recovery 循环与验证/发布门禁。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    RunnerConfig,
)
from backend.core.shared.models.agent_stall import AgentStallCancelledError
from backend.core.use_cases import run_agent_execution_loop as execution_loop
from backend.core.use_cases import run_verifier_agent as verifier_module
from backend.core.use_cases.run_agent_once import MaxRetriesExceededError
from backend.core.use_cases.run_verifier_agent import ValidationVerdict
from tests.conftest import FakeProcessRunner


def _run_stalled_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    verification_return_code: int,
) -> tuple[list[str], list[str], list[str], list[str], list[bool]]:
    """将真实执行循环接到一次监督取消与一次 recovery 调用。"""
    issue = IssueSummary(
        number=77,
        title="Stalled recovery",
        url="https://example.test/issues/77",
        body="Exercise the existing recovery gates.",
        labels=(),
    )
    config = AppConfig(
        runner=RunnerConfig(
            max_recovery_attempts=1,
            recovery_retry_delay_seconds=0,
            verification_commands=("just test",),
        )
    )
    process_runner = FakeProcessRunner()
    prompts: list[str] = []
    gate_order: list[str] = []
    verifier_calls: list[str] = []
    recoveries: list[bool] = []
    committed = [False]

    def _agent_call(_agent: str, prompt: str, *_args: object, **_kwargs: object) -> CommandResult:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise AgentStallCancelledError(
                "[iar-stall-cancel] verified child group exited; "
                "stalled diagnosis: writer has made no progress"
            )
        recoveries.append(True)
        return CommandResult(("codex",), 0, "recovery response", "")

    def _verify(*_args: object, **_kwargs: object) -> list[CommandResult]:
        gate_order.append("verification")
        return [
            CommandResult(
                ("just", "test"),
                verification_return_code,
                "tests passed" if verification_return_code == 0 else "verification failed",
                "",
            )
        ]

    def _commit(*_args: object, **_kwargs: object) -> list[object]:
        gate_order.append("commit")
        committed[0] = True
        return []

    def _verifier(*_args: object, **_kwargs: object) -> ValidationVerdict:
        gate_order.append("independent verifier")
        verifier_calls.append("called")
        return ValidationVerdict(risk="green")

    monkeypatch.setattr(execution_loop, "run_agent_with_prompt_resilient", _agent_call)
    monkeypatch.setattr(execution_loop, "_resolve_repo_id", lambda *_args: "test/repo")
    monkeypatch.setattr(execution_loop, "_resolve_memory_stores", lambda *_args: (None, None))
    monkeypatch.setattr(execution_loop, "run_verification", _verify)
    monkeypatch.setattr(execution_loop, "ensure_prd_delivery_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        execution_loop,
        "ensure_validation_evidence_ready",
        lambda *_args, **_kwargs: gate_order.append("validation evidence"),
    )
    monkeypatch.setattr(
        execution_loop,
        "ensure_no_misplaced_evidence_helpers",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        execution_loop,
        "warn_legacy_evidence_helpers",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        execution_loop,
        "ensure_validation_commands_pass",
        lambda *_args, **_kwargs: gate_order.append("RV re-execution"),
    )
    monkeypatch.setattr(execution_loop, "commit_requested_changes", _commit)
    monkeypatch.setattr(execution_loop, "has_changes", lambda *_args: not committed[0])
    monkeypatch.setattr(
        execution_loop,
        "get_head_sha",
        lambda *_args: "committed-sha" if committed[0] else "before-sha",
    )
    monkeypatch.setattr(verifier_module, "run_verifier_gate", _verifier)

    request = execution_loop.AgentExecutionRequest(
        selected_agent="codex",
        issue=issue,
        worktree_path=tmp_path,
        config=config,
        process_runner=process_runner,
        before_sha="before-sha",
        expected_branch="issue-77",
        prompt_override="Implement the requested change.",
    )
    execution_loop.run_agent_until_committed(request)
    return prompts, gate_order, verifier_calls, recoveries, committed


def test_stalled_attempt_reuses_recovery_budget_and_existing_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停滞异常占用一轮预算，成功 recovery 仍需验证、RV 复跑与 verifier。"""
    prompts, gate_order, verifier_calls, recoveries, committed = _run_stalled_recovery(
        tmp_path,
        monkeypatch,
        verification_return_code=0,
    )

    assert len(prompts) == 2
    assert "writer has made no progress" in prompts[1]
    assert recoveries == [True]
    assert gate_order == [
        "verification",
        "validation evidence",
        "commit",
        "validation evidence",
        "RV re-execution",
        "independent verifier",
    ]
    assert verifier_calls == ["called"]
    assert committed == [True]


def test_failed_verification_after_stall_recovery_cannot_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停滞后的 recovery 验证失败时耗尽原预算，不提交也不进入 verifier。"""
    with pytest.raises(MaxRetriesExceededError) as failure:
        _run_stalled_recovery(
            tmp_path,
            monkeypatch,
            verification_return_code=1,
        )

    assert len(failure.value.attempt_results) == 2
    assert all(attempt.failure_type.value != "success" for attempt in failure.value.attempt_results)
