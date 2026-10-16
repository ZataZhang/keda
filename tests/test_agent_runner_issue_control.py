"""单 Issue verifier 人工接管：控制存储 CAS + Console 接管用例（Issue #278）。

全部走**真实 SQLite**（``SqliteConsoleStore`` 落在 tmp_path 的真实库文件），
不使用任何 store mock：接管状态机的正确性正来自 SQLite 的单语句 CAS，mock 掉
它就没有证明力。跨进程并发用真实多线程 + 真实连接复现。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.shared.interfaces.issue_run_control import (
    PHASE_VERIFIER,
    STATE_EXPIRED,
    STATE_PR_MERGED,
    STATE_RUNNING,
    STATE_TAKEN_OVER,
    STATE_TAKEOVER_FAILED,
    STATE_TAKEOVER_REQUESTED,
    STATE_VERIFIER_COMPLETED,
    STATE_VERIFIER_STALE,
    TAKEOVER_ACCEPTED,
    TAKEOVER_BINDING_MISMATCH,
    TAKEOVER_COMPLETED_CONFLICT,
    TAKEOVER_NOT_FOUND,
    TAKEOVER_PENDING,
    TAKEOVER_STATE_CHANGE_CONFLICT,
    IssueRunControlRecord,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    PullRequestContext,
)
from backend.core.use_cases import agent_runner_issue_control as issue_control
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.support.issue_run_control import ScriptedTakeoverCasStore


def _store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _register(
    store: SqliteConsoleStore,
    *,
    run_id: str = "run-1",
    head_sha: str = "abc123",
    pr_number: int | None = 58,
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
            pr_number=pr_number,
        )
    )


def _request_takeover(
    store: SqliteConsoleStore,
    *,
    run_id: str = "run-1",
    head_sha: str = "abc123",
    requested_at: str = "2026-10-12T00:00:01+00:00",
) -> None:
    store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id=run_id,
        head_sha=head_sha,
        request_id="req-1",
        requested_at=requested_at,
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


def test_takeover_cas_miss_with_running_row_never_reports_accepted(tmp_path: Path) -> None:
    """CAS 反复空手却仍读到 running 时必须拒绝受理：202 承诺的是已持久的取消意图。

    这一条是接管特性里唯一的 fail-open 分支——按旧的"幂等按在途处理"返回 pending 时，
    路由回 202、Console 把动作审计成 accepted，而库里既没有 request_id 也没落闸，
    runner 永远看不到取消，verifier 照样能发 PASS 并把合并队列放行。
    """
    store = ScriptedTakeoverCasStore(tmp_path / "console.db", missed_cas_attempts=99)
    _register(store)

    outcome = store.request_issue_takeover(
        repo_id="repo-a",
        issue_number=42,
        run_id="run-1",
        head_sha="abc123",
        request_id="req-1",
        requested_at="2026-10-12T00:00:01+00:00",
    )

    assert outcome.status == TAKEOVER_STATE_CHANGE_CONFLICT
    assert outcome.observed_state == STATE_RUNNING
    # 未命中不是放弃的理由：必须真的再试一次。
    assert store.cas_attempts == 2
    untouched = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert untouched.state == STATE_RUNNING
    assert untouched.request_id is None
    assert untouched.autopilot_hold is False


def test_takeover_cas_retry_lands_the_request_after_a_first_miss(tmp_path: Path) -> None:
    """首次 CAS 空手、重试命中时必须按受理返回，且取消意图与闸门确实落盘。"""
    store = ScriptedTakeoverCasStore(tmp_path / "console.db", missed_cas_attempts=1)
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
    assert store.cas_attempts == 2
    landed = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert landed.state == STATE_TAKEOVER_REQUESTED
    assert landed.request_id == "req-1"
    assert landed.autopilot_hold is True


def test_use_case_request_takeover_maps_state_change_conflict_to_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未落盘的接管请求对外就是"状态已变化"，绝不借 202 蒙过界面与审计。"""
    store = ScriptedTakeoverCasStore(tmp_path / "console.db", missed_cas_attempts=99)
    _register(store)
    _patch_control_store(monkeypatch, store)

    result = issue_control.request_verifier_takeover(
        repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )

    assert result.outcome == TAKEOVER_STATE_CHANGE_CONFLICT
    assert result.http_status == 409


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


def test_use_case_request_takeover_wraps_storage_errors(
    tmp_path: Path,
) -> None:
    """SQLite busy/locked 等存储层异常必须收敛成 IssueControlUnavailableError。

    否则接管端点会以未处理 500 逃逸且不留审计；接管范围无法确认时必须
    走"存储不可用"分支（Console 审计 rejected + 400，绝不伪装成交班）。
    """
    store = _store(tmp_path)

    def _locked(**_kwargs: object) -> None:
        raise RuntimeError("sqlite3.OperationalError: database is locked")

    store.request_issue_takeover = _locked  # type: ignore[assignment]

    with pytest.raises(issue_control.IssueControlUnavailableError, match="database is locked"):
        issue_control.request_verifier_takeover(
            repo_id="repo-a",
            issue_number=42,
            run_id="run-1",
            head_sha="abc123",
            control_store=store,
        )


def test_autopilot_hold_blocks_when_known_repository_store_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: None)

    assert issue_control.autopilot_hold_blocks(repo_id="repo-a", issue_number=42) is True
    # repo_id 解析不出来时也不再放行：无法证明"没有闸门"就不能放行自动处理（FR-5/FR-6）。
    assert issue_control.autopilot_hold_blocks(repo_id=None, issue_number=42) is True
    assert issue_control.autopilot_hold_blocks(repo_id="", issue_number=42) is True


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
        def __init__(self) -> None:
            self.label_moves: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
            self.comments: list[tuple[int, str]] = []

        def get_pull_request_context(self, _branch: str, *, require_success: bool = False):
            assert require_success is True
            raise RuntimeError("GitHub API unavailable")

        def find_merged_pr_by_head(self, *_args: object, **_kwargs: object):
            pytest.fail("do not infer merged status after a failed authoritative read")

        def edit_issue_labels(self, number: int, *, add=(), remove=()) -> None:  # noqa: ANN001
            self.label_moves.append((number, tuple(add), tuple(remove)))

        def comment_issue(self, number: int, body: str) -> None:
            self.comments.append((number, body))

    github = _UnavailableGitHub()
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
        github_client=github,  # type: ignore[arg-type]
        expected_branch="issue-42",
        handle=handle,
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_TAKEN_OVER
    assert row.autopilot_hold is True
    # PR 侧读失败不阻断 Issue 侧交接：转 review、补人工待办与交接评论必须照常尝试，
    # 且评论不带失效的 PR 链接。
    review_label = AppConfig().labels.review
    human_review_label = AppConfig().labels.human_review
    running_label = AppConfig().labels.running
    assert github.label_moves == [(42, (review_label, human_review_label), (running_label,))]
    assert len(github.comments) == 1
    assert "/pull/" not in github.comments[0][1]


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


def test_autopilot_hold_blocks_when_hold_is_registered_under_the_registry_repo_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按注册表 id 落闸后，解析不出 repo_id 的入口也必须看到闸门。

    回归 Issue #278 评审发现的失效形态：runner 用注册表 ``repo_id`` 登记控制行，
    而 Console 的 review_once 入口未转发 repo_id，于是按 ``repo_path.name`` 查——
    查不到行就当作"没有闸门"，被人工接管的 Issue 重新进入监督与合并队列，即使
    仓库关闭了 ``require_verifier_pass``。
    """
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    _request_takeover(store)

    assert issue_control.autopilot_hold_blocks(repo_id="repo-a", issue_number=42) is True
    assert issue_control.autopilot_hold_blocks(repo_id=None, issue_number=42) is True
    assert issue_control.autopilot_hold_blocks(repo_id="", issue_number=42) is True


def test_autopilot_hold_blocks_are_scoped_to_the_other_repository(tmp_path: Path) -> None:
    """另一仓库的同号 Issue 不影响本仓的精确查询（闸门判定不跨仓放行也不跨仓误伤）。"""
    store = _store(tmp_path)
    _register(store)
    _request_takeover(store)

    assert store.has_autopilot_hold(repo_id="repo-a", issue_number=42) is True
    assert store.has_autopilot_hold(repo_id="repo-b", issue_number=42) is False
    assert store.has_autopilot_hold_for_issue(issue_number=42) is True
    assert store.has_autopilot_hold_for_issue(issue_number=7) is False


def test_orphaned_takeover_request_is_invalidated_and_stays_on_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """owner 随重启消失后，悬空的接管请求收敛成 takeover_failed（PRD §7.7、FR-6）。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    _request_takeover(store, requested_at="2026-10-12T00:00:01+00:00")

    invalidated = issue_control.invalidate_orphaned_takeover_requests(
        repo_id="repo-a",
        issue_number=42,
        now=datetime(2026, 10, 12, 0, 5, tzinfo=timezone.utc),
    )

    assert invalidated is True
    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_TAKEOVER_FAILED
    assert row.completed_at is not None
    # 请求已经落下的闸门不因 owner 消失而自动打开。
    assert row.autopilot_hold is True


def test_takeover_request_inside_the_window_is_not_declared_orphaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """看门狗最多 1 秒一轮，窗口内的请求仍在收尾：不能抢先判死。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    _request_takeover(store, requested_at="2026-10-12T00:00:01+00:00")

    invalidated = issue_control.invalidate_orphaned_takeover_requests(
        repo_id="repo-a",
        issue_number=42,
        now=datetime(2026, 10, 12, 0, 0, 30, tzinfo=timezone.utc),
    )

    assert invalidated is False
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_TAKEOVER_REQUESTED


def test_running_row_is_never_orphaned_by_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``running`` 行的 owner 是 verifier 自己的等待循环，由 mark_expired 收敛。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)

    invalidated = issue_control.invalidate_orphaned_takeover_requests(
        repo_id="repo-a",
        issue_number=42,
        now=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    assert invalidated is False
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_RUNNING


def test_active_verifier_projection_surfaces_orphaned_request_as_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """界面读取时收敛孤儿请求：操作者看到可重试失败，而不是永远"仍在停止"。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    _request_takeover(store, requested_at="2020-01-01T00:00:01+00:00")

    projection = issue_control.active_verifier_projection(repo_id="repo-a", issue_number=42)

    assert projection is not None
    assert projection.state == STATE_TAKEOVER_FAILED
    assert projection.can_take_over is False
    assert projection.autopilot_hold is True


def test_mark_verdict_unpublished_reclaims_the_completed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAS 赢了却没发布结论（树漂移 / RED）时收回 verifier_completed，避免假完成态。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    assert handle.mark_completed() is True

    assert handle.mark_verdict_unpublished() is True

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_EXPIRED
    assert row.completed_at is not None


def test_mark_verdict_unpublished_after_takeover_lands_records_takeover_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接管恰好在最后时刻落地时，收回动作不得掩盖交班状态。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    _request_takeover(store)

    assert handle.mark_verdict_unpublished() is True

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.state == STATE_TAKEOVER_FAILED
    assert row.autopilot_hold is True


def test_mark_verdict_unpublished_leaves_handoff_terminal_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已完成交班（taken_over）的行不被收回——交班终态优先于观测面清理。"""
    store = _store(tmp_path)
    _register(store)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    _request_takeover(store)
    assert handle.confirm_takeover() is True

    assert handle.mark_verdict_unpublished() is False

    assert store.get_issue_run(repo_id="repo-a", issue_number=42).state == STATE_TAKEN_OVER


def test_attach_pr_number_writes_only_the_owning_run_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登记时不知道 PR 编号，交班 fresh-read 后按 run 绑定回写；新一轮不受影响。"""
    store = _store(tmp_path)
    _register(store, pr_number=None)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).pr_number is None

    assert handle.attach_pr_number(71) is True
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).pr_number == 71

    _register(store, run_id="run-2", pr_number=None)
    assert handle.attach_pr_number(72) is False
    assert store.get_issue_run(repo_id="repo-a", issue_number=42).pr_number is None


def test_takeover_handoff_records_the_pr_number_from_the_fresh_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-7：交班把 fresh-read 的 Draft PR 编号落到控制行，界面不再依赖另一次读取。"""
    store = _store(tmp_path)
    _register(store, pr_number=None)
    _patch_control_store(monkeypatch, store)
    handle = issue_control.VerifierRunHandle(
        store=store, repo_id="repo-a", issue_number=42, run_id="run-1", head_sha="abc123"
    )
    _request_takeover(store)
    assert handle.confirm_takeover() is True

    class _OpenDraftGitHub:
        def __init__(self) -> None:
            self.label_edits: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
            self.comments: list[str] = []

        def get_pull_request_context(
            self, branch: str, *, require_success: bool = False
        ) -> PullRequestContext:
            assert require_success is True
            return PullRequestContext(
                pr_url="https://github.com/example/repo/pull/71",
                branch=branch,
                head_sha="abc123",
                base_sha="base123",
                number=71,
                is_draft=True,
            )

        def edit_issue_labels(
            self, number: int, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
        ) -> None:
            self.label_edits.append((number, tuple(add), tuple(remove)))

        def comment_issue(self, number: int, body: str) -> None:
            self.comments.append(body)

    github_client = _OpenDraftGitHub()
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
        github_client=github_client,  # type: ignore[arg-type]
        expected_branch="issue-42",
        handle=handle,
    )

    row = store.get_issue_run(repo_id="repo-a", issue_number=42)
    assert row.pr_number == 71
    assert row.state == STATE_TAKEN_OVER


def test_takeover_sqlite_mirror_constants_match_core_port() -> None:
    """SQLite 适配器的同构状态/接管常量必须与 core 端口逐字一致。

    ``console_store_takeover`` 按约定镜像 core ``issue_run_control`` 的字符串常量
    （持久层不导入 core）。任何一侧的笔误都会让状态机静默错分类——例如
    ``_STATE_TAKEover_REQUESTED`` 拼错时 watchdog 永不命中取消、接管被受理后
    没人执行。这里用等值断言把"靠约定同步"变成机械契约。
    """
    from backend.core.shared.interfaces import issue_run_control as core_port
    from backend.infrastructure.persistence import console_store_takeover as adapter

    mirrored_states = {
        "_STATE_RUNNING": core_port.STATE_RUNNING,
        "_STATE_TAKEOVER_REQUESTED": core_port.STATE_TAKEOVER_REQUESTED,
        "_STATE_TAKEN_OVER": core_port.STATE_TAKEN_OVER,
        "_STATE_VERIFIER_COMPLETED": core_port.STATE_VERIFIER_COMPLETED,
    }
    for adapter_attr, core_value in mirrored_states.items():
        assert getattr(adapter, adapter_attr) == core_value

    mirrored_outcomes = {
        "_TAKEOVER_ACCEPTED": core_port.TAKEOVER_ACCEPTED,
        "_TAKEOVER_PENDING": core_port.TAKEOVER_PENDING,
        "_TAKEOVER_HANDED_OVER": core_port.TAKEOVER_HANDED_OVER,
        "_TAKEOVER_BINDING_MISMATCH": core_port.TAKEOVER_BINDING_MISMATCH,
        "_TAKEOVER_COMPLETED_CONFLICT": core_port.TAKEOVER_COMPLETED_CONFLICT,
        "_TAKEOVER_NOT_FOUND": core_port.TAKEOVER_NOT_FOUND,
        "_TAKEOVER_STATE_CHANGE_CONFLICT": core_port.TAKEOVER_STATE_CHANGE_CONFLICT,
    }
    for adapter_attr, core_value in mirrored_outcomes.items():
        assert getattr(adapter, adapter_attr) == core_value
