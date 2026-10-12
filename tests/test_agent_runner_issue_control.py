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
    STATE_PR_MERGED,
    STATE_RUNNING,
    STATE_TAKEN_OVER,
    STATE_TAKEOVER_REQUESTED,
    STATE_VERIFIER_COMPLETED,
    STATE_VERIFIER_STALE,
    TAKEOVER_ACCEPTED,
    TAKEOVER_BINDING_MISMATCH,
    TAKEOVER_COMPLETED_CONFLICT,
    TAKEOVER_NOT_FOUND,
    TAKEOVER_PENDING,
    IssueRunControlRecord,
)
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
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
    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_TAKEOVER_REQUESTED
    assert row.autopilot_hold is True


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

    assert (
        store.clear_autopilot_hold(
            repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="OTHER"
        )
        is False
    )
    assert store.has_autopilot_hold(repo_id="repo-a", issue_number=42) is True
    assert (
        store.clear_autopilot_hold(
            repo_id="repo-a", issue_number=42, run_id="newer-run", head_sha="abc123"
        )
        is False
    )
    assert (
        store.clear_autopilot_hold(
            repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
        )
        is True
    )
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


def test_autopilot_hold_blocks_when_known_repository_store_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: None)

    assert issue_control.autopilot_hold_blocks(repo_id="repo-a", issue_number=42) is True
    assert issue_control.autopilot_hold_blocks(repo_id=None, issue_number=42) is False


def test_takeover_handoff_does_not_mark_pr_merged_on_github_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub 传输失败不能与已合并 PR 混为一谈。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )
    assert handle.confirm_takeover() is True

    class _UnavailableGitHub:
        def get_pull_request_context(self, _branch: str, *, require_success: bool = False):
            assert require_success is True
            raise RuntimeError("GitHub API unavailable")

        def find_merged_pr_by_head(self, *_args: object, **_kwargs: object):
            pytest.fail("do not infer merged status after a failed authoritative read")

    issue_control.finalize_verifier_takeover(
        repo_id="repo-a",
        issue=IssueSummary(
            number=42,
            title="Takeover",
            url="https://github.com/example/repo/issues/42",
            body="",
            labels=(),
        ),
        config=AppConfig(),
        github_client=_UnavailableGitHub(),  # type: ignore[arg-type]
        expected_branch="issue-42",
        handle=handle,
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_TAKEN_OVER
    assert row.autopilot_hold is True


def test_takeover_handoff_records_merged_only_after_authoritative_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开放 PR 查询为空且合并查询命中时才记录 pr_merged。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )
    assert handle.confirm_takeover() is True

    class _MergedGitHub:
        def get_pull_request_context(self, _branch: str, *, require_success: bool = False):
            assert require_success is True
            return None

        def find_merged_pr_by_head(self, branch: str, *, require_success: bool = False):
            assert branch == "issue-42"
            assert require_success is True
            return "https://github.com/example/repo/pull/58"

    issue_control.finalize_verifier_takeover(
        repo_id="repo-a",
        issue=IssueSummary(
            number=42,
            title="Takeover",
            url="https://github.com/example/repo/issues/42",
            body="",
            labels=(),
        ),
        config=AppConfig(),
        github_client=_MergedGitHub(),  # type: ignore[arg-type]
        expected_branch="issue-42",
        handle=handle,
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_PR_MERGED
    assert row.autopilot_hold is True


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


def test_discard_stale_verdict_holds_the_run_that_produced_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发布端已判定过期时，必须对本轮控制行落 ``verifier_stale`` + 闸门。

    契约要点：``verdict_input_sha`` 是**产出该 verdict 的 verifier 输入提交**
    （与登记时的 ``head_sha`` 同值），漂移是发布端拿它与 GitHub fresh-read 的
    当前 head 比出来的。若这里反过来要求两者不等才落闸，生产路径永远不会置
    ``autopilot_hold``，merge queue 在 ``require_verifier_pass=false`` 下就会
    自动合并一个 HEAD 已漂移的 PR。
    """
    store = _store(tmp_path)
    _register(store, head_sha="abc123")
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    assert handle.mark_completed() is True

    discarded = issue_control.discard_stale_verdict(
        repo_id="repo-a",
        issue_number=42,
        verifier_run_id="run-1",
        verdict_input_sha="abc123",
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert discarded is True
    assert row.state == STATE_VERIFIER_STALE
    assert row.autopilot_hold is True
    assert issue_control.autopilot_hold_blocks(repo_id="repo-a", issue_number=42) is True


def test_discard_stale_verdict_leaves_a_newer_run_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """控制行已被新一轮重新登记时，过期 verdict 不得把它拖成 stale。"""
    store = _store(tmp_path)
    _register(store, run_id="run-1", head_sha="abc123")
    _register(store, run_id="run-2", head_sha="def456")
    _patch_control_store(monkeypatch, store)

    discarded = issue_control.discard_stale_verdict(
        repo_id="repo-a",
        issue_number=42,
        verifier_run_id="run-1",
        verdict_input_sha="abc123",
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert discarded is False
    assert row.run_id == "run-2"
    assert row.state == STATE_RUNNING
    assert row.autopilot_hold is False


def test_discard_stale_verdict_does_not_touch_newer_run_on_same_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 HEAD 的新 run 也不能被旧 verdict 误改写。"""
    store = _store(tmp_path)
    _register(store, run_id="old-run", head_sha="abc123")
    _register(store, run_id="new-run", head_sha="abc123")
    _patch_control_store(monkeypatch, store)

    discarded = issue_control.discard_stale_verdict(
        repo_id="repo-a",
        issue_number=42,
        verifier_run_id="old-run",
        verdict_input_sha="abc123",
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert discarded is False
    assert row.run_id == "new-run"
    assert row.state == STATE_RUNNING
    assert row.autopilot_hold is False
