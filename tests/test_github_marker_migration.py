"""GitHub 协作标记的显式迁移用例。"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.use_cases.github_marker_migration import (
    migrate_github_markers,
    replace_legacy_github_markers,
)
from tests.conftest import FakeGitHubClient


def test_legacy_marker_replacement_changes_only_marker_prefixes() -> None:
    """只改 HTML / Agent 执行标记，正文其它字节保持原样。"""
    original_body = (
        "Intro iar:claim stays prose\r\n"
        "<!--\t iar:claim runner=codex -->\n"
        "<<IAR_EXEC>>kc issue list<<END_IAR_EXEC>>\n"
        "[IAR_EXEC_RESULT]done[/IAR_EXEC_RESULT]"
    )

    migrated_body, marker_counts = replace_legacy_github_markers(original_body)

    assert migrated_body == (
        "Intro iar:claim stays prose\r\n"
        "<!--\t kc:claim runner=codex -->\n"
        "<<KC_EXEC>>kc issue list<<END_KC_EXEC>>\n"
        "[KC_EXEC_RESULT]done[/KC_EXEC_RESULT]"
    )
    assert marker_counts == (
        ("END_IAR_EXEC", 1),
        ("IAR_EXEC", 1),
        ("IAR_EXEC_RESULT", 2),
        ("iar:claim", 1),
    )


def test_marker_migration_previews_applies_and_is_idempotent() -> None:
    """预演不写入，apply 保留评论正文，其后重跑不再产生迁移条目。"""
    github_client = FakeGitHubClient()
    issue_summary = IssueSummary(
        number=17,
        title="Migration sample",
        url="https://github.com/example/repo/issues/17",
        body="Issue text\n<!-- iar:depends-on #12 -->",
        labels=("agent/running",),
    )
    github_client.set_repository_items([issue_summary])
    original_comment_text = "Claim owner\n<!-- iar:claim agent=codex -->\nextra text"
    github_client.comment_issue(17, original_comment_text)

    preview_result = migrate_github_markers(github_client)

    assert preview_result.scanned_item_count == 1
    assert preview_result.occurrence_count == 2
    assert [entry.field_name for entry in preview_result.entries] == ["body", "comment 1"]
    assert not preview_result.applied
    assert github_client.get_issue_body(17) is None
    assert github_client.list_issue_comment_entries(17)[0][1] == original_comment_text

    apply_result = migrate_github_markers(github_client, apply=True)

    assert apply_result.updated_count == 2
    assert not apply_result.failures
    assert github_client.get_issue_body(17) == "Issue text\n<!-- kc:depends-on #12 -->"
    assert github_client.list_issue_comment_entries(17)[0][1] == (
        "Claim owner\n<!-- kc:claim agent=codex -->\nextra text"
    )
    repeated_preview_result = migrate_github_markers(github_client)
    assert repeated_preview_result.entries == ()
    assert repeated_preview_result.occurrence_count == 0


def test_closed_repository_items_are_only_included_by_request() -> None:
    """默认只迁移 open 条目，include_closed 才扫描 closed Issue 与 PR。"""
    github_client = FakeGitHubClient()
    github_client.set_repository_items(
        [
            IssueSummary(
                number=18,
                title="Closed migration sample",
                url="https://github.com/example/repo/issues/18",
                body="<!-- iar:claim owner=closed -->",
                labels=(),
                state="CLOSED",
            )
        ]
    )

    open_only_result = migrate_github_markers(github_client)
    include_closed_result = migrate_github_markers(github_client, include_closed=True)

    assert open_only_result.scanned_item_count == 0
    assert open_only_result.entries == ()
    assert include_closed_result.scanned_item_count == 1
    assert [entry.issue_number for entry in include_closed_result.entries] == [18]
