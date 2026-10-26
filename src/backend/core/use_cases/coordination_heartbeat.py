"""进程级 GitHub 状态上报、认领续期与租约失效隔离。"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ContextManager

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.shared.models.machine_identity import MachineIdentity
from backend.core.use_cases.agent_runner_claim_lease import (
    assert_claim_still_owned,
    with_claim_lease,
)
from backend.core.use_cases.machine_presence import (
    PRESENCE_UPDATE_INTERVAL_SECONDS,
    update_presence_comment,
)

if TYPE_CHECKING:
    from backend.core.use_cases.agent_runner_claim_arbitration import ClaimBid

_logger = logging.getLogger(__name__)
_ACTIVE_HEARTBEAT_LOCK = threading.RLock()
_ACTIVE_HEARTBEAT: "CoordinationHeartbeat | None" = None
_ACTIVE_HEARTBEAT_REFERENCE_COUNT = 0
_MINIMUM_LEASE_TTL_SECONDS = 120


@dataclass
class _TrackedClaim:
    """一条由本进程持有、需要续期的认领。"""

    repo_id: str
    repo_path: Path
    config: AppConfig
    github_client: IGitHubClient
    issue_number: int
    comment_id: int
    body: str
    ttl_seconds: int
    selected_agent: str
    started_at: datetime
    last_renewed_monotonic: float
    last_renewal_attempt_monotonic: float
    attempt_keys: dict[str, IProcessRunner] = field(default_factory=dict)
    lost: bool = False


class CoordinationHeartbeat:
    """维护一个 kc 进程的状态评论与租约评论。"""

    def __init__(
        self,
        *,
        contexts: list[RepositoryRunContext],
        machine_identity: MachineIdentity,
        version: str,
        role: str,
        github_client_factory: Callable[[Path], IGitHubClient],
        lease_client_factory: Callable[[Path], IGitHubClient],
        slot_lock_factory: Callable[[], ContextManager[object]] | None = None,
        host: str | None = None,
        process_id: int | None = None,
        process_started_at: datetime | None = None,
        renew_interval_scale: float = 1.0,
    ) -> None:
        self._contexts = {context.repo_id: context for context in contexts}
        self._contexts_by_path = {context.repo_path.resolve(): context for context in contexts}
        self._repo_id_by_config_id = {id(context.config): context.repo_id for context in contexts}
        self._machine_identity = machine_identity
        self._version = version
        self._role = role
        self._github_client_factory = github_client_factory
        self._lease_client_factory = lease_client_factory
        self._slot_lock_factory = slot_lock_factory
        self._host = host or socket.gethostname()
        self._process_id = process_id or os.getpid()
        startup_time = (process_started_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._slot = f"{self._process_id}@{startup_time.timestamp():.6f}"
        self._renew_interval_scale = max(0.01, renew_interval_scale)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._claims: dict[tuple[str, int], _TrackedClaim] = {}
        self._presence_comment_ids: dict[str, int] = {}
        self._last_presence_refresh: dict[str, float] = {}

    @property
    def machine_id(self) -> str:
        """当前持久机器编号。"""
        return self._machine_identity.machine_id

    def start(self) -> None:
        """发布首次在线状态并启动租约和状态评论后台线程。"""
        global _ACTIVE_HEARTBEAT, _ACTIVE_HEARTBEAT_REFERENCE_COUNT
        with _ACTIVE_HEARTBEAT_LOCK:
            if _ACTIVE_HEARTBEAT is not None:
                raise RuntimeError("A coordination heartbeat is already active in this process.")
            _ACTIVE_HEARTBEAT = self
            _ACTIVE_HEARTBEAT_REFERENCE_COUNT = 1
        for repo_id, context in self._contexts.items():
            if context.config.daemon.presence_enabled:
                self._refresh_presence(repo_id, process_state="online")
        self._thread = threading.Thread(
            target=self._run,
            name="kc-coordination-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """释放一个入口引用；最后一个入口退出时标记已退出并停止线程。"""
        global _ACTIVE_HEARTBEAT, _ACTIVE_HEARTBEAT_REFERENCE_COUNT
        with _ACTIVE_HEARTBEAT_LOCK:
            if _ACTIVE_HEARTBEAT is not self:
                return
            if _ACTIVE_HEARTBEAT_REFERENCE_COUNT > 1:
                _ACTIVE_HEARTBEAT_REFERENCE_COUNT -= 1
                return
            _ACTIVE_HEARTBEAT_REFERENCE_COUNT = 0
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            for repo_id, context in self._contexts.items():
                if context.config.daemon.presence_enabled:
                    self._refresh_presence(repo_id, process_state="exited")
            _ACTIVE_HEARTBEAT = None

    def retain_contexts(
        self,
        *,
        contexts: list[RepositoryRunContext],
        machine_identity: MachineIdentity,
        github_client_factory: Callable[[Path], IGitHubClient],
        lease_client_factory: Callable[[Path], IGitHubClient],
        slot_lock_factory: Callable[[], ContextManager[object]] | None,
    ) -> None:
        """把并发入口的仓库上下文并入当前进程 heartbeat。"""
        global _ACTIVE_HEARTBEAT_REFERENCE_COUNT
        with _ACTIVE_HEARTBEAT_LOCK:
            if _ACTIVE_HEARTBEAT is not self or self._stop_event.is_set():
                raise RuntimeError("Cannot retain a stopped coordination heartbeat.")
            if machine_identity.machine_id != self.machine_id:
                raise RuntimeError("A process cannot use multiple machine identities.")
            with self._lock:
                for context in contexts:
                    existing_context = self._contexts.get(context.repo_id)
                    if (
                        existing_context is not None
                        and existing_context.repo_path.resolve() != context.repo_path.resolve()
                    ):
                        raise RuntimeError(
                            f"Repository id '{context.repo_id}' refers to multiple paths."
                        )
                    self._contexts[context.repo_id] = context
                    self._contexts_by_path[context.repo_path.resolve()] = context
                    self._repo_id_by_config_id[id(context.config)] = context.repo_id
                self._github_client_factory = github_client_factory
                self._lease_client_factory = lease_client_factory
                self._slot_lock_factory = slot_lock_factory
            _ACTIVE_HEARTBEAT_REFERENCE_COUNT += 1

    def prepare_claim_comment(
        self,
        *,
        base_comment: str,
        config: AppConfig,
    ) -> tuple[str, int] | None:
        """给新认领附加机器身份与声明租约；未启用进程服务时保持兼容。"""
        if self._thread is None or self._stop_event.is_set():
            return None
        lease_ttl_seconds = config.daemon.lease_ttl_seconds
        if lease_ttl_seconds < _MINIMUM_LEASE_TTL_SECONDS:
            _logger.warning(
                "Configured lease_ttl_seconds=%d is below %d; using the minimum.",
                lease_ttl_seconds,
                _MINIMUM_LEASE_TTL_SECONDS,
            )
            lease_ttl_seconds = _MINIMUM_LEASE_TTL_SECONDS
        renewed_at = datetime.now(timezone.utc)
        return (
            with_claim_lease(
                base_comment,
                machine_id=self.machine_id,
                ttl_seconds=lease_ttl_seconds,
                renewed_at=renewed_at,
            ),
            lease_ttl_seconds,
        )

    def register_claim(
        self,
        *,
        github_client: IGitHubClient,
        issue_number: int,
        bid: ClaimBid,
        body: str,
        ttl_seconds: int,
        config: AppConfig,
    ) -> None:
        """在仲裁确认胜出后登记认领续期与状态面板任务。"""
        repo_context = self._context_for_client(github_client)
        claim_key = (repo_context.repo_id, issue_number)
        with self._lock:
            existing_claim = self._claims.get(claim_key)
            if existing_claim is not None and existing_claim.comment_id == bid.comment_id:
                return
            self._claims[claim_key] = _TrackedClaim(
                repo_id=repo_context.repo_id,
                repo_path=repo_context.repo_path,
                config=config,
                github_client=self._lease_client_factory(repo_context.repo_path),
                issue_number=issue_number,
                comment_id=bid.comment_id,
                body=body,
                ttl_seconds=ttl_seconds,
                selected_agent=bid.agent or "unknown",
                started_at=bid.started_at,
                last_renewed_monotonic=time.monotonic(),
                last_renewal_attempt_monotonic=time.monotonic(),
            )
        self._refresh_presence(repo_context.repo_id, process_state="online")

    def release_claim(self, *, github_client: IGitHubClient, issue_number: int) -> None:
        """停止续期并清除状态板上的任务引用。"""
        repo_context = self._context_for_client(github_client)
        with self._lock:
            self._claims.pop((repo_context.repo_id, issue_number), None)
        self._refresh_presence(repo_context.repo_id, process_state="online")

    def register_attempt(
        self,
        *,
        config: AppConfig,
        issue_number: int,
        process_runner: IProcessRunner,
        attempt_key: str,
        worktree_path: Path | None = None,
    ) -> None:
        """登记当前 Issue 正在运行的 agent attempt 键。"""
        repo_id = self._resolve_claim_repo_id(config, issue_number, worktree_path)
        if repo_id is None:
            return
        with self._lock:
            claim = self._claims.get((repo_id, issue_number))
            if claim is None:
                return
            claim.attempt_keys[attempt_key] = process_runner
            is_lost = claim.lost
        if is_lost:
            self._terminate_attempts(claim)

    def unregister_attempt(
        self,
        *,
        config: AppConfig,
        issue_number: int,
        attempt_key: str,
        worktree_path: Path | None = None,
    ) -> None:
        """attempt 结束后移除其进程登记键。"""
        repo_id = self._resolve_claim_repo_id(config, issue_number, worktree_path)
        if repo_id is None:
            return
        with self._lock:
            claim = self._claims.get((repo_id, issue_number))
            if claim is not None:
                claim.attempt_keys.pop(attempt_key, None)

    def claim_was_lost(self, *, issue_number: int, worktree_path: Path) -> bool:
        """查询该 attempt 所属认领是否已触发本机自我隔离。"""
        repo_id = self._resolve_claim_repo_id_for_path(issue_number, worktree_path)
        if repo_id is None:
            return False
        with self._lock:
            claim = self._claims.get((repo_id, issue_number))
            return claim.lost if claim is not None else False

    def _resolve_claim_repo_id_for_path(self, issue_number: int, worktree_path: Path) -> str | None:
        """按 attempt worktree 定位同编号认领。"""
        resolved_worktree = worktree_path.resolve()
        matching_claims = [
            claim
            for claim in self._claims.values()
            if claim.issue_number == issue_number
            and (
                resolved_worktree == claim.repo_path.resolve()
                or resolved_worktree.is_relative_to(claim.repo_path.resolve())
            )
        ]
        return matching_claims[0].repo_id if len(matching_claims) == 1 else None

    def _resolve_claim_repo_id(
        self,
        config: AppConfig,
        issue_number: int,
        worktree_path: Path | None,
    ) -> str | None:
        """按配置或 worktree 路径定位对应的在途认领。"""
        mapped_repo_id = self._repo_id_by_config_id.get(id(config))
        if mapped_repo_id is not None and (mapped_repo_id, issue_number) in self._claims:
            return mapped_repo_id
        matching_claims = [
            claim for claim in self._claims.values() if claim.issue_number == issue_number
        ]
        if worktree_path is not None:
            resolved_worktree = worktree_path.resolve()
            path_matches = [
                claim
                for claim in matching_claims
                if resolved_worktree == claim.repo_path.resolve()
                or resolved_worktree.is_relative_to(claim.repo_path.resolve())
            ]
            if len(path_matches) == 1:
                return path_matches[0].repo_id
        config_matches = [
            claim for claim in matching_claims if claim.config is config or claim.config == config
        ]
        if len(config_matches) == 1:
            return config_matches[0].repo_id
        return matching_claims[0].repo_id if len(matching_claims) == 1 else None

    def assert_claim_owned(self, *, github_client: IGitHubClient, issue_number: int) -> None:
        """在推送或开 PR 前重新确认认领仍归当前 attempt 所有。"""
        repo_context = self._context_for_client(github_client)
        with self._lock:
            claim = self._claims.get((repo_context.repo_id, issue_number))
        if claim is None:
            return
        assert_claim_still_owned(
            github_client=claim.github_client,
            issue_number=issue_number,
            own_comment_id=claim.comment_id,
            machine_id=self.machine_id,
            was_lost=claim.lost,
        )

    def _run(self) -> None:
        """按租约自身的续期周期刷新评论，并周期更新 presence。"""
        while not self._stop_event.wait(timeout=1.0):
            monotonic_now = time.monotonic()
            with self._lock:
                claims_snapshot = list(self._claims.values())
                repo_ids_snapshot = list(self._contexts)
            for claim in claims_snapshot:
                self._renew_if_due(claim, monotonic_now)
            for repo_id in repo_ids_snapshot:
                last_refreshed = self._last_presence_refresh.get(repo_id, 0.0)
                if monotonic_now - last_refreshed >= PRESENCE_UPDATE_INTERVAL_SECONDS:
                    self._refresh_presence(repo_id, process_state="online")

    def _renew_if_due(self, claim: _TrackedClaim, monotonic_now: float) -> None:
        """续期单条认领，并在连续失败满半个 TTL 时自停。"""
        renewal_interval = max(1.0, claim.ttl_seconds / 10 * self._renew_interval_scale)
        elapsed_since_success = monotonic_now - claim.last_renewed_monotonic
        elapsed_since_attempt = monotonic_now - claim.last_renewal_attempt_monotonic
        if claim.lost:
            self._terminate_attempts(claim)
            return
        if (
            elapsed_since_success >= claim.ttl_seconds / 2
            and elapsed_since_attempt < renewal_interval
        ):
            with self._lock:
                claim.lost = True
            _logger.warning(
                "Issue #%d claim lease lost after %.1fs without a successful renewal; "
                "terminating its agent and abandoning delivery.",
                claim.issue_number,
                elapsed_since_success,
            )
            self._terminate_attempts(claim)
            return
        if elapsed_since_attempt < renewal_interval:
            return
        claim.last_renewal_attempt_monotonic = monotonic_now
        try:
            renewed_at = datetime.now(timezone.utc)
            updated_body = with_claim_lease(
                claim.body,
                machine_id=self.machine_id,
                ttl_seconds=claim.ttl_seconds,
                renewed_at=renewed_at,
            )
            claim.github_client.edit_issue_comment(claim.comment_id, updated_body)
        except Exception as exc:  # noqa: BLE001 - failure is accumulated via monotonic age.
            if elapsed_since_success >= claim.ttl_seconds / 2:
                with self._lock:
                    claim.lost = True
                _logger.warning(
                    "Issue #%d claim lease lost after %.1fs without a successful renewal; "
                    "terminating its agent and abandoning delivery.",
                    claim.issue_number,
                    elapsed_since_success,
                )
                self._terminate_attempts(claim)
            _logger.warning(
                "Lease renewal failed for Issue #%d; retrying until its self-fence: %s",
                claim.issue_number,
                exc,
            )
            return
        with self._lock:
            claim.body = updated_body
            claim.last_renewed_monotonic = time.monotonic()
            claim.last_renewal_attempt_monotonic = claim.last_renewed_monotonic

    @staticmethod
    def _terminate_attempts(claim: _TrackedClaim) -> None:
        """按进程登记簿复核并终止该 Issue 的 agent 子进程。"""
        for attempt_key, process_runner in tuple(claim.attempt_keys.items()):
            try:
                ownership = process_runner.probe_live_attempt(attempt_key)
                if not ownership.confirmed:
                    continue
                cancel_result = process_runner.cancel_live_attempt(attempt_key, ownership)
                if not cancel_result.cancelled:
                    _logger.warning(
                        "Could not terminate agent attempt for Issue #%d: %s",
                        claim.issue_number,
                        cancel_result.reason,
                    )
            except Exception as exc:  # noqa: BLE001 - never hide the lost-lease state.
                _logger.warning(
                    "Could not inspect agent attempt for Issue #%d during lease loss: %s",
                    claim.issue_number,
                    exc,
                )

    def _refresh_presence(self, repo_id: str, *, process_state: str) -> None:
        """尽力更新一个仓库上的机器状态评论。"""
        context = self._contexts[repo_id]
        if not context.config.daemon.presence_enabled:
            return
        with self._lock:
            issue_rows = tuple(
                {
                    "number": claim.issue_number,
                    "stage": claim.config.labels.running,
                    "agent": claim.selected_agent,
                    "started_at": claim.started_at.astimezone(timezone.utc).isoformat(),
                }
                for claim in self._claims.values()
                if claim.repo_id == repo_id and not claim.lost
            )
        try:
            comment_id = update_presence_comment(
                github_client=self._github_client_factory(context.repo_path),
                config=context.config,
                machine_id=self.machine_id,
                slot=self._slot,
                hostname=self._host,
                version=self._version,
                role=self._role,
                process_state=process_state,
                issues=issue_rows,
                slot_lock=self._slot_lock_factory,
            )
            if comment_id is not None:
                self._presence_comment_ids[repo_id] = comment_id
                self._last_presence_refresh[repo_id] = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - presence is observable, not a work gate.
            _logger.warning("Machine presence update failed for '%s': %s", repo_id, exc)

    def _context_for_client(self, github_client: IGitHubClient) -> RepositoryRunContext:
        """按 GitHub client 的仓库路径解析对应的运行上下文。"""
        client_repo_path = getattr(github_client, "repo_path", None)
        if client_repo_path is None:
            raise RuntimeError("GitHub client does not expose its repository path.")
        with self._lock:
            context = self._contexts_by_path.get(Path(client_repo_path).resolve())
        if context is None:
            raise RuntimeError(
                f"GitHub client targets an unconfigured repository: {client_repo_path}"
            )
        return context


def active_coordination_heartbeat() -> CoordinationHeartbeat | None:
    """返回当前进程已启动的续期服务。"""
    with _ACTIVE_HEARTBEAT_LOCK:
        return _ACTIVE_HEARTBEAT


def acquire_coordination_heartbeat(heartbeat: CoordinationHeartbeat) -> CoordinationHeartbeat:
    """启动或复用当前进程 heartbeat，并把新入口的仓库并入其上下文。"""
    with _ACTIVE_HEARTBEAT_LOCK:
        active_heartbeat = _ACTIVE_HEARTBEAT
        if active_heartbeat is None:
            heartbeat.start()
            return heartbeat
        active_heartbeat.retain_contexts(
            contexts=list(heartbeat._contexts.values()),
            machine_identity=heartbeat._machine_identity,
            github_client_factory=heartbeat._github_client_factory,
            lease_client_factory=heartbeat._lease_client_factory,
            slot_lock_factory=heartbeat._slot_lock_factory,
        )
        return active_heartbeat


__all__ = [
    "CoordinationHeartbeat",
    "acquire_coordination_heartbeat",
    "active_coordination_heartbeat",
]
