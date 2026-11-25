"""单 Issue 的远端观察和本地快照覆盖。"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from time import time_ns
from typing import Any, Protocol

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.interfaces.runner_console import MonitorIssueRefresh, MonitorSnapshotEntry
from backend.core.shared.models.agent_runner import IssueSummary, RepositoryRunContext
from backend.core.use_cases.agent_runner_monitor import _minimal_issue_snapshot


class IssueRefreshStore(Protocol):
    """单 Issue 刷新的跨进程存储端口。"""

    def monitor_issue_refresh_lock(
        self, repo_id: str, issue_number: int
    ) -> AbstractContextManager[None]:
        """对同一机器的进程串行化刷新。"""
        ...

    def save_monitor_issue_refresh(self, entry: MonitorIssueRefresh) -> None:
        """保存已观察到的单条状态。"""
        ...

    def get_monitor_issue_refresh(
        self, repo_id: str, issue_number: int
    ) -> MonitorIssueRefresh | None:
        """读取目标 Issue 已保存的观察值。"""
        ...

    def monitor_issue_refresh_revision(self) -> str:
        """读取本地单条更新版本。"""
        ...

    def list_monitor_snapshots(self) -> list[MonitorSnapshotEntry]:
        """读取全仓快照以复用已确认的 PR 关联。"""
        ...


@dataclass(frozen=True)
class IssueRefreshTarget:
    """已注册仓库及其 Issue 和可选 PR 编号。"""

    repository: RepositoryRunContext
    issue_number: int
    pr_number: int | None = None


def apply_issue_refresh(overview: dict[str, Any], issue_payload: dict[str, Any]) -> None:
    """用新观察替换一张卡片的远端状态；保留其他 Issue 和本地诊断。"""
    issue_number = issue_payload["number"]
    locations = ("issues", "tracked_issues")
    old_row = next(
        (
            issue
            for key in locations
            for issue in overview.get(key, [])
            if issue.get("number") == issue_number
        ),
        None,
    )
    row = dict(
        old_row or issue_payload,
        **{
            key: issue_payload[key]
            for key in ("number", "title", "url", "labels", "state", "primary_label")
        },
    )
    observed_pr = issue_payload.get("pr")
    if observed_pr is not None:
        previous_pr = row.get("pr") or {}
        row["pr"] = (
            {**previous_pr, **observed_pr}
            if previous_pr.get("head_sha") == observed_pr.get("head_sha")
            else {"checks_state": None, "checks_summary": [], **observed_pr}
        )
    if issue_payload["state"].lower() != "open" or not set(row["labels"]).intersection(
        overview.get("labels", {}).values()
    ):
        target = "tracked_issues"
    else:
        target = "issues"
    for key in locations:
        rows = overview.get(key, [])
        overview[key] = [issue for issue in rows if issue.get("number") != issue_number]
        if key == target:
            overview[key].append(row)
    if overview.get("queue_counts"):
        overview["queue_counts"] = {
            label: sum(issue["primary_label"] == label for issue in overview["issues"])
            for label in overview["queue_counts"]
        }


def refresh_monitor_issue(
    store: IssueRefreshStore,
    github_client: IGitHubClient,
    target: IssueRefreshTarget,
) -> MonitorIssueRefresh:
    """读取一条 Issue 和可选的已知 PR，持久化后才报告成功。"""
    requested_ns = time_ns()
    repository, issue_number = target.repository, target.issue_number
    known_pr_number = target.pr_number
    with store.monitor_issue_refresh_lock(repository.repo_id, issue_number):
        existing = store.get_monitor_issue_refresh(repository.repo_id, issue_number)
        if existing is not None and existing.observed_ns >= requested_ns:
            cached_pr = (json.loads(existing.payload_json)["issue"].get("pr") or {}).get("number")
            if known_pr_number is None or known_pr_number == cached_pr:
                return existing
        if known_pr_number is None:
            if existing is not None:
                known_pr_number = (json.loads(existing.payload_json)["issue"].get("pr") or {}).get(
                    "number"
                )
            if known_pr_number is None:
                for snapshot in store.list_monitor_snapshots():
                    if snapshot.repo_id != repository.repo_id:
                        continue
                    overview = json.loads(snapshot.payload_json)
                    matched = next(
                        (
                            row
                            for key in ("issues", "tracked_issues")
                            for row in overview.get(key, [])
                            if row.get("number") == issue_number
                        ),
                        {},
                    )
                    known_pr_number = (matched.get("pr") or {}).get("number")
                    break
        issue = github_client.get_issue(issue_number)
        if issue.number != issue_number:
            raise RuntimeError(f"Remote returned a different Issue than #{issue_number}.")
        observed_pr = None
        if known_pr_number is not None:
            load_pr_state = getattr(github_client, "get_pull_request_monitor_state", None)
            if load_pr_state is None:
                raise ValueError("This repository does not support lightweight PR refresh.")
            observed_pr = load_pr_state(known_pr_number)
        return record_confirmed_monitor_issue(store, repository, issue, observed_pr)


def record_confirmed_monitor_issue(
    store: IssueRefreshStore,
    repository: RepositoryRunContext,
    issue: IssueSummary,
    observed_pr: dict[str, Any] | None = None,
) -> MonitorIssueRefresh:
    """复用写入后已确认的 GitHub 状态，不发起额外远端请求。"""
    with store.monitor_issue_refresh_lock(repository.repo_id, issue.number):
        if observed_pr is None:
            existing = store.get_monitor_issue_refresh(repository.repo_id, issue.number)
            if existing is not None:
                observed_pr = json.loads(existing.payload_json)["issue"].get("pr")
        issue_payload = asdict(_minimal_issue_snapshot(issue, repository.config))
        issue_payload["pr"] = observed_pr
        payload = {
            "issue": issue_payload,
            "display_name": repository.display_name,
            "base_branch": repository.config.git.base_branch,
            "remote": repository.config.git.remote,
            "labels": {
                "ready": repository.config.labels.ready,
                "running": repository.config.labels.running,
                "supervising": repository.config.labels.supervising,
                "review": repository.config.labels.review,
                "failed": repository.config.labels.failed,
                "blocked": repository.config.labels.blocked,
            },
        }
        entry = MonitorIssueRefresh(
            repository.repo_id,
            issue.number,
            json.dumps(payload, ensure_ascii=False),
            time_ns(),
        )
        store.save_monitor_issue_refresh(entry)
        return entry
