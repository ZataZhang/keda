"""Agent 执行过程中的记忆存取适配（长期 / 技能 / 短期记忆）。

从 :mod:`backend.core.use_cases.run_agent_once` 拆分而来：单文件非空行有 CI 硬
上限，而这两个函数只做"拼出 memory service 再按需落一条短期记忆"的适配，与
agent 调用本体无关——搬到这里既给主文件腾出余量，也让执行循环与 attempt 记录
有一条共同的记忆入口。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.agent.memory import save_short_term_memory
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AttemptResult,
    FailureType,
    IssueSummary,
)

_logger = logging.getLogger(__name__)


def _resolve_memory_stores(worktree_path: Path, memory_config):
    """Construct the long-term + skill stores for prompt injection.

    Returns ``(None, None)`` when memory is disabled so callers can fall
    back to non-injecting behaviour without sprinkling the same guard
    everywhere. The actual composition lives in
    ``core/agent/memory/_composition.py`` which dynamically loads the
    ``infrastructure/`` implementations, preserving the strict
    ``core -> infrastructure`` ban.
    """
    from backend.core.agent.memory._composition import (
        build_default_memory_services,
    )

    services = build_default_memory_services(worktree_path, memory_config)
    return services.long_term, services.skill


def _persist_short_term_memory(
    *,
    config: AppConfig,
    issue: IssueSummary,
    worktree_path: Path,
    attempt: AttemptResult,
    repo_id: str,
) -> None:
    """Best-effort save of a single attempt into the short-term memory store."""
    if not config.memory.enabled:
        return
    try:
        from backend.core.agent.memory._composition import (
            build_default_memory_services,
        )

        services = build_default_memory_services(worktree_path, config.memory)
        if services.short_term is None:
            return
        save_short_term_memory(
            repo_id=repo_id,
            issue=issue,
            attempt_result=attempt,
            worktree_path=worktree_path,
            memory_config=config.memory,
            final_solution=(
                attempt.detail
                if attempt.failure_type is FailureType.SUCCESS and attempt.detail
                else None
            ),
            store=services.short_term,
        )
    except Exception as exc:  # noqa: BLE001 - memory side-channel must not break runner.
        _logger.warning(
            "Failed to record short-term memory for Issue #%d attempt %d: %s",
            issue.number,
            attempt.attempt_number,
            exc,
        )
