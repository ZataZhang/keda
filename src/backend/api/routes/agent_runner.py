"""Agent Runner read-only status endpoints."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query

from backend.api.monitoring_reads import MonitoringGitHubReads, MonitoringProcessReads
from backend.api.response_cache import TTLResponseCache
from backend.core.shared.interfaces.agent_runner import (
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import IRunHistoryStore
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    create_github_client,
    create_process_runner,
    get_agent_runner_status_data,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.agent_runner_issue_control import active_verifier_projection
from backend.core.use_cases.agent_runner_monitor import (
    IssueMonitoringSnapshot,
    MonitoringResult,
    build_issue_snapshot,
    build_overview,
)
from backend.core.use_cases.monitor_snapshots import (
    MonitorSyncCoordinator,
    MonitorSyncError,
    get_snapshot_overview,
    persist_monitoring_result,
    snapshot_overview_to_payload,
)
from backend.core.use_cases.runtime_dashboard import (
    RUNTIME_TASK_WINDOW_SECONDS,
    parse_runtime_timestamp,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner"])


@router.get("/agent-runner/status")
def get_agent_runner_status() -> dict:
    """Return runner configuration summary and runtime status."""
    return get_agent_runner_status_data()


@router.get("/agent-runner/health")
def get_agent_runner_health() -> dict:
    """Return runner health status."""
    runner = create_process_runner()
    gh_available = False
    try:
        runner.run(["gh", "--version"], cwd=Path("."))
        gh_available = True
    except Exception:
        pass

    return {
        "status": "healthy" if gh_available else "degraded",
        "gh_cli_available": gh_available,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Monitoring dashboard endpoints
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_monitoring(value: Any) -> Any:
    """Convert dataclass instances (and nested ones) into JSON-friendly dicts."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _serialize_monitoring(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_serialize_monitoring(item) for item in value]
    if isinstance(value, list):
        return [_serialize_monitoring(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_monitoring(item) for key, item in value.items()}
    return value


def _get_monitoring_dependencies() -> tuple[Callable[[Path], IGitHubClient], IProcessRunner]:
    """Resolve the GitHub client factory and shared process runner."""

    def monitoring_client(repo_path: Path) -> IGitHubClient:
        return cast(IGitHubClient, MonitoringGitHubReads(create_github_client(repo_path)))

    return monitoring_client, cast(IProcessRunner, MonitoringProcessReads(create_process_runner()))


_OVERVIEW_CACHE_TTL_SECONDS = 30
_OVERVIEW_CACHE = TTLResponseCache(ttl_seconds=_OVERVIEW_CACHE_TTL_SECONDS)

# In-memory overview job store. Each job tracks the lifecycle of an async
# overview build. Local single-user deployment: jobs are lost on restart.
_OVERVIEW_JOBS: dict[str, dict[str, Any]] = {}
_OVERVIEW_JOBS_TTL_SECONDS = 300  # 5 minutes
_OVERVIEW_JOBS_LOCK = threading.Lock()


def _prune_overview_jobs() -> None:
    """Remove overview jobs that exceeded the retention window."""
    cutoff = time.time() - _OVERVIEW_JOBS_TTL_SECONDS
    with _OVERVIEW_JOBS_LOCK:
        stale = [
            job_id for job_id, job in _OVERVIEW_JOBS.items() if job.get("created_at", 0) < cutoff
        ]
        for job_id in stale:
            _OVERVIEW_JOBS.pop(job_id, None)


def _resolve_enabled_repositories() -> tuple[list[str], list[dict]]:
    """解析当前启用仓库 ID，以及路径失效、已从监控跳过的仓库。

    Returns:
        tuple[list[str], list[dict]]: 启用仓库 ID 列表，与解析失败仓库的
        序列化结果（供 dashboard 显示"无法访问"警示）。
    """
    settings = load_fresh_agent_runner_settings()
    repository_contexts, resolution_failures = resolve_repository_targets_with_diagnostics(settings)
    return (
        [context.repo_id for context in repository_contexts],
        [_serialize_monitoring(failure) for failure in resolution_failures],
    )


def _list_enabled_repo_ids() -> list[str]:
    """Return the repo_id of every currently enabled registry repository."""
    repo_ids, _resolution_failures = _resolve_enabled_repositories()
    return repo_ids


def _scan_repository_and_persist(repo_id: str) -> None:
    """Scan a single repository and persist the already-built payload.

    This is the only producer of monitoring snapshots: it builds the overview
    once and hands the **same** payload to :func:`persist_monitoring_result`,
    never re-scanning GitHub just to write to the database.
    """
    payload = _build_overview_response(repo_ids=[repo_id], include_tracked_issues=True)
    report = persist_monitoring_result(create_console_store(), payload)
    if not report.ok:
        failed_detail = "; ".join(
            f"{failed_repo_id}: {reason}"
            for failed_repo_id, reason in report.failed_repo_ids.items()
        )
        raise MonitorSyncError(f"Failed to persist snapshot for {failed_detail}")
    if not report.persisted_repo_ids:
        raise MonitorSyncError(f"Repository '{repo_id}' produced no overview payload.")


_MONITOR_COORDINATOR: MonitorSyncCoordinator | None = None
_MONITOR_COORDINATOR_LOCK = threading.Lock()


def get_monitor_sync_coordinator() -> MonitorSyncCoordinator:
    """Return the process-wide monitor scan coordinator (created on first use).

    Creating the coordinator starts no thread and performs no I/O; the periodic
    loop itself is owned by the FastAPI application lifespan.
    """
    global _MONITOR_COORDINATOR
    if _MONITOR_COORDINATOR is not None:
        return _MONITOR_COORDINATOR
    with _MONITOR_COORDINATOR_LOCK:
        if _MONITOR_COORDINATOR is None:
            _MONITOR_COORDINATOR = MonitorSyncCoordinator(scan_runner=_scan_repository_and_persist)
        return _MONITOR_COORDINATOR


def _start_overview_job(repo_ids: list[str] | None) -> str:
    """Spawn an async overview build and return its job id."""
    job_id = uuid.uuid4().hex
    now = time.time()
    with _OVERVIEW_JOBS_LOCK:
        _OVERVIEW_JOBS[job_id] = {
            "status": "pending",
            "repo_ids": list(repo_ids) if repo_ids else None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "payload": None,
            "error": None,
        }
    thread = threading.Thread(
        target=_run_overview_job,
        args=(job_id, list(repo_ids) if repo_ids else None),
        daemon=True,
    )
    thread.start()
    return job_id


def _run_overview_job(job_id: str, repo_ids: list[str] | None) -> None:
    """Execute the overview build for a queued job, updating its state.

    Every target repository goes through the shared per-repository coordinator,
    so a periodic sync already running for the same repo is reused instead of
    starting a second GitHub scan. Results are read back from the local
    snapshots (no extra scan) and keep the historical payload shape.
    """
    with _OVERVIEW_JOBS_LOCK:
        job = _OVERVIEW_JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "running"
        job["started_at"] = time.time()
    try:
        target_repo_ids = list(repo_ids) if repo_ids else _list_enabled_repo_ids()
        coordinator = get_monitor_sync_coordinator()
        scan_handles = {repo_id: coordinator.request_sync(repo_id) for repo_id in target_repo_ids}
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Overview job %s failed to start: %s", job_id, exc)
        with _OVERVIEW_JOBS_LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
        return

    scan_failures: dict[str, str] = {}
    for repo_id, handle in scan_handles.items():
        try:
            handle.wait()
        except MonitorSyncError as exc:
            scan_failures[repo_id] = str(exc)
    try:
        payload = _build_snapshot_payload(target_repo_ids)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Overview job %s failed to read snapshots: %s", job_id, exc)
        with _OVERVIEW_JOBS_LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
        return

    with _OVERVIEW_JOBS_LOCK:
        job["payload"] = payload
        job["finished_at"] = time.time()
        if scan_failures:
            # 持久化或扫描失败绝不伪装成刷新成功：旧快照仍在，但 job 必须报错。
            job["status"] = "failed"
            job["error"] = "; ".join(
                f"{failed_repo_id}: {reason}" for failed_repo_id, reason in scan_failures.items()
            )
        else:
            job["status"] = "completed"


def _serialize_overview_job(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe view of an overview job's state."""
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "repo_ids": job.get("repo_ids"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "payload": job.get("payload"),
    }


def _build_snapshot_payload(repo_ids: list[str]) -> dict:
    """Read persisted snapshots back into the historical ``/overview`` payload shape.

    Args:
        repo_ids: Repository IDs to include; acts as the display allow-list so
            removed or disabled repositories never leak back.
    """
    snapshot_result = get_snapshot_overview(
        create_console_store(),
        enabled_repo_ids=repo_ids,
    )
    return {
        "repositories": [entry.overview for entry in snapshot_result.repositories],
        "scanned_at": snapshot_result.scanned_at or "",
        "unreachable_repositories": [],
    }


_TRACKED_ISSUE_SCAN_LIMIT = 20


def _recent_tracked_issue_numbers(repositories: Sequence[Any]) -> dict[str, list[int]]:
    """按仓库收集运行账本里近期出现的 Issue 编号。

    监控快照原本只扫描队列标签下的开放 Issue；已关闭或已移出队列的 Issue
    没有当前状态，运行看板只能把它们显示成「状态未知」。这里把近 7 天运行
    账本里出现过的编号一并交给扫描器补状态，并限制每仓库最多 20 个，避免
    为历史复盘付出无界 GitHub 调用。
    """
    store = cast(IRunHistoryStore, create_console_store())
    cutoff = datetime.now(UTC) - timedelta(seconds=RUNTIME_TASK_WINDOW_SECONDS)
    tracked: dict[str, list[int]] = {}
    for repository in repositories:
        try:
            runs = store.list_recent_runs(
                repo_id=repository.repo_id, limit=_TRACKED_ISSUE_SCAN_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 - 账本读失败只放弃补状态，不阻断扫描。
            _logger.info("Tracked issue lookup failed for %s: %s", repository.repo_id, exc)
            continue
        numbers: list[int] = []
        for run in runs:
            if run.issue_number <= 0 or run.issue_number in numbers:
                continue
            started_at = parse_runtime_timestamp(run.started_at)
            if started_at is not None and started_at < cutoff:
                continue
            numbers.append(run.issue_number)
        tracked[repository.repo_id] = numbers
    return tracked


def _build_overview_response(
    repo_ids: list[str] | None = None,
    *,
    include_tracked_issues: bool = False,
) -> dict:
    """Build the ``/overview`` monitoring response payload.

    Args:
        repo_ids: Optional whitelist of repository IDs to include. ``None``
            means all enabled repositories.
        include_tracked_issues: 是否额外读取运行账本里的 Issue 当前状态。仅周期
            快照写入路径开启；普通 ``/overview`` 请求保持只读队列口径，避免
            每次页面刷新都为历史 Issue 产生额外 GitHub 调用。
    """
    settings = load_fresh_agent_runner_settings()
    repository_contexts, resolution_failures = resolve_repository_targets_with_diagnostics(settings)
    if repo_ids:
        requested = set(repo_ids)
        repository_contexts = [ctx for ctx in repository_contexts if ctx.repo_id in requested]
        resolution_failures = [
            failure for failure in resolution_failures if failure.repo_id in requested
        ]
    github_client_factory, process_runner = _get_monitoring_dependencies()
    try:
        result: MonitoringResult = build_overview(
            repositories=repository_contexts,
            github_client_factory=github_client_factory,
            process_runner=process_runner,
            tracked_issue_numbers_by_repo=(
                _recent_tracked_issue_numbers(repository_contexts)
                if include_tracked_issues
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Failed to build monitoring overview: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to build monitoring overview: {exc}"
        ) from exc
    overview_payload = _serialize_monitoring(result)
    overview_payload["unreachable_repositories"] = [
        _serialize_monitoring(failure) for failure in resolution_failures
    ]
    return overview_payload


def _get_cached_overview_response(repo_ids: list[str] | None = None) -> dict:
    """Return a cached overview when available to keep the dashboard snappy.

    Caching is keyed by the repo_ids filter (or a sentinel for "all") so
    that single-repo refreshes and full overviews don't stomp each other.
    """
    cache_key = ",".join(repo_ids) if repo_ids else "__all__"
    return _OVERVIEW_CACHE.get_or_build(
        cache_key,
        lambda: _build_overview_response(repo_ids=repo_ids),
    )


def _build_issue_detail_response(issue_number: int, repo_id: str | None = None) -> dict:
    """Build the ``/issues/{issue_number}`` monitoring response payload.

    Args:
        issue_number: 目标 Issue 编号。
        repo_id: 可选的仓库作用域。给出时只在该仓库内查找——Issue 编号跨仓库会撞号，
            不限定的话「第一个带该编号的仓库」会答成一个无关仓库的监控详情。
    """
    settings = load_fresh_agent_runner_settings()
    repository_contexts, _resolution_failures = resolve_repository_targets_with_diagnostics(
        settings
    )
    if repo_id:
        repository_contexts = [
            context for context in repository_contexts if context.repo_id == repo_id
        ]
    github_client_factory, process_runner = _get_monitoring_dependencies()

    snapshot: IssueMonitoringSnapshot | None = None
    matched_repo_id: str | None = None
    for repository_context in repository_contexts:
        github_client = github_client_factory(repository_context.repo_path)
        try:
            matching_issues = github_client.list_review_candidate_issues(
                (
                    repository_context.config.labels.ready,
                    repository_context.config.labels.running,
                    repository_context.config.labels.supervising,
                    repository_context.config.labels.review,
                    repository_context.config.labels.failed,
                    repository_context.config.labels.blocked,
                ),
                200,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.info(
                "Issue #%d lookup skipped for %s: %s",
                issue_number,
                repository_context.repo_id,
                exc,
            )
            continue
        for issue in matching_issues:
            if issue.number != issue_number:
                continue
            try:
                snapshot = build_issue_snapshot(
                    issue=issue,
                    config=repository_context.config,
                    github_client=github_client,
                    process_runner=process_runner,
                    repo_path=repository_context.repo_path,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.exception("Failed to build issue detail for #%d: %s", issue_number, exc)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to build issue detail: {exc}",
                ) from exc
            matched_repo_id = repository_context.repo_id
            break
        if snapshot is not None:
            break

    if snapshot is None:
        scope = f"repository '{repo_id}'" if repo_id else "monitored repositories"
        raise HTTPException(
            status_code=404,
            detail=f"Issue #{issue_number} not found in {scope}.",
        )
    payload = _serialize_monitoring(snapshot)
    active_verifier = active_verifier_projection(repo_id=matched_repo_id, issue_number=issue_number)
    payload["active_verifier"] = (
        None
        if active_verifier is None
        else {
            "run_id": active_verifier.run_id,
            "head_sha": active_verifier.head_sha,
            "state": active_verifier.state,
            "can_take_over": active_verifier.can_take_over,
            "pr_number": active_verifier.pr_number,
            "autopilot_hold": active_verifier.autopilot_hold,
        }
    )
    return payload


@router.get("/agent-runner/overview")
def get_agent_runner_overview(
    repo_ids: str | None = Query(
        default=None,
        description=(
            "Optional comma-separated list of repository IDs to scope the "
            "overview to. Omit (or pass empty) to include every enabled "
            "repository."
        ),
    ),
    async_run: bool = Query(
        default=False,
        description=(
            "When true, queue an async overview build and return 202 with "
            "a job_id instead of blocking the request until the payload is "
            "ready. Poll GET /agent-runner/overview/jobs/{job_id} for the "
            "result."
        ),
    ),
) -> dict:
    """Return the Agent Runner monitoring overview.

    Read-only payload that the dashboard renders. Includes per-repository
    health, queue counts, Issue summaries, latest event markers and anomaly
    counts. Does not expose any write/modify API.

    When ``async_run=true`` the response is HTTP 202 with a ``job_id``; the
    payload is fetched later via ``GET /agent-runner/overview/jobs/{id}``.
    """
    parsed_repo_ids: list[str] | None = None
    if repo_ids:
        parsed_repo_ids = [item.strip() for item in repo_ids.split(",") if item.strip()]
        if not parsed_repo_ids:
            parsed_repo_ids = None

    if async_run:
        _prune_overview_jobs()
        job_id = _start_overview_job(parsed_repo_ids)
        # Surface 202 to the caller via a structured payload; FastAPI does
        # not let us set the status code from a return dict, so the client
        # infers async mode from the presence of job_id + pending/running.
        return {
            "async": True,
            "job_id": job_id,
            "repo_ids": parsed_repo_ids,
        }

    return _get_cached_overview_response(repo_ids=parsed_repo_ids)


@router.get("/agent-runner/overview/snapshots")
def get_agent_runner_overview_snapshots() -> dict:
    """Return the locally persisted monitoring snapshots.

    This is the dashboard's first-paint and polling source: it only reads the
    local SQLite snapshots and never touches GitHub, so it stays fast even when
    ``gh`` is slow or offline. Repositories that are no longer enabled are
    filtered out even if historical rows remain.
    """
    enabled_repo_ids, unreachable_repositories = _resolve_enabled_repositories()
    snapshot_result = get_snapshot_overview(
        create_console_store(),
        enabled_repo_ids=enabled_repo_ids,
    )
    return snapshot_overview_to_payload(
        snapshot_result,
        unreachable_repositories=unreachable_repositories,
    )


@router.get("/agent-runner/overview/jobs/{job_id}")
def get_agent_runner_overview_job(job_id: str) -> dict:
    """Return the state of an async overview build job."""
    _prune_overview_jobs()
    with _OVERVIEW_JOBS_LOCK:
        job = _OVERVIEW_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Overview job '{job_id}' not found.")
    return _serialize_overview_job(job_id, job)


@router.get("/agent-runner/overview/per-repo")
def get_agent_runner_overview_per_repo() -> dict:
    """Start one overview job per enabled repository.

    Returns a mapping of ``repo_id -> job_id`` so the dashboard can poll each
    repository independently and render cards as jobs complete (gradual
    reveal). Each job carries a single-repo whitelist so the backend only
    scans the one repository it owns.
    """
    _prune_overview_jobs()
    settings = load_fresh_agent_runner_settings()
    repository_contexts, _ = resolve_repository_targets_with_diagnostics(settings)
    jobs_by_repo: dict[str, str] = {}
    for context in repository_contexts:
        jobs_by_repo[context.repo_id] = _start_overview_job([context.repo_id])
    return {
        "jobs_by_repo": jobs_by_repo,
        "started_at": time.time(),
    }


@router.get("/agent-runner/issues/{issue_number}")
def get_agent_runner_issue_detail(
    issue_number: int,
    repo_id: str | None = Query(
        default=None,
        description=(
            "Optional repository scope. When set, only that repository is "
            "searched, so a colliding issue number in another registered "
            "repository can never answer for it. Omit to keep the legacy "
            "cross-repository lookup."
        ),
    ),
) -> dict:
    """Return monitoring detail (labels, PR, worktree, timeline, anomalies) for an Issue.

    Args:
        issue_number: 目标 Issue 编号。
        repo_id: 可选仓库 ID；给出时把查找限定在该仓库（撞号安全）。

    Returns:
        dict: 序列化后的 Issue 监控快照。

    Raises:
        HTTPException: 400 编号非正；404 目标（作用域内的）仓库里查不到该 Issue。
    """
    if issue_number <= 0:
        raise HTTPException(status_code=400, detail="issue_number must be a positive integer.")
    return _build_issue_detail_response(issue_number, repo_id=repo_id)
