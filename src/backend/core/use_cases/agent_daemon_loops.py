"""普通 agent daemon 对托管 loop 的调度阶段。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IAgentTranscriptRunner,
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.loop_scheduler import ILoopClock, ILoopStateStore
from backend.core.shared.interfaces.runner_console import IBacklogStore, IRunHistoryStore
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import LabelConfig, RepositoryRunContext
from backend.core.shared.models.loop import LoopManager
from backend.core.use_cases.loop_fire import fire_loop
from backend.core.use_cases.loop_scheduler import list_due_tasks

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AgentDaemonLoopRequest:
    """普通 daemon 主循环的内部请求对象。"""

    contexts: list[RepositoryRunContext]
    interval: int
    agent: str
    max_issues: int
    process_runner: IProcessRunner
    github_client_factory: Callable[[Path], IGitHubClient]
    content_generator_factory: Callable[[Path], IContentGenerator] | None
    run_history_store: IRunHistoryStore | None
    run_trigger: str
    max_prd_issues: int
    transcript_runner_factory: Callable[[Path], IAgentTranscriptRunner] | None
    max_deliberation_issues: int
    concurrency: int | None
    output_view: IRunnerLiveView | None
    reconcile_stale_attempts: bool
    reclaim_ttl_seconds: int | None
    backlog_store_factory: Callable[[], IBacklogStore] | None
    autopilot_override: bool | None
    loop_state_store_factory: Callable[[], ILoopStateStore] | None
    loop_clock: ILoopClock | None


@dataclass(frozen=True)
class AgentDaemonLoopPassRequest:
    """普通 daemon 单仓库 loop 调度阶段所需的依赖。"""

    repo_id: str
    repo_path: Path
    state_store: ILoopStateStore
    github_client: IGitHubClient
    process_runner: IProcessRunner
    clock: ILoopClock
    content_generator: IContentGenerator | None = None
    labels_config: LabelConfig | None = None


def run_agent_daemon_loop_pass(
    request: AgentDaemonLoopPassRequest,
) -> list[tuple[str, str]]:
    """执行一个仓库的 agent-daemon loop 调度 pass。

    Args:
        request: 包含仓库目标、状态存储、GitHub 客户端和时钟的请求对象。

    Returns:
        ``(loop_id, status)`` 结果列表；没有到期 loop 时返回空列表。

    Raises:
        Exception: 状态文件无法读取等阶段级错误会交给调用方处理；单个
            loop 的 fire 错误会记录并转成该 loop 的 ``error:`` 状态。
    """
    request.state_store.load()
    due_tasks = [
        task
        for task in list_due_tasks(request.state_store.list_tasks(), clock=request.clock)
        if task.repo_id == request.repo_id and task.managed_by == LoopManager.AGENT_DAEMON.value
    ]
    outcomes: list[tuple[str, str]] = []
    for task in due_tasks:
        try:
            fire_result = fire_loop(
                task,
                repo_path=request.repo_path,
                github_client=request.github_client,
                process_runner=request.process_runner,
                state_store=request.state_store,
                clock=request.clock,
                content_generator=request.content_generator,
                labels_config=request.labels_config,
                dry_run=False,
            )
            outcomes.append((task.id, fire_result.status.value))
        except Exception as exc:  # noqa: BLE001 - one loop must not stop the daemon.
            _logger.error(
                "Agent-daemon loop '%s' fire failed and will be retried next pass: %s",
                task.id,
                exc,
            )
            outcomes.append((task.id, f"error: {exc}"))
    return outcomes
