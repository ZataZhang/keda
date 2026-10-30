"""模型评测 core 层规则测试：suite 解析、judge 响应、payload/manifest、汇总与授权校验。

失败态处理是本模块的核心不变量：任何非 valid 评分绝不折算为 0 分，
授权 hash 不符必须拒绝且不发请求。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.core.shared.models.model_evaluation import (
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
    build_judge_payload_document,
    build_manifest_hash,
    parse_evaluation_suite,
    parse_judge_response,
    preset_snapshot_mismatch,
    sha256_text,
)
from backend.core.use_cases import model_evaluation as evaluation_rules

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
  - id: case-b
    prompt: 修复排序边界
    check_recipe: unittest
"""


def _suite():
    return parse_evaluation_suite(SUITE_TEXT, "/tmp/repo/suite.yaml")


def _run(**overrides: Any) -> EvaluationRunRecord:
    fields: dict[str, Any] = {
        "run_id": "eval-test000001",
        "suite_id": "demo-suite",
        "suite_version": "1.0.0",
        "suite_hash": "hash",
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
    return EvaluationRunRecord(**fields)


def _result(**overrides: Any) -> EvaluationResultRecord:
    fields: dict[str, Any] = {
        "result_id": "res-test000001",
        "run_id": "eval-test000001",
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


def _score(**overrides: Any) -> EvaluationScoreRecord:
    fields: dict[str, Any] = {
        "score_id": "score-test00001",
        "run_id": "eval-test000001",
        "result_id": "res-test000001",
        "case_id": "case-a",
        "execution_preset_id": "p1",
        "judge_preset_id": "j1",
        "judge_model_snapshot": {"preset_name": "j1"},
        "rubric_version": "v1",
        "score_status": EvaluationScoreStatus.valid.value,
        "dimension_scores": {
            "correctness": 4,
            "completeness": 4,
            "implementation_quality": 3,
            "verification_evidence": 5,
        },
        "rationale_summary": "ok",
        "response_hash": None,
        "duration_seconds": 1.0,
        "usage_summary": None,
        "error": None,
        "created_at": "2026-10-13T00:00:00+00:00",
    }
    fields.update(overrides)
    return EvaluationScoreRecord(**fields)


# ── suite 解析 ───────────────────────────────────────────────────────────────


def test_parse_suite_accepts_valid_document() -> None:
    suite = _suite()
    assert suite.suite_id == "demo-suite"
    assert suite.case_ids == ("case-a", "case-b")
    assert suite.content_hash == sha256_text(SUITE_TEXT)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ({"version": "1.0"}, "semver"),
        ({"cases": []}, "非空列表"),
        ({"rubric": {"id": "other"}}, "仅支持 rubric"),
    ],
)
def test_parse_suite_rejects_bad_shapes(mutation: dict, expected_fragment: str) -> None:
    import yaml  # noqa: PLC0415 - 仅本用例需要改写文档结构

    document = yaml.safe_load(SUITE_TEXT)
    document.update(mutation)
    if mutation.get("cases") == []:
        document["cases"] = []
    with pytest.raises(ValueError, match=expected_fragment):
        parse_evaluation_suite(yaml.safe_dump(document, allow_unicode=True), "x.yaml")


def test_parse_suite_rejects_duplicate_case_id_and_unknown_recipe() -> None:
    import yaml  # noqa: PLC0415

    document = yaml.safe_load(SUITE_TEXT)
    document["cases"][1]["id"] = "case-a"
    with pytest.raises(ValueError, match="重复"):
        parse_evaluation_suite(yaml.safe_dump(document, allow_unicode=True), "x.yaml")

    document = yaml.safe_load(SUITE_TEXT)
    document["cases"][0]["check_recipe"] = "make deploy"
    with pytest.raises(ValueError, match="check_recipe"):
        parse_evaluation_suite(yaml.safe_dump(document, allow_unicode=True), "x.yaml")


# ── judge 响应解析 ───────────────────────────────────────────────────────────

_VALID_RESPONSE = json.dumps(
    {
        "scores": {
            "correctness": 4,
            "completeness": 3,
            "implementation_quality": 5,
            "verification_evidence": 2,
        },
        "rationales": {"correctness": "结果正确"},
        "refused": False,
    }
)


def test_parse_judge_response_valid_plain_and_fenced() -> None:
    for raw in (_VALID_RESPONSE, f"说明文字\n```json\n{_VALID_RESPONSE}\n```\n收尾"):
        parsed = parse_judge_response(raw)
        assert parsed.status is EvaluationScoreStatus.valid
        assert parsed.dimension_scores["correctness"] == 4


def test_parse_judge_response_refusal_kept_distinct_from_invalid() -> None:
    refused = parse_judge_response(json.dumps({"refused": True, "rationale": "内容敏感"}))
    assert refused.status is EvaluationScoreStatus.refusal
    assert refused.dimension_scores == {}
    assert "敏感" in (refused.rationale_summary or "")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "纯文本没有 JSON",
        json.dumps({"scores": {"correctness": 4}, "refused": False}),  # 缺维度
        json.dumps(
            {
                "scores": {
                    "correctness": 0,  # 越界
                    "completeness": 3,
                    "implementation_quality": 3,
                    "verification_evidence": 3,
                },
                "refused": False,
            }
        ),
        json.dumps(
            {
                "scores": {
                    "correctness": 2.5,  # 非整数
                    "completeness": 3,
                    "implementation_quality": 3,
                    "verification_evidence": 3,
                },
                "refused": False,
            }
        ),
        json.dumps(
            {
                "scores": {
                    "correctness": 3,
                    "completeness": 3,
                    "implementation_quality": 3,
                    "verification_evidence": 3,
                    "style": 3,  # 多余维度
                },
                "refused": False,
            }
        ),
    ],
)
def test_parse_judge_response_invalid_never_coerced(payload: str) -> None:
    parsed = parse_judge_response(payload)
    assert parsed.status is EvaluationScoreStatus.invalid
    assert parsed.dimension_scores == {}


# ── payload 与 manifest ──────────────────────────────────────────────────────


def _payload(**overrides: Any) -> str:
    args: dict[str, Any] = {
        "run_id": "eval-1",
        "result_id": "res-1",
        "case_prompt": "实现 FizzBuzz",
        "execution_preset_id": "p1",
        "agent_summary": "done",
        "result_status": "succeeded",
        "check_status": "passed",
        "check_output_summary": "1 passed",
        "diff_text": "diff --git a/x b/x",
        "rubric_version": "v1",
    }
    args.update(overrides)
    return build_judge_payload_document(**args)


def test_payload_document_is_deterministic_and_hash_stable() -> None:
    first = _payload()
    assert first == _payload()
    assert sha256_text(first) != sha256_text(_payload(diff_text="changed"))


def test_manifest_hash_order_insensitive_and_drift_sensitive() -> None:
    entries_a = [
        {"result_id": "res-b", "payload_sha256": "h2"},
        {"result_id": "res-a", "payload_sha256": "h1"},
    ]
    entries_b = list(reversed(entries_a))
    assert build_manifest_hash(entries_a) == build_manifest_hash(entries_b)
    drifted = [dict(entries_a[0]), {**entries_a[1], "payload_sha256": "h2!"}]
    assert build_manifest_hash(entries_a) != build_manifest_hash(drifted)


def test_preset_snapshot_mismatch_reports_only_drifted_fields() -> None:
    frozen = {
        "preset_name": "j1",
        "agent": "claude",
        "model": "judge-m",
        "reasoning_effort": None,
    }
    assert preset_snapshot_mismatch(frozen, dict(frozen, preset_name="j1")) == ()
    live = {"agent": "claude", "model": "judge-new", "reasoning_effort": None}
    assert preset_snapshot_mismatch(frozen, live) == ("model",)
    assert preset_snapshot_mismatch(frozen, dict(live, model="judge-m", agent="codex")) == (
        "agent",
    )


# ── run 快照构建 ─────────────────────────────────────────────────────────────


def _snapshot_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "suite": _suite(),
        "execution_preset_names": ["p1", "p2"],
        "judge_preset_names": ["j1"],
        "execution_presets": {
            "p1": {"agent": "claude", "model": "m-a", "reasoning_effort": None},
            "p2": {"agent": "codex", "model": None, "reasoning_effort": None},
            "j1": {"agent": "claude", "model": "judge-m", "reasoning_effort": None},
        },
        "judge_availability_probe": lambda name: {"available": True, "reason": None},
        "base_probe": lambda repo, ref: ("commit123", "tree456"),
    }
    kwargs.update(overrides)
    return kwargs


def test_build_run_snapshot_freezes_matrix_and_base() -> None:
    run = evaluation_rules.build_run_snapshot(**_snapshot_kwargs())
    assert run.status == EvaluationRunStatus.created.value
    assert run.base_commit == "commit123"
    assert run.case_ids == ["case-a", "case-b"]
    assert [p["preset_name"] for p in run.execution_preset_snapshots] == ["p1", "p2"]
    assert run.execution_preset_snapshots[0]["cost_status"] == "unknown"
    # 评审预设复用执行预设：快照与执行快照同形，漂移核对（agent/model/effort）才有依据
    assert run.judge_preset_snapshots[0]["preset_name"] == "j1"
    assert run.judge_preset_snapshots[0]["agent"] == "claude"
    assert run.judge_preset_snapshots[0]["model"] == "judge-m"


def test_build_run_snapshot_fail_fast_aggregates_problems() -> None:
    with pytest.raises(evaluation_rules.EvaluationValidationError) as excinfo:
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(
                execution_preset_names=["p1", "ghost"],
                judge_preset_names=["j-offline"],
                execution_presets={
                    "p1": {"agent": "claude", "model": "m-a", "reasoning_effort": None},
                    "j-offline": {"agent": "codex", "model": "x", "reasoning_effort": None},
                },
                judge_availability_probe=lambda name: {
                    "available": False,
                    "reason": "未声明可禁用工具的 judge 形态",
                },
            )
        )
    message = str(excinfo.value)
    assert "ghost" in message and "j-offline" in message and "judge 形态" in message


def test_build_run_snapshot_rejects_matrix_over_ceiling() -> None:
    suite = _suite()
    with pytest.raises(evaluation_rules.EvaluationValidationError, match="预计候选执行"):
        evaluation_rules.build_run_snapshot(
            **_snapshot_kwargs(
                suite=suite,
                execution_preset_names=[f"p{index}" for index in range(33)],
                execution_presets={
                    f"p{index}": {"agent": "a", "model": None, "reasoning_effort": None}
                    for index in range(33)
                },
            )
        )


# ── 汇总派生 ─────────────────────────────────────────────────────────────────


def test_summary_only_valid_scores_aggregate() -> None:
    results = [
        _result(),
        _result(result_id="res-failed", result_status=EvaluationResultStatus.failed.value),
    ]
    scores = [
        _score(),
        _score(
            score_id="score-timeout",
            score_status=EvaluationScoreStatus.timeout.value,
            dimension_scores={},
        ),
    ]
    summary = evaluation_rules.build_evaluation_summary(results, scores)
    correctness = next(a for a in summary.aggregates if a.dimension == "correctness")
    assert correctness.median == 4.0 and correctness.valid_count == 1
    assert summary.status_counts["timeout"] == 1
    assert summary.status_counts["result:failed"] == 1
    assert summary.valid_judge_count == 1


def test_summary_empty_has_no_zero_coercion() -> None:
    summary = evaluation_rules.build_evaluation_summary([_result()], [])
    assert all(aggregate.median is None for aggregate in summary.aggregates)
    assert all(aggregate.valid_count == 0 for aggregate in summary.aggregates)


def test_summary_change_recomputes_median() -> None:
    base = _score()
    changed = _score(
        score_id="score-second",
        judge_preset_id="j2",
        dimension_scores={
            "correctness": 2,
            "completeness": 4,
            "implementation_quality": 3,
            "verification_evidence": 5,
        },
    )
    first = evaluation_rules.build_evaluation_summary([], [base])
    second = evaluation_rules.build_evaluation_summary([], [base, changed])
    median_a = next(a for a in first.aggregates if a.dimension == "correctness")
    median_b = next(a for a in second.aggregates if a.dimension == "correctness")
    assert median_a.median == 4.0
    assert median_b.median == 3.0
    assert (median_b.minimum, median_b.maximum, median_b.valid_count) == (2, 4, 2)


# ── 授权校验 ─────────────────────────────────────────────────────────────────


def test_validate_consent_requires_eligible_status_and_hash_match() -> None:
    run = _run(
        status=EvaluationRunStatus.awaiting_judge_consent.value,
        manifest_hash="frozen",
    )
    evaluation_rules.validate_consent_request(
        run=run, requested_manifest_hash="frozen", current_manifest_hash="frozen"
    )
    with pytest.raises(evaluation_rules.EvaluationValidationError, match="预览后已变化|不一致"):
        evaluation_rules.validate_consent_request(
            run=run, requested_manifest_hash="frozen", current_manifest_hash="stale"
        )
    with pytest.raises(evaluation_rules.EvaluationValidationError):
        evaluation_rules.validate_consent_request(
            run=_run(manifest_hash=None),
            requested_manifest_hash="anything",
            current_manifest_hash="anything",
        )


def test_unreviewed_score_rows_are_written_for_deny_and_cancel() -> None:
    run = _run(judge_preset_snapshots=[{"preset_name": "j1"}, {"preset_name": "j2"}])
    for status, expected in (
        (EvaluationScoreStatus.unauthorized, "unauthorized"),
        (EvaluationScoreStatus.cancelled, "cancelled"),
    ):
        scores = evaluation_rules.build_unreviewed_scores(
            run,
            [_result()],
            score_status=status,
            rationale_summary=f"留痕：{expected}",
        )
        assert [score.score_status for score in scores] == [expected, expected]
        assert all(score.dimension_scores == {} for score in scores)
        assert all(score.rationale_summary == f"留痕：{expected}" for score in scores)

    with pytest.raises(ValueError, match="valid"):
        evaluation_rules.build_unreviewed_scores(
            run,
            [_result()],
            score_status=EvaluationScoreStatus.valid,
            rationale_summary="不应该发生",
        )
