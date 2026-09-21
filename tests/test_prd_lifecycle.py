"""PRD 生命周期账本：事件幂等、阶段推导、耗时分类、分位数与降级语义。

本文件同时覆盖 rv-3（存储故障负控）与 rv-4（旧记录降级）两条 oracle 的
可执行部分：主流程不被旁路观测阻断，且 fresh 读回能显式看到数据不完整。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console as console_routes
import backend.api.routes.agent_runner_roadmap as roadmap_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    build_prd_lifecycle_detail,
    build_prd_lifecycle_stats,
    classify_durations,
    derive_current_phase,
    lifecycle_run_id,
    record_lifecycle_event,
    record_lifecycle_terminal,
    resolve_lifecycle_store,
)
from backend.infrastructure.persistence.console_store import (
    RunRecord,
    SqliteConsoleStore,
)

client = TestClient(app)

_REPO_ID = "keda-main"
_PRD_PATH = "tasks/pending/P1-FEAT-20260921-161621-demo.md"


def _store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _record(
    store: SqliteConsoleStore,
    event_type: LifecycleEventType,
    occurred_at: str,
    *,
    issue_number: int = 7,
    event_key: str | None = None,
    detail: dict | None = None,
) -> None:
    record_lifecycle_event(
        store=store,
        repo_id=_REPO_ID,
        prd_path=_PRD_PATH,
        issue_number=issue_number,
        trigger="console_start",
        event_type=event_type,
        actor="test",
        occurred_at=occurred_at,
        event_key=event_key,
        detail=detail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Store semantics: append-only, idempotent, run identity
# ─────────────────────────────────────────────────────────────────────────────


def test_event_append_is_idempotent_by_event_key(tmp_path: Path) -> None:
    """同一 (run_id, event_key) 重复写不产生重复事件。"""
    store = _store(tmp_path)
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="queued")
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:01+00:00", event_key="queued")

    run = store.get_latest_lifecycle_run(repo_id=_REPO_ID, prd_path=_PRD_PATH)
    assert run is not None
    events = store.list_lifecycle_events(run_id=run.run_id)
    assert [event.event_type for event in events] == ["queued"]
    assert events[0].occurred_at == "2026-09-21T10:00:00+00:00"


def test_run_id_is_deterministic_from_issue_number() -> None:
    """run id 只由 repo_id + Issue 编号决定，与 PRD 路径无关（改名仍稳定）。"""
    first = lifecycle_run_id(repo_id=_REPO_ID, issue_number=7, prd_path="tasks/pending/a.md")
    second = lifecycle_run_id(repo_id=_REPO_ID, issue_number=7, prd_path="tasks/pending/b.md")
    assert first == second == "keda-main#7"


def test_runner_events_do_not_overwrite_roadmap_prd_path(tmp_path: Path) -> None:
    """runner 侧空 prd_path 不得覆盖 roadmap 侧已写入的真实路径。"""
    store = _store(tmp_path)
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")
    record_lifecycle_event(
        store=store,
        repo_id=_REPO_ID,
        prd_path="",
        issue_number=7,
        trigger="console_run",
        event_type=LifecycleEventType.STARTED,
        actor="runner",
        occurred_at="2026-09-21T10:01:00+00:00",
    )
    run = store.get_lifecycle_run("keda-main#7")
    assert run is not None
    assert run.prd_path == _PRD_PATH
    assert run.trigger == "console_start"  # 首次写入的 trigger 不被覆盖


def test_legacy_runs_are_counted_and_excluded(tmp_path: Path) -> None:
    """旧 run_records 无 lifecycle run 时计入 unlinked，且不进入完成分位数。"""
    store = _store(tmp_path)
    store.append_run(
        RunRecord(
            repo_id=_REPO_ID,
            repo_path="/tmp/repo",
            issue_number=999,
            trigger="cli_run",
            agent="claude",
            outcome="completed",
            error_summary=None,
            started_at="2026-09-21T09:00:00+00:00",
            finished_at="2026-09-21T09:10:00+00:00",
            duration_seconds=600.0,
        )
    )
    stats = build_prd_lifecycle_stats(
        store=store,
        repo_id=_REPO_ID,
        days=30,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert stats.unlinked_run_count == 1
    assert stats.completed_runs == 0
    assert stats.average_end_to_end_seconds is None


# ─────────────────────────────────────────────────────────────────────────────
# Duration classification and phase derivation
# ─────────────────────────────────────────────────────────────────────────────


def test_duration_classification_is_mutually_exclusive(tmp_path: Path) -> None:
    """执行 / 等待 / 阻塞互斥，且相加等于端到端。"""
    store = _store(tmp_path)
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")
    _record(store, LifecycleEventType.CLAIMED, "2026-09-21T10:01:00+00:00", event_key="c")
    _record(store, LifecycleEventType.BLOCKED, "2026-09-21T10:06:00+00:00", event_key="b")
    _record(store, LifecycleEventType.UNBLOCKED, "2026-09-21T10:08:00+00:00", event_key="u")
    _record(store, LifecycleEventType.REVIEW_STARTED, "2026-09-21T10:12:00+00:00", event_key="r")

    run = store.get_latest_lifecycle_run(repo_id=_REPO_ID, prd_path=_PRD_PATH)
    assert run is not None
    events = store.list_lifecycle_events(run_id=run.run_id)
    breakdown = classify_durations(
        events,
        finished_at="2026-09-21T10:14:00+00:00",
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    # 端到端 10:00 → 10:14 = 840s；执行 10:01→10:06 与 10:08→10:12 = 540s；
    # 阻塞 10:06→10:08 = 120s；等待 10:00→10:01 与 10:12→10:14 = 180s
    assert breakdown.end_to_end_seconds == 840.0
    assert breakdown.active_seconds == 540.0
    assert breakdown.blocked_seconds == 120.0
    assert breakdown.waiting_seconds == 180.0
    assert (
        breakdown.active_seconds + breakdown.waiting_seconds + breakdown.blocked_seconds
        == breakdown.end_to_end_seconds
    )
    assert derive_current_phase(run, events) is not None


def test_current_phase_follows_last_event() -> None:
    """当前阶段取最后一条事件的阶段，成功不覆盖此前的失败事件。"""
    store = None  # 纯函数部分不需要存储
    assert resolve_lifecycle_store(store) is None
    from backend.core.shared.interfaces.runner_console import (
        PrdLifecycleEventRecord,
        PrdLifecycleRunRecord,
    )

    run = PrdLifecycleRunRecord(
        run_id="r#1",
        repo_id="r",
        prd_path="p.md",
        issue_number=1,
        trigger="t",
        started_at="2026-09-21T10:00:00+00:00",
        finished_at=None,
        outcome=None,
        history_complete=True,
    )
    events = [
        PrdLifecycleEventRecord(
            run_id="r#1",
            event_key="a",
            event_type="claimed",
            phase="executing",
            actor="runner",
            occurred_at="2026-09-21T10:01:00+00:00",
            detail_json="{}",
        ),
        PrdLifecycleEventRecord(
            run_id="r#1",
            event_key="b",
            event_type="recovered",
            phase="executing",
            actor="runner",
            occurred_at="2026-09-21T10:02:00+00:00",
            detail_json="{}",
        ),
    ]
    assert derive_current_phase(run, events).value == "executing"


def test_retry_after_terminal_reopens_run_and_keeps_duration_invariant(
    tmp_path: Path,
) -> None:
    """终态后重试：run 重开为进行中，且四类耗时仍满足互斥相加等式。

    回归 rv-1 复核发现的 HIGH 缺陷：``finished_at`` 早于后续事件会让
    「执行 + 等待 + 阻塞 == 端到端」失效。
    """
    store = _store(tmp_path)
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")
    _record(store, LifecycleEventType.CLAIMED, "2026-09-21T10:05:00+00:00", event_key="c")
    record_lifecycle_terminal(
        store=store,
        repo_id=_REPO_ID,
        prd_path=_PRD_PATH,
        issue_number=7,
        trigger="console_run",
        event_type=LifecycleEventType.FAILED,
        outcome="failed",
        actor="runner",
        occurred_at="2026-09-21T10:10:00+00:00",
        event_key="f1",
    )
    closed_run = store.get_lifecycle_run("keda-main#7")
    assert closed_run is not None
    assert closed_run.outcome == "failed"
    assert closed_run.finished_at == "2026-09-21T10:10:00+00:00"

    # 同一 stable run id 被重试：非终态事件必须重开该 run。
    _record(store, LifecycleEventType.STARTED, "2026-09-21T11:00:00+00:00", event_key="s2")
    _record(
        store,
        LifecycleEventType.IMPLEMENTATION_COMPLETED,
        "2026-09-21T12:00:00+00:00",
        event_key="ic2",
    )
    reopened_run = store.get_lifecycle_run("keda-main#7")
    assert reopened_run is not None
    assert reopened_run.finished_at is None
    assert reopened_run.outcome is None

    detail = build_prd_lifecycle_detail(
        store=store,
        repo_id=_REPO_ID,
        prd_path=_PRD_PATH,
        now=datetime(2026, 9, 21, 12, 30, tzinfo=timezone.utc),
    )
    durations = detail.durations
    assert detail.in_progress is True
    assert durations.end_to_end_seconds == 9000.0  # 10:00 → 12:30
    assert (
        pytest.approx(
            durations.active_seconds + durations.waiting_seconds + durations.blocked_seconds,
            abs=0.1,
        )
        == durations.end_to_end_seconds
    )
    # 失败历史不被重开抹掉
    assert "failed" in [event.event_type for event in detail.events]


def test_classify_durations_stays_consistent_when_finish_precedes_events() -> None:
    """防御性回归：即使 ``finished_at`` 早于最后一个事件，三段之和仍等于端到端。"""
    from backend.core.shared.interfaces.runner_console import PrdLifecycleEventRecord

    def _event(event_type: str, phase: str, occurred_at: str, key: str) -> PrdLifecycleEventRecord:
        return PrdLifecycleEventRecord(
            run_id="r#1",
            event_key=key,
            event_type=event_type,
            phase=phase,
            actor="test",
            occurred_at=occurred_at,
            detail_json="{}",
        )

    events = [
        _event("queued", "queued", "2026-09-21T10:00:00+00:00", "q"),
        _event("claimed", "executing", "2026-09-21T10:05:00+00:00", "c"),
        _event("failed", "failed", "2026-09-21T10:10:00+00:00", "f"),
        _event("review_started", "reviewing", "2026-09-21T11:00:00+00:00", "r"),
    ]
    breakdown = classify_durations(
        events,
        finished_at="2026-09-21T10:10:00+00:00",
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert breakdown.end_to_end_seconds == 3600.0  # 10:00 → 11:00（取最晚边界）
    assert (
        breakdown.active_seconds + breakdown.waiting_seconds + breakdown.blocked_seconds
        == breakdown.end_to_end_seconds
    )


def test_classify_durations_orders_events_by_real_time_across_offsets() -> None:
    """跨时区偏移时按解析后的真实时序排序，而不是字典序。"""
    from backend.core.shared.interfaces.runner_console import PrdLifecycleEventRecord

    def _event(event_type: str, phase: str, occurred_at: str, key: str) -> PrdLifecycleEventRecord:
        return PrdLifecycleEventRecord(
            run_id="r#1",
            event_key=key,
            event_type=event_type,
            phase=phase,
            actor="test",
            occurred_at=occurred_at,
            detail_json="{}",
        )

    # 12:00+02:00 == 10:00Z（更早）；字符串序里 "2026-09-21T12..." > "2026-09-21T11..."
    events = [
        _event("claimed", "executing", "2026-09-21T11:00:00+00:00", "c"),
        _event("queued", "queued", "2026-09-21T12:00:00+02:00", "q"),
        _event("review_started", "reviewing", "2026-09-21T11:30:00+00:00", "r"),
    ]
    breakdown = classify_durations(
        events,
        finished_at="2026-09-21T11:30:00+00:00",
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    # 真实最早是 10:00Z，端到端 10:00Z → 11:30Z = 5400s
    assert breakdown.end_to_end_seconds == 5400.0
    assert (
        breakdown.active_seconds + breakdown.waiting_seconds + breakdown.blocked_seconds
        == breakdown.end_to_end_seconds
    )


def test_stats_percentiles_over_completed_runs_only(tmp_path: Path) -> None:
    """P90/中位数只统计已收口 run；进行中记录被排除。"""
    store = _store(tmp_path)
    base = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)

    def _complete(issue_number: int, day_offset: int, duration_minutes: int) -> None:
        started = base + timedelta(days=day_offset)
        started_iso = started.isoformat(timespec="seconds")
        finished_iso = (started + timedelta(minutes=duration_minutes)).isoformat(timespec="seconds")
        _record(
            store,
            LifecycleEventType.QUEUED,
            started_iso,
            issue_number=issue_number,
            event_key=f"q-{issue_number}",
        )
        record_lifecycle_terminal(
            store=store,
            repo_id=_REPO_ID,
            prd_path=_PRD_PATH,
            issue_number=issue_number,
            trigger="console_start",
            event_type=LifecycleEventType.MERGED,
            outcome="completed",
            actor="merge_queue",
            occurred_at=finished_iso,
            event_key=f"merged-{issue_number}",
        )

    _complete(1, 0, 10)
    _complete(2, 1, 20)
    _complete(3, 2, 30)
    # 进行中：只有 queued，没有终态，必须被排除
    _record(
        store,
        LifecycleEventType.QUEUED,
        (base + timedelta(days=3)).isoformat(timespec="seconds"),
        issue_number=4,
        event_key="q-4",
    )

    stats = build_prd_lifecycle_stats(
        store=store,
        repo_id=_REPO_ID,
        days=30,
        now=datetime(2026, 9, 21, 23, 59, tzinfo=timezone.utc),
    )
    assert stats.completed_runs == 3
    assert stats.average_end_to_end_seconds == 1200.0
    assert stats.median_end_to_end_seconds == 1200.0
    assert stats.p90_end_to_end_seconds == pytest.approx(1680.0, abs=0.1)
    assert stats.bottleneck_phase == "queued"
    assert any(row.in_progress for row in stats.runs)


# ─────────────────────────────────────────────────────────────────────────────
# rv-3 negative control: store failure must not break the main flow
# ─────────────────────────────────────────────────────────────────────────────


class _FailingAppendStore(SqliteConsoleStore):
    """仅让事件追加抛错的测试替身：业务错误处理与降级必须真实。"""

    def append_lifecycle_event(self, event_record) -> bool:  # type: ignore[override]
        raise RuntimeError("simulated lifecycle append failure")


class _HealthyStore(SqliteConsoleStore):
    """负控对照：不注入故障时应保持 history_complete=True。"""


def test_store_failure_marks_run_incomplete_without_raising(tmp_path: Path) -> None:
    """事件写入失败：主流程不抛异常，fresh 读回显示 history_complete=False。"""
    store = _FailingAppendStore(tmp_path / "console.db")
    # 主流程：不得抛出（runner 完成既有动作）。
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")

    run = store.get_lifecycle_run("keda-main#7")
    assert run is not None
    assert run.history_complete is False

    detail = build_prd_lifecycle_detail(store=store, repo_id=_REPO_ID, prd_path=_PRD_PATH)
    assert detail.has_data is True
    assert detail.history_complete is False
    assert detail.events == []


def test_negative_control_healthy_store_stays_complete(tmp_path: Path) -> None:
    """负控：去掉注入的存储异常后，history_complete 必须为 True。"""
    store = _HealthyStore(tmp_path / "console.db")
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")
    run = store.get_lifecycle_run("keda-main#7")
    assert run is not None
    assert run.history_complete is True


# ─────────────────────────────────────────────────────────────────────────────
# API contracts (fresh HTTP read)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把详情 / 统计端点都接到同一个 tmp SQLite 与假仓库上下文。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    repo_dir = tmp_path / "repo"
    (repo_dir / "tasks" / "pending").mkdir(parents=True)
    contexts = [
        RepositoryRunContext(
            repo_id=_REPO_ID,
            display_name="Keda Main",
            repo_path=repo_dir,
            config=AppConfig(),
        )
    ]
    monkeypatch.setattr(roadmap_routes, "create_roadmap_store", lambda: store)
    monkeypatch.setattr(roadmap_routes, "_resolve_contexts", lambda: contexts)
    monkeypatch.setattr(console_routes, "create_console_store", lambda: store)
    return store


def test_roadmap_lifecycle_endpoint_returns_fresh_detail(api_environment) -> None:
    """Roadmap 详情端点返回与账本一致的 run_id、有序事件与耗时拆分。"""
    store = api_environment
    _record(store, LifecycleEventType.QUEUED, "2026-09-21T10:00:00+00:00", event_key="q")
    _record(store, LifecycleEventType.CLAIMED, "2026-09-21T10:01:00+00:00", event_key="c")

    encoded = roadmap_routes._encode_prd_path(_PRD_PATH)
    response = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded}/lifecycle",
        params={"repo_id": _REPO_ID},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "keda-main#7"
    assert payload["has_data"] is True
    assert payload["current_phase"] == "executing"
    assert [event["event_type"] for event in payload["events"]] == ["queued", "claimed"]


def test_roadmap_lifecycle_endpoint_empty_state(api_environment) -> None:
    """无任何 run 时返回 has_data=False 空态而不是 404。"""
    encoded = roadmap_routes._encode_prd_path(_PRD_PATH)
    response = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded}/lifecycle",
        params={"repo_id": _REPO_ID},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_data"] is False
    assert payload["current_phase"] == "none"
    assert payload["events"] == []


def test_console_prd_lifecycle_stats_endpoint(api_environment) -> None:
    """Stats 端点返回完成分位数与被排除的旧记录条数。"""
    store = api_environment
    store.append_run(
        RunRecord(
            repo_id=_REPO_ID,
            repo_path="/tmp/repo",
            issue_number=555,
            trigger="cli_run",
            agent="claude",
            outcome="completed",
            error_summary=None,
            started_at="2026-09-21T09:00:00+00:00",
            finished_at="2026-09-21T09:05:00+00:00",
            duration_seconds=300.0,
        )
    )
    response = client.get(
        "/api/v1/agent-runner/console/stats/prd-lifecycle",
        params={"repo_id": _REPO_ID, "days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["unlinked_run_count"] == 1
    assert payload["completed_runs"] == 0
    assert payload["runs"] == []
