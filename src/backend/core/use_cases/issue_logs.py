"""按 Issue 读取 Agent 输出日志的统一选择规则。

CLI（``iar logs --issue``）与 Console API
（``GET .../issues/{issue_number}/logs``）共享同一套语义，本模块是唯一
事实源：

- 默认跟随该 Issue 的**最新尝试**；客户端拿到 ``attempt_id`` 后在续读时
  原样回传，重试产生新文件时由客户端显式切换并重置偏移。
- 缺失、清理、截断、未注册仓库分别映射为稳定状态，CLI/API 各自渲染为
  可读提示，不把「进程还活着」或「另一个 Issue 的日志」冒充成当前进度。
- 读取范围只经 :class:`IssueLogReader` 窄端口；core 不直接 glob 任意
  路径，也不感知 ``Path`` 布局细节。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.issue_log_reader import (
    IssueLogReader,
    IssueLogReadRequest,
    IssueLogStatus,
)

#: CLI 首次读取时回看最近内容的默认字节窗口（约等于 64 KiB 尾部）。
#: 与进程日志的 ``_DEFAULT_LOG_CHUNK_BYTES`` 对齐，避免首次加载过大。
DEFAULT_TAIL_BYTES = 64 * 1024

#: 尝试终态标记：sink 在每次尝试收尾时把它追加到 per-Issue 日志末尾。
#: ``iar logs --issue --follow`` 以此为「运行结束」的唯一依据——裸 EOF 只表示
#: 这一刻没有新字节（Agent 两次写入之间、重试间隔里都会出现），把 EOF 当成结束
#: 会提前退出并丢掉后续输出。
ATTEMPT_END_MARKER = "[iar-attempt-end]"


@dataclass(frozen=True)
class IssueLogSelection:
    """一次 Issue 日志读取的高层结果，供 CLI / API 渲染。

    ``latest_attempt_id`` 始终是当前最新尝试（若存在），供调用方判断
    「是否出现了新尝试」；``status`` 为底层读取状态。
    """

    status: IssueLogStatus
    attempt_id: str | None
    latest_attempt_id: str | None
    content: str
    next_offset: int
    eof: bool


def read_issue_log(
    *,
    reader: IssueLogReader,
    repo_id: str,
    issue_number: int,
    attempt_id: str | None = None,
    offset: int = 0,
    max_bytes: int = DEFAULT_TAIL_BYTES,
    tail: bool = False,
) -> IssueLogSelection:
    """按仓库 + Issue 读取一次日志块，并附带上「最新尝试」供切换判断。

    Args:
        reader: Issue 日志窄端口实现。
        repo_id: 已注册仓库 ID。
        issue_number: Issue 编号（必须为正整数）。
        attempt_id: 显式指定的尝试；``None`` 表示读最新尝试。
        offset: 字节偏移；``0`` 表示从头读。
        max_bytes: 单次读取字节上限。
        tail: ``True`` 时忽略 ``offset``，返回末尾 ``max_bytes`` 字节的
            尾部窗口；``next_offset`` 对齐到文件末尾，便于原地续读。

    Returns:
        带最新尝试标识的读取结果；``status`` 非 ``OK`` 时 ``content``
        为空，``latest_attempt_id`` 仍反映当前最新尝试（便于提示切换）。
    """
    result = reader.read_issue_log(
        IssueLogReadRequest(
            repo_id=repo_id,
            issue_number=issue_number,
            attempt_id=attempt_id,
            offset=offset,
            max_bytes=max_bytes,
            tail=tail,
        )
    )

    # ``latest_attempt_id`` 必须始终反映「当前最新尝试」，而不是「本次读到
    # 的尝试」；否则客户端无法发现重试产生的新文件。因此无论本次读取是否
    # 命中显式 ``attempt_id``，都单独探一次最新尝试。
    latest = reader.read_issue_log(
        IssueLogReadRequest(
            repo_id=repo_id,
            issue_number=issue_number,
            attempt_id=None,
            offset=0,
            max_bytes=1,
        )
    )
    latest_attempt_id = latest.attempt_id if latest.status is IssueLogStatus.OK else None

    return IssueLogSelection(
        status=result.status,
        attempt_id=result.attempt_id,
        latest_attempt_id=latest_attempt_id,
        content=result.content,
        next_offset=result.next_offset,
        eof=result.eof,
    )


def describe_issue_log_status(selection: IssueLogSelection, *, issue_number: int) -> str:
    """把读取状态渲染为一句可读的 CLI / 日志提示。"""
    if selection.status is IssueLogStatus.NO_ATTEMPT:
        return f"Issue #{issue_number} 暂无可用输出（尚未开始或日志已清理）。"
    if selection.status is IssueLogStatus.ATTEMPT_GONE:
        if selection.latest_attempt_id and selection.latest_attempt_id != selection.attempt_id:
            return (
                f"Issue #{issue_number} 的尝试 {selection.attempt_id} 已不可用；"
                f"已切换到最新尝试 {selection.latest_attempt_id}。"
            )
        return f"Issue #{issue_number} 的尝试 {selection.attempt_id} 已不可用（日志已清理）。"
    if selection.status is IssueLogStatus.TRUNCATED:
        return f"Issue #{issue_number} 的日志已轮转或截断，已从最新位置续读。"
    if selection.status is IssueLogStatus.REPO_NOT_FOUND:
        return "目标仓库未注册或已禁用，无法读取 Issue 输出。"
    return ""


def create_issue_log_reader(
    repo_path_resolver: Callable[[str], Path | None],
) -> IssueLogReader:
    """创建默认的文件系统 Issue 日志读取器。

    Core 只面向 ``IssueLogReader`` 端口编程，但具体的文件系统实现由
    Infrastructure 提供；为保持 ``core`` 不静态 import ``infrastructure``
    的架构约束（``hooks/shared/check_architecture.py`` 的 AST 检查），这里
    用 ``importlib`` 在运行时解析实现，与
    :mod:`backend.core.use_cases.agent_runner_factory` 的做法一致。

    Args:
        repo_path_resolver: 把 ``repo_id`` 解析为已注册仓库根的回调；
            返回 ``None`` 表示未注册或不可用。

    Returns:
        面向已注册仓库固定日志子树的只读读取器。
    """
    import importlib

    reader_module = importlib.import_module("backend.infrastructure.console.issue_log_reader")
    return reader_module.FilesystemIssueLogReader(repo_path_resolver)


__all__ = [
    "DEFAULT_TAIL_BYTES",
    "IssueLogSelection",
    "create_issue_log_reader",
    "describe_issue_log_status",
    "read_issue_log",
]
