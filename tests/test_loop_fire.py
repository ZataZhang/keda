"""End-to-end tests for ``fire_loop`` against an in-memory state store and
a fake GitHub client.

These tests exercise the full happy-path fire:

1. Render recipe + pre_command output → PRD text.
2. Write PRD into ``tasks/pending/`` under a timestamped filename.
3. Reuse :func:`create_issue_from_prd` to create a GitHub Issue.
4. Apply ``loop/<id>`` + extra recipe labels via ``edit_issue_labels``.
5. Persist updated ``last_fire_at`` / ``next_fire_at`` / ``fire_count``.

Dedup behaviour and dry-run mode are also exercised here so that the
slightly-more-orchestrated scenarios live next to the happy path.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult, IssueSummary
from backend.core.shared.models.loop import (
    LoopSchedule,
    LoopScheduleKind,
    LoopTask,
    LoopFireStatus,
)
from backend.core.use_cases.loop_fire import fire_loop
from backend.engines.agent_runner.persistence.loop_state_json import (
    JsonLoopStateStore,
)
from backend.engines.agent_runner.scheduler.loop_clock import FixedClock
from tests.conftest import FakeGitHubClient, FakeProcessRunner


# ---------------------------------------------------------------------------
# Recipe / task fixtures
# ---------------------------------------------------------------------------


_RECIPE_BODY = """---
id: github-trending
schedule: "0 8 * * *"
repo_id: fire-demo
priority: P2
issue_type: feature
labels:
  - area/discovery
---

# PRD: GitHub Trending digest for {{date}}

Body with loop_id={{loop_id}} repo={{repo_id}}.

## Acceptance Checklist

- [ ] Fetch trending list.
"""


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    """Initialise a minimal git repo so ``publish_prd`` can be exercised."""
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture
def recipe_path(tmp_path: Path) -> Path:
    path = tmp_path / "github-trending.md"
    path.write_text(_RECIPE_BODY, encoding="utf-8")
    return path


@pytest.fixture
def task(recipe_path: Path) -> LoopTask:
    return LoopTask(
        id="github-trending",
        recipe_path=recipe_path,
        repo_id="fire-demo",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 8 * * *"),
        publish_prd=False,
        queue_ready=True,
    )


# ---------------------------------------------------------------------------
# Happy-path fire
# ---------------------------------------------------------------------------


def test_fire_loop_writes_prd_and_creates_issue(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """A real fire writes the PRD file, creates an Issue, and updates state."""
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/101")
    fake_github.set_list_issues_by_label_result([])  # no duplicate today

    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    clock = FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc))
    process_runner = FakeProcessRunner()

    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=process_runner,
        state_store=state_store,
        clock=clock,
        content_generator=None,
        dry_run=False,
    )

    assert result.status is LoopFireStatus.FIRED
    assert result.issue_url == "https://github.com/example/fire-demo/issues/101"
    assert result.issue_number == 101

    # The PRD was written into tasks/pending/ with the timestamped filename.
    relative_prd = result.relative_prd_path
    assert relative_prd is not None
    prd_on_disk = repo_path / relative_prd
    assert prd_on_disk.is_file()
    prd_text = prd_on_disk.read_text(encoding="utf-8")
    assert "GitHub Trending digest for 2026-06-23" in prd_text
    assert "loop_id=github-trending" in prd_text
    assert "repo=fire-demo" in prd_text

    # The Issue was created with the standard runner labels.
    create_calls = [c for c in fake_github.calls if c["method"] == "create_issue"]
    assert len(create_calls) == 1
    create_call = create_calls[0]
    assert "type/feature" in create_call["labels"]
    assert "agent/ready" in create_call["labels"]

    # Loop-specific labels ride along in the create call itself: a follow-up edit
    # that failed would leave an Issue the dedup / wait-for-close rules can't see.
    assert "loop/github-trending" in create_call["labels"]
    assert "area/discovery" in create_call["labels"]
    assert not [c for c in fake_github.calls if c["method"] == "edit_issue_labels"]

    # The state was persisted with updated counters / next fire.
    state_store.load()
    persisted = state_store.get_task("github-trending")
    assert persisted is not None
    assert persisted.fire_count == 1
    assert persisted.last_fire_at is not None
    assert persisted.next_fire_at is not None
    # next fire should be after the fire time
    assert persisted.next_fire_at > persisted.last_fire_at


def test_fire_loop_dry_run_writes_nothing(task: LoopTask, repo_path: Path, tmp_path: Path) -> None:
    """``dry_run=True`` renders but never touches disk or GitHub."""
    fake_github = FakeGitHubClient()
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    clock = FixedClock(datetime(2026, 6, 23, 7, 30, tzinfo=timezone.utc))

    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=clock,
        dry_run=True,
    )

    assert result.status is LoopFireStatus.DRY_RUN
    assert result.skipped_reason and "Would render PRD" in result.skipped_reason
    create_calls = [c for c in fake_github.calls if c["method"] == "create_issue"]
    assert create_calls == []
    assert not (repo_path / "tasks" / "pending").exists()
    state_store.load()
    assert state_store.get_task("github-trending") is None


def test_fire_loop_skips_when_duplicate_exists(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """An existing open Issue for today on ``loop/<id>`` aborts creation."""
    fake_github = FakeGitHubClient()
    fake_github.set_list_issues_by_label_result(
        [
            IssueSummary(
                number=42,
                title="GitHub Trending digest for 2026-06-23",
                url="https://example/issues/42",
                body="",
                labels=("loop/github-trending",),
            )
        ]
    )
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    clock = FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc))

    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=clock,
    )

    assert result.status is LoopFireStatus.SKIPPED_DUPLICATE
    assert "skipped creation" in (result.skipped_reason or "")
    create_calls = [c for c in fake_github.calls if c["method"] == "create_issue"]
    assert create_calls == []
    # State still records the skip so the daemon doesn't immediately re-fire.
    state_store.load()
    persisted = state_store.get_task("github-trending")
    assert persisted is not None
    assert persisted.last_fire_at is not None


def test_agent_daemon_loop_skips_when_previous_issue_is_active(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """上一轮 Issue 活动时，agent-daemon loop 保留原到期槽位。"""
    active_task = replace(task, managed_by="agent-daemon", next_fire_at="2026-06-23T08:00:00+00:00")
    fake_github = FakeGitHubClient()
    fake_github.set_list_issues_by_label_result(
        [
            IssueSummary(
                number=77,
                title="Nightly task",
                url="https://example/issues/77",
                body="",
                labels=("loop/github-trending", "agent/running"),
            )
        ]
    )
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")

    result = fire_loop(
        active_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.SKIPPED_ACTIVE
    assert result.next_fire_at == active_task.next_fire_at
    assert not (repo_path / "tasks" / "pending").exists()
    assert not [call for call in fake_github.calls if call["method"] == "create_issue"]


def test_agent_daemon_loop_renders_local_time_and_persists_utc(
    repo_path: Path, tmp_path: Path
) -> None:
    """recipe 时区控制人读文本，状态字段仍保持 UTC。"""
    recipe_path = tmp_path / "nightly.md"
    recipe_path.write_text(
        """---
id: nightly
schedule: "0 1 * * *"
repo_id: fire-demo
managed_by: agent-daemon
timezone: Asia/Shanghai
publish_prd: false
---

# Nightly {{date}}
""",
        encoding="utf-8",
    )
    task = LoopTask(
        id="nightly",
        recipe_path=recipe_path,
        repo_id="fire-demo",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 1 * * *"),
        managed_by="agent-daemon",
        timezone_name="Asia/Shanghai",
        publish_prd=False,
    )
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/102")
    fake_github.set_list_issues_by_label_result([])
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")

    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 16, 30, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED
    assert result.relative_prd_path is not None
    assert "20260624-003000" in result.relative_prd_path.name
    assert "Nightly 2026-06-24" in (repo_path / result.relative_prd_path).read_text(
        encoding="utf-8"
    )
    state_store.load()
    persisted = state_store.get_task("nightly")
    assert persisted is not None
    assert persisted.last_fire_at == "2026-06-23T16:30:00+00:00"
    assert persisted.next_fire_at == "2026-06-23T17:00:00+00:00"


def test_fire_loop_runs_pre_command_for_extra_variables(tmp_path: Path, repo_path: Path) -> None:
    """pre_command ``KEY=value`` stdout is injected into the template."""
    recipe_path = tmp_path / "github-trending.md"
    recipe_path.write_text(
        """---
id: github-trending
schedule: "0 8 * * *"
repo_id: fire-demo
---

Trending repo: {{trending_repo}} (count={{count}})
""",
        encoding="utf-8",
    )
    task = LoopTask(
        id="github-trending",
        recipe_path=recipe_path,
        repo_id="fire-demo",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 8 * * *"),
        pre_command="printf 'trending_repo=demo/x\\ncount=7\\n'",
        publish_prd=False,
    )

    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/123")
    fake_github.set_list_issues_by_label_result([])
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    clock = FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc))
    process_runner = FakeProcessRunner(
        responses={
            ("/bin/sh", "-c", task.pre_command): CommandResult(
                command=("/bin/sh", "-c"),
                return_code=0,
                stdout="trending_repo=demo/x\ncount=7\n",
                stderr="",
            )
        }
    )

    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=process_runner,
        state_store=state_store,
        clock=clock,
    )
    assert result.status is LoopFireStatus.FIRED
    prd_text = (repo_path / result.relative_prd_path).read_text(encoding="utf-8")
    assert "Trending repo: demo/x (count=7)" in prd_text


def test_fire_loop_reports_pre_command_failure_behind_undefined_variable(
    tmp_path: Path, repo_path: Path
) -> None:
    """pre_command 失败导致变量缺失时，报错附上它的退出码与 stderr，而不只说"请添加变量"。"""
    recipe_path = tmp_path / "nightly.md"
    recipe_path.write_text(
        """---
id: nightly
schedule: "0 2 * * *"
repo_id: fire-demo
---

CI: {{last_ci_status}}
""",
        encoding="utf-8",
    )
    task = LoopTask(
        id="nightly",
        recipe_path=recipe_path,
        repo_id="fire-demo",
        schedule=LoopSchedule(kind=LoopScheduleKind.CRON, expression="0 2 * * *"),
        pre_command="broken-pre-command",
        publish_prd=False,
    )
    fake_github = FakeGitHubClient()
    fake_github.set_list_issues_by_label_result([])
    process_runner = FakeProcessRunner(
        responses={
            ("/bin/sh", "-c", task.pre_command): CommandResult(
                command=("/bin/sh", "-c"),
                return_code=2,
                stdout="",
                stderr="awk: illegal statement at source line 1",
            )
        }
    )

    with pytest.raises(ValueError) as raised:
        fire_loop(
            task,
            repo_path=repo_path,
            github_client=fake_github,
            process_runner=process_runner,
            state_store=JsonLoopStateStore(tmp_path / "loop-state.json"),
            clock=FixedClock(datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)),
        )

    message = str(raised.value)
    assert "undefined variable 'last_ci_status'" in message
    assert "pre_command exited with 2" in message
    assert "awk: illegal statement" in message


def _skip_when_scene(
    task: LoopTask, tmp_path: Path, ci_status: str
) -> tuple[LoopTask, FakeProcessRunner, JsonLoopStateStore]:
    """构造带 skip_when 与 pre_command 的 loop 场景。"""
    scene_task = replace(
        task,
        pre_command="print-ci",
        skip_when="last_ci_status=success,none",
        next_fire_at="2026-06-23T08:00:00+00:00",
    )
    process_runner = FakeProcessRunner(
        responses={
            ("/bin/sh", "-c", "print-ci"): CommandResult(
                command=("/bin/sh", "-c"),
                return_code=0,
                stdout=f"last_ci_status={ci_status}\n",
                stderr="",
            )
        }
    )
    return scene_task, process_runner, JsonLoopStateStore(tmp_path / "loop-state.json")


def test_skip_when_matched_creates_no_issue_and_advances_schedule(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """CI 正常时不造票，但推进 next_fire_at 并留下原因。"""
    skip_task, process_runner, state_store = _skip_when_scene(task, tmp_path, "success")
    state_store.upsert_task(skip_task)
    fake_github = FakeGitHubClient()

    result = fire_loop(
        skip_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=process_runner,
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.SKIPPED_CONDITION
    assert "last_ci_status=success" in (result.skipped_reason or "")
    assert not [call for call in fake_github.calls if call["method"] == "create_issue"]
    saved_task = state_store.get_task(skip_task.id)
    assert saved_task is not None
    assert saved_task.next_fire_at != skip_task.next_fire_at
    assert saved_task.fire_count == skip_task.fire_count
    assert "last_ci_status=success" in (saved_task.last_skip_reason or "")


def test_skip_when_not_matched_still_fires_and_clears_reason(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """CI 失败（不在跳过列表）照常造票，并清掉上次的未造票原因。"""
    skip_task, process_runner, state_store = _skip_when_scene(task, tmp_path, "failure")
    skip_task = replace(skip_task, last_skip_reason="old reason")
    state_store.upsert_task(skip_task)
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/9")
    fake_github.set_list_issues_by_label_result([])

    result = fire_loop(
        skip_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=process_runner,
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED
    saved_task = state_store.get_task(skip_task.id)
    assert saved_task is not None and saved_task.last_skip_reason is None


def test_skip_when_missing_variable_does_not_skip(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """前置命令没产出该变量时 fail-open：照常触发，不悄悄吞掉。"""
    skip_task, _process_runner, state_store = _skip_when_scene(task, tmp_path, "success")
    skip_task = replace(skip_task, skip_when="other_var=x")
    state_store.upsert_task(skip_task)
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/9")
    fake_github.set_list_issues_by_label_result([])
    _, process_runner, _ = _skip_when_scene(task, tmp_path, "success")

    result = fire_loop(
        skip_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=process_runner,
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED


def test_skip_when_dry_run_reports_without_mutating(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """dry-run 只预告会跳过，不写状态。"""
    skip_task, process_runner, state_store = _skip_when_scene(task, tmp_path, "none")
    state_store.upsert_task(skip_task)

    result = fire_loop(
        skip_task,
        repo_path=repo_path,
        github_client=FakeGitHubClient(),
        process_runner=process_runner,
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
        dry_run=True,
    )

    assert result.status is LoopFireStatus.DRY_RUN
    assert (result.skipped_reason or "").startswith("Would skip")
    saved_task = state_store.get_task(skip_task.id)
    assert saved_task is not None and saved_task.last_skip_reason is None


def test_active_pause_persists_visible_reason(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """被活动 Issue 暂停时把原因写进状态，kc loop list 才看得见。"""
    active_task = replace(task, managed_by="agent-daemon", next_fire_at="2026-06-23T08:00:00+00:00")
    fake_github = FakeGitHubClient()
    fake_github.set_list_issues_by_label_result(
        [
            IssueSummary(
                number=77,
                title="Nightly task",
                url="https://example/issues/77",
                body="",
                labels=("loop/github-trending", "agent/failed"),
            )
        ]
    )
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    state_store.upsert_task(active_task)

    fire_loop(
        active_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    saved_task = state_store.get_task(active_task.id)
    assert saved_task is not None
    assert "#77" in (saved_task.last_skip_reason or "")
    assert saved_task.next_fire_at == active_task.next_fire_at


@pytest.mark.parametrize(
    "previous_labels",
    [
        ("loop/github-trending", "agent/automated-review-passed", "human/awaiting-review"),
        ("loop/github-trending", "agent/review"),
        ("loop/github-trending",),
    ],
)
def test_agent_daemon_loop_waits_for_previous_issue_to_close(
    task: LoopTask, repo_path: Path, tmp_path: Path, previous_labels: tuple[str, ...]
) -> None:
    """上一轮 Issue 只要还开着（含“PR 已建、等人工审核”），就不触发下一轮。"""
    waiting_task = replace(
        task, managed_by="agent-daemon", next_fire_at="2026-06-23T08:00:00+00:00"
    )
    fake_github = FakeGitHubClient()
    fake_github.set_list_issues_by_label_result(
        [
            IssueSummary(
                number=288,
                title="Nightly task",
                url="https://example/issues/288",
                body="",
                labels=previous_labels,
            )
        ]
    )
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    state_store.upsert_task(waiting_task)

    result = fire_loop(
        waiting_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.SKIPPED_ACTIVE
    assert "#288" in (result.skipped_reason or "")
    assert "closed" in (result.skipped_reason or "")
    assert result.next_fire_at == waiting_task.next_fire_at
    assert not [call for call in fake_github.calls if call["method"] == "create_issue"]


def test_agent_daemon_loop_fires_once_previous_issue_is_closed(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """上一轮 Issue 已关闭（开放列表为空）时才放行，并保留原到期槽位补发一次。"""
    waiting_task = replace(
        task, managed_by="agent-daemon", next_fire_at="2026-06-23T08:00:00+00:00"
    )
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/300")
    fake_github.set_list_issues_by_label_result([])
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")
    state_store.upsert_task(waiting_task)

    result = fire_loop(
        waiting_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED


# --- output: issue（不带 PRD 的普通 Issue）-----------------------------------


def test_issue_output_creates_plain_issue_without_prd_file(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """output=issue：不写 PRD 文件、不走 PRD 链接回写，配方正文原文进 Issue 正文。"""
    issue_task = replace(task, output="issue")
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/55")
    fake_github.set_list_issues_by_label_result([])
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")

    result = fire_loop(
        issue_task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED
    assert result.issue_number == 55
    assert result.prd_path is None and result.relative_prd_path is None
    assert not (repo_path / "tasks").exists()  # 没有任何 PRD 文件
    created_issue_calls = [call for call in fake_github.calls if call["method"] == "create_issue"]
    assert len(created_issue_calls) == 1
    issue_body = str(created_issue_calls[0]["body"])
    assert "Fetch trending list." in issue_body  # 配方正文是完整的，不只是开头一段
    assert "PRD path:" not in issue_body  # 不带 PRD 锚点


def test_issue_output_dry_run_says_no_prd_file(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    issue_task = replace(task, output="issue")
    result = fire_loop(
        issue_task,
        repo_path=repo_path,
        github_client=FakeGitHubClient(),
        process_runner=FakeProcessRunner(),
        state_store=JsonLoopStateStore(tmp_path / "loop-state.json"),
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
        dry_run=True,
    )
    assert result.status is LoopFireStatus.DRY_RUN
    assert result.relative_prd_path is None
    assert "plain Issue" in (result.skipped_reason or "")
    assert not (repo_path / "tasks").exists()


def test_default_output_is_prd_and_still_writes_the_prd_file(
    task: LoopTask, repo_path: Path, tmp_path: Path
) -> None:
    """兼容性：不声明 output 的 loop 行为不变，仍然生成 PRD 文件。"""
    assert task.output == "prd"
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/56")
    fake_github.set_list_issues_by_label_result([])
    result = fire_loop(
        task,
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=JsonLoopStateStore(tmp_path / "loop-state.json"),
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )
    assert result.relative_prd_path is not None
    assert (repo_path / result.relative_prd_path).is_file()


def test_output_survives_state_store_round_trip(task: LoopTask, tmp_path: Path) -> None:
    state_path = tmp_path / "loop-state.json"
    JsonLoopStateStore(state_path).upsert_task(replace(task, output="issue"))
    reloaded_task = JsonLoopStateStore(state_path).get_task(task.id)
    assert reloaded_task is not None and reloaded_task.output == "issue"


def test_legacy_state_entry_without_output_keeps_running_as_prd(tmp_path: Path) -> None:
    """已登记的旧 loop（状态文件里没有 output 字段）不受新默认值影响，仍按 prd 运行。"""
    import json

    state_path = tmp_path / "loop-state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "id": "legacy-loop",
                        "recipe_path": str(tmp_path / "legacy.md"),
                        "repo_id": "r",
                        "schedule": {"kind": "cron", "expression": "0 1 * * *"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    legacy_task = JsonLoopStateStore(state_path).get_task("legacy-loop")
    assert legacy_task is not None and legacy_task.output == "prd"


@pytest.mark.parametrize("output_mode", ["prd", "issue"])
def test_loop_labels_are_sent_with_the_create_call_not_a_follow_up_edit(
    task: LoopTask, repo_path: Path, tmp_path: Path, output_mode: str
) -> None:
    """``loop/<id>`` 必须随创建调用一起落下。

    以前是“先建 Issue、再补标签”：补标签一失败，Issue 已存在却没有 ``loop/<id>``，
    fire 抛错、状态没推进，去重与“上一个未关闭则暂停”都认不出它，下次触发就重复建一个。
    """
    fake_github = FakeGitHubClient(issue_url="https://github.com/example/fire-demo/issues/9")
    fake_github.set_list_issues_by_label_result([])

    def _label_edit_always_fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated label edit failure")

    fake_github.edit_issue_labels = _label_edit_always_fails  # type: ignore[method-assign]
    state_store = JsonLoopStateStore(tmp_path / "loop-state.json")

    result = fire_loop(
        replace(task, output=output_mode),
        repo_path=repo_path,
        github_client=fake_github,
        process_runner=FakeProcessRunner(),
        state_store=state_store,
        clock=FixedClock(datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)),
    )

    assert result.status is LoopFireStatus.FIRED
    created_issue_calls = [call for call in fake_github.calls if call["method"] == "create_issue"]
    assert len(created_issue_calls) == 1
    assert "loop/github-trending" in created_issue_calls[0]["labels"]
    assert "area/discovery" in created_issue_calls[0]["labels"]
    state_store.load()
    persisted = state_store.get_task("github-trending")
    assert persisted is not None and persisted.fire_count == 1
