"""运行失败执行器诊断的单元测试。"""

from __future__ import annotations

import json

from backend.core.shared.interfaces.runner_console import InvocationEventRecord
from backend.core.use_cases.runtime_failure_diagnosis import diagnose_runtime_failure


def _invocation_events(
    *, phase: str, executor: str, outcome: str, invocation_id: str, minute: int
) -> tuple[InvocationEventRecord, InvocationEventRecord]:
    """构造一组带完成事件的调用账本行。"""
    started_at = f"2026-11-17T10:{minute:02d}:00+00:00"
    finished_at = f"2026-11-17T10:{minute:02d}:30+00:00"
    shared = {
        "run_id": "keda#issue-12#run-1",
        "invocation_id": invocation_id,
        "repo_id": "keda",
        "issue_number": 12,
        "phase": phase,
        "role": "reviewer",
        "agent": executor,
    }
    return (
        InvocationEventRecord(
            **shared,
            event_key=f"{invocation_id}:started",
            event_type="invocation_started",
            occurred_at=started_at,
            detail_json="{}",
        ),
        InvocationEventRecord(
            **shared,
            event_key=f"{invocation_id}:finished",
            event_type="invocation_finished",
            occurred_at=finished_at,
            detail_json=json.dumps({"outcome": outcome}),
        ),
    )


def test_diagnose_latest_failed_executor_and_reason() -> None:
    """诊断选最近的失败阶段，保留该阶段已失败的执行器与具体错误。"""
    events = [
        *_invocation_events(
            phase="implementation",
            executor="codex",
            outcome="ok",
            invocation_id="impl",
            minute=1,
        ),
        *_invocation_events(
            phase="review",
            executor="claude",
            outcome="error",
            invocation_id="review-1",
            minute=2,
        ),
        *_invocation_events(
            phase="review",
            executor="codex",
            outcome="timeout",
            invocation_id="review-2",
            minute=3,
        ),
    ]

    diagnosis = diagnose_runtime_failure(
        started_at="2026-11-17T10:00:00+00:00",
        finished_at="2026-11-17T10:05:00+00:00",
        outcome="failed",
        error_summary="review request timed out after provider capacity was exhausted",
        run_agent="codex",
        invocation_events=events,
    )

    assert diagnosis is not None
    assert diagnosis.stage == "review"
    assert diagnosis.executor == "codex"
    assert diagnosis.failed_executors == ("claude", "codex")
    assert "provider 额度耗尽或请求被限流" in diagnosis.reason
    assert "review request timed out" in diagnosis.reason


def test_successful_last_invocation_does_not_misdiagnose_run_failure() -> None:
    """Agent 成功返回后被确定性门禁拦截时，不提供切换执行器入口。"""
    events = _invocation_events(
        phase="verification",
        executor="claude",
        outcome="ok",
        invocation_id="verify",
        minute=2,
    )

    diagnosis = diagnose_runtime_failure(
        started_at="2026-11-17T10:00:00+00:00",
        finished_at="2026-11-17T10:05:00+00:00",
        outcome="failed",
        error_summary="verification command returned status 1",
        run_agent="codex",
        invocation_events=events,
    )

    assert diagnosis is None
