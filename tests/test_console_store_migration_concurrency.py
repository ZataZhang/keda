"""Console 与 runner 并发打开旧 SQLite 库时的迁移回归。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier, BrokenBarrierError

import pytest

from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.test_console_store import _seed_v3_database


def test_concurrent_console_store_startup_migrates_old_history(tmp_path: Path) -> None:
    """两个入口同时启动时，旧库只有一次版本迁移且两边均能读取原记录。"""
    db_path = tmp_path / "console.db"
    _seed_v3_database(db_path)
    openings = Barrier(2, timeout=5)
    first_column_additions = Barrier(2, timeout=1)

    def overlap_first_column_additions(statement: str) -> None:
        if not statement.startswith("ALTER TABLE attempt_records ADD COLUMN preset"):
            return
        try:
            first_column_additions.wait()
        except BrokenBarrierError:
            # 迁移串行化后，第二条连接读到已升级的版本，不会执行同一 ALTER。
            pass

    class CoordinatedConsoleStore(SqliteConsoleStore):
        """只控制真实 SQLite 连接抵达迁移语句的时机。"""

        def _connect(self) -> sqlite3.Connection:
            connection = super()._connect()
            connection.set_trace_callback(overlap_first_column_additions)
            return connection

    def open_and_read_issue_numbers() -> set[int]:
        openings.wait()
        store = CoordinatedConsoleStore(db_path)
        return {record.issue_number for record in store.list_recent_runs()}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_opening = pool.submit(open_and_read_issue_numbers)
        second_opening = pool.submit(open_and_read_issue_numbers)
        assert first_opening.result() == second_opening.result() == {100, 101, 102}


def test_failed_console_migration_rolls_back_before_retry(tmp_path: Path) -> None:
    """后续 DDL 失败时，版本与先前已执行的 DDL 都回滚。"""
    db_path = tmp_path / "console.db"
    _seed_v3_database(db_path)
    attempt_alter_count = 0

    def deny_second_column(action: int, _database: str, table: str, *_context: object) -> int:
        nonlocal attempt_alter_count
        if action == sqlite3.SQLITE_ALTER_TABLE and table == "attempt_records":
            attempt_alter_count += 1
            if attempt_alter_count == 2:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    class InterruptedMigrationStore(SqliteConsoleStore):
        """使用 SQLite 自身的授权回调模拟迁移中途拒绝 DDL。"""

        def _connect(self) -> sqlite3.Connection:
            connection = super()._connect()
            connection.set_authorizer(deny_second_column)
            return connection

    with pytest.raises(sqlite3.DatabaseError):
        InterruptedMigrationStore(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        attempt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(attempt_records)")
        }
        assert {"preset", "model"}.isdisjoint(attempt_columns)

    repaired_store = SqliteConsoleStore(db_path)
    assert {record.issue_number for record in repaired_store.list_recent_runs()} == {100, 101, 102}
