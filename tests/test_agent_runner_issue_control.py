"""单 Issue verifier 人工接管：控制存储 CAS + Console 接管用例（Issue #278）。

全部走**真实 SQLite**（``SqliteConsoleStore`` 落在 tmp_path 的真实库文件），
不使用任何 store mock：接管状态机的正确性正来自 SQLite 的单语句 CAS，mock 掉
它就没有证明力。跨进程并发用真实多线程 + 真实连接复现。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.core.shared.interfaces.issue_run_control import (
    PHASE_VERIFIER,
    STATE_RUNNING,
    STATE_TAKEN_OVER,
    STATE_TAKEOVER_REQUESTED,
    STATE_VERIFIER_COMPLETED,
    TAKEOVER_ACCEPTED,
    TAKEOVER_BINDING_MISMATCH,
    TAKEOVER_COMPLETED_CONFLICT,
    TAKEOVER_NOT_FOUND,
    TAKEOVER_PENDING,
    IssueRunControlRecord,
)
from backend.core.use_cases import agent_runner_issue_control as issue_control
from backend.infrastructure.persistence.console_store import SqliteConsoleStore


def _store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _register(
    store: SqliteConsoleStore, *, run_id: str = "run-1", head_sha: str = "abc123"
) -> None:
    store.register_issue_run(
        IssueRunControlRecord(
            repo_id="repo-a",
            issue_number=42,
            run_id=run_id,
            head_sha=head_sha,
            phase=PHASE_VERIFIER,
            state=STATE_RUNNING,
            autopilot_hold=False,
            started_at="2026-10-12T00:00:00+00:00",
            pr_number=58,
        )
    )


def test_register_then_get_returns_running_control_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)

    assert row is not None
    assert row.state == STATE_RUNNING
    assert row.run_id == "run-1"
    assert row.head_sha == "abc123"
    assert row.autopilot_hold is False


def test_request_takeover_accepts_matching_binding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    assert outcome.status == TAKEOVER_ACCEPTED
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_TAKEOVER_REQUESTED


def test_duplicate_takeover_is_idempotent_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)
    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-2",
        requested_at="2026-10-12T00:00:02+00:00",
    )

    assert outcome.status == TAKEOVER_PENDING


def test_stale_head_takeover_is_binding_mismatch_without_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store, head_sha="abc123")

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="OLD_HEAD",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    assert outcome.status == TAKEOVER_BINDING_MISMATCH
    # 绑定不符绝不写状态：控制行仍为 running。
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_RUNNING


def test_takeover_after_completion_is_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)
    store.transition_issue_run(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        from_states=(STATE_RUNNING,),
        to_state=STATE_VERIFIER_COMPLETED,
    )

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    assert outcome.status == TAKEOVER_COMPLETED_CONFLICT


def test_takeover_on_unknown_run_is_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=99,
        run_id="nope",
        head_sha="abc",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    assert outcome.status == TAKEOVER_NOT_FOUND


def test_new_run_registration_preserves_autopilot_hold(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)
    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )
    store.transition_issue_run(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        from_states=(STATE_TAKEOVER_REQUESTED,),
        to_state=STATE_TAKEN_OVER,
        autopilot_hold=True,
    )

    _register(store, run_id="run-2", head_sha="def456")

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.run_id == "run-2"
    assert row.state == STATE_RUNNING
    # 闸门只由同 head 的全新 GREEN 清除，新一轮登记不得洗掉。
    assert row.autopilot_hold is True


def test_clear_autopilot_hold_only_on_matching_completed_head(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)
    store.transition_issue_run(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        from_states=(STATE_RUNNING,),
        to_state=STATE_VERIFIER_COMPLETED,
        autopilot_hold=True,
    )

    assert store.clear_autopilot_hold(repo_id="repo-a", issue_number=42, head_sha="OTHER") is False
    assert store.has_autopilot_hold(repo_id="repo-a", issue_number=42) is True
    assert store.clear_autopilot_hold(repo_id="repo-a", issue_number=42, head_sha="abc123") is True
    assert store.has_autopilot_hold(repo_id="repo-a", issue_number=42) is False


def test_concurrent_takeover_and_completion_yield_single_terminal_state(tmp_path: Path) -> None:
    """两个真实线程共享同一库文件：CAS 保证 takeover 与 completion 只有一方赢。"""
    store = _store(tmp_path)
    _register(store)
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _requester() -> None:
        barrier.wait()
        results["takeover"] = store.request_issue_takeover(
            repo_id="repo-a",
            issue_number=42,
            run_id="run-1",
            head_sha="abc123",
            request_id="req-1",
            requested_at="2026-10-12T00:00:01+00:00",
        )

    def _completer() -> None:
        barrier.wait()
        results["completed"] = store.transition_issue_run(
            repo_id="repo-a",
            issue_number=42,
            run_id="run-1",
            from_states=(STATE_RUNNING,),
            to_state=STATE_VERIFIER_COMPLETED,
        )

    threads = [threading.Thread(target=_requester), threading.Thread(target=_completer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final_state = store.get_issue_run(repo_id="repo-a", issue_number=42).state
    accepted = results["takeover"].status == TAKEOVER_ACCEPTED
    completed = bool(results["completed"])
    # 恰有一方把 running 翻走：要么进入接管态，要么进入完成态，不能两者兼得。
    assert accepted != completed
    assert final_state in (STATE_TAKEOVER_REQUESTED, STATE_VERIFIER_COMPLETED)


def _patch_control_store(monkeypatch: pytest.MonkeyPatch, store: SqliteConsoleStore) -> None:
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: store)


def test_use_case_request_takeover_maps_accepted_to_202(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)

    result = issue_control.request_verifier_takeover(
        repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )

    assert result.outcome == TAKEOVER_ACCEPTED
    assert result.http_status == 202


def test_use_case_request_takeover_maps_stale_binding_to_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)

    result = issue_control.request_verifier_takeover(
        repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="STALE"
    )

    assert result.outcome == TAKEOVER_BINDING_MISMATCH
    assert result.http_status == 409
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_RUNNING


def test_use_case_request_takeover_unavailable_store_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: None)

    with pytest.raises(issue_control.IssueControlUnavailableError):
        issue_control.request_verifier_takeover(
            repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
        )


def test_active_verifier_projection_only_running_is_takeable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)

    projection = issue_control.active_verifier_projection(repo_id="repo-a", issue_number=42)
    assert projection is not None
    assert projection.can_take_over is True
    assert projection.head_sha == "abc123"

    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )
    assert (
        issue_control.active_verifier_projection(repo_id="repo-a", issue_number=42).can_take_over
        is False
    )


def test_active_verifier_projection_none_when_no_control_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _patch_control_store(monkeypatch, store)

    assert issue_control.active_verifier_projection(repo_id="repo-a", issue_number=42) is None
