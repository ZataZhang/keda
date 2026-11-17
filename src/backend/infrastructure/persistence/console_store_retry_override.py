"""Console SQLite 存储中的单次重试执行器覆盖。"""

from __future__ import annotations

CREATE_ISSUE_RETRY_OVERRIDES = """
CREATE TABLE IF NOT EXISTS issue_retry_overrides (
    repo_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    stage TEXT NOT NULL,
    executor TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (repo_id, issue_number)
)
"""


class IssueRetryOverrideStoreMixin:
    """以 Issue 为键保存一个待消费的阶段执行器覆盖。"""

    def set_issue_retry_override(
        self,
        *,
        repo_id: str,
        issue_number: int,
        stage: str,
        executor: str,
    ) -> None:
        """原子写入待续跑覆盖。"""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO issue_retry_overrides "
                "(repo_id, issue_number, stage, executor, requested_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(repo_id, issue_number) DO UPDATE SET "
                "stage = excluded.stage, executor = excluded.executor, "
                "requested_at = excluded.requested_at",
                (repo_id, issue_number, stage, executor),
            )

    def pop_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        """在单个 SQLite 写事务中读取并删除待消费覆盖。"""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT stage, executor FROM issue_retry_overrides "
                "WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "DELETE FROM issue_retry_overrides WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            )
            return str(row["stage"]), str(row["executor"])

    def get_issue_retry_override(
        self, *, repo_id: str, issue_number: int
    ) -> tuple[str, str] | None:
        """读取待消费覆盖但保留记录，直至 runner 获得 Issue claim。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT stage, executor FROM issue_retry_overrides "
                "WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            ).fetchone()
            return (str(row["stage"]), str(row["executor"])) if row is not None else None

    def clear_issue_retry_override(self, *, repo_id: str, issue_number: int) -> None:
        """清理未能成功排入队列的覆盖。"""
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM issue_retry_overrides WHERE repo_id = ? AND issue_number = ?",
                (repo_id, issue_number),
            )
