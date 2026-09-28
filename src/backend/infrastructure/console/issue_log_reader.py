"""文件系统版 Issue 日志读取器。

在既有 Console 文件读取边界内，把读取范围限制在「已注册仓库根」下的
固定日志子树 ``logs/agent-runner/issues/<repo_id>/``：

- 仓库解析：``repo_id`` 必须能在当前 registry（``config.toml``）中解析
  为启用的仓库，否则返回 ``REPO_NOT_FOUND``，绝不接受调用方传入的任意
  路径或 repo_id 目录。
- 尝试枚举：只匹配 ``issue-<N>-<时间戳>.log`` 命名，按修改时间取最新；
  不接受客户端传入的文件名。
- 安全读取：打开前做 ``resolve()`` 与父目录比对，拒绝符号链接逃逸；
  读取按字节偏移做有界小块读取，不整文件装载；UTF-8 截断处用
  ``errors="replace"`` 防止跨块乱码。

本模块只依赖 ``core.shared.interfaces`` 与标准库，不反向依赖
``engines`` / ``api``，保持四层依赖方向。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from backend.core.shared.interfaces.issue_log_reader import (
    IssueLogReadRequest,
    IssueLogReadResult,
    IssueLogStatus,
)

_logger = logging.getLogger(__name__)

#: 单次读取的默认字节上限（与进程日志的 ``_DEFAULT_LOG_CHUNK_BYTES`` 对齐）。
_DEFAULT_MAX_BYTES = 64 * 1024

#: 尝试文件名模式：``issue-<N>-<YYYYMMDD>-<HHMMSS>.log``。
_ATTEMPT_NAME_PATTERN = re.compile(r"^issue-(\d+)-(\d{8}-\d{6})\.log$")


def _issue_log_directory(repo_path: Path, repo_id: str) -> Path:
    """返回某仓库下受控的 Issue 日志子树路径。"""
    return repo_path / "logs" / "agent-runner" / "issues" / repo_id


def _resolve_repo_path(
    repo_id: str, repo_path_resolver: Callable[[str], Path | None]
) -> Path | None:
    """把 ``repo_id`` 解析为已注册仓库根；未注册或不可用返回 ``None``。

    ``repo_path_resolver`` 由组装方注入（典型实现来自
    ``resolve_repository_targets`` 的 registry 解析结果），本模块不自行
    glob 用户路径。
    """
    try:
        resolved = repo_path_resolver(repo_id)
    except Exception as exc:  # noqa: BLE001 - 仓库解析失败一律视为不可用。
        _logger.warning("Failed to resolve repository '%s': %s", repo_id, exc)
        return None
    if resolved is None:
        return None
    return resolved.expanduser().resolve()


def _list_attempt_files(log_directory: Path, issue_number: int) -> list[tuple[str, Path, float]]:
    """枚举某 Issue 的全部尝试文件，按修改时间升序。

    返回 ``(attempt_id, path, mtime)`` 三元组；``attempt_id`` 即文件名，
    对客户端不透明。只接受符合命名约定的常规文件，符号链接在后续
    读取前再单独拦截。
    """
    if not log_directory.is_dir():
        return []
    candidates: list[tuple[str, Path, float]] = []
    for entry in log_directory.iterdir():
        match = _ATTEMPT_NAME_PATTERN.match(entry.name)
        if match is None or int(match.group(1)) != issue_number:
            continue
        try:
            stat_result = entry.stat()
        except OSError:
            continue
        candidates.append((entry.name, entry, stat_result.st_mtime))
    candidates.sort(key=lambda item: (item[2], item[0]))
    return candidates


def _is_within_directory(path: Path, directory: Path) -> bool:
    """判断 ``path`` 解析后是否仍位于 ``directory`` 之内（防符号链接逃逸）。"""
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def _drop_incomplete_leading_utf8(raw_chunk: bytes) -> bytes:
    """丢弃块首因任意偏移切分产生的不完整 UTF-8 序列。

    尾部窗口的起点可能落在多字节字符中间；continuation 字节
    （``0b10xxxxxx``）不可能是序列首字节，直接剥掉即可对齐到下一个
    字符边界。
    """
    index = 0
    while index < len(raw_chunk) and (raw_chunk[index] & 0b1100_0000) == 0b1000_0000:
        index += 1
    return raw_chunk[index:]


def read_issue_log_chunk(
    request: IssueLogReadRequest,
    *,
    repo_path_resolver: Callable[[str], Path | None],
) -> IssueLogReadResult:
    """按请求读取一次有界的 Issue 日志块。

    Args:
        request: 读取选择条件（仓库、Issue、可选尝试、字节偏移与上限）。
        repo_path_resolver: 把 ``repo_id`` 解析为已注册仓库根的回调；
            返回 ``None`` 表示未注册或不可用。

    Returns:
        稳定状态的读取结果；任何越界、缺失、截断都映射为显式状态，
        不静默改读别的 Issue 或任意路径。
    """
    if request.offset < 0:
        # 负偏移视同截断：让调用方重置到 0 重读，而不是抛出 500。
        return IssueLogReadResult(
            status=IssueLogStatus.TRUNCATED,
            attempt_id=request.attempt_id,
            content="",
            next_offset=0,
            eof=True,
        )

    repo_path = _resolve_repo_path(request.repo_id, repo_path_resolver)
    if repo_path is None:
        return IssueLogReadResult(
            status=IssueLogStatus.REPO_NOT_FOUND,
            attempt_id=None,
            content="",
            next_offset=0,
            eof=True,
        )

    log_directory = _issue_log_directory(repo_path, request.repo_id)
    attempts = _list_attempt_files(log_directory, request.issue_number)
    if not attempts:
        return IssueLogReadResult(
            status=IssueLogStatus.NO_ATTEMPT,
            attempt_id=None,
            content="",
            next_offset=0,
            eof=True,
        )

    if request.attempt_id is None:
        selected_attempt_id, selected_path, _mtime = attempts[-1]
    else:
        matched = [item for item in attempts if item[0] == request.attempt_id]
        if not matched:
            return IssueLogReadResult(
                status=IssueLogStatus.ATTEMPT_GONE,
                attempt_id=request.attempt_id,
                content="",
                next_offset=0,
                eof=True,
            )
        selected_attempt_id, selected_path, _mtime = matched[-1]

    # 符号链接 / 路径逃逸检查：即使文件名来自我们自己的枚举，也在打开前
    # 再验证一次解析后的真实位置仍在受控子树内。
    if not _is_within_directory(selected_path, log_directory):
        _logger.warning(
            "Refusing to read issue log outside controlled subtree: %s",
            selected_path,
        )
        return IssueLogReadResult(
            status=IssueLogStatus.ATTEMPT_GONE,
            attempt_id=selected_attempt_id,
            content="",
            next_offset=0,
            eof=True,
        )

    try:
        file_size = selected_path.stat().st_size
    except OSError:
        return IssueLogReadResult(
            status=IssueLogStatus.ATTEMPT_GONE,
            attempt_id=selected_attempt_id,
            content="",
            next_offset=0,
            eof=True,
        )

    if request.offset > file_size:
        # 文件在两次读取之间被截断或替换：显式报告，禁止把旧偏移拼到
        # 新内容上（会把旧尝试错拼成当前进度）。
        return IssueLogReadResult(
            status=IssueLogStatus.TRUNCATED,
            attempt_id=selected_attempt_id,
            content="",
            next_offset=0,
            eof=True,
        )

    max_bytes = max(request.max_bytes, 1)
    # 尾部窗口：忽略请求偏移，从 ``max(0, size - max_bytes)`` 起读，返回的
    # ``next_offset`` 对齐到文件末尾，调用方原地转入续读即可。窗口起点可能
    # 落在多字节字符中间：丢弃首个不完整序列，避免首行出现乱码字符。
    start_offset = max(0, file_size - max_bytes) if request.tail else request.offset
    with selected_path.open("rb") as log_file:
        log_file.seek(start_offset)
        raw_chunk = log_file.read(max_bytes)
        next_offset = log_file.tell()
        eof = log_file.read(1) == b""

    if request.tail and start_offset > 0:
        raw_chunk = _drop_incomplete_leading_utf8(raw_chunk)

    return IssueLogReadResult(
        status=IssueLogStatus.OK,
        attempt_id=selected_attempt_id,
        content=raw_chunk.decode("utf-8", errors="replace"),
        next_offset=next_offset,
        eof=eof,
    )


class FilesystemIssueLogReader:
    """把 :func:`read_issue_log_chunk` 适配为 ``IssueLogReader`` 端口。

    通过注入的 ``repo_path_resolver`` 与 registry 对齐；读取规则本身
    不依赖具体配置来源，便于测试与不同宿主（CLI / Console API）复用。
    """

    def __init__(self, repo_path_resolver: Callable[[str], Path | None]) -> None:
        """注入仓库路径解析回调。"""
        self._repo_path_resolver = repo_path_resolver

    def read_issue_log(self, request: IssueLogReadRequest) -> IssueLogReadResult:
        """读取一次有界日志块；参数与状态语义见 :func:`read_issue_log_chunk`。"""
        return read_issue_log_chunk(request, repo_path_resolver=self._repo_path_resolver)


__all__ = ["FilesystemIssueLogReader", "read_issue_log_chunk"]
