"""托管 Console 身份与会话的本地 SQLite 存储（Issue #265）。

设计要点：

- 只存摘要与哈希：引导码、管理员口令、会话令牌在服务端**从不**以可逆形式
  落盘；运行凭据密文另有专用文件（``hosted_credential_store``），不在本库。
- 与 ``console.db`` 分开建库：托管维护的运行摘要清理策略不得触碰本库，
  会话与引导记录的完整性由文件级隔离保证。
- 引导码消费用 ``UPDATE ... WHERE consumed_at IS NULL AND expires_at > ?``
  的单语句原子判定，杜绝 TOCTOU 重放窗口。
- 基础设施层禁止 import core：以下记录与 core 同名类型结构一致（鸭子类型）。
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2


def _to_iso(moment: datetime) -> str:
    """把 aware datetime 归一到 UTC ISO 文本用于落库比较。"""
    if moment.tzinfo is None:
        raise ValueError("naive datetime cannot be persisted for hosted console records")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _from_iso(text: str) -> datetime:
    """解析落库的 ISO 文本为 aware datetime。"""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class HostedBootstrapRecord:
    """一次性引导码记录（与 core.BootstrapTokenRecord 同构；只存哈希）。"""

    token_hash: str
    deployment_id: str
    issued_at_utc: datetime
    expires_at_utc: datetime
    consumed_at_utc: datetime | None = None


@dataclass(frozen=True)
class HostedAdminRecord:
    """管理员口令哈希记录（与 core.HostedAdminRecord 同构）。"""

    password_hash: str
    set_at_utc: datetime


@dataclass(frozen=True)
class HostedSessionRecord:
    """会话记录（与 core.HostedSessionRecord 同构；只存令牌摘要）。"""

    session_hash: str
    user_id: str
    created_at_utc: datetime
    expires_at_utc: datetime

    def is_valid_at(self, now_utc: datetime) -> bool:
        """Return whether the session is still alive (与 core 同构的行为方法）。"""
        return now_utc < self.expires_at_utc


class HostedConsoleStore:
    """实现 ``HostedConsoleIdentityStore`` 契约的本机 SQLite 存储。

    Args:
        db_path: ``[agent_runner.hosted].console_db_path`` 指向的数据库文件。
    """

    def __init__(self, db_path: Path) -> None:
        """建库建表并打开 WAL 模式。"""
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bootstrap_records (
                    id INTEGER PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS admin_record (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    password_hash TEXT NOT NULL,
                    set_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    client_hash TEXT PRIMARY KEY,
                    window_started_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        """打开带 busy_timeout 的连接（Console 与 operator CLI 可能并发）。"""
        connection = sqlite3.connect(self._db_path, timeout=10.0)
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def get_bootstrap(self) -> HostedBootstrapRecord | None:
        """返回最近一次签发的引导码记录（无论是否已消费）。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, deployment_id, issued_at, expires_at, consumed_at "
                "FROM bootstrap_records ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._bootstrap_from_row(row)

    @staticmethod
    def _bootstrap_from_row(row: tuple) -> HostedBootstrapRecord:
        """把查询行转成记录。"""
        token_hash, deployment_id, issued_at, expires_at, consumed_at = row
        return HostedBootstrapRecord(
            token_hash=token_hash,
            deployment_id=deployment_id,
            issued_at_utc=_from_iso(issued_at),
            expires_at_utc=_from_iso(expires_at),
            consumed_at_utc=_from_iso(consumed_at) if consumed_at else None,
        )

    def put_bootstrap(self, record: HostedBootstrapRecord) -> None:
        """插入新引导码记录，并原子作废此前未消费的记录（重发即失效旧码）。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE bootstrap_records SET consumed_at = issued_at " "WHERE consumed_at IS NULL"
            )
            connection.execute(
                "INSERT INTO bootstrap_records "
                "(token_hash, deployment_id, issued_at, expires_at, consumed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record.token_hash,
                    record.deployment_id,
                    _to_iso(record.issued_at_utc),
                    _to_iso(record.expires_at_utc),
                    _to_iso(record.consumed_at_utc) if record.consumed_at_utc else None,
                ),
            )

    def consume_bootstrap(self, token_hash: str, now_utc: datetime) -> bool:
        """原子消费一条未过期引导码；成功返回 ``True``。

        单语句 ``UPDATE ... WHERE`` 同时判定「哈希匹配、未消费、未过期」，
        重复提交同一码时第二次 rowcount 为 0，即拒绝重放。
        """
        now_text = _to_iso(now_utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE bootstrap_records SET consumed_at = ? "
                "WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?",
                (now_text, token_hash, now_text),
            )
            return cursor.rowcount == 1

    def get_admin(self) -> HostedAdminRecord | None:
        """返回管理员口令记录；首次引导完成前为 ``None``。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash, set_at FROM admin_record WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return HostedAdminRecord(password_hash=row[0], set_at_utc=_from_iso(row[1]))

    def put_admin(self, record: HostedAdminRecord) -> None:
        """写入管理员口令哈希（首期每部署单管理员，UPSERT 到 id=1）。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO admin_record (id, password_hash, set_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET password_hash = excluded.password_hash, "
                "set_at = excluded.set_at",
                (record.password_hash, _to_iso(record.set_at_utc)),
            )

    def consume_login_attempt(
        self,
        client_hash: str,
        attempted_at_utc: datetime,
        window_seconds: int,
        max_attempts: int,
    ) -> int | None:
        """原子占用来源地址的一次登录额度；超限时返回剩余秒数。"""
        if window_seconds < 1 or max_attempts < 1:
            raise ValueError("login attempt window and limit must be positive")
        if attempted_at_utc.tzinfo is None:
            raise ValueError("naive datetime cannot be persisted for hosted login attempts")
        started_at = attempted_at_utc.astimezone(UTC)
        now_text = _to_iso(started_at)
        window_start = started_at.timestamp() - window_seconds
        window_start_text = _to_iso(datetime.fromtimestamp(window_start - 1, UTC))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM login_attempts WHERE window_started_at <= ?",
                (window_start_text,),
            )
            row = connection.execute(
                "SELECT window_started_at, attempt_count FROM login_attempts "
                "WHERE client_hash = ?",
                (client_hash,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO login_attempts (client_hash, window_started_at, attempt_count) "
                    "VALUES (?, ?, 1)",
                    (client_hash, now_text),
                )
                return None
            window_started_text, attempt_count = row
            window_expires_at = _from_iso(window_started_text).timestamp() + window_seconds
            if attempted_at_utc.timestamp() >= window_expires_at:
                connection.execute(
                    "UPDATE login_attempts SET window_started_at = ?, attempt_count = 1 "
                    "WHERE client_hash = ?",
                    (now_text, client_hash),
                )
                return None
            if attempt_count >= max_attempts:
                return max(1, math.ceil(window_expires_at - attempted_at_utc.timestamp()))
            connection.execute(
                "UPDATE login_attempts SET attempt_count = attempt_count + 1 "
                "WHERE client_hash = ?",
                (client_hash,),
            )
            return None

    def clear_login_attempts(self, client_hash: str) -> None:
        """成功登录后清除此来源地址的登录尝试窗口。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM login_attempts WHERE client_hash = ?", (client_hash,))

    def put_session(self, record: HostedSessionRecord) -> None:
        """持久化一条会话摘要记录。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO sessions (session_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    record.session_hash,
                    record.user_id,
                    _to_iso(record.created_at_utc),
                    _to_iso(record.expires_at_utc),
                ),
            )

    def get_session(self, session_hash: str) -> HostedSessionRecord | None:
        """按摘要读取会话记录。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_hash, user_id, created_at, expires_at FROM sessions "
                "WHERE session_hash = ?",
                (session_hash,),
            ).fetchone()
        if row is None:
            return None
        return HostedSessionRecord(
            session_hash=row[0],
            user_id=row[1],
            created_at_utc=_from_iso(row[2]),
            expires_at_utc=_from_iso(row[3]),
        )

    def drop_session(self, session_hash: str) -> None:
        """删除会话记录（登出）。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE session_hash = ?", (session_hash,))

    def prune_sessions_expired_before(self, now_utc: datetime) -> int:
        """删除已过期会话并返回条数；这是本库唯一的清理入口，供 Console 维护调用。"""
        now_text = _to_iso(now_utc)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_text,))
            return cursor.rowcount
