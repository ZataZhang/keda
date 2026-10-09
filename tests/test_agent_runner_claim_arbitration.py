"""首次领取真 CAS（FR-23）的 focused 测试：投标 → 回读 → 全序仲裁 → 赢家切 running。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_claim_arbitration import (
    CONCURRENT_CLAIM_WINDOW_SECONDS,
    ClaimArbitrationLost,
    ClaimBid,
    arbitrate_first_claim,
    concurrent_bids,
    format_claim_comment,
    format_withdrawn_claim_comment,
)
from backend.core.use_cases.agent_runner_reclaim import (
    format_claim_marker,
    parse_claim_marker_detail,
)
from tests.conftest import FakeGitHubClient

HOST = "runner-a"
NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def _bid_comment(host: str, pid: int, started_at: datetime, agent: str = "claude") -> str:
    """构造一条历史认领评论（与真实领取评论同格式）。"""
    return format_claim_comment(ClaimBid(0, host, pid, started_at, agent))


def _arbitrate(
    client: FakeGitHubClient,
    *,
    host: str = HOST,
    pid: int = 111,
    started_at: datetime = NOW,
    alive_pids: set[int] | None = None,
) -> ClaimBid:
    """跑一次仲裁，屏蔽真实睡眠。"""
    alive = {pid} if alive_pids is None else alive_pids
    return arbitrate_first_claim(
        issue_number=42,
        github_client=client,
        config=AppConfig(),
        selected_agent="claude",
        grace_seconds=0.0,
        host=host,
        pid=pid,
        started_at=started_at,
        sleeper=lambda _seconds: None,
        pid_alive=lambda candidate: candidate in alive,
        now=NOW,
    )


def test_sole_bidder_wins_and_issue_becomes_running() -> None:
    """线程里只有自己的投标时胜出，并把标签切到 running。"""
    client = FakeGitHubClient()

    bid = _arbitrate(client)

    assert bid.host == HOST and bid.pid == 111
    labels_call = next(call for call in client.calls if call["method"] == "edit_issue_labels")
    assert "agent/running" in labels_call["add"]


def test_winner_hook_runs_before_running_label_transition() -> None:
    """互斥准入在赢家切 running 前执行，锁冲突不会留下假 running 标签。"""
    client = FakeGitHubClient()
    callback_calls: list[str] = []

    def _claim_prd_lock() -> None:
        assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]
        callback_calls.append("claimed")

    arbitrate_first_claim(
        issue_number=42,
        github_client=client,
        config=AppConfig(),
        selected_agent="claude",
        grace_seconds=0,
        host=HOST,
        pid=111,
        started_at=NOW,
        sleeper=lambda _seconds: None,
        pid_alive=lambda candidate: candidate == 111,
        now=NOW,
        on_won=_claim_prd_lock,
    )

    assert callback_calls == ["claimed"]
    assert [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_earlier_rival_claim_makes_this_side_lose_without_touching_labels() -> None:
    """存在更早的认领者时落败：不写标签，并把自己那条评论改写成撤销。"""
    client = FakeGitHubClient()
    client.comment_issue(42, _bid_comment("runner-b", 222, NOW - timedelta(seconds=10)))

    with pytest.raises(ClaimArbitrationLost) as exc_info:
        _arbitrate(client)

    assert exc_info.value.winner is not None
    assert exc_info.value.winner.host == "runner-b"
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]
    withdrawn = next(call for call in client.calls if call["method"] == "edit_issue_comment")
    assert "iar:claim-withdrawn" in withdrawn["body"]


def test_same_timestamp_tie_break_is_the_same_for_both_processes() -> None:
    """同刻投标按 (host, pid) 打破平局，且两边算出同一个赢家。"""
    client = FakeGitHubClient()
    client.comment_issue(42, _bid_comment("runner-b", 1, NOW))

    # runner-a < runner-b 字典序，因此本方赢。
    assert _arbitrate(client, host="runner-a", pid=9).host == "runner-a"

    rival_client = FakeGitHubClient()
    rival_client.comment_issue(42, _bid_comment("runner-a", 9, NOW))
    with pytest.raises(ClaimArbitrationLost):
        _arbitrate(rival_client, host="runner-b", pid=1)


def test_historical_claim_outside_the_window_does_not_block_reclaim() -> None:
    """历史认领（超出并发窗口）不参与仲裁，否则 Issue 永远无法被再次领取。"""
    client = FakeGitHubClient()
    stale = NOW - timedelta(seconds=CONCURRENT_CLAIM_WINDOW_SECONDS + 60)
    client.comment_issue(42, _bid_comment("runner-b", 222, stale))

    assert _arbitrate(client).pid == 111


def test_legacy_marker_without_started_at_is_excluded() -> None:
    """没有 started_at 的老 marker 无法排序，不参与仲裁。"""
    client = FakeGitHubClient()
    client.comment_issue(42, f"legacy claim\n{format_claim_marker('runner-b', 222)}")

    assert _arbitrate(client).pid == 111


def test_dead_local_bid_is_dropped_but_live_remote_bid_counts() -> None:
    """本机死进程的投标让位；跨机投标无法探活，按有效计入（fail-closed）。"""
    entries = [
        (1, _bid_comment(HOST, 999, NOW - timedelta(seconds=5))),
        (2, _bid_comment("runner-b", 777, NOW - timedelta(seconds=5))),
    ]

    bids = concurrent_bids(entries, this_host=HOST, now=NOW, pid_alive=lambda pid: pid == 777)

    assert [bid.host for bid in bids] == ["runner-b"]


def test_read_back_failure_fails_closed_without_running_label() -> None:
    """回读失败时无法证明自己赢，本轮放弃且不改标签。"""

    class _BrokenClient(FakeGitHubClient):
        def list_issue_comment_entries(self, issue_number: int) -> list[tuple[int, str]]:
            raise RuntimeError("network down")

    client = _BrokenClient()
    with pytest.raises(ClaimArbitrationLost) as exc_info:
        _arbitrate(client)

    assert exc_info.value.winner is None
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_own_marker_missing_on_read_back_loses() -> None:
    """回读看不到自己的投标时按落败处理（不进入执行）。"""

    class _DropOwnClient(FakeGitHubClient):
        def list_issue_comment_entries(self, issue_number: int) -> list[tuple[int, str]]:
            return []

    client = _DropOwnClient()
    with pytest.raises(ClaimArbitrationLost):
        _arbitrate(client)
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_same_process_reentry_wins_over_its_own_earlier_bid() -> None:
    """同一次领取的重入（换 agent 会再领一遍）不能判负，也不撤销自己的认领。"""
    client = FakeGitHubClient()
    _arbitrate(client, started_at=NOW)

    again = _arbitrate(client, started_at=NOW + timedelta(seconds=3))

    assert again.pid == 111
    assert not [call for call in client.calls if call["method"] == "edit_issue_comment"]


def test_rival_bid_inserted_during_grace_wins_and_loser_withdraws() -> None:
    """宽限窗口内到达的更早投标（本方投递后才可见）胜出：本方判负、撤销、不碰标签。

    这是裸 read-modify-write 负控的进程内近似：对手的评论在「投标之后、回读之前」
    才进入线程，只有真 CAS（先投标、再回读全量投标仲裁）才会看到它；任何退回
    「投递前看一眼就写标签」的实现都会让双方同时自以为赢。
    """
    client = FakeGitHubClient()
    rival = _bid_comment("runner-b", 222, NOW - timedelta(seconds=5))
    injected = False

    def _sleeper(_seconds: float) -> None:
        nonlocal injected
        if not injected:
            injected = True
            client.comment_issue(42, rival)

    with pytest.raises(ClaimArbitrationLost) as exc_info:
        arbitrate_first_claim(
            issue_number=42,
            github_client=client,
            config=AppConfig(),
            selected_agent="claude",
            grace_seconds=1.0,
            host=HOST,
            pid=111,
            started_at=NOW,
            sleeper=_sleeper,
            pid_alive=lambda candidate: candidate == 111,
            now=NOW,
        )

    assert exc_info.value.winner is not None
    assert exc_info.value.winner.host == "runner-b"
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]
    withdrawn = next(call for call in client.calls if call["method"] == "edit_issue_comment")
    assert "iar:claim-withdrawn" in withdrawn["body"]


def test_other_live_process_on_the_same_host_still_wins_the_earlier_bid() -> None:
    """同机的**另一个**存活进程（例如守护进程）先投标时，本方判负。"""
    client = FakeGitHubClient()
    _arbitrate(client, pid=111, started_at=NOW)

    with pytest.raises(ClaimArbitrationLost) as exc_info:
        _arbitrate(client, pid=222, started_at=NOW + timedelta(seconds=3), alive_pids={111, 222})
    assert exc_info.value.winner is not None
    assert exc_info.value.winner.pid == 111


def test_two_arbitrations_against_one_issue_leave_exactly_one_winner() -> None:
    """同一 Issue 上两个进程先后仲裁：只有一个赢，输家不碰标签。"""
    client = FakeGitHubClient()

    first = _arbitrate(client, host="runner-a", pid=1, started_at=NOW)
    with pytest.raises(ClaimArbitrationLost):
        _arbitrate(client, host="runner-b", pid=2, started_at=NOW + timedelta(seconds=1))

    assert first.host == "runner-a"
    labels_calls = [call for call in client.calls if call["method"] == "edit_issue_labels"]
    assert len(labels_calls) == 1
    # 线程里最后一条「可解析的认领」必须是赢家：输家那条已被改写。
    claims = [
        parsed
        for _comment_id, body in client.list_issue_comment_entries(42)
        if (parsed := parse_claim_marker_detail(body)) is not None
    ]
    assert [claim.host for claim in claims] == ["runner-a"]


def test_withdrawn_marker_is_not_parsed_as_a_claim() -> None:
    """撤销 marker 不被认领解析器匹配，因此「最近一次认领」指向赢家。"""
    loser = ClaimBid(0, "runner-b", 2, NOW, "claude")
    winner = ClaimBid(0, "runner-a", 1, NOW - timedelta(seconds=1), "claude")

    assert parse_claim_marker_detail(format_withdrawn_claim_comment(loser, winner)) is None
