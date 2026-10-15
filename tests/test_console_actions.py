"""Tests for console whitelisted actions and audit logging."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.core.shared.interfaces.runner_console import RunnerProcessKind
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    PublishRecoveryResult,
    RepositoryRunContext,
)
from backend.core.use_cases import agent_runner_factory as runner_factory
from backend.core.use_cases import console_actions
from backend.core.use_cases.console_actions import (
    ConsoleActionError,
    execute_issue_action,
    execute_repository_action,
)
from backend.core.use_cases.recover_publish import PublishRecoveryError
from backend.infrastructure.console.process_supervisor import (
    PidfileProcessSupervisor,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeProcessRunner


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


def _make_context(repo_id: str = "keda-main") -> RepositoryRunContext:
    return RepositoryRunContext(
        repo_id=repo_id,
        display_name=repo_id,
        repo_path=Path("/tmp/repo"),
        config=AppConfig(),
    )


def _make_store(tmp_path: Path) -> SqliteConsoleStore:
    return SqliteConsoleStore(tmp_path / "console.db")


def _make_supervisor(tmp_path: Path) -> PidfileProcessSupervisor:
    return PidfileProcessSupervisor(
        registry_path=tmp_path / "processes.json",
        log_dir=tmp_path / "logs",
    )


def _fake_runner_command() -> list[str]:
    return [sys.executable, "-c", "print('fake runner')"]


def test_retry_failed_flips_labels_and_audits(tmp_path: Path) -> None:
    """retry_failed must flip failed -> ready and audit as accepted."""
    config = AppConfig()
    issue = IssueSummary(
        number=19,
        title="Broken",
        url="https://example.test/19",
        body="",
        labels=(config.labels.failed,),
    )
    github_client = FakeGitHubClient(issue)
    store = _make_store(tmp_path)

    action_result = execute_issue_action(
        action="retry_failed",
        repo_id="keda-main",
        issue_number=19,
        contexts=[_make_context()],
        github_client_factory=lambda _path: github_client,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
    )

    assert action_result.result == "accepted"
    assert github_client.label_edits == [(19, (config.labels.ready,), (config.labels.failed,))]
    audits = store.list_recent_audits(limit=10)
    assert audits[0].action == "retry_failed"
    assert audits[0].result == "accepted"


def test_retry_failed_rejected_when_not_failed(tmp_path: Path) -> None:
    """retry_failed on a non-failed Issue must be rejected and audited."""
    issue = IssueSummary(
        number=20,
        title="Fine",
        url="https://example.test/20",
        body="",
        labels=("agent/automated-review-passed",),
    )
    store = _make_store(tmp_path)
    with pytest.raises(ConsoleActionError):
        execute_issue_action(
            action="retry_failed",
            repo_id="keda-main",
            issue_number=20,
            contexts=[_make_context()],
            github_client_factory=lambda _path: FakeGitHubClient(issue),
            supervisor=_make_supervisor(tmp_path),
            store=store,
            runner_command=_fake_runner_command(),
            spawn_cwd=tmp_path,
            process_runner=FakeProcessRunner(),
        )
    audits = store.list_recent_audits(limit=10)
    assert audits[0].result == "rejected"


def test_unknown_action_rejected_and_audited(tmp_path: Path) -> None:
    """Unknown actions must be rejected (whitelist) and audited."""
    store = _make_store(tmp_path)
    with pytest.raises(ConsoleActionError, match="Unknown issue action"):
        execute_issue_action(
            action="rm_rf_slash",
            repo_id="keda-main",
            issue_number=1,
            contexts=[_make_context()],
            github_client_factory=lambda _path: FakeGitHubClient(
                IssueSummary(number=1, title="", url="", body="", labels=())
            ),
            supervisor=_make_supervisor(tmp_path),
            store=store,
            runner_command=_fake_runner_command(),
            spawn_cwd=tmp_path,
            process_runner=FakeProcessRunner(),
        )
    audits = store.list_recent_audits(limit=10)
    assert audits[0].action == "rm_rf_slash"
    assert audits[0].result == "rejected"


def test_repository_run_once_spawns_process(tmp_path: Path) -> None:
    """run_once action must spawn a one-shot process and audit it."""
    store = _make_store(tmp_path)
    supervisor = _make_supervisor(tmp_path)
    action_result = execute_repository_action(
        action="run_once",
        repo_id="keda-main",
        contexts=[_make_context()],
        supervisor=supervisor,
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
    )
    assert action_result.result == "accepted"
    assert action_result.process is not None
    audits = store.list_recent_audits(limit=10)
    assert audits[0].action == "run_once"
    assert audits[0].result == "accepted"


def test_repository_unknown_action_rejected(tmp_path: Path) -> None:
    """Unknown repository actions must be rejected before any spawn."""
    store = _make_store(tmp_path)
    with pytest.raises(ConsoleActionError, match="Unknown repository action"):
        execute_repository_action(
            action="deploy_to_prod",
            repo_id="keda-main",
            contexts=[_make_context()],
            supervisor=_make_supervisor(tmp_path),
            store=store,
            runner_command=_fake_runner_command(),
            spawn_cwd=tmp_path,
        )
    audits = store.list_recent_audits(limit=10)
    assert audits[0].result == "rejected"


def test_blocked_continue_spawns_process_with_issue(tmp_path: Path) -> None:
    """blocked_continue must spawn a one-shot process bound to the issue."""
    store = _make_store(tmp_path)
    supervisor = _make_supervisor(tmp_path)
    action_result = execute_issue_action(
        action="blocked_continue",
        repo_id="keda-main",
        issue_number=42,
        contexts=[_make_context()],
        github_client_factory=lambda _path: FakeGitHubClient(
            IssueSummary(number=42, title="", url="", body="", labels=())
        ),
        supervisor=supervisor,
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
    )
    assert action_result.result == "accepted"
    assert action_result.process is not None
    assert str(action_result.process.kind) == "blocked_continue"


def test_repository_review_once_spawns_process(tmp_path: Path) -> None:
    """FR-1 复核一轮：review_once 启动一次性托管进程并审计。"""
    store = _make_store(tmp_path)
    action_result = execute_repository_action(
        action="review_once",
        repo_id="keda-main",
        contexts=[_make_context()],
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
    )
    assert action_result.result == "accepted"
    assert action_result.process is not None
    assert str(action_result.process.kind) == RunnerProcessKind.REVIEW_ONCE.value
    audits = store.list_recent_audits(limit=10)
    assert audits[0].action == "review_once"
    assert audits[0].result == "accepted"


def test_recover_failed_publish_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-6 恢复发布：复用 kc recover 用例成功，返回 PR 地址且不启动托管进程。"""
    monkeypatch.setattr(runner_factory, "create_content_generator", lambda *a, **k: None)

    def _fake_recover(**kwargs):
        return PublishRecoveryResult(
            issue_number=55,
            branch="agent/issue-55",
            head_sha="deadbeef",
            pr_url="https://github.com/example/repo/pull/9",
            pr_reused=True,
        )

    monkeypatch.setattr(console_actions, "recover_publish_issue", _fake_recover)
    store = _make_store(tmp_path)

    action_result = execute_issue_action(
        action="recover_failed_publish",
        repo_id="keda-main",
        issue_number=55,
        contexts=[_make_context()],
        github_client_factory=lambda _path: FakeGitHubClient(
            IssueSummary(number=55, title="", url="", body="", labels=("agent/failed",))
        ),
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
    )

    assert action_result.result == "accepted"
    assert action_result.process is None
    assert "https://github.com/example/repo/pull/9" in action_result.detail
    audits = store.list_recent_audits(limit=10)
    assert audits[0].action == "recover_failed_publish"
    assert audits[0].result == "accepted"


def test_recover_failed_publish_error_maps_to_console_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-6 不可恢复时把 PublishRecoveryError 翻成明确的 ConsoleActionError。"""
    monkeypatch.setattr(runner_factory, "create_content_generator", lambda *a, **k: None)

    def _fake_recover(**kwargs):
        raise PublishRecoveryError("no branch", failure_category="detached_head")

    monkeypatch.setattr(console_actions, "recover_publish_issue", _fake_recover)
    store = _make_store(tmp_path)

    with pytest.raises(ConsoleActionError, match="detached_head"):
        execute_issue_action(
            action="recover_failed_publish",
            repo_id="keda-main",
            issue_number=56,
            contexts=[_make_context()],
            github_client_factory=lambda _path: FakeGitHubClient(
                IssueSummary(number=56, title="", url="", body="", labels=())
            ),
            supervisor=_make_supervisor(tmp_path),
            store=store,
            runner_command=_fake_runner_command(),
            spawn_cwd=tmp_path,
            process_runner=FakeProcessRunner(),
        )


def _register_running_run(store: SqliteConsoleStore, *, issue_number: int = 42) -> None:
    from backend.core.shared.interfaces.issue_run_control import (
        PHASE_VERIFIER,
        STATE_RUNNING,
        IssueRunControlRecord,
    )

    store.register_issue_run(
        IssueRunControlRecord(
            repo_id="keda-main",
            issue_number=issue_number,
            run_id="run-1",
            head_sha="abc123",
            phase=PHASE_VERIFIER,
            state=STATE_RUNNING,
            autopilot_hold=False,
            started_at="2026-10-12T00:00:00+00:00",
            pr_number=58,
        )
    )


def test_take_over_verifier_requires_run_id_and_head_sha(tmp_path: Path) -> None:
    """缺少运行身份时接管必须被拒绝并审计，绝不写入控制状态。"""
    store = _make_store(tmp_path)
    with pytest.raises(ConsoleActionError, match="requires run_id and head_sha"):
        execute_issue_action(
            action="take_over_verifier",
            repo_id="keda-main",
            issue_number=42,
            contexts=[_make_context()],
            github_client_factory=lambda _path: None,
            supervisor=_make_supervisor(tmp_path),
            store=store,
            runner_command=_fake_runner_command(),
            spawn_cwd=tmp_path,
            process_runner=FakeProcessRunner(),
        )
    audits = store.list_recent_audits(limit=5)
    assert audits[0].result == "rejected"


def test_take_over_verifier_accepted_returns_202_and_audits(tmp_path: Path, monkeypatch) -> None:
    """合法接管请求把 running 翻到 takeover_requested，返回 202（进程尚未确认停止）。"""
    from backend.core.use_cases import agent_runner_issue_control as issue_control

    store = _make_store(tmp_path)
    _register_running_run(store)
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: store)

    action_result = execute_issue_action(
        action="take_over_verifier",
        repo_id="keda-main",
        issue_number=42,
        contexts=[_make_context()],
        github_client_factory=lambda _path: None,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        run_id="run-1",
        head_sha="abc123",
    )

    assert action_result.takeover_http_status == 202
    assert action_result.takeover_outcome == "accepted"
    assert action_result.result == "accepted"
    assert store.get_issue_run(repo_id="keda-main", issue_number=42).state == "takeover_requested"


def test_take_over_verifier_stale_head_returns_409_without_write(
    tmp_path: Path, monkeypatch
) -> None:
    """HEAD 漂移时接管返回 409，控制行仍为 running，副作用为零。"""
    from backend.core.use_cases import agent_runner_issue_control as issue_control

    store = _make_store(tmp_path)
    _register_running_run(store)
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: store)

    action_result = execute_issue_action(
        action="take_over_verifier",
        repo_id="keda-main",
        issue_number=42,
        contexts=[_make_context()],
        github_client_factory=lambda _path: None,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        run_id="run-1",
        head_sha="STALE_HEAD",
    )

    assert action_result.takeover_http_status == 409
    assert action_result.result == "rejected"
    assert store.get_issue_run(repo_id="keda-main", issue_number=42).state == "running"


def test_take_over_verifier_without_durable_cancel_intent_audits_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """取消意图没有落盘时，审计里也不许出现 accepted。

    存储层把"CAS 反复空手却仍读到 running"分类成 ``state_change_conflict``（409）之后，
    Console 这一侧必须同步把它当作拒绝：一次 202 + accepted 审计配上没有 request_id、
    没有落闸的控制行，等于对操作员谎称接管已登记。
    """
    from backend.core.shared.interfaces.issue_run_control import (
        STATE_RUNNING,
        TAKEOVER_STATE_CHANGE_CONFLICT,
    )
    from backend.core.use_cases import agent_runner_issue_control as issue_control
    from backend.infrastructure.persistence.console_store_takeover import TakeoverRequestResult

    class _NothingPersistedStore(SqliteConsoleStore):
        """把接管请求一律判成"未持久"的存储：控制行仍为 running，闸门未落。"""

        def request_issue_takeover(self, **_kwargs: object) -> TakeoverRequestResult:
            return TakeoverRequestResult(
                status=TAKEOVER_STATE_CHANGE_CONFLICT, observed_state=STATE_RUNNING
            )

    store = _NothingPersistedStore(tmp_path / "console.db")
    _register_running_run(store)
    monkeypatch.setattr(issue_control, "resolve_issue_run_control", lambda: store)

    action_result = execute_issue_action(
        action="take_over_verifier",
        repo_id="keda-main",
        issue_number=42,
        contexts=[_make_context()],
        github_client_factory=lambda _path: None,
        supervisor=_make_supervisor(tmp_path),
        store=store,
        runner_command=_fake_runner_command(),
        spawn_cwd=tmp_path,
        process_runner=FakeProcessRunner(),
        run_id="run-1",
        head_sha="abc123",
    )

    assert action_result.takeover_http_status == 409
    assert action_result.takeover_outcome == TAKEOVER_STATE_CHANGE_CONFLICT
    assert action_result.result == "rejected"
    audits = store.list_recent_audits(limit=5)
    assert audits[0].action == "take_over_verifier"
    assert audits[0].result == "rejected"
    control_row = store.get_issue_run(repo_id="keda-main", issue_number=42)
    assert control_row.state == STATE_RUNNING
    assert control_row.request_id is None
    assert control_row.autopilot_hold is False
