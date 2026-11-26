"""校验运行账本的完整远端 Issue 身份，隔离本机仓库别名复用留下的记录。"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.core.shared.interfaces.runner_console import (
    IPrdLifecycleStore,
    PrdLifecycleRunRecord,
    RunRecord,
)
from backend.core.shared.models.runtime_task import RuntimeTask
from backend.core.use_cases.prd_invocation_detail import invocation_run_from_detail
from backend.core.use_cases.runtime_task_observation import parse_runtime_timestamp

_logger = logging.getLogger(__name__)
type IssueKey = tuple[str, int]


@dataclass(frozen=True)
class RuntimeHistorySelection:
    """经远端身份校验的账本行，以及不能无依据补入旧字段的冲突键。"""

    runs: tuple[RunRecord, ...]
    lifecycle_runs: tuple[PrdLifecycleRunRecord, ...]
    conflicting_keys: frozenset[IssueKey]
    lifecycle_unavailable: bool = False


def _issue_identity(issue_url: str | None) -> tuple[str, int] | None:
    """解析主机、完整仓库路径和 Issue 编号；忽略大小写、尾斜线和查询片段。"""
    if not issue_url:
        return None
    try:
        parsed_url = urlsplit(issue_url.strip())
    except ValueError:
        return None
    repository_path, separator, issue_number = (
        parsed_url.path.rstrip("/").casefold().rpartition("/issues/")
    )
    if not parsed_url.netloc or not separator or not issue_number.isdecimal():
        return None
    return (f"{parsed_url.netloc}{repository_path}".casefold(), int(issue_number))


def _matches_current_issue(
    issue_key: IssueKey,
    candidate_url: str | None,
    expected_issues: Mapping[IssueKey, tuple[str, int]],
    expected_repositories: Mapping[str, set[str]],
) -> bool:
    """用完整 Issue 身份匹配；快照缺少该编号时仍校验同一别名的远端仓库。"""
    candidate_identity = _issue_identity(candidate_url)
    if candidate_identity is None:
        return True  # 尚未发现别名冲突的旧账本继续兼容，冲突键另行禁止未知归属。
    if candidate_identity[1] != issue_key[1]:
        return False
    expected_identity = expected_issues.get(issue_key)
    if expected_identity is not None:
        return candidate_identity == expected_identity
    repository_origins = expected_repositories.get(issue_key[0])
    return not repository_origins or candidate_identity[0] in repository_origins


def select_runtime_history(
    *,
    current_issue_urls: Mapping[IssueKey, str],
    runs: Sequence[RunRecord],
    lifecycle_runs: Sequence[PrdLifecycleRunRecord],
    lifecycle_store: IPrdLifecycleStore | None,
) -> RuntimeHistorySelection:
    """关联历史前校验远端身份，保留旧数据但不把它拼到另一仓库的同号任务上。

    Args:
        current_issue_urls: 当前监控快照确认的 Issue 完整地址。
        runs: 注册仓库别名对应的原始运行记录。
        lifecycle_runs: 原始生命周期记录；旧格式本身不含远端地址。
        lifecycle_store: 读取生命周期开始事件的端口，用于确认所属执行会话。

    Returns:
        已筛选的账本、冲突键和生命周期读取失败标记。已知冲突时无法确认归属的旧行不关联。
    """
    expected_issues = {
        issue_key: identity
        for issue_key, issue_url in current_issue_urls.items()
        if (identity := _issue_identity(issue_url)) is not None
    }
    expected_repositories: dict[str, set[str]] = defaultdict(set)
    for issue_key, identity in expected_issues.items():
        expected_repositories[issue_key[0]].add(identity[0])
    runs_by_issue: dict[IssueKey, list[RunRecord]] = defaultdict(list)
    conflicting_keys: set[IssueKey] = set()
    for run in runs:
        issue_key = (run.repo_id, run.issue_number)
        runs_by_issue[issue_key].append(run)
        if not _matches_current_issue(
            issue_key, run.issue_url, expected_issues, expected_repositories
        ):
            conflicting_keys.add(issue_key)
    accepted_lifecycle_runs: list[PrdLifecycleRunRecord] = []
    lifecycle_unavailable = False
    for lifecycle_run in lifecycle_runs:
        issue_key = (lifecycle_run.repo_id, lifecycle_run.issue_number)
        if lifecycle_run.issue_number is None or lifecycle_run.repo_id not in expected_repositories:
            accepted_lifecycle_runs.append(lifecycle_run)
            continue
        if lifecycle_store is None:
            continue
        try:
            events = lifecycle_store.list_lifecycle_events(run_id=lifecycle_run.run_id)
        except Exception as read_error:  # noqa: BLE001 - 账本异常不能放行身份冲突的记录。
            _logger.warning("Cannot confirm lifecycle repository for %s: %s", issue_key, read_error)
            lifecycle_unavailable = True
            continue
        starts = [
            (timestamp, event)
            for event in events
            if event.event_type in {"started", "claimed"}
            if (timestamp := parse_runtime_timestamp(event.occurred_at)) is not None
        ]
        latest_start = max(starts, key=lambda pair: pair[0], default=None)
        start_time = (
            latest_start[0] if latest_start else parse_runtime_timestamp(lifecycle_run.started_at)
        )
        start_detail = latest_start[1].detail_json if latest_start else "{}"
        try:
            detail_payload = json.loads(start_detail)
        except json.JSONDecodeError:
            detail_payload = {}
        event_issue_url = (
            detail_payload.get("issue_url") if isinstance(detail_payload, dict) else None
        )
        if isinstance(event_issue_url, str) and _issue_identity(event_issue_url) is not None:
            if _matches_current_issue(
                issue_key, event_issue_url, expected_issues, expected_repositories
            ):
                accepted_lifecycle_runs.append(lifecycle_run)
            else:
                conflicting_keys.add(issue_key)
            continue
        if issue_key not in conflicting_keys:
            accepted_lifecycle_runs.append(lifecycle_run)
            continue

        # 旧生命周期缺少远端地址时，借用同一执行会话的 run 行确认身份。
        invocation_run_id = invocation_run_from_detail(start_detail)
        matching_runs = []
        for run in runs_by_issue[issue_key]:
            if invocation_run_id is not None and run.invocation_run_id is not None:
                same_session = invocation_run_id == run.invocation_run_id
            else:
                run_start = parse_runtime_timestamp(run.started_at)
                same_session = bool(
                    run_start and start_time and abs((run_start - start_time).total_seconds()) < 1
                )
            if same_session:
                matching_runs.append(run)
        if matching_runs and all(
            _issue_identity(run.issue_url) is not None
            and _matches_current_issue(
                issue_key, run.issue_url, expected_issues, expected_repositories
            )
            for run in matching_runs
        ):
            accepted_lifecycle_runs.append(lifecycle_run)

    accepted_runs = tuple(
        run
        for run in runs
        if _matches_current_issue(
            (run.repo_id, run.issue_number), run.issue_url, expected_issues, expected_repositories
        )
        and (
            (run.repo_id, run.issue_number) not in conflicting_keys
            or _issue_identity(run.issue_url) is not None
        )
    )
    return RuntimeHistorySelection(
        runs=accepted_runs,
        lifecycle_runs=tuple(accepted_lifecycle_runs),
        conflicting_keys=frozenset(conflicting_keys),
        lifecycle_unavailable=lifecycle_unavailable,
    )


def can_attach_runtime_history(task: RuntimeTask, conflicting_keys: frozenset[IssueKey]) -> bool:
    """判断可否补齐本机观测，避免在已知串库且无可信运行窗口时重新填回旧信息。

    Args:
        task: 已由筛选后的账本组装的任务。
        conflicting_keys: 存在其他远端仓库同号运行记录的本机别名键。

    Returns:
        没有身份冲突或已有可信运行窗口时为 True。
    """
    return (task.repo_id, task.issue_number) not in conflicting_keys or task.started_at is not None
