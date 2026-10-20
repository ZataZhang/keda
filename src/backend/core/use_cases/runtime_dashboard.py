"""把已注册仓库的 Issue 运行观测组装成 Dashboard / 桌面小窗共用的任务摘要。

设计约束：

- **只读复用**：任务身份与标签来自监控快照（:mod:`monitor_snapshots`），运行终态
  来自运行账本（``IRunHistoryStore``），阶段 / preset / 模型来自 attempt 账本，
  PRD 关联来自生命周期账本（``IPrdLifecycleStore``）。本模块不打开 GitHub、
  不读日志文件、不算改动树，也不新增任何存储。
- **改动树触达读数不在这里**：唯一来源仍是现有
  ``GET /api/v1/agent-runner/backlog/prds/{encoded_path}/progress`` 契约，摘要只带
  ``prd_path``，由展示侧按需取数，避免第二份检测实现漂移。
- **降级如实披露**：任一来源读取失败时把来源名写进 ``unavailable_sources``，
  不把读取失败折叠成「没有任务」，也不把未知状态折算成零。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    IPrdLifecycleStore,
    IRunHistoryStore,
    PrdLifecycleRunRecord,
    RunRecord,
)
from backend.core.shared.models.runtime_task import (
    RUNTIME_TASK_MAX_ROWS,
    RUNTIME_TASK_WINDOW_SECONDS,
    RuntimeTask,
    RuntimeTaskStatus,
    RuntimeTaskSummary,
)
from backend.core.use_cases.monitor_snapshots import SnapshotOverviewResult

_logger = logging.getLogger(__name__)

#: 监控标签到生命周期分桶的唯一映射表。
#:
#: ``agent/review`` 归入 waiting：它表示「等人来评审」，与 ``agent/ready`` 一样
#: 不需要 Agent 继续动作，值守者要做的是等待而不是介入。
_LABEL_STATUS_MAP: dict[str, RuntimeTaskStatus] = {
    "agent/running": RuntimeTaskStatus.RUNNING,
    "agent/supervising": RuntimeTaskStatus.RUNNING,
    "agent/ready": RuntimeTaskStatus.WAITING,
    "agent/review": RuntimeTaskStatus.WAITING,
    "agent/automated-review-passed": RuntimeTaskStatus.WAITING,
    "agent/blocked": RuntimeTaskStatus.BLOCKED,
    "agent/failed": RuntimeTaskStatus.FAILED,
}

#: 运行账本只能证明交付结果；历史失败不能证明 Issue 当前仍然阻塞。
_OUTCOME_STATUS_MAP: dict[str, RuntimeTaskStatus] = {
    "completed": RuntimeTaskStatus.COMPLETED,
}

#: ``unavailable_sources`` 的取值：前端据此把对应面板标为不可用。
SOURCE_MONITOR_SNAPSHOT = "monitor_snapshot"
SOURCE_RUN_HISTORY = "run_history"
SOURCE_PRD_LIFECYCLE = "prd_lifecycle"
SOURCE_ATTEMPT_LEDGER = "attempt_ledger"

#: 快照同步状态里，「整体还不可信」的取值。``partial`` 仍可用（缺的仓库单独披露）。
_UNTRUSTED_SYNC_STATUSES = frozenset({"pending_first_sync"})

#: 只为这些分桶回查 attempt 账本，避免对整表任务做无界读取。
_ATTEMPT_ENRICHED_STATUSES = frozenset(
    {RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.BLOCKED, RuntimeTaskStatus.FAILED}
)

#: 运行账本一次扫描的最大行数：窗口内正常量级远小于此，超出即属历史复盘（Stats 页面）。
_RUN_SCAN_LIMIT = 1000


def parse_runtime_timestamp(value: str | None) -> datetime | None:
    """把账本里的 ISO-8601 字符串解析成带时区时间；坏值返回 ``None``。

    Args:
        value: 账本存储的 ISO-8601 时间戳，可能为空、缺失时区或格式损坏。

    Returns:
        带 UTC 时区的时间；无法解析时返回 ``None``，调用方据此保留「未知」。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_before_runtime_window(started_at: str | None, window_start: datetime) -> bool:
    """判断账本记录是否早于运行看板的有效时间窗口。"""
    parsed_started_at = parse_runtime_timestamp(started_at)
    return parsed_started_at is not None and parsed_started_at < window_start


def status_for_labels(labels: Sequence[str]) -> RuntimeTaskStatus | None:
    """按监控标签推断生命周期分桶；没有已知 `agent/*` 标签时返回 ``None``。

    同时命中多个标签时取「最需要盯」的那个，避免一个 Issue 在标签切换窗口内
    被显示成较宽松的状态。
    """
    matched: RuntimeTaskStatus | None = None
    for label in labels:
        candidate = _LABEL_STATUS_MAP.get(label)
        if candidate is None:
            continue
        if matched is None or candidate.display_order < matched.display_order:
            matched = candidate
    return matched


def _overview_issues(overview_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从 per-repo overview payload 里取出可渲染的 Issue 条目列表。"""
    raw_issues = overview_payload.get("issues")
    if not isinstance(raw_issues, list):
        return []
    return [entry for entry in raw_issues if isinstance(entry, dict)]


def _latest_run_by_issue(runs: Sequence[RunRecord]) -> dict[tuple[str, int], RunRecord]:
    """按 ``repo_id + issue_number`` 保留最近一次运行（``started_at`` 最晚）。"""
    latest: dict[tuple[str, int], RunRecord] = {}
    for run in runs:
        key = (run.repo_id, run.issue_number)
        current = latest.get(key)
        if current is None or run.started_at >= current.started_at:
            latest[key] = run
    return latest


def _latest_prd_path_by_issue(
    lifecycle_runs: Sequence[PrdLifecycleRunRecord],
) -> dict[tuple[str, int], str]:
    """按 ``repo_id + issue_number`` 取最近一次生命周期 run 的 PRD 路径。"""
    latest: dict[tuple[str, int], tuple[str, str]] = {}
    for lifecycle_run in lifecycle_runs:
        if lifecycle_run.issue_number is None or not lifecycle_run.prd_path:
            continue
        key = (lifecycle_run.repo_id, lifecycle_run.issue_number)
        current = latest.get(key)
        if current is None or lifecycle_run.started_at >= current[0]:
            latest[key] = (lifecycle_run.started_at, lifecycle_run.prd_path)
    return {key: value[1] for key, value in latest.items()}


def _latest_lifecycle_by_issue(
    lifecycle_runs: Sequence[PrdLifecycleRunRecord],
) -> dict[tuple[str, int], PrdLifecycleRunRecord]:
    """按 ``repo_id + issue_number`` 保留最近一次生命周期 run。"""
    latest: dict[tuple[str, int], PrdLifecycleRunRecord] = {}
    for lifecycle_run in lifecycle_runs:
        if lifecycle_run.issue_number is None:
            continue
        key = (lifecycle_run.repo_id, lifecycle_run.issue_number)
        current = latest.get(key)
        if current is None or lifecycle_run.started_at >= current.started_at:
            latest[key] = lifecycle_run
    return latest


def _timeline_phase(issue_entry: Mapping[str, Any]) -> str | None:
    """取 Issue 监控时间线里最后一个阶段名；没有条目时返回 ``None``。"""
    timeline = issue_entry.get("timeline")
    if not isinstance(timeline, list):
        return None
    for entry in reversed(timeline):
        if isinstance(entry, dict):
            phase = entry.get("phase")
            if isinstance(phase, str) and phase:
                return phase
    return None


def _snapshot_task(
    *,
    repo_id: str,
    repo_display_name: str,
    issue_entry: Mapping[str, Any],
    run_record: RunRecord | None,
    prd_path: str | None,
) -> RuntimeTask | None:
    """由监控快照条目 + 运行账本回落组装一条任务摘要。"""
    issue_number = issue_entry.get("number")
    if not isinstance(issue_number, int):
        return None
    labels = tuple(label for label in (issue_entry.get("labels") or ()) if isinstance(label, str))
    raw_anomaly_types = issue_entry.get("anomaly_types")
    anomaly_types = (
        tuple(anomaly_type for anomaly_type in raw_anomaly_types if isinstance(anomaly_type, str))
        if isinstance(raw_anomaly_types, (list, tuple))
        else ()
    )
    issue_is_closed = str(issue_entry.get("state") or "").lower() == "closed"
    if issue_is_closed:
        status = RuntimeTaskStatus.CLOSED
        status_reason = "GitHub Issue 已关闭"
    else:
        status = status_for_labels(labels)
        status_labels = tuple(
            label
            for label in labels
            if status is not None and _LABEL_STATUS_MAP.get(label) is status
        )
        status_reason = f"当前监控标签：{'、'.join(status_labels)}" if status_labels else None
        if status is None:
            if run_record is not None and run_record.outcome in _OUTCOME_STATUS_MAP:
                status = _OUTCOME_STATUS_MAP[run_record.outcome]
                status_reason = f"运行账本终态：{run_record.outcome}"
            else:
                status = RuntimeTaskStatus.UNKNOWN
                status_reason = "监控快照没有可判定的生命周期标签"

    title = issue_entry.get("title")
    url = issue_entry.get("url")
    return RuntimeTask(
        repo_id=repo_id,
        issue_number=issue_number,
        repo_display_name=repo_display_name,
        issue_title=title if isinstance(title, str) and title else None,
        issue_url=url if isinstance(url, str) and url else None,
        status=status,
        status_reason=status_reason,
        phase=_timeline_phase(issue_entry),
        agent=run_record.agent if run_record else None,
        started_at=run_record.started_at if run_record else None,
        finished_at=run_record.finished_at if run_record else None,
        duration_seconds=run_record.duration_seconds if run_record else None,
        last_outcome=run_record.outcome if run_record else None,
        prd_path=prd_path,
        labels=labels,
        anomaly_types=anomaly_types,
    )


def _run_record_task(run_record: RunRecord, prd_path: str | None) -> RuntimeTask:
    """快照里没有、但运行账本里有的 Issue：状态取账本终态，其余字段保持未知。"""
    status = _OUTCOME_STATUS_MAP.get(run_record.outcome, RuntimeTaskStatus.UNKNOWN)
    return RuntimeTask(
        repo_id=run_record.repo_id,
        issue_number=run_record.issue_number,
        repo_display_name=None,
        issue_title=run_record.issue_title,
        issue_url=run_record.issue_url,
        status=status,
        status_reason=(
            f"运行账本终态：{run_record.outcome}"
            if status is not RuntimeTaskStatus.UNKNOWN
            else f"缺少当前 Issue 状态；上次运行结果：{run_record.outcome}"
        ),
        agent=run_record.agent,
        started_at=run_record.started_at,
        finished_at=run_record.finished_at,
        duration_seconds=run_record.duration_seconds,
        last_outcome=run_record.outcome,
        prd_path=prd_path,
    )


def _attempt_record_task(
    lifecycle_run: PrdLifecycleRunRecord, attempt: AttemptRecord | None
) -> RuntimeTask:
    """生命周期账本里仍在进行、但运行账本尚未收口的任务。

    运行记录在 run 结束才写入，因此「进程还活着」只能由未收口的 lifecycle run
    识别；缺这一步会把正在跑的任务从看板上整体漏掉。
    """
    key_status = RuntimeTaskStatus.RUNNING
    return RuntimeTask(
        repo_id=lifecycle_run.repo_id,
        issue_number=int(lifecycle_run.issue_number or 0),
        status=key_status,
        status_reason="生命周期账本尚未收口",
        phase=attempt.failure_phase if attempt else None,
        agent=attempt.agent if attempt else None,
        preset=attempt.preset if attempt else None,
        model=attempt.model if attempt else None,
        attempt_number=attempt.attempt_number if attempt else None,
        started_at=(attempt.started_at if attempt else lifecycle_run.started_at),
        prd_path=lifecycle_run.prd_path,
    )


def _enrich_from_attempt(
    task: RuntimeTask,
    *,
    run_history_store: IRunHistoryStore,
    unavailable_sources: set[str],
    reference_time: datetime,
) -> RuntimeTask:
    """用最新 attempt 记录补齐阶段 / preset / 模型，并为运行中的任务推算耗时。

    耗时只由「attempt 开始时间 → 参考时间」得到，不从日志字节数或运行时长
    伪造任何百分比。
    """
    try:
        attempts = run_history_store.list_issue_attempts(
            repo_id=task.repo_id, issue_number=task.issue_number, limit=1
        )
    except Exception as exc:  # noqa: BLE001 - attempt 账本读失败只降级该字段。
        _logger.warning(
            "Runtime summary failed to read attempt ledger for %s#%d: %s",
            task.repo_id,
            task.issue_number,
            exc,
        )
        unavailable_sources.add(SOURCE_ATTEMPT_LEDGER)
        return task
    if not attempts:
        return task
    latest_attempt = attempts[-1]
    started_at = task.started_at or latest_attempt.started_at
    duration_seconds = task.duration_seconds
    if task.status is RuntimeTaskStatus.RUNNING:
        parsed_start = parse_runtime_timestamp(started_at)
        if parsed_start is not None:
            duration_seconds = max(0.0, (reference_time - parsed_start).total_seconds())
    return RuntimeTask(
        repo_id=task.repo_id,
        issue_number=task.issue_number,
        repo_display_name=task.repo_display_name,
        issue_title=task.issue_title,
        issue_url=task.issue_url,
        status=task.status,
        status_reason=task.status_reason,
        phase=latest_attempt.failure_phase or task.phase,
        agent=task.agent or latest_attempt.agent,
        preset=latest_attempt.preset,
        model=latest_attempt.model,
        attempt_number=latest_attempt.attempt_number,
        prd_path=task.prd_path,
        started_at=started_at,
        finished_at=task.finished_at,
        duration_seconds=duration_seconds,
        last_outcome=task.last_outcome,
        labels=task.labels,
        anomaly_types=task.anomaly_types,
    )


def _task_sort_key(task: RuntimeTask) -> tuple[int, float, str, int]:
    """看板排序键：状态优先级 → 开始时间倒序 → 仓库 → Issue 编号。"""
    parsed_start = parse_runtime_timestamp(task.started_at)
    # 越新的运行越靠前；无时间戳的任务排在同状态末尾。
    recency = -parsed_start.timestamp() if parsed_start else float("inf")
    return (task.status.display_order, recency, task.repo_id, task.issue_number)


def build_runtime_task_summary(
    *,
    snapshot_overview: SnapshotOverviewResult,
    run_history_store: IRunHistoryStore | None,
    prd_lifecycle_store: IPrdLifecycleStore | None,
    enabled_repo_ids: Sequence[str],
    now: datetime | None = None,
    max_rows: int = RUNTIME_TASK_MAX_ROWS,
) -> RuntimeTaskSummary:
    """组装运行态任务摘要。

    Args:
        snapshot_overview: 按当前 registry 过滤后的监控快照读路径结果。
        run_history_store: 运行与 attempt 账本端口；``None`` 记为不可用来源。
        prd_lifecycle_store: PRD 生命周期账本端口；``None`` 记为不可用来源。
        enabled_repo_ids: 当前注册且启用的仓库标识。**账本按仓库逐个读取**，
            因此已注销或停用仓库的历史运行不会带着旧 Issue 身份回到看板上。
        now: 推算耗时与时间窗的参考时间，注入以便单测确定性。
        max_rows: 摘要行数上限，超出时截断并置 ``truncated=True``。

    Returns:
        RuntimeTaskSummary: 任务摘要与数据来源可用性披露。改动树触达读数不在其中，
        展示侧按 ``prd_path`` 走现有 progress 契约取数。
    """
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    unavailable_sources: set[str] = set()
    allowed_repo_ids = set(enabled_repo_ids)

    runs: list[RunRecord] = []
    if run_history_store is None:
        unavailable_sources.add(SOURCE_RUN_HISTORY)
    else:
        try:
            # 逐仓库扫描而不是整表读取：注册边界由读取路径本身保证。
            for repo_id in allowed_repo_ids:
                runs.extend(
                    run_history_store.list_recent_runs(repo_id=repo_id, limit=_RUN_SCAN_LIMIT)
                )
        except Exception as exc:  # noqa: BLE001 - 单个来源读失败不得让整个摘要 500。
            _logger.warning("Runtime summary failed to read run history: %s", exc)
            unavailable_sources.add(SOURCE_RUN_HISTORY)
            runs = []

    lifecycle_runs: list[PrdLifecycleRunRecord] = []
    if prd_lifecycle_store is None:
        unavailable_sources.add(SOURCE_PRD_LIFECYCLE)
    else:
        try:
            lifecycle_runs = [
                run
                for run in prd_lifecycle_store.list_lifecycle_runs()
                if run.repo_id in allowed_repo_ids
            ]
        except Exception as exc:  # noqa: BLE001 - 旧库缺表时降级披露而非报错。
            _logger.warning("Runtime summary failed to read PRD lifecycle ledger: %s", exc)
            unavailable_sources.add(SOURCE_PRD_LIFECYCLE)

    if snapshot_overview.sync_status in _UNTRUSTED_SYNC_STATUSES:
        unavailable_sources.add(SOURCE_MONITOR_SNAPSHOT)

    runs_by_issue = _latest_run_by_issue(runs)
    prd_path_by_issue = _latest_prd_path_by_issue(lifecycle_runs)
    lifecycle_by_issue = _latest_lifecycle_by_issue(lifecycle_runs)
    window_start = reference_time - timedelta(seconds=RUNTIME_TASK_WINDOW_SECONDS)

    tasks: list[RuntimeTask] = []
    seen_keys: set[tuple[str, int]] = set()

    for repo_snapshot in snapshot_overview.repositories:
        display_name = str(repo_snapshot.overview.get("display_name") or repo_snapshot.repo_id)
        for issue_entry in _overview_issues(repo_snapshot.overview):
            issue_number = issue_entry.get("number")
            if not isinstance(issue_number, int):
                continue
            key = (repo_snapshot.repo_id, issue_number)
            task = _snapshot_task(
                repo_id=repo_snapshot.repo_id,
                repo_display_name=display_name,
                issue_entry=issue_entry,
                run_record=runs_by_issue.get(key),
                prd_path=prd_path_by_issue.get(key),
            )
            if task is None:
                continue
            seen_keys.add(key)
            tasks.append(task)

    for key, run_record in runs_by_issue.items():
        # 快照里没有、运行账本里有的 Issue：快照过期或已脱离监控标签，
        # 仍列出最近七天的记录并把身份交给账本，未知字段保持未知。
        if key in seen_keys:
            continue
        if _is_before_runtime_window(run_record.started_at, window_start):
            continue
        seen_keys.add(key)
        tasks.append(_run_record_task(run_record, prd_path_by_issue.get(key)))

    for key, lifecycle_run in lifecycle_by_issue.items():
        # 未收口的近期 lifecycle run 可补充运行账本缺少的任务；旧记录不能永久冒充运行中。
        if key in seen_keys or lifecycle_run.finished_at:
            continue
        if _is_before_runtime_window(lifecycle_run.started_at, window_start):
            continue
        seen_keys.add(key)
        tasks.append(_attempt_record_task(lifecycle_run, None))

    if run_history_store is not None:
        tasks = [
            _enrich_from_attempt(
                task,
                run_history_store=run_history_store,
                unavailable_sources=unavailable_sources,
                reference_time=reference_time,
            )
            if task.status in _ATTEMPT_ENRICHED_STATUSES
            else task
            for task in tasks
        ]

    # 账本只记 repo_id，展示名来自快照：补齐后前端不必再猜是哪个仓库。
    display_names = {
        entry.repo_id: str(entry.overview.get("display_name") or entry.repo_id)
        for entry in snapshot_overview.repositories
    }
    tasks = [
        replace(task, repo_display_name=display_names[task.repo_id])
        if task.repo_display_name is None and task.repo_id in display_names
        else task
        for task in tasks
    ]

    tasks.sort(key=_task_sort_key)
    truncated = len(tasks) > max_rows
    visible_tasks = tuple(tasks[:max_rows])

    status_counts = {status.value: 0 for status in RuntimeTaskStatus}
    for task in visible_tasks:
        status_counts[task.status.value] += 1

    return RuntimeTaskSummary(
        generated_at=reference_time.isoformat(timespec="seconds"),
        tasks=visible_tasks,
        status_counts=status_counts,
        unavailable_sources=tuple(sorted(unavailable_sources)),
        missing_repo_ids=snapshot_overview.missing_repo_ids,
        sync_status=snapshot_overview.sync_status,
        scanned_at=snapshot_overview.scanned_at,
        truncated=truncated,
    )


__all__ = [
    "SOURCE_ATTEMPT_LEDGER",
    "SOURCE_MONITOR_SNAPSHOT",
    "SOURCE_PRD_LIFECYCLE",
    "SOURCE_RUN_HISTORY",
    "build_runtime_task_summary",
    "parse_runtime_timestamp",
    "status_for_labels",
]
