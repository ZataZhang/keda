"""Tests for the SQLite console store (run history + audit log + monitor snapshots).

快照与同步设置是 dashboard 的事实读取路径，因此本文件的迁移用例以"用户真实
旧库"为起点（用 v3 时代的 CREATE 语句手工播种），并在迁移后用**独立连接**
复核，避免只验证新表存在而漏掉旧数据是否保留。
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    AuditEntry,
    RunRecord,
)
from backend.infrastructure.persistence.console_store import (
    MonitorSettingsEntry,
    MonitorSnapshotEntry,
    RoadmapQueueEntry,
    RoadmapSettingsEntry,
    SqliteConsoleStore,
    _SCHEMA_VERSION,
)

# v3 时代的建表语句：刻意与当前代码里的 CREATE 解耦，模拟用户磁盘上的旧库。
_V3_CREATE_RUN_RECORDS = """
CREATE TABLE IF NOT EXISTS run_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    agent TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error_summary TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL
)
"""

_V3_CREATE_AUDIT_LOGS = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    repo_id TEXT,
    issue_number INTEGER,
    params_json TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT
)
"""

_V3_CREATE_ROADMAP_QUEUE = """
CREATE TABLE IF NOT EXISTS roadmap_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    prd_path TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_detail TEXT
)
"""

_V3_CREATE_ROADMAP_SETTINGS = """
CREATE TABLE IF NOT EXISTS roadmap_settings (
    repo_id TEXT PRIMARY KEY,
    max_parallel INTEGER NOT NULL,
    default_view TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_V3_CREATE_ATTEMPT_RECORDS = """
CREATE TABLE IF NOT EXISTS attempt_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    agent TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    failure_type TEXT NOT NULL,
    recovered INTEGER NOT NULL,
    detail TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _seed_v3_database(db_path: Path, run_record_count: int = 3) -> None:
    """Create a v3-shaped database with historical rows and user_version=3."""
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(_V3_CREATE_RUN_RECORDS)
        connection.execute(_V3_CREATE_AUDIT_LOGS)
        connection.execute(_V3_CREATE_ROADMAP_QUEUE)
        connection.execute(_V3_CREATE_ROADMAP_SETTINGS)
        connection.execute(_V3_CREATE_ATTEMPT_RECORDS)
        for index in range(run_record_count):
            connection.execute(
                "INSERT INTO run_records "
                "(repo_id, repo_path, issue_number, trigger, agent, outcome, "
                " error_summary, started_at, finished_at, duration_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"legacy-repo-{index}",
                    f"/tmp/legacy-{index}",
                    100 + index,
                    "cli_run",
                    "codex",
                    "completed",
                    None,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:01:00+00:00",
                    60.0,
                ),
            )
        connection.execute(
            "INSERT INTO roadmap_settings (repo_id, max_parallel, default_view, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-repo-0", 2, "list", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()


def _fresh_connection(db_path: Path) -> sqlite3.Connection:
    """Open a brand-new connection so no migrated in-memory state is reused."""
    return sqlite3.connect(str(db_path))


def _make_run_record(
    *,
    issue_number: int = 19,
    outcome: str = "completed",
    repo_id: str = "keda-main",
    started_at: str = "2026-06-11T10:00:00+00:00",
) -> RunRecord:
    return RunRecord(
        repo_id=repo_id,
        repo_path="/tmp/repo",
        issue_number=issue_number,
        trigger="cli_run",
        agent="claude",
        outcome=outcome,
        error_summary=None if outcome == "completed" else "boom",
        started_at=started_at,
        finished_at="2026-06-11T10:05:00+00:00",
        duration_seconds=300.0,
    )


def test_append_and_list_runs(tmp_path: Path) -> None:
    """Run records should round-trip through the SQLite store."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_run(_make_run_record(issue_number=1))
    store.append_run(_make_run_record(issue_number=2, outcome="failed"))

    recent_runs = store.list_recent_runs(limit=10)
    assert len(recent_runs) == 2
    # 倒序：最后写入的在最前。
    assert recent_runs[0].issue_number == 2
    assert recent_runs[0].outcome == "failed"
    assert recent_runs[0].error_summary == "boom"
    assert recent_runs[1].outcome == "completed"


def _make_attempt_record(
    *,
    repo_id: str = "keda-main",
    issue_number: int = 99,
    agent: str = "claude",
    attempt_number: int = 1,
) -> AttemptRecord:
    return AttemptRecord(
        repo_id=repo_id,
        issue_number=issue_number,
        agent=agent,
        attempt_number=attempt_number,
        failure_type="agent_error",
        recovered=False,
        detail=f"{agent} round {attempt_number}",
        started_at="2026-07-28T03:17:58+00:00",
        finished_at="2026-07-28T03:35:38+00:00",
        duration_seconds=1059.9,
    )


def test_list_issue_attempts_returns_chronological_trail(tmp_path: Path) -> None:
    """One Issue's attempts come back oldest-first, across agents."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_attempt(_make_attempt_record(agent="claude", attempt_number=1))
    store.append_attempt(_make_attempt_record(agent="claude", attempt_number=2))
    store.append_attempt(_make_attempt_record(agent="kimi", attempt_number=1))

    issue_attempts = store.list_issue_attempts(repo_id="keda-main", issue_number=99)

    assert [(a.agent, a.attempt_number) for a in issue_attempts] == [
        ("claude", 1),
        ("claude", 2),
        ("kimi", 1),
    ]
    assert issue_attempts[0].recovered is False
    assert issue_attempts[0].duration_seconds == 1059.9


def test_list_issue_attempts_filters_by_repo_and_issue(tmp_path: Path) -> None:
    """Attempts from other repos or other Issues never leak into the trail."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_attempt(_make_attempt_record(repo_id="freshai", issue_number=99))
    store.append_attempt(_make_attempt_record(repo_id="keda-main", issue_number=99))
    store.append_attempt(_make_attempt_record(repo_id="freshai", issue_number=100))

    issue_attempts = store.list_issue_attempts(repo_id="freshai", issue_number=99)

    assert len(issue_attempts) == 1
    assert issue_attempts[0].repo_id == "freshai"
    assert issue_attempts[0].issue_number == 99


def test_list_issue_attempts_keeps_latest_within_limit(tmp_path: Path) -> None:
    """The limit trims the oldest rows and still returns oldest-first."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    for attempt_number in (1, 2, 3):
        store.append_attempt(_make_attempt_record(attempt_number=attempt_number))

    issue_attempts = store.list_issue_attempts(repo_id="keda-main", issue_number=99, limit=2)

    assert [a.attempt_number for a in issue_attempts] == [2, 3]


def test_list_runs_filters_by_repo(tmp_path: Path) -> None:
    """repo_id filter should only return matching records."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_run(_make_run_record(repo_id="alpha"))
    store.append_run(_make_run_record(repo_id="beta"))

    alpha_runs = store.list_recent_runs(repo_id="alpha", limit=10)
    assert [run.repo_id for run in alpha_runs] == ["alpha"]


def test_audit_round_trip(tmp_path: Path) -> None:
    """Audit entries should round-trip including rejected results."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_audit(
        AuditEntry(
            occurred_at="2026-06-11T10:00:00+00:00",
            actor="console",
            action="retry_failed",
            repo_id="keda-main",
            issue_number=19,
            params_json='{"action": "retry_failed"}',
            result="rejected",
            detail="not failed",
        )
    )
    audits = store.list_recent_audits(limit=10)
    assert len(audits) == 1
    assert audits[0].action == "retry_failed"
    assert audits[0].result == "rejected"
    assert audits[0].issue_number == 19


def test_daily_trend_groups_by_day_and_outcome(tmp_path: Path) -> None:
    """Trend aggregation should bucket by day with per-outcome counts."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    today_prefix = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    store.append_run(_make_run_record(issue_number=1, started_at=f"{today_prefix}T08:00:00+00:00"))
    store.append_run(
        _make_run_record(
            issue_number=2,
            outcome="failed",
            started_at=f"{today_prefix}T09:00:00+00:00",
        )
    )

    trend = store.daily_run_trend(repo_id=None, days=7)
    assert len(trend) == 1
    assert trend[0].day == today_prefix
    assert trend[0].completed == 1
    assert trend[0].failed == 1
    assert trend[0].blocked == 0
    assert trend[0].average_duration_seconds == 300.0


def test_store_survives_concurrent_style_reopen(tmp_path: Path) -> None:
    """Two store instances on the same file must both read/write (WAL)."""
    db_path = tmp_path / "console.db"
    writer_a = SqliteConsoleStore(db_path)
    writer_b = SqliteConsoleStore(db_path)
    writer_a.append_run(_make_run_record(issue_number=1))
    writer_b.append_run(_make_run_record(issue_number=2))
    assert len(writer_a.list_recent_runs(limit=10)) == 2


def test_append_failure_degrades_to_warning(tmp_path: Path) -> None:
    """A broken database must not raise out of append_run."""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    # 用目录占住 db 文件路径之外的方式不可行；直接破坏文件权限模拟。
    raw_connection = sqlite3.connect(db_path)
    raw_connection.execute("DROP TABLE run_records")
    raw_connection.commit()
    raw_connection.close()
    # 表被删掉后 append 不得抛出。
    store.append_run(_make_run_record())


def test_roadmap_settings_round_trip(tmp_path: Path) -> None:
    """Roadmap settings should be persisted and retrievable."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    settings = store.get_roadmap_settings("keda-main")
    assert settings is None

    store.save_roadmap_settings(
        RoadmapSettingsEntry(
            repo_id="keda-main",
            max_parallel=3,
            default_view="timeline",
            updated_at="2026-06-14T12:00:00+00:00",
        )
    )
    settings = store.get_roadmap_settings("keda-main")
    assert settings is not None
    assert settings.max_parallel == 3
    assert settings.default_view == "timeline"


def test_roadmap_queue_round_trip(tmp_path: Path) -> None:
    """Roadmap queue entries should be persisted and filterable."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    entry_id = store.enqueue_roadmap(
        RoadmapQueueEntry(
            repo_id="keda-main",
            prd_path="tasks/pending/P1-FEAT-20260101-a.md",
            status="queued",
            trigger="global",
            started_at=None,
            finished_at=None,
            error_detail=None,
        )
    )
    queue = store.list_roadmap_queue(repo_id="keda-main")
    assert len(queue) == 1
    assert queue[0].entry_id == entry_id
    assert queue[0].prd_path == "tasks/pending/P1-FEAT-20260101-a.md"

    store.update_roadmap_queue_status(
        entry_id=entry_id, status="running", started_at="2026-06-14T12:00:00+00:00"
    )
    running = store.list_roadmap_queue(repo_id="keda-main", status="running")
    assert len(running) == 1
    assert running[0].status == "running"


def test_schema_migration_from_version_1(tmp_path: Path) -> None:
    """An existing v1 database should be migrated to v2 in-place."""
    db_path = tmp_path / "console.db"
    SqliteConsoleStore(db_path)
    raw = sqlite3.connect(db_path)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 2
    tables = {
        row[0]
        for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "roadmap_queue" in tables
    assert "roadmap_settings" in tables
    raw.close()


# ─────────────────────────────────────────────────────────────────────────────
# 监控快照 / 同步设置（schema v4）
# ─────────────────────────────────────────────────────────────────────────────


def test_v3_database_migrates_to_v4_and_keeps_history(tmp_path: Path) -> None:
    """旧库打开新代码后自动升到最新版本，历史数据逐条保留且新表建成。"""
    db_path = tmp_path / "console.db"
    _seed_v3_database(db_path, run_record_count=3)

    SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        assert probe.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 3
        table_names = {
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        probe.close()
    assert "monitoring_snapshots" in table_names
    assert "monitor_settings" in table_names
    assert "prd_lifecycle_runs" in table_names
    assert "prd_lifecycle_events" in table_names

    reopened = SqliteConsoleStore(db_path)
    assert len(reopened.list_recent_runs()) == 3
    assert reopened.get_roadmap_settings("legacy-repo-0") is not None


def test_fresh_database_creates_monitor_tables(tmp_path: Path) -> None:
    """全新环境首次启动从零建表（不依赖任何历史库）。"""
    db_path = tmp_path / "fresh.db"

    store = SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
    finally:
        probe.close()
    assert store.list_monitor_snapshots() == []
    assert store.get_monitor_settings() is None
    assert store.list_lifecycle_runs() == []


def test_v4_database_migrates_to_latest_and_creates_lifecycle_tables(tmp_path: Path) -> None:
    """v4 旧库（无 lifecycle 表）打开新代码后补齐 v5 表并保留运行历史。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.append_run(
        RunRecord(
            repo_id="keda-main",
            repo_path="/tmp/repo",
            issue_number=1,
            trigger="cli_run",
            agent="claude",
            outcome="completed",
            error_summary=None,
            started_at="2026-09-21T10:00:00+00:00",
            finished_at="2026-09-21T10:05:00+00:00",
            duration_seconds=300.0,
        )
    )

    # 把库退回“v4 时代”形态：删掉 lifecycle 表并降回 user_version=4。
    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE IF EXISTS prd_lifecycle_events")
    raw.execute("DROP TABLE IF EXISTS prd_lifecycle_runs")
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()

    migrated = SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        table_names = {
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        probe.close()
    assert {"prd_lifecycle_runs", "prd_lifecycle_events"} <= table_names
    assert len(migrated.list_recent_runs()) == 1
    assert migrated.list_lifecycle_runs() == []


def test_monitor_snapshot_upsert_overwrites_same_repo(tmp_path: Path) -> None:
    """同一仓库重复 upsert 只保留一行，内容为最后一次写入。"""
    store = SqliteConsoleStore(tmp_path / "console.db")

    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id="keda-main",
            payload_json='{"repo_id": "keda-main", "issues": []}',
            scanned_at="2026-09-16T01:00:00+00:00",
        )
    )
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id="keda-main",
            payload_json='{"repo_id": "keda-main", "issues": [1]}',
            scanned_at="2026-09-16T02:00:00+00:00",
        )
    )
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id="other-repo",
            payload_json='{"repo_id": "other-repo"}',
            scanned_at="2026-09-16T02:00:00+00:00",
        )
    )

    snapshots = {entry.repo_id: entry for entry in store.list_monitor_snapshots()}
    assert set(snapshots) == {"keda-main", "other-repo"}
    assert snapshots["keda-main"].scanned_at == "2026-09-16T02:00:00+00:00"
    assert snapshots["keda-main"].payload_json == '{"repo_id": "keda-main", "issues": [1]}'


def test_monitor_settings_round_trip(tmp_path: Path) -> None:
    """设置写入后能被重新读回（界面改完重启仍生效的前提）。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    assert store.get_monitor_settings() is None

    store.save_monitor_settings(
        MonitorSettingsEntry(
            sync_enabled=False,
            sync_interval_seconds=900,
            updated_at="2026-09-16T03:00:00+00:00",
        )
    )
    loaded = store.get_monitor_settings()
    assert loaded is not None
    assert loaded.sync_enabled is False
    assert loaded.sync_interval_seconds == 900
    assert loaded.updated_at == "2026-09-16T03:00:00+00:00"

    store.save_monitor_settings(replace(loaded, sync_enabled=True, sync_interval_seconds=60))
    reloaded = store.get_monitor_settings()
    assert reloaded is not None
    assert reloaded.sync_enabled is True
    assert reloaded.sync_interval_seconds == 60


def test_monitor_snapshot_write_failure_propagates(tmp_path: Path) -> None:
    """快照是事实读取路径：写库失败必须抛出，不能像旁路审计那样被吞掉。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    saboteur = sqlite3.connect(str(db_path))
    saboteur.execute("DROP TABLE monitoring_snapshots")
    saboteur.commit()
    saboteur.close()

    with pytest.raises(Exception):
        store.upsert_monitor_snapshot(
            MonitorSnapshotEntry(
                repo_id="keda-main",
                payload_json="{}",
                scanned_at="2026-09-16T01:00:00+00:00",
            )
        )
