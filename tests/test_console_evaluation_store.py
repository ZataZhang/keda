"""Console SQLite 评测三表（v15）的行为测试：迁移、往返、CAS、级联删除与自愈。"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from backend.core.shared.models.model_evaluation import (
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
)
from backend.infrastructure.persistence.console_store import (
    _SCHEMA_VERSION,
    SqliteConsoleStore,
)
from backend.infrastructure.persistence.console_store_evaluation import (
    EvaluationStoreConflictError,
)


def _run(run_id: str = "eval-a", **overrides: object) -> EvaluationRunRecord:
    fields: dict[str, object] = {
        "run_id": run_id,
        "suite_id": "demo",
        "suite_version": "1.0.0",
        "suite_hash": "sha",
        "rubric_version": "v1",
        "repo_path": "/tmp/repo",
        "base_ref": "main",
        "base_commit": "abc",
        "base_tree_hash": "tree",
        "execution_preset_snapshots": [{"preset_name": "p1"}],
        "judge_preset_snapshots": [{"preset_name": "j1"}],
        "status": EvaluationRunStatus.created.value,
        "manifest_hash": None,
        "case_ids": ["case-a"],
        "check_timeout_seconds": 60,
        "error": None,
        "created_at": "2026-10-13T00:00:00+00:00",
        "updated_at": "2026-10-13T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
    }
    fields.update(overrides)
    return EvaluationRunRecord(**fields)  # type: ignore[arg-type]


def _result(
    result_id: str = "res-a",
    run_id: str = "eval-a",
    **overrides: object,
) -> EvaluationResultRecord:
    fields: dict[str, object] = {
        "result_id": result_id,
        "run_id": run_id,
        "case_id": "case-a",
        "execution_preset_id": "p1",
        "execution_model_snapshot": {"preset_name": "p1"},
        "base_tree_hash": "tree",
        "worktree_path": None,
        "result_status": EvaluationResultStatus.succeeded.value,
        "check_status": EvaluationCheckStatus.passed.value,
        "check_recipe": "pytest",
        "check_exit_code": 0,
        "check_output_summary": "1 passed",
        "agent_summary": "done",
        "duration_seconds": 1.5,
        "usage_summary": {"total_tokens": 10},
        "cost_status": "unknown",
        "diff_hash": "diff",
        "error": None,
        "created_at": "2026-10-13T00:00:00+00:00",
        "updated_at": "2026-10-13T00:00:00+00:00",
    }
    fields.update(overrides)
    return EvaluationResultRecord(**fields)  # type: ignore[arg-type]


def _score(
    score_id: str = "score-a",
    run_id: str = "eval-a",
    result_id: str = "res-a",
) -> EvaluationScoreRecord:
    return EvaluationScoreRecord(
        score_id=score_id,
        run_id=run_id,
        result_id=result_id,
        case_id="case-a",
        execution_preset_id="p1",
        judge_preset_id="j1",
        judge_model_snapshot={"preset_name": "j1"},
        rubric_version="v1",
        score_status=EvaluationScoreStatus.valid.value,
        dimension_scores={"correctness": 4},
        rationale_summary="ok",
        response_hash="resp",
        duration_seconds=0.5,
        usage_summary=None,
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )


def test_fresh_database_migrates_to_v15_with_evaluation_tables(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    assert store.get_evaluation_run("eval-a") is not None
    with sqlite3.connect(tmp_path / "console.db") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert version == _SCHEMA_VERSION == 15
    assert {"evaluation_run", "evaluation_result", "evaluation_score"} <= tables


def test_legacy_database_upgrades_and_keeps_history(tmp_path: Path) -> None:
    db_path = tmp_path / "console.db"
    SqliteConsoleStore(db_path)  # 建出最新 schema
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 14")
        connection.execute("DROP TABLE evaluation_score")
        connection.execute("DROP TABLE evaluation_result")
        connection.execute("DROP TABLE evaluation_run")
    store = SqliteConsoleStore(db_path)
    assert store.get_evaluation_run("eval-a") is None  # 表已重建
    store.save_evaluation_run(_run())
    assert store.get_evaluation_run("eval-a") is not None


def test_run_round_trip_preserves_json_columns(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    loaded = store.get_evaluation_run("eval-a")
    assert loaded is not None
    assert loaded.execution_preset_snapshots == [{"preset_name": "p1"}]
    assert loaded.case_ids == ["case-a"]
    assert loaded.status == EvaluationRunStatus.created.value


def test_cas_update_flips_status_and_sets_timestamps(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    running = store.update_evaluation_run_status(
        "eval-a",
        expected_statuses=[EvaluationRunStatus.created.value],
        new_status=EvaluationRunStatus.running.value,
    )
    assert running.status == "running"
    assert running.started_at is not None
    finished = store.update_evaluation_run_status(
        "eval-a",
        expected_statuses=[EvaluationRunStatus.running.value],
        new_status=EvaluationRunStatus.completed.value,
        finished=True,
    )
    assert finished.finished_at is not None


def test_cas_conflict_raises_and_keeps_row(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    with pytest.raises(EvaluationStoreConflictError):
        store.update_evaluation_run_status(
            "eval-a",
            expected_statuses=[EvaluationRunStatus.running.value],
            new_status=EvaluationRunStatus.cancelled.value,
        )
    assert store.get_evaluation_run("eval-a").status == "created"
    with pytest.raises(EvaluationStoreConflictError):
        store.update_evaluation_run_status(
            "eval-missing",
            expected_statuses=[EvaluationRunStatus.created.value],
            new_status=EvaluationRunStatus.running.value,
        )


def test_manifest_hash_frozen_on_awaiting_consent(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run(status="running"))
    updated = store.update_evaluation_run_status(
        "eval-a",
        expected_statuses=["running"],
        new_status=EvaluationRunStatus.awaiting_judge_consent.value,
        manifest_hash="frozen-hash",
    )
    assert updated.manifest_hash == "frozen-hash"


def test_results_and_scores_round_trip_and_list(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    store.save_evaluation_result(_result("res-a"))
    store.save_evaluation_result(_result("res-b", execution_preset_id="p2"))
    store.save_evaluation_score(_score("score-a", result_id="res-a"))
    listed_results = store.list_evaluation_results("eval-a")
    assert {row.result_id for row in listed_results} == {"res-a", "res-b"}
    listed_scores = store.list_evaluation_scores("eval-a")
    assert [row.score_id for row in listed_scores] == ["score-a"]
    # 失败状态原样读回，不被任何默认值吞掉。
    store.save_evaluation_score(
        dataclasses.replace(
            _score("score-timeout", result_id="res-b"),
            score_status=EvaluationScoreStatus.timeout.value,
            dimension_scores={},
        )
    )
    timeout_row = [
        row for row in store.list_evaluation_scores("eval-a") if row.score_id == "score-timeout"
    ][0]
    assert timeout_row.score_status == "timeout"
    assert timeout_row.dimension_scores == {}


def test_delete_run_cascades(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run())
    store.save_evaluation_result(_result())
    store.save_evaluation_score(_score())
    removed = store.delete_evaluation_run("eval-a")
    assert removed is not None and removed.run_id == "eval-a"
    assert store.list_evaluation_results("eval-a") == []
    assert store.list_evaluation_scores("eval-a") == []
    assert store.delete_evaluation_run("eval-a") is None


def test_mark_interrupted_only_touches_active_runs(tmp_path: Path) -> None:
    store = SqliteConsoleStore(tmp_path / "console.db")
    store.save_evaluation_run(_run("eval-1", status="queued"))
    store.save_evaluation_run(_run("eval-2", status="running"))
    store.save_evaluation_run(_run("eval-3", status="awaiting_judge_consent"))
    store.save_evaluation_run(_run("eval-4", status="judging"))
    store.save_evaluation_run(_run("eval-5", status="completed"))
    store.save_evaluation_run(_run("eval-6", status="cancelled"))
    affected = store.mark_interrupted_evaluation_runs("2026-10-13T01:00:00+00:00")
    assert affected == 4
    assert store.get_evaluation_run("eval-1").status == "interrupted"
    assert store.get_evaluation_run("eval-5").status == "completed"
    assert store.get_evaluation_run("eval-6").status == "cancelled"
