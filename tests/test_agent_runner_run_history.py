"""验证运行历史记录携带 Issue 的任务标题与规范链接。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from backend.core.shared.interfaces.runner_console import IRunHistoryStore, RunRecord
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.agent_runner_run_history import append_run_record


def test_append_run_record_captures_issue_title_and_url() -> None:
    """追加运行历史时保存同一次 Issue 快照中的标题和 URL。"""
    saved_run_records: list[RunRecord] = []
    run_history_store = cast(
        IRunHistoryStore,
        SimpleNamespace(append_run=saved_run_records.append),
    )
    issue_summary = IssueSummary(
        number=264,
        title="Improve recent run task names",
        url="https://github.com/example/keda/issues/264",
        body="",
        labels=(),
    )

    append_run_record(
        run_history_store=run_history_store,
        repo_id="keda",
        repo_path=Path("/tmp/keda"),
        issue=issue_summary,
        trigger="cli_run",
        agent="codex",
        outcome="completed",
        error_summary=None,
        started_at=datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc),
    )

    assert len(saved_run_records) == 1
    assert saved_run_records[0].issue_title == "Improve recent run task names"
    assert saved_run_records[0].issue_url == "https://github.com/example/keda/issues/264"
