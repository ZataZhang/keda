"""Tests for real-time completion stats aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    RepositoryRunContext,
)
from backend.core.shared.interfaces.runner_console import (
    AgentPerformanceRecords,
    PerformanceAttemptRecord,
    PerformanceRunRecord,
)
from backend.core.use_cases.console_stats import (
    build_agent_performance_stats,
    build_completion_stats,
)


class FakeStatsGitHubClient:
    """IGitHubClient stub returning canned label query results."""

    def __init__(self, issues_by_label: dict[str, list[IssueSummary]]) -> None:
        self._issues_by_label = issues_by_label
        self.requested_states: list[str] = []

    def list_issues_by_label(self, label, limit, state="all"):
        self.requested_states.append(state)
        return self._issues_by_label.get(label, [])


def _issue(number: int, labels: tuple[str, ...], state: str) -> IssueSummary:
    return IssueSummary(
        number=number,
        title=f"Issue {number}",
        url=f"https://example.test/{number}",
        body="",
        labels=labels,
        state=state,
    )


def _make_context() -> RepositoryRunContext:
    return RepositoryRunContext(
        repo_id="keda-main",
        display_name="Keda Main",
        repo_path=Path("/tmp/repo"),
        config=AppConfig(),
    )


def test_completion_stats_partitions_outcomes() -> None:
    """Closed/failed/blocked/open issues should be counted correctly."""
    config = AppConfig()
    labels = config.labels
    issues_by_label = {
        # closed 且无 failed/blocked → completed
        labels.review: [_issue(1, (labels.review,), "CLOSED")],
        # closed 但带 failed → failed，不计 completed
        labels.failed: [
            _issue(2, (labels.failed,), "CLOSED"),
            _issue(3, (labels.failed,), "OPEN"),
        ],
        # open blocked
        labels.blocked: [_issue(4, (labels.blocked,), "OPEN")],
        # open 进行中
        labels.running: [_issue(5, (labels.running,), "OPEN")],
    }
    github_client = FakeStatsGitHubClient(issues_by_label)
    stats = build_completion_stats(context=_make_context(), github_client=github_client)

    assert stats.total_tracked == 5
    assert stats.completed == 1
    assert stats.failed == 2
    assert stats.blocked == 1
    assert stats.open_in_pipeline == 1  # 仅 #5；#3/#4 是 open failed/blocked。
    assert stats.completion_rate == 1 / 5
    assert stats.truncated is False
    # 必须用 state="all" 查询，否则 closed Issue 进不了统计。
    assert set(github_client.requested_states) == {"all"}


def test_completion_stats_dedupes_multi_label_issues() -> None:
    """An issue carrying two workflow labels must be counted once."""
    config = AppConfig()
    labels = config.labels
    shared_issue = _issue(7, (labels.supervising, labels.review), "OPEN")
    github_client = FakeStatsGitHubClient(
        {
            labels.supervising: [shared_issue],
            labels.review: [shared_issue],
        }
    )
    stats = build_completion_stats(context=_make_context(), github_client=github_client)
    assert stats.total_tracked == 1
    assert stats.open_in_pipeline == 1


def test_completion_stats_empty_repo() -> None:
    """No tracked issues → completion_rate is None, not a division error."""
    stats = build_completion_stats(context=_make_context(), github_client=FakeStatsGitHubClient({}))
    assert stats.total_tracked == 0
    assert stats.completion_rate is None


def test_completion_stats_isolates_github_failure() -> None:
    """A GitHub query failure must degrade to an error entry, not raise."""

    class ExplodingClient:
        def list_issues_by_label(self, label, limit, state="all"):
            raise RuntimeError("gh exploded")

    stats = build_completion_stats(context=_make_context(), github_client=ExplodingClient())
    assert stats.error is not None
    assert stats.total_tracked == 0


def test_agent_performance_stats_uses_attempt_snapshots_and_separate_run_outcomes() -> None:
    """Attempt 分组使用持久化归属；整项任务结果独立统计。"""

    class InMemoryPerformanceStore:
        def __init__(self) -> None:
            self.query: tuple[str | None, str] | None = None

        def list_agent_performance_records(
            self, *, repo_id: str | None, since: str
        ) -> AgentPerformanceRecords:
            self.query = (repo_id, since)
            return AgentPerformanceRecords(
                attempts=(
                    PerformanceAttemptRecord(
                        repo_id="keda-main",
                        agent="codex",
                        failure_type="success",
                        duration_seconds=10,
                        preset="deleted-preset",
                        model="model-a",
                    ),
                    PerformanceAttemptRecord(
                        repo_id="keda-main",
                        agent="codex",
                        failure_type="verification_failed",
                        duration_seconds=30,
                        preset="deleted-preset",
                        model="model-a",
                    ),
                    PerformanceAttemptRecord(
                        repo_id="keda-main",
                        agent="codex",
                        failure_type="success",
                        duration_seconds=20,
                        preset="deleted-preset",
                        model="model-b",
                    ),
                    PerformanceAttemptRecord(
                        repo_id="keda-main",
                        agent="",
                        failure_type="future_failure_type",
                        duration_seconds=-1,
                        preset=None,
                        model=None,
                    ),
                ),
                runs=(
                    PerformanceRunRecord("keda-main", "completed", 45),
                    PerformanceRunRecord("keda-main", "failed", 15),
                    PerformanceRunRecord("keda-main", "not_a_run_outcome", 5),
                ),
            )

    performance_store = InMemoryPerformanceStore()
    reference_now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    stats = build_agent_performance_stats(
        store=performance_store,
        repo_id="keda-main",
        days=30,
        reference_now=reference_now,
    )

    assert performance_store.query == (
        "keda-main",
        (reference_now - timedelta(days=30)).isoformat(timespec="seconds"),
    )
    assert stats.window_days == 30
    assert stats.unbound_preset_attempt_count == 1
    codex_group = next(group for group in stats.agents if group.agent == "codex")
    assert codex_group.attempt_count == 3
    assert codex_group.success_count == 2
    assert codex_group.non_success_count == 1
    assert codex_group.success_rate == 2 / 3
    assert codex_group.non_success_rate == 1 / 3
    assert codex_group.p50_duration_seconds == 20
    assert codex_group.p90_duration_seconds == 28
    assert codex_group.failure_types[0].failure_type == "verification_failed"
    assert codex_group.failure_types[0].count == 1
    assert len(stats.presets) == 2
    assert {group.model for group in stats.presets} == {"model-a", "model-b"}
    assert stats.presets[0].preset == "deleted-preset"
    missing_agent_group = next(group for group in stats.agents if group.agent == "未记录")
    assert missing_agent_group.success_rate == 0
    assert missing_agent_group.failure_types[0].failure_type == "future_failure_type"
    assert [(group.outcome, group.run_count) for group in stats.runs] == [
        ("completed", 1),
        ("failed", 1),
    ]


def test_agent_performance_stats_empty_window_has_no_zero_rate_groups() -> None:
    """空时间窗口不生成样本组或成功率。"""

    class EmptyPerformanceStore:
        def list_agent_performance_records(
            self, *, repo_id: str | None, since: str
        ) -> AgentPerformanceRecords:
            return AgentPerformanceRecords(attempts=(), runs=())

    stats = build_agent_performance_stats(
        store=EmptyPerformanceStore(),
        repo_id=None,
        days=7,
    )

    assert stats.agents == ()
    assert stats.presets == ()
    assert stats.unbound_preset_attempt_count == 0
    assert stats.runs == ()
