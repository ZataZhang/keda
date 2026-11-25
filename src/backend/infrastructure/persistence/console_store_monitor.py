"""Console 单 Issue 监控观察值与跨进程锁。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from backend.infrastructure.file_lock import file_lock

if TYPE_CHECKING:
    from backend.infrastructure.persistence.console_store import MonitorSnapshotEntry


@dataclass(frozen=True)
class MonitorIssueRefresh:
    """单条监控观察值的 SQLite 表示。"""

    repo_id: str
    issue_number: int
    payload_json: str
    observed_ns: int


class MonitorIssueRefreshStoreMixin:
    """在控制台的 SQLite 连接上维护单 Issue 刷新。"""

    def monitor_issue_refresh_lock(
        self, repo_id: str, issue_number: int
    ) -> AbstractContextManager[None]:
        """跨 Console 与 CLI 进程合并同一个 Issue 的同时刷新请求。"""
        lock_key = sha256(f"{repo_id}:{issue_number}".encode("utf-8")).hexdigest()
        lock_path = self._db_path.parent / "monitor-issue-locks" / lock_key
        return file_lock(lock_path, timeout_seconds=60)

    def save_monitor_issue_refresh(self, entry: MonitorIssueRefresh) -> None:
        """原子替换单个 Issue 的观察值。"""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO monitoring_issue_refreshes VALUES (?, ?, ?, ?) "
                "ON CONFLICT(repo_id, issue_number) DO UPDATE SET "
                "payload_json=excluded.payload_json, observed_ns=excluded.observed_ns "
                "WHERE excluded.observed_ns > monitoring_issue_refreshes.observed_ns",
                (entry.repo_id, entry.issue_number, entry.payload_json, entry.observed_ns),
            )
            connection.commit()

    def monitor_issue_refresh_revision(self) -> str:
        """读取快速刷新版本，以字符串保留纳秒精度。"""
        with self._connection() as connection:
            return str(
                connection.execute(
                    "SELECT COALESCE(MAX(observed_ns), 0) FROM monitoring_issue_refreshes"
                ).fetchone()[0]
            )

    def get_monitor_issue_refresh(
        self, repo_id: str, issue_number: int
    ) -> MonitorIssueRefresh | None:
        """按主键读取当前观察值。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT repo_id, issue_number, payload_json, observed_ns "
                "FROM monitoring_issue_refreshes WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            ).fetchone()
        return MonitorIssueRefresh(**dict(row)) if row is not None else None

    def read_monitor_snapshot_state(
        self,
    ) -> tuple[list[MonitorSnapshotEntry], list[MonitorIssueRefresh]]:
        """在同一个 SQLite 读事务中读取快照和覆盖的 Issue。"""
        from backend.infrastructure.persistence.console_store import MonitorSnapshotEntry

        with self._connection() as connection:
            connection.execute("BEGIN")
            snapshots = [
                MonitorSnapshotEntry(**dict(row))
                for row in connection.execute(
                    "SELECT repo_id, payload_json, scanned_at FROM monitoring_snapshots"
                )
            ]
            refreshes = [
                MonitorIssueRefresh(**dict(row))
                for row in connection.execute(
                    "SELECT repo_id, issue_number, payload_json, observed_ns "
                    "FROM monitoring_issue_refreshes"
                )
            ]
            return snapshots, refreshes
