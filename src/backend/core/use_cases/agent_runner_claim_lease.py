"""认领租约的标记格式、时间判定与发布归属检查。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueCommentRecord
from backend.core.use_cases.agent_runner_reclaim import ClaimMarkerDetail, parse_claim_marker_detail

_LEASE_MARKER_PATTERN = re.compile(r"<!--\s*kc:lease\s+(?P<attributes>.*?)\s*-->", re.DOTALL)
_ATTRIBUTE_PATTERN = re.compile(r'([a-z_]+)="([^"]*)"')
_LEASE_HUMAN_BLOCK_PATTERN = re.compile(
    r"(?m)^- Lease: renews every \d+s, expires \d+s after the last renewal\n"
    r"- Last renewed(?: \(UTC\))?: `[^`]*`$"
)


@dataclass(frozen=True)
class ClaimLease:
    """认领评论声明的租约参数。"""

    machine_id: str
    ttl_seconds: int
    renewed_at: datetime


@dataclass(frozen=True)
class RemoteLeaseStatus:
    """远端认领租约的判定与剩余时间。"""

    state: str
    remaining_seconds: float


class ClaimLeaseLost(RuntimeError):
    """本进程已失去认领，必须放弃提交、推送和开 PR。"""


def format_claim_lease_marker(*, machine_id: str, ttl_seconds: int, renewed_at: datetime) -> str:
    """渲染独立于 kc:claim 的租约标记。"""
    renewal_time = _as_aware_utc(renewed_at).isoformat()
    return (
        f'<!-- kc:lease machine="{machine_id}" ttl="{ttl_seconds}" '
        f'renewed_at="{renewal_time}" v="1" -->'
    )


def parse_claim_lease(comment_body: str) -> ClaimLease | None:
    """解析正文中最后一个属性顺序不限的 kc:lease 标记。"""
    matches = list(_LEASE_MARKER_PATTERN.finditer(comment_body))
    if not matches:
        return None
    attributes = dict(_ATTRIBUTE_PATTERN.findall(matches[-1].group("attributes")))
    try:
        machine_id = attributes["machine"]
        ttl_seconds = int(attributes["ttl"])
        renewed_at = _as_aware_utc(datetime.fromisoformat(attributes["renewed_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not machine_id or ttl_seconds <= 0:
        return None
    return ClaimLease(machine_id, ttl_seconds, renewed_at)


def with_claim_lease(
    claim_comment: str,
    *,
    machine_id: str,
    ttl_seconds: int,
    renewed_at: datetime,
) -> str:
    """在原认领标记后追加或更新租约说明，不改 kc:claim 原文。"""
    marker = format_claim_lease_marker(
        machine_id=machine_id, ttl_seconds=ttl_seconds, renewed_at=renewed_at
    )
    human_line = (
        f"- Lease: renews every {max(1, ttl_seconds // 10)}s, expires "
        f"{ttl_seconds}s after the last renewal\n"
        f"- Last renewed (UTC): `{_as_aware_utc(renewed_at).isoformat()}`"
    )
    updated_comment = _LEASE_HUMAN_BLOCK_PATTERN.sub("", claim_comment).rstrip()
    previous_marker = _LEASE_MARKER_PATTERN.search(updated_comment)
    if previous_marker is not None:
        updated_comment = _LEASE_MARKER_PATTERN.sub(marker, updated_comment).rstrip()
    else:
        updated_comment = f"{updated_comment}\n\n{marker}"
    marker_offset = updated_comment.rfind(marker)
    return f"{updated_comment[:marker_offset].rstrip()}\n{human_line}\n{updated_comment[marker_offset:]}"


def classify_remote_lease(
    *, updated_at: datetime, lease: ClaimLease | None, now: datetime
) -> RemoteLeaseStatus:
    """按服务器端评论更新时间与持有者 TTL 判定租约。"""
    if lease is None:
        return RemoteLeaseStatus("no_lease", 0.0)
    remaining_seconds = (
        lease.ttl_seconds - (_as_aware_utc(now) - _as_aware_utc(updated_at)).total_seconds()
    )
    return RemoteLeaseStatus(
        "expired" if remaining_seconds <= 0 else "live",
        max(0.0, remaining_seconds),
    )


def is_local_claim(
    *,
    claim_host: str,
    lease: ClaimLease | None,
    machine_id: str | None,
    host: str,
) -> bool:
    """新认领按机器编号判本机，旧认领回退到主机名。"""
    if lease is not None:
        return machine_id is not None and lease.machine_id == machine_id
    return claim_host == host


def latest_claim_record(
    comment_records: list[IssueCommentRecord],
) -> tuple[IssueCommentRecord, ClaimMarkerDetail, ClaimLease | None] | None:
    """返回评论流中最近的可解析认领及其租约。"""
    for comment_record in reversed(comment_records):
        claim_detail = parse_claim_marker_detail(comment_record.body)
        if claim_detail is not None:
            return comment_record, claim_detail, parse_claim_lease(comment_record.body)
    return None


def assert_claim_still_owned(
    *,
    github_client: IGitHubClient,
    issue_number: int,
    own_comment_id: int,
    machine_id: str,
    was_lost: bool = False,
) -> None:
    """回读可信认领评论，失去认领或读取不确定时拒绝发布。"""
    if was_lost:
        raise ClaimLeaseLost(f"Issue #{issue_number} claim lease was lost; refusing delivery.")
    try:
        comment_records = github_client.list_issue_comment_records(
            issue_number,
            trusted_only=True,
            body_contains="<!-- kc:claim ",
            require_success=True,
        )
    except Exception as exc:  # noqa: BLE001 - 无法确认归属时必须 fail closed。
        raise ClaimLeaseLost(
            f"Could not verify the claim for Issue #{issue_number}; refusing delivery: {exc}"
        ) from exc
    latest_record = latest_claim_record(comment_records)
    if (
        latest_record is None
        or latest_record[0].comment_id != own_comment_id
        or latest_record[2] is None
        or latest_record[2].machine_id != machine_id
    ):
        raise ClaimLeaseLost(f"Issue #{issue_number} claim is no longer owned by this process.")


def _as_aware_utc(value: datetime) -> datetime:
    """把服务端或注入时间归一为带时区 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ClaimLease",
    "ClaimLeaseLost",
    "RemoteLeaseStatus",
    "assert_claim_still_owned",
    "classify_remote_lease",
    "format_claim_lease_marker",
    "is_local_claim",
    "latest_claim_record",
    "parse_claim_lease",
    "with_claim_lease",
]
