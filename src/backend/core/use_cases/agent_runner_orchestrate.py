"""Agent runner orchestration — high-level issue processing flow.

本模块是 Agent Runner 的核心编排层，负责管理 Issue 的完整生命周期流程：

1. **轮询发现** — 从 GitHub 发现 ready/running 状态的 Issue
2. **工作树准备** — 为每个 Issue 创建或复用 git worktree
3. **Agent 执行** — 调用 AI Agent 实现 Issue（可选，视恢复路径而定）
4. **代码评审** — push 之后、PR 之前运行 pre-PR review
5. **发布** — 将代码推送到远程并创建 Draft PR
6. **事后监督** — 可选的 PR 后监督循环（修复冲突、重新构建等）

Issue 有三条处理路径：
- `_process_ready_issue`: 新 Issue → 完整 Agent 执行 → 评审 → 发布
- `_process_running_rework`: 已运行 Issue → 检测到 rework 标记 → 执行修复 → 评审
- `_process_running_publish_recovery`: 已运行 Issue → 有本地 commit → 直接评审 → 发布
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import (
    AttemptRecord,
    IRunHistoryStore,
)
from backend.core.shared.interfaces.runner_live_view import (
    IRunnerLiveView,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AttemptResult,
    IssueSummary,
    ReviewEventMarker,
)
from backend.core.use_cases.agent_runner_events import (
    parse_latest_pending_rework_marker,
)
from backend.core.use_cases.agent_runner_blocked_claim import (
    BlockedWorktreeClaimedError,
)
from backend.core.use_cases.agent_runner_publication import (
    _finish_existing_commit_publication,
    _finish_implementation_publication,
    _reuse_existing_local_commit,
)
from backend.core.use_cases.agent_runner_run_history import (
    ATTEMPT_HISTORY_MAX_ROWS,
    append_run_record,
    load_issue_attempt_trail,
)
from backend.core.use_cases.run_agent_once import (
    choose_agent,
    create_or_reuse_worktree,
    get_head_sha,
    resolve_agent_fallback_order,
)
from backend.core.use_cases.agent_runner_failure import (
    AgentUnavailableError,
    ForbiddenBlockedError,
    MaxRetriesExceededError,
    ProviderCapacityError,
    UnrecoverableError,
    format_attempt_history,
)
from backend.core.use_cases.agent_runner_failure_marking import (
    _mark_issue_blocked,
    _mark_issue_failed,
)
from backend.core.use_cases.agent_runner_issue_handlers import (
    _guard_blocked_issue_has_resolution,
    _process_blocked_resolution,
    _process_ready_issue,
    _process_running_publish_recovery,
    _process_running_rework,
)
from backend.core.use_cases.agent_runner_worktree_probe import (
    _has_existing_local_commit_ready_for_publish,
    _worktree_needs_rebase_recovery,
)
from backend.core.use_cases.agent_runner_worktree_probe import (
    _find_worktree_path_for_issue,
)

# 运行时依赖注入面。以下名字在本模块内没有直接调用点，但必须留在命名空间里：
# ``_orchestration_runtime_module`` 按名取用它们，注入派发层
# （``agent_runner_orchestration_runtime``）与 Issue 状态处理器
# （``agent_runner_issue_handlers``）。这两处模块内部按各自
# ``RUNTIME_DEPENDENCY_NAMES`` 声明需要哪些名字。测试与集成方替换这些依赖时，
# 补丁点仍然是本模块——单一中枢，避免同一处替换要打两个模块。
# **不要因为「本模块内看起来没用到」而删除这些导入。**
from backend.core.use_cases.agent_runner_blocked_claim import (
    _acquire_blocked_claim_lock,
    _release_blocked_claim_lock,
)
from backend.core.use_cases.agent_runner_git import (
    get_current_branch,
)
from backend.core.use_cases.agent_runner_supervisor import (
    _run_supervisor_with_repair_loop,
)
from backend.core.use_cases.agent_runner_worktree_branch import (
    _ensure_worktree_branch,
)
from backend.core.use_cases.pr_supervisor import (
    execute_rebase,
)

_logger = logging.getLogger(__name__)

# 本列表同时承担两个职责：对外导出，以及声明**运行时依赖同步源**。
# ``_orchestration_runtime_module`` 通过 ``globals()`` 按名取用这些私有名，
# 把它们注入 ``agent_runner_orchestration_runtime`` 与
# ``agent_runner_issue_handlers``，从而让测试与集成方继续可以在本模块上
# 替换这些依赖（既有 monkeypatch 接缝位置不变）。因此其中若干名字在本模块
# 内没有直接调用点，仍必须保留在命名空间里。
__all__ = [
    "BlockedWorktreeClaimedError",
    "_acquire_blocked_claim_lock",
    "_ensure_worktree_branch",
    "_find_worktree_path_for_issue",
    "_finish_existing_commit_publication",
    "_finish_implementation_publication",
    "_guard_blocked_issue_has_resolution",
    "_has_existing_local_commit_ready_for_publish",
    "_mark_issue_blocked",
    "_mark_issue_failed",
    "_process_blocked_resolution",
    "_process_ready_issue",
    "_process_running_publish_recovery",
    "_process_running_rework",
    "_release_blocked_claim_lock",
    "_reuse_existing_local_commit",
    "_run_supervisor_with_repair_loop",
    "_worktree_needs_rebase_recovery",
    "choose_agent",
    "create_or_reuse_worktree",
    "execute_rebase",
    "get_current_branch",
    "get_head_sha",
    "process_prd_rework_issues",
    "refresh_runtime_dependencies",
    "run_issue_with_agent_fallback",
    "run_once",
]

# Scan past dependency-blocked ready Issues without letting them consume the
# per-pass processing quota. ``max_issues`` still caps actual claims.
_READY_DISCOVERY_LIMIT = 100


def _orchestration_runtime_module():
    """加载实现模块并同步可被测试或集成方替换的运行时依赖。"""
    from backend.core.use_cases import agent_runner_issue_handlers as handlers_module
    from backend.core.use_cases import agent_runner_orchestration_runtime as module

    for dependency_name in module.RUNTIME_DEPENDENCY_NAMES:
        setattr(module, dependency_name, globals()[dependency_name])
    for dependency_name in handlers_module.RUNTIME_DEPENDENCY_NAMES:
        setattr(handlers_module, dependency_name, globals()[dependency_name])
    return module


def refresh_runtime_dependencies() -> None:
    """把本模块命名空间里的运行时依赖同步给按名解析它们的实现模块。

    ``agent_runner_issue_handlers`` 与 ``agent_runner_orchestration_runtime`` 都在
    各自模块的命名空间里解析这些协作者（见各自的 ``RUNTIME_DEPENDENCY_NAMES``）。
    因此测试与集成方在**本模块**上替换依赖后，必须先经过一次同步，替换才会对
    这两个模块生效。

    派发路径（``run_once`` / ``process_prd_rework_issues`` / ``_process_single_issue``）
    会经由 :func:`_orchestration_runtime_module` 自动同步；**其它直接调用状态处理器
    的入口必须在调用前显式调用本函数**，否则那些补丁会静默失效——这与拆分前
    "补丁打在本模块即生效"的语义不一致。当前已知的这类入口：
    :func:`backend.core.use_cases.blocked_continue.blocked_continue_issue`
    （``iar blocked-continue``）。新增同类入口时请一并补上，并考虑是否需要覆盖它的探针。
    """
    _orchestration_runtime_module()


def process_prd_rework_issues(
    *,
    repo_path: Path,
    config: AppConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    max_issues: int = 1,
) -> None:
    """处理标记为 PRD rework 的 Issue。"""
    module = _orchestration_runtime_module()
    module.process_prd_rework_issues(
        module.PrdReworkRequest(
            repo_path=repo_path,
            config=config,
            github_client=github_client,
            process_runner=process_runner,
            content_generator=content_generator,
            max_issues=max_issues,
        )
    )


def _has_rework_intent(
    issue: IssueSummary,
    github_client: IGitHubClient,
) -> tuple[bool, ReviewEventMarker | None]:
    """检测 Issue 是否包含事后修复请求标记。

    通过解析 Issue 的评论列表，查找尚未被完成事件消费的
    post_pr_rework_requested 事件标记。该标记在监督者请求修复时写入，
    后续 supervisor 观察类 marker 不能掩盖仍待执行的 repair/rebase。

    Args:
        issue: Issue 对象
        github_client: GitHub 客户端

    Returns:
        (是否存在 rework 意图, 事件标记对象)
    """
    comments = github_client.list_issue_comments(issue.number)
    marker = parse_latest_pending_rework_marker(comments)
    if marker is not None:
        return True, marker
    return False, None


def _guard_running_issue_is_rework(
    issue: IssueSummary,
    config: AppConfig,
    github_client: IGitHubClient,
) -> tuple[bool, ReviewEventMarker | None]:
    """判断一个 running 状态的 Issue 是否符合 rework 资格。

    资格条件：
    1. 有 post_pr_rework_requested 事件标记
    2. 标记中包含有效的 PR 分支名
    3. 该分支在 GitHub 上存在对应的 open PR
    4. 标记的 head_sha 与 open PR 当前 head 一致（避免修错 head）

    Args:
        issue: Issue 对象
        config: 应用配置
        github_client: GitHub 客户端

    Returns:
        (是否符合 rework 资格, 事件标记对象)
    """
    has_rework, marker = _has_rework_intent(issue, github_client)
    if not has_rework or marker is None:
        return False, None
    pr_branch = marker.pr_branch
    if pr_branch is None:
        return False, None
    pr_context = github_client.get_pull_request_context(pr_branch)
    if pr_context is None:
        return False, None
    if marker.head_sha and marker.head_sha != pr_context.head_sha:
        _logger.warning(
            "Issue #%d rework marker head %s does not match open PR head %s; "
            "ignoring stale marker.",
            issue.number,
            marker.head_sha,
            pr_context.head_sha,
        )
        return False, None
    return True, marker


def _stamp_attempts_with_agent(
    attempts: list[AttemptResult],
    agent: str,
) -> list[AttemptResult]:
    """Return attempts labeled with the agent that produced them.

    Attempts recorded inside a single agent's run already carry the agent
    name; this helper remains for backward compatibility and cross-agent
    fallback merging. Attempts that already carry an agent label are left
    untouched.

    Args:
        attempts: Attempt history from one agent's run.
        agent: Agent name to stamp onto unlabeled attempts.

    Returns:
        A new list of attempts with the agent stamped.
    """
    return [attempt if attempt.agent else replace(attempt, agent=agent) for attempt in attempts]


_ATTEMPT_HISTORY_MARKER = "<!-- iar-attempt-history -->"
_ATTEMPT_HISTORY_TITLE = "### Attempt History"


def _build_attempt_history_comment(
    attempt_results: list[AttemptResult],
    *,
    older_omitted: bool = False,
) -> str:
    """Build a GitHub comment body that carries the attempt history table.

    Args:
        attempt_results: Attempts to render, oldest first.
        older_omitted: Whether older attempts were dropped by the row cap, in
            which case the comment says so instead of silently truncating.
    """
    history_table = format_attempt_history(attempt_results, include_title=False)
    if not history_table:
        history_table = "_(No attempts recorded yet.)_"
    body_lines = [_ATTEMPT_HISTORY_MARKER, f"{_ATTEMPT_HISTORY_TITLE} (live)", ""]
    if older_omitted:
        body_lines.extend(
            [
                f"_(Older attempts omitted; showing the latest {ATTEMPT_HISTORY_MAX_ROWS}.)_",
                "",
            ]
        )
    body_lines.append(history_table)
    return "\n".join(body_lines)


def _persist_attempt_result(
    *,
    result: AttemptResult,
    attempt_results: list[AttemptResult],
    repo_id: str,
    issue_number: int,
    github_client: IGitHubClient,
    run_history_store: IRunHistoryStore | None,
) -> None:
    """Persist one attempt to SQLite and update the GitHub running comment.

    This is the incremental persistence callback wired into
    :func:`run_agent_until_committed`. Failures are logged and swallowed so the
    runner state machine is never blocked by the side-channel storage.

    The comment is rendered from the stored trail rather than from
    ``attempt_results``, because the in-memory list restarts at attempt 1 on
    every agent switch and every re-claim — rendering from it would edit the
    single marker comment down to just the current agent's rows and drop
    everything an earlier agent recorded. ``attempt_results`` stays the fallback
    for runs without a store (and for the rare case where the append above
    failed but the read succeeded, which the next attempt's update repairs).
    """
    if run_history_store is not None:
        try:
            run_history_store.append_attempt(
                AttemptRecord(
                    repo_id=repo_id,
                    issue_number=issue_number,
                    agent=result.agent,
                    attempt_number=result.attempt_number,
                    failure_type=result.failure_type.value,
                    recovered=result.recovered,
                    detail=result.detail,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    duration_seconds=result.duration_seconds,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel must not break runs
            _logger.warning(
                "Failed to append attempt record for Issue #%d",
                issue_number,
                exc_info=True,
            )

    stored_trail = load_issue_attempt_trail(
        run_history_store=run_history_store,
        repo_id=repo_id,
        issue_number=issue_number,
    )
    try:
        entries = github_client.list_issue_comment_entries(issue_number)
        comment_id: int | None = None
        for existing_id, body in entries:
            if _ATTEMPT_HISTORY_MARKER in body:
                comment_id = existing_id
                break
        comment_body = _build_attempt_history_comment(
            stored_trail.attempts or attempt_results,
            older_omitted=stored_trail.older_omitted,
        )
        if comment_id is not None:
            github_client.edit_issue_comment(comment_id, comment_body)
        else:
            github_client.comment_issue(issue_number, comment_body)
    except Exception:  # noqa: BLE001 - side-channel must not break runs
        _logger.warning(
            "Failed to update GitHub attempt history for Issue #%d",
            issue_number,
            exc_info=True,
        )


def run_issue_with_agent_fallback(
    *,
    issue: IssueSummary,
    config: AppConfig,
    agent: str,
    process_for_agent: Callable[..., None],
    on_attempt_recorded: Callable[[AttemptResult, list[AttemptResult]], None] | None = None,
) -> str:
    """Process an Issue across the configured agent fallback chain.

    Level 2 of the escalation ladder. ``process_for_agent`` is invoked with a
    keyword ``agent`` argument for each candidate agent resolved by
    :func:`resolve_agent_fallback_order`, capped at ``max_agent_switches``
    switches. The chain advances to the next agent when an agent exhausts its
    recovery budget (:class:`MaxRetriesExceededError`) or hits a provider
    capacity limit (:class:`ProviderCapacityError`), and skips an agent whose
    CLI is unavailable (:class:`AgentUnavailableError`). Unrecoverable and
    forbidden-path failures are re-raised immediately because every agent would
    hit the same wall.

    When the chain is exhausted, the merged (agent-stamped) attempt history is
    raised as a :class:`MaxRetriesExceededError` so the failure comment shows
    every agent that was tried. With no fallback configured the chain contains
    only the primary agent, so behavior matches single-agent runs.

    Args:
        issue: Issue being processed.
        config: Agent Runner configuration.
        agent: The ``--agent`` override (``"auto"`` routes by label).
        process_for_agent: Callable accepting ``agent=<name>`` that runs the
            full implement → review → publish pipeline for one agent.

    Returns:
        The agent name that completed the Issue.

    Raises:
        UnrecoverableError: A security/branch violation that no agent can fix.
        ForbiddenBlockedError: Forbidden paths require human intervention.
        MaxRetriesExceededError: Every candidate agent failed.
        AgentUnavailableError: Every candidate agent's CLI was unavailable.
    """
    fallback_order = resolve_agent_fallback_order(issue, config, agent)
    max_switches = max(0, config.runner.max_agent_switches)
    candidate_agents = fallback_order[: max_switches + 1]
    combined_attempts: list[AttemptResult] = []
    last_switch_exc: Exception | None = None
    for candidate_index, candidate_agent in enumerate(candidate_agents):
        is_last_candidate = candidate_index == len(candidate_agents) - 1
        try:
            process_for_agent(
                agent=candidate_agent,
                on_attempt_recorded=on_attempt_recorded,
            )
            return candidate_agent
        except (UnrecoverableError, ForbiddenBlockedError):
            # Every agent would hit the same wall; do not switch.
            raise
        except AgentUnavailableError as exc:
            last_switch_exc = exc
            _logger.warning(
                "Issue #%d: agent '%s' is unavailable; trying next candidate.",
                issue.number,
                candidate_agent,
            )
            continue
        except (ProviderCapacityError, MaxRetriesExceededError) as exc:
            combined_attempts.extend(
                _stamp_attempts_with_agent(
                    getattr(exc, "attempt_results", None) or [],
                    candidate_agent,
                )
            )
            last_switch_exc = exc
            if is_last_candidate:
                break
            _logger.warning(
                "Issue #%d: agent '%s' failed with %s; switching to next agent.",
                issue.number,
                candidate_agent,
                type(exc).__name__,
            )
            continue

    if last_switch_exc is None:
        raise RuntimeError(f"No agent candidates available for Issue #{issue.number}.")
    if combined_attempts:
        # Carry the merged, agent-stamped attempt history while preserving the
        # last agent's root cause (e.g. the verification error) so the failure
        # comment still surfaces it instead of a duplicated wrapper message.
        raise MaxRetriesExceededError(combined_attempts) from last_switch_exc.__cause__
    raise last_switch_exc


_RUN_HISTORY_LOCK = threading.Lock()


def _append_run_record_locked(**kwargs: object) -> None:
    """Thread-safe ``append_run_record`` for the parallel processing path.

    Run-history storage (e.g. SQLite) is not safe for concurrent writers, so
    serialize appends. Uncontended in the default sequential path.
    """
    with _RUN_HISTORY_LOCK:
        append_run_record(**kwargs)


def _process_single_issue(
    issue: IssueSummary,
    issue_kind: str,
    **kwargs: object,
) -> int:
    """处理单个已发现 Issue。"""
    module = _orchestration_runtime_module()
    return module._process_single_issue(issue, issue_kind, **kwargs)


def run_once(
    *,
    repo_path: Path,
    config: AppConfig,
    dry_run: bool,
    agent: str,
    max_issues: int,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    content_generator: IContentGenerator | None = None,
    run_history_store: IRunHistoryStore | None = None,
    run_trigger: str = "cli_run",
    repo_id: str | None = None,
    concurrency: int = 1,
    output_view: IRunnerLiveView | None = None,
) -> int:
    """执行一次 Agent Runner 轮询。"""
    module = _orchestration_runtime_module()
    return module.run_once(
        module.RunOnceRequest(
            repo_path=repo_path,
            config=config,
            dry_run=dry_run,
            agent=agent,
            max_issues=max_issues,
            github_client=github_client,
            process_runner=process_runner,
            content_generator=content_generator,
            run_history_store=run_history_store,
            run_trigger=run_trigger,
            repo_id=repo_id,
            concurrency=concurrency,
            output_view=output_view,
        )
    )
