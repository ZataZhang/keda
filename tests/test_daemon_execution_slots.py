"""daemon 执行期间补位的测试（PRD P1-REFACTOR-20261024-232740）。

覆盖三层：常驻执行名单本身；``run_once`` 的补位模式（只占空位、跳过本机在执行的
Issue、提交后不等待）；daemon 主循环在长任务执行期间的下一次轮询里补领新 Issue。
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.daemon_execution_slots import DaemonExecutionSlots
from tests.conftest import FakeGitHubClient
from tests.test_agent_runner_orchestrate import _make_ready_issue, _preflight_ok_runner

REPO_ID = "keda-test"
_WAIT_SECONDS = 5


def _wait_until_finished(slots: DaemonExecutionSlots) -> list[tuple[int, int]]:
    """轮询清理直到名单为空，返回清理到的 ``(Issue 编号, 退出码)``。"""
    finished: list[tuple[int, int]] = []
    deadline_event = threading.Event()
    for _ in range(_WAIT_SECONDS * 20):
        finished.extend(slots.reap())
        if not slots.in_flight_issue_numbers():
            return finished
        deadline_event.wait(0.05)
    raise AssertionError("in-flight Issues did not finish in time")


# --- DaemonExecutionSlots ---------------------------------------------------


def test_submit_returns_before_the_issue_finishes() -> None:
    """提交立即返回，任务结束前一直在名单里，清理后释放并给出退出码。"""
    release_event = threading.Event()
    slots = DaemonExecutionSlots(REPO_ID)

    slots.submit(7, lambda: 0 if release_event.wait(_WAIT_SECONDS) else 1)

    assert slots.in_flight_issue_numbers() == frozenset({7})
    assert slots.reap() == []
    release_event.set()
    assert _wait_until_finished(slots) == [(7, 0)]


def test_submit_rejects_an_issue_already_in_flight() -> None:
    """同一个 Issue 不能被提交两次——这是防重复执行的最后一道闸。"""
    release_event = threading.Event()
    slots = DaemonExecutionSlots(REPO_ID)
    slots.submit(7, lambda: 0 if release_event.wait(_WAIT_SECONDS) else 1)

    with pytest.raises(ValueError, match="already in flight"):
        slots.submit(7, lambda: 0)
    release_event.set()
    _wait_until_finished(slots)


def test_task_exception_is_recorded_as_failure() -> None:
    """任务抛异常按退出码 1 记录，名单照常释放，不让线程静默死掉。"""
    slots = DaemonExecutionSlots(REPO_ID)

    def _explode() -> int:
        raise RuntimeError("boom")

    slots.submit(9, _explode)
    assert _wait_until_finished(slots) == [(9, 1)]


# --- run_once 补位模式 ---------------------------------------------------------


def _run_once_with_slots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fake_client: FakeGitHubClient,
    slots: DaemonExecutionSlots,
    parallel_limit: int,
) -> int:
    """以 daemon 的参数形状调用一次补位模式的 ``run_once``。"""
    from backend.core.use_cases.agent_runner_orchestrate import run_once

    return run_once(
        repo_path=tmp_path,
        config=AppConfig(),
        dry_run=False,
        agent="auto",
        max_issues=parallel_limit,
        github_client=fake_client,
        process_runner=_preflight_ok_runner(),
        repo_id=REPO_ID,
        concurrency=parallel_limit,
        execution_ceiling=parallel_limit,
        execution_slots=slots,
    )


def test_run_once_fills_only_free_slots_and_skips_in_flight_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上限 3、本机已有 #1 在跑：只再领 2 个，且绝不再领 #1，提交后立即返回。"""
    from backend.core.use_cases import agent_runner_orchestrate as orchestrate

    release_event = threading.Event()
    started_issue_numbers: list[int] = []

    def _blocking_process_ready_issue(*, issue, **_kwargs: object) -> None:
        started_issue_numbers.append(issue.number)
        release_event.wait(_WAIT_SECONDS)

    monkeypatch.setattr(orchestrate, "_process_ready_issue", _blocking_process_ready_issue)
    slots = DaemonExecutionSlots(REPO_ID)
    slots.submit(1, lambda: 0 if release_event.wait(_WAIT_SECONDS) else 1)
    fake_client = FakeGitHubClient()
    # #1 的标签还停在 ready（标签更新有延迟），它也不能被再次领取。
    ready_issues = [_make_ready_issue(n, f"I{n}", "", ("agent/ready",)) for n in (1, 2, 3, 4)]
    fake_client.list_ready_issues = lambda ready_label, limit: list(ready_issues)

    exit_code = _run_once_with_slots(
        monkeypatch, tmp_path, fake_client=fake_client, slots=slots, parallel_limit=3
    )

    assert exit_code == 0
    assert slots.in_flight_issue_numbers() == frozenset({1, 2, 3})
    release_event.set()
    _wait_until_finished(slots)
    assert sorted(started_issue_numbers) == [2, 3]


def test_run_once_counts_in_flight_when_running_label_lags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GitHub 上数到 0 个 running（标签还没写上），本机在执行数仍计入上限。"""
    from backend.core.use_cases import agent_runner_orchestrate as orchestrate

    release_event = threading.Event()
    monkeypatch.setattr(
        orchestrate,
        "_process_ready_issue",
        lambda **_kwargs: release_event.wait(_WAIT_SECONDS) and None,
    )
    slots = DaemonExecutionSlots(REPO_ID)
    slots.submit(1, lambda: 0 if release_event.wait(_WAIT_SECONDS) else 1)
    slots.submit(2, lambda: 0 if release_event.wait(_WAIT_SECONDS) else 1)
    fake_client = FakeGitHubClient()
    fake_client.set_list_issues_by_label_result([])
    ready_issues = [_make_ready_issue(n, f"I{n}", "", ("agent/ready",)) for n in (3, 4)]
    fake_client.list_ready_issues = lambda ready_label, limit: list(ready_issues)

    _run_once_with_slots(
        monkeypatch, tmp_path, fake_client=fake_client, slots=slots, parallel_limit=2
    )

    assert slots.in_flight_issue_numbers() == frozenset({1, 2})
    release_event.set()
    _wait_until_finished(slots)


# --- daemon 主循环：执行期间补位 ----------------------------------------------------


def test_daemon_backfills_a_new_issue_while_a_long_issue_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上限 2：#1 一直在跑时，下一次轮询领取新进入就绪队列的 #2；#3 等空位。"""
    import backend.core.use_cases.run_agent_daemon as daemon_module
    from backend.core.use_cases import agent_runner_orchestrate as orchestrate

    release_event = threading.Event()
    started_issue_numbers: list[int] = []

    def _blocking_process_ready_issue(*, issue, **_kwargs: object) -> None:
        started_issue_numbers.append(issue.number)
        release_event.wait(_WAIT_SECONDS)

    monkeypatch.setattr(orchestrate, "_process_ready_issue", _blocking_process_ready_issue)
    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **_kwargs: None)

    fake_client = FakeGitHubClient()
    ready_issues = [_make_ready_issue(1, "long", "", ("agent/ready",))]
    fake_client.list_ready_issues = lambda ready_label, limit: list(ready_issues)

    poll_count = 0

    def _drive_polls(_seconds: float) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            # 第一次轮询领走了 #1（它不会结束）；此时 #2、#3 进入就绪队列。
            ready_issues[:] = [_make_ready_issue(n, f"I{n}", "", ("agent/ready",)) for n in (2, 3)]
            return
        release_event.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_module.time, "sleep", _drive_polls)
    config = AppConfig()
    config = replace(config, runner=replace(config.runner, max_concurrent_issues=2))
    context = RepositoryRunContext(
        repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=config
    )

    with pytest.raises(KeyboardInterrupt):
        daemon_module.run_agent_daemon(
            contexts=[context],
            interval=1,
            agent="auto",
            process_runner=_preflight_ok_runner(),
            github_client_factory=lambda repo_path: fake_client,
        )

    for _ in range(_WAIT_SECONDS * 20):
        if len(started_issue_numbers) >= 2:
            break
        threading.Event().wait(0.05)
    # 第二次轮询时 #1 仍在跑：只剩 1 个空位，领了 #2，#3 留在队列里。
    assert sorted(started_issue_numbers) == [1, 2]
