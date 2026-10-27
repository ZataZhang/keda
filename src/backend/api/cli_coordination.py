"""CLI 与 API 运行入口共用的机器状态和认领租约启动器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from backend.api.version_info import resolve_keda_version
from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.use_cases.agent_runner_factory import (
    create_github_client,
    create_machine_identity_store,
)
from backend.core.use_cases.coordination_heartbeat import (
    CoordinationHeartbeat,
    acquire_coordination_heartbeat,
)

_LEASE_GITHUB_REQUEST_TIMEOUT_SECONDS = 5


def start_coordination_heartbeat(
    *,
    contexts: Sequence[RepositoryRunContext],
    github_client_factory: Callable[[Path], IGitHubClient],
    process_runner: IProcessRunner,
    role: str,
) -> CoordinationHeartbeat:
    """为执行入口建立本机身份、状态评论与可续租 GitHub 客户端。"""
    identity_store = create_machine_identity_store()
    heartbeat = CoordinationHeartbeat(
        contexts=list(contexts),
        machine_identity=identity_store.load_or_create(),
        version=resolve_keda_version(),
        role=role,
        github_client_factory=github_client_factory,
        lease_client_factory=lambda repo_path: _create_lease_client(
            repo_path,
            process_runner=process_runner,
        ),
        slot_lock_factory=identity_store.presence_slot_lock,
    )
    return acquire_coordination_heartbeat(heartbeat)


def _create_lease_client(
    repo_path: Path,
    *,
    process_runner: IProcessRunner,
) -> IGitHubClient:
    """创建计入预算但不受预算暂停门禁的租约客户端。"""
    return create_github_client(
        repo_path,
        process_runner,
        bypass_budget_pause=True,
        request_timeout_seconds=_LEASE_GITHUB_REQUEST_TIMEOUT_SECONDS,
    )


__all__ = ["start_coordination_heartbeat"]
