"""模型评测 v2 的隔离、校准、重复与稳定性规则测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.models.model_evaluation import (
    CALIBRATION_PRESET_GOLD,
    CALIBRATION_PRESET_NOOP,
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunRecord,
    EvaluationRunStatus,
    parse_evaluation_suite,
)
from backend.core.use_cases import model_evaluation as evaluation_rules
from backend.engines.model_evaluation import workspace


def _suite_text(*, case_count: int = 2) -> str:
    """建立带一个 dev 与一个 holdout 的最小 suite 文本。"""
    cases = []
    for case_index in range(case_count):
        case_id = f"case-{case_index}"
        split = "dev" if case_index == 0 else "holdout"
        cases.append(
            f"  - id: {case_id}\n"
            f"    prompt: 修复 {case_id} 的行为\n"
            "    check_recipe: uv-pytest\n"
            f"    base_ref: base-{case_index}\n"
            f"    split: {split}\n"
            "    check_targets: [tests/test_behavior.py]\n"
            "    held_out: [tests/test_behavior.py]\n"
            "    candidate_hidden: [tasks/pending/known-answer.md]\n"
            "    gold_patch: gold.patch\n"
        )
    return (
        "id: v2-fixture\n"
        "version: 1.0.0\n"
        "repo_path: .\n"
        "base_ref: main\n"
        "check:\n  timeout_seconds: 60\n"
        "cases:\n" + "".join(cases)
    )


def _v2_suite(*, case_count: int = 2):
    """解析含全部判分材料的 suite fixture。"""
    materials: dict[str, bytes] = {}
    for case_index in range(case_count):
        case_id = f"case-{case_index}"
        materials[f"{case_id}/held_out/tests/test_behavior.py"] = b"def test_behavior(): pass\n"
        materials[f"{case_id}/gold.patch"] = b"diff --git a/src/app.py b/src/app.py\n"
    return parse_evaluation_suite(_suite_text(case_count=case_count), "suite.yaml", materials)


def _record(
    *,
    result_id: str,
    case_id: str = "case-0",
    preset: str = "agent-a",
    repeat_index: int = 0,
    result_status: str = EvaluationResultStatus.succeeded.value,
    check_status: str = EvaluationCheckStatus.passed.value,
) -> EvaluationResultRecord:
    """建立稳定性与校准裁决使用的结果行。"""
    return EvaluationResultRecord(
        result_id=result_id,
        run_id="eval-fixture",
        case_id=case_id,
        execution_preset_id=preset,
        execution_model_snapshot={"preset_name": preset},
        base_tree_hash="tree",
        worktree_path=None,
        result_status=result_status,
        check_status=check_status,
        check_recipe="uv-pytest",
        check_exit_code=0 if check_status == "passed" else 1,
        check_output_summary=None,
        agent_summary=None,
        duration_seconds=None,
        usage_summary=None,
        cost_status="unknown",
        diff_hash=None,
        error=None,
        created_at="2026-10-29T00:00:00+00:00",
        updated_at="2026-10-29T00:00:00+00:00",
        repeat_index=repeat_index,
    )


def _snapshot_kwargs(**overrides: Any) -> dict[str, Any]:
    """建立 run 创建规则需要的注入参数。"""
    return {
        "suite": _v2_suite(),
        "execution_preset_names": ["agent-a"],
        "judge_preset_names": [],
        "execution_presets": {
            "agent-a": {"agent": "local-agent", "model": "fixture", "reasoning_effort": None}
        },
        "judge_availability_probe": lambda _name: {"available": True, "reason": None},
        "base_probe": lambda _repo, ref: (f"commit-{ref}", f"tree-{ref}"),
        "calibrated_case_ids": ["case-0", "case-1"],
        **overrides,
    }


def test_legacy_suite_remains_single_run_without_calibration() -> None:
    suite = parse_evaluation_suite(
        "id: old\nversion: 1.0.0\nrepo_path: /tmp/repo\nbase_ref: main\n"
        "cases:\n  - id: old-case\n    prompt: 旧格式\n    check_recipe: pytest\n",
        "example.yaml",
    )

    assert suite.requires_calibration is False
    assert suite.cases[0].split == "dev"
    assert suite.cases[0].base_ref is None
    assert suite.cases[0].check_targets == ()

    portable_legacy_suite = parse_evaluation_suite(
        "id: portable-old\nversion: 1.0.0\nrepo_path: .\nbase_ref: main\n"
        "cases:\n  - id: old-case\n    prompt: 旧格式\n    check_recipe: pytest\n",
        "example.yaml",
    )
    assert portable_legacy_suite.requires_calibration is False


def test_suite_hash_includes_materials_and_rejects_unsafe_targets() -> None:
    raw_text = _suite_text(case_count=1)
    materials = {
        "case-0/held_out/tests/test_behavior.py": b"def test_behavior(): pass\n",
        "case-0/gold.patch": b"patch-v1",
    }
    first = parse_evaluation_suite(raw_text, "suite.yaml", materials)
    second = parse_evaluation_suite(
        raw_text,
        "suite.yaml",
        {**materials, "case-0/gold.patch": b"patch-v2"},
    )
    assert first.requires_calibration is True
    assert first.content_hash != second.content_hash
    assert first.cases[0].candidate_hidden == ("tasks/pending/known-answer.md",)

    with pytest.raises(ValueError, match="非法相对路径"):
        parse_evaluation_suite(
            raw_text.replace("tasks/pending/known-answer.md", "../secret"),
            "suite.yaml",
            materials,
        )
    with pytest.raises(ValueError, match="非法相对路径"):
        parse_evaluation_suite(
            raw_text.replace("[tests/test_behavior.py]", "[--doctest-modules]"),
            "suite.yaml",
            materials,
        )


def test_snapshot_freezes_per_case_bases_and_gates_holdout_and_calibration() -> None:
    calibration_kwargs = _snapshot_kwargs(
        execution_preset_names=[CALIBRATION_PRESET_NOOP, CALIBRATION_PRESET_GOLD],
        execution_presets={},
        repeats=3,
        include_holdout=False,
    )
    run = evaluation_rules.build_run_snapshot(**calibration_kwargs)
    assert run.repeats == 3
    assert run.case_ids == ["case-0", "case-1"]
    assert run.case_bases == {
        "case-0": {"base_ref": "base-0", "commit": "commit-base-0", "tree": "tree-base-0"},
        "case-1": {"base_ref": "base-1", "commit": "commit-base-1", "tree": "tree-base-1"},
    }

    dev_only_run = evaluation_rules.build_run_snapshot(**_snapshot_kwargs(include_holdout=False))
    assert dev_only_run.case_ids == ["case-0"]

    with pytest.raises(evaluation_rules.EvaluationValidationError, match="尚未校准通过: case-1"):
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(include_holdout=True, calibrated_case_ids=["case-0"])
        )
    with pytest.raises(evaluation_rules.EvaluationValidationError, match="至少为 3"):
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(
                execution_preset_names=[CALIBRATION_PRESET_NOOP, CALIBRATION_PRESET_GOLD],
                execution_presets={},
                repeats=2,
            )
        )


def test_run_snapshot_rejects_model_and_calibration_cell_overages() -> None:
    with pytest.raises(evaluation_rules.EvaluationValidationError, match="超过上限 48"):
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(
                execution_preset_names=[f"agent-{index}" for index in range(5)],
                execution_presets={
                    f"agent-{index}": {"agent": "local-agent", "model": None} for index in range(5)
                },
                repeats=5,
                include_holdout=True,
            )
        )
    with pytest.raises(evaluation_rules.EvaluationValidationError, match="超过上限 144"):
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(
                suite=_v2_suite(case_count=15),
                execution_preset_names=[CALIBRATION_PRESET_NOOP, CALIBRATION_PRESET_GOLD],
                execution_presets={},
                repeats=5,
                include_holdout=True,
            )
        )


def test_calibration_derivation_requires_complete_noop_and_gold_runs() -> None:
    run = EvaluationRunRecord(
        run_id="eval-fixture",
        suite_id="v2-fixture",
        suite_version="1.0.0",
        suite_hash="suite-hash",
        rubric_version="v1",
        repo_path=".",
        base_ref="main",
        base_commit="commit",
        base_tree_hash="tree",
        execution_preset_snapshots=[
            {"preset_name": CALIBRATION_PRESET_NOOP},
            {"preset_name": CALIBRATION_PRESET_GOLD},
        ],
        judge_preset_snapshots=[],
        status=EvaluationRunStatus.completed.value,
        manifest_hash=None,
        case_ids=["case-0"],
        check_timeout_seconds=60,
        error=None,
        created_at="2026-10-29T00:00:00+00:00",
        updated_at="2026-10-29T00:00:00+00:00",
        started_at="2026-10-29T00:00:00+00:00",
        finished_at="2026-10-29T00:00:00+00:00",
        repeats=3,
    )
    results = [
        _record(
            result_id=f"noop-{index}",
            preset=CALIBRATION_PRESET_NOOP,
            repeat_index=index,
            check_status=EvaluationCheckStatus.failed.value,
        )
        for index in range(3)
    ] + [
        _record(
            result_id=f"gold-{index}",
            preset=CALIBRATION_PRESET_GOLD,
            repeat_index=index,
        )
        for index in range(3)
    ]

    calibrated = evaluation_rules.derive_case_calibration([run], results, "suite-hash")
    assert calibrated == [{"case_id": "case-0", "calibrated": True, "noop": 3, "gold": 3}]
    incomplete = evaluation_rules.derive_case_calibration([run], results[:-1], "suite-hash")
    assert incomplete[0]["calibrated"] is False
    missing_first_repeat = [result for result in results if result.repeat_index != 0] + [
        _record(
            result_id="noop-duplicate-index",
            preset=CALIBRATION_PRESET_NOOP,
            repeat_index=3,
            check_status=EvaluationCheckStatus.failed.value,
        ),
        _record(
            result_id="gold-duplicate-index",
            preset=CALIBRATION_PRESET_GOLD,
            repeat_index=3,
        ),
    ]
    missing_index_state = evaluation_rules.derive_case_calibration(
        [run], missing_first_repeat, "suite-hash"
    )
    assert missing_index_state[0]["calibrated"] is False


def test_stability_separates_verdicts_from_execution_faults() -> None:
    results = [
        _record(result_id="pass-0", repeat_index=0),
        _record(result_id="pass-1", repeat_index=1),
        _record(result_id="pass-2", repeat_index=2),
        _record(
            result_id="unstable-fail",
            case_id="case-1",
            repeat_index=0,
            check_status=EvaluationCheckStatus.failed.value,
        ),
        _record(
            result_id="unstable-pass",
            case_id="case-1",
            repeat_index=1,
            check_status=EvaluationCheckStatus.passed.value,
        ),
        _record(
            result_id="fault",
            case_id="case-1",
            repeat_index=2,
            result_status=EvaluationResultStatus.timeout.value,
            check_status=EvaluationCheckStatus.not_run.value,
        ),
    ]

    summary = evaluation_rules.build_stability_summary(results)
    stable = next(row for row in summary if row.case_id == "case-0")
    unstable = next(row for row in summary if row.case_id == "case-1")
    assert stable.stable is True and stable.passed_count == 3
    assert unstable.stable is False
    assert unstable.execution_fault_count == 1
    assert unstable.check_failure_count == 1

    incomplete = evaluation_rules.build_stability_summary(
        [
            _record(result_id="pass", repeat_index=0),
            _record(
                result_id="fault-only",
                repeat_index=1,
                result_status=EvaluationResultStatus.failed.value,
                check_status=EvaluationCheckStatus.not_run.value,
            ),
        ]
    )[0]
    assert incomplete.stable is None
    assert incomplete.execution_fault_count == 1
    assert incomplete.check_failure_count == 0


def test_snapshot_repo_has_one_commit_and_restores_hidden_scoring_material(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=source_repo, check=True)
    subprocess.run(["git", "config", "user.name", "fixture"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.test"],
        cwd=source_repo,
        check=True,
    )
    (source_repo / "src").mkdir()
    (source_repo / "tests").mkdir()
    (source_repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (source_repo / "tests" / "test_hidden.py").write_text(
        "def test_hidden(): assert True\n", encoding="utf-8"
    )
    (source_repo / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (source_repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    source_commit = _commit(source_repo, "baseline")
    (source_repo / "tests" / "test_future.py").write_text(
        "def test_future(): pass\n", encoding="utf-8"
    )
    repair_commit = _commit(source_repo, "future repair")

    snapshot = tmp_path / "isolated"
    workspace.create_snapshot_repo(str(source_repo), source_commit, snapshot)
    assert (
        workspace.worktree_tree_hash(snapshot)
        == subprocess.run(
            ["git", "rev-parse", f"{source_commit}^{{tree}}"],
            cwd=source_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    log_output = subprocess.run(
        ["git", "log", "--all", "--oneline"],
        cwd=snapshot,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(log_output) == 1
    assert (snapshot / "tests" / "test_future.py").exists() is False
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{repair_commit}^{{commit}}"],
            cwd=snapshot,
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
    assert not subprocess.run(
        ["git", "remote"], cwd=snapshot, check=True, capture_output=True, text=True
    ).stdout.strip()
    workspace.remove_held_out_files(snapshot, ("tests/test_hidden.py",))
    assert not (snapshot / "tests" / "test_hidden.py").exists()

    (snapshot / "pyproject.toml").write_text("tampered\n", encoding="utf-8")
    (snapshot / "uv.lock").write_text("tampered\n", encoding="utf-8")
    outside_marker = tmp_path / "outside-marker"
    outside_marker.write_text("unchanged\n", encoding="utf-8")
    (snapshot / "pyproject.toml").unlink()
    (snapshot / "pyproject.toml").symlink_to(outside_marker)
    (snapshot / "tests" / "test_hidden.py").symlink_to(outside_marker)
    held_out = tmp_path / "materials" / "held_out"
    (held_out / "tests").mkdir(parents=True)
    (held_out / "tests" / "test_hidden.py").write_text(
        "def test_hidden(): pass\n", encoding="utf-8"
    )
    workspace.restore_held_out_base_files(
        str(source_repo), source_commit, snapshot, ("tests/test_hidden.py",)
    )
    workspace.restore_dependency_manifests(str(source_repo), source_commit, snapshot)
    workspace.overlay_held_out(snapshot, held_out, ("tests/test_hidden.py",))
    assert (snapshot / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]")
    assert (snapshot / "uv.lock").read_text(encoding="utf-8") == "version = 1\n"
    assert (snapshot / "tests" / "test_hidden.py").read_text(encoding="utf-8") == (
        "def test_hidden(): pass\n"
    )
    assert outside_marker.read_text(encoding="utf-8") == "unchanged\n"


def _commit(repo_path: Path, subject: str) -> str:
    """为隔离仓库 fixture 创建一次提交并返回 SHA。"""
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", subject], cwd=repo_path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
