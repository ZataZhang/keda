"""Tests for the SQLite console store (run history + audit log + monitor snapshots).

快照与同步设置是 dashboard 的事实读取路径，因此本文件的迁移用例以"用户真实
旧库"为起点（用 v3 时代的 CREATE 语句手工播种），并在迁移后用**独立连接**
复核，避免只验证新表存在而漏掉旧数据是否保留。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    AuditEntry,
    InvocationEventRecord,
    PrdLifecycleEventRecord,
    PrdLifecycleRunRecord,
    RunRecord,
)
from backend.infrastructure.persistence.console_store import (
    MonitorSettingsEntry,
    MonitorSnapshotEntry,
    BacklogQueueEntry,
    BacklogSettingsEntry,
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

# v3 时代的队列/设置表叫 roadmap_*：这里刻意保留旧表名，才能模拟用户磁盘上的
# 旧库并验证 v7 迁移是否把表名与数据一起带过来。
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
        connection.execute(
            "INSERT INTO roadmap_queue "
            "(repo_id, prd_path, status, trigger, started_at, finished_at, error_detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-repo-0",
                "tasks/pending/legacy-prd.md",
                "completed",
                "manual",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:02:00+00:00",
                None,
            ),
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
    issue_title: str | None = None,
    issue_url: str | None = None,
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
        issue_title=issue_title,
        issue_url=issue_url,
    )


def test_append_and_list_runs(tmp_path: Path) -> None:
    """Run records should round-trip through the SQLite store."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_run(
        _make_run_record(
            issue_number=1,
            issue_title="Show task names in recent run history",
            issue_url="https://github.com/example/keda/issues/1",
        )
    )
    store.append_run(_make_run_record(issue_number=2, outcome="failed"))

    recent_runs = store.list_recent_runs(limit=10)
    assert len(recent_runs) == 2
    # 倒序：最后写入的在最前。
    assert recent_runs[0].issue_number == 2
    assert recent_runs[0].outcome == "failed"
    assert recent_runs[0].error_summary == "boom"
    assert recent_runs[1].outcome == "completed"
    assert recent_runs[1].issue_title == "Show task names in recent run history"
    assert recent_runs[1].issue_url == "https://github.com/example/keda/issues/1"


def test_v12_run_records_migrate_with_empty_issue_metadata(tmp_path: Path) -> None:
    """v12 运行记录升级后保留原数据，新增任务标题与 URL 为空。"""
    db_path = tmp_path / "console.db"
    legacy_connection = sqlite3.connect(str(db_path))
    try:
        legacy_connection.executescript(_V3_CREATE_RUN_RECORDS)
        legacy_connection.execute(
            "INSERT INTO run_records "
            "(repo_id, repo_path, issue_number, trigger, agent, outcome, error_summary, "
            "started_at, finished_at, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "keda-main",
                "/tmp/repo",
                264,
                "cli_run",
                "codex",
                "failed",
                "push rejected",
                "2026-10-10T10:13:03+00:00",
                "2026-10-10T10:55:41+00:00",
                2560.0,
            ),
        )
        legacy_connection.execute("PRAGMA user_version = 12")
        legacy_connection.commit()
    finally:
        legacy_connection.close()

    migrated_store = SqliteConsoleStore(db_path)
    migrated_run = migrated_store.list_recent_runs()[0]
    migrated_connection = _fresh_connection(db_path)
    try:
        migrated_version = migrated_connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        migrated_connection.close()

    assert migrated_version == _SCHEMA_VERSION
    assert migrated_run.issue_number == 264
    assert migrated_run.outcome == "failed"
    assert migrated_run.issue_title is None
    assert migrated_run.issue_url is None


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


def test_backlog_settings_round_trip(tmp_path: Path) -> None:
    """Backlog settings should be persisted and retrievable."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    settings = store.get_backlog_settings("keda-main")
    assert settings is None

    store.save_backlog_settings(
        BacklogSettingsEntry(
            repo_id="keda-main",
            max_parallel=3,
            default_view="timeline",
            updated_at="2026-06-14T12:00:00+00:00",
        )
    )
    settings = store.get_backlog_settings("keda-main")
    assert settings is not None
    assert settings.max_parallel == 3
    assert settings.default_view == "timeline"


def test_backlog_queue_round_trip(tmp_path: Path) -> None:
    """Backlog queue entries should be persisted and filterable."""
    store = SqliteConsoleStore(tmp_path / "console.db")
    entry_id = store.enqueue_backlog(
        BacklogQueueEntry(
            repo_id="keda-main",
            prd_path="tasks/pending/P1-FEAT-20260101-a.md",
            status="queued",
            trigger="global",
            started_at=None,
            finished_at=None,
            error_detail=None,
        )
    )
    queue = store.list_backlog_queue(repo_id="keda-main")
    assert len(queue) == 1
    assert queue[0].entry_id == entry_id
    assert queue[0].prd_path == "tasks/pending/P1-FEAT-20260101-a.md"

    store.update_backlog_queue_status(
        entry_id=entry_id, status="running", started_at="2026-06-14T12:00:00+00:00"
    )
    running = store.list_backlog_queue(repo_id="keda-main", status="running")
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
    assert "backlog_queue" in tables
    assert "backlog_settings" in tables
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
    assert reopened.get_backlog_settings("legacy-repo-0") is not None


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


def test_v5_database_migrates_to_v6_and_adds_attempt_preset_columns(
    tmp_path: Path,
) -> None:
    """v5 旧库打开新代码后自动补 attempt_records.preset / model 列，旧记录两列为 NULL。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.append_attempt(
        AttemptRecord(
            repo_id="keda-main",
            issue_number=1,
            agent="claude",
            attempt_number=1,
            failure_type="success",
            recovered=False,
            detail="pre-migration attempt",
            started_at="2026-09-28T10:00:00+00:00",
            finished_at="2026-09-28T10:01:00+00:00",
            duration_seconds=60.0,
        )
    )

    # 把库退回 v5 形态：去掉 v6 追加的列并降回 user_version=5。
    raw = sqlite3.connect(str(db_path))
    raw.execute("ALTER TABLE attempt_records DROP COLUMN preset")
    raw.execute("ALTER TABLE attempt_records DROP COLUMN model")
    raw.execute("PRAGMA user_version = 5")
    raw.commit()
    raw.close()

    migrated = SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        attempt_columns = {
            row[1] for row in probe.execute("PRAGMA table_info(attempt_records)").fetchall()
        }
        assert {"preset", "model"} <= attempt_columns
    finally:
        probe.close()

    attempts = migrated.list_issue_attempts(repo_id="keda-main", issue_number=1)
    assert len(attempts) == 1
    assert attempts[0].preset is None
    assert attempts[0].model is None


def test_v7_database_migrates_to_v8_and_adds_lifecycle_status_column(
    tmp_path: Path,
) -> None:
    """v7 旧库打开新代码后自动补 prd_lifecycle_events.status 列，旧行回落空串。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    # 先按当前 schema 写一条带 status 的事件，稍后回退到 v7 形态再迁移验证。
    store.upsert_lifecycle_run(
        PrdLifecycleRunRecord(
            run_id="keda-main#7",
            repo_id="keda-main",
            prd_path="tasks/pending/a.md",
            issue_number=7,
            trigger="console_start",
            started_at="2026-09-21T10:00:00+00:00",
            finished_at=None,
            outcome=None,
            history_complete=True,
        )
    )
    store.append_lifecycle_event(
        PrdLifecycleEventRecord(
            run_id="keda-main#7",
            event_key="q",
            event_type="queued",
            phase="queued",
            actor="backlog",
            occurred_at="2026-09-21T10:00:00+00:00",
            detail_json="{}",
            status="queued",
        )
    )

    # 把库退回 v7 形态：删掉 v8 追加的 status 列并降回 user_version=7。
    raw = sqlite3.connect(str(db_path))
    raw.execute("ALTER TABLE prd_lifecycle_events DROP COLUMN status")
    raw.execute("PRAGMA user_version = 7")
    raw.commit()
    raw.close()

    migrated = SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        event_columns = {
            row[1] for row in probe.execute("PRAGMA table_info(prd_lifecycle_events)").fetchall()
        }
        assert "status" in event_columns
    finally:
        probe.close()

    # 迁移前写入的旧行 status 回落空串（不伪造），读取不报错。
    legacy_events = migrated.list_lifecycle_events(run_id="keda-main#7")
    assert len(legacy_events) == 1
    assert legacy_events[0].status == ""

    # 迁移后写入的新事件把 status 落库并按值读回。
    migrated.append_lifecycle_event(
        PrdLifecycleEventRecord(
            run_id="keda-main#7",
            event_key="s",
            event_type="started",
            phase="executing",
            actor="runner",
            occurred_at="2026-09-21T10:01:00+00:00",
            detail_json="{}",
            status="started",
        )
    )
    fresh_events = migrated.list_lifecycle_events(run_id="keda-main#7")
    started = next(event for event in fresh_events if event.event_type == "started")
    assert started.status == "started"


def test_backlog_migration_renames_roadmap_tables_and_keeps_rows(tmp_path: Path) -> None:
    """v6 旧库（roadmap_* 表且有数据）打开新代码后改名为 backlog_*，行与列值无损保留。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.save_backlog_settings(
        BacklogSettingsEntry(
            repo_id="keda-main",
            max_parallel=3,
            default_view="timeline",
            updated_at="2026-10-05T10:00:00+00:00",
        )
    )
    entry_id = store.enqueue_backlog(
        BacklogQueueEntry(
            repo_id="keda-main",
            prd_path="tasks/pending/P1-FEAT-20261001-legacy.md",
            status="running",
            trigger="global",
            started_at="2026-10-01T09:00:00+00:00",
            finished_at=None,
            error_detail=None,
        )
    )

    # 把库退回 v6 形态：表名改回 roadmap_*，user_version 降回 6。
    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE backlog_queue RENAME TO roadmap_queue")
    raw.execute("ALTER TABLE backlog_settings RENAME TO roadmap_settings")
    raw.execute("PRAGMA user_version = 6")
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
        assert {"backlog_queue", "backlog_settings"} <= table_names
        assert "roadmap_queue" not in table_names
        assert "roadmap_settings" not in table_names
        assert probe.execute("SELECT COUNT(*) FROM backlog_queue").fetchone()[0] == 1
        assert probe.execute(
            "SELECT id, repo_id, prd_path, status, trigger, started_at "
            "FROM backlog_queue WHERE id = ?",
            (entry_id,),
        ).fetchone() == (
            entry_id,
            "keda-main",
            "tasks/pending/P1-FEAT-20261001-legacy.md",
            "running",
            "global",
            "2026-10-01T09:00:00+00:00",
        )
        assert probe.execute(
            "SELECT repo_id, max_parallel, default_view FROM backlog_settings"
        ).fetchall() == [("keda-main", 3, "timeline")]
    finally:
        probe.close()

    assert migrated.get_backlog_settings("keda-main") is not None
    assert migrated.get_backlog_settings("keda-main").max_parallel == 3
    assert [entry.entry_id for entry in migrated.list_backlog_queue(repo_id="keda-main")] == [
        entry_id
    ]


def test_backlog_migration_creates_missing_legacy_tables(tmp_path: Path) -> None:
    """v6 库缺少 roadmap_* 表（或完全空库）时迁移补齐 backlog_* 空表，不报错也不留空洞。"""
    db_path = tmp_path / "console.db"
    SqliteConsoleStore(db_path)

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE backlog_queue")
    raw.execute("DROP TABLE backlog_settings")
    raw.execute("PRAGMA user_version = 6")
    raw.commit()
    raw.close()

    migrated = SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        assert probe.execute("SELECT COUNT(*) FROM backlog_queue").fetchone()[0] == 0
        assert probe.execute("SELECT COUNT(*) FROM backlog_settings").fetchone()[0] == 0
    finally:
        probe.close()
    assert migrated.list_backlog_queue() == []
    assert migrated.get_backlog_settings("keda-main") is None


def test_attempt_preset_and_model_roundtrip(tmp_path: Path) -> None:
    """attempt 的预设、模型、失败阶段与生命周期耗时都能往返保存。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)

    store.append_attempt(
        AttemptRecord(
            repo_id="keda-main",
            issue_number=7,
            agent="codebuddy",
            attempt_number=1,
            failure_type="success",
            recovered=False,
            detail="bound",
            started_at="2026-09-30T10:00:00+00:00",
            finished_at="2026-09-30T10:02:00+00:00",
            duration_seconds=120.0,
            preset="plan",
            model="glm-5.3-flash",
            failure_phase="prd_delivery",
            phase_durations=(("agent", 90.0), ("prd_delivery", 4.5)),
        )
    )
    store.append_attempt(
        AttemptRecord(
            repo_id="keda-main",
            issue_number=7,
            agent="claude",
            attempt_number=1,
            failure_type="success",
            recovered=False,
            detail="unbound",
            started_at="2026-09-30T10:03:00+00:00",
            finished_at="2026-09-30T10:04:00+00:00",
            duration_seconds=60.0,
        )
    )

    attempts = store.list_issue_attempts(repo_id="keda-main", issue_number=7)
    by_detail = {attempt.detail: attempt for attempt in attempts}
    assert by_detail["bound"].preset == "plan"
    assert by_detail["bound"].model == "glm-5.3-flash"
    assert by_detail["bound"].failure_phase == "prd_delivery"
    assert by_detail["bound"].phase_durations == (("agent", 90.0), ("prd_delivery", 4.5))
    assert by_detail["unbound"].preset is None
    assert by_detail["unbound"].model is None
    assert by_detail["unbound"].failure_phase is None


def test_v10_database_migrates_attempt_failure_phase(tmp_path: Path) -> None:
    """v10 旧库自动补失败阶段和生命周期耗时列，不推测历史阶段数据。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.append_attempt(_make_attempt_record())

    raw = sqlite3.connect(str(db_path))
    raw.execute("ALTER TABLE attempt_records DROP COLUMN failure_phase")
    raw.execute("ALTER TABLE attempt_records DROP COLUMN phase_durations_json")
    raw.execute("PRAGMA user_version = 10")
    raw.commit()
    raw.close()

    migrated = SqliteConsoleStore(db_path)
    attempts = migrated.list_issue_attempts(repo_id="keda-main", issue_number=99)

    assert len(attempts) == 1
    assert attempts[0].failure_phase is None
    assert attempts[0].phase_durations == ()


# --- Agent 调用观测事件账本（Issue #242 / schema v9）-------------------------


def _invocation_event(
    *,
    run_id: str = "keda-main#issue-7#20261008T000000Z-abc",
    event_key: str,
    invocation_id: str,
    event_type: str = "invocation_started",
    issue_number: int | None = 7,
    repo_id: str = "keda-main",
    occurred_at: str = "2026-10-08T00:00:00+00:00",
    phase: str = "implementation",
) -> InvocationEventRecord:
    """构造一条调用观测事件（detail 只放结构化非敏感摘要）。"""
    return InvocationEventRecord(
        run_id=run_id,
        event_key=event_key,
        event_type=event_type,
        invocation_id=invocation_id,
        repo_id=repo_id,
        issue_number=issue_number,
        phase=phase,
        role="implementer",
        agent="claude",
        occurred_at=occurred_at,
        detail_json=json.dumps({"outcome": None}, ensure_ascii=False),
    )


def test_v8_database_migrates_to_v9_and_creates_invocation_events_table(
    tmp_path: Path,
) -> None:
    """v8 旧库打开新代码后自动补 agent_invocation_events 表，旧行完整保留。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.append_run(_make_run_record(issue_number=228, outcome="failed"))

    # 把库退回 v8 形态：删掉 v9 追加的表与索引并降回 user_version=8。
    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP TABLE IF EXISTS agent_invocation_events")
    raw.execute("PRAGMA user_version = 8")
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
        assert "agent_invocation_events" in table_names
        index_names = {
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert any(name.startswith("idx_agent_invocation_events") for name in index_names)
    finally:
        probe.close()

    # 迁移不得动既有历史：旧 run 记录仍读得到。
    runs = migrated.list_recent_runs(repo_id="keda-main")
    assert [run.issue_number for run in runs] == [228]

    # 迁移后写入的新事件可落库并读回。
    migrated.append_invocation_event(
        _invocation_event(event_key="inv-1:invocation_started", invocation_id="inv-1")
    )
    events = migrated.list_invocation_events(run_id="keda-main#issue-7#20261008T000000Z-abc")
    assert [event.invocation_id for event in events] == ["inv-1"]


def test_fresh_database_creates_invocation_events_table(tmp_path: Path) -> None:
    """全新库直接建到 v9，无需经过迁移分支。"""
    db_path = tmp_path / "console.db"
    SqliteConsoleStore(db_path)

    probe = _fresh_connection(db_path)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
        columns = {
            row[1] for row in probe.execute("PRAGMA table_info(agent_invocation_events)").fetchall()
        }
    finally:
        probe.close()
    assert {
        "run_id",
        "event_key",
        "event_type",
        "invocation_id",
        "repo_id",
        "issue_number",
        "phase",
        "role",
        "agent",
        "occurred_at",
        "detail_json",
    } <= columns


def test_invocation_event_append_is_idempotent_on_event_key(tmp_path: Path) -> None:
    """同一 (run_id, event_key) 重复写入只落一行：重试/并发不会产生重复事实。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    event = _invocation_event(event_key="inv-1:invocation_started", invocation_id="inv-1")

    assert store.append_invocation_event(event) is True
    assert store.append_invocation_event(event) is False

    events = store.list_invocation_events(run_id=event.run_id)
    assert len(events) == 1


def test_list_invocation_events_returns_chronological_trail(tmp_path: Path) -> None:
    """按发生顺序返回，且只返回本 run 的事件。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.append_invocation_event(
        _invocation_event(
            event_key="inv-1:invocation_started",
            invocation_id="inv-1",
            occurred_at="2026-10-08T00:00:00+00:00",
        )
    )
    store.append_invocation_event(
        _invocation_event(
            event_key="inv-1:invocation_finished",
            invocation_id="inv-1",
            event_type="invocation_finished",
            occurred_at="2026-10-08T00:05:00+00:00",
        )
    )
    store.append_invocation_event(
        _invocation_event(
            run_id="keda-main#issue-9#20261008T000000Z-def",
            event_key="inv-9:invocation_started",
            invocation_id="inv-9",
            issue_number=9,
        )
    )

    events = store.list_invocation_events(run_id="keda-main#issue-7#20261008T000000Z-abc")
    assert [(event.invocation_id, event.event_type) for event in events] == [
        ("inv-1", "invocation_started"),
        ("inv-1", "invocation_finished"),
    ]


def test_list_issue_invocation_events_spans_runs_and_honours_limit(tmp_path: Path) -> None:
    """跨 run 按 Issue 聚合（无 PRD 也能查），并保留最近 limit 条。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    for index in range(5):
        store.append_invocation_event(
            _invocation_event(
                run_id=f"keda-main#issue-7#20261008T00000{index}Z-run{index}",
                event_key=f"inv-{index}:invocation_started",
                invocation_id=f"inv-{index}",
                occurred_at=f"2026-10-08T00:0{index}:00+00:00",
            )
        )
    store.append_invocation_event(
        _invocation_event(
            run_id="keda-main#issue-9#20261008T000000Z-other",
            event_key="inv-other:invocation_started",
            invocation_id="inv-other",
            issue_number=9,
        )
    )

    all_events = store.list_issue_invocation_events(repo_id="keda-main", issue_number=7)
    assert [event.invocation_id for event in all_events] == [
        "inv-0",
        "inv-1",
        "inv-2",
        "inv-3",
        "inv-4",
    ]

    recent = store.list_issue_invocation_events(repo_id="keda-main", issue_number=7, limit=2)
    assert [event.invocation_id for event in recent] == ["inv-3", "inv-4"]

    other_issue = store.list_issue_invocation_events(repo_id="keda-main", issue_number=9)
    assert [event.invocation_id for event in other_issue] == ["inv-other"]

    assert store.list_issue_invocation_events(repo_id="other-repo", issue_number=7) == []


def test_invocation_event_reads_degrade_when_table_missing(tmp_path: Path) -> None:
    """历史库没有这张表时读取降级为空列表：披露不完整，而不是抛错阻断。"""
    db_path = tmp_path / "console.db"
    SqliteConsoleStore(db_path)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP TABLE agent_invocation_events")
    raw.commit()
    raw.close()

    legacy_store = SqliteConsoleStore(db_path)
    assert legacy_store.list_invocation_events(run_id="anything") == []
    assert legacy_store.list_issue_invocation_events(repo_id="keda-main", issue_number=7) == []


def test_backlog_snapshot_migration_v10_only_adds_its_table(tmp_path: Path) -> None:
    """v9 库升级到 v10 只新增 backlog_prd_snapshots，既有表、行与列值原样保留。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.save_backlog_settings(
        BacklogSettingsEntry(
            repo_id="keda-main",
            max_parallel=3,
            default_view="list",
            updated_at="2026-10-08T10:00:00+00:00",
        )
    )
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id="keda-main",
            payload_json=json.dumps({"repositories": [{"repo_id": "keda-main"}]}),
            scanned_at="2026-10-08T10:00:00+00:00",
        )
    )

    # 把库退回 v9 形态：删掉 v10 新增的表并降回 user_version=9。
    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP TABLE backlog_prd_snapshots")
    raw.execute("PRAGMA user_version = 9")
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
        assert "backlog_prd_snapshots" in table_names
        assert probe.execute("SELECT COUNT(*) FROM backlog_prd_snapshots").fetchone()[0] == 0
        assert probe.execute(
            "SELECT repo_id, max_parallel, default_view FROM backlog_settings"
        ).fetchall() == [("keda-main", 3, "list")]
        assert probe.execute("SELECT repo_id, scanned_at FROM monitoring_snapshots").fetchall() == [
            ("keda-main", "2026-10-08T10:00:00+00:00")
        ]
    finally:
        probe.close()

    assert migrated.get_backlog_settings("keda-main").max_parallel == 3
    assert migrated.get_backlog_snapshot(repo_id="keda-main", include_archived=False) is None
