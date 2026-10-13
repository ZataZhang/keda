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
from dataclasses import dataclass, field
from typing import Any

from backend.core.shared.interfaces.evaluation_store import IEvaluationStore
from backend.core.shared.models.model_evaluation import (
    ACTIVE_RUN_STATUSES,
    CONSENT_ELIGIBLE_RUN_STATUSES,
    EVALUATION_RUBRIC_DIMENSIONS,
    SCORING_STATUSES,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
    EvaluationSuite,
    build_manifest_hash,
)

_logger = logging.getLogger(__name__)

#: run 创建时矩阵规模上限（case 数 × 执行预设数），防呆而不是计费门禁。
MAX_EVALUATION_MATRIX_CELLS = 32


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


def build_run_snapshot(
    *,
    suite: EvaluationSuite,
    execution_preset_names: Sequence[str],
    judge_preset_names: Sequence[str],
    execution_presets: Mapping[str, Mapping[str, Any]],
    judge_presets: Mapping[str, Mapping[str, Any]],
    judge_availability_probe: Callable[[str], Mapping[str, Any]],
    base_probe: Callable[[str, str], tuple[str, str]],
) -> EvaluationRunRecord:
    """校验评测矩阵并冻结 run 配置快照（不落库、不执行）。

    Args:
        suite: 已通过 :func:`parse_evaluation_suite` 的评测集。
        execution_preset_names: 用户选择的执行预设名单（≥2 才有对照意义，
            但 1 个也允许，页面负责提示）。
        judge_preset_names: 用户选择的评审预设名单；为空表示本轮只做
            确定性检查对照，候选完成后不进入评审授权。
        execution_presets: 预设名 -> 解析后的 ``(agent, model, reasoning_effort)``
            快照字典（来自现有 agent_runner 配置，不重新定义模型目录）。
        judge_presets: 评审预设名 -> 配置字典（model/temperature/base_url/api_key_env）。
        judge_availability_probe: engines 注入的凭据/模型可解析性探针；返回
            ``{"available": bool, "provider": str, "model": str, "reason": str}``。
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
    unknown_execution = [name for name in execution_preset_names if name not in execution_presets]
    if unknown_execution:
        problems.append(f"执行预设不存在: {', '.join(unknown_execution)}")
    unknown_judge = [name for name in judge_preset_names if name not in judge_presets]
    if unknown_judge:
        problems.append(f"评审预设不存在: {', '.join(unknown_judge)}")
    matrix_cells = len(suite.cases) * len(execution_preset_names)
    if matrix_cells > MAX_EVALUATION_MATRIX_CELLS:
        problems.append(
            f"矩阵规模 {matrix_cells} 超过上限 {MAX_EVALUATION_MATRIX_CELLS}（cases × presets）"
        )

    judge_probes: dict[str, Mapping[str, Any]] = {}
    for judge_name in judge_preset_names:
        probe = judge_availability_probe(judge_name)
        if not probe.get("available"):
            problems.append(
                f"评审预设 '{judge_name}' 不可用: {probe.get('reason') or '凭据或模型无法解析'}"
            )
        judge_probes[judge_name] = probe

    try:
        base_commit, base_tree_hash = base_probe(suite.repo_path, suite.base_ref)
    except ValueError as exc:
        problems.append(f"基线解析失败: {exc}")
        base_commit, base_tree_hash = "", ""

    if problems:
        raise EvaluationValidationError("；".join(problems))

    created_at = _utc_now_iso()
    execution_snapshots = [
        {
            "preset_name": name,
            **dict(execution_presets[name]),
            "cost_status": "unknown",
            "cost_estimate": None,
        }
        for name in execution_preset_names
    ]
    judge_snapshots = [
        {
            "preset_name": name,
            "model": str(judge_presets[name].get("model") or ""),
            "temperature": judge_presets[name].get("temperature"),
            "provider": judge_probes[name].get("provider"),
            "api_key_env": judge_presets[name].get("api_key_env"),
            "base_url": judge_presets[name].get("base_url"),
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
        base_commit=base_commit,
        base_tree_hash=base_tree_hash,
        execution_preset_snapshots=execution_snapshots,
        judge_preset_snapshots=judge_snapshots,
        status=EvaluationRunStatus.created.value,
        manifest_hash=None,
        case_ids=list(suite.case_ids),
        check_timeout_seconds=suite.check_timeout_seconds,
        error=None,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
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
    """带守卫的状态迁移；非法来源状态抛 :class:`EvaluationStateConflictError`。"""
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


def manifest_hash_for_entries(entries: Sequence[Mapping[str, Any]]) -> str:
    """从 manifest 条目列表派生整体 hash（授权绑定的服务端重算口径）。"""
    return build_manifest_hash([dict(entry) for entry in entries])


def recover_interrupted_runs(store: IEvaluationStore) -> int:
    """Console 启动自愈：活跃 run 一律标 interrupted，等待用户显式恢复或删除。

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


@dataclass(frozen=True)
class ConsentDecisionOutcome:
    """授权动作的结果语义：进入 judging / 已拒绝并清理 / 冲突。"""

    new_run_status: EvaluationRunStatus
    provider_requests_sent: int = 0
    cleanup_required: bool = False
    notes: dict[str, Any] = field(default_factory=dict)


def apply_consent_outcome(
    store: IEvaluationStore,
    run: EvaluationRunRecord,
    *,
    approved: bool,
    current_manifest_hash: str,
) -> ConsentDecisionOutcome:
    """在 core 语义层落实授权决策（状态迁移 + 未授权评分行的显式留痕）。

    approve 走 CAS 把 run 推进到 judging；deny 把 run 收尾为 completed，并为
    每个候选 × judge 组合写入 ``unauthorized`` 评分行——页面据此区分
    "评审者被拒绝" 与 "评审者缺席"。
    """
    validate_consent_request(
        run=run,
        requested_manifest_hash=run.manifest_hash or "",
        current_manifest_hash=current_manifest_hash,
    )
    if approved:
        transition_run(
            store,
            run.run_id,
            expected=list(CONSENT_ELIGIBLE_RUN_STATUSES),
            new_status=EvaluationRunStatus.judging,
        )
        return ConsentDecisionOutcome(new_run_status=EvaluationRunStatus.judging)
    transition_run(
        store,
        run.run_id,
        expected=list(CONSENT_ELIGIBLE_RUN_STATUSES),
        new_status=EvaluationRunStatus.completed,
        finished=True,
    )
    return ConsentDecisionOutcome(
        new_run_status=EvaluationRunStatus.completed,
        cleanup_required=True,
        notes={"recorded": "unauthorized_scores"},
    )


def build_unauthorized_scores(
    run: EvaluationRunRecord,
    results: Sequence[EvaluationResultRecord],
) -> list[EvaluationScoreRecord]:
    """为拒绝授权的场景生成逐候选 × judge 的 unauthorized 评分行。"""
    created_at = _utc_now_iso()
    rubric_version = run.rubric_version
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
                    rubric_version=rubric_version,
                    score_status=EvaluationScoreStatus.unauthorized.value,
                    dimension_scores={},
                    rationale_summary="用户拒绝外发评审内容，未发送任何评审请求",
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
