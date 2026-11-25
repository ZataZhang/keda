"""通用 Agent 调用观测账本（``agent_invocation_events`` 表）的持久化切片。

从 :mod:`backend.infrastructure.persistence.console_store` 拆出，职责单一：
建表/索引 DDL、行映射与 ``IInvocationEventStore`` 鸭子类型实现。身份是
``repo_id + issue_number + run_id``，刻意不含 ``prd_path`` 之类 PRD 必填字段，
因此无 PRD 的 Issue 同样能落调用事实。

与 PRD 生命周期账本同为旁路观测：追加式不可变，重复 ``event_key`` 幂等命中；
读取故障降级为空列表，读取侧据此显式披露"历史不完整"，不回填推测的执行器或
模型。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from backend.infrastructure.persistence.console_store_history import RunRecord, _run_record_from_row

_logger = logging.getLogger(__name__)

type RunInvocationLookup = tuple[str, int, str, str, str | None]


@dataclass(frozen=True)
class InvocationEventRecord:
    """一条追加式 Agent 调用观测事件（与 core 同构）。"""

    run_id: str
    event_key: str
    event_type: str
    invocation_id: str
    repo_id: str
    issue_number: int | None
    phase: str
    role: str
    agent: str
    occurred_at: str
    detail_json: str


# schema v8 -> v9（附加式）：通用 Agent 调用观测账本。旧库升级后本表为空：
# 读取侧据此显式披露"历史不完整"，不回填推测的执行器或模型。
CREATE_INVOCATION_EVENTS = """
CREATE TABLE IF NOT EXISTS agent_invocation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    issue_number INTEGER,
    phase TEXT NOT NULL,
    role TEXT NOT NULL,
    agent TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    UNIQUE (run_id, event_key)
)
"""

CREATE_INVOCATION_EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_agent_invocation_events_run "
    "ON agent_invocation_events (run_id, occurred_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_invocation_events_issue "
    "ON agent_invocation_events (repo_id, issue_number, occurred_at, id)",
)

_INVOCATION_EVENT_COLUMNS = (
    "run_id, event_key, event_type, invocation_id, repo_id, issue_number, "
    "phase, role, agent, occurred_at, detail_json"
)


# 先按会话标识或旧窗口选会话，再读取其完整事件。旧窗口多查最后一秒，由 core
# 确认精确的边界及唯一归属；日期粗范围使用索引并容纳带偏移的时间表示。
# 整次执行已显式绑定到其他记录的会话不参与兼容匹配。
_SCOPED_EVENTS_SQL = """
, sessions AS (
    SELECT repo_id, issue_number, run_id FROM requested WHERE run_id IS NOT NULL
    UNION
    SELECT e.repo_id, e.issue_number, e.run_id
    FROM requested AS w JOIN agent_invocation_events AS e USING (repo_id, issue_number)
    WHERE w.run_id IS NULL AND e.event_type = 'invocation_started'
      AND e.occurred_at >= date(w.started_at, '-1 day')
      AND e.occurred_at < date(w.finished_at, '+2 days')
      AND julianday(e.occurred_at) >= julianday(w.started_at)
      AND julianday(e.occurred_at) < julianday(w.finished_at, '+1 second')
      AND NOT EXISTS (
          SELECT 1 FROM run_records AS r INDEXED BY idx_run_records_invocation_run
          WHERE r.invocation_run_id = e.run_id
            AND r.repo_id = e.repo_id AND r.issue_number = e.issue_number
      )
)
SELECT e.* FROM sessions
JOIN agent_invocation_events AS e INDEXED BY idx_agent_invocation_events_run
USING (run_id, repo_id, issue_number)
ORDER BY julianday(e.occurred_at), e.id
"""

_LEGACY_PEERS_SQL = """
SELECT DISTINCT r.* FROM requested AS w JOIN run_records AS r USING (repo_id, issue_number)
WHERE w.run_id IS NULL AND r.invocation_run_id IS NULL
  AND julianday(r.started_at) < julianday(w.finished_at, '+1 second')
  AND julianday(r.finished_at, '+1 second') > julianday(w.started_at)
"""


class InvocationEventStoreMixin:
    """为 :class:`SqliteConsoleStore` 补齐调用观测账本的读写方法。

    依赖宿主的 ``_connect()`` 与 ``_db_path`` 属性（鸭子类型，不引入继承耦合
    之外的状态）。
    """

    def append_invocation_event(self, event_record: InvocationEventRecord) -> bool:
        """追加一条调用观测事件，按 ``(run_id, event_key)`` 幂等；失败时抛出异常。"""
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO agent_invocation_events "
                f"({_INVOCATION_EVENT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_record.run_id,
                    event_record.event_key,
                    event_record.event_type,
                    event_record.invocation_id,
                    event_record.repo_id,
                    event_record.issue_number,
                    event_record.phase,
                    event_record.role,
                    event_record.agent,
                    event_record.occurred_at,
                    event_record.detail_json,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_invocation_events(self, *, run_id: str) -> list[InvocationEventRecord]:
        """按发生顺序列出某个 run 的全部调用事件；表缺失时降级为空列表。"""
        query = (
            f"SELECT {_INVOCATION_EVENT_COLUMNS} FROM agent_invocation_events "
            "WHERE run_id = ? ORDER BY occurred_at ASC, id ASC"
        )
        return self._read_invocation_events(query, (run_id,))

    def list_issue_invocation_events(
        self, *, repo_id: str, issue_number: int, limit: int = 500
    ) -> list[InvocationEventRecord]:
        """按发生顺序列出某个 Issue 最近 ``limit`` 条调用事件（跨 run）。

        旧库没有这张表时降级为空列表而不是抛出：读取侧据此显式披露历史不完整，
        不回填推测字段。
        """
        query = (
            f"SELECT {_INVOCATION_EVENT_COLUMNS} FROM ("
            f"SELECT id, {_INVOCATION_EVENT_COLUMNS} FROM agent_invocation_events "
            "WHERE repo_id = ? AND issue_number = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY occurred_at ASC, id ASC"
        )
        return self._read_invocation_events(query, (repo_id, issue_number, max(1, limit)))

    def find_runs_for_invocation_lookup(
        self, lookup: RunInvocationLookup, *, open_window: bool = False
    ) -> list[RunRecord]:
        """查找详情端点指定的运行记录，接受等价的 ISO 时区表示。"""
        query = (
            "SELECT * FROM run_records WHERE repo_id = ? AND issue_number = ? "
            "AND julianday(started_at) = julianday(?) "
        )
        if not open_window:
            query += " AND julianday(finished_at) = julianday(?)"
        params = lookup[:3] if open_window else lookup[:4]
        with self._connection() as connection:
            return [_run_record_from_row(row) for row in connection.execute(query, params)]

    def read_run_invocation_events(
        self, lookups: Sequence[RunInvocationLookup]
    ) -> tuple[list[InvocationEventRecord], list[RunRecord]]:
        """批量读取指定会话和旧运行候选会话，保留完整配对事件。"""
        distinct_lookups = list(dict.fromkeys(lookups))
        invocation_events: dict[tuple[str, int | None, str, str], InvocationEventRecord] = {}
        legacy_runs: dict[int | None, RunRecord] = {}
        batch_size = 150  # 每个窗口五个参数，低于 SQLite 的旧版默认绑定上限。
        with self._connection() as connection:
            connection.execute("BEGIN")
            for batch_start in range(0, len(distinct_lookups), batch_size):
                batch = distinct_lookups[batch_start : batch_start + batch_size]
                values_sql = ", ".join("(?, ?, ?, ?, ?)" for _ in batch)
                requested = (
                    "WITH requested(repo_id, issue_number, started_at, finished_at, run_id) AS "
                    f"(VALUES {values_sql}) "
                )
                bound_values = tuple(value for lookup in batch for value in lookup)
                for event_row in connection.execute(requested + _SCOPED_EVENTS_SQL, bound_values):
                    event = _row_to_invocation_event(event_row)
                    event_identity = (
                        event.repo_id,
                        event.issue_number,
                        event.run_id,
                        event.event_key,
                    )
                    invocation_events[event_identity] = event
                if any(lookup[-1] is None for lookup in batch):
                    for run_row in connection.execute(requested + _LEGACY_PEERS_SQL, bound_values):
                        peer = _run_record_from_row(run_row)
                        legacy_runs[peer.record_id] = peer
        return list(invocation_events.values()), list(legacy_runs.values())

    def _read_invocation_events(
        self, query: str, params: tuple[object, ...]
    ) -> list[InvocationEventRecord]:
        """执行一次调用事件查询；表缺失等读取故障降级为空列表（旁路观测语义）。"""
        try:
            with self._connection() as connection:
                event_rows = connection.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            _logger.warning(
                "Failed to read agent invocation events from %s: %s", self._db_path, exc
            )
            return []
        return [_row_to_invocation_event(event_row) for event_row in event_rows]


def _row_to_invocation_event(event_row: sqlite3.Row) -> InvocationEventRecord:
    """把一条 ``agent_invocation_events`` 行还原为同构 dataclass。"""
    return InvocationEventRecord(
        run_id=event_row["run_id"],
        event_key=event_row["event_key"],
        event_type=event_row["event_type"],
        invocation_id=event_row["invocation_id"],
        repo_id=event_row["repo_id"],
        issue_number=(
            int(event_row["issue_number"]) if event_row["issue_number"] is not None else None
        ),
        phase=event_row["phase"],
        role=event_row["role"],
        agent=event_row["agent"],
        occurred_at=event_row["occurred_at"],
        detail_json=event_row["detail_json"],
    )
