"""显式迁移 GitHub Issue 与 PR 中的旧 KedaCode 标记。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import IssueSummary

_LEGACY_HTML_MARKER_PATTERN = re.compile(r"(?P<opening><!--[ \t\r\n]*)iar:(?P<name>[a-z0-9-]+)")
_LEGACY_EXEC_MARKER_PATTERN = re.compile(r"(?P<opening><<)(?P<ending>END_)?IAR_EXEC(?P<closing>>>)")
_LEGACY_EXEC_RESULT_PATTERN = re.compile(
    r"(?P<opening>\[/?)(?P<name>IAR_EXEC_RESULT)(?P<closing>\])"
)
# GitHub 的 issues 列表把 PR 也当 Issue 返回，`html_url` 因此指向 ``/pull/<n>``。
_PULL_REQUEST_URL_PATTERN = re.compile(r"/pull/\d+")


@dataclass(frozen=True)
class GitHubMarkerMigrationEntry:
    """一个待更新的 Issue / PR 正文或评论。"""

    issue_number: int
    field_name: str
    comment_id: int | None
    marker_counts: tuple[tuple[str, int], ...]
    migrated_body: str
    #: 正文条目是否属于 PR：PR 正文只能经 pulls 端口写回。
    is_pull_request: bool = False

    @property
    def occurrence_count(self) -> int:
        """返回该正文或评论中要迁移的标记总数。"""
        return sum(count for _marker_name, count in self.marker_counts)


@dataclass(frozen=True)
class GitHubMarkerMigrationResult:
    """GitHub 标记迁移的扫描与写入结果。"""

    scanned_item_count: int
    entries: tuple[GitHubMarkerMigrationEntry, ...]
    updated_count: int
    updated_locations: tuple[tuple[int, str], ...]
    failures: tuple[str, ...]
    include_closed: bool
    applied: bool

    @property
    def occurrence_count(self) -> int:
        """返回所有待迁移位置中的旧标记总数。"""
        return sum(entry.occurrence_count for entry in self.entries)


def replace_legacy_github_markers(body: str) -> tuple[str, tuple[tuple[str, int], ...]]:
    """只替换旧标记前缀，保留标记外文字与原始空白。

    Args:
        body: Issue / PR 正文或评论正文。

    Returns:
        更新后的正文，以及按标记名统计的出现次数。
    """
    marker_counts: dict[str, int] = {}

    def replace_html_marker(match: re.Match[str]) -> str:
        marker_name = f"iar:{match.group('name')}"
        marker_counts[marker_name] = marker_counts.get(marker_name, 0) + 1
        return f"{match.group('opening')}kc:{match.group('name')}"

    def replace_exec_marker(match: re.Match[str]) -> str:
        old_marker_name = f"{match.group('ending') or ''}IAR_EXEC"
        marker_counts[old_marker_name] = marker_counts.get(old_marker_name, 0) + 1
        return (
            f"{match.group('opening')}{match.group('ending') or ''}KC_EXEC{match.group('closing')}"
        )

    def replace_exec_result_marker(match: re.Match[str]) -> str:
        marker_name = match.group("name")
        marker_counts[marker_name] = marker_counts.get(marker_name, 0) + 1
        return f"{match.group('opening')}KC_EXEC_RESULT{match.group('closing')}"

    migrated_body = _LEGACY_HTML_MARKER_PATTERN.sub(replace_html_marker, body)
    migrated_body = _LEGACY_EXEC_MARKER_PATTERN.sub(replace_exec_marker, migrated_body)
    migrated_body = _LEGACY_EXEC_RESULT_PATTERN.sub(replace_exec_result_marker, migrated_body)
    return migrated_body, tuple(sorted(marker_counts.items()))


def migrate_github_markers(
    github_client: IGitHubClient,
    *,
    include_closed: bool = False,
    apply: bool = False,
) -> GitHubMarkerMigrationResult:
    """预演或执行仓库中 open Issue / PR 的旧标记迁移。

    Args:
        github_client: 当前仓库的 GitHub 客户端。
        include_closed: 是否把 closed Issue / PR 也纳入扫描。
        apply: 为真时逐条写回正文与评论；否则只返回预演计划。

    Returns:
        扫描条目、标记数量、写入成功数和逐条失败信息。

    Raises:
        NotImplementedError: 客户端未实现完整的 Issue / PR 列举能力。
        RuntimeError: 仓库条目或评论无法被完整读取。
    """
    repository_items = github_client.list_repository_items(
        state="all" if include_closed else "open"
    )
    entries = _collect_migration_entries(github_client, repository_items)
    if not apply:
        return GitHubMarkerMigrationResult(
            scanned_item_count=len(repository_items),
            entries=tuple(entries),
            updated_count=0,
            updated_locations=(),
            failures=(),
            include_closed=include_closed,
            applied=False,
        )

    updated_count = 0
    updated_locations: list[tuple[int, str]] = []
    failures: list[str] = []
    for entry in entries:
        try:
            _write_migration_entry(github_client, entry)
        except Exception as exc:  # noqa: BLE001 - continue and report every failed location.
            failures.append(f"#{entry.issue_number} {entry.field_name}: {exc}")
            continue
        updated_count += 1
        updated_locations.append((entry.issue_number, entry.field_name))

    return GitHubMarkerMigrationResult(
        scanned_item_count=len(repository_items),
        entries=tuple(entries),
        updated_count=updated_count,
        updated_locations=tuple(updated_locations),
        failures=tuple(failures),
        include_closed=include_closed,
        applied=True,
    )


def _write_migration_entry(github_client: IGitHubClient, entry: GitHubMarkerMigrationEntry) -> None:
    """把一条待迁移正文写回它真正的宿主端点。

    GitHub 把 PR 也算进 issues 集合，所以读取 PR 正文可以走 issues 端口；但
    ``PATCH /issues/{n}`` 不会改写 PR 正文——用它写回会返回成功、远端却原样不动，
    于是迁移报告声称已更新，而 PR 里读不懂的旧签收区块还在那儿（门禁依旧 fail
    closed）。因此 PR 正文必须经 pulls 端口写回。
    """
    if entry.comment_id is not None:
        github_client.edit_issue_comment(entry.comment_id, entry.migrated_body)
    elif entry.is_pull_request:
        github_client.update_pull_request_body(entry.issue_number, entry.migrated_body)
    else:
        github_client.edit_issue_body(entry.issue_number, entry.migrated_body)


def _is_pull_request_item(item: IssueSummary) -> bool:
    """仓库条目是否为 PR（issues 列表里 PR 的 ``html_url`` 指向 ``/pull/<n>``）。"""
    return bool(_PULL_REQUEST_URL_PATTERN.search(item.url))


def _collect_migration_entries(
    github_client: IGitHubClient,
    repository_items: list[IssueSummary],
) -> list[GitHubMarkerMigrationEntry]:
    """读取每条 Issue / PR 的正文和全部评论并生成待写回条目。"""
    migration_entries: list[GitHubMarkerMigrationEntry] = []
    for repository_item in repository_items:
        is_pull_request = _is_pull_request_item(repository_item)
        _append_migration_entry(
            migration_entries,
            issue_number=repository_item.number,
            field_name="PR body" if is_pull_request else "body",
            comment_id=None,
            body=repository_item.body,
            is_pull_request=is_pull_request,
        )
        comment_entries = github_client.list_issue_comment_entries(
            repository_item.number,
            require_success=True,
        )
        for comment_id, comment_body in comment_entries:
            _append_migration_entry(
                migration_entries,
                issue_number=repository_item.number,
                field_name=f"comment {comment_id}",
                comment_id=comment_id,
                body=comment_body,
            )
    return migration_entries


def _append_migration_entry(
    migration_entries: list[GitHubMarkerMigrationEntry],
    *,
    issue_number: int,
    field_name: str,
    comment_id: int | None,
    body: str,
    is_pull_request: bool = False,
) -> None:
    """把含旧标记的正文或评论追加到迁移计划。"""
    migrated_body, marker_counts = replace_legacy_github_markers(body)
    if not marker_counts:
        return
    migration_entries.append(
        GitHubMarkerMigrationEntry(
            issue_number=issue_number,
            field_name=field_name,
            comment_id=comment_id,
            marker_counts=marker_counts,
            migrated_body=migrated_body,
            is_pull_request=is_pull_request,
        )
    )


__all__ = [
    "GitHubMarkerMigrationEntry",
    "GitHubMarkerMigrationResult",
    "migrate_github_markers",
    "replace_legacy_github_markers",
]
