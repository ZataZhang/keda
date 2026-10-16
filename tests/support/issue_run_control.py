"""Issue 运行控制（人工接管）的测试替身，供存储层与 HTTP 层用例共用。

接管 CAS 的"绑定一致却仍读到 ``running``"分支在真实库里只有 CAS 与回读之间的并发
窗口能撞出来，回归测试没法可靠地复现时间窗。本模块只固定**这一个事实**（UPDATE 没
写成、行也没被别家改写），重试、分类、闸门落盘与审计全部走生产代码。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.infrastructure.persistence.console_store import SqliteConsoleStore


class _ZeroRowCursor:
    """只回答 ``rowcount == 0`` 的游标替身：语句"执行了却没改写任何行"。"""

    rowcount = 0


class _TakeoverCasConnection:
    """透传全部语句，只把接管 CAS 的前若干次 UPDATE 变成"匹配 0 行且不落盘"。"""

    def __init__(self, connection: sqlite3.Connection, store: "ScriptedTakeoverCasStore") -> None:
        self._connection = connection
        self._store = store

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> object:
        is_takeover_cas = "UPDATE issue_run_control" in statement and "request_id = ?" in statement
        if is_takeover_cas:
            self._store.cas_attempts += 1
            if self._store.misses_left > 0:
                self._store.misses_left -= 1
                return _ZeroRowCursor()
        return self._connection.execute(statement, parameters)

    def commit(self) -> None:
        self._connection.commit()

    def __enter__(self) -> "_TakeoverCasConnection":
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._connection.__exit__(*exc_info)  # type: ignore[arg-type]


class ScriptedTakeoverCasStore(SqliteConsoleStore):
    """让接管 CAS 的前 ``missed_cas_attempts`` 次 UPDATE 空手的 SQLite 存储。

    ``cas_attempts`` 记录 CAS 实际发起次数，用于断言"未命中必须真的重试一次"，
    而不是只断言最终状态码。
    """

    def __init__(self, db_path: Path, *, missed_cas_attempts: int) -> None:
        self.cas_attempts = 0
        self.misses_left = missed_cas_attempts
        super().__init__(db_path)

    def _connect(self) -> sqlite3.Connection:  # type: ignore[override]
        return _TakeoverCasConnection(super()._connect(), self)  # type: ignore[return-value]
