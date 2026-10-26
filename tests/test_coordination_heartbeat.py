"""租约续期失败时隔离当前 agent attempt。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    DaemonConfig,
    RepositoryRunContext,
)
from backend.core.shared.models.machine_identity import MachineIdentity
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid, format_claim_comment
from backend.core.use_cases.agent_runner_claim_lease import (
    ClaimLeaseLost,
    parse_claim_lease,
    with_claim_lease,
)
from backend.core.use_cases.coordination_heartbeat import (
    CoordinationHeartbeat,
    acquire_coordination_heartbeat,
    active_coordination_heartbeat,
)
from tests.conftest import FakeGitHubClient


def test_lease_github_client_factory_sets_a_bounded_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """续租客户端把单次挂起的 gh 命令限制在五秒内（重试另有上限）。"""
    captured_arguments: dict[str, object] = {}
    expected_client = object()

    def fake_create_github_client(
        _repo_path: Path,
        _process_runner: object,
        **options: object,
    ) -> object:
        captured_arguments.update(options)
        return expected_client

    monkeypatch.setattr(
        "backend.api.cli_coordination.create_github_client",
        fake_create_github_client,
    )

    from backend.api.cli_coordination import _create_lease_client

    actual_client = _create_lease_client(tmp_path, process_runner=object())  # type: ignore[arg-type]

    assert actual_client is expected_client
    assert captured_arguments == {
        "bypass_budget_pause": True,
        "request_timeout_seconds": 5,
    }


class FailingRenewalClient(FakeGitHubClient):
    """通过认领评论编辑故障模拟无法向 GitHub 续租。"""

    def __init__(self) -> None:
        super().__init__()
        self.renewal_attempts = 0

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        self.renewal_attempts += 1
        raise RuntimeError("simulated GitHub outage")


class RegisteredAttemptRunner:
    """记录身份复核与取消调用的最小进程执行器。"""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def probe_live_attempt(self, attempt_key: str) -> SimpleNamespace:
        return SimpleNamespace(confirmed=True)

    def cancel_live_attempt(self, attempt_key: str, ownership: object) -> SimpleNamespace:
        self.cancelled.append(attempt_key)
        return SimpleNamespace(cancelled=True, reason="cancelled")


def test_concurrent_entry_points_retain_one_process_heartbeat(tmp_path: Path) -> None:
    """同一进程的并发恢复入口共享一条 heartbeat 并按引用数退出。"""
    machine_identity = MachineIdentity("machine-b", datetime.now(timezone.utc))
    first_repo_path = (tmp_path / "first").resolve()
    second_repo_path = (tmp_path / "second").resolve()
    first_repo_path.mkdir()
    second_repo_path.mkdir()
    first_config = AppConfig(daemon=DaemonConfig(presence_enabled=False))
    second_config = AppConfig(daemon=DaemonConfig(presence_enabled=False))
    first_context = RepositoryRunContext("first", "First", first_repo_path, first_config)
    second_context = RepositoryRunContext("second", "Second", second_repo_path, second_config)
    first_client = FakeGitHubClient()
    second_client = FakeGitHubClient()
    first_client.repo_path = first_repo_path
    second_client.repo_path = second_repo_path

    def make_heartbeat(context, client):
        return CoordinationHeartbeat(
            contexts=[context],
            machine_identity=machine_identity,
            version="1.2.3",
            role="recover",
            github_client_factory=lambda _repo_path: client,
            lease_client_factory=lambda _repo_path: client,
        )

    first_reference = acquire_coordination_heartbeat(make_heartbeat(first_context, first_client))
    second_reference = acquire_coordination_heartbeat(make_heartbeat(second_context, second_client))
    try:
        assert first_reference is second_reference
        assert active_coordination_heartbeat() is first_reference
        assert set(first_reference._contexts) == {"first", "second"}
        first_reference.stop()
        assert active_coordination_heartbeat() is first_reference
    finally:
        second_reference.stop()

    assert active_coordination_heartbeat() is None


def test_half_ttl_self_fence_does_not_start_another_renewal(tmp_path: Path) -> None:
    """连续半个 TTL 没有续期成功后，先自停而不再发送一次租约请求。"""
    repo_path = tmp_path.resolve()
    config = AppConfig(daemon=DaemonConfig(presence_enabled=False, lease_ttl_seconds=120))
    repository_context = RepositoryRunContext("repo", "Repository", repo_path, config)
    client = FailingRenewalClient()
    client.repo_path = repo_path
    bid = ClaimBid(1, "host-b", 321, datetime.now(timezone.utc), "claude")
    runner = RegisteredAttemptRunner()
    heartbeat = CoordinationHeartbeat(
        contexts=[repository_context],
        machine_identity=MachineIdentity("machine-b", datetime.now(timezone.utc)),
        version="1.2.3",
        role="run",
        github_client_factory=lambda _repo_path: client,
        lease_client_factory=lambda _repo_path: client,
    )
    client.comment_issue(
        42,
        with_claim_lease(
            format_claim_comment(bid),
            machine_id="machine-b",
            ttl_seconds=120,
            renewed_at=datetime.now(timezone.utc),
        ),
    )
    heartbeat.start()
    try:
        heartbeat.register_claim(
            github_client=client,
            issue_number=42,
            bid=bid,
            body=client.list_issue_comments(42)[0],
            ttl_seconds=120,
            config=config,
        )
        heartbeat.register_attempt(
            config=config,
            issue_number=42,
            process_runner=runner,  # type: ignore[arg-type]
            attempt_key="attempt-42",
            worktree_path=repo_path,
        )
        claim = heartbeat._claims[("repo", 42)]
        claim.last_renewed_monotonic = time.monotonic() - 61
        claim.last_renewal_attempt_monotonic = time.monotonic() - 61

        heartbeat._renew_if_due(claim, time.monotonic())

        assert heartbeat.claim_was_lost(issue_number=42, worktree_path=repo_path)
        assert runner.cancelled == ["attempt-42"]
        assert client.renewal_attempts == 0
    finally:
        heartbeat.stop()


def test_publish_fence_fails_closed_when_the_claim_is_not_registered(tmp_path: Path) -> None:
    """本进程没登记认领时，推送前仍要回读确认归属，且远端认领必须拒绝交付。"""
    repo_path = tmp_path.resolve()
    config = AppConfig(daemon=DaemonConfig(presence_enabled=False))
    repository_context = RepositoryRunContext("repo", "Repository", repo_path, config)
    client = FakeGitHubClient()
    client.repo_path = repo_path
    heartbeat = CoordinationHeartbeat(
        contexts=[repository_context],
        machine_identity=MachineIdentity("machine-a", datetime.now(timezone.utc)),
        version="1.2.3",
        role="recover",
        github_client_factory=lambda _repo_path: client,
        lease_client_factory=lambda _repo_path: client,
    )
    client.comment_issue(
        42,
        with_claim_lease(
            format_claim_comment(ClaimBid(1, "host-b", 321, datetime.now(timezone.utc), "claude")),
            machine_id="machine-b",
            ttl_seconds=120,
            renewed_at=datetime.now(timezone.utc),
        ),
    )
    client.comment_issue(
        43,
        with_claim_lease(
            format_claim_comment(ClaimBid(2, "host-a", 321, datetime.now(timezone.utc), "claude")),
            machine_id="machine-a",
            ttl_seconds=120,
            renewed_at=datetime.now(timezone.utc),
        ),
    )

    with pytest.raises(ClaimLeaseLost):
        heartbeat.assert_claim_owned(github_client=client, issue_number=42)
    heartbeat.assert_claim_owned(github_client=client, issue_number=43)


def test_publish_fence_resolves_a_desynced_repo_id(tmp_path: Path) -> None:
    """认领登记在另一个仓库标识下时，发布栅栏仍按评论 ID 精确确认。"""
    repo_path = tmp_path.resolve()
    config = AppConfig(daemon=DaemonConfig(presence_enabled=False))
    repository_context = RepositoryRunContext("repo", "Repository", repo_path, config)
    client = FakeGitHubClient()
    client.repo_path = repo_path
    heartbeat = CoordinationHeartbeat(
        contexts=[repository_context],
        machine_identity=MachineIdentity("machine-a", datetime.now(timezone.utc)),
        version="1.2.3",
        role="run",
        github_client_factory=lambda _repo_path: client,
        lease_client_factory=lambda _repo_path: client,
    )
    bid = ClaimBid(1, "host-a", 321, datetime.now(timezone.utc), "claude")
    claim_body = with_claim_lease(
        format_claim_comment(bid),
        machine_id="machine-a",
        ttl_seconds=120,
        renewed_at=datetime.now(timezone.utc),
    )
    client.comment_issue(42, claim_body)
    heartbeat.start()
    try:
        heartbeat.register_claim(
            github_client=client,
            issue_number=42,
            bid=bid,
            body=claim_body,
            ttl_seconds=120,
            config=config,
        )
        # 模拟 repo_id 漂移：登记键与发布时解析出的仓库标识不一致。
        relocated_claim = heartbeat._claims.pop(("repo", 42))
        heartbeat._claims[("other-repo", 42)] = relocated_claim
        heartbeat.assert_claim_owned(github_client=client, issue_number=42)

        client.comment_issue(
            42,
            with_claim_lease(
                format_claim_comment(
                    ClaimBid(2, "host-b", 555, datetime.now(timezone.utc), "claude")
                ),
                machine_id="machine-b",
                ttl_seconds=120,
                renewed_at=datetime.now(timezone.utc),
            ),
        )
        with pytest.raises(ClaimLeaseLost):
            heartbeat.assert_claim_owned(github_client=client, issue_number=42)
    finally:
        heartbeat.stop()


def test_current_machine_id_falls_back_to_the_persisted_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有心跳时按持久身份取机器编号，本机带租约的认领不会被判成远端。"""
    from backend.core.use_cases import coordination_heartbeat as heartbeat_module

    monkeypatch.setattr(heartbeat_module, "_PERSISTED_MACHINE_ID", None)
    monkeypatch.setattr(
        "backend.core.use_cases.agent_runner_factory.create_machine_identity_store",
        lambda: SimpleNamespace(
            load_or_create=lambda: SimpleNamespace(machine_id="machine-persisted")
        ),
    )

    assert active_coordination_heartbeat() is None
    assert heartbeat_module.current_machine_id() == "machine-persisted"


def test_claim_ttl_below_minimum_is_clamped_with_a_warning(
    tmp_path: Path,
    caplog,
) -> None:
    """配置低于 120 秒仍可加载，但新认领按下限声明并记录告警。"""
    repository_path = tmp_path.resolve()
    config = AppConfig(daemon=DaemonConfig(presence_enabled=False, lease_ttl_seconds=60))
    repository_context = RepositoryRunContext("repo", "Repository", repository_path, config)
    client = FailingRenewalClient()
    client.repo_path = repository_path
    heartbeat = CoordinationHeartbeat(
        contexts=[repository_context],
        machine_identity=MachineIdentity("machine-b", datetime.now(timezone.utc)),
        version="1.2.3",
        role="run",
        github_client_factory=lambda _repo_path: client,
        lease_client_factory=lambda _repo_path: client,
    )
    heartbeat.start()
    try:
        prepared_comment = heartbeat.prepare_claim_comment(
            base_comment=format_claim_comment(
                ClaimBid(1, "host-b", 321, datetime.now(timezone.utc), "claude")
            ),
            config=config,
        )
    finally:
        heartbeat.stop()

    assert prepared_comment is not None
    comment_body, effective_ttl = prepared_comment
    lease = parse_claim_lease(comment_body)
    assert effective_ttl == 120
    assert lease is not None and lease.ttl_seconds == 120
    assert "using the minimum" in caplog.text
