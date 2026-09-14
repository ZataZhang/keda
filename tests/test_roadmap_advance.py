"""Tests for the roadmap continuous-scheduling advance pass.

The fakes are only ``IRoadmapStore`` / ``IGitHubClient``; the scanner, the
dependency evaluator, the state resolver and the shared selection helper all
run for real, so a regression in any of them shows up here too.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    AutopilotConfig,
    CommandResult,
    RepositoryRunContext,
)
from backend.core.use_cases.roadmap_actions import advance_roadmap_queue
from backend.core.use_cases.run_agent_daemon import run_agent_daemon
from tests.conftest import FakeGitHubClient, FakeProcessRunner, FakeRoadmapStore

REPO_ID = "keda-test"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / builders
# ─────────────────────────────────────────────────────────────────────────────


def write_prd(
    repo_path: Path,
    relative_path: str,
    *,
    title: str = "Test PRD",
    issue_number: int | None = None,
) -> str:
    """Write a minimal PRD file and return its repository-relative path."""
    prd_file_path = repo_path / relative_path
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    issue_line = (
        f"- GitHub Issue: https://github.com/example/repo/issues/{issue_number}"
        if issue_number is not None
        else "- GitHub Issue: (to be created)"
    )
    prd_file_path.write_text(
        f"# PRD: {title}\n\n{issue_line}\n\n"
        "## Acceptance Checklist\n\n- [ ] item one\n- [ ] item two\n",
        encoding="utf-8",
    )
    return relative_path


def build_context(
    repo_path: Path,
    *,
    autopilot_enabled: bool = True,
) -> RepositoryRunContext:
    """Build a repository context with the autopilot gate set explicitly."""
    config = replace(AppConfig(), autopilot=AutopilotConfig(enabled=autopilot_enabled))
    return RepositoryRunContext(
        repo_id=REPO_ID,
        display_name="Keda Test",
        repo_path=repo_path,
        config=config,
    )


def mark_issue_merged(client: FakeGitHubClient, issue_number: int) -> None:
    """Make the fake report the issue as closed with a merged PR."""
    client._issue_states[issue_number] = "CLOSED"
    client._issue_comments[issue_number] = [
        f"<!-- iar:event version=1 phase=draft_pr_created cycle=1 pr_branch=issue-{issue_number} -->"
    ]
    client._merged_prs[f"issue-{issue_number}"] = (
        f"https://github.com/example/repo/pull/{issue_number}"
    )


def git_publish_ready_runner() -> FakeProcessRunner:
    """Process runner that satisfies the PRD publish path of issue creation."""
    return FakeProcessRunner(
        responses={
            ("git", "branch", "--show-current"): CommandResult(
                command=("git", "branch", "--show-current"),
                return_code=0,
                stdout="main",
                stderr="",
            )
        }
    )


def advance(
    *,
    repo_path: Path,
    store: FakeRoadmapStore,
    client: FakeGitHubClient,
    process_runner: FakeProcessRunner | None = None,
    dry_run: bool = False,
):
    """Run one advance pass against the given fakes."""
    return advance_roadmap_queue(
        context=build_context(repo_path),
        github_client=client,
        store=store,
        process_runner=process_runner or FakeProcessRunner(),
        dry_run=dry_run,
    )


# ─────────────────────────────────────────────────────────────────────────────
# rv-1: reconciliation + slot refill
# ─────────────────────────────────────────────────────────────────────────────


def test_merged_prd_closes_running_entry(tmp_path: Path) -> None:
    """A running entry whose PRD merged must be reconciled to completed."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    store = FakeRoadmapStore()
    store.seed(pending_a, "running")
    client = FakeGitHubClient()
    mark_issue_merged(client, 1)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.reconciled_completed == [pending_a]
    assert store.entry_for(pending_a).status == "completed"
    assert store.entry_for(pending_a).finished_at is not None


def test_archived_prd_closes_running_entry(tmp_path: Path) -> None:
    """Moving a PRD to tasks/archive/ also closes its queue entry."""
    repo_path = tmp_path
    archived_a = write_prd(repo_path, "tasks/archive/P1-FEAT-20260101-a.md", issue_number=2)
    store = FakeRoadmapStore()
    store.seed(archived_a, "running")
    client = FakeGitHubClient()

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.reconciled_completed == [archived_a]
    assert store.entry_for(archived_a).status == "completed"


def test_failed_prd_is_parked_with_reason(tmp_path: Path) -> None:
    """A failed PRD parks its entry with an error detail and is never retried."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=3)
    store = FakeRoadmapStore()
    store.seed(pending_a, "running")
    client = FakeGitHubClient()
    client._issue_labels[3] = ("agent/failed",)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.reconciled_failed == [pending_a]
    parked_entry = store.entry_for(pending_a)
    assert parked_entry.status == "failed"
    assert parked_entry.error_detail is not None
    assert "已泊车" in parked_entry.error_detail


def test_merged_prd_frees_slot_and_promotes_next_queued(tmp_path: Path) -> None:
    """One pass must close the finished PRD and promote the next queued PRD."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    pending_b = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=2)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "running")
    store.seed(pending_b, "queued")
    client = FakeGitHubClient()
    mark_issue_merged(client, 1)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.reconciled_completed == [pending_a]
    assert report.free_slots == 1
    assert [item.prd_path for item in report.started] == [pending_b]
    assert store.entry_for(pending_b).status == "running"
    assert client._issue_labels[2] == ("agent/ready",)


def test_failed_prd_frees_slot_for_next_candidate(tmp_path: Path) -> None:
    """Parking a failed PRD must release its slot to the next candidate."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=3)
    pending_b = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=4)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "running")
    store.seed(pending_b, "queued")
    client = FakeGitHubClient()
    client._issue_labels[3] = ("agent/failed",)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert store.entry_for(pending_a).status == "failed"
    assert [item.prd_path for item in report.started] == [pending_b]
    assert store.entry_for(pending_b).status == "running"


# ─────────────────────────────────────────────────────────────────────────────
# rv-1: slot accounting
# ─────────────────────────────────────────────────────────────────────────────


def test_no_slot_when_max_parallel_is_saturated(tmp_path: Path) -> None:
    """With every slot busy, candidates stay queued and nothing is promoted."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    pending_b = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=2)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "running")
    store.seed(pending_b, "queued")
    client = FakeGitHubClient()
    client._issue_labels[1] = ("agent/running",)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.free_slots == 0
    assert report.started == []
    assert store.entry_for(pending_b).status == "queued"
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_blocked_prd_holds_entry_but_consumes_no_slot(tmp_path: Path) -> None:
    """A blocked PRD keeps its running entry yet must not occupy a slot."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=5)
    pending_b = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=6)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "running")
    store.seed(pending_b, "queued")
    client = FakeGitHubClient()
    client._issue_labels[5] = ("agent/blocked",)

    report = advance(repo_path=repo_path, store=store, client=client)

    # Blocked is not RUNNING, so the slot is free and the blocked PRD itself is
    # never re-promoted (its state is not NOT_STARTED).
    assert report.free_slots == 1
    assert store.entry_for(pending_a).status == "running"
    assert pending_a not in [item.prd_path for item in report.started]
    assert [item.prd_path for item in report.started] == [pending_b]


def test_waiting_prd_is_never_touched(tmp_path: Path) -> None:
    """Waiting PRDs (dependency gate) are out of scope for this scheduler."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=7)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "running")
    client = FakeGitHubClient()
    client._issue_labels[7] = ("agent/waiting",)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.reconciled_completed == []
    assert report.reconciled_failed == []
    assert store.entry_for(pending_a).status == "running"
    assert store.write_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# rv-1: ordering + discovery
# ─────────────────────────────────────────────────────────────────────────────


def test_promotion_prefers_higher_priority(tmp_path: Path) -> None:
    """With one slot and two candidates, the P0 PRD runs first."""
    repo_path = tmp_path
    p2_prd = write_prd(repo_path, "tasks/pending/P2-FEAT-20260101-low.md", issue_number=10)
    p0_prd = write_prd(repo_path, "tasks/pending/P0-FEAT-20260101-high.md", issue_number=11)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(p2_prd, "queued")
    store.seed(p0_prd, "queued")
    client = FakeGitHubClient()

    report = advance(repo_path=repo_path, store=store, client=client)

    assert [item.prd_path for item in report.started] == [p0_prd]
    assert report.queued == [p2_prd]


def test_discovery_enqueues_new_pending_prd(tmp_path: Path) -> None:
    """A PRD only present in tasks/pending/ is discovered and enqueued."""
    repo_path = tmp_path
    discovered = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-new.md", issue_number=20)
    store = FakeRoadmapStore(max_parallel=1)
    client = FakeGitHubClient()

    report = advance(repo_path=repo_path, store=store, client=client)

    assert [item.prd_path for item in report.started] == [discovered]
    assert store.entry_for(discovered).status == "running"
    assert client._issue_labels[20] == ("agent/ready",)


def test_discovery_creates_issue_when_prd_has_no_link(tmp_path: Path) -> None:
    """A discovered PRD without an Issue link goes through the creation path."""
    repo_path = tmp_path
    discovered = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-fresh.md")
    store = FakeRoadmapStore(max_parallel=1)
    client = FakeGitHubClient(issue_url="https://github.com/example/repo/issues/77")

    report = advance(
        repo_path=repo_path,
        store=store,
        client=client,
        process_runner=git_publish_ready_runner(),
    )

    assert [item.prd_path for item in report.started] == [discovered]
    assert report.started[0].issue_number == 77
    assert store.entry_for(discovered).status == "running"
    prd_text = (repo_path / discovered).read_text(encoding="utf-8")
    assert "https://github.com/example/repo/issues/77" in prd_text


def test_discovery_respects_dependency_gate(tmp_path: Path) -> None:
    """A PRD whose upstream Issue is still open is not discovered."""
    repo_path = tmp_path
    blocked_prd = write_prd(
        repo_path,
        "tasks/pending/P1-FEAT-20260101-blocked.md",
        issue_number=30,
    )
    (repo_path / blocked_prd).write_text(
        (repo_path / blocked_prd).read_text(encoding="utf-8")
        + "\n## Delivery Dependencies\n\n- Depends on tasks/issues: #99\n",
        encoding="utf-8",
    )
    store = FakeRoadmapStore(max_parallel=1)
    client = FakeGitHubClient()

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.started == []
    assert report.queued == []
    assert store.list_roadmap_queue(repo_id=REPO_ID) == []


# ─────────────────────────────────────────────────────────────────────────────
# rv-3: idempotency / concurrency
# ─────────────────────────────────────────────────────────────────────────────


def test_idempotent_second_pass_writes_nothing(tmp_path: Path) -> None:
    """Re-running the pass on an unchanged state must produce zero writes."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=40)
    store = FakeRoadmapStore(max_parallel=1)
    client = FakeGitHubClient()

    first_report = advance(repo_path=repo_path, store=store, client=client)
    writes_after_first = store.write_count
    label_calls_after_first = sum(
        1 for call in client.calls if call["method"] == "edit_issue_labels"
    )

    second_report = advance(repo_path=repo_path, store=store, client=client)

    assert [item.prd_path for item in first_report.started] == [pending_a]
    assert second_report.started == []
    assert second_report.queued == []
    assert second_report.reconciled_completed == []
    assert store.write_count == writes_after_first
    assert (
        sum(1 for call in client.calls if call["method"] == "edit_issue_labels")
        == label_calls_after_first
    )


def test_running_prd_is_not_promoted_twice(tmp_path: Path) -> None:
    """A PRD already running is never re-promoted, even with free slots."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=41)
    store = FakeRoadmapStore(max_parallel=2)
    store.seed(pending_a, "running")
    client = FakeGitHubClient()
    client._issue_labels[41] = ("agent/running",)

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.started == []
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_parked_prd_is_not_rediscovered(tmp_path: Path) -> None:
    """A parked PRD stays parked even once its failed label is cleared.

    This pins the "never re-add a PRD that already has a queue entry" half of
    the discovery rule — without it, clearing ``agent/failed`` would silently
    re-launch a PRD the operator deliberately parked.
    """
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=50)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(pending_a, "failed")
    client = FakeGitHubClient()

    report = advance(repo_path=repo_path, store=store, client=client)

    assert report.started == []
    assert report.queued == []
    assert store.entry_for(pending_a).status == "failed"
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_dry_run_has_zero_side_effects(tmp_path: Path) -> None:
    """Dry-run reports the plan but writes nothing anywhere."""
    repo_path = tmp_path
    merged_prd = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    fresh_prd = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-b.md", issue_number=2)
    store = FakeRoadmapStore(max_parallel=1)
    store.seed(merged_prd, "running")
    client = FakeGitHubClient()
    mark_issue_merged(client, 1)

    report = advance(repo_path=repo_path, store=store, client=client, dry_run=True)

    assert report.dry_run is True
    assert report.reconciled_completed == [merged_prd]
    assert [item.prd_path for item in report.started] == [fresh_prd]
    # Nothing moved: the queue, the settings and the Issue labels are untouched.
    assert store.entry_for(merged_prd).status == "running"
    assert store.write_count == 0
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]
    assert not [call for call in client.calls if call["method"] == "create_issue"]


# ─────────────────────────────────────────────────────────────────────────────
# rv-5: autopilot gate in the daemon
# ─────────────────────────────────────────────────────────────────────────────


def _run_single_daemon_pass(
    monkeypatch: pytest.MonkeyPatch,
    *,
    context: RepositoryRunContext,
    store: FakeRoadmapStore,
    client: FakeGitHubClient,
) -> None:
    """Drive exactly one daemon pass with every phase stubbed out."""
    import backend.core.use_cases.run_agent_daemon as daemon_module

    monkeypatch.setattr(daemon_module, "process_prd_rework_issues", lambda **kwargs: None)
    monkeypatch.setattr(daemon_module, "run_once", lambda **kwargs: None)

    def stop_after_one_pass(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_module.time, "sleep", stop_after_one_pass)

    with pytest.raises(KeyboardInterrupt):
        run_agent_daemon(
            contexts=[context],
            interval=0,
            agent="auto",
            max_issues=1,
            process_runner=FakeProcessRunner(),
            github_client_factory=lambda repo_path: client,
            roadmap_store_factory=lambda: store,
        )


def test_gate_disabled_skips_scheduling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-fast-lane repositories must see zero roadmap store calls."""
    repo_path = tmp_path
    write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    store = FakeRoadmapStore()
    client = FakeGitHubClient()
    context = build_context(repo_path, autopilot_enabled=False)

    _run_single_daemon_pass(monkeypatch, context=context, store=store, client=client)

    assert store.enqueue_calls == []
    assert store.update_calls == []
    assert store.clear_calls == 0
    assert not [call for call in client.calls if call["method"] == "edit_issue_labels"]


def test_gate_enabled_runs_scheduling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast-lane repositories advance the roadmap on the very first pass."""
    repo_path = tmp_path
    pending_a = write_prd(repo_path, "tasks/pending/P1-FEAT-20260101-a.md", issue_number=1)
    store = FakeRoadmapStore(max_parallel=1)
    client = FakeGitHubClient()
    context = build_context(repo_path, autopilot_enabled=True)

    _run_single_daemon_pass(monkeypatch, context=context, store=store, client=client)

    assert [item.prd_path for item in store.enqueue_calls] == [pending_a]
    assert client._issue_labels[1] == ("agent/ready",)
