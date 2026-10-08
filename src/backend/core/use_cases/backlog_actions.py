"""Backlog start actions: single PRD, global scheduling, continuous advance.

Actions reuse existing Issue creation, label editing, and runner spawn
workflows so the backlog layer never bypasses the KedaCode state machine.

Three entry points share one selection rule (see :func:`_select_eligible_prds`):

- :func:`start_prd` — start exactly one PRD (console single-start).
- :func:`start_global_backlog` — one-shot batch start used by the console.
- :func:`advance_backlog_queue` — continuous scheduling: reconcile finished
  queue entries, then top the queue up to ``max_parallel``.

:func:`enqueue_prd_ready` 不参与这条选择规则：它只把**指定**的一个 PRD 放进
``agent/ready``，绝不启动 runner，是否被领取交给 daemon / autopilot。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import (
    AuditEntry,
    IBacklogStore,
    IRunnerProcessSupervisor,
    BacklogQueueEntry,
    RunnerProcessKind,
)
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.shared.models.backlog import (
    BacklogActionResult,
    BacklogAdvanceReport,
    BacklogGlobalStartResult,
    BacklogPrd,
    BacklogPrdState,
    BacklogSettingsEntry,
)
from backend.core.shared.models.runner_launch import RunnerLaunchOptions
from backend.core.shared.priority import priority_rank
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    record_lifecycle_event,
    record_lifecycle_terminal,
)
from backend.core.use_cases.console_processes import (
    ConsoleProcessError,
    start_runner_process,
)
from backend.core.use_cases.create_issue_from_prd import (
    IssueFromPrdRequest,
    create_issue_from_prd,
    parse_issue_number,
)
from backend.core.use_cases.backlog_prd_scanner import scan_backlog_prds
from backend.core.use_cases.backlog_dependencies import evaluate_backlog_dependencies
from backend.core.use_cases.backlog_state_resolver import resolve_backlog_states

_logger = logging.getLogger(__name__)


class BacklogActionError(ValueError):
    """Backlog action was rejected or failed."""


class BacklogEnqueueConflictError(BacklogActionError):
    """「加入就绪」的目标正被 runner 执行：再入队会打断在途执行并与 daemon 抢占。"""


_DEFAULT_MAX_PARALLEL = 2

#: Queue statuses that still need reconciliation (terminal ones never change again).
_ACTIVE_QUEUE_STATUSES = ("queued", "running")

#: PRD states that mean "this PRD is done" and therefore close its queue entry.
_COMPLETED_PRD_STATES = frozenset({BacklogPrdState.MERGED, BacklogPrdState.ARCHIVED})


def _issue_type_from_filename(filename: str) -> str:
    """Map PRD filename tokens such as ``FEAT`` or ``BUG`` to Issue type labels."""
    match = re.search(r"P\d+-([A-Z]+)-", filename.upper())
    type_token = match.group(1) if match else "FEAT"
    mapping = {
        "FEAT": "feature",
        "BUG": "bug",
        "CHORE": "chore",
        "DOCS": "docs",
        "REFACTOR": "chore",
        "TEST": "chore",
    }
    return mapping.get(type_token, "feature")


def _now_iso() -> str:
    """Return current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(
    store: IBacklogStore,
    *,
    action: str,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    result: str,
    detail: str,
) -> None:
    """Append a backlog action audit entry."""
    try:
        store.append_audit(
            AuditEntry(
                occurred_at=_now_iso(),
                actor="backlog",
                action=action,
                repo_id=repo_id,
                issue_number=issue_number,
                params_json=f'{{"prd_path": "{prd_path}"}}',
                result=result,
                detail=detail,
            )
        )
    except Exception as exc:  # noqa: BLE001 - audit must not break actions.
        _logger.warning("Failed to audit backlog action %s: %s", action, exc)


def _record_lifecycle(
    store: IBacklogStore,
    *,
    event_type: LifecycleEventType,
    repo_id: str,
    prd_path: str,
    issue_number: int | None,
    trigger: str,
    detail: dict | None = None,
) -> None:
    """旁路记录 backlog 侧的 PRD 生命周期事件（失败不阻断动作）。

    backlog 与 runner 共用同一个 console SQLite 文件，``store`` 同时具备
    lifecycle 账本能力；这里只写“排队 / 启动 / 归档”等 backlog 拥有语义的
    事件，runner 执行内部事件由 runner 自己写，避免同一事实两处猜测。
    """
    record_lifecycle_event(
        store=store,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger=trigger,
        event_type=event_type,
        actor="backlog",
        detail=detail,
    )


def _create_issue_for_prd(
    prd: BacklogPrd,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> int:
    """Create a GitHub Issue for a PRD and return its number.

    Uses the publish-safe path so the PRD is pushed to the base branch before
    ``agent/ready`` is added.
    """
    issue_type = _issue_type_from_filename(Path(prd.prd_path).name)
    request = IssueFromPrdRequest(
        repo_path=context.repo_path,
        prd_path=Path(prd.prd_path),
        issue_type=issue_type,
        queue_ready=True,
        publish_prd=True,
        git_remote=context.config.git.remote,
        git_base_branch=context.config.git.base_branch,
        generated_content_config=context.config.generated_content,
        labels_config=context.config.labels,
        evidence_dir=context.config.validation.evidence_dir,
    )
    issue_url = create_issue_from_prd(
        request=request,
        github_client=github_client,
        process_runner=process_runner,
    )
    return parse_issue_number(issue_url)


def _ensure_ready_label(
    prd: BacklogPrd,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> None:
    """Add ``agent/ready`` to an existing Issue, removing ``agent/failed``."""
    if prd.issue_number is None:
        raise BacklogActionError(f"PRD {prd.prd_path} has no Issue to label.")
    labels_config = context.config.labels
    add_labels = [labels_config.ready]
    remove_labels = [labels_config.failed]
    github_client.edit_issue_labels(
        prd.issue_number,
        add=add_labels,
        remove=remove_labels,
    )


def _spawn_runner(
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    supervisor: IRunnerProcessSupervisor,
    runner_command: Sequence[str],
    spawn_cwd: Path,
    issue_number: int | None = None,
    options: RunnerLaunchOptions | None = None,
) -> None:
    """Spawn a one-shot runner for the repository."""
    start_runner_process(
        repo_id=repo_id,
        kind=RunnerProcessKind.RUN_ONCE,
        contexts=contexts,
        supervisor=supervisor,
        runner_command=runner_command,
        spawn_cwd=spawn_cwd,
        issue_number=issue_number,
        options=options,
    )


def _reject_launch_options_incompatible_with_prd(
    prd: BacklogPrd,
    launch_options: RunnerLaunchOptions | None,
) -> None:
    """发起端复刻 CLI 的**目标域**规则：直出 PR 不适用于 PRD-backed Issue。

    ``RunnerLaunchOptions`` 只能校验选项自身（互斥、缺预设、单 token）；而本用例的
    目标按构造就是带 PRD 锚点的 Issue（``kc run --issue N`` 的 N 来自这份 PRD），
    CLI 对该组合是硬性用法拒绝（``_reject_direct_pr_on_prd_backed_issue``）。不在这里
    拦下的话，网页得到一次「已开始」成功提示，进程页留下一条立刻以用法错误退出的
    托管进程，永远出不了 PR。

    Args:
        prd: 已定位的 pending PRD（提供锚点路径与可能已存在的 Issue 编号）。
        launch_options: 「开始此 PRD」的高级选项；``None`` 表示未给出选项。

    Raises:
        BacklogActionError: 选项与启动目标冲突；此时 GitHub 与本地状态均未被动过。
    """
    if launch_options is None or not launch_options.direct_pr:
        return
    target = f"Issue #{prd.issue_number}" if prd.issue_number is not None else "该 PRD 的 Issue"
    raise BacklogActionError(
        f"{target} 由 PRD 锚定（{prd.prd_path}），直出 PR 只适用于没有 PRD 锚点的 Issue："
        "PRD-backed Issue 必须过 PRD 交付门并归档 PRD，不能被「快速出 PR」旁路。"
        "想跳过合并前的独立验证阶段请改勾快合（fast_merge），"
        "或在终端对一个没有 PRD 锚点的 Issue 执行 `kc run --direct-pr`。"
    )


def start_prd(
    *,
    prd_path: str,
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    github_client: IGitHubClient,
    supervisor: IRunnerProcessSupervisor,
    store: IBacklogStore,
    runner_command: Sequence[str],
    spawn_cwd: Path,
    process_runner: IProcessRunner,
    launch_options: RunnerLaunchOptions | None = None,
) -> BacklogActionResult:
    """Start a single PRD: create Issue if needed, label it, spawn runner.

    Args:
        prd_path: Repository-relative PRD path.
        repo_id: Target repository ID.
        contexts: Resolved enabled repository contexts.
        github_client: GitHub client.
        supervisor: Process supervisor.
        store: Backlog store for auditing.
        runner_command: Runner command prefix.
        spawn_cwd: Working directory for the runner subprocess.
        process_runner: Process runner for Git publishing commands.
        launch_options: 「开始此 PRD」的高级选项，逐项对应 ``kc run`` 同名旗标。
            ``None`` 或全缺省时启动命令与选项引入前逐字节一致。

    Returns:
        Action result with the new state.

    Raises:
        BacklogActionError: PRD 缺失、建 Issue / 打标签失败、选项非法或启动失败。
            选项与 PRD 目标的组合非法（如直出 PR 配 PRD-backed Issue）时在改动
            GitHub 之前就被拒绝。
    """
    context = _resolve_context(repo_id, contexts)
    prds = scan_backlog_prds(context.repo_path, include_archived=False).prds
    prd = next((p for p in prds if p.prd_path == prd_path), None)
    if prd is None:
        raise BacklogActionError(f"PRD not found or not pending: {prd_path}")
    _reject_launch_options_incompatible_with_prd(prd, launch_options)

    if prd.issue_number is None:
        try:
            issue_number = _create_issue_for_prd(prd, context, github_client, process_runner)
        except Exception as exc:  # noqa: BLE001
            _audit(
                store,
                action="start_prd",
                repo_id=repo_id,
                prd_path=prd_path,
                issue_number=None,
                result="error",
                detail=str(exc),
            )
            raise BacklogActionError(f"创建 Issue 失败: {exc}") from exc
    else:
        try:
            _ensure_ready_label(prd, context, github_client)
        except Exception as exc:  # noqa: BLE001
            _audit(
                store,
                action="start_prd",
                repo_id=repo_id,
                prd_path=prd_path,
                issue_number=prd.issue_number,
                result="error",
                detail=str(exc),
            )
            raise BacklogActionError(f"添加 ready 标签失败: {exc}") from exc
        issue_number = prd.issue_number

    _record_lifecycle(
        store,
        event_type=LifecycleEventType.QUEUED,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger="console_start",
        detail={"trigger": "manual"},
    )
    try:
        _spawn_runner(
            repo_id,
            contexts,
            supervisor,
            runner_command,
            spawn_cwd,
            issue_number=issue_number,
            options=launch_options,
        )
    except ConsoleProcessError as exc:
        _audit(
            store,
            action="start_prd",
            repo_id=repo_id,
            prd_path=prd_path,
            issue_number=issue_number,
            result="error",
            detail=str(exc),
        )
        raise BacklogActionError(f"启动 runner 失败: {exc}") from exc

    _record_lifecycle(
        store,
        event_type=LifecycleEventType.STARTED,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger="console_start",
    )
    _audit(
        store,
        action="start_prd",
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        result="accepted",
        detail="Issue created/labelled and runner spawned.",
    )
    return BacklogActionResult(
        prd_path=prd_path,
        issue_number=issue_number,
        state=BacklogPrdState.READY,
        detail="PRD 已进入 ready 状态并启动 runner。",
    )


def _resolve_context(
    repo_id: str, contexts: Sequence[RepositoryRunContext]
) -> RepositoryRunContext:
    """Return the enabled repository context for ``repo_id``."""
    for context in contexts:
        if context.repo_id == repo_id:
            return context
    raise BacklogActionError(f"Repository '{repo_id}' is not an enabled registry target.")


def _backlog_sort_key(prd: BacklogPrd) -> tuple[int, str]:
    """Return the promotion sort key: priority first, then oldest update first.

    This is half of the single source of truth for "which PRD runs next": the
    one-shot console batch start and the continuous scheduling loop both sort
    through it, so manual and automatic paths can never drift apart.

    Args:
        prd: A PRD with live GitHub state already resolved.

    Returns:
        A tuple ordered by ``P0 > P1 > P2 > P3`` and then by ``updated_at``
        ascending (oldest first, matching the historical console behaviour).
    """
    return (priority_rank(prd.priority), prd.updated_at)


def _select_eligible_prds(resolved_prds: Sequence[BacklogPrd]) -> list[BacklogPrd]:
    """Return promotable PRDs ordered by priority, then most recently updated.

    Args:
        resolved_prds: PRDs with live GitHub state already resolved.

    Returns:
        PRDs that are ``NOT_STARTED`` with no dependency blocker, sorted by
        the shared :func:`_backlog_sort_key`.
    """
    eligible = [
        prd
        for prd in resolved_prds
        if prd.state == BacklogPrdState.NOT_STARTED and not prd.block_reason
    ]
    eligible.sort(key=_backlog_sort_key)
    return eligible


def get_or_create_backlog_settings(store: IBacklogStore, repo_id: str) -> BacklogSettingsEntry:
    """Return existing settings or create defaults."""
    settings = store.get_backlog_settings(repo_id)
    if settings is not None:
        return settings
    return BacklogSettingsEntry(
        repo_id=repo_id,
        max_parallel=_DEFAULT_MAX_PARALLEL,
        default_view="list",
        updated_at=_now_iso(),
    )


def start_global_backlog(
    *,
    repo_id: str,
    max_parallel: int,
    contexts: Sequence[RepositoryRunContext],
    github_client_factory: Callable[[Path], IGitHubClient],
    supervisor: IRunnerProcessSupervisor,
    store: IBacklogStore,
    runner_command: Sequence[str],
    spawn_cwd: Path,
    process_runner: IProcessRunner,
) -> BacklogGlobalStartResult:
    """Start up to ``max_parallel`` eligible pending PRDs.

    Args:
        repo_id: Target repository ID.
        max_parallel: Upper bound on concurrent running PRDs.
        contexts: Resolved enabled repository contexts.
        github_client_factory: Callable ``(repo_path) -> IGitHubClient``.
        supervisor: Process supervisor.
        store: Backlog store.
        runner_command: Runner command prefix.
        spawn_cwd: Working directory for the runner subprocess.
        process_runner: Process runner for Git publishing commands.

    Returns:
        Summary of started, queued, and skipped PRDs.
    """
    if max_parallel < 1:
        raise BacklogActionError("并发数必须 >= 1")

    context = _resolve_context(repo_id, contexts)
    github_client = github_client_factory(context.repo_path)
    prds = scan_backlog_prds(context.repo_path, include_archived=False).prds
    block_reasons = evaluate_backlog_dependencies(
        prds,
        github_client=github_client,
    )
    resolved_prds = resolve_backlog_states(
        prds,
        github_client=github_client,
        config=context.config,
        block_reasons=block_reasons,
    )

    # Persist settings.
    settings = BacklogSettingsEntry(
        repo_id=repo_id,
        max_parallel=max_parallel,
        default_view="list",
        updated_at=_now_iso(),
    )
    store.save_backlog_settings(settings)

    # Determine how many slots are free.
    running_count = sum(1 for p in resolved_prds if p.state == BacklogPrdState.RUNNING)
    free_slots = max(0, max_parallel - running_count)

    # Eligible PRDs: not started and not blocked/merged/running.
    eligible = _select_eligible_prds(resolved_prds)

    started: list[BacklogActionResult] = []
    queued: list[str] = []
    skipped: list[str] = []

    for prd in eligible:
        if len(started) < free_slots:
            try:
                result = start_prd(
                    prd_path=prd.prd_path,
                    repo_id=repo_id,
                    contexts=contexts,
                    github_client=github_client,
                    supervisor=supervisor,
                    store=store,
                    runner_command=runner_command,
                    spawn_cwd=spawn_cwd,
                    process_runner=process_runner,
                )
                started.append(result)
                store.enqueue_backlog(
                    BacklogQueueEntry(
                        repo_id=repo_id,
                        prd_path=prd.prd_path,
                        status="running",
                        trigger="global",
                        started_at=_now_iso(),
                        finished_at=None,
                        error_detail=None,
                    )
                )
            except BacklogActionError as exc:
                skipped.append(f"{prd.prd_path}: {exc}")
                store.enqueue_backlog(
                    BacklogQueueEntry(
                        repo_id=repo_id,
                        prd_path=prd.prd_path,
                        status="failed",
                        trigger="global",
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        error_detail=str(exc),
                    )
                )
        else:
            store.enqueue_backlog(
                BacklogQueueEntry(
                    repo_id=repo_id,
                    prd_path=prd.prd_path,
                    status="queued",
                    trigger="global",
                    started_at=None,
                    finished_at=None,
                    error_detail=None,
                )
            )
            queued.append(prd.prd_path)

    return BacklogGlobalStartResult(
        started=started,
        queued=queued,
        skipped=skipped,
    )


def _parked_failure_detail(prd: BacklogPrd) -> str:
    """Return the reason recorded when a failed PRD is parked.

    Continuous scheduling parks failures instead of retrying them: a fast-lane
    failure is usually a requirement or environment problem, so blindly
    retrying burns tokens and re-pollutes the branch.
    """
    issue_reference = f"（Issue #{prd.issue_number}）" if prd.issue_number is not None else ""
    return (
        f"PRD {prd.prd_path}{issue_reference} 执行失败，已泊车等待人工处理；"
        "持续调度不会自动重试。"
    )


def _promote_prd_without_spawn(
    prd: BacklogPrd,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> int:
    """Idempotently put a PRD into ``agent/ready`` and return its Issue number.

    Unlike :func:`start_prd` this never spawns a runner process. The daemon's
    own Phase 2 consumes ``agent/ready``, so spawning here would put the same
    Issue on two execution tracks and race the daemon.

    The promotion is idempotent because the Issue link written back into the
    PRD file is the reuse key: when ``prd.issue_number`` is already known we only
    re-assert the label, and when it is empty the creation path refuses PRDs
    that already carry an Issue link.
    """
    if prd.issue_number is not None:
        _ensure_ready_label(prd, context, github_client)
        return prd.issue_number
    return _create_issue_for_prd(prd, context, github_client, process_runner)


def _assert_issue_not_running(
    prd: BacklogPrd,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
) -> None:
    """已在执行中的 Issue 不允许再入队（否则会打断在途执行并与 daemon 抢占）。

    判定读的是 GitHub 上的实时标签，而不是 PRD 文件里的回链状态：网页点按钮时
    daemon 可能已经刚领走这个 Issue。
    """
    if prd.issue_number is None:
        return
    live_labels = github_client.get_issue(prd.issue_number).labels
    running_label = context.config.labels.running
    if running_label in live_labels:
        raise BacklogEnqueueConflictError(
            f"Issue #{prd.issue_number} 正在执行中（标签 '{running_label}'），"
            "加入就绪会打断本轮执行。请等它结束或先停止本轮。"
        )


def enqueue_prd_ready(
    *,
    prd_path: str,
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    github_client: IGitHubClient,
    store: IBacklogStore,
    process_runner: IProcessRunner,
) -> BacklogActionResult:
    """把 PRD 加入就绪队列：建 Issue（若无）+ 打 ready 标签，**绝不启动 runner**。

    与 :func:`start_prd` 的区别只在最后一步：入队复用完全相同的建 Issue / 打标签
    路径（:func:`_promote_prd_without_spawn`），但不发出 ``kc run``。队列资格由此
    完全交给既有的 autopilot 开关决定，与 CLI 侧「ready 标签是资格不是命令」的
    语义一致。

    Args:
        prd_path: 仓库相对的 PRD 路径。
        repo_id: 目标仓库 ID。
        contexts: 已解析的 enabled 仓库上下文。
        github_client: 目标仓库的 GitHub 客户端。
        store: Backlog store（审计与生命周期账本）。
        process_runner: 建 Issue 时发布 PRD 用的进程执行器。

    Returns:
        状态为 ``READY`` 的动作结果，``detail`` 明确说明未启动 runner。

    Raises:
        BacklogActionError: PRD 缺失或建 Issue / 打标签失败。
        BacklogEnqueueConflictError: 关联 Issue 正在执行中。
    """
    context = _resolve_context(repo_id, contexts)
    prds = scan_backlog_prds(context.repo_path, include_archived=False).prds
    prd = next((item for item in prds if item.prd_path == prd_path), None)
    if prd is None:
        raise BacklogActionError(f"PRD not found or not pending: {prd_path}")

    try:
        _assert_issue_not_running(prd, context, github_client)
        issue_number = _promote_prd_without_spawn(prd, context, github_client, process_runner)
    except BacklogEnqueueConflictError as exc:
        _audit(
            store,
            action="enqueue_ready",
            repo_id=repo_id,
            prd_path=prd_path,
            issue_number=prd.issue_number,
            result="rejected",
            detail=str(exc),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        _audit(
            store,
            action="enqueue_ready",
            repo_id=repo_id,
            prd_path=prd_path,
            issue_number=prd.issue_number,
            result="error",
            detail=str(exc),
        )
        raise BacklogActionError(f"加入就绪失败: {exc}") from exc

    _record_lifecycle(
        store,
        event_type=LifecycleEventType.QUEUED,
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        trigger="console_enqueue",
        detail={"trigger": "manual", "spawned": False},
    )
    _audit(
        store,
        action="enqueue_ready",
        repo_id=repo_id,
        prd_path=prd_path,
        issue_number=issue_number,
        result="accepted",
        detail="Issue created/relabelled to ready; no runner spawned.",
    )
    return BacklogActionResult(
        prd_path=prd_path,
        issue_number=issue_number,
        state=BacklogPrdState.READY,
        detail="已进入就绪队列（未启动 runner）；是否被自动领取由 autopilot 决定。",
    )


def _upsert_queue_status(
    store: IBacklogStore,
    existing_entry: BacklogQueueEntry | None,
    *,
    repo_id: str,
    prd_path: str,
    status: str,
    started_at: str | None,
    finished_at: str | None,
    error_detail: str | None,
) -> None:
    """Move a PRD's queue entry to ``status``, creating it when absent."""
    if existing_entry is not None and existing_entry.entry_id is not None:
        store.update_backlog_queue_status(
            entry_id=existing_entry.entry_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            error_detail=error_detail,
        )
        return
    store.enqueue_backlog(
        BacklogQueueEntry(
            repo_id=repo_id,
            prd_path=prd_path,
            status=status,
            trigger="global",
            started_at=started_at,
            finished_at=finished_at,
            error_detail=error_detail,
        )
    )


def advance_backlog_queue(
    *,
    context: RepositoryRunContext,
    github_client: IGitHubClient,
    store: IBacklogStore,
    process_runner: IProcessRunner,
    dry_run: bool = False,
) -> BacklogAdvanceReport:
    """Run one continuous-scheduling pass for a repository.

    One pass performs three steps:

    1. **Reconcile** — every ``running``/``queued`` entry is checked against the
       live PRD state: merged or archived PRDs close as ``completed``; failed
       PRDs are parked as ``failed`` with a reason and are never retried.
    2. **Account slots** — ``free = max_parallel - running``. Only PRDs that are
       actually executing hold a slot; blocked / supervising / review PRDs keep
       their queue entry but consume none.
    3. **Promote and discover** — candidates are the still-``queued`` entries
       plus PRDs newly found in ``tasks/pending/``; they are ordered by the
       shared :func:`_select_eligible_prds` rule and promoted until the slots
       are full. Promotion means *Issue + ``agent/ready``*, not spawning a
       process, so the daemon's Phase 2 does the actual work.

    Args:
        context: Resolved repository context (config + path).
        github_client: GitHub client for the repository.
        store: Backlog queue / settings store.
        process_runner: Process runner used by the PRD publish path when a
            promotion has to create a new Issue.
        dry_run: When ``True`` the pass computes and reports the plan without
            writing to the store, the Issue tracker, or any PRD file.

    Returns:
        A :class:`BacklogAdvanceReport` describing what happened (or, in dry-run
        mode, what would happen).
    """
    repo_id = context.repo_id

    # Archived PRDs are scanned too: "the PRD moved to tasks/archive/" is the
    # terminal signal that lets its queue entry be closed.
    scanned_prds = scan_backlog_prds(context.repo_path, include_archived=True).prds
    block_reasons = evaluate_backlog_dependencies(
        scanned_prds,
        github_client=github_client,
    )
    resolved_prds = resolve_backlog_states(
        scanned_prds,
        github_client=github_client,
        config=context.config,
        block_reasons=block_reasons,
    )
    resolved_by_path = {prd.prd_path: prd for prd in resolved_prds}

    settings = get_or_create_backlog_settings(store, repo_id)
    max_parallel = max(1, settings.max_parallel)

    queue_entries = store.list_backlog_queue(repo_id=repo_id)
    entry_by_path = {entry.prd_path: entry for entry in queue_entries}
    tracked_paths = set(entry_by_path)

    # ── Step 1: reconcile finished / failed queue entries ──────────────────
    reconciled_completed: list[str] = []
    reconciled_failed: list[str] = []
    for entry in queue_entries:
        if entry.status not in _ACTIVE_QUEUE_STATUSES or entry.entry_id is None:
            continue
        prd = resolved_by_path.get(entry.prd_path)
        if prd is None:
            # The PRD file vanished (renamed or deleted). That is ambiguous, so
            # leave the entry alone instead of releasing a slot on a guess.
            _logger.info("Queue entry for missing PRD left untouched: %s", entry.prd_path)
            continue
        if prd.state in _COMPLETED_PRD_STATES:
            reconciled_completed.append(entry.prd_path)
            if not dry_run:
                store.update_backlog_queue_status(
                    entry_id=entry.entry_id,
                    status="completed",
                    finished_at=_now_iso(),
                )
                # 归档 / 合并是 backlog 调度侧能可靠观测到的终态：PRD 已离开
                # pending（归档）或已标记合并，属于既有 workflow 的真实事实，
                # 不是从 label 猜历史。occurred_at 记观测时刻，不伪造精确合并时间。
                record_lifecycle_terminal(
                    store=store,
                    repo_id=repo_id,
                    prd_path=entry.prd_path,
                    issue_number=prd.issue_number,
                    trigger="autopilot_backlog",
                    event_type=(
                        LifecycleEventType.ARCHIVED
                        if prd.state is BacklogPrdState.ARCHIVED
                        else LifecycleEventType.MERGED
                    ),
                    outcome="completed",
                    actor="backlog",
                    detail={"observed_state": prd.state.value},
                )
        elif prd.state is BacklogPrdState.FAILED:
            reconciled_failed.append(entry.prd_path)
            if not dry_run:
                store.update_backlog_queue_status(
                    entry_id=entry.entry_id,
                    status="failed",
                    finished_at=_now_iso(),
                    error_detail=_parked_failure_detail(prd),
                )

    # ── Step 2: slot accounting (RUNNING-only, matching the console) ───────
    running_count = sum(1 for prd in resolved_prds if prd.state is BacklogPrdState.RUNNING)
    free_slots = max(0, max_parallel - running_count)

    # ── Step 3: candidate set, then promote up to the free slots ───────────
    candidate_by_path: dict[str, BacklogPrd] = {}
    for entry in queue_entries:
        if entry.status != "queued":
            continue
        queued_prd = resolved_by_path.get(entry.prd_path)
        if queued_prd is not None:
            candidate_by_path.setdefault(queued_prd.prd_path, queued_prd)
    for prd in resolved_prds:
        if prd.prd_path in tracked_paths:
            continue
        candidate_by_path.setdefault(prd.prd_path, prd)

    eligible = _select_eligible_prds(list(candidate_by_path.values()))

    started: list[BacklogActionResult] = []
    queued: list[str] = []
    skipped: list[str] = []

    for prd in eligible:
        if len(started) < free_slots:
            if dry_run:
                started.append(
                    BacklogActionResult(
                        prd_path=prd.prd_path,
                        issue_number=prd.issue_number,
                        state=BacklogPrdState.READY,
                        detail="dry-run：本轮将晋升该 PRD（不落任何变更）。",
                    )
                )
                continue
            try:
                issue_number = _promote_prd_without_spawn(
                    prd, context, github_client, process_runner
                )
            except Exception as exc:  # noqa: BLE001 - one bad PRD must not stall the queue.
                _logger.error("Failed to promote PRD %s: %s", prd.prd_path, exc)
                skipped.append(f"{prd.prd_path}: {exc}")
                _upsert_queue_status(
                    store,
                    entry_by_path.get(prd.prd_path),
                    repo_id=repo_id,
                    prd_path=prd.prd_path,
                    status="failed",
                    started_at=None,
                    finished_at=_now_iso(),
                    error_detail=str(exc),
                )
                continue
            _upsert_queue_status(
                store,
                entry_by_path.get(prd.prd_path),
                repo_id=repo_id,
                prd_path=prd.prd_path,
                status="running",
                started_at=_now_iso(),
                finished_at=None,
                error_detail=None,
            )
            _record_lifecycle(
                store,
                event_type=LifecycleEventType.QUEUED,
                repo_id=repo_id,
                prd_path=prd.prd_path,
                issue_number=issue_number,
                trigger="autopilot_backlog",
                detail={"trigger": "global"},
            )
            started.append(
                BacklogActionResult(
                    prd_path=prd.prd_path,
                    issue_number=issue_number,
                    state=BacklogPrdState.READY,
                    detail="已建/复用 Issue 并打上 ready 标签，交由 daemon Phase 2 消费。",
                )
            )
            continue

        # No slot left: hold the PRD as queued for a later pass.
        queued.append(prd.prd_path)
        if prd.prd_path in tracked_paths:
            continue
        if not dry_run:
            _upsert_queue_status(
                store,
                None,
                repo_id=repo_id,
                prd_path=prd.prd_path,
                status="queued",
                started_at=None,
                finished_at=None,
                error_detail=None,
            )

    return BacklogAdvanceReport(
        repo_id=repo_id,
        dry_run=dry_run,
        max_parallel=max_parallel,
        free_slots=free_slots,
        reconciled_completed=reconciled_completed,
        reconciled_failed=reconciled_failed,
        started=started,
        queued=queued,
        skipped=skipped,
    )


def stop_global_backlog(*, repo_id: str, store: IBacklogStore) -> dict:
    """Clear the backlog queue for a repository.

    Already-running PRDs are not stopped; only queued entries are removed.
    """
    store.clear_backlog_queue(repo_id=repo_id)
    _audit(
        store,
        action="stop_global",
        repo_id=repo_id,
        prd_path="",
        issue_number=None,
        result="accepted",
        detail="Cleared backlog queue.",
    )
    return {"stopped": True, "repo_id": repo_id}
