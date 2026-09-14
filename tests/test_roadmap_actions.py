"""Regression tests for ``start_global_roadmap`` after the selection-helper extraction.

The console's one-shot batch start and the daemon's continuous scheduler now
share :func:`backend.core.use_cases.roadmap_actions._select_eligible_prds`.
These tests pin the behaviour the console had *before* the extraction so the
refactor provably changed nothing: same eligibility filter, same P0-first
ordering, same queue overflow handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.runner_console import (
    IRunnerProcessSupervisor,
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.shared.models.roadmap import RoadmapPrdState
from backend.core.use_cases.roadmap_actions import start_global_roadmap
from tests.conftest import FakeGitHubClient, FakeProcessRunner, FakeRoadmapStore

REPO_ID = "keda-test"


@dataclass(frozen=True)
class _FakeSupervisor(IRunnerProcessSupervisor):
    """Supervisor that records spawns instead of launching processes."""

    spawns: list[str]

    def spawn(
        self,
        *,
        repo_id: str,
        kind: RunnerProcessKind,
        argv,
        cwd: Path,
    ) -> RunnerProcessRecord:
        self.spawns.append(repo_id)
        return RunnerProcessRecord(
            process_id="fake",
            repo_id=repo_id,
            kind=kind,
            pid=1,
            status="running",
            exit_code=None,
            log_path="",
            command=tuple(argv),
            started_at="",
            stopped_at=None,
        )

    def list_processes(self) -> list[RunnerProcessRecord]:
        return []

    def list_unmanaged_processes(self, registry_entries) -> list[RunnerProcessRecord]:
        return []

    def get_process(self, process_id: str) -> RunnerProcessRecord | None:
        return None

    def stop(self, process_id: str, *, timeout_seconds: int) -> RunnerProcessRecord:
        raise KeyError(process_id)

    def read_log(self, process_id: str, *, offset: int, max_bytes: int):
        raise KeyError(process_id)


def _write_prd(repo_path: Path, relative_path: str, *, issue_number: int | None = None) -> str:
    """Write a minimal pending PRD and return its repository-relative path."""
    prd_file_path = repo_path / relative_path
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    issue_line = (
        f"- GitHub Issue: https://github.com/example/repo/issues/{issue_number}"
        if issue_number is not None
        else "- GitHub Issue: (to be created)"
    )
    prd_file_path.write_text(
        f"# PRD: {prd_file_path.stem}\n\n{issue_line}\n\n"
        "## Acceptance Checklist\n\n- [ ] item one\n",
        encoding="utf-8",
    )
    return relative_path


def _start_global(
    repo_path: Path,
    *,
    max_parallel: int,
    store: FakeRoadmapStore,
    client: FakeGitHubClient,
    supervisor: _FakeSupervisor,
):
    context = RepositoryRunContext(
        repo_id=REPO_ID,
        display_name="Keda Test",
        repo_path=repo_path,
        config=AppConfig(),
    )
    return start_global_roadmap(
        repo_id=REPO_ID,
        max_parallel=max_parallel,
        contexts=[context],
        github_client_factory=lambda path: client,
        supervisor=supervisor,
        store=store,
        runner_command=("echo",),
        spawn_cwd=repo_path,
        process_runner=FakeProcessRunner(),
    )


def test_global_start_orders_by_priority(tmp_path: Path) -> None:
    """P0 PRDs must be started before P2 PRDs, as before the extraction."""
    p2_prd = _write_prd(tmp_path, "tasks/pending/P2-FEAT-20260101-low.md", issue_number=1)
    p0_prd = _write_prd(tmp_path, "tasks/pending/P0-FEAT-20260101-high.md", issue_number=2)
    store = FakeRoadmapStore(repo_id=REPO_ID)
    supervisor = _FakeSupervisor(spawns=[])

    result = _start_global(
        tmp_path, max_parallel=1, store=store, client=FakeGitHubClient(), supervisor=supervisor
    )

    assert [item.prd_path for item in result.started] == [p0_prd]
    assert result.queued == [p2_prd]
    assert result.started[0].state is RoadmapPrdState.READY
    assert supervisor.spawns == [REPO_ID]


def test_global_start_queues_overflow(tmp_path: Path) -> None:
    """Candidates beyond max_parallel are enqueued, not started."""
    first_prd = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    second_prd = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=2)
    store = FakeRoadmapStore(repo_id=REPO_ID)
    supervisor = _FakeSupervisor(spawns=[])

    result = _start_global(
        tmp_path, max_parallel=1, store=store, client=FakeGitHubClient(), supervisor=supervisor
    )

    assert len(result.started) == 1
    assert len(result.queued) == 1
    assert result.started[0].prd_path in (first_prd, second_prd)
    assert result.queued[0] in (first_prd, second_prd)
    assert result.started[0].prd_path != result.queued[0]
    queued_statuses = [entry.status for entry in store.list_roadmap_queue(repo_id=REPO_ID)]
    assert queued_statuses.count("running") == 1
    assert queued_statuses.count("queued") == 1


def test_global_start_skips_running_and_blocked(tmp_path: Path) -> None:
    """Running / blocked / merged PRDs are never started by the global start."""
    running_prd = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20260101-running.md", issue_number=1)
    blocked_prd = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20260101-blocked.md", issue_number=2)
    free_prd = _write_prd(tmp_path, "tasks/pending/P1-FEAT-20260101-free.md", issue_number=3)
    (tmp_path / blocked_prd).write_text(
        (tmp_path / blocked_prd).read_text(encoding="utf-8")
        + "\n## Delivery Dependencies\n\n- Depends on tasks/issues: #99\n",
        encoding="utf-8",
    )
    client = FakeGitHubClient()
    client._issue_labels[1] = ("agent/running",)
    store = FakeRoadmapStore(repo_id=REPO_ID)
    supervisor = _FakeSupervisor(spawns=[])

    result = _start_global(
        tmp_path, max_parallel=3, store=store, client=client, supervisor=supervisor
    )

    assert [item.prd_path for item in result.started] == [free_prd]
    assert running_prd not in result.queued
    assert blocked_prd not in result.queued
