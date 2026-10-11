"""管理终端 PRD 生命周期账本的 SQLite 适配。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


# 以下数据类与 core/shared/interfaces/runner_console.py 中的同名类型
# 结构一致（鸭子类型实现端口），infrastructure 层禁止导入 core。


@dataclass(frozen=True)
class PrdLifecycleRunRecord:
    """一次 PRD 生命周期的稳定身份与终态（与 core 同构）。"""

    run_id: str
    repo_id: str
    prd_path: str
    issue_number: int | None
    trigger: str
    started_at: str
    finished_at: str | None
    outcome: str | None
    history_complete: bool


@dataclass(frozen=True)
class PrdLifecycleEventRecord:
    """一条追加式生命周期事件（与 core 同构）。"""

    run_id: str
    event_key: str
    event_type: str
    phase: str
    actor: str
    occurred_at: str
    detail_json: str
    status: str = ""


class PrdLifecycleStoreMixin:
    """为 SQLite ConsoleStore 提供 PRD 生命周期账本适配。"""

    # ─────────────────────────────────────────────────────────────────────────
    # PRD 生命周期账本（IPrdLifecycleStore 鸭子类型实现）
    #
    # 与 run_records/attempt_records 同为旁路账本，但语义更强：run/event 是
    # 追加式不可变历史（event 只增不改），当前阶段与耗时由 event 聚合得出。

    def upsert_lifecycle_run(self, run_record: PrdLifecycleRunRecord) -> None:
        """创建或刷新一个 lifecycle run；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO prd_lifecycle_runs "
                "(run_id, repo_id, prd_path, issue_number, trigger, started_at, "
                " finished_at, outcome, history_complete) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "prd_path=CASE WHEN excluded.prd_path = '' "
                "THEN prd_lifecycle_runs.prd_path ELSE excluded.prd_path END, "
                "issue_number=COALESCE(excluded.issue_number, prd_lifecycle_runs.issue_number), "
                "finished_at=COALESCE(excluded.finished_at, prd_lifecycle_runs.finished_at), "
                "outcome=COALESCE(excluded.outcome, prd_lifecycle_runs.outcome), "
                "history_complete=CASE "
                "WHEN prd_lifecycle_runs.history_complete = 0 OR excluded.history_complete = 0 "
                "THEN 0 ELSE 1 END",
                (
                    run_record.run_id,
                    run_record.repo_id,
                    run_record.prd_path,
                    run_record.issue_number,
                    run_record.trigger,
                    run_record.started_at,
                    run_record.finished_at,
                    run_record.outcome,
                    int(run_record.history_complete),
                ),
            )
            connection.commit()

    def append_lifecycle_event(self, event_record: PrdLifecycleEventRecord) -> bool:
        """追加一条生命周期事件，按 ``(run_id, event_key)`` 幂等；失败时抛出异常。"""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO prd_lifecycle_events "
                "(run_id, event_key, event_type, phase, actor, occurred_at, detail_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_record.run_id,
                    event_record.event_key,
                    event_record.event_type,
                    event_record.phase,
                    event_record.actor,
                    event_record.occurred_at,
                    event_record.detail_json,
                    getattr(event_record, "status", ""),
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def mark_lifecycle_run_incomplete(self, run_id: str) -> None:
        """把某个 run 标记为观测历史不完整；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE prd_lifecycle_runs SET history_complete = 0 WHERE run_id = ?",
                (run_id,),
            )
            connection.commit()

    def finish_lifecycle_run(self, *, run_id: str, outcome: str, finished_at: str) -> None:
        """写入 run 终态；失败时抛出异常。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE prd_lifecycle_runs SET finished_at = ?, outcome = ? WHERE run_id = ?",
                (finished_at, outcome, run_id),
            )
            connection.commit()

    def reopen_lifecycle_run(self, run_id: str) -> None:
        """重开一个已收口的 run；失败时抛出异常。

        重试或解除阻塞后同一 run 继续累积事件，必须清掉 ``finished_at``，否则
        它早于后续事件时间，会让端到端与执行 / 等待 / 阻塞拆分互相矛盾。
        """
        with self._connect() as connection:
            connection.execute(
                "UPDATE prd_lifecycle_runs SET finished_at = NULL, outcome = NULL "
                "WHERE run_id = ? AND finished_at IS NOT NULL",
                (run_id,),
            )
            connection.commit()

    def get_lifecycle_run(self, run_id: str) -> PrdLifecycleRunRecord | None:
        """按 run id 读取单个 run；不存在时返回 ``None``。"""
        with self._connect() as connection:
            run_row = connection.execute(
                "SELECT run_id, repo_id, prd_path, issue_number, trigger, started_at, "
                "finished_at, outcome, history_complete "
                "FROM prd_lifecycle_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if run_row is None:
            return None
        return _row_to_lifecycle_run(run_row)

    def get_latest_lifecycle_run(
        self, *, repo_id: str, prd_path: str
    ) -> PrdLifecycleRunRecord | None:
        """按 ``repo_id + prd_path`` 读取最近一次 run；不存在时返回 ``None``。"""
        with self._connect() as connection:
            run_row = connection.execute(
                "SELECT run_id, repo_id, prd_path, issue_number, trigger, started_at, "
                "finished_at, outcome, history_complete "
                "FROM prd_lifecycle_runs WHERE repo_id = ? AND prd_path = ? "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (repo_id, prd_path),
            ).fetchone()
        if run_row is None:
            return None
        return _row_to_lifecycle_run(run_row)

    def list_lifecycle_events(self, *, run_id: str) -> list[PrdLifecycleEventRecord]:
        """按发生顺序列出某个 run 的全部事件。"""
        with self._connect() as connection:
            event_rows = connection.execute(
                "SELECT run_id, event_key, event_type, phase, actor, occurred_at, "
                "detail_json, status "
                "FROM prd_lifecycle_events WHERE run_id = ? "
                "ORDER BY occurred_at ASC, id ASC",
                (run_id,),
            ).fetchall()
        return [
            PrdLifecycleEventRecord(
                run_id=event_row["run_id"],
                event_key=event_row["event_key"],
                event_type=event_row["event_type"],
                phase=event_row["phase"],
                actor=event_row["actor"],
                occurred_at=event_row["occurred_at"],
                detail_json=event_row["detail_json"],
                status=event_row["status"],
            )
            for event_row in event_rows
        ]

    def list_lifecycle_runs(
        self, *, repo_id: str | None = None, since: str | None = None
    ) -> list[PrdLifecycleRunRecord]:
        """列出 run，可按仓库与 ``since`` 下界过滤，按 ``started_at`` 正序。"""
        query = (
            "SELECT run_id, repo_id, prd_path, issue_number, trigger, started_at, "
            "finished_at, outcome, history_complete FROM prd_lifecycle_runs"
        )
        conditions: list[str] = []
        params: list[object] = []
        if repo_id is not None:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if since is not None:
            conditions.append("started_at >= ?")
            params.append(since)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at ASC, rowid ASC"
        with self._connect() as connection:
            run_rows = connection.execute(query, params).fetchall()
        return [_row_to_lifecycle_run(run_row) for run_row in run_rows]

    def count_legacy_runs_without_lifecycle(
        self, *, repo_id: str | None = None, since: str | None = None
    ) -> int:
        """统计无法关联 lifecycle run 的旧 ``run_records`` 条数。"""
        query = (
            "SELECT COUNT(*) AS legacy_count FROM run_records AS rr "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM prd_lifecycle_runs AS lr "
            "WHERE lr.repo_id = rr.repo_id AND lr.issue_number = rr.issue_number"
            ")"
        )
        params: list[object] = []
        if repo_id is not None:
            query += " AND rr.repo_id = ?"
            params.append(repo_id)
        if since is not None:
            query += " AND rr.started_at >= ?"
            params.append(since)
        with self._connect() as connection:
            legacy_row = connection.execute(query, params).fetchone()
        return int(legacy_row["legacy_count"] or 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Backlog queue / settings (IBacklogStore duck-type implementation)
    # ─────────────────────────────────────────────────────────────────────────


def _row_to_lifecycle_run(run_row: sqlite3.Row) -> PrdLifecycleRunRecord:
    """把一条 ``prd_lifecycle_runs`` 行还原为同构 dataclass。"""
    return PrdLifecycleRunRecord(
        run_id=run_row["run_id"],
        repo_id=run_row["repo_id"],
        prd_path=run_row["prd_path"],
        issue_number=(
            int(run_row["issue_number"]) if run_row["issue_number"] is not None else None
        ),
        trigger=run_row["trigger"],
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        outcome=run_row["outcome"],
        history_complete=bool(run_row["history_complete"]),
    )
