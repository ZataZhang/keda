"""Dashboard 与桌面小窗共享的只读运行任务摘要模型。

本模块只描述「值守时需要看到什么」，不承担任何读取职责：数据由
:mod:`backend.core.use_cases.runtime_dashboard` 从已有的监控快照、运行账本与
PRD 生命周期账本组装而成。刻意不引入数据库表，也不复制 Issue 日志、
invocation ledger 或改动树触达读数的原始内容——摘要只到「一条任务行」的粒度。

触达比例（``touched/judgeable``）**不在本模型里**：它的唯一来源仍是现有
``GET /api/v1/agent-runner/backlog/prds/{encoded_path}/progress`` 契约，由
前端按任务取数，避免第二份实现漂移。

字段语义约束（PRD FR-1 / FR-6）：可空字段一律用 ``None`` 表达「未知」，
不填占位默认值，也不把缺失映射成零。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: 一次摘要最多返回的任务行数，避免注册仓库很多时把页面拖垮。
RUNTIME_TASK_MAX_ROWS = 100

#: 摘要默认只覆盖最近这段时间内的运行，历史复盘仍走 Stats 页面。
RUNTIME_TASK_WINDOW_SECONDS = 7 * 24 * 60 * 60


class RuntimeTaskStatus(str, Enum):
    """Issue Agent 任务在运行看板上的生命周期分桶。

    分桶来源是 Console 已观察到的事实（GitHub Issue 状态、监控标签与运行账本），
    不是推测：`agent/blocked` 与 `agent/failed` 分别归入 :attr:`BLOCKED` 和
    :attr:`FAILED`；关闭的 Issue 归入 :attr:`CLOSED`；无法判定时返回
    :attr:`UNKNOWN` 并附带原因，而不是猜一个。
    """

    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CLOSED = "closed"
    UNKNOWN = "unknown"

    @property
    def display_order(self) -> int:
        """返回看板默认排序权重：需要盯的在前，已收口的在后。"""
        return _STATUS_DISPLAY_ORDER[self]


_STATUS_DISPLAY_ORDER: dict["RuntimeTaskStatus", int] = {
    RuntimeTaskStatus.RUNNING: 0,
    RuntimeTaskStatus.BLOCKED: 1,
    RuntimeTaskStatus.FAILED: 2,
    RuntimeTaskStatus.WAITING: 3,
    RuntimeTaskStatus.UNKNOWN: 4,
    RuntimeTaskStatus.COMPLETED: 5,
    RuntimeTaskStatus.CLOSED: 6,
}


@dataclass(frozen=True)
class RuntimeTask:
    """一个 Issue Agent 任务的只读摘要行。

    身份固定为 ``repo_id + issue_number``，与 Console 现有 Issue 日志、
    invocation ledger 的读取键一致，前端据此续读详情而不必再猜。

    Attributes:
        repo_id: 注册仓库标识。
        repo_display_name: 仓库展示名；registry 未提供时为 ``None``。
        issue_number: Issue 编号。
        issue_title: Issue 标题；来源缺失时为 ``None``。
        issue_url: Issue 网页链接；来源缺失时为 ``None``。
        pr_url: 该 Issue 关联 PR 的网页链接；尚无 PR 时为 ``None``。
        pr_number: PR 编号；链接无法解析编号时为 ``None``。
        status: 生命周期分桶。
        status_reason: 当前分桶的直接来源，例如监控标签或 Issue 关闭状态。
        anomaly_types: 监控快照发现的异常类型；异常与任务状态分开记录。
        waiting_reason: 等待原因：queued、human_review 或 recheck；非等待时为空。
        review_daemon_running: 本机 PR 值守进程是否存活；未探测或读取失败时为空。
        phase: 最近一次观测到的生命周期阶段名。
        agent: 最近一次运行选择的 Agent 名称。
        unclosed_invocation_executor: 当前运行会话里最近一次尚无结束事件的调用执行器。
        unclosed_invocation_model: 该未收口调用实际请求的模型；未请求模型时为空。
        preset: 最近一次 attempt 使用的模型 preset；旧记录为 ``None``。
        model: 最近一次 attempt 上报的模型名；旧记录为 ``None``。
        attempt_number: 最近一次 attempt 序号。
        prd_path: 该 Issue 关联的 PRD 相对路径；未关联时为 ``None``。
        started_at: 最近一次运行开始时间（ISO-8601 UTC）。
        finished_at: 最近一次运行结束时间；仍在运行时为 ``None``。
        duration_seconds: 最近一次运行耗时；无法计算时为 ``None``。
        last_outcome: 运行账本记录的终态（completed / failed / blocked）。
        labels: 监控看到的 `agent/*` 标签，供前端保持一致的状态文字。
    """

    repo_id: str
    issue_number: int
    repo_display_name: str | None = None
    issue_title: str | None = None
    issue_url: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    status: RuntimeTaskStatus = RuntimeTaskStatus.UNKNOWN
    status_reason: str | None = None
    phase: str | None = None
    agent: str | None = None
    unclosed_invocation_executor: str | None = None
    unclosed_invocation_model: str | None = None
    preset: str | None = None
    model: str | None = None
    attempt_number: int | None = None
    prd_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    last_outcome: str | None = None
    labels: tuple[str, ...] = ()
    anomaly_types: tuple[str, ...] = ()
    waiting_reason: str | None = None
    review_daemon_running: bool | None = None

    @property
    def task_key(self) -> str:
        """返回前端选路与原生窗口去重用的稳定键。"""
        return f"{self.repo_id}#{self.issue_number}"


@dataclass(frozen=True)
class RuntimeTaskSummary:
    """一次运行态摘要：任务列表 + 数据来源可用性披露。

    ``unavailable_sources`` 让页面把「列表为空」与「读取失败 / 尚未同步」
    分开呈现：前者是空态，后者是降级态，两者都不得渲染成零条任务的成功态。

    Attributes:
        generated_at: 本次摘要生成时间（ISO-8601 UTC）。
        tasks: 任务摘要行，按状态优先级与开始时间排序。
        status_counts: 各生命周期分桶的任务数。
        unavailable_sources: 本轮读取失败或尚不可用的来源标识。
        missing_repo_ids: 启用但尚无监控快照的仓库标识。
        sync_status: 沿用监控快照的同步状态（ready / partial / pending_first_sync）。
        scanned_at: 快照最近扫描时间；无快照时为 ``None``。
        truncated: 是否因超出 :data:`RUNTIME_TASK_MAX_ROWS` 被截断。
    """

    generated_at: str
    tasks: tuple[RuntimeTask, ...] = ()
    status_counts: dict[str, int] | None = None
    unavailable_sources: tuple[str, ...] = ()
    missing_repo_ids: tuple[str, ...] = ()
    sync_status: str = "pending_first_sync"
    scanned_at: str | None = None
    truncated: bool = False


__all__ = [
    "RUNTIME_TASK_MAX_ROWS",
    "RUNTIME_TASK_WINDOW_SECONDS",
    "RuntimeTask",
    "RuntimeTaskStatus",
    "RuntimeTaskSummary",
]
