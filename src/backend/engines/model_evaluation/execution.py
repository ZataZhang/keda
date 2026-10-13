"""评测后台编排：候选执行矩阵与 judge 排程。

执行模型：每个 run 一个后台线程（外层池），run 内矩阵单元（case ×
execution preset）用小并发线程池执行，上限同时受
``evaluation.max_concurrency`` 与既有 runner 并发 ceiling 约束；矩阵池的
上限是 **per-run** 的，因此候选真正落地（worktree + Agent 子进程 + check）
另外要过一道进程级闸门，保证多个 run 同时在跑时主机上的在飞候选数仍是同一
个 ceiling，而不是 N × ceiling。候选之间**绝不共享 worktree**；diff 产出后
worktree 即移除，暂存 payload 只在等待授权 / 评分期间存在。
"""

from __future__ import annotations

import dataclasses
import logging
import re
import subprocess
import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.core.shared.models.model_evaluation import (
    DIFF_PAYLOAD_LIMIT_BYTES,
    EvaluationCheckStatus,
    EvaluationResultRecord,
    EvaluationResultStatus,
    EvaluationRunRecord,
    EvaluationRunStatus,
    EvaluationScoreRecord,
    EvaluationScoreStatus,
    EvaluationSuiteCase,
    build_judge_payload_document,
    build_manifest_hash,
    parse_judge_response,
    preset_snapshot_mismatch,
    sha256_text,
)
from backend.engines.model_evaluation import agent_adapter, checks, judge_client, workspace
from backend.engines.model_evaluation.store_adapter import (
    core_result as _core_result,
    core_run as _core_run,
    now_iso,
    store as _store,
)

_logger = logging.getLogger(__name__)

#: run 级后台任务共享的外层池；并发刻意小于矩阵池，避免无界 worker。
_OUTER_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="evaluation-run")
#: 摘要字段保留长度（agent 输出尾部），刻意小于完整 transcript。
_AGENT_SUMMARY_LIMIT = 1200

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: 跨 run 共享的候选执行闸门。矩阵池的 ceiling 是 **per-run** 的，而外层池允许
#: 4 个 run 同时在跑，实际 worktree/Agent 子进程数会叠成 N × ceiling，突破 runner
#: 主机容量约定。这里用进程级信号量把「同时在跑的候选单元数」压回同一个 ceiling。
#: 首个进入闸门的调用按其 ceiling 定型，之后所有 run 复用同一实例。
_HOST_GATE: threading.BoundedSemaphore | None = None
_HOST_GATE_LOCK = threading.Lock()


def _candidate_ceiling(settings: Any) -> int:
    """候选矩阵的单飞上限：评测自身上限与 runner 主机容量取小。"""
    return max(1, min(settings.evaluation.max_concurrency, settings.runner.max_concurrent_issues))


def _host_gate(settings: Any) -> threading.BoundedSemaphore:
    """返回进程级候选闸门（懒创建，创建后槽位固定）。"""
    global _HOST_GATE
    with _HOST_GATE_LOCK:
        if _HOST_GATE is None:
            _HOST_GATE = threading.BoundedSemaphore(_candidate_ceiling(settings))
        return _HOST_GATE


def schedule_run_execution(run_id: str) -> None:
    """把候选执行矩阵排进后台池（API 调用即返回，不阻塞请求线程）。"""
    _OUTER_EXECUTOR.submit(_safe_entry, run_run_execution, run_id)


def schedule_run_judging(run_id: str) -> None:
    """把已授权的评审调用排进后台池。只允许在 consent approve 之后到达这里。"""
    _OUTER_EXECUTOR.submit(_safe_entry, run_run_judging, run_id)


def _safe_entry(action: Any, run_id: str) -> None:
    """后台入口兜底：异常必须留日志并把 run 收敛到可解释状态。"""
    try:
        action(run_id)
    except Exception as exc:  # noqa: BLE001 - 后台线程不允许无声死亡
        _logger.exception("评测后台任务 '%s' 崩溃: %s", run_id, exc)
        try:
            _store().update_evaluation_run_status(
                run_id,
                expected_statuses=[
                    status.value
                    for status in EvaluationRunStatus
                    if status.value in {"queued", "running", "awaiting_judge_consent", "judging"}
                ],
                new_status=EvaluationRunStatus.interrupted.value,
                error=f"后台执行崩溃: {exc}"[:400],
            )
        except Exception as inner_exc:  # noqa: BLE001 - 收敛失败只剩日志
            _logger.warning("评测 run '%s' 崩溃状态收敛失败: %s", run_id, inner_exc)


# ─────────────────────────────────────────────────────────────────────────────
# 候选执行阶段
# ─────────────────────────────────────────────────────────────────────────────


def run_run_execution(run_id: str) -> None:
    """执行一个 queued run 的全部矩阵单元，收尾进入授权或直接完成。"""
    from backend.engines.agent_runner.factory import load_fresh_agent_runner_settings
    from backend.engines.model_evaluation import service

    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None or EvaluationRunStatus(run.status) is not EvaluationRunStatus.queued:
        return
    settings = load_fresh_agent_runner_settings()
    try:
        suite = service.load_suite(run.suite_id)
    except service.EvaluationActionError as exc:
        store.update_evaluation_run_status(
            run_id,
            expected_statuses=[EvaluationRunStatus.queued.value],
            new_status=EvaluationRunStatus.completed.value,
            error=f"suite 在执行前不可用: {exc}"[:400],
            finished=True,
        )
        return
    if suite.content_hash != run.suite_hash:
        store.update_evaluation_run_status(
            run_id,
            expected_statuses=[EvaluationRunStatus.queued.value],
            new_status=EvaluationRunStatus.completed.value,
            error="suite 内容在 run 创建后发生变化，已拒绝执行（请新建 run）"[:400],
            finished=True,
        )
        return
    store.update_evaluation_run_status(
        run_id,
        expected_statuses=[EvaluationRunStatus.queued.value],
        new_status=EvaluationRunStatus.running.value,
    )
    root = workspace.evaluation_root(settings.console.history_db_path)
    cases_by_id = {case.case_id: case for case in suite.cases}
    cells = [
        (cases_by_id[case_id], preset_snapshot)
        for case_id in run.case_ids
        if case_id in cases_by_id
        for preset_snapshot in run.execution_preset_snapshots
    ]
    ceiling = _candidate_ceiling(settings)
    staged_entries: list[dict[str, Any]] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=ceiling, thread_name_prefix="evaluation-cell") as pool:
        futures = [
            pool.submit(
                _execute_cell, run, cell_case, preset_snapshot, root, settings, staged_entries, lock
            )
            for cell_case, preset_snapshot in cells
        ]
        for future in futures:
            future.result()
    # 逐单元 worktree 已在 diff 后移除；这里把空掉的 run 级父目录也收掉，
    # 避免未删除的 run 在状态目录里无限堆积空壳。
    workspace.remove_directory_if_empty(workspace.run_workspace_dir(root, run_id))
    final_status_row = store.get_evaluation_run(run_id)
    final_status = final_status_row.status if final_status_row else "unknown"
    if final_status != EvaluationRunStatus.running.value:
        # 运行期间被取消/打断：清掉可能已写入的暂存 payload，保留本地结果状态。
        purge_run_artifacts(run_id)
        return
    if run.judge_preset_snapshots and staged_entries:
        write_manifest(run_id, staged_entries)
        manifest_hash = current_manifest_hash(run_id) or build_manifest_hash(staged_entries)
        store.update_evaluation_run_status(
            run_id,
            expected_statuses=[EvaluationRunStatus.running.value],
            new_status=EvaluationRunStatus.awaiting_judge_consent.value,
            manifest_hash=manifest_hash,
        )
    else:
        store.update_evaluation_run_status(
            run_id,
            expected_statuses=[EvaluationRunStatus.running.value],
            new_status=EvaluationRunStatus.completed.value,
            finished=True,
        )


def _execute_cell(
    run: EvaluationRunRecord,
    case: EvaluationSuiteCase,
    preset_snapshot: Mapping[str, Any],
    root: Path,
    settings: Any,
    staged_entries: list[dict[str, Any]],
    lock: threading.Lock,
) -> None:
    """执行一个矩阵单元：干净 worktree -> Agent -> diff -> check -> 清理。"""
    store = _store()
    current = store.get_evaluation_run(run.run_id)
    if current is None or current.status != EvaluationRunStatus.running.value:
        return
    preset_name = str(preset_snapshot.get("preset_name") or "")
    result_id = f"res-{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    result = EvaluationResultRecord(
        result_id=result_id,
        run_id=run.run_id,
        case_id=case.case_id,
        execution_preset_id=preset_name,
        execution_model_snapshot=dict(preset_snapshot),
        base_tree_hash=run.base_tree_hash,
        worktree_path=None,
        result_status=EvaluationResultStatus.pending.value,
        check_status=EvaluationCheckStatus.not_run.value,
        check_recipe=case.check_recipe,
        check_exit_code=None,
        check_output_summary=None,
        agent_summary=None,
        duration_seconds=None,
        usage_summary=None,
        cost_status="unknown",
        diff_hash=None,
        error=None,
        created_at=created_at,
        updated_at=now_iso(),
    )
    store.save_evaluation_result(result)
    worktree_path = (
        workspace.run_workspace_dir(root, run.run_id)
        / f"{_safe(case.case_id)}--{_safe(preset_name)}"
    )
    result = dataclasses.replace(
        result, result_status=EvaluationResultStatus.running.value, updated_at=now_iso()
    )
    store.save_evaluation_result(result)

    diff_text = ""
    outcome: agent_adapter.CandidateAgentOutcome | None = None
    with _host_gate(settings):
        # 闸门排队可能一直在等其他 run 释放槽位，期间本 run 可能已被取消或自愈收敛：
        # 拿到槽位后重新确认，绝不因为「终于排到了」就补发一次 Agent 调用。
        queued_row = store.get_evaluation_run(run.run_id)
        if queued_row is None or queued_row.status != EvaluationRunStatus.running.value:
            store.save_evaluation_result(
                dataclasses.replace(
                    result,
                    result_status=EvaluationResultStatus.cancelled.value,
                    updated_at=now_iso(),
                )
            )
            return
        try:
            live_preset = settings.presets.get(preset_name)
            if live_preset is not None:
                drifted = preset_snapshot_mismatch(
                    preset_snapshot,
                    {
                        "agent": live_preset.agent,
                        "model": live_preset.model,
                        "reasoning_effort": live_preset.reasoning_effort,
                    },
                )
                if drifted:
                    raise ValueError(
                        f"执行预设 '{preset_name}' 配置在 run 创建后已变化（{', '.join(drifted)}），"
                        "与冻结快照不一致，为避免不可解释对照已停止执行"
                    )
            workspace.create_detached_worktree(run.repo_path, run.base_commit, worktree_path)
            result = dataclasses.replace(result, worktree_path=str(worktree_path))
            store.save_evaluation_result(result)
            observed_tree = workspace.worktree_tree_hash(worktree_path)
            if observed_tree != run.base_tree_hash:
                raise ValueError(
                    f"worktree 基线 tree 不一致: 期望 {run.base_tree_hash}，实际 {observed_tree}"
                )
            outcome = agent_adapter.run_candidate(
                preset_name=preset_name,
                prompt=case.prompt,
                worktree_path=worktree_path,
                settings=settings,
                timeout_seconds=settings.evaluation.agent_timeout_seconds,
            )
            diff_text = workspace.collect_diff(worktree_path, run.base_commit)
        except subprocess.TimeoutExpired:
            result = dataclasses.replace(
                result,
                result_status=EvaluationResultStatus.timeout.value,
                error="Agent 执行超时",
                updated_at=now_iso(),
            )
            store.save_evaluation_result(result)
            _cleanup_worktree(run, worktree_path, store, result)
            return
        except (ValueError, workspace.EvaluationWorkspaceError) as exc:
            result = dataclasses.replace(
                result,
                result_status=EvaluationResultStatus.failed.value,
                error=str(exc)[:400],
                updated_at=now_iso(),
            )
            store.save_evaluation_result(result)
            _cleanup_worktree(run, worktree_path, store, result)
            return

        if outcome.timed_out:
            result_status = EvaluationResultStatus.timeout
        elif outcome.ok:
            result_status = EvaluationResultStatus.succeeded
        else:
            result_status = EvaluationResultStatus.failed
        check_outcome: checks.CheckRunOutcome | None = None
        if result_status is EvaluationResultStatus.succeeded:
            try:
                check_outcome = checks.run_check_recipe(
                    case.check_recipe, worktree_path, run.check_timeout_seconds
                )
            except ValueError as exc:
                check_outcome = checks.CheckRunOutcome(
                    status=EvaluationCheckStatus.failed,
                    exit_code=None,
                    output_summary=str(exc)[:200],
                )
        result = dataclasses.replace(
            result,
            result_status=result_status.value,
            check_status=(
                check_outcome.status.value if check_outcome else EvaluationCheckStatus.not_run.value
            ),
            check_exit_code=check_outcome.exit_code if check_outcome else None,
            check_output_summary=check_outcome.output_summary if check_outcome else None,
            agent_summary=_truncate(outcome.summary, _AGENT_SUMMARY_LIMIT),
            duration_seconds=outcome.duration_seconds,
            usage_summary=outcome.usage_summary,
            diff_hash=sha256_text(diff_text),
            error=_truncate(outcome.error, 400),
            updated_at=now_iso(),
        )
        _cleanup_worktree(run, worktree_path, store, result)
        store.save_evaluation_result(result)

    if result_status is EvaluationResultStatus.succeeded and run.judge_preset_snapshots:
        payload_text = build_judge_payload_document(
            run_id=run.run_id,
            result_id=result.result_id,
            case_prompt=case.prompt,
            execution_preset_id=preset_name,
            agent_summary=result.agent_summary,
            result_status=result.result_status,
            check_status=result.check_status,
            check_output_summary=result.check_output_summary,
            diff_text=_truncate_bytes(diff_text, DIFF_PAYLOAD_LIMIT_BYTES),
            rubric_version=run.rubric_version,
        )
        payload_file = f"{result.result_id}.json"
        workspace.write_payload(workspace.staging_dir(root, run.run_id), payload_file, payload_text)
        with lock:
            staged_entries.append(
                {
                    "result_id": result.result_id,
                    "case_id": result.case_id,
                    "execution_preset_id": result.execution_preset_id,
                    "payload_file": payload_file,
                    "payload_sha256": sha256_text(payload_text),
                    "size_bytes": len(payload_text.encode("utf-8")),
                }
            )


def _cleanup_worktree(
    run: EvaluationRunRecord,
    worktree_path: Path,
    store: Any,
    result: EvaluationResultRecord,
) -> None:
    """移除单元 worktree 并把记录里的路径引用置空。"""
    if worktree_path.exists():
        workspace.remove_worktree(run.repo_path, worktree_path)
    if result.worktree_path is not None:
        store.save_evaluation_result(dataclasses.replace(result, worktree_path=None))


# ─────────────────────────────────────────────────────────────────────────────
# manifest 与暂存 payload
# ─────────────────────────────────────────────────────────────────────────────


def write_manifest(run_id: str, entries: list[dict[str, Any]]) -> None:
    """写入 manifest.json（条目按 result_id 排序，hash 口径与 core 一致）。"""
    from backend.engines.agent_runner.factory import load_fresh_agent_runner_settings

    root = workspace.evaluation_root(load_fresh_agent_runner_settings().console.history_db_path)
    ordered = sorted(entries, key=lambda item: item["result_id"])
    workspace.write_json(
        workspace.staging_dir(root, run_id),
        "manifest.json",
        {"run_id": run_id, "entries": ordered},
    )


def read_manifest(run_id: str) -> dict[str, Any] | None:
    """读取当前 run 的 manifest；缺失返回 ``None``。"""
    root = _evaluation_root()
    return workspace.read_json(workspace.staging_dir(root, run_id), "manifest.json")


def read_staged_payload(run_id: str, payload_file: str) -> str | None:
    """按 manifest 声明的文件名读取暂存 payload。"""
    root = _evaluation_root()
    return workspace.read_payload(workspace.staging_dir(root, run_id), payload_file)


def current_manifest_hash(run_id: str) -> str | None:
    """从**当前暂存文件**重算 manifest hash（授权时的 fresh compare 口径）。

    manifest 缺失、任一 payload 缺失或内容漂移都会得到与冻结值不同的 hash
    或 ``None``，授权流程据此拒绝发送。
    """
    manifest = read_manifest(run_id)
    if manifest is None:
        return None
    recomputed: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        text = read_staged_payload(run_id, str(entry.get("payload_file") or ""))
        if text is None:
            return None
        recomputed.append(
            {"result_id": str(entry.get("result_id")), "payload_sha256": sha256_text(text)}
        )
    if not recomputed:
        return None
    return build_manifest_hash(recomputed)


def purge_run_artifacts(run_id: str) -> None:
    """删除一个 run 的暂存 payload 目录（deny / cancel / delete / 评分完成后）。"""
    workspace.purge_directory(_evaluation_root() / "runs" / run_id)


def purge_run_worktrees(run: EvaluationRunRecord) -> None:
    """删除 run 时兜底清理其 worktree 根目录（含 git 引用）。"""
    store = _store()
    root = _evaluation_root()
    for row in store.list_evaluation_results(run.run_id):
        result = _core_result(row)
        if result.worktree_path:
            path = Path(result.worktree_path)
            if path.exists():
                workspace.remove_worktree(run.repo_path, path)
    workspace.purge_directory(workspace.run_workspace_dir(root, run.run_id))
    try:
        workspace._git(Path(run.repo_path), ["worktree", "prune"])
    except (workspace.EvaluationWorkspaceError, OSError):
        _logger.warning("评测清理：git worktree prune 失败 repo=%s", run.repo_path)


def _evaluation_root() -> Path:
    from backend.engines.agent_runner.factory import load_fresh_agent_runner_settings

    return workspace.evaluation_root(load_fresh_agent_runner_settings().console.history_db_path)


# ─────────────────────────────────────────────────────────────────────────────
# judge 阶段
# ─────────────────────────────────────────────────────────────────────────────


def run_run_judging(run_id: str) -> None:
    """对已授权 run 执行全部 judge 调用；每个结果独立留状态。"""
    from backend.engines.agent_runner.factory import load_fresh_agent_runner_settings

    store = _store()
    run = _core_run(store.get_evaluation_run(run_id))
    if run is None or EvaluationRunStatus(run.status) is not EvaluationRunStatus.judging:
        return
    settings = load_fresh_agent_runner_settings()
    manifest = read_manifest(run_id) or {"entries": []}
    entries = list(manifest.get("entries", []))
    judge_names = [
        str(snapshot.get("preset_name") or "") for snapshot in run.judge_preset_snapshots
    ]
    results_by_id = {
        result.result_id: result
        for result in map(_core_result, store.list_evaluation_results(run_id))
    }
    ceiling = max(1, settings.evaluation.max_concurrency)
    with ThreadPoolExecutor(max_workers=ceiling, thread_name_prefix="evaluation-judge") as pool:
        futures = [
            pool.submit(
                _execute_judge,
                run,
                entry,
                judge_name,
                results_by_id,
                settings,
            )
            for entry in entries
            for judge_name in judge_names
        ]
        for future in futures:
            future.result()
    current = store.get_evaluation_run(run_id)
    if current is not None and current.status == EvaluationRunStatus.judging.value:
        store.update_evaluation_run_status(
            run_id,
            expected_statuses=[EvaluationRunStatus.judging.value],
            new_status=EvaluationRunStatus.completed.value,
            finished=True,
        )
    purge_run_artifacts(run_id)


def _execute_judge(
    run: EvaluationRunRecord,
    entry: Mapping[str, Any],
    judge_name: str,
    results_by_id: Mapping[str, EvaluationResultRecord],
    settings: Any,
) -> None:
    """执行一次 judge × 候选的评分调用并落库（任何失败独立留状态）。"""
    store = _store()
    # 强制删除的 run 不再派发新的 provider 调用：future 是预先排好的，
    # 逐个在调用前确认 run 仍在 judging；残余回写另有 store 层丢弃守卫。
    live_run = store.get_evaluation_run(run.run_id)
    if live_run is None or live_run.status != EvaluationRunStatus.judging.value:
        return
    result = results_by_id.get(str(entry.get("result_id") or ""))
    judge_snapshot = next(
        (
            snapshot
            for snapshot in run.judge_preset_snapshots
            if snapshot.get("preset_name") == judge_name
        ),
        {},
    )
    score = EvaluationScoreRecord(
        score_id=f"score-{uuid.uuid4().hex[:12]}",
        run_id=run.run_id,
        result_id=str(entry.get("result_id") or ""),
        case_id=str(entry.get("case_id") or ""),
        execution_preset_id=str(entry.get("execution_preset_id") or ""),
        judge_preset_id=judge_name,
        judge_model_snapshot=dict(judge_snapshot),
        rubric_version=run.rubric_version,
        score_status=EvaluationScoreStatus.invalid.value,
        dimension_scores={},
        rationale_summary=None,
        response_hash=None,
        duration_seconds=None,
        usage_summary=None,
        error=None,
        created_at=now_iso(),
    )
    if result is None:
        store.save_evaluation_score(
            dataclasses.replace(score, error="候选结果缺失，未发起评审请求")
        )
        return
    preset = settings.evaluation.judge_presets.get(judge_name)
    payload_text = read_staged_payload(run.run_id, str(entry.get("payload_file") or ""))
    if preset is None:
        store.save_evaluation_score(
            dataclasses.replace(
                score,
                score_status=EvaluationScoreStatus.provider_error.value,
                error="评审预设已从配置中移除",
            )
        )
        return
    drifted = preset_snapshot_mismatch(
        judge_snapshot,
        {
            "model": preset.model,
            "temperature": preset.temperature,
            "api_key_env": preset.api_key_env,
            "base_url": preset.base_url,
        },
    )
    if drifted:
        # 配置漂移即中止：用户按冻结快照授权外发，实际端点/模型变化后绝不发送。
        drift_reason = (
            f"评审预设 '{judge_name}' 配置在 run 创建后已变化"
            f"（{', '.join(drifted)}），未发起评审请求"
        )[:400]
        store.save_evaluation_score(
            dataclasses.replace(
                score,
                score_status=EvaluationScoreStatus.provider_error.value,
                error=drift_reason,
            )
        )
        return
    if payload_text is None:
        store.save_evaluation_score(
            dataclasses.replace(
                score,
                score_status=EvaluationScoreStatus.provider_error.value,
                error="暂存 payload 缺失，未发起评审请求",
            )
        )
        return
    if sha256_text(payload_text) != str(entry.get("payload_sha256") or ""):
        # 授权后 payload 漂移：宁可不评也不发请求，保证"所批即所发"。
        store.save_evaluation_score(
            dataclasses.replace(
                score,
                score_status=EvaluationScoreStatus.provider_error.value,
                error="payload 与授权时 manifest hash 不一致，未发起评审请求",
            )
        )
        return
    outcome = judge_client.invoke_judge(
        preset,
        payload_text,
        model_factory=_chat_model_factory(),
        timeout_seconds=settings.evaluation.judge_timeout_seconds,
    )
    if outcome.error_status is not None:
        store.save_evaluation_score(
            dataclasses.replace(
                score,
                score_status=outcome.error_status.value,
                duration_seconds=round(outcome.duration_seconds, 3),
                response_hash=sha256_text(outcome.response_text or "")
                if outcome.response_text
                else None,
                error=_truncate(outcome.response_text, 400),
            )
        )
        return
    parsed = parse_judge_response(outcome.response_text or "")
    store.save_evaluation_score(
        dataclasses.replace(
            score,
            score_status=parsed.status.value,
            dimension_scores=dict(parsed.dimension_scores),
            rationale_summary=parsed.rationale_summary,
            response_hash=sha256_text(outcome.response_text or ""),
            duration_seconds=round(outcome.duration_seconds, 3),
            usage_summary=outcome.usage_summary,
            error=None
            if parsed.status is EvaluationScoreStatus.valid
            else "响应不符合 rubric schema",
        )
    )


def _chat_model_factory() -> Any:
    """返回真实 provider 工厂（infrastructure ``create_chat_model``）。"""
    from backend.infrastructure.models.model_loader import create_chat_model

    def factory(model: str, **kwargs: Any) -> Any:
        return create_chat_model(
            model,
            temperature=kwargs.get("temperature", 0.0),
            api_key=kwargs.get("api_key"),
            client_kwargs=kwargs.get("client_kwargs") or None,
        )

    return factory


def _safe(name: str) -> str:
    """把预设/case 名收敛为文件系统安全的片段。"""
    return _SAFE_NAME_RE.sub("_", name)[:64] or "x"


def _truncate(text: str | None, limit: int) -> str | None:
    """按字符截断并加省略号标记；``None`` 原样传递。"""
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _truncate_bytes(text: str, limit: int) -> str:
    """按 UTF-8 字节上限截断 diff 文本（超出标记明确写入 payload）。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return clipped + "\n…[diff 超出评审 payload 字节上限，已截断]"
