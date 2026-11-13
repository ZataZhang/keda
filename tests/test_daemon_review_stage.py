"""daemon 内置 PR 值守阶段的测试。

发 PR 后 Issue 停在 supervising，普通 daemon 必须自己回看推进；本机已有
supervise-daemon 时让出，且不碰本机仍在执行的 Issue。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.agent_daemon_loops import DaemonReviewStage
from tests.conftest import FakeGitHubClient
from tests.test_agent_runner_orchestrate import _make_ready_issue, _preflight_ok_runner

REPO_ID = "keda-test"


def _run_daemon_polls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    review_stage: DaemonReviewStage | None,
    poll_rounds: int = 1,
) -> list[dict[str, object]]:
    """驱动 daemon 跑指定轮数，返回每次 PR 值守调用的参数。"""
    import backend.core.use_cases.run_agent_daemon as daemon_module

    review_calls: list[dict[str, object]] = []
    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_module, "run_once", lambda **_kwargs: 0)
    monkeypatch.setattr(
        daemon_module, "review_once", lambda **kwargs: review_calls.append(kwargs) or 0
    )
    poll_count = 0

    def _drive_polls(_seconds: float) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count >= poll_rounds:
            raise KeyboardInterrupt

    monkeypatch.setattr(daemon_module.time, "sleep", _drive_polls)
    context = RepositoryRunContext(
        repo_id=REPO_ID, display_name="Keda Test", repo_path=tmp_path, config=AppConfig()
    )
    with pytest.raises(KeyboardInterrupt):
        daemon_module.run_agent_daemon(
            contexts=[context],
            interval=1,
            agent="auto",
            process_runner=_preflight_ok_runner(),
            github_client_factory=lambda repo_path: FakeGitHubClient(),
            review_stage=review_stage,
        )
    return review_calls


def test_daemon_runs_review_pass_when_no_supervise_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """没有 supervise-daemon 时，daemon 每轮推进 supervising Issue。"""
    review_calls = _run_daemon_polls(
        monkeypatch,
        tmp_path,
        review_stage=DaemonReviewStage(
            interval_seconds=0, max_issues=1, review_daemon_running=lambda _repo_id: False
        ),
    )

    assert len(review_calls) == 1
    assert review_calls[0]["repo_id"] == REPO_ID
    assert review_calls[0]["max_issues"] == 1


def test_daemon_skips_review_pass_when_supervise_daemon_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """本机已有 supervise-daemon 时 daemon 让出，避免两边同时推进同一 Issue。"""
    review_calls = _run_daemon_polls(
        monkeypatch,
        tmp_path,
        review_stage=DaemonReviewStage(
            interval_seconds=0, max_issues=1, review_daemon_running=lambda _repo_id: True
        ),
    )

    assert review_calls == []


def test_daemon_review_pass_respects_review_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """轮询间隔短于值守间隔时，同一仓库只值守一次。"""
    review_calls = _run_daemon_polls(
        monkeypatch,
        tmp_path,
        review_stage=DaemonReviewStage(
            interval_seconds=3600, max_issues=1, review_daemon_running=lambda _repo_id: False
        ),
        poll_rounds=3,
    )

    assert len(review_calls) == 1


def test_daemon_without_review_stage_does_not_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """未注入值守阶段的调用方保持原行为。"""
    assert _run_daemon_polls(monkeypatch, tmp_path, review_stage=None) == []


def test_review_once_skips_excluded_in_flight_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """本机仍在执行的 Issue 由执行线程自己推进，值守跳过它。"""
    from backend.core.use_cases import review_once as review_module

    processed_issue_numbers: list[int] = []
    monkeypatch.setattr(
        review_module,
        "_process_review_candidate",
        lambda *, issue, **_kwargs: processed_issue_numbers.append(issue.number) or "waiting",
    )
    fake_client = FakeGitHubClient()
    fake_client.list_review_candidate_issues = lambda labels, limit: [
        _make_ready_issue(number, f"I{number}", "", ("agent/supervising",)) for number in (1, 2)
    ]
    exit_code = review_module.review_once(
        repo_path=tmp_path,
        config=AppConfig(),
        dry_run=False,
        agent="auto",
        max_issues=2,
        github_client=fake_client,
        process_runner=_preflight_ok_runner(),
        repo_id=REPO_ID,
        exclude_issue_numbers=frozenset({1}),
    )

    assert exit_code == 0
    assert processed_issue_numbers == [2]
