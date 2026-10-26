"""监控读取的去重、刷新边界与健康缓存验证。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.api import monitoring_reads, response_cache
from backend.api.monitoring_reads import MonitoringGitHubReads, MonitoringProcessReads
from backend.api.response_cache import TTLResponseCache


def test_cache_coalesces_builds_without_blocking_other_keys() -> None:
    """同键共享构建，其他键不被慢查询阻塞。"""
    cache = TTLResponseCache(ttl_seconds=120)
    started = Event()
    release = Event()
    payload = object()

    def build():
        started.set()
        assert release.wait(timeout=3)
        return payload

    builder = Mock(side_effect=build)
    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(cache.get_or_build, "repo", builder)
        assert started.wait(timeout=3)
        second = executor.submit(cache.get_or_build, "repo", builder)
        unrelated = executor.submit(cache.get_or_build, "other", lambda: "other")
        try:
            assert unrelated.result(timeout=1) == "other"
        finally:
            release.set()
        assert first.result(timeout=3) is payload
        assert second.result(timeout=3) is payload
    assert builder.call_count == 1


def test_cache_expiry_failure_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败可重试，TTL 到期重建，清空时在途结果不回填。"""
    clock = Mock(return_value=0)
    monkeypatch.setattr(response_cache.time, "monotonic", clock)
    cache = TTLResponseCache(ttl_seconds=120)
    builder = Mock(side_effect=[RuntimeError("unavailable"), "first", "second"])
    with pytest.raises(RuntimeError, match="unavailable"):
        cache.get_or_build("stats", builder)
    assert cache.get_or_build("stats", builder) == "first"
    clock.return_value = 119
    assert cache.get_or_build("stats", builder) == "first"
    clock.return_value = 120
    assert cache.get_or_build("stats", builder) == "second"

    def clear_during_build():
        cache.clear()
        return "discarded"

    assert cache.get_or_build("in-flight", clear_during_build) == "discarded"
    assert cache.get_or_build("in-flight", lambda: "fresh") == "fresh"


def test_pr_reads_are_shared_only_within_scan() -> None:
    """PR 与回退同轮去重，严格读取和下轮扫描不受缓存影响。"""
    client = Mock()
    reads = MonitoringGitHubReads(client)
    for _ in range(2):
        reads.get_pull_request_context("branch")
        reads.find_open_pr_by_head("branch")
        reads.list_issue_comments(1)
    assert client.get_pull_request_context.call_count == 1
    assert client.find_open_pr_by_head.call_count == 1
    assert client.list_issue_comments.call_count == 2
    reads.get_pull_request_context("branch", require_success=True)
    reads.find_open_pr_by_head("branch", require_success=True)
    client.get_pull_request_context.assert_called_with("branch", require_success=True)
    client.find_open_pr_by_head.assert_called_with("branch", require_success=True)
    MonitoringGitHubReads(client).get_pull_request_context("branch")
    assert client.get_pull_request_context.call_count == 3


def test_health_shared_but_remote_scoped_and_worktree_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """gh 跨仓库共享，remote 按路径与名称隔离，git status 始终实时。"""
    monkeypatch.setattr(monitoring_reads, "HEALTH_CACHE", TTLResponseCache(ttl_seconds=300))
    runner = Mock()
    runner.run.return_value = SimpleNamespace(return_code=0, stdout="ok", stderr="")
    reads = MonitoringProcessReads(runner)
    for repo_path in (tmp_path / "a", tmp_path / "b"):
        for _ in range(2):
            reads.run(["gh", "--version"], cwd=repo_path, check=False)
            reads.run(["git", "remote", "get-url", "origin"], cwd=repo_path, check=False)
            reads.run(["git", "status", "--porcelain"], cwd=repo_path, check=False)
    assert runner.run.call_count == 7  # gh × 1、remote × 2、status × 4
    reads.run(["git", "remote", "get-url", "upstream"], cwd=tmp_path / "a", check=False)
    assert runner.run.call_count == 8
    # 重新装配执行器仍共享健康缓存。
    MonitoringProcessReads(runner).run(["gh", "--version"], cwd=tmp_path, check=False)
    assert runner.run.call_count == 8


def test_health_failure_not_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """修复健康命令失败后，下次扫描立即重新探测。"""
    monkeypatch.setattr(monitoring_reads, "HEALTH_CACHE", TTLResponseCache(ttl_seconds=300))
    runner = Mock()
    runner.run.side_effect = [
        SimpleNamespace(return_code=1, stderr="missing"),
        SimpleNamespace(return_code=0, stdout="available", stderr=""),
    ]
    reads = MonitoringProcessReads(runner)
    with pytest.raises(RuntimeError, match="missing"):
        reads.run(["gh", "--version"], cwd=tmp_path, check=False)
    assert reads.run(["gh", "--version"], cwd=tmp_path, check=False).return_code == 0
    assert runner.run.call_count == 2


def test_overview_http_reuses_pr_but_next_scan_reads_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HTTP 入口穿过真实扫描与序列化，同分支去重且跨轮重新读取。"""
    from fastapi.testclient import TestClient

    from backend.api.app import app
    from backend.api.routes import agent_runner as routes
    from backend.core.shared.models.agent_runner import (
        AppConfig,
        IssueSummary,
        PullRequestContext,
        RepositoryRunContext,
    )

    config = AppConfig()
    context = RepositoryRunContext(
        repo_id="test", display_name="Test", repo_path=tmp_path, config=config
    )
    issues = [
        IssueSummary(
            number=number, title=f"Issue {number}", url="", body="", labels=(config.labels.ready,)
        )
        for number in (1, 2)
    ]
    github = Mock()
    github.list_ready_issues.side_effect = (
        lambda label, limit: issues if label == config.labels.ready else []
    )
    github.list_issue_comments.return_value = [
        "<!-- kc:event version=1 phase=implement cycle=1 pr_branch=shared -->"
    ]
    github.get_pull_request_context.return_value = PullRequestContext(
        pr_url="https://github.com/test/repo/pull/3",
        branch="shared",
        head_sha="abc",
        base_sha="def",
        mergeable=True,
    )
    runner = Mock()
    runner.run.return_value = SimpleNamespace(return_code=0, stdout="", stderr="")
    monkeypatch.setattr(routes, "load_fresh_agent_runner_settings", lambda: None)
    monkeypatch.setattr(
        routes, "resolve_repository_targets_with_diagnostics", lambda _: ([context], [])
    )
    monkeypatch.setattr(routes, "create_github_client", lambda _: github)
    monkeypatch.setattr(routes, "create_process_runner", lambda: runner)
    monkeypatch.setattr(routes, "_OVERVIEW_CACHE", TTLResponseCache(ttl_seconds=0))
    monkeypatch.setattr(monitoring_reads, "HEALTH_CACHE", TTLResponseCache(ttl_seconds=300))
    client = TestClient(app)
    for scan_number in (1, 2):
        response = client.get("/api/v1/agent-runner/overview")
        assert response.status_code == 200, response.text
        snapshots = response.json()["repositories"][0]["issues"]
        assert len(snapshots) == 2
        assert all(snapshot["pr"]["number"] == 3 for snapshot in snapshots)
        assert github.get_pull_request_context.call_count == scan_number
        assert github.list_issue_comments.call_count == scan_number * 2
