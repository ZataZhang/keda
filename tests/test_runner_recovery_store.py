"""恢复账本的严格事务、跨进程状态与副作用对账。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.core.shared.interfaces.agent_runner import GitHubOperationOutcomeUnknownError
from backend.core.shared.interfaces.runner_claim_guard import (
    clear_active_runner_claim_guard,
    set_active_runner_claim_guard,
)
from backend.core.shared.models.runner_recovery import (
    OperationIntent,
    OperationState,
    RecoveryFailureCategory,
    RecoveryFailureRecord,
)
from backend.core.shared.models.agent_runner import CommandResult, IssueSummary
from backend.core.shared.models.runner_recovery_policy import classify_recovery_failure
from backend.core.shared.models.runner_recovery_operations import build_operation_intent
from backend.core.use_cases.agent_runner_git_recovery import (
    GitRefMutationRequest,
    mutate_git_ref_with_recovery,
)
from backend.core.use_cases.agent_runner_recovery import execute_journaled_write
from backend.infrastructure.persistence.runner_recovery_store import (
    RecoveryOperationIdentityConflict,
    SqliteRunnerRecoveryStore,
)
from tests.conftest import FakeGitHubClient, FakeProcessRunner


def _intent() -> OperationIntent:
    """创建一个固定评论操作身份。"""
    return OperationIntent(
        operation_key="operation-1",
        run_key="repo:test:issue:73",
        repository_id="test",
        issue_number=73,
        operation_kind="issue_comment",
        target="repos/example/repo/issues/73/comments",
        payload_digest="payload-sha256",
    )


def _failure(occurred_at: datetime, category: RecoveryFailureCategory) -> RecoveryFailureRecord:
    """创建一条使用短测试预算的故障记录。"""
    return RecoveryFailureRecord(
        run_key="repo:test:issue:73",
        repository_id="test",
        issue_number=73,
        occurred_at=occurred_at,
        retry_after=occurred_at + timedelta(seconds=30),
        max_attempts=6,
        budget_seconds=1800,
        category=category,
        error_summary="local test outage",
    )


def test_recovery_budget_survives_store_reconstruction_and_auth_hands_off(
    tmp_path: Path,
) -> None:
    """重建存储实例不会清零预算，权限拒绝立即转人工。"""
    db_path = tmp_path / "console.db"
    first_store = SqliteRunnerRecoveryStore(db_path)
    first_failure_at = datetime(2026, 11, 27, tzinfo=timezone.utc)
    first_record = first_store.record_failure(
        _failure(first_failure_at, RecoveryFailureCategory.TRANSIENT_NETWORK)
    )

    restarted_store = SqliteRunnerRecoveryStore(db_path)
    second_record = restarted_store.record_failure(
        _failure(
            first_failure_at + timedelta(seconds=60), RecoveryFailureCategory.TRANSIENT_NETWORK
        )
    )

    assert first_record.recovery_attempts == 0
    assert second_record.recovery_attempts == 1
    assert second_record.first_failure_at == first_failure_at
    assert second_record.budget_deadline == first_failure_at + timedelta(seconds=1800)

    handoff_record = restarted_store.record_failure(
        _failure(first_failure_at + timedelta(seconds=90), RecoveryFailureCategory.AUTH)
    )
    assert handoff_record.recovery_state == "needs_human"
    assert handoff_record.resume_at is None


def test_stale_in_flight_operation_becomes_unknown_and_reconciles_without_resend(
    tmp_path: Path,
) -> None:
    """新进程把遗留发送转为未知，只读对账命中后不创建第二个评论。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    intent = _intent()
    store.begin_operation(intent)
    assert store.transition_operation(
        intent.operation_key,
        [OperationState.PENDING],
        OperationState.IN_FLIGHT,
    )
    with sqlite3.connect(store._db_path) as connection:
        connection.execute(
            "UPDATE runner_external_operations SET owner_pid = ? WHERE operation_key = ?",
            (2_147_000_000, intent.operation_key),
        )

    result = execute_journaled_write(
        recovery_store=store,
        intent=intent,
        send=lambda: _unexpected_send(),
        reconcile=lambda: "comment-91",
    )

    assert result == "comment-91"
    assert store.get_operation(intent.operation_key).state is OperationState.CONFIRMED


def test_runner_git_push_persists_confirmation(tmp_path: Path) -> None:
    """Issue 关联的 runner Git push 在严格账本确认后才返回成功。"""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    github_client = FakeGitHubClient()
    github_client.recovery_store = store
    issue = IssueSummary(
        number=73,
        title="Recovery",
        url="https://github.com/example/repo/issues/73",
        body="",
        labels=("agent/running",),
    )
    process_runner = FakeProcessRunner()

    remote_reference = mutate_git_ref_with_recovery(
        GitRefMutationRequest(
            issue=issue,
            worktree_path=worktree_path,
            process_runner=process_runner,
            github_client=github_client,
            remote_name="origin",
            target_ref="refs/heads/issue-73",
            desired_sha="abc123",
        )
    )

    operation_intent = build_operation_intent(
        repository_id=worktree_path.resolve().as_posix(),
        issue_number=73,
        operation_kind="git_push",
        target="origin:refs/heads/issue-73",
        payload_text="abc123",
    )
    operation_record = store.get_operation(operation_intent.operation_key)
    assert remote_reference == "refs/heads/issue-73@abc123"
    assert operation_record is not None
    assert operation_record.state is OperationState.CONFIRMED
    assert (
        process_runner.calls.count(["git", "push", "-u", "origin", "abc123:refs/heads/issue-73"])
        == 1
    )


def test_runner_git_push_keeps_unknown_remote_result_without_resending(
    tmp_path: Path,
) -> None:
    """远端 push 回应丢失且对账无结果时保留 unknown，不盲目重发。"""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    github_client = FakeGitHubClient()
    github_client.recovery_store = store
    issue = IssueSummary(
        number=73,
        title="Recovery",
        url="https://github.com/example/repo/issues/73",
        body="",
        labels=("agent/running",),
    )

    class LostPushResponseRunner(FakeProcessRunner):
        def run(self, command, *, cwd, check=True, **kwargs):  # type: ignore[override]
            command_list = list(command)
            if command_list[:2] == ["git", "push"]:
                result = CommandResult(
                    command=tuple(command_list),
                    return_code=1,
                    stdout="",
                    stderr="connection reset by peer",
                )
                self.calls.append(command_list)
                return result
            if command_list[:3] == ["git", "ls-remote", "--heads"]:
                result = CommandResult(
                    command=tuple(command_list),
                    return_code=0,
                    stdout="",
                    stderr="",
                )
                self.calls.append(command_list)
                return result
            return super().run(command, cwd=cwd, check=check, **kwargs)

    process_runner = LostPushResponseRunner()
    with pytest.raises(GitHubOperationOutcomeUnknownError):
        mutate_git_ref_with_recovery(
            GitRefMutationRequest(
                issue=issue,
                worktree_path=worktree_path,
                process_runner=process_runner,
                github_client=github_client,
                remote_name="origin",
                target_ref="refs/heads/issue-73",
                desired_sha="abc123",
            )
        )

    operation_intent = build_operation_intent(
        repository_id=worktree_path.resolve().as_posix(),
        issue_number=73,
        operation_kind="git_push",
        target="origin:refs/heads/issue-73",
        payload_text="abc123",
    )
    operation_record = store.get_operation(operation_intent.operation_key)
    assert operation_record is not None
    assert operation_record.state is OperationState.UNKNOWN
    assert sum(command[:2] == ["git", "push"] for command in process_runner.calls) == 1


def test_runner_force_push_persists_the_original_remote_lease(tmp_path: Path) -> None:
    """PR rebase 的 ledgered force push 必须显式使用 rebase 前的 head lease。"""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    github_client = FakeGitHubClient()
    github_client.recovery_store = store
    issue = IssueSummary(
        number=73,
        title="Recovery",
        url="https://github.com/example/repo/issues/73",
        body="",
        labels=("agent/running",),
    )
    process_runner = FakeProcessRunner()

    remote_reference = mutate_git_ref_with_recovery(
        GitRefMutationRequest(
            issue=issue,
            worktree_path=worktree_path,
            process_runner=process_runner,
            github_client=github_client,
            remote_name="origin",
            target_ref="refs/heads/issue-73",
            desired_sha="rebased123",
            expected_remote_sha="before123",
            force_with_lease=True,
        )
    )

    assert remote_reference == "refs/heads/issue-73@rebased123"
    assert [
        "git",
        "push",
        "--force-with-lease=refs/heads/issue-73:before123",
        "-u",
        "origin",
        "rebased123:refs/heads/issue-73",
    ] in process_runner.calls
    operation_intent = build_operation_intent(
        repository_id=worktree_path.resolve().as_posix(),
        issue_number=73,
        operation_kind="git_push",
        target="origin:refs/heads/issue-73",
        payload_text="rebased123",
    )
    operation_record = store.get_operation(operation_intent.operation_key)
    assert operation_record is not None
    assert operation_record.state is OperationState.CONFIRMED


def test_known_not_sent_failure_can_retry_but_unknown_failure_cannot(
    tmp_path: Path,
) -> None:
    """连接建立前失败可补发；收到 EOF 后无可靠对账则停止重发。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    intent = _intent()
    sends = 0

    def send_after_refused_connection() -> str:
        nonlocal sends
        sends += 1
        if sends == 1:
            raise RuntimeError(f"gh API diagnostic: {'x' * 1200} connection refused")
        return "comment-92"

    assert (
        execute_journaled_write(
            recovery_store=store,
            intent=intent,
            send=send_after_refused_connection,
            reconcile=lambda: None,
        )
        == "comment-92"
    )
    assert sends == 2

    unknown_intent = OperationIntent(**{**intent.__dict__, "operation_key": "operation-2"})
    unknown_sends = 0

    def send_with_lost_response() -> str:
        nonlocal unknown_sends
        unknown_sends += 1
        raise RuntimeError("unexpected EOF")

    with pytest.raises(GitHubOperationOutcomeUnknownError):
        execute_journaled_write(
            recovery_store=store,
            intent=unknown_intent,
            send=send_with_lost_response,
            reconcile=lambda: None,
        )

    assert unknown_sends == 1
    assert store.get_operation(unknown_intent.operation_key).state is OperationState.UNKNOWN


def test_journaled_write_rechecks_claim_before_sending(tmp_path: Path) -> None:
    """认领复查失败时不得将写请求发送到 GitHub。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    send_count = 0

    def reject_claim() -> None:
        raise RuntimeError("claim ownership was lost")

    def send() -> str:
        nonlocal send_count
        send_count += 1
        return "comment-93"

    set_active_runner_claim_guard(reject_claim)
    try:
        with pytest.raises(RuntimeError, match="claim ownership was lost"):
            execute_journaled_write(
                recovery_store=store,
                intent=_intent(),
                send=send,
                reconcile=lambda: None,
            )
    finally:
        clear_active_runner_claim_guard()

    assert send_count == 0
    assert store.get_operation("operation-1").state is OperationState.PENDING


def test_journaled_write_rechecks_claim_after_cas_before_sending(tmp_path: Path) -> None:
    """拿到本机 CAS 后认领失效时回滚发送权并阻止副作用。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    send_count = 0
    guard_checks = 0

    def reject_second_claim_check() -> None:
        nonlocal guard_checks
        guard_checks += 1
        if guard_checks == 2:
            raise RuntimeError("claim lease expired before send")

    def send() -> str:
        nonlocal send_count
        send_count += 1
        return "comment-94"

    set_active_runner_claim_guard(reject_second_claim_check)
    try:
        with pytest.raises(RuntimeError, match="claim lease expired before send"):
            execute_journaled_write(
                recovery_store=store,
                intent=_intent(),
                send=send,
                reconcile=lambda: None,
            )
    finally:
        clear_active_runner_claim_guard()

    assert send_count == 0
    assert store.get_operation("operation-1").state is OperationState.PENDING


def test_http_499_write_is_unknown_until_remote_reconciliation() -> None:
    """HTTP 499 是连接中断信号，不得当作写请求确定拒绝。"""
    decision = classify_recovery_failure(
        diagnostic_text='non-200 OK status code: 499 body: ""',
        is_write=True,
    )

    assert decision.category is RecoveryFailureCategory.OUTCOME_UNKNOWN
    assert decision.result_certainty == "unknown"


def test_operation_compare_and_swap_allows_only_one_sender(tmp_path: Path) -> None:
    """并发恢复者中只有一个能获得发送权。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    intent = _intent()
    store.begin_operation(intent)

    def claim_send_right() -> bool:
        return store.transition_operation(
            intent.operation_key,
            [OperationState.PENDING],
            OperationState.IN_FLIGHT,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _index: claim_send_right(), range(8)))

    assert sum(claims) == 1


def test_operation_key_cannot_be_reused_for_changed_payload(tmp_path: Path) -> None:
    """载荷改变时必须换用新修订身份，不能让旧确认吞掉新内容。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    original_intent = _intent()
    store.begin_operation(original_intent)
    changed_payload_intent = OperationIntent(
        **{
            **original_intent.__dict__,
            "payload_digest": "changed-payload-sha256",
        }
    )

    with pytest.raises(RecoveryOperationIdentityConflict, match="不同目标或载荷摘要"):
        store.begin_operation(changed_payload_intent)

    assert store.get_operation(original_intent.operation_key).intent == original_intent


@pytest.mark.parametrize(
    ("diagnostic_text", "expected_category"),
    [
        ("HTTP 403: API rate limit exceeded", RecoveryFailureCategory.RATE_LIMITED),
        ("HTTP 403: Resource not accessible by integration", RecoveryFailureCategory.AUTH),
    ],
    ids=["rate-limit-403", "permission-403"],
)
def test_forbidden_status_distinguishes_rate_limit_from_permission(
    diagnostic_text: str,
    expected_category: RecoveryFailureCategory,
) -> None:
    """限流型 403 等待，权限型 403 转人工。"""
    decision = classify_recovery_failure(diagnostic_text=diagnostic_text, is_write=True)

    assert decision.category is expected_category


def test_sqlite_transition_failure_is_not_swallowed(tmp_path: Path) -> None:
    """严格恢复事务失败会阻断副作用调用方。"""
    store = SqliteRunnerRecoveryStore(tmp_path / "console.db")
    intent = _intent()
    store.begin_operation(intent)
    with sqlite3.connect(store._db_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_recovery_update BEFORE UPDATE ON runner_external_operations "
            "BEGIN SELECT RAISE(ABORT, 'strict write rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="strict write rejected"):
        execute_journaled_write(
            recovery_store=store,
            intent=intent,
            send=lambda: _unexpected_send(),
            reconcile=lambda: None,
        )


def _unexpected_send() -> str:
    """一旦状态分支错误地发送请求就令测试失败。"""
    raise AssertionError("operation must not be sent")
