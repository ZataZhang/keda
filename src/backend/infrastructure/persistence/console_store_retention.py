"""本地运行摘要的过期清理持久化能力。"""

from __future__ import annotations


class RunSummaryRetentionMixin:
    """为 SQLite Console Store 提供运行摘要保留策略。

    依赖宿主提供 ``_connect()`` 方法，与同目录的存储切片保持一致。
    """

    def prune_expired_summaries(self, *, cutoff: str, batch_size: int = 5000) -> tuple[int, int]:
        """以单个事务分批删除截止时间前完成的 run 与 attempt 摘要。

        审计、生命周期账本、队列、设置和监控快照不属于运行摘要，因此不触碰。
        未完成记录也保留，避免把活动执行状态误当作过期历史。
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        deleted_counts: list[int] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table_name in ("run_records", "attempt_records"):
                deleted_for_table = 0
                while True:
                    cursor = connection.execute(
                        f"DELETE FROM {table_name} WHERE id IN ("
                        f"SELECT id FROM {table_name} WHERE finished_at < ? "
                        "ORDER BY id LIMIT ?) "
                        "AND finished_at < ?",
                        (cutoff, batch_size, cutoff),
                    )
                    deleted_for_table += cursor.rowcount
                    if cursor.rowcount < batch_size:
                        break
                deleted_counts.append(deleted_for_table)
            connection.commit()
        return deleted_counts[0], deleted_counts[1]
