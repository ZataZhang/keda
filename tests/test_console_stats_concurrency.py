"""Tests for concurrent completion stats aggregation and the route TTL cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_console as console_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import (
    AppConfig,
    RepositoryRunContext,
)
from backend.core.use_cases.console_stats import (
    build_completion_stats_overview,
)
from backend.infrastructure.config.settings import AgentRunnerConsoleSettings, AgentRunnerSettings


class _DelayedFakeClient:
    """IGitHubClient stub that waits once before returning canned issues.

    Args:
        delay_seconds: 首次查询的等待时长（只等一次，使单仓库成本等于
            ``delay_seconds``）；用于构造"先提交的仓库反而更慢"的乱序
            场景与并发度证明。
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self._delay_consumed = False

    def list_issues_by_label(self, label, limit, state="all"):
        if not self._delay_consumed:
            self._delay_consumed = True
            time.sleep(self._delay_seconds)
        return []


def _context(repo_id: str) -> RepositoryRunContext:
    return RepositoryRunContext(
        repo_id=repo_id,
        display_name=repo_id,
        repo_path=Path("/tmp") / repo_id,
        config=AppConfig(),
    )


def test_overview_concurrency_actual_overlap() -> None:
    """Per-repo queries must run concurrently, not strictly one after another.

    三个仓库各等 0.4s：串行至少 1.2s，max_workers=5 的并发应在 0.9s 内
    返回。用时间断言给并发度一个下界（顺序稳定性由另一条测试保证）。
    """
    contexts = [_context("repo-a"), _context("repo-b"), _context("repo-c")]
    started_at = time.monotonic()
    stats = build_completion_stats_overview(
        contexts=contexts,
        github_client_factory=lambda repo_path: _DelayedFakeClient(0.4),
    )
    elapsed_seconds = time.monotonic() - started_at
    assert all(entry.error is None for entry in stats)
    assert elapsed_seconds < 1.0


def test_overview_preserves_context_order() -> None:
    """返回顺序必须与入参 contexts 一致，即使先提交的仓库更慢。"""
    contexts = [_context("slow"), _context("middle"), _context("fast")]
    delays = {"slow": 0.5, "middle": 0.25, "fast": 0.0}
    stats = build_completion_stats_overview(
        contexts=contexts,
        github_client_factory=lambda repo_path: _DelayedFakeClient(delays[repo_path.name]),
    )
    assert [entry.repo_id for entry in stats] == ["slow", "middle", "fast"]


def test_overview_isolates_single_repo_failure() -> None:
    """One broken repo degrades to its own error entry; others still report."""
    contexts = [_context("good-1"), _context("broken"), _context("good-2")]

    def exploding_factory(repo_path: Path):
        if repo_path.name == "broken":
            raise RuntimeError("gh exploded")
        return _DelayedFakeClient(0.0)

    stats = build_completion_stats_overview(
        contexts=contexts,
        github_client_factory=exploding_factory,
    )
    assert [entry.repo_id for entry in stats] == ["good-1", "broken", "good-2"]
    broken_entry = stats[1]
    assert broken_entry.error is not None
    assert broken_entry.total_tracked == 0
    assert all(entry.error is None for entry in (stats[0], stats[2]))


@pytest.fixture
def stats_api(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Wire the console stats route to a counting fake GitHub factory."""
    call_counter = {"client_builds": 0, "label_queries": 0}
    context = _context("keda-main")

    def counting_factory(repo_path: Path):
        call_counter["client_builds"] += 1

        class CountingClient:
            def list_issues_by_label(self, label, limit, state="all"):
                call_counter["label_queries"] += 1
                return []

        return CountingClient()

    monkeypatch.setattr(console_routes, "_resolve_contexts", lambda: [context])
    monkeypatch.setattr(console_routes, "create_github_client", counting_factory)
    # 清空模块级缓存，避免其它用例留下的条目让本用例直接命中。
    monkeypatch.setattr(
        console_routes,
        "_STATS_CACHE",
        console_routes.TTLResponseCache(ttl_seconds=30),
    )
    return call_counter


def test_stats_overview_cache_hit_within_ttl(
    stats_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二次调用必须命中缓存：不再新建客户端、不再查 gh。"""
    monkeypatch.setattr(
        console_routes,
        "load_fresh_agent_runner_settings",
        lambda: AgentRunnerSettings(console=AgentRunnerConsoleSettings(runner_command=["iar"])),
    )
    client = TestClient(app)

    first = client.get("/api/v1/agent-runner/console/stats/overview")
    second = client.get("/api/v1/agent-runner/console/stats/overview")

    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert first.json() == second.json()
    assert stats_api["client_builds"] == 1
    assert stats_api["label_queries"] == 6  # 1 repo × 6 workflow labels
