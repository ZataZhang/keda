"""三组开发评测的校准资格、单变量配置和完整账本回归。"""

from dataclasses import asdict, replace
import sys
import time
from unittest.mock import Mock

import pytest

from backend.core.shared.models.agent_model_preset import AgentModelPreset, ModelSelection
from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, CommandResult
from backend.core.use_cases.agent_invocation import build_agent_invocation
from scripts.evaluation.common import CellSpec, RunSpec, save_json
from scripts.evaluation.matrix import is_calibrated, summarize
from scripts.evaluation import workflow
from backend.core.use_cases.agent_runner_output_routing import _OutputRoutedProcessRunner
from tests.conftest import FakeProcessRunner


def test_calibration_rejects_collection_errors_and_missing_repeats():
    controls = [
        {
            "case": "example",
            "arm": arm,
            "repeat": repeat,
            "score": {
                "valid": True,
                "accepted": arm == "gold",
                "failure": ["behavior"] if arm == "noop" else [],
            },
        }
        for arm in ("noop", "gold")
        for repeat in (1, 2, 3)
    ]
    assert is_calibrated(controls, ["example"])
    assert not is_calibrated(controls[:-1], ["example"])
    controls[0]["score"]["skipped"] = ["boundary"]
    assert not is_calibrated(controls, ["example"])
    controls[0]["score"]["skipped"] = []
    controls[0]["score"]["valid"] = False
    controls[0]["score"]["failure"] = []
    controls[0]["score"]["error"] = ["ImportError"]
    assert not is_calibrated(controls, ["example"])


def test_keda_arms_change_only_review_enabled(tmp_path, monkeypatch):
    config = AppConfig(
        agent_presets={
            "codex-luna-max": AgentModelPreset(
                agent="codex", model="gpt-6-luna", reasoning_effort="max"
            )
        }
    )
    monkeypatch.setattr(workflow, "load_fresh_agent_runner_settings", lambda: None)
    monkeypatch.setattr(workflow, "build_app_config_from_settings", lambda _: config)
    monkeypatch.setattr(workflow, "load_agent_runner_local_settings", lambda _: None)
    spec = RunSpec(root=tmp_path, output=tmp_path, suite_id="fixture", seconds=30)
    enabled = asdict(workflow.build_config(CellSpec(spec, "example", "keda-review", 1)))
    disabled = asdict(workflow.build_config(CellSpec(spec, "example", "keda-no-review", 1)))
    assert enabled["pre_pr_review"]["enabled"] is True
    assert disabled["pre_pr_review"]["enabled"] is False
    disabled["pre_pr_review"]["enabled"] = True
    assert enabled == disabled
    assert enabled["runner"]["verification_commands"]


def test_partial_matrix_keeps_infrastructure_faults_separate(tmp_path):
    save_json(
        tmp_path / "manifest.json",
        {"cases": ["example"], "arms": ["native", "keda-review"], "schedule": [1, 2, 3, 4]},
    )
    save_json(
        tmp_path / "r1/example/native/result.json",
        {"case": "example", "arm": "native", "repeat": 1, "harness_error": "disk full"},
    )
    save_json(
        tmp_path / "r1/example/keda-review/result.json",
        {
            "case": "example",
            "arm": "keda-review",
            "repeat": 1,
            "execution": {"seconds": 30, "delivered": False},
            "score": {"valid": True, "accepted": True, "passed": ["behavior"], "failure": []},
        },
    )
    summary = summarize(tmp_path)
    assert not summary["complete"]
    assert summary["unique_tasks"] == 1
    assert summary["arms"]["native"]["invalid_or_unscored"] == 1
    assert summary["arms"]["native"]["valid_scores"] == 0
    assert summary["arms"]["keda-review"]["accepted_patches"] == 1
    assert summary["arms"]["keda-review"]["delivered_and_accepted"] == 0


def test_observed_runner_preserves_protocol_through_issue_log_router(tmp_path):
    """真实日志路由不得因包装器签名而丢掉 cwd 和输出协议。"""
    spec = CellSpec(
        RunSpec(root=tmp_path, output=tmp_path, suite_id="fixture"), "task", "keda-review", 1
    )
    observed = workflow.ObservedRunner(spec, time.monotonic() + 10)
    chunks = []
    routed = _OutputRoutedProcessRunner(observed, chunks.append)
    outcome = routed.run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.cwd()); print('relay-ok')"],
        cwd=tmp_path,
        output_protocol="plain",
        capture_output=False,
        timeout=5,
    )
    assert outcome.return_code == 0
    assert str(tmp_path) in outcome.stdout
    assert "relay-ok" in "".join(chunks)


def test_observed_runner_records_review_sha_and_remaining_budget(tmp_path, monkeypatch):
    """通用标签下保留审查前 SHA，且后续模型调用收到递减的剩余预算。"""
    spec = CellSpec(
        RunSpec(root=tmp_path, output=tmp_path, suite_id="fixture"), "task", "keda-review", 1
    )
    spec.directory.mkdir(parents=True)
    clock_seconds = [10]
    monkeypatch.setattr(workflow.time, "monotonic", lambda: clock_seconds[0])
    observed = workflow.ObservedRunner(spec, 40)
    observed.inner = FakeProcessRunner()
    command = ["codex", "--model", "gpt-6-luna", "--config", "model_reasoning_effort=max"]
    observed.run(command, cwd=tmp_path, label="Issue #42: local fixture", input_text="Task body")
    assert observed.before_review is None
    assert observed.invocations[0]["provided_budget_seconds"] == 30
    assert "30 seconds remaining" in observed.inner.input_texts[0]
    assert observed.inner.input_texts[0].endswith("Task body")
    review_head = "1234567890abcdef" * 2 + "12345678"
    (spec.directory / "execution.log").write_text(
        "INFO:backend.core.use_cases.agent_review:Starting pre-PR review for Issue #42 "
        f"with reviewer 'codex' (profile=run, timeout=3600s, head={review_head}).\n",
        encoding="utf-8",
    )
    clock_seconds[0] = 25
    observed.run(command, cwd=tmp_path, label="Issue #42: local fixture", input_text="Review body")
    assert observed.before_review == review_head
    assert observed.invocations[1]["provided_budget_seconds"] == 15
    assert "15 seconds remaining" in observed.inner.input_texts[1]
    assert "UTC deadline:" in observed.inner.input_texts[1]
    assert observed.inner.input_texts[1].endswith("Review body")


@pytest.mark.parametrize("model,effort", [("gpt-6-luna", "max"), ("gpt-6.1-terra", "medium")])
def test_observed_budget_reaches_production_codex_argv(tmp_path, model, effort):
    """真实生产调用构造的 argv 提示词也收到预算，且调用方参数不被改写。"""
    spec = CellSpec(
        RunSpec(root=tmp_path, output=tmp_path, suite_id="fixture"), "task", "keda-review", 1
    )
    spec.directory.mkdir(parents=True)
    config = AppConfig()
    config.agents["codex"] = replace(
        config.agents["codex"],
        reasoning_effort_args=("--config", "model_reasoning_effort={effort}"),
    )
    selection = ModelSelection(agent="codex", model=model, reasoning_effort=effort)
    invocation = build_agent_invocation(
        "codex",
        "run",
        "Actual review body",
        tmp_path,
        config,
        selection,
    )
    assert invocation.prompt_delivery == "argv_tail"
    observed = workflow.ObservedRunner(spec, time.monotonic() + 30, model_selection=selection)
    observed.inner = Mock(spec=IProcessRunner)
    observed.inner.run.return_value = CommandResult(
        command=invocation.argv, return_code=0, stdout="", stderr=""
    )
    routed = _OutputRoutedProcessRunner(observed, lambda _: None)
    routed.run(list(invocation.argv), cwd=tmp_path, output_protocol=invocation.output_protocol)
    forwarded = observed.inner.run.call_args
    assert forwarded.args[0][-1].startswith("[Evaluation runtime budget:")
    assert forwarded.args[0][-1].endswith("Actual review body")
    assert forwarded.kwargs.get("input_text") is None
    assert invocation.argv[-1] == "Actual review body"
    assert observed.invocations[0]["model"] == model
    assert observed.invocations[0]["effort"] == effort
