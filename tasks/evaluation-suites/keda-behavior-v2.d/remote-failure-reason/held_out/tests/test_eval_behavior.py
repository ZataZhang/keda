"""监控快照到真实 SQLite 再到看板摘要，验证远端失败理由。"""

from dataclasses import asdict

import pytest

from backend.core.shared.interfaces.runner_console import RunRecord
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_monitor import build_issue_snapshot
from tests.conftest import FakeGitHubClient, FakeProcessRunner
from tests.eval_support import _store, _seed_snapshot, _summary, _iso


@pytest.mark.parametrize(
    "comment, expected",
    [
        (
            "## Agent Runner Review Failed\n\n```text\nTraceback:\nModuleNotFoundError: missing package\n```",
            "missing package",
        ),
        (
            "## Agent Runner Failed\n\n```text\nerror: cannot push\nhint: read the manual\n```",
            "cannot push",
        ),
        ("## Agent Runner Merge Queue Failed\n\nrebase conflict on main", "rebase conflict"),
    ],
)
def test_remote_comment_reaches_dashboard_when_no_local_run(tmp_path, comment, expected):
    snapshot = build_issue_snapshot(
        issue=IssueSummary(42, "Broken", "https://example.test/42", "", ("agent/failed",)),
        config=AppConfig(),
        github_client=FakeGitHubClient(),
        process_runner=FakeProcessRunner(),
        repo_path=tmp_path,
        comments=["## Agent Runner Failed\n\nold reason", comment, "unrelated"],
    )
    store = _store(tmp_path)
    _seed_snapshot(store, "keda", [asdict(snapshot)])
    summary = _summary(store, ["keda"]).tasks[0].last_error_summary
    assert summary is not None and expected in summary
    assert "read the manual" not in summary


def test_local_run_error_has_precedence(tmp_path):
    store = _store(tmp_path)
    _seed_snapshot(
        store,
        "keda",
        [
            {
                "number": 42,
                "title": "Broken",
                "url": "https://example.test/42",
                "labels": ["agent/failed"],
                "state": "open",
                "timeline": [],
                "anomaly_types": [],
                "latest_failure_summary": "remote reason",
            }
        ],
    )
    store.append_run(
        RunRecord(
            repo_id="keda",
            repo_path=str(tmp_path),
            issue_number=42,
            trigger="console_run",
            agent="codex",
            outcome="failed",
            error_summary="local reason",
            started_at=_iso(-20),
            finished_at=_iso(-10),
            duration_seconds=600,
        )
    )
    assert _summary(store, ["keda"]).tasks[0].last_error_summary == "local reason"
