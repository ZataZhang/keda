"""Tests for loops advanced by the ordinary agent daemon."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.models.loop import (
    LoopFireResult,
    LoopFireStatus,
    LoopSchedule,
    LoopScheduleKind,
    LoopTask,
)
from backend.core.use_cases import agent_daemon_loops
from backend.core.use_cases.agent_daemon_loops import (
    AgentDaemonLoopPassRequest,
    run_agent_daemon_loop_pass,
)
from backend.engines.agent_runner.persistence.loop_state_json import JsonLoopStateStore
from backend.engines.agent_runner.scheduler.loop_clock import FixedClock
from tests.conftest import FakeGitHubClient, FakeProcessRunner


def _make_due_task(loop_id: str, *, managed_by: str) -> LoopTask:
    """构造一条已经到期的测试 loop。"""
    return LoopTask(
        id=loop_id,
        recipe_path=Path(f"/tmp/{loop_id}.md"),
        repo_id="keda",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 1 * * *"),
        managed_by=managed_by,
        next_fire_at="2026-06-23T00:00:00+00:00",
    )


def test_agent_daemon_pass_only_advances_owned_loops(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """普通 daemon 不接管旧 loop-daemon 任务或其他仓库任务。"""
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    agent_task = _make_due_task("agent-owned", managed_by="agent-daemon")
    old_task = _make_due_task("old-owned", managed_by="loop-daemon")
    other_repo_task = replace(agent_task, id="other-repo", repo_id="other")
    state_store.upsert_task(agent_task)
    state_store.upsert_task(old_task)
    state_store.upsert_task(other_repo_task)
    called_ids: list[str] = []

    def fake_fire_loop(task: LoopTask, **_kwargs) -> LoopFireResult:
        called_ids.append(task.id)
        return LoopFireResult(loop_id=task.id, status=LoopFireStatus.SKIPPED_ACTIVE)

    monkeypatch.setattr(agent_daemon_loops, "fire_loop", fake_fire_loop)
    outcomes = run_agent_daemon_loop_pass(
        AgentDaemonLoopPassRequest(
            repo_id="keda",
            repo_path=tmp_path,
            state_store=state_store,
            github_client=FakeGitHubClient(),
            process_runner=FakeProcessRunner(),
            clock=FixedClock(datetime(2026, 6, 23, 1, 0, tzinfo=timezone.utc)),
        )
    )

    assert called_ids == ["agent-owned"]
    assert outcomes == [("agent-owned", "skipped_active")]
