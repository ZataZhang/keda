"""模型评测的 Console 侧编排入口：run 生命周期动作与授权流程。

core 只管规则；本模块把规则接到真实边界上——SQLite store（鸭子端口）、
git 工作区、Agent 执行池、评审（judge）调用边界。API 路由通过
``core.use_cases.model_evaluation_runtime`` 薄 facade 调用这里。

生命周期红线：

- ``POST start`` 只排程候选执行，**从不**触发 judge 调用（FR-5）。
- 授权请求携带的 manifest hash 必须同时命中 run 冻结值与**当前暂存文件**
  重算值，任何一端不符即 409。
- deny / cancel / delete / judging 收尾都会清除暂存 payload；worktree 在
  每个矩阵单元产出 diff 后立即移除。
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from pathlib import Path
from typing import Any

from backend.core.shared.models.model_evaluation import (
    CANCELLABLE_RUN_STATUSES,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
    EvaluationSuite,
    parse_evaluation_suite,
)
from backend.core.use_cases import model_evaluation as evaluation_rules
from backend.engines.agent_runner.factory import (
    create_console_store,
    load_fresh_agent_runner_settings,
    resolve_project_root_path,
)
from backend.engines.model_evaluation import execution, judge_client, workspace
from backend.infrastructure.persistence.console_store_evaluation import (
    EvaluationStoreConflictError,
)

_logger = logging.getLogger(__name__)

SUITE_SUBDIR = Path("tasks") / "evaluation-suites"
#: 单条 payload 预览返回的字符上限。刻意取 diff 字节上限（60k B，JSON 转义后
#: 约翻倍）加固定开销之上的宽裕值，保证默认完整展示；仅超长 suite prompt 等
#: 极端情况会截断，响应以 ``payload_truncated`` 显式标记，hash 仍绑定全文。
PREVIEW_PAYLOAD_LIMIT = 250_000

_lock = threading.Lock()


class EvaluationActionError(RuntimeError):
    """面向 API 的动作级错误，带 HTTP 语义分类。

    Attributes:
        kind: ``not_found`` / ``conflict`` / ``invalid`` 三类。
    """

    def __init__(self, kind: str, message: str) -> None:
        """记录错误分类与可读原因。"""
        super().__init__(message)
        self.kind = kind


# ─────────────────────────────────────────────────────────────────────────────
# store 行 <-> core 记录适配（同字段名镜像 dataclass）
# ─────────────────────────────────────────────────────────────────────────────


def _core_run(row: Any) -> EvaluationRunRecord | None:
    return EvaluationRunRecord(**dataclasses.asdict(row)) if row is not None else None


def _core_result(row: Any) -> EvaluationResultRecord:
    return EvaluationResultRecord(**dataclasses.asdict(row))


def _core_score(row: Any) -> EvaluationScoreRecord:
    return EvaluationScoreRecord(**dataclasses.asdict(row))


def _store() -> Any:
    return create_console_store()


# ─────────────────────────────────────────────────────────────────────────────
# suite registry
# ─────────────────────────────────────────────────────────────────────────────


def suite_registry_root() -> Path:
    """仓库内固定的版本化 suite 目录（不存在时返回路径但不创建）。"""
    return resolve_project_root_path() / SUITE_SUBDIR


def list_suites() -> list[dict[str, Any]]:
    """列出 registry 目录下的 suite 及其校验状态（维护者校验入口）。

    同一目录内重复的 suite id 无法确定 ``load_suite`` 会取哪个文件，因此
    涉及的两个文件**一起**降级为 invalid，口径与 README 及 ``load_suite``
    的报错一致，避免页面下拉里出现两个同名可选项。
    """
    root = suite_registry_root()
    if not root.is_dir():
        return []
    entries = [_describe_suite_file(path) for path in sorted(root.glob("*.yaml"))]
    id_counts: dict[str, int] = {}
    for entry in entries:
        if entry["valid"]:
            suite_id = str(entry["suite_id"])
            id_counts[suite_id] = id_counts.get(suite_id, 0) + 1
    duplicated_ids = {suite_id for suite_id, count in id_counts.items() if count > 1}
    return [_mark_duplicate_suite_entry(entry, duplicated_ids) for entry in entries]


def _mark_duplicate_suite_entry(entry: dict[str, Any], duplicated_ids: set[str]) -> dict[str, Any]:
    """把 id 落在重复集合里的条目改写成可解释的 invalid 条目。"""
    if not entry["valid"] or entry.get("suite_id") not in duplicated_ids:
        return entry
    return {
        "file": entry["file"],
        "valid": False,
        "errors": [f"suite id '{entry['suite_id']}' 在 registry 目录内重复定义"],
    }


def _describe_suite_file(path: Path) -> dict[str, Any]:
    """解析单个 suite 文件为 DTO；失败时返回可解释的 invalid 条目。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"file": path.name, "valid": False, "errors": [f"读取失败: {exc}"]}
    try:
        suite = parse_evaluation_suite(raw, str(path))
    except ValueError as exc:
        return {"file": path.name, "valid": False, "errors": [str(exc)]}
    return {
        "file": path.name,
        "valid": True,
        "suite_id": suite.suite_id,
        "version": suite.version,
        "title": suite.title,
        "repo_path": suite.repo_path,
        "base_ref": suite.base_ref,
        "content_hash": suite.content_hash,
        "rubric_version": suite.rubric_version,
        "check_timeout_seconds": suite.check_timeout_seconds,
        "cases": [dataclasses.asdict(case) for case in suite.cases],
        "errors": [],
    }


def load_suite(suite_id: str) -> EvaluationSuite:
    """按 suite id 从 registry 加载并解析；不可用时抛动作错误。"""
    root = suite_registry_root()
    if not root.is_dir():
        raise EvaluationActionError("invalid", f"suite registry 目录不存在: {root}")
    matches: list[EvaluationSuite] = []
    problems: list[str] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            suite = parse_evaluation_suite(path.read_text(encoding="utf-8"), str(path))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        if suite.suite_id == suite_id:
            matches.append(suite)
    if not matches:
        detail = f"；校验问题: {' / '.join(problems)}" if problems else ""
        raise EvaluationActionError("not_found", f"suite '{suite_id}' 不存在或校验失败{detail}")
    if len(matches) > 1:
        raise EvaluationActionError("invalid", f"suite id '{suite_id}' 在多个文件中重复定义")
    return matches[0]


# ─────────────────────────────────────────────────────────────────────────────
# 预设目录（执行候选 + 评审预设）
# ─────────────────────────────────────────────────────────────────────────────


def list_preset_options() -> dict[str, Any]:
    """返回页面可选的执行预设与评审预设清单（agent/model、可得费用、评审可用性）。"""
    settings = load_fresh_agent_runner_settings()
    execution_presets = [
        {
            "name": name,
            "agent": preset.agent,
            "model": preset.model,
            "reasoning_effort": preset.reasoning_effort,
            "cost_status": "unknown",
            "cost_estimate": None,
        }
        for name, preset in sorted(settings.presets.items())
    ]
    judge_items: list[dict[str, Any]] = []
    for name, preset in sorted(settings.presets.items()):
        availability = judge_client.probe_judge_availability(name, settings)
        judge_items.append(
            {
                "name": name,
                "agent": preset.agent,
                "model": preset.model,
                "reasoning_effort": preset.reasoning_effort,
                "available": availability.available,
                "reason": availability.reason,
                "cost_status": "unknown",
            }
        )
    return {
        "execution_presets": execution_presets,
        "judge_presets": judge_items,
        "default_judge_presets": list(settings.evaluation.default_judge_presets),
        "max_concurrency": settings.evaluation.max_concurrency,
        "agent_timeout_seconds": settings.evaluation.agent_timeout_seconds,
        "judge_timeout_seconds": settings.evaluation.judge_timeout_seconds,
    }


# ─────────────────────────────────────────────────────────────────────────────
# run 生命周期动作
# ─────────────────────────────────────────────────────────────────────────────


def create_run(
    *, suite_id: str, execution_presets: list[str], judge_presets: list[str]
) -> EvaluationRunRecord:
    """校验并冻结 run 快照（不执行）。评审预设清单为空时只做确定性对照。"""
    settings = load_fresh_agent_runner_settings()
    suite = load_suite(suite_id)
    preset_map = {
        name: {
            "agent": preset.agent,
            "model": preset.model,
            "reasoning_effort": preset.reasoning_effort,
        }
        for name, preset in settings.presets.items()
    }
    try:
        run = evaluation_rules.build_run_snapshot(
            suite=suite,
            execution_preset_names=execution_presets,
            judge_preset_names=judge_presets,
            execution_presets=preset_map,
            judge_availability_probe=lambda name: dataclasses.asdict(
                judge_client.probe_judge_availability(name, settings)
            ),
            base_probe=workspace.probe_base,
        )
    except evaluation_rules.EvaluationValidationError as exc:
        raise EvaluationActionError("invalid", str(exc)) from exc
    _store().save_evaluation_run(run)
    return run


def start_run(run_id: str) -> EvaluationRunRecord:
    """显式启动候选执行：created -> queued 并排程后台任务。不触发任何 judge 调用。"""
    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None:
        raise EvaluationActionError("not_found", f"run '{run_id}' 不存在")
    if EvaluationRunStatus(run.status) is EvaluationRunStatus.queued:
        return run
    try:
        transitioned = evaluation_rules.transition_run(
            store,
            run_id,
            expected=[EvaluationRunStatus.created],
            new_status=EvaluationRunStatus.queued,
        )
    except EvaluationStoreConflictError as exc:
        raise EvaluationActionError("conflict", str(exc)) from exc
    execution.schedule_run_execution(run_id)
    return _core_run(transitioned)


def cancel_run(run_id: str) -> EvaluationRunRecord:
    """取消 run：标记 cancelled、把未完成候选落状态并清除暂存 payload。

    在 ``awaiting_judge_consent`` 上取消时，为已产出的候选 × judge 组合补
    ``cancelled`` 评分留痕（不产生任何分数），与 deny 的 ``unauthorized`` 一样
    让页面能区分「评审者被拒绝 / 被取消」与「根本没选评审者」。
    """
    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None:
        raise EvaluationActionError("not_found", f"run '{run_id}' 不存在")
    status = EvaluationRunStatus(run.status)
    if status not in CANCELLABLE_RUN_STATUSES:
        raise EvaluationActionError(
            "conflict", f"run 状态 '{run.status}' 不可取消（judging 已获授权，不撤回）"
        )
    try:
        transitioned = evaluation_rules.transition_run(
            store,
            run_id,
            expected=list(CANCELLABLE_RUN_STATUSES),
            new_status=EvaluationRunStatus.cancelled,
            finished=True,
        )
    except EvaluationStoreConflictError as exc:
        raise EvaluationActionError("conflict", str(exc)) from exc
    _mark_unfinished_results_cancelled(store, run_id)
    if status is EvaluationRunStatus.awaiting_judge_consent:
        _record_cancelled_scores(store, run)
    execution.purge_run_artifacts(run_id)
    return _core_run(transitioned)


def _record_cancelled_scores(store: Any, run: EvaluationRunRecord) -> None:
    """授权前取消：为已产出的候选补 ``cancelled`` 留痕，区分「评审被取消」与「评审缺席」。"""
    scored_pairs = {
        (str(row.result_id), str(row.judge_preset_id))
        for row in store.list_evaluation_scores(run.run_id)
    }
    results = [_core_result(row) for row in store.list_evaluation_results(run.run_id)]
    for score in evaluation_rules.build_unreviewed_scores(
        run,
        results,
        score_status=EvaluationScoreStatus.cancelled,
        rationale_summary="用户在授权前取消了评测，未发送任何评审请求",
    ):
        if (score.result_id, score.judge_preset_id) in scored_pairs:
            continue
        store.save_evaluation_score(score)


def _mark_unfinished_results_cancelled(store: Any, run_id: str) -> None:
    """把 pending/running 的候选收敛为 cancelled（执行线程同样会在单元边界自检）。"""
    from backend.core.shared.models.model_evaluation import EvaluationResultStatus

    for row in store.list_evaluation_results(run_id):
        result = _core_result(row)
        if result.result_status in {
            EvaluationResultStatus.pending.value,
            EvaluationResultStatus.running.value,
        }:
            result = dataclasses.replace(
                result,
                result_status=EvaluationResultStatus.cancelled.value,
                updated_at=execution.now_iso(),
            )
            store.save_evaluation_result(result)


def delete_run(run_id: str) -> bool:
    """级联删除 run 的 DB 行与全部本地产物；不存在返回 False。"""
    store = _store()
    removed = store.delete_evaluation_run(run_id)
    if removed is None:
        return False
    execution.purge_run_worktrees(_core_run(removed))
    execution.purge_run_artifacts(run_id)
    return True


def get_run_detail(run_id: str) -> dict[str, Any]:
    """run + 候选结果 + 逐评审评分 + 从明细派生的汇总（fresh store 读取）。"""
    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None:
        raise EvaluationActionError("not_found", f"run '{run_id}' 不存在")
    results = [_core_result(row) for row in store.list_evaluation_results(run_id)]
    scores = [_core_score(row) for row in store.list_evaluation_scores(run_id)]
    summary = evaluation_rules.build_evaluation_summary(results, scores)
    return {
        "run": run,
        "results": results,
        "scores": scores,
        "summary": summary,
        "payload_state": evaluation_rules.summarize_run_payload_state(run),
    }


def list_runs() -> list[EvaluationRunRecord]:
    """按创建时间倒序列出全部 run。"""
    return [_core_run(row) for row in _store().list_evaluation_runs()]


def recover_interrupted() -> int:
    """``kc console`` 启动自愈：活跃 run 标 interrupted，并清掉其本机暂存 payload。

    自愈后 run 不再可能走到授权/评分收尾，遗留的暂存内容（候选代码 diff）
    若不在此处清除就会无主地留在磁盘上。worktree 清理留给 ``delete_run``：
    那要调 git，不能压在 console 启动路径上。
    """
    store = _store()
    active_statuses = {status.value for status in evaluation_rules.ACTIVE_RUN_STATUSES}
    stale_run_ids = [
        str(row.run_id)
        for row in store.list_evaluation_runs()
        if str(row.status) in active_statuses
    ]
    affected = evaluation_rules.recover_interrupted_runs(store)
    for run_id in stale_run_ids:
        execution.purge_run_artifacts(run_id)
    if affected:
        _logger.info(
            "模型评测自愈：清除 %d 个 interrupted run 的暂存 payload。", len(stale_run_ids)
        )
    return affected


# ─────────────────────────────────────────────────────────────────────────────
# judge 预览与授权
# ─────────────────────────────────────────────────────────────────────────────


def get_judge_preview(run_id: str) -> dict[str, Any]:
    """返回当前待审 manifest 与逐条**完整** payload 文本（预览即实际发送内容）。"""
    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None:
        raise EvaluationActionError("not_found", f"run '{run_id}' 不存在")
    if EvaluationRunStatus(run.status) is not EvaluationRunStatus.awaiting_judge_consent:
        raise EvaluationActionError("conflict", f"run 状态 '{run.status}' 没有待授权的评审 payload")
    manifest = execution.read_manifest(run_id)
    if manifest is None:
        raise EvaluationActionError("conflict", "暂存 manifest 缺失，请重跑或删除该 run")
    entries: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        text = execution.read_staged_payload(run_id, str(entry["payload_file"]))
        entries.append(
            {
                **entry,
                "payload_available": text is not None,
                "payload_preview": (text or "")[:PREVIEW_PAYLOAD_LIMIT],
                "payload_truncated": text is not None and len(text) > PREVIEW_PAYLOAD_LIMIT,
            }
        )
    return {
        "run_id": run_id,
        "manifest_hash": execution.current_manifest_hash(run_id),
        "frozen_manifest_hash": run.manifest_hash,
        "judge_targets": list(run.judge_preset_snapshots),
        "entries": entries,
        "estimated_requests": len(entries) * len(run.judge_preset_snapshots),
        "sensitive_notice": ("payload 含候选代码差异，可能包含敏感内容；发送后无法由本机撤回。"),
    }


def submit_consent(*, run_id: str, decision: str, manifest_hash: str) -> EvaluationRunRecord:
    """授权动作：approve 推进 judging 并排程评审；deny 收尾并清除暂存 payload。

    服务端在发出任何评审请求前重新核对：请求 hash == run 冻结 hash ==
    当前暂存文件重算 hash。过期或不符一律 conflict，且**不会**发起调用。
    """
    if decision not in {"approve", "deny"}:
        raise EvaluationActionError("invalid", "decision 必须是 approve 或 deny")
    store = _store()
    with _lock:
        run = _core_run(store.get_evaluation_run(run_id))
        if run is None:
            raise EvaluationActionError("not_found", f"run '{run_id}' 不存在")
        current_hash = execution.current_manifest_hash(run_id)
        try:
            evaluation_rules.validate_consent_request(
                run=run,
                requested_manifest_hash=manifest_hash,
                current_manifest_hash=current_hash or "",
            )
        except evaluation_rules.EvaluationValidationError as exc:
            raise EvaluationActionError("conflict", str(exc)) from exc
        try:
            if decision == "approve":
                evaluation_rules.transition_run(
                    store,
                    run_id,
                    expected=list(evaluation_rules.CONSENT_ELIGIBLE_RUN_STATUSES),
                    new_status=EvaluationRunStatus.judging,
                )
            else:
                evaluation_rules.transition_run(
                    store,
                    run_id,
                    expected=list(evaluation_rules.CONSENT_ELIGIBLE_RUN_STATUSES),
                    new_status=EvaluationRunStatus.completed,
                    finished=True,
                )
        except EvaluationStoreConflictError as exc:
            raise EvaluationActionError("conflict", str(exc)) from exc
        if decision == "deny":
            results = [_core_result(row) for row in store.list_evaluation_results(run_id)]
            for score in evaluation_rules.build_unreviewed_scores(
                run,
                results,
                score_status=EvaluationScoreStatus.unauthorized,
                rationale_summary="用户拒绝外发评审内容，未发送任何评审请求",
            ):
                store.save_evaluation_score(score)
            execution.purge_run_artifacts(run_id)
    if decision == "approve":
        execution.schedule_run_judging(run_id)
    return _core_run(store.get_evaluation_run(run_id))
