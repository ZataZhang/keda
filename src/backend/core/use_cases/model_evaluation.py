"""模型评测的 core 编排：run 快照校验、状态迁移、授权核对与逐评审汇总。

依赖方向说明：本模块只做**规则与编排**——suite/preset/base 的创建期校验、
状态机守卫、manifest hash 比对、评分聚合（只从 valid 明细派生）。git、
worktree、provider 调用与文件 IO 都由 engines 层的注入回调承担；
store 端口见 :mod:`backend.core.shared.interfaces.evaluation_store`。

行为红线（PRD §7.1）：
- 非 valid 的评分状态永不进入中位数/范围，也不折算为 0 分。
- 单一有效评审只显示该分数与"1 位评审"，不产生"一致/多数"结论。
- 授权动作只接受与当前暂存 payload hash 完全一致的请求，过期即冲突。
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from backend.core.shared.interfaces.evaluation_store import IEvaluationStore
from backend.core.shared.models.model_evaluation import (
    ACTIVE_RUN_STATUSES,
    CALIBRATION_PRESET_GOLD,
    CALIBRATION_PRESET_NAMES,
    CALIBRATION_PRESET_NOOP,
    CONSENT_ELIGIBLE_RUN_STATUSES,
    EVALUATION_RUBRIC_DIMENSIONS,
    SCORING_STATUSES,
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
    EvaluationSuite,
)

_logger = logging.getLogger(__name__)

#: 模型候选与不调用模型的校准候选分别限量，避免额度与运行时失控。
MAX_MODEL_EVALUATION_CELLS = 48
MAX_CALIBRATION_EVALUATION_CELLS = 144


class EvaluationValidationError(ValueError):
    """run 创建/授权前置校验失败；message 汇总全部可解释原因。"""


@dataclass(frozen=True)
class EvaluationDimensionAggregate:
    """单个维度在一组 valid 评分上的分布：中位数 / min / max / 有效数。"""

    dimension: str
    median: float | None
    minimum: int | None
    maximum: int | None
    valid_count: int


@dataclass(frozen=True)
class EvaluationSummary:
    """run 级汇总：全局与逐执行预设的维度分布 + 非评分状态计数。

    ``aggregates`` 只统计 valid 评分；``status_counts`` 按 (result/preset)
    保留全部状态桶（含 failure/refusal/unauthorized），让页面能区分
    "没人评" 与 "评了但失败"。
    """

    aggregates: list[EvaluationDimensionAggregate]
    by_execution_preset: dict[str, list[EvaluationDimensionAggregate]]
    status_counts: dict[str, int]
    valid_judge_count: int


@dataclass(frozen=True)
class EvaluationStability:
    """一个题目与预设组合的重复结果汇总。"""

    case_id: str
    execution_preset_id: str
    repeat_count: int
    passed_count: int
    execution_fault_count: int
    check_failure_count: int
    stable: bool | None


def build_run_snapshot(
    *,
    suite: EvaluationSuite,
    execution_preset_names: Sequence[str],
    judge_preset_names: Sequence[str],
    execution_presets: Mapping[str, Mapping[str, Any]],
    judge_availability_probe: Callable[[str], Mapping[str, Any]],
    base_probe: Callable[[str, str], tuple[str, str]],
    repeats: int = 1,
    include_holdout: bool = False,
    calibrated_case_ids: Sequence[str] = (),
) -> EvaluationRunRecord:
    """校验评测矩阵并冻结 run 配置快照（不落库、不执行）。

    Args:
        suite: 已通过 :func:`parse_evaluation_suite` 的评测集。
        execution_preset_names: 用户选择的执行预设名单（≥2 才有对照意义，
            但 1 个也允许，页面负责提示）。
        judge_preset_names: 用户选择的评审预设名单（取自同一份执行预设清单）；
            为空表示本轮只做确定性检查对照，候选完成后不进入评审授权。
        execution_presets: 预设名 -> 解析后的 ``(agent, model, reasoning_effort)``
            快照字典（来自现有 agent_runner 配置，不重新定义模型目录）；评审预设
            复用它，因此评审快照与执行快照同形。
        judge_availability_probe: engines 注入的评审可用性探针（agent 是否声明
            禁用工具的 judge 形态、CLI 是否可执行）；返回
            ``{"available": bool, "reason": str}``。
        base_probe: engines 注入的基线探针，``(repo_path, base_ref)`` ->
            ``(commit, tree_hash)``；repo 不可达或 ref 解析失败抛 ValueError。

    Returns:
        EvaluationRunRecord: status=created 的快照，含 suite/base/preset 指纹。

    Raises:
        EvaluationValidationError: 任一预检失败（原因逐条汇总，fail-fast）。
    """
    problems: list[str] = []
    if not execution_preset_names:
        problems.append("至少需要一个 execution preset")
    unknown_execution = [
        name
        for name in execution_preset_names
        if name not in execution_presets and name not in CALIBRATION_PRESET_NAMES
    ]
    if unknown_execution:
        problems.append(f"执行预设不存在: {', '.join(unknown_execution)}")
    unknown_judge = [
        name
        for name in judge_preset_names
        if name not in execution_presets or name in CALIBRATION_PRESET_NAMES
    ]
    if unknown_judge:
        problems.append(f"评审预设不存在: {', '.join(unknown_judge)}")
    if not 1 <= repeats <= 5:
        problems.append("repeats 必须在 1–5 之间")
    calibration_names = set(execution_preset_names) & CALIBRATION_PRESET_NAMES
    calibration_run = bool(calibration_names)
    if calibration_run and calibration_names != CALIBRATION_PRESET_NAMES:
        problems.append("校准 run 必须同时选择 @noop 与 @gold")
    if calibration_run and judge_preset_names:
        problems.append("校准候选不可选择为评审，校准 run 不接受 judge presets")
    if calibration_run and repeats < 3:
        problems.append("校准 run 的 repeats 必须至少为 3")
    model_preset_names = set(execution_preset_names) - CALIBRATION_PRESET_NAMES
    if calibration_run and model_preset_names:
        problems.append("校准候选不能与真实模型预设混合")
    selected_cases = [
        case
        for case in suite.cases
        if calibration_run or include_holdout or case.split != "holdout"
    ]
    if suite.requires_calibration and not calibration_run:
        missing_calibration = [
            case.case_id for case in selected_cases if case.case_id not in set(calibrated_case_ids)
        ]
        if missing_calibration:
            problems.append(f"题目尚未校准通过: {', '.join(missing_calibration)}")
    if not selected_cases:
        problems.append("所选评测范围没有可执行题目")
    matrix_cells = len(selected_cases) * len(execution_preset_names) * repeats
    matrix_limit = (
        MAX_CALIBRATION_EVALUATION_CELLS if calibration_run else MAX_MODEL_EVALUATION_CELLS
    )
    if matrix_cells > matrix_limit:
        problems.append(f"预计候选执行 {matrix_cells} 次，超过上限 {matrix_limit}")

    for judge_name in judge_preset_names:
        if judge_name in unknown_judge:
            continue
        probe = judge_availability_probe(judge_name)
        if not probe.get("available"):
            problems.append(
                f"评审预设 '{judge_name}' 不可用: {probe.get('reason') or '无法作为评审'}"
            )

    case_bases: dict[str, dict[str, str]] = {}
    for case in selected_cases:
        selected_base_ref = case.base_ref or suite.base_ref
        try:
            case_commit, case_tree = base_probe(suite.repo_path, selected_base_ref)
            case_bases[case.case_id] = {
                "base_ref": selected_base_ref,
                "commit": case_commit,
                "tree": case_tree,
            }
        except ValueError as exc:
            problems.append(f"题目 '{case.case_id}' 基线解析失败: {exc}")

    if problems:
        raise EvaluationValidationError("；".join(problems))

    created_at = _utc_now_iso()
    execution_snapshots = [
        {
            "preset_name": name,
            **dict(execution_presets.get(name, {})),
            "cost_status": "unknown",
            "cost_estimate": None,
            "kind": "calibration" if name in CALIBRATION_PRESET_NAMES else "agent",
        }
        for name in execution_preset_names
    ]
    judge_snapshots = [
        {
            "preset_name": name,
            **dict(execution_presets.get(name, {})),
            "cost_status": "unknown",
        }
        for name in judge_preset_names
    ]
    return EvaluationRunRecord(
        run_id=f"eval-{uuid.uuid4().hex[:12]}",
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_hash=suite.content_hash,
        rubric_version=suite.rubric_version,
        repo_path=suite.repo_path,
        base_ref=suite.base_ref,
        base_commit=next(iter(case_bases.values()))["commit"],
        base_tree_hash=next(iter(case_bases.values()))["tree"],
        execution_preset_snapshots=execution_snapshots,
        judge_preset_snapshots=judge_snapshots,
        status=EvaluationRunStatus.created.value,
        manifest_hash=None,
        case_ids=[case.case_id for case in selected_cases],
        check_timeout_seconds=suite.check_timeout_seconds,
        error=None,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
        repeats=repeats,
        case_bases=case_bases,
    )


def transition_run(
    store: IEvaluationStore,
    run_id: str,
    *,
    expected: Sequence[EvaluationRunStatus],
    new_status: EvaluationRunStatus,
    manifest_hash: str | None = None,
    error: str | None = None,
    finished: bool = False,
) -> EvaluationRunRecord:
    """带守卫的状态迁移，透传存储层的比较并交换结果。

    Args:
        store: 评测存储端口。
        run_id: 目标 run。
        expected: 允许的来源状态集合。
        new_status: 目标状态。
        manifest_hash: 非 ``None`` 时同步冻结。
        error: 非 ``None`` 时同步写入错误摘要。
        finished: True 时同时冻结 ``finished_at``。

    Returns:
        EvaluationRunRecord: 迁移后的最新行。

    Raises:
        RuntimeError: 来源状态与 ``expected`` 不符（存储层抛出的冲突错误），
            调用方（engines service）据此转成 409 conflict 语义。
    """
    return store.update_evaluation_run_status(
        run_id,
        expected_statuses=[status.value for status in expected],
        new_status=new_status.value,
        manifest_hash=manifest_hash,
        error=error,
        finished=finished,
    )


def validate_consent_request(
    *,
    run: EvaluationRunRecord,
    requested_manifest_hash: str,
    current_manifest_hash: str,
) -> None:
    """核对授权请求携带的 manifest hash 与服务端**当前**暂存 payload hash。

    调用方（API/engines）必须先用暂存文件重新计算 hash 再进来，保证
    "所见即所发"：任何在预览之后发生的 payload 漂移都会在这里变成冲突。

    Raises:
        EvaluationValidationError: run 不在可授权状态，或 hash 过期/不匹配。
    """
    if EvaluationRunStatus(run.status) not in CONSENT_ELIGIBLE_RUN_STATUSES:
        raise EvaluationValidationError(f"run 当前状态 '{run.status}' 不接受评审授权")
    if run.manifest_hash is None or requested_manifest_hash != run.manifest_hash:
        raise EvaluationValidationError("授权请求的 manifest hash 与 run 冻结值不一致")
    if requested_manifest_hash != current_manifest_hash:
        raise EvaluationValidationError("payload 在预览后已变化，请重新查看后再授权")


def build_evaluation_summary(
    results: Sequence[EvaluationResultRecord],
    scores: Sequence[EvaluationScoreRecord],
) -> EvaluationSummary:
    """从逐评审明细派生每维中位数 / min / max / N（不保存第二份统计事实）。

    只统计 :data:`SCORING_STATUSES` 内的评分；其它状态全部进 ``status_counts``
    桶。单一有效评审不产生"一致"表述——聚合值照常给出，页数展示层依据
    ``valid_count`` 决定文案。
    """
    valid_by_preset: dict[str, dict[str, list[int]]] = {}
    valid_global: dict[str, list[int]] = {key: [] for key in EVALUATION_RUBRIC_DIMENSIONS}
    status_counts: dict[str, int] = {}
    all_valid_judges: set[str] = set()
    for score in scores:
        if score.score_status in SCORING_STATUSES:
            all_valid_judges.add(score.judge_preset_id)
            bucket = valid_by_preset.setdefault(score.execution_preset_id, {})
            for dimension, value in score.dimension_scores.items():
                if dimension in valid_global:
                    valid_global[dimension].append(value)
                    bucket.setdefault(dimension, []).append(value)
        else:
            status_counts[score.score_status] = status_counts.get(score.score_status, 0) + 1
    for result in results:
        key = f"result:{result.result_status}"
        status_counts[key] = status_counts.get(key, 0) + 1
    by_execution_preset = {
        preset: _aggregate_dimensions(buckets)
        for preset, buckets in sorted(valid_by_preset.items())
    }
    return EvaluationSummary(
        aggregates=_aggregate_dimensions(valid_global),
        by_execution_preset=by_execution_preset,
        status_counts=status_counts,
        valid_judge_count=len(all_valid_judges),
    )


def derive_case_calibration(
    runs: Sequence[EvaluationRunRecord],
    results: Sequence[EvaluationResultRecord],
    suite_hash: str,
) -> list[dict[str, Any]]:
    """从已完成校准 run 与结果明细派生每题校准状态。"""
    eligible_runs = [
        run
        for run in runs
        if run.suite_hash == suite_hash
        and run.status == EvaluationRunStatus.completed.value
        and run.repeats >= 3
        and {str(snapshot.get("preset_name")) for snapshot in run.execution_preset_snapshots}
        == CALIBRATION_PRESET_NAMES
        and not run.judge_preset_snapshots
    ]
    suite_case_ids = list(
        dict.fromkeys(case_id for run in eligible_runs for case_id in run.case_ids)
    )
    calibration_by_case: dict[str, dict[str, Any]] = {
        case_id: {"case_id": case_id, "calibrated": False, "noop": 0, "gold": 0}
        for case_id in suite_case_ids
    }
    results_by_run_and_case: dict[tuple[str, str], list[EvaluationResultRecord]] = {}
    for result in results:
        results_by_run_and_case.setdefault((result.run_id, result.case_id), []).append(result)
    for run in eligible_runs:
        for case_id in run.case_ids:
            case_results = results_by_run_and_case.get((run.run_id, case_id), [])
            noop_results = [
                result
                for result in case_results
                if result.execution_preset_id == CALIBRATION_PRESET_NOOP
            ]
            gold_results = [
                result
                for result in case_results
                if result.execution_preset_id == CALIBRATION_PRESET_GOLD
            ]
            expected_count = run.repeats
            expected_repeat_indexes = list(range(expected_count))
            noop_valid = sorted(
                result.repeat_index for result in noop_results
            ) == expected_repeat_indexes and all(
                result.result_status == "succeeded"
                and result.check_status == EvaluationCheckStatus.failed.value
                for result in noop_results
            )
            gold_valid = sorted(
                result.repeat_index for result in gold_results
            ) == expected_repeat_indexes and all(
                result.result_status == "succeeded"
                and result.check_status == EvaluationCheckStatus.passed.value
                for result in gold_results
            )
            case_state = calibration_by_case.setdefault(
                case_id, {"case_id": case_id, "calibrated": False, "noop": 0, "gold": 0}
            )
            case_state["noop"] = max(case_state["noop"], len(noop_results))
            case_state["gold"] = max(case_state["gold"], len(gold_results))
            case_state["calibrated"] = case_state["calibrated"] or (noop_valid and gold_valid)
    return [calibration_by_case[case_id] for case_id in sorted(calibration_by_case)]


def build_stability_summary(
    results: Sequence[EvaluationResultRecord],
    expected_repeats: int | None = None,
) -> list[EvaluationStability]:
    """按题目与预设汇总重复结论，并独立统计执行故障。

    ``expected_repeats`` 用 run 快照里的重复数约束结论完整度；旧调用方不
    提供时，仅凭当前结果行数量判断。
    """
    grouped_results: dict[tuple[str, str], list[EvaluationResultRecord]] = {}
    for result in results:
        grouped_results.setdefault((result.case_id, result.execution_preset_id), []).append(result)
    summaries: list[EvaluationStability] = []
    execution_fault_statuses = {"failed", "timeout", "cancelled"}
    for (case_id, preset_id), repeated_results in sorted(grouped_results.items()):
        ordered_results = sorted(repeated_results, key=lambda result: result.repeat_index)
        graded_checks = [
            result.check_status
            for result in ordered_results
            if result.result_status == "succeeded"
            and result.check_status
            in {EvaluationCheckStatus.passed.value, EvaluationCheckStatus.failed.value}
        ]
        stable: bool | None
        if len(set(graded_checks)) > 1:
            stable = False
        elif (
            graded_checks
            and len(graded_checks) == len(ordered_results)
            and (expected_repeats is None or len(ordered_results) == expected_repeats)
        ):
            stable = True
        else:
            stable = None
        summaries.append(
            EvaluationStability(
                case_id=case_id,
                execution_preset_id=preset_id,
                repeat_count=len(ordered_results),
                passed_count=sum(
                    result.check_status == EvaluationCheckStatus.passed.value
                    and result.result_status == "succeeded"
                    for result in ordered_results
                ),
                execution_fault_count=sum(
                    result.result_status in execution_fault_statuses for result in ordered_results
                ),
                check_failure_count=sum(
                    result.result_status == "succeeded"
                    and result.check_status == EvaluationCheckStatus.failed.value
                    for result in ordered_results
                ),
                stable=stable,
            )
        )
    return summaries


def _aggregate_dimensions(
    values_by_dimension: Mapping[str, Sequence[int]],
) -> list[EvaluationDimensionAggregate]:
    """把维度 -> 分数列表映射收敛为有序聚合列表。"""
    aggregates: list[EvaluationDimensionAggregate] = []
    for dimension in EVALUATION_RUBRIC_DIMENSIONS:
        values = list(values_by_dimension.get(dimension, []))
        if not values:
            aggregates.append(
                EvaluationDimensionAggregate(
                    dimension=dimension, median=None, minimum=None, maximum=None, valid_count=0
                )
            )
            continue
        aggregates.append(
            EvaluationDimensionAggregate(
                dimension=dimension,
                median=float(statistics.median(values)),
                minimum=min(values),
                maximum=max(values),
                valid_count=len(values),
            )
        )
    return aggregates


def recover_interrupted_runs(store: IEvaluationStore) -> int:
    """Console 启动自愈：活跃 run 一律标 interrupted，等待用户删除或新建 run 重跑。

    不自动重跑 Agent：候选执行可能已产生外部 provider 调用，自动重试会
    把一次手动评测变成不可解释的重复副作用（PRD §6）。
    """
    affected = store.mark_interrupted_evaluation_runs(_utc_now_iso())
    if affected:
        _logger.info("模型评测自愈：标记 %d 个未完成 run 为 interrupted。", affected)
    return affected


def summarize_run_payload_state(run: EvaluationRunRecord) -> dict[str, Any]:
    """给 DTO 用的 run 阶段语义摘要（是否有待授权 payload、是否已结束）。"""
    status = EvaluationRunStatus(run.status)
    return {
        "is_active": status in ACTIVE_RUN_STATUSES,
        "awaiting_consent": status is EvaluationRunStatus.awaiting_judge_consent,
        "judge_selected": bool(run.judge_preset_snapshots),
    }


def build_unreviewed_scores(
    run: EvaluationRunRecord,
    results: Sequence[EvaluationResultRecord],
    *,
    score_status: EvaluationScoreStatus,
    rationale_summary: str,
) -> list[EvaluationScoreRecord]:
    """为「评审者没有评过」的候选 × judge 组合生成独立状态评分行。

    拒绝外发落 ``unauthorized``、取消评测落 ``cancelled``：页面据此区分
    「评审者被拒绝 / 被取消」与「评审者缺席」，两者都不产生任何分数。

    Args:
        run: 目标评测 run（提供 judge 快照与 rubric 版本）。
        results: 该 run 下需要留痕的候选结果。
        score_status: 留痕状态（永不使用 valid，也永不带分数）。
        rationale_summary: 页面可读的原因文案。

    Returns:
        list[EvaluationScoreRecord]: 每个候选 × judge 组合一条空分数行。

    Raises:
        ValueError: ``score_status`` 是 valid（valid 必须由真实评审派生）。
    """
    if score_status is EvaluationScoreStatus.valid:
        raise ValueError("valid 评分只能来自评审响应，不能由留痕生成")
    created_at = _utc_now_iso()
    scores: list[EvaluationScoreRecord] = []
    for result in results:
        for judge in run.judge_preset_snapshots:
            scores.append(
                EvaluationScoreRecord(
                    score_id=f"score-{uuid.uuid4().hex[:12]}",
                    run_id=run.run_id,
                    result_id=result.result_id,
                    case_id=result.case_id,
                    execution_preset_id=result.execution_preset_id,
                    judge_preset_id=str(judge.get("preset_name") or ""),
                    judge_model_snapshot=dict(judge),
                    rubric_version=run.rubric_version,
                    score_status=score_status.value,
                    dimension_scores={},
                    rationale_summary=rationale_summary,
                    response_hash=None,
                    duration_seconds=None,
                    usage_summary=None,
                    error=None,
                    created_at=created_at,
                )
            )
    return scores


def _utc_now_iso() -> str:
    """返回 UTC ISO-8601 时间戳（带 +00:00 偏移，机器可读口径）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
