"""Roadmap 仓库级 Autopilot 控制用例。

职责边界（刻意保守）：

- **读**：把「仓库 local 配置的持久值」「生效配置」「daemon 是否真的在运行」
  「Roadmap 并发上限」聚合成一份状态快照，供页面如实展示完整闭环条件。
- **写**：只经受限端口修改目标仓库 ``.iar.toml`` 的
  ``[agent_runner.autopilot].enabled``，并在写回后 fresh load 生效配置作为
  成功判据（不回显请求体冒充持久化结果）。

不做的事：不 spawn / 停止 daemon；不修改 ``safety.auto_merge``；不调用
``advance_roadmap_queue``——运行中的 daemon 下一轮自然读取新配置，既有持续
调度链不变。``autopilot.enabled`` 与 ``safety.auto_merge`` 的双重门禁是既有
不可逆远端合并的安全契约，本用例不允许把它折叠成一个开关。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from backend.core.shared.interfaces.runner_console import (
    IRepositoryAutopilotSettingsEditor,
    IRunnerProcessSupervisor,
    RunnerProcessKind,
    RunnerProcessRecord,
)
from backend.core.shared.models.agent_runner import RepositoryRunContext

#: supervisor 记录中表示进程仍然存活的状态值。
_RUNNING_STATUS = "running"


class RoadmapAutopilotError(ValueError):
    """Autopilot 读取或写回失败：仓库不存在、配置不可写或写后读回不一致。"""


@dataclass(frozen=True)
class RoadmapAutopilotState:
    """当前仓库 Autopilot 完整闭环状态快照。

    Attributes:
        repo_id: 目标仓库 ID。
        enabled: **生效**配置中的 ``autopilot.enabled``（fresh load 结果）。
        auto_merge_enabled: ``safety.auto_merge``；第二道危险动作门禁，只读展示。
        daemon_running: 该仓库是否存在运行中的 daemon 进程。
        max_parallel: Roadmap 并发上限（来自既有 roadmap settings）。
        config_source: Autopilot 持久值的来源文件（仓库相对路径）。
        persisted_enabled: 仓库本地配置中的持久值；``None`` 表示文件缺失或键
            未设置（此时生效值来自全局配置默认值）。
    """

    repo_id: str
    enabled: bool
    auto_merge_enabled: bool
    daemon_running: bool
    max_parallel: int
    config_source: str
    persisted_enabled: bool | None


def daemon_is_running(repo_id: str, records: Sequence[RunnerProcessRecord]) -> bool:
    """判断某个仓库是否存在运行中的 daemon。

    daemon 状态复用既有 process supervisor 记录（含面板托管与发现出的
    unmanaged 进程），不新增心跳表或轮询线程。 ``status`` 字段可能带
    ``list_unmanaged_processes`` 写入的后缀，故用包含而非等值判断。

    Args:
        repo_id: 目标仓库 ID。
        records: 进程记录快照（通常来自 ``supervisor.list_processes()``）。

    Returns:
        存在任一 ``kind=daemon`` 且存活于该仓库的进程时为 ``True``。
    """
    for record in records:
        if record.repo_id != repo_id:
            continue
        if record.kind is not RunnerProcessKind.DAEMON:
            continue
        if _RUNNING_STATUS in str(record.status):
            return True
    return False


def _resolve_context(
    repo_id: str, contexts: Sequence[RepositoryRunContext]
) -> RepositoryRunContext:
    """从已解析的仓库上下文中取出目标仓库。"""
    for context in contexts:
        if context.repo_id == repo_id:
            return context
    raise RoadmapAutopilotError(f"仓库 '{repo_id}' 不存在或未启用。")


def _relative_source_name(config_source: Path, repo_root_path: Path) -> str:
    """尽量把配置来源表示为仓库相对路径，越界时退回绝对路径。"""
    resolved_repo_root = Path(repo_root_path)
    try:
        return str(config_source.resolve().relative_to(resolved_repo_root.resolve()))
    except ValueError:
        return str(config_source)


def load_autopilot_state(
    *,
    repo_id: str,
    contexts: Sequence[RepositoryRunContext],
    supervisor: IRunnerProcessSupervisor,
    max_parallel: int,
    editor: IRepositoryAutopilotSettingsEditor,
) -> RoadmapAutopilotState:
    """聚合并返回当前仓库的 Autopilot 状态快照（每次调用都必须 fresh）。"""
    context = _resolve_context(repo_id, contexts)
    persisted_enabled = editor.read_enabled(context.repo_path)
    return RoadmapAutopilotState(
        repo_id=repo_id,
        enabled=bool(context.config.autopilot.enabled),
        auto_merge_enabled=bool(context.config.safety.auto_merge),
        daemon_running=daemon_is_running(repo_id, supervisor.list_processes()),
        max_parallel=max_parallel,
        config_source=_relative_source_name(
            editor.config_source_path(context.repo_path), context.repo_path
        ),
        persisted_enabled=persisted_enabled,
    )


def set_autopilot_enabled(
    *,
    repo_id: str,
    enabled: bool,
    editor: IRepositoryAutopilotSettingsEditor,
    contexts_loader: Callable[[], Sequence[RepositoryRunContext]],
    supervisor: IRunnerProcessSupervisor,
    max_parallel: int,
) -> RoadmapAutopilotState:
    """修改目标仓库 ``autopilot.enabled``，并以 fresh load 读回作为成功判据。

    Args:
        repo_id: 目标仓库 ID。
        enabled: 目标布尔值。
        editor: 受限仓库配置写回端口。
        contexts_loader: 重新解析生效配置的加载器（写后必须重新调用，
            以便从磁盘而不是内存拿到最新值）。
        supervisor: 进程监管端口，用于如实报告 daemon 是否在跑。
        max_parallel: Roadmap 并发上限（不受本写回影响，原样透传）。

    Returns:
        写回并重新加载后的状态快照。

    Raises:
        RoadmapAutopilotError: 仓库不存在、写回失败，或写后生效值与请求值不一致。
    """
    context = _resolve_context(repo_id, contexts_loader())
    try:
        editor.set_enabled(context.repo_path, enabled)
    except ValueError as exc:
        # 受限端口以 ValueError 表达「文件缺失 / 结构非法 / 校验失败」：
        # 统一转成用例层错误，避免 API 泄漏 infrastructure 异常类型。
        raise RoadmapAutopilotError(str(exc)) from exc

    # 成功判据必须是「写后 fresh load 的生效配置」，而不是请求体回显：
    # 只有重新走一遍 resolver 才能证明原子替换后的文件被既有 loader 正确读取。
    fresh_context = _resolve_context(repo_id, contexts_loader())
    if bool(fresh_context.config.autopilot.enabled) is not enabled:
        raise RoadmapAutopilotError(
            "Autopilot 写回后重新加载的配置与请求值不一致，原文件可能未被正确替换。"
        )
    return load_autopilot_state(
        repo_id=repo_id,
        contexts=(fresh_context,),
        supervisor=supervisor,
        max_parallel=max_parallel,
        editor=editor,
    )
