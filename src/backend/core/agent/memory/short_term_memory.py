"""短期记忆写业务规则。"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.agent.memory.protocols import (
    IShortTermMemoryStore,
    ShortTermAttempt,
    ShortTermContextPayload,
)
from backend.core.shared.models.agent_runner import (
    AttemptResult,
    IssueSummary,
    MemoryConfig,
)

_logger = logging.getLogger(__name__)


def save_short_term_memory(
    repo_id: str,
    issue: IssueSummary,
    attempt_result: AttemptResult,
    worktree_path: Path,
    memory_config: MemoryConfig,
    *,
    final_solution: str | None = None,
    key_files: tuple[str, ...] = (),
    summary: str | None = None,
    store: IShortTermMemoryStore,
) -> Path | None:
    """在一次恢复尝试后更新 Issue 的短期记忆。

    Args:
        repo_id: 稳定的仓库标识。
        issue: 正在处理的 Issue。
        attempt_result: 刚完成的尝试结果。
        worktree_path: 用于解析 ``memory_config.base_dir`` 并持久化记忆的 worktree。
        memory_config: 生效的记忆配置。
        final_solution: 可选的最终解决方案摘要。
        key_files: 可选的已修改文件路径列表。
        summary: 可选的稳定任务摘要；未提供时使用最近一次 attempt 详情。
        store: 由调用方注入的短期记忆存储。

    Returns:
        已写入的 ``context.json`` 路径；未启用记忆持久化时返回 ``None``。
    """
    if not memory_config.enabled:
        return None
    if not worktree_path:
        return None
    existing = store.load(repo_id, issue.number) or ShortTermContextPayload(
        repo_id=repo_id,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_url=issue.url,
    )
    existing.issue_title = issue.title or existing.issue_title
    existing.issue_url = issue.url or existing.issue_url
    if summary is not None:
        existing.summary = summary
    elif attempt_result.detail:
        # 未提供任务摘要时，让摘要跟随最近一次 attempt，避免重试后留下旧故障。
        existing.summary = attempt_result.detail
    existing.attempts.append(
        ShortTermAttempt(
            attempt_number=attempt_result.attempt_number,
            failure_type=attempt_result.failure_type.value,
            detail=attempt_result.detail,
            recovered=attempt_result.recovered,
        )
    )
    if final_solution is not None:
        existing.final_solution = final_solution
    if key_files:
        merged = list(existing.key_files)
        for path in key_files:
            if path and path not in merged:
                merged.append(path)
        existing.key_files = tuple(merged)
    try:
        path = store.save(repo_id, issue.number, existing)
    except OSError as exc:
        _logger.warning(
            "Failed to persist short-term memory for Issue #%d: %s",
            issue.number,
            exc,
        )
        return None
    return path


__all__ = ["save_short_term_memory"]
