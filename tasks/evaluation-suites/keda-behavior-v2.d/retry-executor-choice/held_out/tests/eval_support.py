"""从冻结基线抽取的测试夹具；来源见 suite provenance.json。"""

from __future__ import annotations


import json


import sys


from pathlib import Path


from backend.core.shared.interfaces.runner_console import (
    InvocationEventRecord,
    RunRecord,
)


from backend.core.shared.models.agent_runner import (
    IssueSummary,
)


from backend.infrastructure.console.process_supervisor import (
    PidfileProcessSupervisor,
)


from backend.infrastructure.persistence.console_store import SqliteConsoleStore


class FakeGitHubClient:
    """Minimal IGitHubClient stub for label-flip actions."""

    def __init__(self, issue: IssueSummary) -> None:
        self._issue = issue
        self.label_edits: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []

    def get_issue(self, issue_number: int) -> IssueSummary:
        assert issue_number == self._issue.number
        return self._issue

    def edit_issue_labels(self, issue_number, *, add=(), remove=()):
        self.label_edits.append((issue_number, tuple(add), tuple(remove)))


def _make_supervisor(tmp_path: Path) -> PidfileProcessSupervisor:
    return PidfileProcessSupervisor(
        registry_path=tmp_path / "processes.json",
        log_dir=tmp_path / "logs",
    )


def _fake_runner_command() -> list[str]:
    return [sys.executable, "-c", "print('fake runner')"]


def _seed_failed_executor_run(
    store: SqliteConsoleStore, *, issue_number: int, executor: str = "claude"
) -> None:
    """写入一条失败运行及实现调用事实，供切换执行器动作核验。"""
    started_at = "2026-11-17T10:00:00+00:00"
    finished_at = "2026-11-17T10:05:00+00:00"
    store.append_run(
        RunRecord(
            repo_id="keda-main",
            repo_path="/tmp/repo",
            issue_number=issue_number,
            trigger="console_run",
            agent="codex",
            outcome="failed",
            error_summary="provider rate limit: too many requests",
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=300,
        )
    )
    for event_type, occurred_at, detail in (
        ("invocation_started", started_at, {}),
        ("invocation_finished", "2026-11-17T10:04:00+00:00", {"outcome": "error"}),
    ):
        store.append_invocation_event(
            InvocationEventRecord(
                run_id=f"keda-main#issue-{issue_number}#run-1",
                event_key=f"impl:{event_type}",
                event_type=event_type,
                invocation_id="impl",
                repo_id="keda-main",
                issue_number=issue_number,
                phase="implementation",
                role="implementer",
                agent=executor,
                occurred_at=occurred_at,
                detail_json=json.dumps(detail),
            )
        )
