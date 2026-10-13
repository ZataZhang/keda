"""模型评测 Console API 路由行为测试。

链路走真实的 ``route → core facade → engines service → SQLite store``，
只把外部边界（git 基线探针、后台执行/评审排程、judge 凭据探针、
payload 暂存文件）替换为可观测的桩，验证 HTTP 语义映射与授权红线：
未 approve 之前绝不发起评审排程，hash 不符一律 409。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.core.shared.models.model_evaluation import (
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunStatus,
    EvaluationScoreStatus,
)
from backend.engines.model_evaluation import execution, judge_client
from backend.engines.model_evaluation import service as evaluation_service
from backend.engines.model_evaluation import workspace
from backend.infrastructure.config.agent_runner_settings_models import (
    AgentRunnerEvaluationSettings,
    AgentRunnerPresetSettings,
    EvaluationJudgePresetSettings,
)
from backend.infrastructure.config.settings import AgentRunnerSettings
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

client = TestClient(app)

API_BASE = "/api/v1/agent-runner/console/evaluation"

SUITE_TEXT = """
id: demo-suite
version: 1.0.0
title: 演示评测集
repo_path: /tmp/repo
base_ref: main
check:
  timeout_seconds: 60
cases:
  - id: case-a
    prompt: 实现 FizzBuzz
    check_recipe: pytest
"""

_STAGED_MANIFEST = {
    "entries": [
        {
            "result_id": "res-a",
            "payload_file": "res-a.json",
            "payload_sha256": "payload-hash-a",
        }
    ]
}
_FROZEN_HASH = "frozen-manifest-hash"


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把评测 service 接到 tmp store / 假配置 / 记录型排程桩。"""
    console_store = SqliteConsoleStore(tmp_path / "console.db")
    project_root = tmp_path / "repo-root"
    suites_dir = project_root / "tasks" / "evaluation-suites"
    suites_dir.mkdir(parents=True)
    (suites_dir / "demo.yaml").write_text(SUITE_TEXT, encoding="utf-8")

    settings = AgentRunnerSettings(
        presets={
            "alpha": AgentRunnerPresetSettings(agent="codex", model="gpt-alpha"),
            "beta": AgentRunnerPresetSettings(agent="claude", model="claude-beta"),
        },
        evaluation=AgentRunnerEvaluationSettings(
            judge_presets={
                "judge-1": EvaluationJudgePresetSettings(model="gpt-judge"),
            },
            default_judge_presets=["judge-1"],
        ),
    )
    scheduled: dict[str, list[str]] = {"execution": [], "judging": []}
    monkeypatch.setattr(evaluation_service, "create_console_store", lambda: console_store)
    monkeypatch.setattr(evaluation_service, "load_fresh_agent_runner_settings", lambda: settings)
    monkeypatch.setattr(evaluation_service, "resolve_project_root_path", lambda: project_root)
    monkeypatch.setattr(
        evaluation_service,
        "_probe_judge",
        lambda preset: judge_client.JudgeAvailability(
            available=True, provider="openai", model="gpt-judge", reason=None
        ),
    )
    monkeypatch.setattr(
        workspace, "probe_base", lambda repo_path, base_ref: ("deadbeef", "tree-hash")
    )
    monkeypatch.setattr(
        execution, "schedule_run_execution", lambda run_id: scheduled["execution"].append(run_id)
    )
    monkeypatch.setattr(
        execution, "schedule_run_judging", lambda run_id: scheduled["judging"].append(run_id)
    )
    monkeypatch.setattr(execution, "purge_run_artifacts", lambda run_id: None)
    monkeypatch.setattr(execution, "purge_run_worktrees", lambda run: None)
    monkeypatch.setattr(execution, "read_manifest", lambda run_id: dict(_STAGED_MANIFEST))
    monkeypatch.setattr(
        execution, "read_staged_payload", lambda run_id, payload_file: '{"diff": "staged"}'
    )
    monkeypatch.setattr(execution, "current_manifest_hash", lambda run_id: _FROZEN_HASH)
    return SimpleNamespace(store=console_store, scheduled=scheduled)


def _created_run_id(harness: Any) -> str:
    response = client.post(
        f"{API_BASE}/runs",
        json={
            "suite_id": "demo-suite",
            "execution_presets": ["alpha"],
            "judge_presets": ["judge-1"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def _advance_to_awaiting(harness: Any, run_id: str) -> None:
    harness.store.update_evaluation_run_status(
        run_id,
        expected_statuses=[EvaluationRunStatus.created.value],
        new_status=EvaluationRunStatus.awaiting_judge_consent.value,
        manifest_hash=_FROZEN_HASH,
    )


def _seed_result(store: SqliteConsoleStore, run_id: str) -> None:
    store.save_evaluation_result(
        EvaluationResultRecord(
            result_id="res-a",
            run_id=run_id,
            case_id="case-a",
            execution_preset_id="alpha",
            execution_model_snapshot={"preset_name": "alpha"},
            base_tree_hash="tree-hash",
            worktree_path=None,
            result_status=EvaluationResultStatus.succeeded.value,
            check_status=EvaluationCheckStatus.passed.value,
            check_recipe="pytest",
            check_exit_code=0,
            check_output_summary="1 passed",
            agent_summary=None,
            duration_seconds=1.0,
            usage_summary=None,
            cost_status="unknown",
            diff_hash="diff",
            error=None,
            created_at="2026-10-13T00:00:00+00:00",
            updated_at="2026-10-13T00:00:00+00:00",
        )
    )


# ── 清单与目录 ──────────────────────────────────────────────────────────────


def test_list_suites_returns_validated_entries(harness: Any) -> None:
    response = client.get(f"{API_BASE}/suites")
    assert response.status_code == 200
    (entry,) = response.json()["suites"]
    assert entry["valid"] is True
    assert entry["suite_id"] == "demo-suite"
    assert len(entry["cases"]) == 1


def test_list_presets_exposes_execution_and_available_judges(harness: Any) -> None:
    response = client.get(f"{API_BASE}/presets")
    assert response.status_code == 200
    payload = response.json()
    # 本机 config.toml 的真实预设会被 pydantic-settings 合并进清单，只断言注入项在场。
    assert {"alpha", "beta"} <= {item["name"] for item in payload["execution_presets"]}
    judge = next(item for item in payload["judge_presets"] if item["name"] == "judge-1")
    assert judge["available"] is True
    assert "judge-1" in payload["default_judge_presets"]


# ── run 生命周期 ────────────────────────────────────────────────────────────


def test_create_then_start_schedules_execution_only(harness: Any) -> None:
    run_id = _created_run_id(harness)
    assert harness.store.get_evaluation_run(run_id).status == "created"
    assert harness.scheduled == {"execution": [], "judging": []}

    response = client.post(f"{API_BASE}/runs/{run_id}/start")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert harness.scheduled["execution"] == [run_id]
    assert harness.scheduled["judging"] == []


def test_create_run_maps_validation_failures_to_http_semantics(harness: Any) -> None:
    unknown_suite = client.post(
        f"{API_BASE}/runs",
        json={"suite_id": "ghost", "execution_presets": ["alpha"]},
    )
    assert unknown_suite.status_code == 404
    unknown_preset = client.post(
        f"{API_BASE}/runs",
        json={"suite_id": "demo-suite", "execution_presets": ["nope"]},
    )
    assert unknown_preset.status_code == 400
    empty_presets = client.post(
        f"{API_BASE}/runs",
        json={"suite_id": "demo-suite", "execution_presets": []},
    )
    assert empty_presets.status_code == 422


def test_run_detail_contains_summary_and_payload_state(harness: Any) -> None:
    run_id = _created_run_id(harness)
    _seed_result(harness.store, run_id)
    response = client.get(f"{API_BASE}/runs/{run_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["run"]["run_id"] == run_id
    assert [row["result_id"] for row in detail["results"]] == ["res-a"]
    assert detail["summary"]["valid_judge_count"] == 0
    assert all(item["median"] is None for item in detail["summary"]["aggregates"])
    assert detail["payload_state"]["is_active"] is True
    assert detail["payload_state"]["awaiting_consent"] is False
    assert detail["payload_state"]["judge_selected"] is True
    missing = client.get(f"{API_BASE}/runs/eval-missing")
    assert missing.status_code == 404


def test_cancel_respects_status_and_delete_cascades(harness: Any) -> None:
    run_id = _created_run_id(harness)
    harness.store.update_evaluation_run_status(
        run_id,
        expected_statuses=[EvaluationRunStatus.created.value],
        new_status=EvaluationRunStatus.judging.value,
    )
    rejected = client.post(f"{API_BASE}/runs/{run_id}/cancel")
    assert rejected.status_code == 409

    second = client.post(
        f"{API_BASE}/runs",
        json={"suite_id": "demo-suite", "execution_presets": ["beta"], "judge_presets": []},
    )
    second_id = second.json()["run_id"]
    cancelled = client.post(f"{API_BASE}/runs/{second_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    assert client.delete(f"{API_BASE}/runs/eval-ghost").status_code == 404
    removed = client.delete(f"{API_BASE}/runs/{run_id}")
    assert removed.status_code == 200
    assert removed.json() == {"deleted": run_id}
    assert harness.store.get_evaluation_run(run_id) is None


# ── 预览与授权红线 ──────────────────────────────────────────────────────────


def test_preview_requires_awaiting_status(harness: Any) -> None:
    run_id = _created_run_id(harness)
    response = client.get(f"{API_BASE}/runs/{run_id}/judge-preview")
    assert response.status_code == 409


def test_preview_returns_exact_payload_and_hashes(harness: Any) -> None:
    run_id = _created_run_id(harness)
    _advance_to_awaiting(harness, run_id)
    response = client.get(f"{API_BASE}/runs/{run_id}/judge-preview")
    assert response.status_code == 200
    preview = response.json()
    assert preview["manifest_hash"] == _FROZEN_HASH
    assert preview["frozen_manifest_hash"] == _FROZEN_HASH
    assert preview["estimated_requests"] == 1
    (entry,) = preview["entries"]
    assert entry["payload_available"] is True
    assert entry["payload_preview"] == '{"diff": "staged"}'
    assert preview["sensitive_notice"]


def test_consent_stale_hash_conflicts_without_any_judge_schedule(harness: Any) -> None:
    run_id = _created_run_id(harness)
    _advance_to_awaiting(harness, run_id)
    stale = client.post(
        f"{API_BASE}/runs/{run_id}/judge-consent",
        json={"decision": "approve", "manifest_hash": "stale-hash"},
    )
    assert stale.status_code == 409
    assert harness.store.get_evaluation_run(run_id).status == "awaiting_judge_consent"
    assert harness.scheduled["judging"] == []


def test_consent_approve_advances_to_judging_and_schedules(harness: Any) -> None:
    run_id = _created_run_id(harness)
    _advance_to_awaiting(harness, run_id)
    bad_decision = client.post(
        f"{API_BASE}/runs/{run_id}/judge-consent",
        json={"decision": "maybe", "manifest_hash": _FROZEN_HASH},
    )
    assert bad_decision.status_code == 400

    approved = client.post(
        f"{API_BASE}/runs/{run_id}/judge-consent",
        json={"decision": "approve", "manifest_hash": _FROZEN_HASH},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "judging"
    assert harness.scheduled["judging"] == [run_id]


def test_consent_deny_finishes_run_with_unauthorized_scores(harness: Any) -> None:
    run_id = _created_run_id(harness)
    _seed_result(harness.store, run_id)
    _advance_to_awaiting(harness, run_id)
    denied = client.post(
        f"{API_BASE}/runs/{run_id}/judge-consent",
        json={"decision": "deny", "manifest_hash": _FROZEN_HASH},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "completed"
    assert harness.scheduled["judging"] == []
    scores = harness.store.list_evaluation_scores(run_id)
    assert [row.score_status for row in scores] == [EvaluationScoreStatus.unauthorized.value]
    assert [row.dimension_scores for row in scores] == [{}]
