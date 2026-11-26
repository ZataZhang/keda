"""真实 SQLite 回归：仓库别名和 Issue 编号相同，不代表远端 Issue 身份相同。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    InvocationEventRecord,
    MonitorSnapshotEntry,
    PrdLifecycleEventRecord,
    PrdLifecycleRunRecord,
    RunRecord,
)
from backend.core.shared.models.runtime_task import RuntimeTaskStatus, RuntimeTaskSummary
from backend.core.use_cases.agent_runner_evidence_media import EvidenceMedia
from backend.core.use_cases.monitor_snapshots import get_snapshot_overview
from backend.core.use_cases.runtime_dashboard import build_runtime_task_summary
from backend.core.use_cases.runtime_history_identity import select_runtime_history
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

CURRENT_ISSUE_URL = "https://github.com/team/freshai/issues/141"
FOREIGN_ISSUE_URL = "https://github.com/other-owner/freshai/issues/141"
OLD_START = "2026-11-26T08:00:00+00:00"
OLD_FINISH = "2026-11-26T08:30:00+00:00"
NEW_START = "2026-11-26T10:00:00+00:00"
NEW_FINISH = "2026-11-26T10:10:00+00:00"
REFERENCE_TIME = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def identity_store(tmp_path: Path) -> SqliteConsoleStore:
    """创建同名仓库同号 Issue 的旧运行、attempt、生命周期和调用账本。"""
    store = SqliteConsoleStore(tmp_path / "identity.db")
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id="freshai",
            payload_json=json.dumps(
                {
                    "repo_id": "freshai",
                    "issues": [
                        {
                            "number": 141,
                            "title": "新仓库任务",
                            "url": CURRENT_ISSUE_URL,
                            "labels": ["agent/ready"],
                            "state": "open",
                        }
                    ],
                }
            ),
            scanned_at=NEW_FINISH,
        )
    )
    store.append_run(
        RunRecord(
            repo_id="freshai",
            repo_path="/repo/freshai",
            issue_number=141,
            trigger="cli_run",
            agent="foreign-agent",
            outcome="failed",
            error_summary="另一个仓库的失败",
            started_at=OLD_START,
            finished_at=OLD_FINISH,
            duration_seconds=1800,
            issue_title="旧仓库任务",
            issue_url=FOREIGN_ISSUE_URL,
        )
    )
    store.append_attempt(
        AttemptRecord(
            repo_id="freshai",
            issue_number=141,
            agent="foreign-agent",
            attempt_number=1,
            failure_type="execution_failed",
            recovered=False,
            detail="",
            started_at=OLD_START,
            finished_at=OLD_FINISH,
            duration_seconds=1800,
            preset="foreign-preset",
            model="foreign-model",
            failure_phase="fix",
        )
    )
    store.upsert_lifecycle_run(
        PrdLifecycleRunRecord(
            run_id="freshai#141",
            repo_id="freshai",
            prd_path="tasks/archive/foreign.md",
            issue_number=141,
            trigger="cli_run",
            started_at=OLD_START,
            finished_at=OLD_FINISH,
            outcome="failed",
            history_complete=True,
        )
    )
    store.append_lifecycle_event(
        PrdLifecycleEventRecord(
            run_id="freshai#141",
            event_key="old-start",
            event_type="started",
            phase="executing",
            actor="runner",
            occurred_at=OLD_START,
            detail_json='{"invocation_run":"old-session"}',
        )
    )
    store.append_invocation_event(
        InvocationEventRecord(
            run_id="old-session",
            event_key="old-invocation",
            event_type="invocation_started",
            invocation_id="old-call",
            repo_id="freshai",
            issue_number=141,
            phase="supervisor_repair",
            role="implementer",
            agent="foreign-agent",
            occurred_at=OLD_START,
            detail_json="{}",
        )
    )
    return store


def _summary(store: SqliteConsoleStore, **options: Any) -> RuntimeTaskSummary:
    """在真实账本上执行完整摘要用例，而不替换数据库或身份筛选实现。"""
    return build_runtime_task_summary(
        snapshot_overview=get_snapshot_overview(store, enabled_repo_ids=["freshai"]),
        run_history_store=store,
        prd_lifecycle_store=store,
        enabled_repo_ids=["freshai"],
        now=REFERENCE_TIME,
        **options,
    )


def test_foreign_repository_history_never_attaches_to_same_number(
    identity_store: SqliteConsoleStore,
) -> None:
    """新仓库的待执行 Issue 不关联旧仓库的任何运行字段、PRD、调用或证据。"""
    evidence_lookups = []
    summary = _summary(
        identity_store,
        evidence_media=lambda task: evidence_lookups.append(task.task_key)
        or EvidenceMedia(page_ref="foreign-review.md"),
    )
    assert len(summary.tasks) == 1
    task = summary.tasks[0]
    assert task.status is RuntimeTaskStatus.WAITING
    assert task.issue_url == CURRENT_ISSUE_URL
    assert task.waiting_reason == "queued"
    for field_name in (
        "last_outcome",
        "last_error_summary",
        "started_at",
        "finished_at",
        "duration_seconds",
        "agent",
        "preset",
        "model",
        "attempt_number",
        "pipeline_stage",
        "prd_path",
        "invocation_run_id",
        "unclosed_invocation_executor",
    ):
        assert getattr(task, field_name) is None, field_name
    assert evidence_lookups == []
    assert not task.evidence_page_available
    assert identity_store.list_recent_runs(repo_id="freshai")[0].issue_url == FOREIGN_ISSUE_URL


@pytest.mark.parametrize(
    "current_url", [CURRENT_ISSUE_URL, CURRENT_ISSUE_URL.upper() + "/?view=1#top"]
)
def test_selects_matching_run_before_latest_run_reduction(
    identity_store: SqliteConsoleStore, current_url: str
) -> None:
    """较新的其他仓库记录不能盖掉较早但身份正确的当前仓库运行。"""
    foreign_run = identity_store.list_recent_runs(repo_id="freshai")[0]
    identity_store.append_run(
        replace(
            foreign_run,
            agent="current-agent",
            outcome="completed",
            error_summary=None,
            started_at=NEW_START,
            finished_at=NEW_FINISH,
            issue_url=current_url,
        )
    )
    identity_store.append_run(
        replace(
            foreign_run,
            started_at="2026-11-26T11:00:00+00:00",
            finished_at="2026-11-26T11:10:00+00:00",
        )
    )
    task = _summary(identity_store).tasks[0]
    assert task.agent == "current-agent"
    assert task.last_outcome == "completed"
    assert task.started_at == NEW_START
    assert task.prd_path is None
    assert task.preset is None


def test_current_unfinished_lifecycle_keeps_its_recorded_issue_identity(
    identity_store: SqliteConsoleStore,
) -> None:
    """新会话有明确远端身份时，保留其进度，不要求它先产生已结束的运行记录。"""
    identity_store.upsert_lifecycle_run(
        PrdLifecycleRunRecord(
            run_id="freshai#141",
            repo_id="freshai",
            prd_path="tasks/pending/current.md",
            issue_number=141,
            trigger="console_run",
            started_at=NEW_START,
            finished_at=None,
            outcome=None,
            history_complete=True,
        )
    )
    identity_store.reopen_lifecycle_run("freshai#141")
    identity_store.append_lifecycle_event(
        PrdLifecycleEventRecord(
            run_id="freshai#141",
            event_key="current-start",
            event_type="started",
            phase="executing",
            actor="runner",
            occurred_at=NEW_START,
            detail_json=json.dumps(
                {"issue_url": CURRENT_ISSUE_URL, "invocation_run": "current-session"}
            ),
        )
    )
    # 当前快照明确观察到新仓库正在运行。
    snapshot = identity_store.list_monitor_snapshots()[0]
    overview = json.loads(snapshot.payload_json)
    overview["issues"][0]["labels"] = ["agent/running"]
    identity_store.upsert_monitor_snapshot(replace(snapshot, payload_json=json.dumps(overview)))
    task = _summary(identity_store).tasks[0]
    assert task.status is RuntimeTaskStatus.RUNNING
    assert task.started_at == NEW_START
    assert task.invocation_run_id == "current-session"
    assert task.prd_path == "tasks/pending/current.md"
    assert task.last_error_summary is None
    assert task.preset is None


@pytest.mark.parametrize(
    "event_url, expected_count", [(CURRENT_ISSUE_URL, 1), (FOREIGN_ISSUE_URL, 0)]
)
def test_unfinished_identity_is_checked_without_completed_run_rows(
    identity_store: SqliteConsoleStore, event_url: str, expected_count: int
) -> None:
    """即使没有收口的 run 行，也按开始事件的地址隔离不同远端的会话。"""
    identity_store.append_lifecycle_event(
        PrdLifecycleEventRecord(
            run_id="freshai#141",
            event_key="unclosed-start",
            event_type="started",
            phase="executing",
            actor="runner",
            occurred_at=NEW_START,
            detail_json=json.dumps({"issue_url": event_url}),
        )
    )
    selection = select_runtime_history(
        current_issue_urls={("freshai", 141): CURRENT_ISSUE_URL},
        runs=(),
        lifecycle_runs=identity_store.list_lifecycle_runs(),
        lifecycle_store=identity_store,
    )
    assert len(selection.lifecycle_runs) == expected_count
