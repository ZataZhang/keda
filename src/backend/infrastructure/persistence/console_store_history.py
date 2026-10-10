"""管理终端运行历史的 SQLite 读模型与查询适配。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunRecord:
    """一次 Issue 处理的运行结果（与 core 同构）。"""

    repo_id: str
    repo_path: str
    issue_number: int
    trigger: str
    agent: str
    outcome: str
    error_summary: str | None
    started_at: str
    finished_at: str
    duration_seconds: float


@dataclass(frozen=True)
class AttemptRecord:
    """一次 agent execution attempt 的本地记录（与 core 同构）。"""

    repo_id: str
    issue_number: int
    agent: str
    attempt_number: int
    failure_type: str
    recovered: bool
    detail: str
    started_at: str
    finished_at: str
    duration_seconds: float
    preset: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    """一次管理终端写操作的审计条目（与 core 同构）。"""

    occurred_at: str
    actor: str
    action: str
    repo_id: str | None
    issue_number: int | None
    params_json: str
    result: str
    detail: str | None


@dataclass(frozen=True)
class DailyRunTrendEntry:
    """运行历史按天聚合的一个数据点。"""

    day: str
    completed: int
    failed: int
    blocked: int
    average_duration_seconds: float | None


@dataclass(frozen=True)
class PerformanceAttemptRecord:
    """供 Agent 表现汇总使用的最小 attempt 行。"""

    repo_id: str
    agent: str
    failure_type: str
    duration_seconds: float
    preset: str | None
    model: str | None


@dataclass(frozen=True)
class PerformanceRunRecord:
    """供整项任务耗时汇总使用的最小 run 行。"""

    repo_id: str
    outcome: str
    duration_seconds: float


@dataclass(frozen=True)
class AgentPerformanceRecords:
    """一次窗口查询返回的 attempt 与 run 投影。"""

    attempts: tuple[PerformanceAttemptRecord, ...]
    runs: tuple[PerformanceRunRecord, ...]


class RunHistoryReadMixin:
    """为 SQLite console store 提供只读运行历史查询。"""

    def list_recent_runs(self, *, repo_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """倒序列出最近的运行记录。"""
        query = (
            "SELECT repo_id, repo_path, issue_number, trigger, agent, outcome, "
            "error_summary, started_at, finished_at, duration_seconds "
            "FROM run_records"
        )
        query_params: list[object] = []
        if repo_id is not None:
            query += " WHERE repo_id = ?"
            query_params.append(repo_id)
        query += " ORDER BY id DESC LIMIT ?"
        query_params.append(limit)
        with self._connect() as connection:
            record_rows = connection.execute(query, query_params).fetchall()
        return [
            RunRecord(
                repo_id=record_row["repo_id"],
                repo_path=record_row["repo_path"],
                issue_number=int(record_row["issue_number"]),
                trigger=record_row["trigger"],
                agent=record_row["agent"],
                outcome=record_row["outcome"],
                error_summary=record_row["error_summary"],
                started_at=record_row["started_at"],
                finished_at=record_row["finished_at"],
                duration_seconds=float(record_row["duration_seconds"]),
            )
            for record_row in record_rows
        ]

    def list_issue_attempts(
        self, *, repo_id: str, issue_number: int, limit: int = 100
    ) -> list[AttemptRecord]:
        """按时间正序列出某个 Issue 的 attempt 记录；失败时降级为空列表。"""
        try:
            with self._connect() as connection:
                attempt_rows = connection.execute(
                    "SELECT repo_id, issue_number, agent, attempt_number, failure_type, "
                    "recovered, detail, started_at, finished_at, duration_seconds, "
                    "preset, model "
                    "FROM attempt_records WHERE repo_id = ? AND issue_number = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (repo_id, issue_number, limit),
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 - 旁路历史读取失败不阻断 runner。
            _logger.warning("Failed to list attempt records from %s: %s", self._db_path, exc)
            return []
        return [
            AttemptRecord(
                repo_id=attempt_row["repo_id"],
                issue_number=int(attempt_row["issue_number"]),
                agent=attempt_row["agent"],
                attempt_number=int(attempt_row["attempt_number"]),
                failure_type=attempt_row["failure_type"],
                recovered=bool(attempt_row["recovered"]),
                detail=attempt_row["detail"],
                started_at=attempt_row["started_at"],
                finished_at=attempt_row["finished_at"],
                duration_seconds=float(attempt_row["duration_seconds"]),
                preset=attempt_row["preset"],
                model=attempt_row["model"],
            )
            for attempt_row in reversed(attempt_rows)
        ]

    def list_recent_audits(self, *, limit: int = 100) -> list[AuditEntry]:
        """倒序列出最近的审计条目。"""
        with self._connect() as connection:
            audit_rows = connection.execute(
                "SELECT occurred_at, actor, action, repo_id, issue_number, "
                "params_json, result, detail "
                "FROM audit_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditEntry(
                occurred_at=audit_row["occurred_at"],
                actor=audit_row["actor"],
                action=audit_row["action"],
                repo_id=audit_row["repo_id"],
                issue_number=(
                    int(audit_row["issue_number"])
                    if audit_row["issue_number"] is not None
                    else None
                ),
                params_json=audit_row["params_json"],
                result=audit_row["result"],
                detail=audit_row["detail"],
            )
            for audit_row in audit_rows
        ]

    def daily_run_trend(self, *, repo_id: str | None, days: int) -> list[DailyRunTrendEntry]:
        """按天聚合最近 ``days`` 天的运行结果。"""
        query = (
            "SELECT date(started_at) AS day, "
            "SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END) AS completed, "
            "SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN outcome = 'blocked' THEN 1 ELSE 0 END) AS blocked, "
            "AVG(duration_seconds) AS average_duration_seconds "
            "FROM run_records "
            "WHERE date(started_at) >= date('now', ?)"
        )
        query_params: list[object] = [f"-{max(days, 1)} days"]
        if repo_id is not None:
            query += " AND repo_id = ?"
            query_params.append(repo_id)
        query += " GROUP BY day ORDER BY day ASC"
        with self._connect() as connection:
            trend_rows = connection.execute(query, query_params).fetchall()
        return [
            DailyRunTrendEntry(
                day=trend_row["day"],
                completed=int(trend_row["completed"] or 0),
                failed=int(trend_row["failed"] or 0),
                blocked=int(trend_row["blocked"] or 0),
                average_duration_seconds=(
                    float(trend_row["average_duration_seconds"])
                    if trend_row["average_duration_seconds"] is not None
                    else None
                ),
            )
            for trend_row in trend_rows
        ]

    def list_agent_performance_records(
        self, *, repo_id: str | None, since: str
    ) -> AgentPerformanceRecords:
        """读取统计窗口内必要字段，不加载长 attempt 详情。"""
        repo_clause = " AND repo_id = ?" if repo_id is not None else ""
        query_params: list[object] = [since]
        if repo_id is not None:
            query_params.append(repo_id)
        with self._connect() as connection:
            attempt_rows = connection.execute(
                "SELECT repo_id, agent, failure_type, duration_seconds, preset, model "
                "FROM attempt_records WHERE started_at >= ?" + repo_clause,
                query_params,
            ).fetchall()
            run_rows = connection.execute(
                "SELECT repo_id, outcome, duration_seconds FROM run_records "
                "WHERE started_at >= ? AND outcome IN ('completed', 'failed', 'blocked')"
                + repo_clause,
                query_params,
            ).fetchall()
        return AgentPerformanceRecords(
            attempts=tuple(
                PerformanceAttemptRecord(
                    repo_id=attempt_row["repo_id"],
                    agent=attempt_row["agent"],
                    failure_type=attempt_row["failure_type"],
                    duration_seconds=float(attempt_row["duration_seconds"]),
                    preset=attempt_row["preset"],
                    model=attempt_row["model"],
                )
                for attempt_row in attempt_rows
            ),
            runs=tuple(
                PerformanceRunRecord(
                    repo_id=run_row["repo_id"],
                    outcome=run_row["outcome"],
                    duration_seconds=float(run_row["duration_seconds"]),
                )
                for run_row in run_rows
            ),
        )
