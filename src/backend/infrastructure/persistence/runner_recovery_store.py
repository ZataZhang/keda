"""Agent Runner 严格恢复账本的 SQLite 适配器。"""

from __future__ import annotations

import sqlite3
import os
import socket
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.shared.interfaces.runner_recovery import IRunnerRecoveryStore
from backend.core.shared.models.runner_recovery import (
    OperationIntent,
    OperationRecord,
    OperationState,
    RecoveryFailureRecord,
    RecoveryFailureCategory,
    RecoveryRunRecord,
    RecoveryStageRecord,
)

_SQLITE_BUSY_TIMEOUT_SECONDS = 5

_CREATE_RECOVERY_RUNS = """
CREATE TABLE IF NOT EXISTS runner_recovery_runs (
    run_key TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    first_failure_at TEXT NOT NULL,
    budget_deadline TEXT NOT NULL,
    resume_at TEXT,
    recovery_attempts INTEGER NOT NULL,
    recovery_state TEXT NOT NULL,
    failure_category TEXT NOT NULL,
    error_summary TEXT NOT NULL,
    report_path TEXT,
    updated_at TEXT NOT NULL
)
"""

_CREATE_RECOVERY_STAGES = """
CREATE TABLE IF NOT EXISTS runner_recovery_stages (
    stage_key TEXT PRIMARY KEY,
    run_key TEXT NOT NULL,
    stage TEXT NOT NULL,
    artifact_tree TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    validation_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    completed_at TEXT NOT NULL
)
"""

_CREATE_EXTERNAL_OPERATIONS = """
CREATE TABLE IF NOT EXISTS runner_external_operations (
    operation_key TEXT PRIMARY KEY,
    run_key TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    operation_kind TEXT NOT NULL,
    target TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_revision INTEGER NOT NULL,
    state TEXT NOT NULL,
    remote_reference TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    owner_host TEXT,
    owner_pid INTEGER
)
"""

_CREATE_OPERATION_LOOKUP_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runner_external_operations_run_state "
    "ON runner_external_operations (run_key, state)"
)


class RecoveryOperationIdentityConflict(sqlite3.IntegrityError):
    """操作键与已持久化的远端目标或载荷不匹配。"""


class SqliteRunnerRecoveryStore(IRunnerRecoveryStore):
    """在 Console SQLite 文件中保存严格恢复状态，与 best-effort 审计隔离。

    Args:
        db_path: 与现有 Console 运行历史共用的 SQLite 文件路径。
    """

    def __init__(self, db_path: str | Path) -> None:
        """打开 Console SQLite 并创建严格恢复表。

        Args:
            db_path: 现有 Console SQLite 文件路径。
        """
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.execute(_CREATE_RECOVERY_RUNS)
            connection.execute(_CREATE_RECOVERY_STAGES)
            connection.execute(_CREATE_EXTERNAL_OPERATIONS)
            connection.execute(_CREATE_OPERATION_LOOKUP_INDEX)

    def _connect(self) -> sqlite3.Connection:
        """创建启用 WAL 与外键的连接。

        Returns:
            由 ``_transaction`` 关闭的 SQLite 连接。
        """
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """执行严格事务；任何 SQLite 错误都传播到调用方。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()

    def begin_operation(self, intent: OperationIntent) -> OperationRecord:
        """持久化发送意图，并拒绝稳定键复用到不同内容。

        Args:
            intent: 写操作的稳定身份与载荷摘要。

        Returns:
            提交后的账本记录。
        """
        updated_at = _format_datetime(datetime.now(timezone.utc))
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runner_external_operations "
                "(operation_key, run_key, repository_id, issue_number, operation_kind, target, "
                "payload_digest, payload_revision, state, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent.operation_key,
                    intent.run_key,
                    intent.repository_id,
                    intent.issue_number,
                    intent.operation_kind,
                    intent.target,
                    intent.payload_digest,
                    intent.payload_revision,
                    OperationState.PENDING.value,
                    updated_at,
                ),
            )
            operation_row = connection.execute(
                "SELECT * FROM runner_external_operations WHERE operation_key = ?",
                (intent.operation_key,),
            ).fetchone()
            if operation_row is None:
                raise sqlite3.IntegrityError("操作意图提交后无法读取。")
            existing_intent = _operation_intent_from_row(operation_row)
            if existing_intent != intent:
                raise RecoveryOperationIdentityConflict(
                    f"操作键 {intent.operation_key} 已绑定到不同目标或载荷摘要。"
                )
            return _operation_record_from_row(operation_row)

    def get_operation(self, operation_key: str) -> OperationRecord | None:
        """读取账本并把崩溃遗留的发送中状态保守转为未知。

        Args:
            operation_key: 稳定操作键。

        Returns:
            操作快照；不存在时为 ``None``。
        """
        with self._transaction() as connection:
            operation_row = connection.execute(
                "SELECT * FROM runner_external_operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if operation_row is None:
                return None
            if operation_row["state"] == OperationState.IN_FLIGHT.value:
                if not _is_operation_owner_alive(operation_row):
                    connection.execute(
                        "UPDATE runner_external_operations SET state = ?, updated_at = ?, "
                        "owner_host = NULL, owner_pid = NULL WHERE operation_key = ? AND state = ?",
                        (
                            OperationState.UNKNOWN.value,
                            _format_datetime(datetime.now(timezone.utc)),
                            operation_key,
                            OperationState.IN_FLIGHT.value,
                        ),
                    )
                operation_row = connection.execute(
                    "SELECT * FROM runner_external_operations WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
            return _operation_record_from_row(operation_row)

    def transition_operation(
        self,
        operation_key: str,
        expected_states: Sequence[OperationState],
        new_state: OperationState,
        *,
        remote_reference: str | None = None,
        last_error: str | None = None,
    ) -> bool:
        """用单条 CAS 语句推进外部操作状态。

        Args:
            operation_key: 稳定操作键。
            expected_states: 可推进的旧状态。
            new_state: 新状态。
            remote_reference: 已确认的远端 ID 或 URL。
            last_error: 脱敏诊断。

        Returns:
            CAS 成功时为 ``True``。
        """
        if not expected_states:
            return False
        expected_placeholders = ", ".join("?" for _ in expected_states)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE runner_external_operations SET state = ?, remote_reference = ?, "
                "last_error = ?, updated_at = ?, owner_host = ?, owner_pid = ? "
                "WHERE operation_key = ? "
                f"AND state IN ({expected_placeholders})",
                (
                    new_state.value,
                    remote_reference,
                    last_error,
                    _format_datetime(datetime.now(timezone.utc)),
                    socket.gethostname() if new_state is OperationState.IN_FLIGHT else None,
                    os.getpid() if new_state is OperationState.IN_FLIGHT else None,
                    operation_key,
                    *(state.value for state in expected_states),
                ),
            )
            return cursor.rowcount == 1

    def record_failure(self, failure: RecoveryFailureRecord) -> RecoveryRunRecord:
        """累计跨调度恢复预算，超过次数或时长后转人工。

        Args:
            failure: 当前失败与有界恢复策略。

        Returns:
            更新后的持久恢复记录。
        """
        with self._transaction() as connection:
            existing_row = connection.execute(
                "SELECT * FROM runner_recovery_runs WHERE run_key = ?",
                (failure.run_key,),
            ).fetchone()
            first_failure_at = (
                _parse_datetime(existing_row["first_failure_at"])
                if existing_row is not None
                else failure.occurred_at
            )
            starts_new_cycle = existing_row is not None and existing_row["recovery_state"] in {
                "needs_human",
                "completed",
            }
            if starts_new_cycle:
                first_failure_at = failure.occurred_at
            budget_deadline = (
                _parse_datetime(existing_row["budget_deadline"])
                if existing_row is not None and not starts_new_cycle
                else first_failure_at + timedelta(seconds=failure.budget_seconds)
            )
            recovery_attempts = (
                int(existing_row["recovery_attempts"]) + 1
                if existing_row is not None and not starts_new_cycle
                else 0
            )
            should_handoff = (
                recovery_attempts >= failure.max_attempts
                or failure.occurred_at >= budget_deadline
                or (failure.retry_after is not None and failure.retry_after >= budget_deadline)
                or failure.category
                in {
                    RecoveryFailureCategory.AUTH,
                    RecoveryFailureCategory.FATAL_STATE,
                }
            )
            recovery_state = "needs_human" if should_handoff else "waiting"
            resume_at = None if should_handoff else failure.retry_after
            connection.execute(
                "INSERT INTO runner_recovery_runs "
                "(run_key, repository_id, issue_number, first_failure_at, budget_deadline, "
                "resume_at, recovery_attempts, recovery_state, failure_category, error_summary, "
                "report_path, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_key) DO UPDATE SET "
                "resume_at = excluded.resume_at, recovery_attempts = excluded.recovery_attempts, "
                "first_failure_at = excluded.first_failure_at, "
                "budget_deadline = excluded.budget_deadline, "
                "recovery_state = excluded.recovery_state, "
                "failure_category = excluded.failure_category, "
                "error_summary = excluded.error_summary, "
                "report_path = COALESCE(excluded.report_path, runner_recovery_runs.report_path), "
                "updated_at = excluded.updated_at",
                (
                    failure.run_key,
                    failure.repository_id,
                    failure.issue_number,
                    _format_datetime(first_failure_at),
                    _format_datetime(budget_deadline),
                    _format_datetime(resume_at) if resume_at is not None else None,
                    recovery_attempts,
                    recovery_state,
                    failure.category.value,
                    failure.error_summary,
                    failure.report_path,
                    _format_datetime(failure.occurred_at),
                ),
            )
            recovery_row = connection.execute(
                "SELECT * FROM runner_recovery_runs WHERE run_key = ?",
                (failure.run_key,),
            ).fetchone()
            if recovery_row is None:
                raise sqlite3.IntegrityError("恢复预算提交后无法读取。")
            return _recovery_run_from_row(recovery_row)

    def finish_run(self, run_key: str) -> None:
        """标记恢复链完成，供后续独立运行开启新的有限预算。"""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE runner_recovery_runs SET recovery_state = 'completed', resume_at = NULL, "
                "updated_at = ? WHERE run_key = ?",
                (_format_datetime(datetime.now(timezone.utc)), run_key),
            )

    def get_run(self, run_key: str) -> RecoveryRunRecord | None:
        """读取跨进程恢复预算。

        Args:
            run_key: 仓库与 Issue 的稳定键。

        Returns:
            恢复预算快照；不存在时为 ``None``。
        """
        with self._transaction() as connection:
            recovery_row = connection.execute(
                "SELECT * FROM runner_recovery_runs WHERE run_key = ?",
                (run_key,),
            ).fetchone()
            return _recovery_run_from_row(recovery_row) if recovery_row is not None else None

    def save_stage(self, stage: RecoveryStageRecord) -> None:
        """保存阶段成功结果及其可复用前提。

        Args:
            stage: 阶段与代码成果、base、验证配置摘要。
        """
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO runner_recovery_stages "
                "(stage_key, run_key, stage, artifact_tree, base_sha, validation_digest, "
                "status, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(stage_key) DO UPDATE SET run_key = excluded.run_key, "
                "stage = excluded.stage, artifact_tree = excluded.artifact_tree, "
                "base_sha = excluded.base_sha, validation_digest = excluded.validation_digest, "
                "status = excluded.status, completed_at = excluded.completed_at",
                (
                    stage.stage_key,
                    stage.run_key,
                    stage.stage,
                    stage.artifact_tree,
                    stage.base_sha,
                    stage.validation_digest,
                    stage.status,
                    _format_datetime(stage.completed_at),
                ),
            )

    def get_stage(self, stage_key: str) -> RecoveryStageRecord | None:
        """读取已保存阶段的成果前提。

        Args:
            stage_key: 稳定阶段键。

        Returns:
            阶段快照；不存在时为 ``None``。
        """
        with self._transaction() as connection:
            stage_row = connection.execute(
                "SELECT * FROM runner_recovery_stages WHERE stage_key = ?",
                (stage_key,),
            ).fetchone()
            return _recovery_stage_from_row(stage_row) if stage_row is not None else None

    def list_unresolved_operations(self, run_key: str) -> list[OperationRecord]:
        """读取未决操作，并将崩溃遗留的 ``in_flight`` 标为 ``unknown``。

        Args:
            run_key: 稳定恢复键。

        Returns:
            需要继续对账或人工处理的操作。
        """
        with self._transaction() as connection:
            in_flight_rows = connection.execute(
                "SELECT operation_key, owner_host, owner_pid FROM runner_external_operations "
                "WHERE run_key = ? AND state = ?",
                (run_key, OperationState.IN_FLIGHT.value),
            ).fetchall()
            for in_flight_row in in_flight_rows:
                if not _is_operation_owner_alive(in_flight_row):
                    connection.execute(
                        "UPDATE runner_external_operations SET state = ?, updated_at = ?, "
                        "owner_host = NULL, owner_pid = NULL WHERE operation_key = ? AND state = ?",
                        (
                            OperationState.UNKNOWN.value,
                            _format_datetime(datetime.now(timezone.utc)),
                            in_flight_row["operation_key"],
                            OperationState.IN_FLIGHT.value,
                        ),
                    )
            operation_rows = connection.execute(
                "SELECT * FROM runner_external_operations WHERE run_key = ? "
                "AND state IN (?, ?, ?) ORDER BY updated_at, operation_key",
                (
                    run_key,
                    OperationState.PENDING.value,
                    OperationState.IN_FLIGHT.value,
                    OperationState.UNKNOWN.value,
                ),
            ).fetchall()
            return [_operation_record_from_row(row) for row in operation_rows]


def _format_datetime(value: datetime) -> str:
    """以带 UTC 偏移的 ISO 格式序列化时间。"""
    if value.tzinfo is None:
        raise ValueError("恢复账本时间必须包含时区。")
    return value.astimezone(timezone.utc).isoformat()


def _is_operation_owner_alive(row: sqlite3.Row) -> bool:
    """检查正在发送的操作是否仍由当前机器上的进程持有。"""
    owner_host = row["owner_host"]
    owner_pid = row["owner_pid"]
    if not isinstance(owner_host, str) or not isinstance(owner_pid, int):
        return False
    if owner_host != socket.gethostname():
        return False
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_datetime(value: str) -> datetime:
    """读取包含偏移的 ISO 时间。"""
    parsed_datetime = datetime.fromisoformat(value)
    if parsed_datetime.tzinfo is None:
        raise ValueError("恢复账本中的时间缺少 UTC 偏移。")
    return parsed_datetime.astimezone(timezone.utc)


def _operation_intent_from_row(row: sqlite3.Row) -> OperationIntent:
    """从 SQLite 行中解析操作身份。"""
    return OperationIntent(
        operation_key=row["operation_key"],
        run_key=row["run_key"],
        repository_id=row["repository_id"],
        issue_number=int(row["issue_number"]),
        operation_kind=row["operation_kind"],
        target=row["target"],
        payload_digest=row["payload_digest"],
        payload_revision=int(row["payload_revision"]),
    )


def _operation_record_from_row(row: sqlite3.Row) -> OperationRecord:
    """从 SQLite 行中解析外部操作快照。"""
    return OperationRecord(
        intent=_operation_intent_from_row(row),
        state=OperationState(row["state"]),
        remote_reference=row["remote_reference"],
        last_error=row["last_error"],
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _recovery_run_from_row(row: sqlite3.Row) -> RecoveryRunRecord:
    """从 SQLite 行中解析跨进程恢复状态。"""
    return RecoveryRunRecord(
        run_key=row["run_key"],
        repository_id=row["repository_id"],
        issue_number=int(row["issue_number"]),
        first_failure_at=_parse_datetime(row["first_failure_at"]),
        budget_deadline=_parse_datetime(row["budget_deadline"]),
        resume_at=_parse_datetime(row["resume_at"]) if row["resume_at"] else None,
        recovery_attempts=int(row["recovery_attempts"]),
        recovery_state=row["recovery_state"],
        failure_category=RecoveryFailureCategory(row["failure_category"]),
        error_summary=row["error_summary"],
        report_path=row["report_path"],
    )


def _recovery_stage_from_row(row: sqlite3.Row) -> RecoveryStageRecord:
    """从 SQLite 行中解析阶段快照。"""
    return RecoveryStageRecord(
        stage_key=row["stage_key"],
        run_key=row["run_key"],
        stage=row["stage"],
        artifact_tree=row["artifact_tree"],
        base_sha=row["base_sha"],
        validation_digest=row["validation_digest"],
        status=row["status"],
        completed_at=_parse_datetime(row["completed_at"]),
    )
