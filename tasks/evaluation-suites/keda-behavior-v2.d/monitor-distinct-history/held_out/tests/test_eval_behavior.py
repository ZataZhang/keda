"""监控生产装配读取真实运行账本，覆盖重试数量、时间窗口和不同 Issue 上限。"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.api.routes import agent_runner as routes
from backend.core.shared.interfaces.runner_console import RunRecord
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient, FakeProcessRunner


@pytest.mark.parametrize("history_kind", ["basic", "heavy_retries", "window_and_distinct_limit"])
def test_recent_issue_history_boundaries(tmp_path, monkeypatch, history_kind):
    store = SqliteConsoleStore(tmp_path / "console.db")
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    expired = (now - timedelta(days=8)).isoformat()
    if history_kind == "basic":
        records = [("fixture", 10, recent), ("other", 99, recent)] + [("fixture", 59, recent)] * 25
        expected = {10, 59}
    elif history_kind == "heavy_retries":
        records = [("fixture", 10, recent)] + [("fixture", 59, recent)] * 1201
        expected = {10, 59}
    else:
        records = [("fixture", number, recent) for number in range(1, 25)]
        records += [("fixture", 24, recent)] * 1201
        records += [("fixture", 99, expired), ("other", 88, recent)]
        expected = set(range(5, 25))
    for repo_id, number, started_at in records:
        store.append_run(
            RunRecord(
                repo_id=repo_id,
                repo_path=str(tmp_path),
                issue_number=number,
                trigger="console_run",
                agent="codex",
                outcome="failed",
                error_summary=None,
                started_at=started_at,
                finished_at=started_at,
                duration_seconds=1,
            )
        )
    client = FakeGitHubClient()
    for number in {entry[1] for entry in records}:
        client.close_issue(number)
    monkeypatch.setattr(routes, "create_console_store", lambda: store)
    monkeypatch.setattr(
        routes,
        "resolve_repository_targets_with_diagnostics",
        lambda settings: (
            [
                RepositoryRunContext(
                    repo_id="fixture",
                    display_name="Fixture",
                    repo_path=tmp_path,
                    config=AppConfig(),
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        routes, "_get_monitoring_dependencies", lambda: (lambda _: client, FakeProcessRunner())
    )
    overview = routes._build_overview_response(["fixture"], include_tracked_issues=True)
    tracked = overview["repositories"][0]["tracked_issues"]
    assert {entry["number"] for entry in tracked} == expected
    assert len(tracked) == len(expected)
    assert all(entry["state"].lower() == "closed" for entry in tracked)
