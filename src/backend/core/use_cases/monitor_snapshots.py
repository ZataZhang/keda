"""dashboard 监控快照与后台同步的 core 编排用例。

本模块是"dashboard 读本地快照、后台按节奏重扫"这一需求的全部编排逻辑：

- :func:`persist_monitoring_result` 是**唯一**的写库入口，只持久化调用方
  已经构建好的 overview payload，绝不为了写库再次调用 GitHub。
- :func:`get_snapshot_overview` 是按当前 registry 过滤后的读路径；已删除
  或已禁用仓库的历史行不会泄漏回页面。
- :func:`get_monitor_settings` / :func:`update_monitor_settings` 负责全局
  同步设置的读取、默认值回落与校验。
- :class:`MonitorSyncCoordinator` 按仓库去重，供周期调度与手动刷新共用；
  :class:`MonitorSyncScheduler` 是周期循环本身，只由应用生命周期启停。

设计约束：core 不依赖 FastAPI、不依赖具体 SQLite 实现，扫描动作与设置
读取都由调用方以可调用对象注入。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.core.shared.interfaces.runner_console import (
    MONITOR_SYNC_INTERVAL_MAX_SECONDS,
    MONITOR_SYNC_INTERVAL_MIN_SECONDS,
    SYNC_STATUS_PENDING_FIRST_SYNC,
    SYNC_STATUS_READY,
    SYNC_STATUS_PARTIAL,
    IMonitorSnapshotStore,
    MonitorSettingsEntry,
    MonitorSnapshotEntry,
)

_logger = logging.getLogger(__name__)


class MonitorSyncError(RuntimeError):
    """后台扫描或快照持久化失败。"""


def _now_iso() -> str:
    """Return a coarse ISO-8601 timestamp for ``scanned_at`` / ``updated_at``."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# 写路径
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MonitorPersistReport:
    """一次持久化批次的结果。

    Attributes:
        persisted_repo_ids: 成功写入快照的仓库 ID。
        failed_repo_ids: 写库失败的仓库 ID → 失败原因。失败仓库保留旧快照。
    """

    persisted_repo_ids: tuple[str, ...] = ()
    failed_repo_ids: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether every repository in the batch was persisted."""
        return not self.failed_repo_ids


def persist_monitoring_result(
    store: IMonitorSnapshotStore,
    payload: Mapping[str, Any],
) -> MonitorPersistReport:
    """把已构建好的 overview payload 写入本地快照。

    本函数是快照的唯一写入入口：**不会**为了写库重新扫描 GitHub。单个仓库
    写库失败只影响该仓库（其余仓库继续写入并保留各自旧快照），失败原因通过
    :attr:`MonitorPersistReport.failed_repo_ids` 交给调用方，由调用方决定是
    标记 job 失败还是只记录日志——绝不允许把写库失败伪装成刷新成功。

    Args:
        store: 快照存储端口实现。
        payload: ``GET /agent-runner/overview`` 同构的响应体，必须含
            ``repositories`` 列表。

    Returns:
        MonitorPersistReport: 成功与失败的仓库清单。
    """
    repository_payloads = payload.get("repositories") or []
    fallback_scanned_at = str(payload.get("scanned_at") or _now_iso())
    persisted_repo_ids: list[str] = []
    failed_repo_ids: dict[str, str] = {}
    for repository_payload in repository_payloads:
        repo_id = str(repository_payload.get("repo_id") or "")
        if not repo_id:
            continue
        scanned_at = str(repository_payload.get("scanned_at") or fallback_scanned_at)
        try:
            store.upsert_monitor_snapshot(
                MonitorSnapshotEntry(
                    repo_id=repo_id,
                    payload_json=json.dumps(repository_payload, ensure_ascii=False),
                    scanned_at=scanned_at,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 记录后继续其他仓库。
            _logger.warning("Failed to persist monitor snapshot for %s: %s", repo_id, exc)
            failed_repo_ids[repo_id] = str(exc)
            continue
        persisted_repo_ids.append(repo_id)
    return MonitorPersistReport(
        persisted_repo_ids=tuple(persisted_repo_ids),
        failed_repo_ids=failed_repo_ids,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 读路径
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MonitorSnapshotOverview:
    """一个仓库的快照读取结果。"""

    repo_id: str
    scanned_at: str
    overview: dict[str, Any]


@dataclass(frozen=True)
class SnapshotOverviewResult:
    """dashboard 首屏与轮询使用的快照概览。

    Attributes:
        repositories: 当前启用仓库中已有快照的条目，按 ``enabled_repo_ids``
            的顺序排列。
        missing_repo_ids: 当前启用但尚无快照的仓库 ID。
        sync_status: ``ready`` / ``partial`` / ``pending_first_sync`` 之一。
        scanned_at: 全部仓库快照时间的最大值，无快照时为 ``None``。
    """

    repositories: tuple[MonitorSnapshotOverview, ...]
    missing_repo_ids: tuple[str, ...]
    sync_status: str
    scanned_at: str | None


def _resolve_sync_status(
    *, enabled_repo_ids: Sequence[str], missing_repo_ids: Sequence[str]
) -> str:
    """根据缺失快照的比例判定整体同步状态。"""
    if not enabled_repo_ids or not missing_repo_ids:
        return SYNC_STATUS_READY
    if len(missing_repo_ids) >= len(enabled_repo_ids):
        return SYNC_STATUS_PENDING_FIRST_SYNC
    return SYNC_STATUS_PARTIAL


def _is_renderable_overview(overview_payload: Any, repo_id: str) -> bool:
    """判断快照内容是否是可交给前端渲染的 per-repo overview。

    只做最低限度结构校验（``repo_id`` 对得上、``issues`` 是列表），坏形状一律
    视同缺失，避免把渲染期会抛错的数据下发到 dashboard。
    """
    if not isinstance(overview_payload, dict):
        return False
    if str(overview_payload.get("repo_id") or "") != repo_id:
        return False
    return isinstance(overview_payload.get("issues"), list)


def get_snapshot_overview(
    store: IMonitorSnapshotStore,
    *,
    enabled_repo_ids: Iterable[str],
) -> SnapshotOverviewResult:
    """读取按当前 registry 过滤后的快照概览。

    库中已删除或已禁用仓库的历史行**不会**出现在结果里；当前启用但还没有
    快照的仓库进入 ``missing_repo_ids``，由前端以空态呈现。

    Args:
        store: 快照存储端口实现。
        enabled_repo_ids: 当前 registry 中启用的仓库 ID（决定展示集合与顺序）。

    Returns:
        SnapshotOverviewResult: 过滤后的快照概览。
    """
    enabled_order = [str(repo_id) for repo_id in enabled_repo_ids]
    enabled_lookup = set(enabled_order)
    snapshot_payloads: dict[str, tuple[str, dict[str, Any]]] = {}
    for snapshot_entry in store.list_monitor_snapshots():
        if snapshot_entry.repo_id not in enabled_lookup:
            continue
        try:
            overview_payload = json.loads(snapshot_entry.payload_json)
        except ValueError as exc:
            # 快照损坏视同缺失：宁可显示空态，也不要把坏数据渲染成队列状态。
            _logger.warning(
                "Discarding corrupt monitor snapshot for %s: %s",
                snapshot_entry.repo_id,
                exc,
            )
            continue
        if not _is_renderable_overview(overview_payload, snapshot_entry.repo_id):
            # 形状不符（合法 JSON 但不是本接口的 per-repo payload）同样视同缺失：
            # 坏数据下发给前端会让卡片渲染期抛错，比空态难排查得多。
            _logger.warning(
                "Discarding malformed monitor snapshot for %s",
                snapshot_entry.repo_id,
            )
            continue
        snapshot_payloads[snapshot_entry.repo_id] = (
            snapshot_entry.scanned_at,
            overview_payload,
        )

    repositories: list[MonitorSnapshotOverview] = []
    missing_repo_ids: list[str] = []
    for repo_id in enabled_order:
        snapshot = snapshot_payloads.get(repo_id)
        if snapshot is None:
            missing_repo_ids.append(repo_id)
            continue
        scanned_at, overview_payload = snapshot
        repositories.append(
            MonitorSnapshotOverview(
                repo_id=repo_id,
                scanned_at=scanned_at,
                overview=overview_payload,
            )
        )

    scanned_at_values = [entry.scanned_at for entry in repositories if entry.scanned_at]
    return SnapshotOverviewResult(
        repositories=tuple(repositories),
        missing_repo_ids=tuple(missing_repo_ids),
        sync_status=_resolve_sync_status(
            enabled_repo_ids=enabled_order, missing_repo_ids=missing_repo_ids
        ),
        scanned_at=max(scanned_at_values) if scanned_at_values else None,
    )


def snapshot_overview_to_payload(
    result: SnapshotOverviewResult,
    *,
    unreachable_repositories: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """把快照概览序列化为 HTTP 响应体。

    Args:
        result: 读路径产出的快照概览。
        unreachable_repositories: 当前 registry 中路径失效、已从监控跳过的
            仓库；由调用方（route 层）解析后透传，core 不认识 registry 细节。

    Returns:
        dict[str, Any]: 与 dashboard 前端约定一致的响应体。
    """
    return {
        "repositories": [
            {
                "repo_id": entry.repo_id,
                "scanned_at": entry.scanned_at,
                "overview": entry.overview,
            }
            for entry in result.repositories
        ],
        "missing_repo_ids": list(result.missing_repo_ids),
        "sync_status": result.sync_status,
        "scanned_at": result.scanned_at,
        "unreachable_repositories": [dict(entry) for entry in unreachable_repositories],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 设置路径
# ─────────────────────────────────────────────────────────────────────────────


def get_monitor_settings(
    store: IMonitorSnapshotStore,
    *,
    default_interval_seconds: int,
) -> MonitorSettingsEntry:
    """读取全局同步设置；无记录时回落到静态默认值。

    Args:
        store: 快照存储端口实现。
        default_interval_seconds: 配置中的静态默认间隔（秒），仅在没有 DB
            记录时生效，避免 core 内硬编码一份第二默认值。

    Returns:
        MonitorSettingsEntry: 当前生效的设置。
    """
    stored_settings = store.get_monitor_settings()
    if stored_settings is not None:
        return stored_settings
    return MonitorSettingsEntry(
        sync_enabled=True,
        sync_interval_seconds=default_interval_seconds,
        updated_at="",
    )


def update_monitor_settings(
    store: IMonitorSnapshotStore,
    *,
    sync_enabled: bool,
    sync_interval_seconds: int,
) -> MonitorSettingsEntry:
    """校验并保存全局同步设置。

    Args:
        store: 快照存储端口实现。
        sync_enabled: 是否开启后台自动同步。
        sync_interval_seconds: 同步间隔（秒），合法区间 ``[60, 3600]``。

    Returns:
        MonitorSettingsEntry: 写入后的设置（含新的 ``updated_at``）。

    Raises:
        ValueError: 同步间隔超出合法区间。
    """
    if (
        sync_interval_seconds < MONITOR_SYNC_INTERVAL_MIN_SECONDS
        or sync_interval_seconds > MONITOR_SYNC_INTERVAL_MAX_SECONDS
    ):
        raise ValueError(
            "sync_interval_seconds must be between "
            f"{MONITOR_SYNC_INTERVAL_MIN_SECONDS} and {MONITOR_SYNC_INTERVAL_MAX_SECONDS}, "
            f"got {sync_interval_seconds}"
        )
    settings_entry = MonitorSettingsEntry(
        sync_enabled=bool(sync_enabled),
        sync_interval_seconds=int(sync_interval_seconds),
        updated_at=_now_iso(),
    )
    store.save_monitor_settings(settings_entry)
    return settings_entry


# ─────────────────────────────────────────────────────────────────────────────
# 按仓库扫描协调
# ─────────────────────────────────────────────────────────────────────────────


class _SyncTask:
    """单次扫描任务的可等待状态（协调器内部实现细节）。"""

    def __init__(self, repo_id: str) -> None:
        """初始化任务状态。

        Args:
            repo_id: 本次扫描的目标仓库 ID。
        """
        self.repo_id = repo_id
        self.done_event = threading.Event()
        self.error: str | None = None


class MonitorSyncHandle:
    """:meth:`MonitorSyncCoordinator.request_sync` 的返回值。

    调用方据此判断扫描是不是自己启动的（``started``），并等待扫描结束
    （:meth:`wait`）。同一仓库上的多个调用方共享同一个任务状态。
    """

    def __init__(self, task: _SyncTask, *, started: bool) -> None:
        """绑定到底层任务。

        Args:
            task: 共享的扫描任务状态。
            started: 本次调用是否新启动了扫描。
        """
        self._task = task
        self._started = started

    @property
    def repo_id(self) -> str:
        """本次扫描的目标仓库 ID。"""
        return self._task.repo_id

    @property
    def started(self) -> bool:
        """Whether this call launched a new scan (False when de-duplicated)."""
        return self._started

    @property
    def completed(self) -> bool:
        """Whether the underlying scan has finished."""
        return self._task.done_event.is_set()

    def wait(self, *, timeout_seconds: float | None = None) -> None:
        """等待扫描结束。

        Args:
            timeout_seconds: 最长等待秒数；``None`` 表示一直等到结束。

        Raises:
            MonitorSyncError: 扫描或快照持久化失败。
        """
        self._task.done_event.wait(timeout=timeout_seconds)
        if self._task.error is not None:
            raise MonitorSyncError(self._task.error)


class MonitorSyncCoordinator:
    """按仓库去重的扫描协调器。

    不同仓库的扫描可以并行；同一仓库在扫描未结束前再次申请只会拿到已有
    任务的句柄（``started=False``），不会启动第二次 GitHub 扫描。周期调度
    与手动刷新必须共用同一个实例，否则两套入口仍会重复扫描。
    """

    def __init__(
        self,
        scan_runner: Callable[[str], None],
        *,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        """初始化协调器。

        Args:
            scan_runner: 执行一次仓库扫描并写回快照的可调用对象，接受
                ``repo_id``；抛出的异常会记录在任务状态里。
            thread_factory: 线程工厂，默认 :class:`threading.Thread`。
        """
        self._scan_runner = scan_runner
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        self._tasks: dict[str, _SyncTask] = {}
        self._threads: list[threading.Thread] = []

    def in_flight_repo_ids(self) -> tuple[str, ...]:
        """返回当前正在扫描的仓库 ID。"""
        with self._lock:
            return tuple(self._tasks)

    def is_in_flight(self, repo_id: str) -> bool:
        """判断指定仓库是否正在扫描。"""
        with self._lock:
            return repo_id in self._tasks

    def request_sync(self, repo_id: str) -> MonitorSyncHandle:
        """申请一次仓库扫描；已有在途扫描时复用而不新开。

        Args:
            repo_id: 目标仓库 ID。

        Returns:
            MonitorSyncHandle: 可用于等待结果的句柄。
        """
        if not repo_id:
            raise ValueError("repo_id must be a non-empty string.")
        with self._lock:
            existing_task = self._tasks.get(repo_id)
            if existing_task is not None:
                return MonitorSyncHandle(existing_task, started=False)
            task = _SyncTask(repo_id)
            self._tasks[repo_id] = task
        thread = self._thread_factory(
            target=self._run_scan,
            args=(task,),
            daemon=True,
        )
        with self._lock:
            self._threads.append(thread)
        thread.start()
        return MonitorSyncHandle(task, started=True)

    def _run_scan(self, task: _SyncTask) -> None:
        """执行扫描并把结果写入任务状态。"""
        try:
            self._scan_runner(task.repo_id)
        except Exception as exc:  # noqa: BLE001 - 失败只影响本次扫描。
            _logger.warning("Monitor sync failed for %s: %s", task.repo_id, exc)
            task.error = str(exc)
        finally:
            task.done_event.set()
            with self._lock:
                if self._tasks.get(task.repo_id) is task:
                    del self._tasks[task.repo_id]
                # 顺手回收已结束的线程句柄：长驻进程每周期都会创建新线程，
                # 不清理会让 _threads 无界增长。
                self._threads = [item for item in self._threads if item.is_alive()]

    def wait_until_idle(self, *, timeout_seconds: float = 30.0) -> bool:
        """等待所有在途扫描结束。

        Args:
            timeout_seconds: 最长等待秒数。

        Returns:
            bool: 全部结束时为 ``True``，超时为 ``False``。
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._lock:
                pending_tasks = list(self._tasks.values())
                threads = list(self._threads)
            if not pending_tasks:
                for thread in threads:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                with self._lock:
                    self._threads = [item for item in self._threads if item not in set(threads)]
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            pending_tasks[0].done_event.wait(timeout=min(remaining, 0.05))


# ─────────────────────────────────────────────────────────────────────────────
# 周期调度
# ─────────────────────────────────────────────────────────────────────────────


class MonitorSyncScheduler:
    """后台周期同步调度器。

    只由应用生命周期（FastAPI lifespan）启停：``start()`` 会先为缺失快照的
    仓库幂等申请首次扫描，再进入按间隔的周期循环。设置变化通过 :meth:`wake`
    立即唤醒，使等待时间按新设置重算，而不是沿用旧的等待窗口。
    """

    def __init__(
        self,
        *,
        coordinator: MonitorSyncCoordinator,
        settings_reader: Callable[[], MonitorSettingsEntry],
        repo_id_provider: Callable[[], Sequence[str]],
        missing_repo_id_provider: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        """初始化调度器。

        Args:
            coordinator: 共享的按仓库扫描协调器。
            settings_reader: 读取当前全局同步设置的可调用对象。
            repo_id_provider: 返回当前启用仓库 ID 列表的可调用对象。
            missing_repo_id_provider: 返回"已启用但尚无快照"仓库 ID 的可调用
                对象；启动时只为这些仓库幂等申请首次扫描，已有快照的仓库等
                到下一个周期再刷新。
        """
        self._coordinator = coordinator
        self._settings_reader = settings_reader
        self._repo_id_provider = repo_id_provider
        self._missing_repo_id_provider = missing_repo_id_provider
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """Whether the scheduler loop is currently alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """启动调度循环；重复调用只会启动一次。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wake(self) -> None:
        """唤醒调度循环，使其立即按最新设置重算等待时间。"""
        self._wake_event.set()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        """停止调度循环并有界等待线程退出。

        线程在超时窗口内没有退出时**保留句柄**：这样后续 ``start()`` 会被
        ``is_alive()`` 挡住而不会起第二个循环，``stop()`` 也可以再次重试 join。

        Args:
            timeout_seconds: 等待线程退出的最长秒数。
        """
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                _logger.warning(
                    "Monitor scheduler thread is still alive after %ss; keeping its handle "
                    "so no second loop can start.",
                    timeout_seconds,
                )
                return
        self._thread = None

    def _wait(self, timeout_seconds: float) -> None:
        """等待指定时长，期间可被 stop 或 wake 中断。"""
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._wake_event.wait(timeout=min(remaining, 1.0)):
                self._wake_event.clear()
                return

    def _request_initial_scan(self) -> None:
        """为缺失快照的仓库幂等申请首次扫描。"""
        if self._missing_repo_id_provider is None:
            return
        try:
            missing_repo_ids = [str(item) for item in self._missing_repo_id_provider()]
        except Exception as exc:  # noqa: BLE001 - 读取失败不该杀死循环。
            _logger.warning("Monitor scheduler failed to resolve missing repos: %s", exc)
            return
        if not missing_repo_ids:
            return
        _logger.info(
            "Monitor sync initial pass: %d repositories have no snapshot yet.",
            len(missing_repo_ids),
        )
        for repo_id in missing_repo_ids:
            if self._stop_event.is_set():
                return
            self._coordinator.request_sync(repo_id)

    def _run(self) -> None:
        """调度主循环。

        第一圈只为缺失快照的仓库幂等补扫（已有快照的仓库等下个周期），之后
        每个周期对全部启用仓库重扫。开关关闭时既不做首扫也不做周期扫描。
        """
        first_pass = True
        while not self._stop_event.is_set():
            try:
                settings_entry = self._settings_reader()
                repo_ids = [str(item) for item in self._repo_id_provider()]
            except Exception as exc:  # noqa: BLE001 - 单次读取失败不杀死循环。
                _logger.warning("Monitor scheduler failed to read settings: %s", exc)
                self._wait(MONITOR_SYNC_INTERVAL_MIN_SECONDS)
                continue
            if settings_entry.sync_enabled:
                if first_pass:
                    self._request_initial_scan()
                elif repo_ids:
                    # 每周期留一条可观察的同步记录：运维据此确认节奏与设置一致。
                    _logger.info(
                        "Monitor sync cycle: refreshing %d repositories (interval %ss)",
                        len(repo_ids),
                        settings_entry.sync_interval_seconds,
                    )
                    for repo_id in repo_ids:
                        if self._stop_event.is_set():
                            break
                        self._coordinator.request_sync(repo_id)
            first_pass = False
            self._wait(settings_entry.sync_interval_seconds)


__all__ = [
    "MonitorPersistReport",
    "MonitorSettingsEntry",
    "MonitorSnapshotEntry",
    "MonitorSnapshotOverview",
    "MonitorSyncCoordinator",
    "MonitorSyncError",
    "MonitorSyncHandle",
    "MonitorSyncScheduler",
    "SnapshotOverviewResult",
    "get_monitor_settings",
    "get_snapshot_overview",
    "persist_monitoring_result",
    "snapshot_overview_to_payload",
    "update_monitor_settings",
]
