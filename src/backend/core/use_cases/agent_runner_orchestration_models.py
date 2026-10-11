"""Agent Runner 编排请求模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IContentGenerator,
    IGitHubClient,
    IProcessRunner,
)
from backend.core.shared.interfaces.runner_console import IRunHistoryStore
from backend.core.shared.interfaces.runner_live_view import IRunnerLiveView
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.publish_stage import PublishStage


@dataclass(frozen=True)
class PrdReworkRequest:
    """一轮 PRD rework Issue 处理请求。"""

    repo_path: Path
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    content_generator: IContentGenerator | None = None
    max_issues: int = 1


@dataclass(frozen=True)
class RunOnceRequest:
    """Agent Runner 单轮队列调度请求。"""

    repo_path: Path
    config: AppConfig
    dry_run: bool
    agent: str
    max_issues: int
    github_client: IGitHubClient
    process_runner: IProcessRunner
    content_generator: IContentGenerator | None = None
    run_history_store: IRunHistoryStore | None = None
    run_trigger: str = "cli_run"
    repo_id: str | None = None
    concurrency: int = 1
    output_view: IRunnerLiveView | None = None
    #: 定向目标 Issue 编号（``kc run --issue``）。非 ``None`` 时只处理该
    #: Issue（仍走依赖门禁与 claim）；``None`` 保持"按优先级捞队列"行为。
    target_issue: int | None = None
    #: 发布档位（``kc run --fast-merge`` / ``--direct-pr``）：本次运行执行 agent
    #: 之后还剩多少门禁与第二个 agent。``NORMAL`` 即默认全量路径；daemon 与其余
    #: 调用方传的仍是 ``NORMAL``（默认值），但它是**调用侧请求档位**——认领后
    #: core 会用 Issue 上的 ``direct-pr`` 标签把它升级为 ``DIRECT``（逐 Issue 独立）。
    publish_stage: PublishStage = PublishStage.NORMAL
    #: 仅显式队列运行聚合；必须等待本轮全部 worker 完成后才可调用聚合用例。
    aggregate_pr: bool = False
    #: 统一自动执行并发上限（由调用方经 ``resolve_execution_ceiling`` 解析出的
    #: 单一生效值）。非 ``None`` 时只约束 **ready 通道的新认领**：本轮认领数 =
    #: max(0, ceiling − 在途 running 数)，认领数不足即少领；``None`` 表示不设
    #: 闸门——人显式点名的路径（``kc run --issue`` / 控制台单PRD 开始）保持全量。
    #: running / blocked / cleanup 通道是恢复性处理，不新增并发，不受该值约束。
    execution_ceiling: int | None = None
