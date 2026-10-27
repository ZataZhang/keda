"""机器状态 Issue 和评论槽位行为。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueCommentRecord,
    IssueSummary,
)
from backend.core.use_cases.machine_presence import (
    ensure_presence_issue,
    list_presence_machines,
    parse_presence_comment,
    presence_entry_is_online,
    render_presence_comment,
    update_presence_comment,
)
from tests.conftest import FakeGitHubClient


def test_presence_marker_payload_survives_angle_bracket_hostname() -> None:
    """主机名里的 ``-->`` 不会提前闭合 JSON 区块，这台机器仍出现在 peers 里。"""
    hostile_hostname = 'macbook-->\n<!-- kc:presence-json {"broken": true'

    comment_body = render_presence_comment(
        machine_id="machine-a",
        slot="111@1730000000.000000",
        hostname=hostile_hostname,
        version="1.2.3",
        role="daemon",
        process_state="online",
        issues=({"number": 42, "stage": "agent/running", "agent": "codex"},),
        reported_at=datetime.now(timezone.utc),
    )
    record = IssueCommentRecord(
        comment_id=1,
        body=comment_body,
        updated_at=datetime.now(timezone.utc),
        author="fake-user",
    )

    entry = parse_presence_comment(record)
    payload_text = comment_body.split("kc:presence-json", maxsplit=1)[1].partition("-->")[0].strip()

    # JSON 区块只被它自己的闭合符结束：主机名带来的 `-->` 已转义，读回仍是原值。
    assert json.loads(payload_text)["hostname"] == hostile_hostname
    assert entry is not None
    assert entry.hostname == hostile_hostname
    assert entry.machine_id == "machine-a"


def test_duplicate_presence_issues_converge_to_lowest_number() -> None:
    """状态板只保留编号最小的 open Issue，重复项提示不会无限增长。"""
    client = FakeGitHubClient()
    client.set_list_issues_by_label_result(
        [
            IssueSummary(33, "status", "url/33", "", ("kedacode/presence",)),
            IssueSummary(12, "status", "url/12", "", ("kedacode/presence",)),
        ]
    )

    canonical_issue = ensure_presence_issue(client, AppConfig())
    ensure_presence_issue(client, AppConfig())

    assert canonical_issue.number == 12
    assert any(
        call["method"] == "edit_issue_labels"
        and call["issue_number"] == 33
        and "kedacode/presence" in call["remove"]
        for call in client.calls
    )
    assert any(
        call["method"] == "comment_issue" and call["issue_number"] == 33 and "#12" in call["body"]
        for call in client.calls
    )
    duplicate_comments = [
        call
        for call in client.calls
        if call["method"] == "comment_issue" and call["issue_number"] == 33
    ]
    assert len(duplicate_comments) == 1


def test_duplicate_presence_issue_retries_pointer_comment_before_unlabeling() -> None:
    """评论失败时重复项仍可被发现，重试后先写指针再移除标签。"""

    class FailFirstDuplicateCommentClient(FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.fail_duplicate_comment = True

        def comment_issue(self, issue_number: int, body: str) -> None:
            if self.fail_duplicate_comment and "kc:presence-duplicate" in body:
                self.fail_duplicate_comment = False
                raise RuntimeError("simulated comment failure")
            super().comment_issue(issue_number, body)

    client = FailFirstDuplicateCommentClient()
    client.set_list_issues_by_label_result(
        [
            IssueSummary(33, "status", "url/33", "", ("kedacode/presence",)),
            IssueSummary(12, "status", "url/12", "", ("kedacode/presence",)),
        ]
    )

    try:
        ensure_presence_issue(client, AppConfig())
    except RuntimeError as error:
        assert str(error) == "simulated comment failure"
    else:
        raise AssertionError("first duplicate pointer comment should fail")

    assert not any(
        call["method"] == "edit_issue_labels" and call["issue_number"] == 33
        for call in client.calls
    )
    ensure_presence_issue(client, AppConfig())

    duplicate_comment = next(
        call
        for call in client.calls
        if call["method"] == "comment_issue" and call["issue_number"] == 33
    )
    label_removal_index = next(
        index
        for index, call in enumerate(client.calls)
        if call["method"] == "edit_issue_labels" and call["issue_number"] == 33
    )
    assert "#12" in duplicate_comment["body"]
    assert client.calls.index(duplicate_comment) < label_removal_index


def test_duplicate_presence_issue_consolidates_racing_pointer_comments() -> None:
    """并发写入的可信指针评论会收敛为一条，清理失败时保留重复标签。"""

    class RacingPointerClient(FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.inject_racing_comment = True
            self.fail_first_delete = True

        def comment_issue(self, issue_number: int, body: str) -> None:
            super().comment_issue(issue_number, body)
            if self.inject_racing_comment and "kc:presence-duplicate" in body:
                self.inject_racing_comment = False
                super().comment_issue(issue_number, body)

        def delete_issue_comment(self, comment_id: int) -> None:
            if self.fail_first_delete:
                self.fail_first_delete = False
                raise RuntimeError("simulated duplicate-comment deletion failure")
            super().delete_issue_comment(comment_id)

    client = RacingPointerClient()
    client.set_list_issues_by_label_result(
        [
            IssueSummary(33, "status", "url/33", "", ("kedacode/presence",)),
            IssueSummary(12, "status", "url/12", "", ("kedacode/presence",)),
        ]
    )

    try:
        ensure_presence_issue(client, AppConfig())
    except RuntimeError as error:
        assert str(error) == "simulated duplicate-comment deletion failure"
    else:
        raise AssertionError("the injected duplicate-comment cleanup should fail")

    assert not any(
        call["method"] == "edit_issue_labels" and call["issue_number"] == 33
        for call in client.calls
    )

    ensure_presence_issue(client, AppConfig())

    pointer_entries = client.list_issue_comment_entries(
        33,
        trusted_only=True,
        body_contains='<!-- kc:presence-duplicate canonical="12" -->',
        require_success=True,
    )
    assert len(pointer_entries) == 1
    delete_index = next(
        index for index, call in enumerate(client.calls) if call["method"] == "delete_issue_comment"
    )
    label_removal_index = next(
        index
        for index, call in enumerate(client.calls)
        if call["method"] == "edit_issue_labels" and call["issue_number"] == 33
    )
    assert delete_index < label_removal_index
    assert any(
        call["method"] == "list_issue_comment_entries"
        and call["trusted_only"]
        and call["require_success"]
        and call["issue_number"] == 33
        for call in client.calls
    )


def test_presence_comment_updates_in_place_and_expires_after_five_minutes() -> None:
    """同一进程复用一条评论，读取方按服务端更新时间计算离线。"""
    client = FakeGitHubClient()
    client.set_list_issues_by_label_result(
        [IssueSummary(12, "status", "url/12", "", ("kedacode/presence",))]
    )
    config = AppConfig()
    reported_at = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
    render_args = {
        "github_client": client,
        "config": config,
        "machine_id": "machine-a",
        "slot": "121@1.0",
        "hostname": "macbook",
        "version": "1.2.3",
        "role": "daemon",
        "process_state": "online",
        "issues": (
            {
                "number": 42,
                "stage": "agent/running",
                "agent": "claude",
                "started_at": "2026-10-26T07:30:00+00:00",
            },
        ),
        "now": reported_at,
    }

    first_comment_id = update_presence_comment(**render_args)
    second_comment_id = update_presence_comment(**render_args)
    presence_records = client.list_issue_comment_records(12, body_contains="<!-- kc:presence ")

    assert first_comment_id == second_comment_id == 1
    assert len(presence_records) == 1
    parsed_entry = parse_presence_comment(presence_records[0])
    assert parsed_entry is not None
    assert parsed_entry.machine_id == "machine-a"
    assert parsed_entry.issues[0]["number"] == 42
    assert parsed_entry.issues[0]["started_at"] == "2026-10-26T07:30:00+00:00"
    assert "最后上报 (UTC)" in presence_records[0].body
    assert "2026-10-26T08:00:00+00:00" in presence_records[0].body
    assert "started (UTC): 2026-10-26T07:30:00+00:00" in presence_records[0].body
    assert presence_entry_is_online(parsed_entry, now=presence_records[0].updated_at)
    assert not presence_entry_is_online(
        parsed_entry, now=presence_records[0].updated_at + timedelta(minutes=6)
    )


def test_peers_merge_tasks_from_multiple_process_slots_on_one_machine() -> None:
    """同一机器的 peers 汇总保留多个 kc 进程各自持有的任务。"""
    client = FakeGitHubClient()
    client.set_list_issues_by_label_result(
        [IssueSummary(12, "status", "url/12", "", ("kedacode/presence",))]
    )
    now = datetime.now(timezone.utc)
    shared_args = {
        "github_client": client,
        "config": AppConfig(),
        "machine_id": "machine-a",
        "hostname": "macbook",
        "version": "1.2.3",
        "process_state": "online",
        "now": now,
    }
    stale_comment_id = update_presence_comment(
        **shared_args,
        slot=f"{os.getpid()}@slot-one",
        role="run",
        issues=({"number": 41, "stage": "agent/running", "agent": "claude"},),
    )
    update_presence_comment(
        **shared_args,
        slot=f"{os.getpid()}@slot-two",
        role="daemon",
        issues=({"number": 42, "stage": "agent/running", "agent": "codex"},),
    )
    assert stale_comment_id is not None
    client._issue_comment_updated_at[stale_comment_id] = now - timedelta(minutes=6)

    presence_issue, machines = list_presence_machines(github_client=client, now=now)

    assert presence_issue is not None and presence_issue.number == 12
    assert len(machines) == 1
    assert machines[0].machine_id == "machine-a"
    assert presence_entry_is_online(machines[0], now=now)
    assert [issue_row["number"] for issue_row in machines[0].issues] == [41, 42]
