"""认领租约标记兼容性、所有权检查与配置映射。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueCommentRecord,
    RepositoryRunContext,
)
from backend.core.shared.models.machine_identity import MachineIdentity
from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid, format_claim_comment
from backend.core.use_cases.agent_runner_claim_lease import (
    ClaimLeaseLost,
    assert_claim_still_owned,
    classify_remote_lease,
    is_local_claim,
    parse_claim_lease,
    with_claim_lease,
)
from backend.core.use_cases.coordination_heartbeat import CoordinationHeartbeat
from backend.core.use_cases.agent_runner_reclaim import parse_claim_marker_detail
from backend.engines.agent_runner.factory import build_app_config_from_settings
from backend.infrastructure.config.settings import AgentRunnerDaemonSettings, AgentRunnerSettings
from tests.conftest import FakeGitHubClient

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


def test_lease_is_independent_and_legacy_claim_parser_still_reads_it() -> None:
    """新标记不改认领 marker，旧的严格解析器仍可解析认领字段。"""
    bid = ClaimBid(0, "shared-host", 444, NOW, "claude")
    original_claim = format_claim_comment(bid)
    leased_comment = with_claim_lease(
        original_claim,
        machine_id="machine-b",
        ttl_seconds=600,
        renewed_at=NOW,
    )

    parsed_claim = parse_claim_marker_detail(leased_comment)
    parsed_lease = parse_claim_lease(leased_comment)

    assert (
        original_claim.split("<!-- kc:claim", maxsplit=1)[1].split("-->", maxsplit=1)[0]
        == (leased_comment.split("<!-- kc:claim", maxsplit=1)[1].split("-->", maxsplit=1)[0])
    )
    assert parsed_claim is not None
    assert (parsed_claim.host, parsed_claim.pid, parsed_claim.agent) == (
        "shared-host",
        444,
        "claude",
    )
    assert parsed_lease is not None
    assert parsed_lease.machine_id == "machine-b"
    assert parsed_lease.ttl_seconds == 600


def test_remote_lease_classification_and_machine_identity() -> None:
    """按服务端更新时间判断 TTL，并以机器编号区分同名主机。"""
    lease = parse_claim_lease(
        '<!-- kc:lease ttl="600" renewed_at="2026-10-26T08:00:00+00:00" '
        'machine="machine-b" v="1" -->'
    )

    assert lease is not None
    assert (
        classify_remote_lease(
            updated_at=NOW + timedelta(minutes=9), lease=lease, now=NOW + timedelta(minutes=10)
        ).state
        == "live"
    )
    assert (
        classify_remote_lease(updated_at=NOW, lease=lease, now=NOW + timedelta(minutes=10)).state
        == "expired"
    )
    assert classify_remote_lease(updated_at=NOW, lease=None, now=NOW + timedelta(days=1)).state == (
        "no_lease"
    )
    assert not is_local_claim(
        claim_host="macbook", lease=lease, machine_id="machine-a", host="macbook"
    )
    assert is_local_claim(
        claim_host="other-name", lease=lease, machine_id="machine-b", host="macbook"
    )
    assert is_local_claim(claim_host="macbook", lease=None, machine_id=None, host="macbook")


def test_publish_ownership_fails_closed_after_a_newer_claim() -> None:
    """最新可信认领易主后，发布检查必须拒绝推送。"""
    client = FakeGitHubClient()
    own_bid = ClaimBid(0, "host-b", 444, NOW, "claude")
    own_comment = with_claim_lease(
        format_claim_comment(own_bid),
        machine_id="machine-b",
        ttl_seconds=600,
        renewed_at=NOW,
    )
    client.comment_issue(42, own_comment)
    assert_claim_still_owned(
        github_client=client,
        issue_number=42,
        own_comment_id=1,
        machine_id="machine-b",
    )

    client.comment_issue(
        42,
        with_claim_lease(
            format_claim_comment(ClaimBid(0, "host-a", 888, NOW, "claude")),
            machine_id="machine-a",
            ttl_seconds=600,
            renewed_at=NOW,
        ),
    )
    with pytest.raises(ClaimLeaseLost):
        assert_claim_still_owned(
            github_client=client,
            issue_number=42,
            own_comment_id=1,
            machine_id="machine-b",
        )


def test_daemon_settings_map_through_to_domain_config() -> None:
    """Pydantic 配置和领域配置保持一致，不让默认值掩盖映射漏项。"""
    settings = AgentRunnerSettings()
    settings.daemon = AgentRunnerDaemonSettings(presence_enabled=False, lease_ttl_seconds=900)

    domain_config: AppConfig = build_app_config_from_settings(settings)

    assert domain_config.daemon.presence_enabled is False
    assert domain_config.daemon.lease_ttl_seconds == 900


def test_configured_ttl_is_written_into_new_claim_comment() -> None:
    """settings 到领域配置再到新认领评论的租约时长保持一致。"""
    settings = AgentRunnerSettings()
    settings.daemon = AgentRunnerDaemonSettings(presence_enabled=False, lease_ttl_seconds=900)
    domain_config = build_app_config_from_settings(settings)
    repository_context = RepositoryRunContext(
        repo_id="repo",
        display_name="Repository",
        repo_path=Path("/tmp/repo"),
        config=domain_config,
    )

    def github_client_factory(_repo_path: Path) -> FakeGitHubClient:
        return FakeGitHubClient()

    heartbeat = CoordinationHeartbeat(
        contexts=[repository_context],
        machine_identity=MachineIdentity("machine-test", NOW),
        version="1.2.3",
        role="run",
        github_client_factory=github_client_factory,
        lease_client_factory=github_client_factory,
    )
    heartbeat.start()
    try:
        prepared_comment = heartbeat.prepare_claim_comment(
            base_comment=format_claim_comment(ClaimBid(1, "host", 444, NOW, "claude")),
            config=domain_config,
        )
    finally:
        heartbeat.stop()

    assert prepared_comment is not None
    comment_body, effective_ttl = prepared_comment
    lease = parse_claim_lease(comment_body)
    assert effective_ttl == 900
    assert lease is not None and lease.ttl_seconds == 900


def test_daemon_settings_preserve_low_ttl_for_runtime_clamp() -> None:
    """低 TTL 保留到运行期，由心跳按下限修正并告警。"""
    settings = AgentRunnerSettings()
    settings.daemon = AgentRunnerDaemonSettings(lease_ttl_seconds=60)

    domain_config: AppConfig = build_app_config_from_settings(settings)

    assert domain_config.daemon.lease_ttl_seconds == 60


def test_comment_record_timestamp_is_aware() -> None:
    """评论记录的类型承载服务器时间和作者字段。"""
    record = IssueCommentRecord(1, "comment", NOW, "writer")

    assert record.updated_at.utcoffset() == timedelta(0)
