"""统一管理终端（Operations Console）的端口与共享模型。

本模块定义管理终端在 core 层依赖的端口：

- ``IRunnerProcessSupervisor``：托管 runner 子进程（spawn / 探活 / 停止 /
  日志续读）。实现位于 ``infrastructure/console/process_supervisor.py``。
- ``IRunHistoryStore``：运行历史与审计日志的旁路存储。实现位于
  ``infrastructure/persistence/console_store.py``（本地 SQLite）。
- ``IRepositoryRegistryEditor``：对 ``config.toml`` 仓库 registry 的受限
  写回。实现位于 ``infrastructure/config/registry_editor.py``（tomlkit）。
- ``IMonitorSnapshotStore``：dashboard 监控快照与全局同步设置的持久化。
  实现同样位于 ``infrastructure/persistence/console_store.py``。

设计约束：

- SQLite 历史只是旁路记录，不参与 workflow 状态机决策；GitHub
  labels/comments/PR 仍是唯一事实来源。
- 进程监管以 pidfile registry 中由面板启动的托管进程为操作对象；
  同时提供 ``list_unmanaged_processes`` 用于观测用户手工启动的 CLI
      进程，但不对其执行停止、重启等生命周期操作。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence


class RunnerProcessKind(str, Enum):
    """面板可托管的 runner 进程类型（白名单）。"""

    DAEMON = "daemon"
    REVIEW_DAEMON = "review_daemon"
    RUN_ONCE = "run_once"
    REVIEW_ONCE = "review_once"
    BLOCKED_CONTINUE = "blocked_continue"


#: 常驻类进程：同一 (repo_id, kind) 同时只允许一个 running 实例。
PERSISTENT_PROCESS_KINDS = frozenset({RunnerProcessKind.DAEMON, RunnerProcessKind.REVIEW_DAEMON})


@dataclass(frozen=True)
class RunnerProcessRecord:
    """一个被托管 runner 进程的状态快照。"""

    process_id: str
    repo_id: str
    kind: RunnerProcessKind
    pid: int
    status: str  # running / exited / stopped / killed
    exit_code: int | None
    log_path: str
    command: tuple[str, ...]
    started_at: str
    stopped_at: str | None


@dataclass(frozen=True)
class ProcessLogChunk:
    """日志 offset 续读的一段内容。"""

    content: str
    next_offset: int
    eof: bool


@dataclass(frozen=True)
class RunRecord:
    """一次 Issue 处理的运行结果（旁路记录）。"""

    repo_id: str
    repo_path: str
    issue_number: int
    trigger: str  # cli_run / cli_daemon / console_run / console_daemon
    agent: str
    outcome: str  # completed / failed / blocked
    error_summary: str | None
    started_at: str  # ISO8601 UTC
    finished_at: str  # ISO8601 UTC
    duration_seconds: float


@dataclass(frozen=True)
class AttemptRecord:
    """一次 agent execution attempt 的旁路记录。

    与 :class:`backend.core.shared.models.agent_runner.AttemptResult` 同构，
    用于本地 SQLite 持久化，方便在 runner 崩溃或跨 agent fallback 后仍能
    复盘每轮耗时与失败原因。
    """

    repo_id: str
    issue_number: int
    agent: str
    attempt_number: int
    failure_type: str
    recovered: bool
    detail: str
    started_at: str  # ISO8601 UTC
    finished_at: str  # ISO8601 UTC
    duration_seconds: float


@dataclass(frozen=True)
class AuditEntry:
    """一次管理终端写操作的审计条目。"""

    occurred_at: str  # ISO8601 UTC
    actor: str
    action: str
    repo_id: str | None
    issue_number: int | None
    params_json: str
    result: str  # accepted / rejected / error
    detail: str | None


@dataclass(frozen=True)
class DailyRunTrendEntry:
    """运行历史按天聚合的一个数据点。"""

    day: str  # YYYY-MM-DD
    completed: int
    failed: int
    blocked: int
    average_duration_seconds: float | None


class IRunnerProcessSupervisor(ABC):
    """托管 runner 子进程生命周期的端口。"""

    @abstractmethod
    def spawn(
        self,
        *,
        repo_id: str,
        kind: RunnerProcessKind,
        argv: Sequence[str],
        cwd: Path,
    ) -> RunnerProcessRecord:
        """启动一个脱离当前进程组的 runner 子进程并登记。

        Args:
            repo_id: 目标仓库 ID。
            kind: 进程类型（白名单枚举）。
            argv: 完整命令参数序列，不经过 shell 解析。
            cwd: 子进程工作目录。

        Returns:
            RunnerProcessRecord: 新进程的登记记录（status 为 running）。
        """
        ...

    @abstractmethod
    def list_processes(self) -> list[RunnerProcessRecord]:
        """列出全部登记的进程并刷新其存活状态。"""
        ...

    @abstractmethod
    def list_unmanaged_processes(
        self, registry_entries: list[RegistryRepositoryEntry]
    ) -> list[RunnerProcessRecord]:
        """扫描系统进程，返回属于 registry 但未在 pidfile 中登记的 runner 进程。

        结果仅用于观测，不纳入 ``stop`` / ``read_log`` 等托管生命周期操作。
        实现应过滤当前用户拥有的进程，并排除已在 ``list_processes`` 中
        出现的 pid。

        Args:
            registry_entries: 当前 registry 中的仓库条目，用于按 cwd 匹配
                未显式指定 ``--repo-id`` 的手动进程。
        """
        ...

    @abstractmethod
    def get_process(self, process_id: str) -> RunnerProcessRecord | None:
        """按 ID 查询单个进程的最新状态，不存在时返回 ``None``。"""
        ...

    @abstractmethod
    def stop(self, process_id: str, *, timeout_seconds: int) -> RunnerProcessRecord:
        """停止进程：先 SIGTERM，超时后升级 SIGKILL。

        Args:
            process_id: 进程登记 ID。
            timeout_seconds: SIGTERM 后等待的秒数。

        Returns:
            RunnerProcessRecord: 停止后的最终记录。

        Raises:
            KeyError: 进程 ID 未登记。
        """
        ...

    @abstractmethod
    def read_log(self, process_id: str, *, offset: int, max_bytes: int) -> ProcessLogChunk:
        """从指定偏移量续读进程日志。

        Args:
            process_id: 进程登记 ID。
            offset: 起始字节偏移。
            max_bytes: 本次最多读取的字节数。

        Returns:
            ProcessLogChunk: 日志内容、下一偏移与是否到达文件尾。

        Raises:
            KeyError: 进程 ID 未登记。
        """
        ...


class IRunHistoryStore(ABC):
    """运行历史与审计日志的旁路存储端口。"""

    @abstractmethod
    def append_run(self, run_record: RunRecord) -> None:
        """追加一条运行记录。实现必须不抛出阻断 runner 的异常。"""
        ...

    @abstractmethod
    def append_attempt(self, attempt_record: AttemptRecord) -> None:
        """追加一条 attempt 记录。实现必须不抛出阻断 runner 的异常。"""
        ...

    @abstractmethod
    def append_audit(self, audit_entry: AuditEntry) -> None:
        """追加一条审计条目。"""
        ...

    @abstractmethod
    def list_recent_runs(self, *, repo_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """倒序列出最近的运行记录。"""
        ...

    @abstractmethod
    def list_issue_attempts(
        self, *, repo_id: str, issue_number: int, limit: int = 100
    ) -> list[AttemptRecord]:
        """按时间正序列出某个 Issue 的 attempt 记录。

        runner 进程内存中的 attempt 列表在每次跨 agent fallback 与每次重新
        claim 时都会从 1 重新计数，只有存储侧保留该 Issue 的完整尝试轨迹；
        实时 attempt 历史评论据此渲染，才不会把上一个 agent 的历史行覆盖掉。

        Args:
            repo_id: 目标仓库标识。
            issue_number: 目标 Issue 编号。
            limit: 最多返回的记录数；超出时保留最近写入的若干条。

        Returns:
            按写入时间正序（最早在前）排列的 attempt 记录；读取失败时返回空
            列表，实现不得抛出阻断 runner 的异常。
        """
        ...

    @abstractmethod
    def list_recent_audits(self, *, limit: int = 100) -> list[AuditEntry]:
        """倒序列出最近的审计条目。"""
        ...

    @abstractmethod
    def daily_run_trend(self, *, repo_id: str | None, days: int) -> list[DailyRunTrendEntry]:
        """按天聚合最近 ``days`` 天的运行结果。"""
        ...


@dataclass(frozen=True)
class PrdLifecycleRunRecord:
    """一次 PRD 生命周期的稳定身份与终态（追加账本的 run 行）。

    身份固定为 ``repo_id + prd_path + run_id``：``run_id`` 由 core 依据
    ``repo_id`` 与 Issue 编号（无 Issue 时退化为 PRD 路径摘要）确定性推导，
    因此 runner 与 roadmap 两侧无需显式传递就能写到同一行，重试与跨进程
    恢复也不会新建重复 run。
    """

    run_id: str
    repo_id: str
    prd_path: str
    issue_number: int | None
    trigger: str
    started_at: str  # ISO8601 UTC
    finished_at: str | None
    outcome: str | None  # completed / failed / blocked
    history_complete: bool


@dataclass(frozen=True)
class PrdLifecycleEventRecord:
    """一条追加式生命周期事件（账本事件行）。

    ``event_key`` 在同一 ``run_id`` 内唯一，用于抵抗重试与并发重复写；
    ``detail_json`` 只允许结构化非敏感摘要（不含 prompt / 终端原文 / 密钥）。
    """

    run_id: str
    event_key: str
    event_type: str
    phase: str
    actor: str
    occurred_at: str  # ISO8601 UTC
    detail_json: str


class IPrdLifecycleStore(ABC):
    """PRD 生命周期 run/event 追加账本的旁路存储端口。

    与 :class:`IRunHistoryStore` 同库同族：run/event 只是观测账本，不参与
    workflow 决策；写入失败必须降级为日志告警并把对应 run 标记为
    ``history_complete=False``，绝不允许阻断 runner 主流程。
    """

    @abstractmethod
    def upsert_lifecycle_run(self, run_record: PrdLifecycleRunRecord) -> None:
        """创建或刷新一个 lifecycle run。

        首次写入落 ``started_at``；已存在时只允许补齐 ``prd_path`` /
        ``issue_number`` / ``finished_at`` / ``outcome`` / ``history_complete``，
        不覆盖更早的 ``started_at``。失败时抛出异常，由 core 记录函数降级。
        """
        ...

    @abstractmethod
    def append_lifecycle_event(self, event_record: PrdLifecycleEventRecord) -> bool:
        """追加一条生命周期事件。

        Returns:
            ``True`` 表示本次真实插入；``False`` 表示 ``event_key`` 已存在
            （幂等命中，未产生重复事件）。

        Raises:
            Exception: 存储故障时抛出，由 core 记录函数捕获并标记 run 不完整。
        """
        ...

    @abstractmethod
    def mark_lifecycle_run_incomplete(self, run_id: str) -> None:
        """把某个 run 标记为观测历史不完整（事件写入失败后的降级信号）。"""
        ...

    @abstractmethod
    def finish_lifecycle_run(
        self,
        *,
        run_id: str,
        outcome: str,
        finished_at: str,
    ) -> None:
        """写终态：设置 ``finished_at`` 与 ``outcome``，不改动已有事件。"""
        ...

    @abstractmethod
    def reopen_lifecycle_run(self, run_id: str) -> None:
        """重开一个已收口的 run（``finished_at`` 与 ``outcome`` 清空）。

        同一个稳定 run id 在失败/阻塞后可能被再次执行（重试或解除阻塞），此时
        该 run 仍是“进行中”。若不重开，``finished_at`` 会早于后续事件时间，
        使端到端耗时与执行/等待/阻塞拆分互相矛盾。终态由事件历史保留，不因
        重开而丢失（``failed`` / ``blocked`` 事件仍在时间线上）。
        """
        ...

    @abstractmethod
    def get_lifecycle_run(self, run_id: str) -> PrdLifecycleRunRecord | None:
        """按 run id 读取单个 run；不存在时返回 ``None``。"""
        ...

    @abstractmethod
    def get_latest_lifecycle_run(
        self, *, repo_id: str, prd_path: str
    ) -> PrdLifecycleRunRecord | None:
        """按 ``repo_id + prd_path`` 读取最近一次 run；不存在时返回 ``None``。"""
        ...

    @abstractmethod
    def list_lifecycle_events(self, *, run_id: str) -> list[PrdLifecycleEventRecord]:
        """按发生顺序（occurred_at，再按写入顺序）列出某个 run 的全部事件。"""
        ...

    @abstractmethod
    def list_lifecycle_runs(
        self, *, repo_id: str | None = None, since: str | None = None
    ) -> list[PrdLifecycleRunRecord]:
        """列出 run，可按仓库与 ``since``（ISO8601 下界）过滤。

        返回按 ``started_at`` 正序排列的 run；用于仓库级统计聚合。
        """
        ...

    @abstractmethod
    def count_legacy_runs_without_lifecycle(
        self, *, repo_id: str | None = None, since: str | None = None
    ) -> int:
        """统计无法可靠关联 PRD 的旧 ``run_records`` 条数（降级披露用）。

        旧记录只有 Issue 编号、没有稳定 run id，不能并入完整生命周期分位数；
        本方法让统计显式披露被排除的条数，而不是静默丢弃。
        """
        ...


@dataclass(frozen=True)
class RegistryRepositoryEntry:
    """registry 中一个仓库条目的摘要视图。"""

    repo_id: str
    path: str
    enabled: bool
    display_name: str | None
    path_exists: bool


@dataclass(frozen=True)
class DiscoveredRepositoryEntry:
    """本地扫描发现的 IAR 仓库候选条目。"""

    repo_id: str
    path: str
    display_name: str | None
    already_registered: bool


@dataclass(frozen=True)
class BrowsableDirectoryEntry:
    """目录选择器中的一个子目录候选。"""

    name: str
    path: str
    is_git_repo: bool
    has_iar_config: bool
    already_registered: bool
    suggested_repo_id: str


@dataclass(frozen=True)
class DirectoryBrowseResult:
    """目录选择器单次浏览的结果：当前目录自身 + 其子目录列表。"""

    path: str
    parent: str | None
    home: str
    suggested_repo_id: str
    suggested_display_name: str
    directories: list[BrowsableDirectoryEntry]


class IRepositoryRegistryEditor(ABC):
    """对仓库 registry（config.toml）的受限读写端口。

    实现只允许触碰 ``agent_runner.repositories.<repo_id>`` 子树，
    其余配置节必须保持原样（含注释与格式）。
    """

    @abstractmethod
    def list_repositories(self) -> list[RegistryRepositoryEntry]:
        """列出 registry 中的全部仓库条目。"""
        ...

    @abstractmethod
    def add_repository(self, *, repo_id: str, path: str, display_name: str | None) -> None:
        """新增一个仓库条目（enabled 默认 true）。

        Raises:
            ValueError: repo_id 已存在。
        """
        ...

    @abstractmethod
    def set_enabled(self, repo_id: str, *, enabled: bool) -> None:
        """启用或停用一个已有条目。

        Raises:
            KeyError: repo_id 不存在。
        """
        ...

    @abstractmethod
    def remove_repository(self, repo_id: str) -> None:
        """从 registry 中删除一个仓库条目。

        Raises:
            KeyError: repo_id 不存在。
        """
        ...


class IRepositoryAutopilotSettingsEditor(ABC):
    """仓库级 Autopilot 设置的受限读写端口。

    Autopilot 的唯一持久事实源是仓库根目录的 ``.iar.toml``，因此本端口比
    :class:`IRepositoryRegistryEditor` 更窄：只允许读写
    ``[agent_runner.autopilot].enabled`` 这一个布尔键，其余配置节、键、子表
    与注释必须逐字保留。

    刻意不提供通用 TOML PATCH：它会把写权限扩大到全部配置键，突破
    ``autopilot.enabled`` 与 ``safety.auto_merge`` 的双重危险动作门禁边界。
    """

    @abstractmethod
    def config_source_path(self, repo_root_path: Path) -> Path:
        """返回目标仓库的本地配置文件绝对路径（无论文件是否存在）。"""
        ...

    @abstractmethod
    def read_enabled(self, repo_root_path: Path) -> bool | None:
        """读取仓库本地配置中的 ``autopilot.enabled``。

        Args:
            repo_root_path: 目标仓库根目录。

        Returns:
            ``True`` / ``False`` 为配置中的显式值；``None`` 表示该文件不存在
            或该键未设置（此时生效值等于全局配置的默认值）。

        Raises:
            ValueError: 本地配置存在但非法（TOML 语法错误或类型不符）。
        """
        ...

    @abstractmethod
    def set_enabled(self, repo_root_path: Path, enabled: bool) -> None:
        """仅修改 ``[agent_runner.autopilot].enabled`` 并原子替换文件。

        实现必须：保留注释与未知键/子表、只改这一个布尔键、失败时原文件保持
        不变。原子替换与格式保留由共享原语 ``update_toml_table_keys`` 提供，
        实现负责在委托前校验现有配置合法（详见
        ``backend.infrastructure.config.repository_settings_editor``）。

        Args:
            repo_root_path: 目标仓库根目录。
            enabled: 目标布尔值。

        Raises:
            ValueError: 配置非法（文件缺失、结构不符或模型校验不通过）。
            OSError: 文件不可写。
        """
        ...


@dataclass(frozen=True)
class RoadmapQueueEntry:
    """roadmap 全局调度队列的一条记录（core 侧端口类型）。"""

    repo_id: str
    prd_path: str
    status: str  # queued / running / completed / failed
    trigger: str  # manual / global
    started_at: str | None
    finished_at: str | None
    error_detail: str | None
    entry_id: int | None = None


@dataclass(frozen=True)
class RoadmapSettingsEntry:
    """roadmap 用户设置（core 侧端口类型）。"""

    repo_id: str
    max_parallel: int
    default_view: str  # timeline / list
    updated_at: str


class IRoadmapStore(ABC):
    """roadmap 调度队列与设置的旁路存储端口。"""

    @abstractmethod
    def get_roadmap_settings(self, repo_id: str) -> RoadmapSettingsEntry | None:
        """读取指定仓库的 roadmap 设置；不存在时返回 ``None``。"""
        ...

    @abstractmethod
    def save_roadmap_settings(self, settings: RoadmapSettingsEntry) -> None:
        """保存或更新 roadmap 设置；失败时抛出异常。"""
        ...

    @abstractmethod
    def enqueue_roadmap(self, entry: RoadmapQueueEntry) -> int:
        """将 PRD 加入 roadmap 队列，返回自增 ID；失败时抛出异常。"""
        ...

    @abstractmethod
    def list_roadmap_queue(
        self, *, repo_id: str | None = None, status: str | None = None
    ) -> list[RoadmapQueueEntry]:
        """列出 roadmap 队列条目，支持按仓库与状态过滤。"""
        ...

    @abstractmethod
    def update_roadmap_queue_status(
        self,
        *,
        entry_id: int,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """更新队列条目的状态；失败时抛出异常。"""
        ...

    @abstractmethod
    def clear_roadmap_queue(self, *, repo_id: str | None = None) -> None:
        """清空 roadmap 队列；失败时抛出异常。"""
        ...


#: 同步间隔合法区间（秒）：界面上 1–60 分钟的可调范围。
MONITOR_SYNC_INTERVAL_MIN_SECONDS = 60
MONITOR_SYNC_INTERVAL_MAX_SECONDS = 3600

#: 快照读取接口的状态取值。
SYNC_STATUS_READY = "ready"
SYNC_STATUS_PARTIAL = "partial"
SYNC_STATUS_PENDING_FIRST_SYNC = "pending_first_sync"


@dataclass(frozen=True)
class MonitorSnapshotEntry:
    """一个仓库的监控快照（core 侧端口类型）。

    ``payload_json`` 是单个仓库 overview 的 JSON 序列化结果，结构与
    ``GET /agent-runner/overview`` 返回的 ``repositories[]`` 元素一致。
    """

    repo_id: str
    payload_json: str
    scanned_at: str


@dataclass(frozen=True)
class MonitorSettingsEntry:
    """全局监控同步设置（core 侧端口类型）。

    进程级别的静态默认值来自配置（``AgentRunnerConsoleSettings``），本表只存
    用户在界面上改过的运行时覆盖值；无记录时 core 用例回落到静态默认值。
    """

    sync_enabled: bool
    sync_interval_seconds: int
    updated_at: str


class IMonitorSnapshotStore(ABC):
    """dashboard 监控快照与全局同步设置的持久化端口。

    与旁路记录端口（``IRunHistoryStore``）不同：本端口是 dashboard 的事实
    读取路径，写入失败必须抛给调用方，避免出现"刷新成功但数据未更新"。
    """

    @abstractmethod
    def upsert_monitor_snapshot(self, entry: MonitorSnapshotEntry) -> None:
        """写入或覆盖一个仓库的监控快照；失败时抛出异常。"""
        ...

    @abstractmethod
    def list_monitor_snapshots(self) -> list[MonitorSnapshotEntry]:
        """列出全部仓库的监控快照；失败时抛出异常。"""
        ...

    @abstractmethod
    def get_monitor_settings(self) -> MonitorSettingsEntry | None:
        """读取全局同步设置；尚无记录时返回 ``None``。"""
        ...

    @abstractmethod
    def save_monitor_settings(self, settings: MonitorSettingsEntry) -> None:
        """保存或更新全局同步设置；失败时抛出异常。"""
        ...
