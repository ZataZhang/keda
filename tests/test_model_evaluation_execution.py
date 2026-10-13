"""评测 engines 执行矩阵的边界测试：冻结快照漂移、主机闸门与取消红线。

这些守卫住在 engines 层，只有把 git/worktree/Agent/provider 四条边界同时
钉住才能证明「配置变了就不跑、不送」——HTTP 层测试桩掉了整条执行链，看不到
它们。断言口径是**调用次数为 0**，而不只是看落库状态。
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.models.model_evaluation import (
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreStatus,
    EvaluationSuiteCase,
    sha256_text,
)
from backend.engines.model_evaluation import (
    agent_adapter,
    checks,
    execution,
    judge_client,
    workspace,
)
from backend.engines.model_evaluation.checks import CheckRunOutcome
from backend.infrastructure.config.agent_runner_settings_models import (
    AgentRunnerEvaluationSettings,
    AgentRunnerPresetSettings,
    EvaluationJudgePresetSettings,
)
from backend.infrastructure.config.settings import AgentRunnerSettings
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

_CASE = EvaluationSuiteCase(case_id="case-a", prompt="实现 FizzBuzz", check_recipe="pytest")
_SNAPSHOT = {
    "preset_name": "alpha",
    "agent": "codex",
    "model": "gpt-alpha",
    "reasoning_effort": None,
}
_JUDGE_SNAPSHOT = {
    "preset_name": "judge-1",
    "model": "gpt-judge",
    "temperature": None,
    "api_key_env": None,
    "base_url": None,
}


def _run(run_id: str = "eval-cell0000001", **overrides: Any) -> EvaluationRunRecord:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "suite_id": "demo-suite",
        "suite_version": "1.0.0",
        "suite_hash": "hash",
        "rubric_version": "v1",
        "repo_path": "/tmp/repo",
        "base_ref": "main",
        "base_commit": "abc123",
        "base_tree_hash": "tree-hash",
        "execution_preset_snapshots": [dict(_SNAPSHOT)],
        "judge_preset_snapshots": [dict(_JUDGE_SNAPSHOT)],
        "status": EvaluationRunStatus.running.value,
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
    return EvaluationRunRecord(**fields)


def _result_record(run_id: str = "eval-cell0000001", **overrides: Any) -> EvaluationResultRecord:
    fields: dict[str, Any] = {
        "result_id": "res-a",
        "run_id": run_id,
        "case_id": "case-a",
        "execution_preset_id": "alpha",
        "execution_model_snapshot": dict(_SNAPSHOT),
        "base_tree_hash": "tree-hash",
        "worktree_path": None,
        "result_status": EvaluationResultStatus.succeeded.value,
        "check_status": EvaluationCheckStatus.passed.value,
        "check_recipe": "pytest",
        "check_exit_code": 0,
        "check_output_summary": "1 passed",
        "agent_summary": None,
        "duration_seconds": 1.0,
        "usage_summary": None,
        "cost_status": "unknown",
        "diff_hash": "diff",
        "error": None,
        "created_at": "2026-10-13T00:00:00+00:00",
        "updated_at": "2026-10-13T00:00:00+00:00",
    }
    fields.update(overrides)
    return EvaluationResultRecord(**fields)


@pytest.fixture(autouse=True)
def _reset_host_gate() -> Any:
    """闸门是进程级单例：每个用例重置，避免槽位数跨用例泄漏。"""
    execution._HOST_GATE = None
    yield
    execution._HOST_GATE = None


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把执行矩阵接到 tmp store / 假 git / 记录型 Agent 与 provider 桩。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    settings = AgentRunnerSettings(
        presets={"alpha": AgentRunnerPresetSettings(agent="codex", model="gpt-alpha")},
        evaluation=AgentRunnerEvaluationSettings(
            max_concurrency=1,
            judge_presets={"judge-1": EvaluationJudgePresetSettings(model="gpt-judge")},
            default_judge_presets=["judge-1"],
        ),
    )
    calls: dict[str, list[Any]] = {"candidate": [], "judge": [], "worktree": []}
    inflight = {"now": 0, "max": 0}
    inflight_lock = threading.Lock()

    def _fake_create_worktree(repo_path: str, base_commit: str, worktree_path: Path) -> None:
        calls["worktree"].append(str(worktree_path))
        worktree_path.mkdir(parents=True, exist_ok=True)

    def _fake_run_candidate(**kwargs: Any) -> agent_adapter.CandidateAgentOutcome:
        calls["candidate"].append(kwargs)
        with inflight_lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        time.sleep(0.05)
        with inflight_lock:
            inflight["now"] -= 1
        return agent_adapter.CandidateAgentOutcome(
            ok=True,
            timed_out=False,
            summary="implemented",
            exit_code=0,
            duration_seconds=1.0,
            usage_summary=None,
            error=None,
        )

    def _never_invoke(*args: Any, **kwargs: Any) -> Any:
        calls["judge"].append(args)
        raise AssertionError("本用例的链路不应发起评审调用")

    monkeypatch.setattr(execution, "_store", lambda: store)
    monkeypatch.setattr(execution, "_evaluation_root", lambda: tmp_path / "evaluation")
    monkeypatch.setattr(workspace, "create_detached_worktree", _fake_create_worktree)
    monkeypatch.setattr(workspace, "worktree_tree_hash", lambda path: "tree-hash")
    monkeypatch.setattr(workspace, "collect_diff", lambda path, base_commit: "diff text")
    monkeypatch.setattr(
        workspace,
        "remove_worktree",
        lambda repo_path, path: shutil.rmtree(path, ignore_errors=True),
    )
    monkeypatch.setattr(agent_adapter, "run_candidate", _fake_run_candidate)
    monkeypatch.setattr(
        checks,
        "run_check_recipe",
        lambda recipe, path, timeout: CheckRunOutcome(
            status=EvaluationCheckStatus.passed, exit_code=0, output_summary="1 passed"
        ),
    )
    monkeypatch.setattr(judge_client, "invoke_judge", _never_invoke)
    return type(
        "Harness",
        (),
        {
            "store": store,
            "settings": settings,
            "calls": calls,
            "inflight": inflight,
            "root": tmp_path / "evaluation",
        },
    )()


def _execute_cell(harness: Any, run: EvaluationRunRecord, snapshot: dict[str, Any]) -> None:
    execution._execute_cell(
        run,
        _CASE,
        snapshot,
        harness.root,
        harness.settings,
        [],
        threading.Lock(),
    )


def _only_result(harness: Any, run_id: str = "eval-cell0000001") -> Any:
    (row,) = harness.store.list_evaluation_results(run_id)
    return row


def test_candidate_config_drift_stops_before_worktree_and_agent(harness: Any) -> None:
    """冻结快照与 live 配置不一致：不建 worktree、不调 Agent，失败原因落库。"""
    drifted_snapshot = dict(_SNAPSHOT, model="gpt-alpha-old")
    run = _run(execution_preset_snapshots=[drifted_snapshot])
    harness.store.save_evaluation_run(run)

    _execute_cell(harness, run, drifted_snapshot)

    result = _only_result(harness)
    assert result.result_status == EvaluationResultStatus.failed.value
    assert "model" in (result.error or "")
    assert "配置在 run 创建后已变化" in (result.error or "")
    assert harness.calls["candidate"] == []
    assert harness.calls["worktree"] == []


def test_cell_drops_queued_work_when_run_cancelled_while_waiting(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """排队期间 run 被取消：拿到槽位后重新确认，不补发 Agent 调用。"""
    run = _run()
    harness.store.save_evaluation_run(run)
    real_store = harness.store
    reads = {"count": 0}
    real_get = real_store.get_evaluation_run

    def _get_evaluation_run(run_id: str) -> Any:
        reads["count"] += 1
        # 第二次读发生在闸门之后：模拟排队期间 run 已被 cancel 收尾。
        if reads["count"] == 2:
            real_store.update_evaluation_run_status(
                run_id,
                expected_statuses=[EvaluationRunStatus.running.value],
                new_status=EvaluationRunStatus.cancelled.value,
                finished=True,
            )
        return real_get(run_id)

    monkeypatch.setattr(
        execution, "_store", lambda: _PatchedReader(real_store, _get_evaluation_run)
    )
    _execute_cell(harness, run, dict(_SNAPSHOT))

    assert reads["count"] >= 2
    assert _only_result(harness).result_status == EvaluationResultStatus.cancelled.value
    assert harness.calls["candidate"] == []


class _PatchedReader:
    """只替换 ``get_evaluation_run`` 的 store 代理，用于制造排队期间的状态变化。"""

    def __init__(self, inner: Any, get_evaluation_run: Any) -> None:
        """持有真实 store 与替换读函数。"""
        self._inner = inner
        self._get_evaluation_run = get_evaluation_run

    def get_evaluation_run(self, run_id: str) -> Any:
        """走注入的读函数。"""
        return self._get_evaluation_run(run_id)

    def __getattr__(self, name: str) -> Any:
        """其余方法透传到真实 store。"""
        return getattr(self._inner, name)


def test_host_gate_caps_candidates_across_concurrent_runs(harness: Any) -> None:
    """两个 run 同时跑矩阵时，在飞候选数仍是单个 ceiling，而不是 N × ceiling。"""
    run_ids = ["eval-cell0000001", "eval-cell0000002"]
    for run_id in run_ids:
        harness.store.save_evaluation_run(_run(run_id))
    threads = [
        threading.Thread(
            target=_execute_cell,
            args=(harness, _run(run_id), dict(_SNAPSHOT)),
        )
        for run_id in run_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(harness.calls["candidate"]) == 2
    assert harness.inflight["max"] == 1
    for run_id in run_ids:
        assert _only_result(harness, run_id).result_status == EvaluationResultStatus.succeeded.value


def test_judge_config_drift_never_sends_payload(harness: Any) -> None:
    """评审预设配置漂移：落 provider_error，绝不把 payload 发给 provider。"""
    run = _run(
        status=EvaluationRunStatus.judging.value,
        judge_preset_snapshots=[dict(_JUDGE_SNAPSHOT, model="gpt-judge-old")],
    )
    harness.store.save_evaluation_run(run)
    result = _result_record(run.run_id)
    harness.store.save_evaluation_result(result)
    entry = {"result_id": "res-a", "case_id": "case-a", "execution_preset_id": "alpha"}

    execution._execute_judge(run, entry, "judge-1", {"res-a": result}, harness.settings)

    (score,) = harness.store.list_evaluation_scores(run.run_id)
    assert score.score_status == EvaluationScoreStatus.provider_error.value
    assert "配置在 run 创建后已变化" in (score.error or "")
    assert "model" in (score.error or "")
    assert harness.calls["judge"] == []


def test_judge_payload_drift_after_consent_never_sends(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """授权后暂存内容变了：hash 不符即拒发，所批非所发一律不送。"""
    run = _run(status=EvaluationRunStatus.judging.value)
    harness.store.save_evaluation_run(run)
    result = _result_record(run.run_id)
    harness.store.save_evaluation_result(result)
    entry = {
        "result_id": "res-a",
        "case_id": "case-a",
        "execution_preset_id": "alpha",
        "payload_file": "res-a.json",
        "payload_sha256": sha256_text("batched payload"),
    }
    monkeypatch.setattr(
        execution, "read_staged_payload", lambda run_id, payload_file: "silently edited payload"
    )
    execution._execute_judge(run, entry, "judge-1", {"res-a": result}, harness.settings)

    (score,) = harness.store.list_evaluation_scores(run.run_id)
    assert score.score_status == EvaluationScoreStatus.provider_error.value
    assert "payload 与授权时 manifest hash 不一致" in (score.error or "")
    assert harness.calls["judge"] == []
