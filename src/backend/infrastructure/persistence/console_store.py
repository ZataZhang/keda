"""管理终端运行历史与审计的本地 SQLite 存储。

设计要点：

- 使用 stdlib ``sqlite3`` 而非 SQLAlchemy/alembic：CLI 直跑 ``kc run``
  也要写运行记录，不能要求 PostgreSQL 常驻；本地单文件零依赖。
- WAL + busy_timeout 容忍多个 runner 进程并发收尾写库。
- 通过 ``PRAGMA user_version`` 做就地迁移（当前版本 10：v5 新增
  ``prd_lifecycle_runs`` 与 ``prd_lifecycle_events`` 两张 PRD 生命周期账本表；
  v6 为 ``attempt_records`` 附加可空 ``preset`` / ``model`` 观测列；
  v7 把队列与设置两张表按新功能名重建；v8 为 ``prd_lifecycle_events`` 附加
  非空 ``status`` 列，记录每条事件写入时冻结的语义状态；v9 新增
  ``agent_invocation_events`` 通用调用观测账本表，身份不依赖 PRD；v10 新增
  按仓库与归档变体隔离的 ``backlog_prd_snapshots`` 列表快照表）。
- 旁路记录（运行历史 / 审计 / attempt）的写入失败不允许向上抛出阻断
  runner 主流程，降级为日志警告；而 dashboard 事实读取路径（监控快照
  与同步设置）的写入失败必须抛给调用方，避免"刷新成功但数据没更新"。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from backend.infrastructure.persistence.console_store_invocations import (
    CREATE_INVOCATION_EVENT_INDEXES,
    CREATE_INVOCATION_EVENTS,
    InvocationEventStoreMixin,
)
from backend.infrastructure.persistence.console_store_history import (
    AttemptRecord,
    AuditEntry,
    RunHistoryReadMixin,
    RunRecord,
)
from backend.infrastructure.persistence.console_store_lifecycle import (
    PrdLifecycleStoreMixin,
)

_logger = logging.getLogger(__name__)


_SCHEMA_VERSION = 10

_CREATE_RUN_RECORDS = """
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

_CREATE_ATTEMPT_RECORDS = """
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

# schema v5 -> v6（附加式）：attempt_records 追加可空 preset / model 列。
# 仅 ALTER 既有表；新库由 _CREATE_ATTEMPT_RECORDS 建表后再补列亦可（幂等）。
_ATTEMPT_V6_ADD_PRESET = "ALTER TABLE attempt_records ADD COLUMN preset TEXT"
_ATTEMPT_V6_ADD_MODEL = "ALTER TABLE attempt_records ADD COLUMN model TEXT"

_CREATE_AUDIT_LOGS = """
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

_CREATE_BACKLOG_QUEUE = """
CREATE TABLE IF NOT EXISTS backlog_queue (
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

_CREATE_BACKLOG_SETTINGS = """
CREATE TABLE IF NOT EXISTS backlog_settings (
    repo_id TEXT PRIMARY KEY,
    max_parallel INTEGER NOT NULL,
    default_view TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# schema v6 -> v7（重命名）：roadmap 功能正名为 backlog，两张表用
# ``ALTER TABLE ... RENAME TO`` 就地改名，行与列值原样保留。新库已由
# ``_CREATE_BACKLOG_*`` 以新名建表，故仅在旧表存在且新表缺席时才改名，保持幂等。
_BACKLOG_TABLE_RENAMES = (
    ("roadmap_queue", "backlog_queue"),
    ("roadmap_settings", "backlog_settings"),
)

_CREATE_MONITORING_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS monitoring_snapshots (
    repo_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    scanned_at TEXT NOT NULL
)
"""

_CREATE_MONITOR_SETTINGS = """
CREATE TABLE IF NOT EXISTS monitor_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sync_enabled INTEGER NOT NULL,
    sync_interval_seconds INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# schema v9 -> v10：Backlog 列表快照。同一仓库的"默认视图 / 显示已归档"是两个
# 独立变体，因此主键是 (repo_id, include_archived) 复合键，两行互不覆盖。
_CREATE_BACKLOG_PRD_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS backlog_prd_snapshots (
    repo_id TEXT NOT NULL,
    include_archived INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (repo_id, include_archived)
)
"""

_CREATE_PRD_LIFECYCLE_RUNS = """
CREATE TABLE IF NOT EXISTS prd_lifecycle_runs (
    run_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    prd_path TEXT NOT NULL,
    issue_number INTEGER,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    history_complete INTEGER NOT NULL DEFAULT 1
)
"""

_CREATE_PRD_LIFECYCLE_EVENTS = """
CREATE TABLE IF NOT EXISTS prd_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, event_key)
)
"""

# schema v7 -> v8：prd_lifecycle_events 追加非空 status 列（事件写入时冻结的语义
# 状态）。旧库行回落空串，由 core 序列化按 event_type 派生；新库由建表直接带上。
_LIFECYCLE_EVENT_V8_ADD_STATUS = (
    "ALTER TABLE prd_lifecycle_events ADD COLUMN status TEXT NOT NULL DEFAULT ''"
)

_CREATE_PRD_LIFECYCLE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_prd_lifecycle_runs_repo "
    "ON prd_lifecycle_runs (repo_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_prd_lifecycle_runs_prd "
    "ON prd_lifecycle_runs (repo_id, prd_path, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_prd_lifecycle_events_run "
    "ON prd_lifecycle_events (run_id, occurred_at, id)",
)


class SqliteConsoleStore(RunHistoryReadMixin, PrdLifecycleStoreMixin, InvocationEventStoreMixin):
    """``IRunHistoryStore`` / ``IBacklogStore`` / ``IMonitorSnapshotStore`` /
    ``IBacklogSnapshotStore`` 的 SQLite 实现。

    各端口都以鸭子类型实现：本类不 import core，仅保证方法签名与 core
    侧同名 dataclass 结构一致。
    """

    def __init__(self, db_path: str | Path) -> None:
        """初始化存储并确保 schema 就绪。

        Args:
            db_path: SQLite 文件路径，支持 ``~`` 展开。
        """
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._migrate(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        current_version_row = connection.execute("PRAGMA user_version").fetchone()
        current_version = int(current_version_row[0])
        if current_version >= _SCHEMA_VERSION:
            return
        if current_version < 1:
            connection.execute(_CREATE_RUN_RECORDS)
            connection.execute(_CREATE_AUDIT_LOGS)
        if current_version < 2:
            connection.execute(_CREATE_BACKLOG_QUEUE)
            connection.execute(_CREATE_BACKLOG_SETTINGS)
        if current_version < 3:
            connection.execute(_CREATE_ATTEMPT_RECORDS)
        if current_version < 4:
            connection.execute(_CREATE_MONITORING_SNAPSHOTS)
            connection.execute(_CREATE_MONITOR_SETTINGS)
        if current_version < 5:
            connection.execute(_CREATE_PRD_LIFECYCLE_RUNS)
            connection.execute(_CREATE_PRD_LIFECYCLE_EVENTS)
            for index_statement in _CREATE_PRD_LIFECYCLE_INDEXES:
                connection.execute(index_statement)
        if current_version < 6:
            # 附加式迁移：attempt_records 补 preset / model 可空列。新库在本轮
            # 迁移前刚由 _CREATE_ATTEMPT_RECORDS 建表，PRAGMA 探测保证幂等。
            existing_attempt_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(attempt_records)").fetchall()
            }
            if "preset" not in existing_attempt_columns:
                connection.execute(_ATTEMPT_V6_ADD_PRESET)
            if "model" not in existing_attempt_columns:
                connection.execute(_ATTEMPT_V6_ADD_MODEL)
        if current_version < 7:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for legacy_name, backlog_name in _BACKLOG_TABLE_RENAMES:
                if legacy_name in existing_tables and backlog_name not in existing_tables:
                    connection.execute(f"ALTER TABLE {legacy_name} RENAME TO {backlog_name}")
            # 缺表兜底：旧库若从未建过这两张表（或只建了一张），补齐空表，
            # 避免后续 SQL 命中 "no such table"。
            connection.execute(_CREATE_BACKLOG_QUEUE)
            connection.execute(_CREATE_BACKLOG_SETTINGS)
        if current_version < 8:
            # 附加式迁移：prd_lifecycle_events 补非空 status 列。新库本轮已由
            # _CREATE_PRD_LIFECYCLE_EVENTS 建表并带该列，PRAGMA 探测保证幂等。
            existing_event_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(prd_lifecycle_events)").fetchall()
            }
            if "status" not in existing_event_columns:
                connection.execute(_LIFECYCLE_EVENT_V8_ADD_STATUS)
        if current_version < 9:
            # 附加式迁移：只新增一张表与它的索引，既有表、行与列值原样保留。
            # 建表语句带 IF NOT EXISTS，重复迁移幂等。
            connection.execute(CREATE_INVOCATION_EVENTS)
            for index_statement in CREATE_INVOCATION_EVENT_INDEXES:
                connection.execute(index_statement)
        if current_version < 10:
            # 附加式迁移：只新增 backlog_prd_snapshots 一张表，既有表、行与列值
            # 原样保留；建表带 IF NOT EXISTS，重复迁移幂等。
            connection.execute(_CREATE_BACKLOG_PRD_SNAPSHOTS)
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        connection.commit()

    def append_run(self, run_record: RunRecord) -> None:
        """追加运行记录；失败时降级为日志警告，不阻断 runner。"""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO run_records "
                    "(repo_id, repo_path, issue_number, trigger, agent, outcome, "
                    " error_summary, started_at, finished_at, duration_seconds) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_record.repo_id,
                        run_record.repo_path,
                        run_record.issue_number,
                        run_record.trigger,
                        run_record.agent,
                        run_record.outcome,
                        run_record.error_summary,
                        run_record.started_at,
                        run_record.finished_at,
                        run_record.duration_seconds,
                    ),
                )
                connection.commit()
        except Exception as exc:  # noqa: BLE001 - side-channel must not break runs.
            _logger.warning("Failed to append run record to %s: %s", self._db_path, exc)

    def append_attempt(self, attempt_record: AttemptRecord) -> None:
        """追加 attempt 记录；失败时降级为日志警告，不阻断 runner。"""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO attempt_records "
                    "(repo_id, issue_number, agent, attempt_number, failure_type, "
                    " recovered, detail, started_at, finished_at, duration_seconds, "
                    " preset, model) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_record.repo_id,
                        attempt_record.issue_number,
                        attempt_record.agent,
                        attempt_record.attempt_number,
                        attempt_record.failure_type,
                        int(attempt_record.recovered),
                        attempt_record.detail,
                        attempt_record.started_at,
                        attempt_record.finished_at,
                        attempt_record.duration_seconds,
                        attempt_record.preset,
                        attempt_record.model,
                    ),
                )
                connection.commit()
        except Exception as exc:  # noqa: BLE001 - side-channel must not break runs.
            _logger.warning("Failed to append attempt record to %s: %s", self._db_path, exc)

    def append_audit(self, audit_entry: AuditEntry) -> None:
        """追加审计条目；失败时降级为日志警告。"""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO audit_logs "
                    "(occurred_at, actor, action, repo_id, issue_number, "
                    " params_json, result, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        audit_entry.occurred_at,
                        audit_entry.actor,
                        audit_entry.action,
                        audit_entry.repo_id,
                        audit_entry.issue_number,
                        audit_entry.params_json,
                        audit_entry.result,
                        audit_entry.detail,
                    ),
                )
                connection.commit()
        except Exception as exc:  # noqa: BLE001 - side-channel must not break actions.
            _logger.warning("Failed to append audit entry to %s: %s", self._db_path, exc)

    # ─────────────────────────────────────────────────────────────────────────

    def get_backlog_settings(self, repo_id: str) -> BacklogSettingsEntry | None:
        """读取指定仓库的 backlog 设置。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT repo_id, max_parallel, default_view, updated_at "
                "FROM backlog_settings WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
        if row is None:
            return None
        return BacklogSettingsEntry(
            repo_id=row["repo_id"],
            max_parallel=int(row["max_parallel"]),
            default_view=row["default_view"],
            updated_at=row["updated_at"],
        )

    def save_backlog_settings(self, settings: BacklogSettingsEntry) -> None:
        """保存或更新 backlog 设置；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO backlog_settings (repo_id, max_parallel, default_view, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(repo_id) DO UPDATE SET "
                "max_parallel=excluded.max_parallel, default_view=excluded.default_view, updated_at=excluded.updated_at",
                (
                    settings.repo_id,
                    settings.max_parallel,
                    settings.default_view,
                    settings.updated_at,
                ),
            )
            connection.commit()

    def enqueue_backlog(self, entry: BacklogQueueEntry) -> int:
        """将 PRD 加入 backlog 队列，返回自增 ID；失败时抛出异常。"""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO backlog_queue (repo_id, prd_path, status, trigger, started_at, finished_at, error_detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.repo_id,
                    entry.prd_path,
                    entry.status,
                    entry.trigger,
                    entry.started_at,
                    entry.finished_at,
                    entry.error_detail,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_backlog_queue(
        self, *, repo_id: str | None = None, status: str | None = None
    ) -> list[BacklogQueueEntry]:
        """列出 backlog 队列条目。"""
        query = (
            "SELECT id, repo_id, prd_path, status, trigger, started_at, finished_at, error_detail "
            "FROM backlog_queue"
        )
        conditions: list[str] = []
        params: list[object] = []
        if repo_id is not None:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            BacklogQueueEntry(
                entry_id=int(row["id"]),
                repo_id=row["repo_id"],
                prd_path=row["prd_path"],
                status=row["status"],
                trigger=row["trigger"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                error_detail=row["error_detail"],
            )
            for row in rows
        ]

    def update_backlog_queue_status(
        self,
        *,
        entry_id: int,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """更新队列条目的状态；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE backlog_queue SET status = ?, started_at = ?, finished_at = ?, error_detail = ? "
                "WHERE id = ?",
                (status, started_at, finished_at, error_detail, entry_id),
            )
            connection.commit()

    def clear_backlog_queue(self, *, repo_id: str | None = None) -> None:
        """清空 backlog 队列；失败时抛出异常。"""
        with self._connect() as connection:
            if repo_id is None:
                connection.execute("DELETE FROM backlog_queue")
            else:
                connection.execute("DELETE FROM backlog_queue WHERE repo_id = ?", (repo_id,))
            connection.commit()

    # ─────────────────────────────────────────────────────────────────────────
    # Monitor snapshots / settings (IMonitorSnapshotStore duck-type implementation)
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_monitor_snapshot(self, entry: MonitorSnapshotEntry) -> None:
        """写入或覆盖一个仓库的监控快照；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO monitoring_snapshots (repo_id, payload_json, scanned_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(repo_id) DO UPDATE SET "
                "payload_json=excluded.payload_json, scanned_at=excluded.scanned_at",
                (entry.repo_id, entry.payload_json, entry.scanned_at),
            )
            connection.commit()

    def list_monitor_snapshots(self) -> list[MonitorSnapshotEntry]:
        """列出全部仓库的监控快照；失败时抛出异常。"""
        with self._connect() as connection:
            snapshot_rows = connection.execute(
                "SELECT repo_id, payload_json, scanned_at FROM monitoring_snapshots"
            ).fetchall()
        return [
            MonitorSnapshotEntry(
                repo_id=snapshot_row["repo_id"],
                payload_json=snapshot_row["payload_json"],
                scanned_at=snapshot_row["scanned_at"],
            )
            for snapshot_row in snapshot_rows
        ]

    def get_monitor_settings(self) -> MonitorSettingsEntry | None:
        """读取全局监控同步设置；无记录时返回 ``None``（默认值由 core 注入）。"""
        with self._connect() as connection:
            settings_row = connection.execute(
                "SELECT sync_enabled, sync_interval_seconds, updated_at "
                "FROM monitor_settings WHERE id = 1"
            ).fetchone()
        if settings_row is None:
            return None
        return MonitorSettingsEntry(
            sync_enabled=bool(settings_row["sync_enabled"]),
            sync_interval_seconds=int(settings_row["sync_interval_seconds"]),
            updated_at=settings_row["updated_at"],
        )

    def save_monitor_settings(self, settings: MonitorSettingsEntry) -> None:
        """保存或更新全局监控同步设置；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO monitor_settings (id, sync_enabled, sync_interval_seconds, updated_at) "
                "VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "sync_enabled=excluded.sync_enabled, "
                "sync_interval_seconds=excluded.sync_interval_seconds, "
                "updated_at=excluded.updated_at",
                (
                    int(settings.sync_enabled),
                    settings.sync_interval_seconds,
                    settings.updated_at,
                ),
            )
            connection.commit()

    # ─────────────────────────────────────────────────────────────────────────
    # Backlog PRD snapshots (IBacklogSnapshotStore duck-type implementation)
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_backlog_snapshot(self, entry: BacklogSnapshotEntry) -> None:
        """写入或覆盖一个视图变体的 Backlog 快照；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO backlog_prd_snapshots "
                "(repo_id, include_archived, payload_json, scanned_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(repo_id, include_archived) DO UPDATE SET "
                "payload_json=excluded.payload_json, scanned_at=excluded.scanned_at",
                (
                    entry.repo_id,
                    int(entry.include_archived),
                    entry.payload_json,
                    entry.scanned_at,
                ),
            )
            connection.commit()

    def get_backlog_snapshot(
        self, *, repo_id: str, include_archived: bool
    ) -> BacklogSnapshotEntry | None:
        """读取指定视图变体的快照；不存在时返回 ``None``。"""
        with self._connect() as connection:
            snapshot_row = connection.execute(
                "SELECT repo_id, include_archived, payload_json, scanned_at "
                "FROM backlog_prd_snapshots WHERE repo_id = ? AND include_archived = ?",
                (repo_id, int(include_archived)),
            ).fetchone()
        if snapshot_row is None:
            return None
        return _row_to_backlog_snapshot(snapshot_row)

    def list_backlog_snapshots(self) -> list[BacklogSnapshotEntry]:
        """列出全部 Backlog 快照行；失败时抛出异常。"""
        with self._connect() as connection:
            snapshot_rows = connection.execute(
                "SELECT repo_id, include_archived, payload_json, scanned_at "
                "FROM backlog_prd_snapshots"
            ).fetchall()
        return [_row_to_backlog_snapshot(snapshot_row) for snapshot_row in snapshot_rows]


@dataclass(frozen=True)
class BacklogQueueEntry:
    """backlog 队列条目（与 core 侧同构，供 SQLite 实现使用）。"""

    repo_id: str
    prd_path: str
    status: str
    trigger: str
    started_at: str | None
    finished_at: str | None
    error_detail: str | None
    entry_id: int | None = None


@dataclass(frozen=True)
class BacklogSettingsEntry:
    """backlog 用户设置（与 core 侧同构，供 SQLite 实现使用）。"""

    repo_id: str
    max_parallel: int
    default_view: str
    updated_at: str


@dataclass(frozen=True)
class MonitorSnapshotEntry:
    """一个仓库的监控快照（与 core 侧同构，供 SQLite 实现使用）。"""

    repo_id: str
    payload_json: str
    scanned_at: str


@dataclass(frozen=True)
class MonitorSettingsEntry:
    """全局监控同步设置（与 core 侧同构，供 SQLite 实现使用）。"""

    sync_enabled: bool
    sync_interval_seconds: int
    updated_at: str


@dataclass(frozen=True)
class BacklogSnapshotEntry:
    """一个 Backlog 视图变体的列表快照（与 core 侧同构，供 SQLite 实现使用）。"""

    repo_id: str
    include_archived: bool
    payload_json: str
    scanned_at: str


def _row_to_backlog_snapshot(snapshot_row: sqlite3.Row) -> BacklogSnapshotEntry:
    """把一条 ``backlog_prd_snapshots`` 行还原为同构 dataclass。"""
    return BacklogSnapshotEntry(
        repo_id=snapshot_row["repo_id"],
        include_archived=bool(snapshot_row["include_archived"]),
        payload_json=snapshot_row["payload_json"],
        scanned_at=snapshot_row["scanned_at"],
    )


def summarize_params(params: dict) -> str:
    """将动作参数序列化为审计用 JSON 字符串。"""
    return json.dumps(params, ensure_ascii=False, sort_keys=True)
