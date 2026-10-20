"""统一运行时看板摘要用例的测试（PRD FR-1 / FR-2 / rv-4 的单元档）。

数据全部写进**真实的 SQLite store**（监控快照、运行账本、attempt 账本、
生命周期账本共用同一个库），不 mock 存储层；只有「读取抛错」这类故障注入用
最小端口替身，因为真实库里没有稳定的读失败路径。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    MonitorSnapshotEntry,
    PrdLifecycleRunRecord,
    RunRecord,
)
from backend.core.shared.models.runtime_task import RuntimeTaskStatus
from backend.core.use_cases.monitor_snapshots import get_snapshot_overview
from backend.core.use_cases.runtime_dashboard import (
    SOURCE_ATTEMPT_LEDGER,
    SOURCE_MONITOR_SNAPSHOT,
    SOURCE_PRD_LIFECYCLE,
    SOURCE_RUN_HISTORY,
    build_runtime_task_summary,
    parse_runtime_timestamp,
    status_for_labels,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore

REFERENCE_TIME = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


def _iso(minutes_offset: int) -> str:
    """相对参考时间生成 ISO-8601 UTC 串，避免测试里散落硬编码时间戳。"""
    from datetime import timedelta

    return (REFERENCE_TIME + timedelta(minutes=minutes_offset)).isoformat(timespec="seconds")


def _issue(
    number: int,
    *,
    labels: list[str],
    title: str = "任务标题",
    state: str = "open",
    timeline: list[dict] | None = None,
    anomaly_types: list[str] | None = None,
) -> dict:
    """构造一条与 ``/overview`` 的 ``issues[]`` 元素同形的快照条目。"""
    return {
        "number": number,
        "title": title,
        "url": f"https://example.test/issues/{number}",
        "labels": labels,
        "state": state,
        "timeline": timeline or [],
        "anomaly_types": anomaly_types or [],
    }


def _seed_snapshot(store: SqliteConsoleStore, repo_id: str, issues: list[dict]) -> None:
    """把一份 per-repo overview 写入真实快照表。"""
    payload = {
        "repo_id": repo_id,
        "display_name": repo_id.upper(),
        "issues": issues,
        "anomaly_count": 0,
    }
    store.upsert_monitor_snapshot(
        MonitorSnapshotEntry(
            repo_id=repo_id,
            payload_json=json.dumps(payload),
            scanned_at=_iso(-5),
        )
    )


def _store(tmp_path) -> SqliteConsoleStore:
    """返回一个独立的真实 SQLite store。"""
    return SqliteConsoleStore(tmp_path / "console.db")


def _summary(store: SqliteConsoleStore, repo_ids: list[str], **kwargs):
    """按当前 registry 读取快照并组装摘要，测试里省去重复的胶水参数。"""
    return build_runtime_task_summary(
        snapshot_overview=get_snapshot_overview(store, enabled_repo_ids=repo_ids),
        run_history_store=store,
        prd_lifecycle_store=store,
        enabled_repo_ids=repo_ids,
        now=REFERENCE_TIME,
        **kwargs,
    )


def test_parse_runtime_timestamp_keeps_unknown_as_none() -> None:
    """坏时间戳返回 ``None``，不允许被折算成 0 点或当前时间。"""
    assert parse_runtime_timestamp(None) is None
    assert parse_runtime_timestamp("") is None
    assert parse_runtime_timestamp("not-a-time") is None
    naive = parse_runtime_timestamp("2026-10-12T12:00:00")
    assert naive is not None and naive.utcoffset() is not None


def test_status_for_labels_picks_the_most_urgent_bucket() -> None:
    """标签切换窗口内同时命中多个标签时，取最需要盯的分桶。"""
    assert status_for_labels(["agent/review"]) is RuntimeTaskStatus.WAITING
    assert status_for_labels(["agent/automated-review-passed"]) is RuntimeTaskStatus.WAITING
    assert status_for_labels(["documentation", "agent/failed"]) is RuntimeTaskStatus.BLOCKED
    assert status_for_labels(["agent/failed", "agent/running"]) is RuntimeTaskStatus.RUNNING
    assert status_for_labels(["bug"]) is None


def test_snapshot_labels_drive_status_and_running_duration(tmp_path) -> None:
    """运行中的任务：状态来自标签，耗时来自 attempt 开始时间到参考时间。"""
    store = _store(tmp_path)
    _seed_snapshot(
        store,
        "keda",
        [
            _issue(7, labels=["agent/running"], title="正在跑"),
            _issue(6, labels=["agent/ready"]),
            _issue(5, labels=["agent/blocked"], anomaly_types=["stalled"]),
            _issue(4, labels=["agent/blocked"], state="closed", title="已手动关闭"),
        ],
    )
    store.append_attempt(
        AttemptRecord(
            repo_id="keda",
            issue_number=7,
            agent="qoder",
            attempt_number=2,
            failure_type="",
            recovered=False,
            detail="",
            started_at=_iso(-30),
            finished_at="",
            duration_seconds=0.0,
            preset="qoder-qwen3_8-flash_max",
            model="qwen3.8-flash",
            failure_phase="executing",
        )
    )

    summary = _summary(store, ["keda"])
    tasks = {task.issue_number: task for task in summary.tasks}

    assert tasks[7].status is RuntimeTaskStatus.RUNNING
    assert tasks[7].duration_seconds == pytest.approx(1800.0)
    assert tasks[7].preset == "qoder-qwen3_8-flash_max"
    assert tasks[7].model == "qwen3.8-flash"
    assert tasks[7].attempt_number == 2
    assert tasks[7].phase == "executing"
    assert tasks[6].status is RuntimeTaskStatus.WAITING
    assert tasks[5].status is RuntimeTaskStatus.BLOCKED
    assert tasks[5].status_reason == "检测到异常：stalled"
    assert tasks[4].status is RuntimeTaskStatus.CLOSED
    assert tasks[4].status_reason == "GitHub Issue 已关闭"
    assert summary.status_counts["running"] == 1
    assert summary.status_counts["closed"] == 1
    # 排序把运行中的任务放在最前，值守者不必翻页去找正在跑的东西。
    assert summary.tasks[0].issue_number == 7


def test_run_ledger_fills_issues_the_snapshot_no_longer_lists(tmp_path) -> None:
    """快照里没有、账本里有的 Issue 仍然出现，身份来自账本，未知字段保持未知。"""
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [_issue(7, labels=["agent/running"])])
    store.append_run(
        RunRecord(
            repo_id="keda",
            repo_path="/tmp/keda",
            issue_number=3,
            trigger="console_run",
            agent="claude",
            outcome="completed",
            error_summary=None,
            started_at=_iso(-120),
            finished_at=_iso(-100),
            duration_seconds=1200.0,
            issue_title="历史任务",
            issue_url="https://example.test/issues/3",
        )
    )

    summary = _summary(store, ["keda"])
    completed = next(task for task in summary.tasks if task.issue_number == 3)
    assert completed.status is RuntimeTaskStatus.COMPLETED
    assert completed.repo_display_name == "KEDA"
    assert completed.issue_title == "历史任务"
    assert completed.last_outcome == "completed"


@pytest.mark.parametrize("outcome", ["failed", "blocked"])
@pytest.mark.parametrize("snapshot_labels", [None, [], ["agent/automated-review-passed"]])
def test_historical_failure_does_not_imply_current_blockage(
    tmp_path, monkeypatch: pytest.MonkeyPatch, outcome: str, snapshot_labels: list[str] | None
) -> None:
    """历史失败不冒充当前阻塞；真实队列状态仍由快照标签决定。"""
    store = _store(tmp_path)
    _seed_snapshot(
        store, "keda", [] if snapshot_labels is None else [_issue(278, labels=snapshot_labels)]
    )
    store.append_run(
        RunRecord(
            repo_id="keda",
            repo_path="/tmp/keda",
            issue_number=278,
            trigger="console_run",
            agent="qoder",
            outcome=outcome,
            error_summary="历史错误",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=1200.0,
        )
    )

    summary = _summary(store, ["keda"])
    historical_task = summary.tasks[0]
    expected_status = RuntimeTaskStatus.WAITING if snapshot_labels else RuntimeTaskStatus.UNKNOWN
    assert historical_task.status is expected_status
    assert historical_task.last_outcome == outcome
    assert summary.status_counts["blocked"] == 0
    if snapshot_labels is None:
        assert historical_task.status_reason == f"缺少当前 Issue 状态；上次运行结果：{outcome}"

    # 穿过生产 HTTP 路由与 JSON 序列化；仅替换 registry 和存储工厂，SQLite 重新打开读取。
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import agent_runner_console_runtime as runtime_routes

    monkeypatch.setattr(runtime_routes, "_enabled_repo_ids", lambda: ["keda"])
    monkeypatch.setattr(runtime_routes, "create_console_store", lambda: _store(tmp_path))
    runtime_app = FastAPI()
    runtime_app.include_router(runtime_routes.router)
    with TestClient(runtime_app) as runtime_client:
        runtime_response = runtime_client.get("/agent-runner/console/runtime/tasks")
    assert runtime_response.status_code == 200
    runtime_payload = runtime_response.json()
    assert runtime_payload["status_counts"]["blocked"] == 0
    assert runtime_payload["tasks"][0]["status"] == expected_status.value
    assert runtime_payload["tasks"][0]["last_outcome"] == outcome


def test_unregistered_repository_history_stays_off_the_board(tmp_path) -> None:
    """已注销仓库的历史运行不得带着旧 Issue 身份回到看板（FR-2 数据来源边界）。"""
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [_issue(7, labels=["agent/running"])])
    _seed_snapshot(store, "archived-repo", [_issue(9, labels=["agent/ready"])])
    store.append_run(
        RunRecord(
            repo_id="archived-repo",
            repo_path="/tmp/archived",
            issue_number=9,
            trigger="cli_run",
            agent="claude",
            outcome="completed",
            error_summary=None,
            started_at=_iso(-60),
            finished_at=_iso(-50),
            duration_seconds=600.0,
        )
    )

    summary = _summary(store, ["keda"])
    assert [task.repo_id for task in summary.tasks] == ["keda"]


def test_unfinished_lifecycle_run_is_running_even_without_run_record(tmp_path) -> None:
    """生命周期账本尚未收口 = 进程还活着；缺这一步会把运行中的任务整体漏掉。"""
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [])
    store.upsert_lifecycle_run(
        PrdLifecycleRunRecord(
            run_id="run-1",
            repo_id="keda",
            prd_path="tasks/pending/P1-FEAT-demo.md",
            issue_number=11,
            trigger="console_run",
            started_at=_iso(-45),
            finished_at=None,
            outcome=None,
            history_complete=True,
        )
    )

    summary = _summary(store, ["keda"])
    assert len(summary.tasks) == 1
    task = summary.tasks[0]
    assert task.status is RuntimeTaskStatus.RUNNING
    assert task.prd_path == "tasks/pending/P1-FEAT-demo.md"
    assert task.started_at == _iso(-45)


def test_finished_lifecycle_run_only_links_the_prd(tmp_path) -> None:
    """已收口的生命周期 run 不再声称运行中，只把 PRD 关联挂到对应任务上。"""
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [_issue(11, labels=["agent/review"])])
    record = PrdLifecycleRunRecord(
        run_id="run-2",
        repo_id="keda",
        prd_path="tasks/pending/P1-FEAT-demo.md",
        issue_number=11,
        trigger="console_run",
        started_at=_iso(-90),
        finished_at=_iso(-80),
        outcome="completed",
        history_complete=True,
    )
    store.upsert_lifecycle_run(record)
    store.finish_lifecycle_run(run_id="run-2", outcome="completed", finished_at=_iso(-80))

    summary = _summary(store, ["keda"])
    assert summary.tasks[0].status is RuntimeTaskStatus.WAITING
    assert summary.tasks[0].prd_path == "tasks/pending/P1-FEAT-demo.md"


def test_pending_first_sync_is_disclosed_as_unavailable_source(tmp_path) -> None:
    """首扫未完成时把快照标为不可信来源，而不是渲染成「零条任务」的成功态。"""
    store = _store(tmp_path)

    summary = _summary(store, ["keda"])
    assert summary.sync_status == "pending_first_sync"
    assert SOURCE_MONITOR_SNAPSHOT in summary.unavailable_sources
    assert summary.missing_repo_ids == ("keda",)
    assert summary.tasks == ()


def test_store_read_failures_degrade_only_the_affected_source(tmp_path) -> None:
    """单个来源读失败只写进 unavailable_sources，不 500、不折叠成空列表。"""

    class _ExplodingStore(SqliteConsoleStore):
        """真实 store 的子类：把指定方法换成抛错，模拟账本读失败。"""

        def __init__(self, db_path, failing: str) -> None:
            super().__init__(db_path)
            self._failing = failing

        def list_recent_runs(self, **kwargs):  # noqa: ANN002, ANN003, ARG002
            if self._failing == "runs":
                raise RuntimeError("run ledger unreadable")
            return super().list_recent_runs(**kwargs)

        def list_issue_attempts(self, **kwargs):  # noqa: ANN002, ANN003, ARG002
            if self._failing == "attempts":
                raise RuntimeError("attempt ledger unreadable")
            return super().list_issue_attempts(**kwargs)

        def list_lifecycle_runs(self, **kwargs):  # noqa: ANN002, ANN003, ARG002
            if self._failing == "lifecycle":
                raise RuntimeError("lifecycle ledger unreadable")
            return super().list_lifecycle_runs(**kwargs)

    for failing, expected in (
        ("runs", SOURCE_RUN_HISTORY),
        ("attempts", SOURCE_ATTEMPT_LEDGER),
        ("lifecycle", SOURCE_PRD_LIFECYCLE),
    ):
        store = _ExplodingStore(tmp_path / f"{failing}.db", failing)
        _seed_snapshot(store, "keda", [_issue(7, labels=["agent/running"])])
        summary = _summary(store, ["keda"])
        assert expected in summary.unavailable_sources, failing
        # 读失败没有把任务变没：快照来源仍在，Issue 7 依然可见。
        assert [task.issue_number for task in summary.tasks] == [7], failing


def test_missing_ports_are_reported_not_silently_empty(tmp_path) -> None:
    """端口为 ``None`` 时同样逐项披露，摘要不假装数据源存在。"""
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [_issue(7, labels=["agent/ready"])])
    summary = build_runtime_task_summary(
        snapshot_overview=get_snapshot_overview(store, enabled_repo_ids=["keda"]),
        run_history_store=None,
        prd_lifecycle_store=None,
        enabled_repo_ids=["keda"],
        now=REFERENCE_TIME,
    )
    assert {SOURCE_RUN_HISTORY, SOURCE_PRD_LIFECYCLE} <= set(summary.unavailable_sources)
    assert summary.tasks[0].issue_number == 7


def test_summary_truncates_and_says_so(tmp_path) -> None:
    """超出行数上限时截断并置 ``truncated``，不把「还有更多」藏起来。"""
    store = _store(tmp_path)
    _seed_snapshot(
        store, "keda", [_issue(number, labels=["agent/ready"]) for number in range(1, 6)]
    )

    summary = _summary(store, ["keda"], max_rows=3)
    assert len(summary.tasks) == 3
    assert summary.truncated is True
