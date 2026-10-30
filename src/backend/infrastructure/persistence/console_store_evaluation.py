"""Console SQLite 账本的模型评测 mixin（schema v16）。

三张独立表 ``evaluation_run`` / ``evaluation_result`` / ``evaluation_score``
只承载评测事实与配置快照：不写 API key、不写原始工具 transcript、
不写完整 diff（diff 只进临时暂存文件，库里留 hash）。

依赖方向：基础设施层不得 import core，因此本模块自带与 core 侧
``model_evaluation`` 记录**同字段名**的行 dataclass；engines 层负责在
端口边界做 ``dataclasses.asdict`` 形状的转换。字段漂移由
``tests/test_console_evaluation_store.py`` 的镜像断言捕获。

级联删除在代码层显式执行（score → result → run），不依赖每个连接都要打开
的 ``PRAGMA foreign_keys``；外键声明只作为数据图注释保留。评测是后台线程
编排：候选/judge 单元可能仍持有已删除 run 的内存记录并回写，因此 result 与
score 的写入用 ``INSERT ... SELECT ... WHERE EXISTS`` 原子绑定 run 存在性，
run 已删除时写入被静默丢弃，不留孤儿行。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

CREATE_EVALUATION_RUN = """
CREATE TABLE IF NOT EXISTS evaluation_run (
    run_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    base_tree_hash TEXT NOT NULL,
    execution_preset_snapshots_json TEXT NOT NULL,
    judge_preset_snapshots_json TEXT NOT NULL,
    status TEXT NOT NULL,
    manifest_hash TEXT,
    case_ids_json TEXT NOT NULL,
    check_timeout_seconds INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    repeats INTEGER NOT NULL DEFAULT 1,
    case_bases_json TEXT NOT NULL DEFAULT '{}'
)
"""

CREATE_EVALUATION_RESULT_V16 = """
CREATE TABLE IF NOT EXISTS evaluation_result (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_run(run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    execution_preset_id TEXT NOT NULL,
    execution_model_snapshot_json TEXT NOT NULL,
    base_tree_hash TEXT NOT NULL,
    worktree_path TEXT,
    result_status TEXT NOT NULL,
    check_status TEXT NOT NULL,
    check_recipe TEXT NOT NULL,
    check_exit_code INTEGER,
    check_output_summary TEXT,
    agent_summary TEXT,
    duration_seconds REAL,
    usage_summary_json TEXT,
    cost_status TEXT NOT NULL DEFAULT 'unknown',
    diff_hash TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    repeat_index INTEGER NOT NULL DEFAULT 0,
    UNIQUE (run_id, case_id, execution_preset_id, repeat_index)
)
"""

CREATE_EVALUATION_RESULT = CREATE_EVALUATION_RESULT_V16

CREATE_EVALUATION_SCORE = """
CREATE TABLE IF NOT EXISTS evaluation_score (
    score_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    result_id TEXT NOT NULL REFERENCES evaluation_result(result_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    execution_preset_id TEXT NOT NULL,
    judge_preset_id TEXT NOT NULL,
    judge_model_snapshot_json TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    score_status TEXT NOT NULL,
    dimension_scores_json TEXT NOT NULL,
    rationale_summary TEXT,
    response_hash TEXT,
    duration_seconds REAL,
    usage_summary_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (result_id, judge_preset_id)
)
"""

CREATE_EVALUATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_evaluation_run_status ON evaluation_run (status)",
    "CREATE INDEX IF NOT EXISTS idx_evaluation_result_run ON evaluation_result (run_id, case_id)",
    "CREATE INDEX IF NOT EXISTS idx_evaluation_score_run ON evaluation_score (run_id, result_id)",
)

#: 启动自愈要收敛的活跃状态闭集，必须与 core 的 ACTIVE_RUN_STATUSES 一致
#: （镜像断言见 tests/test_console_evaluation_store.py）。
_INTERRUPTIBLE_STATUSES = ("created", "queued", "running", "awaiting_judge_consent", "judging")


class EvaluationStoreConflictError(RuntimeError):
    """run 状态比较并交换失败：当前状态与调用方预期不符。"""


# 基础设施层不能 import core，行模型因此必须镜像 core 记录；字段漂移由测试断言捕获。
# jscpd:ignore-start
@dataclass(frozen=True)
class EvaluationRunRow:
    """``evaluation_run`` 行的镜像 dataclass（字段与 core 记录一致）。"""

    run_id: str
    suite_id: str
    suite_version: str
    suite_hash: str
    rubric_version: str
    repo_path: str
    base_ref: str
    base_commit: str
    base_tree_hash: str
    execution_preset_snapshots: list[dict[str, Any]]
    judge_preset_snapshots: list[dict[str, Any]]
    status: str
    manifest_hash: str | None
    case_ids: list[str]
    check_timeout_seconds: int
    error: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    repeats: int
    case_bases: dict[str, dict[str, str]]


@dataclass(frozen=True)
class EvaluationResultRow:
    """``evaluation_result`` 行的镜像 dataclass。"""

    result_id: str
    run_id: str
    case_id: str
    execution_preset_id: str
    execution_model_snapshot: dict[str, Any]
    base_tree_hash: str
    worktree_path: str | None
    result_status: str
    check_status: str
    check_recipe: str
    check_exit_code: int | None
    check_output_summary: str | None
    agent_summary: str | None
    duration_seconds: float | None
    usage_summary: dict[str, Any] | None
    cost_status: str
    diff_hash: str | None
    error: str | None
    created_at: str
    updated_at: str
    repeat_index: int


# jscpd:ignore-end
@dataclass(frozen=True)
class EvaluationScoreRow:
    """``evaluation_score`` 行的镜像 dataclass。"""

    score_id: str
    run_id: str
    result_id: str
    case_id: str
    execution_preset_id: str
    judge_preset_id: str
    judge_model_snapshot: dict[str, Any]
    rubric_version: str
    score_status: str
    dimension_scores: dict[str, int]
    rationale_summary: str | None
    response_hash: str | None
    duration_seconds: float | None
    usage_summary: dict[str, Any] | None
    error: str | None
    created_at: str


class EvaluationStoreMixin:
    """``IEvaluationStore`` 的 SQLite 实现（鸭子类型，不 import core）。"""

    def save_evaluation_run(self, run: Any) -> None:
        """插入或整体覆盖一个 run 快照行（入参为 core 或镜像记录均可）。"""
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO evaluation_run ("
                " run_id, suite_id, suite_version, suite_hash, rubric_version,"
                " repo_path, base_ref, base_commit, base_tree_hash,"
                " execution_preset_snapshots_json, judge_preset_snapshots_json,"
                " status, manifest_hash, case_ids_json, check_timeout_seconds,"
                " error, created_at, updated_at, started_at, finished_at, repeats, case_bases_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.suite_id,
                    run.suite_version,
                    run.suite_hash,
                    run.rubric_version,
                    run.repo_path,
                    run.base_ref,
                    run.base_commit,
                    run.base_tree_hash,
                    json.dumps(run.execution_preset_snapshots, ensure_ascii=False),
                    json.dumps(run.judge_preset_snapshots, ensure_ascii=False),
                    run.status,
                    run.manifest_hash,
                    json.dumps(run.case_ids, ensure_ascii=False),
                    run.check_timeout_seconds,
                    run.error,
                    run.created_at,
                    run.updated_at,
                    run.started_at,
                    run.finished_at,
                    run.repeats,
                    json.dumps(run.case_bases, ensure_ascii=False),
                ),
            )

    def get_evaluation_run(self, run_id: str) -> EvaluationRunRow | None:
        """按 id 读取 run；不存在返回 ``None``。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_run WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def list_evaluation_runs(self) -> list[EvaluationRunRow]:
        """按创建时间倒序列出全部 run。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_run ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_evaluation_run_status(
        self,
        run_id: str,
        *,
        expected_statuses: Sequence[str],
        new_status: str,
        manifest_hash: str | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> EvaluationRunRow:
        """run 状态比较并交换（CAS）；来源状态不匹配时抛冲突错误。"""
        if not expected_statuses:
            raise EvaluationStoreConflictError("expected_statuses 不能为空")
        placeholders = ", ".join("?" for _ in expected_statuses)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[object] = [new_status, _now_iso()]
        if manifest_hash is not None:
            assignments.append("manifest_hash = ?")
            params.append(manifest_hash)
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if finished:
            assignments.append("finished_at = ?")
            params.append(_now_iso())
        if new_status == "running":
            assignments.append("started_at = COALESCE(started_at, ?)")
            params.append(_now_iso())
        params.append(run_id)
        params.extend(expected_statuses)
        # 列名与占位符全部来自内部常量，不拼接任何外部输入。
        statement = (
            f"UPDATE evaluation_run SET {', '.join(assignments)} "
            f"WHERE run_id = ? AND status IN ({placeholders})"
        )
        with self._connection() as connection:
            cursor = connection.execute(statement, params)
            if cursor.rowcount != 1:
                current = connection.execute(
                    "SELECT status FROM evaluation_run WHERE run_id = ?", (run_id,)
                ).fetchone()
                raise EvaluationStoreConflictError(
                    f"evaluation run '{run_id}' 状态冲突："
                    f"当前 {current['status'] if current else '不存在'}，"
                    f"预期 {list(expected_statuses)} -> {new_status}"
                )
            row = connection.execute(
                "SELECT * FROM evaluation_run WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_run(row)

    def save_evaluation_result(self, result: Any) -> None:
        """插入或整体覆盖一个候选结果行；所属 run 已删除时静默丢弃该写入。

        后台执行线程可能持有已被「强制删除」的 run 的内存记录，写入用
        ``WHERE EXISTS`` 与 run 存在性原子绑定，避免删除后残留孤儿行。
        """
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO evaluation_result ("
                " result_id, run_id, case_id, execution_preset_id,"
                " execution_model_snapshot_json, base_tree_hash, worktree_path,"
                " result_status, check_status, check_recipe, check_exit_code,"
                " check_output_summary, agent_summary, duration_seconds,"
                " usage_summary_json, cost_status, diff_hash, error,"
                " created_at, updated_at, repeat_index)"
                " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                " WHERE EXISTS (SELECT 1 FROM evaluation_run WHERE run_id = ?) "
                "ON CONFLICT(result_id) DO UPDATE SET "
                "run_id = excluded.run_id, case_id = excluded.case_id, "
                "execution_preset_id = excluded.execution_preset_id, "
                "execution_model_snapshot_json = excluded.execution_model_snapshot_json, "
                "base_tree_hash = excluded.base_tree_hash, worktree_path = excluded.worktree_path, "
                "result_status = excluded.result_status, check_status = excluded.check_status, "
                "check_recipe = excluded.check_recipe, check_exit_code = excluded.check_exit_code, "
                "check_output_summary = excluded.check_output_summary, "
                "agent_summary = excluded.agent_summary, duration_seconds = excluded.duration_seconds, "
                "usage_summary_json = excluded.usage_summary_json, cost_status = excluded.cost_status, "
                "diff_hash = excluded.diff_hash, error = excluded.error, "
                "created_at = excluded.created_at, updated_at = excluded.updated_at, "
                "repeat_index = excluded.repeat_index",
                (
                    result.result_id,
                    result.run_id,
                    result.case_id,
                    result.execution_preset_id,
                    json.dumps(result.execution_model_snapshot, ensure_ascii=False),
                    result.base_tree_hash,
                    result.worktree_path,
                    result.result_status,
                    result.check_status,
                    result.check_recipe,
                    result.check_exit_code,
                    result.check_output_summary,
                    result.agent_summary,
                    result.duration_seconds,
                    json.dumps(result.usage_summary, ensure_ascii=False)
                    if result.usage_summary is not None
                    else None,
                    result.cost_status,
                    result.diff_hash,
                    result.error,
                    result.created_at,
                    result.updated_at,
                    result.repeat_index,
                    result.run_id,
                ),
            )

    def get_evaluation_result(self, result_id: str) -> EvaluationResultRow | None:
        """按 id 读取候选结果；不存在返回 ``None``。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_result WHERE result_id = ?", (result_id,)
            ).fetchone()
        return self._row_to_result(row) if row is not None else None

    def list_evaluation_results(self, run_id: str) -> list[EvaluationResultRow]:
        """列出某个 run 的全部候选结果（按 case/preset 稳定排序）。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_result WHERE run_id = ? "
                "ORDER BY case_id, execution_preset_id, repeat_index",
                (run_id,),
            ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def save_evaluation_score(self, score: Any) -> None:
        """插入或覆盖一条逐评审评分行（同 result × judge 幂等覆盖）。

        与候选结果同理：所属 run 已被删除（含 judging 期间的强制删除）时，
        在飞的评审线程回写被静默丢弃，不留孤儿行。
        """
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO evaluation_score ("
                " score_id, run_id, result_id, case_id, execution_preset_id,"
                " judge_preset_id, judge_model_snapshot_json, rubric_version,"
                " score_status, dimension_scores_json, rationale_summary,"
                " response_hash, duration_seconds, usage_summary_json, error, created_at)"
                " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                " WHERE EXISTS (SELECT 1 FROM evaluation_run WHERE run_id = ?)",
                (
                    score.score_id,
                    score.run_id,
                    score.result_id,
                    score.case_id,
                    score.execution_preset_id,
                    score.judge_preset_id,
                    json.dumps(score.judge_model_snapshot, ensure_ascii=False),
                    score.rubric_version,
                    score.score_status,
                    json.dumps(score.dimension_scores, ensure_ascii=False),
                    score.rationale_summary,
                    score.response_hash,
                    score.duration_seconds,
                    json.dumps(score.usage_summary, ensure_ascii=False)
                    if score.usage_summary is not None
                    else None,
                    score.error,
                    score.created_at,
                    score.run_id,
                ),
            )

    def list_evaluation_scores(self, run_id: str) -> list[EvaluationScoreRow]:
        """列出某个 run 的全部评分行（含全部非 valid 状态）。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_score WHERE run_id = ? "
                "ORDER BY result_id, judge_preset_id",
                (run_id,),
            ).fetchall()
        return [self._row_to_score(row) for row in rows]

    def delete_evaluation_run(self, run_id: str) -> EvaluationRunRow | None:
        """级联删除 run 的评分、候选结果与 run 行本身；返回被删除的 run。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM evaluation_score WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM evaluation_result WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM evaluation_run WHERE run_id = ?", (run_id,))
        return self._row_to_run(row)

    def mark_interrupted_evaluation_runs(self, interrupted_at: str) -> int:
        """把所有活跃状态 run 标为 interrupted（Console 启动自愈）。"""
        placeholders = ", ".join("?" for _ in _INTERRUPTIBLE_STATUSES)
        statement = (
            "UPDATE evaluation_run SET status = ?, updated_at = ?, error = ? "
            f"WHERE status IN ({placeholders})"
        )
        with self._connection() as connection:
            cursor = connection.execute(
                statement,
                [
                    "interrupted",
                    interrupted_at,
                    "Console 退出或崩溃时该 run 仍在进行；不支持续跑，请新建 run 重跑或删除",
                    *_INTERRUPTIBLE_STATUSES,
                ],
            )
            return int(cursor.rowcount)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> EvaluationRunRow:
        return EvaluationRunRow(
            run_id=row["run_id"],
            suite_id=row["suite_id"],
            suite_version=row["suite_version"],
            suite_hash=row["suite_hash"],
            rubric_version=row["rubric_version"],
            repo_path=row["repo_path"],
            base_ref=row["base_ref"],
            base_commit=row["base_commit"],
            base_tree_hash=row["base_tree_hash"],
            execution_preset_snapshots=json.loads(row["execution_preset_snapshots_json"]),
            judge_preset_snapshots=json.loads(row["judge_preset_snapshots_json"]),
            status=row["status"],
            manifest_hash=row["manifest_hash"],
            case_ids=json.loads(row["case_ids_json"]),
            check_timeout_seconds=int(row["check_timeout_seconds"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            repeats=int(row["repeats"]),
            case_bases=json.loads(row["case_bases_json"]),
        )

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> EvaluationResultRow:
        usage_raw = row["usage_summary_json"]
        return EvaluationResultRow(
            result_id=row["result_id"],
            run_id=row["run_id"],
            case_id=row["case_id"],
            execution_preset_id=row["execution_preset_id"],
            execution_model_snapshot=json.loads(row["execution_model_snapshot_json"]),
            base_tree_hash=row["base_tree_hash"],
            worktree_path=row["worktree_path"],
            result_status=row["result_status"],
            check_status=row["check_status"],
            check_recipe=row["check_recipe"],
            check_exit_code=row["check_exit_code"],
            check_output_summary=row["check_output_summary"],
            agent_summary=row["agent_summary"],
            duration_seconds=row["duration_seconds"],
            usage_summary=json.loads(usage_raw) if usage_raw else None,
            cost_status=row["cost_status"],
            diff_hash=row["diff_hash"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            repeat_index=int(row["repeat_index"]),
        )

    @staticmethod
    def _row_to_score(row: sqlite3.Row) -> EvaluationScoreRow:
        usage_raw = row["usage_summary_json"]
        dimension_raw = row["dimension_scores_json"]
        return EvaluationScoreRow(
            score_id=row["score_id"],
            run_id=row["run_id"],
            result_id=row["result_id"],
            case_id=row["case_id"],
            execution_preset_id=row["execution_preset_id"],
            judge_preset_id=row["judge_preset_id"],
            judge_model_snapshot=json.loads(row["judge_model_snapshot_json"]),
            rubric_version=row["rubric_version"],
            score_status=row["score_status"],
            dimension_scores=json.loads(dimension_raw) if dimension_raw else {},
            rationale_summary=row["rationale_summary"],
            response_hash=row["response_hash"],
            duration_seconds=row["duration_seconds"],
            usage_summary=json.loads(usage_raw) if usage_raw else None,
            error=row["error"],
            created_at=row["created_at"],
        )


def _now_iso() -> str:
    """UTC ISO-8601 时间戳（机器可读，保持偏移标记）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
