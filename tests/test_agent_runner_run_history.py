"""验证运行历史持久化和 Console 调用会话的归属。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console_runs as console_run_routes
from backend.api.app import app
from backend.core.shared.interfaces.runner_console import (
    InvocationEventRecord,
    IRunHistoryStore,
    RunRecord,
)
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.agent_invocation_tracing import (
    bound_invocation_trace_context,
    build_invocation_trace_context,
)
from backend.core.use_cases.agent_runner_run_history import append_run_record
from backend.infrastructure.persistence.console_store import SqliteConsoleStore


def test_append_run_record_captures_issue_title_and_url() -> None:
    """追加运行历史时保存同一次 Issue 快照中的标题和 URL。"""
    saved_run_records: list[RunRecord] = []
    run_history_store = cast(
        IRunHistoryStore,
        SimpleNamespace(append_run=saved_run_records.append),
    )
    issue_summary = IssueSummary(
        number=264,
        title="Improve recent run task names",
        url="https://github.com/example/keda/issues/264",
        body="",
        labels=(),
    )

    trace_context = build_invocation_trace_context(
        repo_id="keda",
        issue_number=issue_summary.number,
        run_history_store=run_history_store,
    )
    with bound_invocation_trace_context(trace_context):
        append_run_record(
            run_history_store=run_history_store,
            repo_id="keda",
            repo_path=Path("/tmp/keda"),
            issue=issue_summary,
            trigger="cli_run",
            agent="codex",
            outcome="completed",
            error_summary=None,
            started_at=datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc),
        )

    assert len(saved_run_records) == 1
    assert saved_run_records[0].issue_title == "Improve recent run task names"
    assert saved_run_records[0].issue_url == "https://github.com/example/keda/issues/264"
    assert saved_run_records[0].invocation_run_id == trace_context.run_id


@pytest.mark.parametrize(
    ("linked", "same_window"),
    [(False, False), (True, False), (True, True), (False, True)],
    ids=["legacy-handoff", "linked-handoff", "linked-same-window", "ambiguous-legacy"],
)
def test_run_invocations_remain_isolated_at_same_second_and_over_issue_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: bool, same_window: bool
) -> None:
    """同秒交接保留历史归属，后续 5000 条事件不会挤掉早期调用。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    monkeypatch.setattr(console_run_routes, "create_console_store", lambda: store)
    client = TestClient(app)
    handoff_at = "2026-10-10T06:01:00+00:00"
    run_specs = (
        ("20261010T060000Z-first", "2026-10-10T06:00:00+00:00", handoff_at, "claude", "200"),
        ("20261010T060100Z-second", handoff_at, "2026-10-10T06:02:00+00:00", "codex", "800"),
    )
    for session_suffix, started_at, finished_at, executor, millis in run_specs:
        session_id = f"keda-main#issue-314#{session_suffix}"
        store.append_run(
            RunRecord(
                repo_id="keda-main",
                repo_path="/tmp/keda-main",
                issue_number=314,
                trigger="console_run",
                agent="qoder",
                outcome="completed",
                error_summary=None,
                started_at=run_specs[0][1] if same_window else started_at,
                finished_at=run_specs[1][2] if same_window else finished_at,
                duration_seconds=60,
                invocation_run_id=session_id if linked else None,
            )
        )
        for event_type, occurred_at in (
            ("invocation_started", f"2026-10-10T06:01:00.{millis}+00:00"),
            ("invocation_finished", f"2026-10-10T06:01:00.{millis[0]}50+00:00"),
        ):
            store.append_invocation_event(
                InvocationEventRecord(
                    run_id=session_id,
                    event_key=event_type,
                    event_type=event_type,
                    invocation_id="shared-invocation-id",
                    repo_id="keda-main",
                    issue_number=314,
                    phase="implementation",
                    role="agent",
                    agent=executor,
                    occurred_at=occurred_at,
                    detail_json=json.dumps({"outcome": "ok"}),
                )
            )

    with store._connection() as connection:
        connection.execute(
            """
            WITH RECURSIVE burst(event_index) AS (
                SELECT 1 UNION ALL SELECT event_index + 1 FROM burst WHERE event_index < 5000
            )
            INSERT INTO agent_invocation_events (
                run_id, event_key, event_type, invocation_id, repo_id, issue_number,
                phase, role, agent, occurred_at, detail_json
            )
            SELECT run_id, 'burst-' || event_index, event_type, 'missing-start-' || event_index,
                   repo_id, issue_number, phase, role, agent, occurred_at, detail_json
            FROM agent_invocation_events CROSS JOIN burst
            WHERE run_id = ? AND event_type = 'invocation_finished'
            """,
            ("keda-main#issue-314#20261010T060100Z-second",),
        )
        connection.commit()

    response = client.get("/api/v1/agent-runner/console/runs", params={"repo_id": "keda-main"})
    assert response.status_code == 200
    listed_runs = response.json()["runs"]
    expected_executors = [[], []] if same_window and not linked else [["codex"], ["claude"]]
    assert [run["executors"] for run in listed_runs] == expected_executors
    newest_only = client.get(
        "/api/v1/agent-runner/console/runs", params={"repo_id": "keda-main", "limit": 1}
    )
    assert [run["executors"] for run in newest_only.json()["runs"]] == expected_executors[:1]
    for run, executors in zip(listed_runs, expected_executors, strict=True):
        detail = client.get(
            "/api/v1/agent-runner/console/runs/attempts",
            params={
                key: run[key]
                for key in ("repo_id", "issue_number", "started_at", "finished_at", "record_id")
            },
        )
        assert detail.status_code == 200
        assert [(row["executor"], row["outcome"]) for row in detail.json()["invocations"]] == [
            (executor, "ok") for executor in executors
        ]
