"""GitHub API 额度预算：账本、client 接入、daemon 节奏。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import GitHubApiRateLimitError
from backend.core.shared.models.agent_runner import AppConfig, CommandResult, RepositoryRunContext
from backend.core.use_cases import run_agent_daemon as daemon_module
from backend.core.use_cases.agent_daemon_pacing import (
    IDLE_BACKOFF_MAX_FACTOR,
    MAX_BUDGET_PAUSE_SLEEP_SECONDS,
    budget_pause_sleep_seconds,
    next_poll_interval,
)
from backend.engines.agent_runner.persistence.github_api_budget_json import JsonGitHubApiBudget
from backend.infrastructure.github_client import GitHubCliClient, _gh_operation_label
from tests.conftest import FakeGitHubClient, FakeProcessRunner

_NOW = datetime(2026, 10, 15, 14, 0, 0, tzinfo=timezone.utc)


class _Clock:
    """可手动拨动的时钟。"""

    def __init__(self) -> None:
        self.moment = _NOW

    def __call__(self) -> datetime:
        return self.moment


def _budget(tmp_path: Path, clock: _Clock | None = None, **kwargs: object) -> JsonGitHubApiBudget:
    return JsonGitHubApiBudget(tmp_path / "budget.json", now=clock or _Clock(), **kwargs)


# --- 账本与暂停闸门 -------------------------------------------------------


def test_rate_limited_kind_pauses_until_resume_time_then_recovers(tmp_path: Path) -> None:
    clock = _Clock()
    budget = _budget(tmp_path, clock)
    budget.record_rate_limited("graphql", _NOW + timedelta(minutes=20))

    with pytest.raises(GitHubApiRateLimitError, match="paused until"):
        budget.ensure_available("graphql")
    budget.ensure_available("rest")  # 另一类额度不受影响
    assert budget.pause_remaining_seconds() == pytest.approx(1200)

    clock.moment = _NOW + timedelta(minutes=21)
    budget.ensure_available("graphql")
    assert budget.pause_remaining_seconds() == 0.0


def test_pause_is_shared_between_instances_on_same_file(tmp_path: Path) -> None:
    """一个进程撞限流，另一个进程（同一账本文件）立刻也被拦住。"""
    clock = _Clock()
    _budget(tmp_path, clock).record_rate_limited("graphql", _NOW + timedelta(minutes=5))
    with pytest.raises(GitHubApiRateLimitError):
        _budget(tmp_path, clock).ensure_available("graphql")


def test_a_later_block_is_not_shortened_by_an_earlier_one(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    budget.record_rate_limited("graphql", _NOW + timedelta(minutes=30))
    budget.record_rate_limited("graphql", _NOW + timedelta(minutes=5))
    assert budget.pause_remaining_seconds() == pytest.approx(1800)


def test_low_probe_pauses_polling_but_lets_in_flight_calls_through(tmp_path: Path) -> None:
    """额度偏低（没耗尽）：只暂停轮询，进行中的收尾调用仍放行。"""
    budget = _budget(tmp_path)
    reset_at = _NOW + timedelta(minutes=40)
    budget.record_probe("graphql", remaining=100, limit=5000, reset_at=reset_at)

    budget.ensure_available("graphql")  # 收尾调用（创建 PR、改标签）不受影响
    assert budget.pause_remaining_seconds() == pytest.approx(2400)  # daemon 轮询要暂停


def test_exhausted_probe_blocks_every_call(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    budget.record_probe("graphql", remaining=0, limit=5000, reset_at=_NOW + timedelta(minutes=40))
    with pytest.raises(GitHubApiRateLimitError):
        budget.ensure_available("graphql")
    assert budget.pause_remaining_seconds() == pytest.approx(2400)


def test_healthy_probe_clears_both_kinds_of_stale_pause(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    reset_at = _NOW + timedelta(minutes=40)
    budget.record_rate_limited("graphql", reset_at)
    budget.record_probe("graphql", remaining=100, limit=5000, reset_at=reset_at)
    assert budget.pause_remaining_seconds() > 0  # 偏低：仍暂停轮询，但硬暂停被撤销
    budget.ensure_available("graphql")

    budget.record_probe("graphql", remaining=4900, limit=5000, reset_at=reset_at)
    assert budget.pause_remaining_seconds() == 0.0


def test_probe_due_respects_interval_across_instances(tmp_path: Path) -> None:
    clock = _Clock()
    budget = _budget(tmp_path, clock, probe_interval_seconds=300)
    assert budget.probe_due()
    budget.record_probe("rest", remaining=4000, limit=5000, reset_at=_NOW + timedelta(hours=1))
    assert not _budget(tmp_path, clock, probe_interval_seconds=300).probe_due()
    clock.moment = _NOW + timedelta(seconds=301)
    assert budget.probe_due()


def test_usage_ledger_batches_calls_and_merges_across_instances(tmp_path: Path) -> None:
    budget = _budget(tmp_path, flush_every_calls=3, flush_interval_seconds=3600)
    for _ in range(2):
        budget.record_call("graphql", "issue create")
    assert not budget.state_path.exists()  # 还没到批量落盘阈值
    budget.record_call("graphql", "issue create")  # 第 3 次触发落盘
    other_budget = _budget(tmp_path)
    other_budget.record_call("rest", "api GET issues")
    assert other_budget.usage_summary() == {"graphql:issue create": 3, "rest:api GET issues": 1}


def test_corrupt_ledger_never_blocks_calls(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    budget.state_path.write_text("{not json", encoding="utf-8")
    budget.ensure_available("graphql")
    assert budget.pause_remaining_seconds() == 0.0


# --- 操作标签 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected_label"),
    [
        (["gh", "issue", "create", "--title", "x"], "issue create"),
        (["gh", "pr", "list", "--head", "b"], "pr list"),
        (
            ["gh", "api", "-X", "GET", "repos/o/r/issues/288/comments", "-f", "state=open"],
            "api GET issues/N/comments",
        ),
        (
            ["gh", "api", "-X", "POST", "repos/o/r/issues/12/labels", "-F", "x=y"],
            "api POST issues/N/labels",
        ),
        (["git", "status"], "other"),
    ],
)
def test_gh_operation_label_strips_repo_and_numbers(
    command: list[str], expected_label: str
) -> None:
    assert _gh_operation_label(command) == expected_label


# --- client 接入 ----------------------------------------------------------


def _rate_limit_json(graphql_remaining: int) -> str:
    """``gh api rate_limit`` 的假响应；``reset`` 固定在 30 分钟 / 50 分钟之后。"""
    return json.dumps(
        {
            "resources": {
                "graphql": {
                    "limit": 5000,
                    "remaining": graphql_remaining,
                    "reset": int(_NOW.timestamp()) + 1800,
                },
                "core": {"limit": 5000, "remaining": 4900, "reset": int(_NOW.timestamp()) + 3000},
            }
        }
    )


class _GhRunner:
    """按命令类型返回预设结果的假 ``gh`` 执行器，并记录所有调用。"""

    def __init__(
        self, *, fail_issue_with: str | None = None, graphql_remaining: int = 4900
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._fail_issue_with = fail_issue_with
        self._graphql_remaining = graphql_remaining

    def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001
        self.commands.append(tuple(command))
        if tuple(command[:3]) == ("gh", "api", "rate_limit"):
            return CommandResult(tuple(command), 0, _rate_limit_json(self._graphql_remaining), "")
        if self._fail_issue_with is not None:
            if check:
                raise subprocess.CalledProcessError(
                    1, list(command), output="", stderr=self._fail_issue_with
                )
            return CommandResult(tuple(command), 1, "", self._fail_issue_with)
        return CommandResult(tuple(command), 0, "[]", "")


def test_client_counts_calls_and_skips_gh_when_paused(tmp_path: Path) -> None:
    budget = _budget(tmp_path, flush_every_calls=1)
    runner = _GhRunner()
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)
    assert "graphql:issue view" in budget.usage_summary()

    budget.record_rate_limited("graphql", _NOW + timedelta(minutes=10))
    commands_before = len(runner.commands)
    with pytest.raises(GitHubApiRateLimitError):
        client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)
    assert len(runner.commands) == commands_before  # 暂停期内一个真实请求都没发


def test_client_rate_limit_error_pauses_budget_until_real_reset(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    runner = _GhRunner(fail_issue_with="GraphQL: API rate limit already exceeded for user ID 1.")
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    with pytest.raises(GitHubApiRateLimitError):
        client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)

    # 暂停到 rate_limit 端点报告的重置时间（约 30 分钟），而不是固定兜底值。
    assert budget.pause_remaining_seconds() == pytest.approx(1800, abs=5)
    with pytest.raises(GitHubApiRateLimitError):
        budget.ensure_available("graphql")
    budget.ensure_available("rest")


def test_client_without_budget_behaves_as_before(tmp_path: Path) -> None:
    runner = _GhRunner()
    client = GitHubCliClient(tmp_path, runner)  # type: ignore[arg-type]
    client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)
    assert runner.commands == [("gh", "issue", "view", "5")]


def test_client_probe_runs_once_per_interval_and_pauses_on_low_remaining(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    runner = _GhRunner(graphql_remaining=0)
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    # 探测结果里 graphql 剩余 0 → 首个请求探测后就被拦住，不会发出真实请求。
    with pytest.raises(GitHubApiRateLimitError):
        client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)
    assert [c for c in runner.commands if c[:3] != ("gh", "api", "rate_limit")] == []

    # REST 剩余充足，不受影响；探测间隔内不会重复探测。
    probes_before = sum(1 for c in runner.commands if c[:3] == ("gh", "api", "rate_limit"))
    client._run_with_retry(["gh", "api", "-X", "GET", "repos/o/r/issues"], cwd=tmp_path)
    probes_after = sum(1 for c in runner.commands if c[:3] == ("gh", "api", "rate_limit"))
    assert probes_after == probes_before


# --- 轮询节奏 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("idle_streak", "expected_seconds"),
    [(0, 120), (2, 120), (3, 240), (4, 480), (9, 480)],
)
def test_next_poll_interval_backs_off_and_caps(idle_streak: int, expected_seconds: int) -> None:
    assert next_poll_interval(120, idle_streak, IDLE_BACKOFF_MAX_FACTOR) == expected_seconds


def test_next_poll_interval_factor_one_disables_backoff() -> None:
    assert next_poll_interval(120, 50, 1) == 120


def test_budget_pause_sleep_is_capped_and_none_budget_never_pauses(tmp_path: Path) -> None:
    assert budget_pause_sleep_seconds(None) == 0.0
    budget = _budget(tmp_path)
    assert budget_pause_sleep_seconds(budget) == 0.0
    budget.record_rate_limited("graphql", _NOW + timedelta(hours=1))
    assert budget_pause_sleep_seconds(budget) == MAX_BUDGET_PAUSE_SLEEP_SECONDS


# --- daemon 主循环 --------------------------------------------------------


def _context(tmp_path: Path) -> RepositoryRunContext:
    return RepositoryRunContext(
        repo_id="budget-test", repo_path=tmp_path, display_name="budget-test", config=AppConfig()
    )


def _run_daemon_rounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ready_per_round: list[int],
    rounds: int,
    api_budget: JsonGitHubApiBudget | None = None,
) -> tuple[list[float], list[int]]:
    """跑固定轮数的 daemon，返回每次 sleep 的秒数与 run_once 被调用的次数。"""
    sleeps: list[float] = []
    run_once_calls: list[int] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= rounds:
            raise StopIteration

    def fake_run_once(**kwargs):  # noqa: ANN003
        round_index = len(run_once_calls)
        run_once_calls.append(round_index)
        kwargs["pass_activity"].ready_found = ready_per_round[
            min(round_index, len(ready_per_round) - 1)
        ]
        return 0

    monkeypatch.setattr(daemon_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(daemon_module, "run_once", fake_run_once)
    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **_kwargs: None)
    with pytest.raises(StopIteration):
        daemon_module.run_agent_daemon(
            contexts=[_context(tmp_path)],
            interval=100,
            agent="claude",
            process_runner=FakeProcessRunner(),
            github_client_factory=lambda _repo_path: FakeGitHubClient(),
            api_budget=api_budget,
        )
    return sleeps, run_once_calls


def test_daemon_backs_off_while_idle_and_snaps_back_when_work_appears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps, _ = _run_daemon_rounds(
        monkeypatch, tmp_path, ready_per_round=[0, 0, 0, 0, 0, 3, 0], rounds=7
    )
    # 前两轮空闲保持 100；之后 200、400（封顶）；出现 ready 后立刻回到 100。
    assert sleeps == [100, 100, 200, 400, 400, 100, 100]


def test_daemon_skips_polling_entirely_while_budget_is_paused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    budget = JsonGitHubApiBudget(tmp_path / "budget.json")
    budget.record_rate_limited("graphql", datetime.now(timezone.utc) + timedelta(minutes=10))
    sleeps, run_once_calls = _run_daemon_rounds(
        monkeypatch, tmp_path, ready_per_round=[0], rounds=2, api_budget=budget
    )
    assert run_once_calls == []  # 暂停期内一次轮询都没发
    assert all(0 < seconds <= MAX_BUDGET_PAUSE_SLEEP_SECONDS for seconds in sleeps)


# --- 来源归因与汇总日志 ---------------------------------------------------


def test_usage_ledger_prefixes_label_with_process_source(tmp_path: Path) -> None:
    daemon_budget = JsonGitHubApiBudget(tmp_path / "budget.json", source="kc daemon", now=_Clock())
    console_budget = JsonGitHubApiBudget(
        tmp_path / "budget.json", source="kc console", now=_Clock()
    )
    daemon_budget.record_call("graphql", "issue view")
    console_budget.record_call("rest", "api GET issues/N/comments")
    console_budget.record_call("rest", "api GET issues/N/comments")
    daemon_budget.flush()  # 计数默认攒在内存里批量落盘；显式落盘后才对其他进程可见
    assert console_budget.usage_summary() == {
        "kc daemon graphql:issue view": 1,
        "kc console rest:api GET issues/N/comments": 2,
    }


def test_format_usage_summary_ranks_by_count(tmp_path: Path) -> None:
    from backend.core.use_cases.agent_daemon_pacing import format_usage_summary

    budget = _budget(tmp_path)
    assert format_usage_summary(budget) == "no calls recorded"
    budget.record_call("rest", "api GET a")
    for _ in range(3):
        budget.record_call("graphql", "issue list")
    assert format_usage_summary(budget) == "total=4; graphql:issue list=3, rest:api GET a=1"


def test_client_can_finish_publication_while_quota_is_low_but_not_empty(tmp_path: Path) -> None:
    """收尾场景：剩余额度偏低（100/5000）时，创建 PR 这类调用仍能发出去。"""
    budget = _budget(tmp_path)
    runner = _GhRunner(graphql_remaining=100)
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    client._run_with_retry(["gh", "pr", "create", "--draft", "--title", "x"], cwd=tmp_path)

    assert ("gh", "pr", "create", "--draft", "--title", "x") in runner.commands
    assert budget.pause_remaining_seconds() > 0  # 但 daemon 的轮询已经被叫停


# --- 账本写不进去时不得弄坏 gh 调用 ----------------------------------------


def _unwritable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(JsonGitHubApiBudget, "_write_state", _deny)


def test_unwritable_ledger_never_raises_from_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """只读目录 / 磁盘满：记账降级为告警，不抛异常（且只告警一次）。"""
    budget = _budget(tmp_path, flush_every_calls=1)
    _unwritable(monkeypatch)

    budget.record_call("graphql", "issue view")
    budget.record_rate_limited("graphql", _NOW + timedelta(minutes=5))
    budget.record_probe("graphql", remaining=100, limit=5000, reset_at=_NOW + timedelta(minutes=5))

    warnings = [record for record in caplog.records if "not writable" in record.getMessage()]
    assert len(warnings) == 1


def test_rate_limit_is_still_deferred_when_the_ledger_is_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """撞限流时账本写不进去，调用方仍必须收到限流延期信号，而不是 PermissionError。"""
    budget = _budget(tmp_path, flush_every_calls=1)
    _unwritable(monkeypatch)
    runner = _GhRunner(fail_issue_with="GraphQL: API rate limit already exceeded for user ID 1.")
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    with pytest.raises(GitHubApiRateLimitError):
        client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)


def test_unwritable_ledger_does_not_break_normal_gh_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = _budget(tmp_path, flush_every_calls=1)
    _unwritable(monkeypatch)
    runner = _GhRunner()
    client = GitHubCliClient(tmp_path, runner, api_budget=budget)  # type: ignore[arg-type]

    client._run_with_retry(["gh", "issue", "view", "5"], cwd=tmp_path)

    assert ("gh", "issue", "view", "5") in runner.commands
