"""GitHub Issue 机器状态板的收敛、评论槽位与 peers 汇总。"""

from __future__ import annotations

import json
import os
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, ContextManager

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_labels import KEDACODE_PRESENCE_LABEL
from backend.core.shared.models.agent_runner import AppConfig, IssueCommentRecord, IssueSummary

PRESENCE_OFFLINE_AFTER = timedelta(minutes=5)
PRESENCE_UPDATE_INTERVAL_SECONDS = 60
_PRESENCE_MARKER_PATTERN = re.compile(
    r'<!--\s*kc:presence\s+machine="(?P<machine>[^"]+)"\s+' r'slot="(?P<slot>[^"]+)"\s+v="1"\s*-->'
)
_PRESENCE_JSON_PATTERN = re.compile(r"<!--\s*kc:presence-json\s+(?P<payload>.*?)\s*-->", re.DOTALL)


@dataclass(frozen=True)
class PresenceEntry:
    """一条机器进程状态评论的解析结果。"""

    machine_id: str
    slot: str
    hostname: str
    version: str
    role: str
    process_state: str
    reported_at: datetime
    updated_at: datetime
    issues: tuple[dict[str, object], ...]

    @property
    def online(self) -> bool:
        """返回进程显式在线且服务端评论在五分钟内更新。"""
        return self.process_state == "online" and datetime.now(timezone.utc) - self.updated_at < (
            PRESENCE_OFFLINE_AFTER
        )


def ensure_presence_issue(
    github_client: IGitHubClient,
    config: AppConfig,
) -> IssueSummary:
    """创建或收敛本仓库状态 Issue，保留编号最小的 open 项。"""
    presence_issues = _open_presence_issues(github_client)
    if not presence_issues:
        github_client.sync_labels(config.labels, config.agents)
        github_client.create_issue(
            title="KedaCode 机器状态",
            body=(
                "此 Issue 汇总 KedaCode 在本仓库上的机器与执行状态。\n\n"
                "状态由 kc 自动维护，请勿手动编辑或添加任务标签。"
            ),
            labels=(KEDACODE_PRESENCE_LABEL,),
        )
        presence_issues = _open_presence_issues(github_client)
    if not presence_issues:
        raise RuntimeError("GitHub did not return the KedaCode presence Issue after creation.")
    canonical_issue = min(presence_issues, key=lambda issue_summary: issue_summary.number)
    for duplicate_issue in presence_issues:
        if duplicate_issue.number == canonical_issue.number:
            continue
        duplicate_marker = f'<!-- kc:presence-duplicate canonical="{canonical_issue.number}" -->'
        duplicate_comment_entries = github_client.list_issue_comment_entries(
            duplicate_issue.number,
            trusted_only=True,
            body_contains="<!-- kc:presence-duplicate ",
            require_success=True,
        )
        matching_duplicate_entries = [
            (comment_id, comment_body)
            for comment_id, comment_body in duplicate_comment_entries
            if duplicate_marker in comment_body
        ]
        if not matching_duplicate_entries:
            github_client.comment_issue(
                duplicate_issue.number,
                f"{duplicate_marker}\nDuplicate machine status Issue; "
                f"the canonical board is #{canonical_issue.number}.",
            )
            duplicate_comment_entries = github_client.list_issue_comment_entries(
                duplicate_issue.number,
                trusted_only=True,
                body_contains="<!-- kc:presence-duplicate ",
                require_success=True,
            )
            matching_duplicate_entries = [
                (comment_id, comment_body)
                for comment_id, comment_body in duplicate_comment_entries
                if duplicate_marker in comment_body
            ]
            if not matching_duplicate_entries:
                raise RuntimeError(
                    "GitHub did not return the canonical presence pointer after creation."
                )
        retained_comment_id = min(comment_id for comment_id, _ in matching_duplicate_entries)
        for comment_id, _ in duplicate_comment_entries:
            if comment_id != retained_comment_id:
                github_client.delete_issue_comment(comment_id)
        github_client.edit_issue_labels(
            duplicate_issue.number,
            remove=(KEDACODE_PRESENCE_LABEL,),
        )
    return canonical_issue


def update_presence_comment(
    *,
    github_client: IGitHubClient,
    config: AppConfig,
    machine_id: str,
    slot: str,
    hostname: str,
    version: str,
    role: str,
    process_state: str,
    issues: tuple[dict[str, object], ...],
    now: datetime | None = None,
    slot_lock: Callable[[], ContextManager[object]] | None = None,
) -> int | None:
    """原地更新当前进程的状态评论，复用本机已退出进程留下的槽位。"""
    if not config.daemon.presence_enabled:
        return None
    effective_now = _as_aware_utc(now or datetime.now(timezone.utc))
    presence_issue = ensure_presence_issue(github_client, config)
    lock_context = slot_lock() if slot_lock is not None else nullcontext()
    with lock_context:
        comment_records = github_client.list_issue_comment_records(
            presence_issue.number,
            trusted_only=True,
            body_contains="<!-- kc:presence ",
            require_success=True,
        )
        own_record = _find_presence_comment(
            comment_records,
            machine_id=machine_id,
            slot=slot,
        )
        if own_record is None:
            own_record = _reusable_presence_comment(
                comment_records,
                machine_id=machine_id,
                pid_alive=is_process_alive,
            )
        comment_body = render_presence_comment(
            machine_id=machine_id,
            slot=slot,
            hostname=hostname,
            version=version,
            role=role,
            process_state=process_state,
            issues=issues,
            reported_at=effective_now,
        )
        if own_record is not None:
            github_client.edit_issue_comment(own_record.comment_id, comment_body)
            return own_record.comment_id
        github_client.comment_issue(presence_issue.number, comment_body)
        refreshed_records = github_client.list_issue_comment_records(
            presence_issue.number,
            trusted_only=True,
            body_contains="<!-- kc:presence ",
            require_success=True,
        )
        created_record = _find_presence_comment(
            refreshed_records,
            machine_id=machine_id,
            slot=slot,
        )
        if created_record is None:
            raise RuntimeError("Could not read back the newly created machine status comment.")
        return created_record.comment_id


def render_presence_comment(
    *,
    machine_id: str,
    slot: str,
    hostname: str,
    version: str,
    role: str,
    process_state: str,
    issues: tuple[dict[str, object], ...],
    reported_at: datetime,
) -> str:
    """渲染机器状态评论中的机器标记、JSON 区块和网页表格。"""
    report_utc = _as_aware_utc(reported_at)
    payload = {
        "machine_id": machine_id,
        "slot": slot,
        "hostname": hostname,
        "version": version,
        "role": role,
        "process_state": process_state,
        "reported_at": report_utc.isoformat(),
        "issues": list(issues),
    }
    serialized_payload = _escape_marker_breaks(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    task_rows = issues or ({"number": None, "stage": "idle", "agent": None},)
    table_rows = [
        "| 主机名 | kc 版本 | 进程角色 | 状态 | 最后上报 (UTC) | 正在处理 |",
        "|---|---|---|---|---|---|",
    ]
    report_text = report_utc.isoformat(timespec="seconds")
    for task in task_rows:
        issue_number = task.get("number")
        issue_text = f"#{issue_number}" if issue_number is not None else "—"
        agent_name = str(task.get("agent") or "—")
        started_at = str(task.get("started_at") or "")
        if started_at:
            issue_text = (
                f"{issue_text} · {agent_name} · started (UTC): "
                f"{_format_utc_display_time(started_at)}"
            )
        table_rows.append(
            f"| {hostname} (`{machine_id[:8]}`) | {version} | {role} | "
            f"{process_state} | {report_text} | {issue_text} |"
        )
    return "\n".join(
        (
            f'<!-- kc:presence machine="{machine_id}" slot="{slot}" v="1" -->',
            "<!-- kc:presence-json",
            serialized_payload,
            "-->",
            *table_rows,
        )
    )


def parse_presence_comment(record: IssueCommentRecord) -> PresenceEntry | None:
    """解析本功能生成的机器状态评论，拒绝不完整 JSON。"""
    marker_match = _PRESENCE_MARKER_PATTERN.search(record.body)
    payload_match = _PRESENCE_JSON_PATTERN.search(record.body)
    if marker_match is None or payload_match is None:
        return None
    try:
        payload = json.loads(payload_match.group("payload"))
        reported_at = _as_aware_utc(datetime.fromisoformat(payload["reported_at"]))
        issues_value = payload.get("issues", [])
        if not isinstance(issues_value, list) or any(
            not isinstance(issue_value, dict) for issue_value in issues_value
        ):
            return None
        return PresenceEntry(
            machine_id=marker_match.group("machine"),
            slot=marker_match.group("slot"),
            hostname=str(payload["hostname"]),
            version=str(payload["version"]),
            role=str(payload["role"]),
            process_state=str(payload["process_state"]),
            reported_at=reported_at,
            updated_at=_as_aware_utc(record.updated_at),
            issues=tuple(issues_value),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def list_presence_machines(
    *,
    github_client: IGitHubClient,
    now: datetime | None = None,
) -> tuple[IssueSummary | None, list[PresenceEntry]]:
    """读取状态板，按 machine_id 合并进程状态与任务。"""
    effective_now = _as_aware_utc(now or datetime.now(timezone.utc))
    presence_issues = _open_presence_issues(github_client)
    if not presence_issues:
        return None, []
    canonical_issue = min(presence_issues, key=lambda issue_summary: issue_summary.number)
    comment_records = github_client.list_issue_comment_records(
        canonical_issue.number,
        trusted_only=True,
        body_contains="<!-- kc:presence ",
        require_success=True,
    )
    entries_by_machine: dict[str, list[PresenceEntry]] = {}
    for comment_record in comment_records:
        entry = parse_presence_comment(comment_record)
        if entry is None:
            continue
        entries_by_machine.setdefault(entry.machine_id, []).append(entry)
    machine_entries: list[PresenceEntry] = []
    for process_entries in entries_by_machine.values():
        online_entries = [
            entry for entry in process_entries if presence_entry_is_online(entry, now=effective_now)
        ]
        representative = max(
            online_entries or process_entries,
            key=lambda presence_entry: presence_entry.updated_at,
        )
        issue_rows_by_number: dict[int, dict[str, object]] = {}
        issue_rows_without_number: list[dict[str, object]] = []
        for process_entry in sorted(
            process_entries, key=lambda presence_entry: presence_entry.updated_at
        ):
            for issue_row in process_entry.issues:
                issue_number = issue_row.get("number")
                if isinstance(issue_number, int):
                    issue_rows_by_number[issue_number] = issue_row
                else:
                    issue_rows_without_number.append(issue_row)
        machine_entries.append(
            PresenceEntry(
                machine_id=representative.machine_id,
                slot=representative.slot,
                hostname=representative.hostname,
                version=representative.version,
                role=representative.role,
                process_state="online" if online_entries else representative.process_state,
                reported_at=representative.reported_at,
                updated_at=representative.updated_at,
                issues=tuple(
                    [issue_rows_by_number[number] for number in sorted(issue_rows_by_number)]
                    + issue_rows_without_number
                ),
            )
        )
    return canonical_issue, sorted(
        machine_entries,
        key=lambda presence_entry: (
            presence_entry.hostname.lower(),
            presence_entry.machine_id,
        ),
    )


def presence_entry_is_online(entry: PresenceEntry, *, now: datetime) -> bool:
    """按调用方当前时钟判定机器最近五分钟是否上报。"""
    effective_now = _as_aware_utc(now)
    return entry.process_state == "online" and effective_now - entry.updated_at < (
        PRESENCE_OFFLINE_AFTER
    )


def _open_presence_issues(github_client: IGitHubClient) -> list[IssueSummary]:
    """只返回带固定状态标签的 open Issue。"""
    return [
        issue_summary
        for issue_summary in github_client.list_issues_by_label(
            KEDACODE_PRESENCE_LABEL,
            100,
            state="open",
        )
        if issue_summary.state.upper() == "OPEN"
    ]


def _find_presence_comment(
    comment_records: list[IssueCommentRecord],
    *,
    machine_id: str,
    slot: str,
) -> IssueCommentRecord | None:
    """按 machine id 与进程槽位查找现有评论。"""
    for comment_record in reversed(comment_records):
        parsed_entry = parse_presence_comment(comment_record)
        if parsed_entry is not None and (parsed_entry.machine_id, parsed_entry.slot) == (
            machine_id,
            slot,
        ):
            return comment_record
    return None


def _reusable_presence_comment(
    comment_records: list[IssueCommentRecord],
    *,
    machine_id: str,
    pid_alive: Callable[[int], bool],
) -> IssueCommentRecord | None:
    """选择同一机器已退出 PID 的评论槽位。"""
    for comment_record in reversed(comment_records):
        parsed_entry = parse_presence_comment(comment_record)
        if parsed_entry is None or parsed_entry.machine_id != machine_id:
            continue
        pid_text = parsed_entry.slot.split("@", maxsplit=1)[0]
        try:
            process_id = int(pid_text)
        except ValueError:
            continue
        if parsed_entry.process_state == "exited" or not pid_alive(process_id):
            return comment_record
    return None


def is_process_alive(process_id: int) -> bool:
    """只探测本机 PID；权限错误视为存活，避免覆盖活动进程的评论。"""
    if process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _escape_marker_breaks(serialized_payload: str) -> str:
    """转义尖括号，防止正文里的 ``-->`` 提前闭合 ``kc:presence-json`` 注释块。

    JSON 字符串里的 ``\\u003c`` / ``\\u003e`` 在解析时原样还原，因此只影响注释块边界，
    不影响读回的主机名等字段；不做这层转义时，带 ``-->`` 的主机名会让这台机器从
    ``kc peers`` 里静默消失。
    """
    return serialized_payload.replace("<", "\\u003c").replace(">", "\\u003e")


def _as_aware_utc(value: datetime) -> datetime:
    """把时间归一为带时区 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_utc_display_time(timestamp: str) -> str:
    """把带偏移的时间换成 UTC，墙钟文本与非法值原样保留。"""
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    if parsed_timestamp.tzinfo is None:
        return timestamp
    return parsed_timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "PRESENCE_OFFLINE_AFTER",
    "PRESENCE_UPDATE_INTERVAL_SECONDS",
    "PresenceEntry",
    "ensure_presence_issue",
    "list_presence_machines",
    "parse_presence_comment",
    "presence_entry_is_online",
    "render_presence_comment",
    "update_presence_comment",
]
