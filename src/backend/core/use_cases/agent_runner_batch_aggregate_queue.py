"""夜间批次聚合接入单轮队列调度的接线：worker 全部 join 后的发布与诊断。

聚合的生命周期本身属于 :mod:`backend.core.use_cases.agent_runner_batch_aggregate`；
本模块只负责**队列侧**的判定——本轮是否声明了聚合、整批是否全部成功、把队列请求
转换成聚合用例入参，以及把失败翻译成退出码。这样调度入口模块不必承载聚合分支。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.shared.models.agent_runner import IssueSummary

if TYPE_CHECKING:
    from backend.core.use_cases.agent_runner_orchestration_runtime import RunOnceRequest

_logger = logging.getLogger(__name__)

__all__ = [
    "AggregateQueueCompletionRequest",
    "check_aggregate_candidate_shortfall",
    "complete_aggregate_queue",
    "log_aggregate_dry_run_preview",
]


@dataclass(frozen=True)
class AggregateQueueCompletionRequest:
    """完整 worker join 后交给批次聚合的队列运行结果。"""

    run_request: RunOnceRequest
    issue_numbers: tuple[int, ...]
    exit_codes: tuple[int, ...]


def check_aggregate_candidate_shortfall(candidate_count: int) -> int | None:
    """队列本轮选中的 Issue 不足两个时给出退出码；够一批时返回 ``None`` 继续执行。

    Args:
        candidate_count: 本轮实际选中、准备交给聚合用例的 Issue 数量。

    Returns:
        ``1`` 表示批次不成立必须结束本轮；``None`` 表示可以继续处理这些 Issue。
    """
    from backend.core.use_cases.agent_runner_batch_aggregate import (
        validate_aggregate_candidate_count,
    )

    try:
        validate_aggregate_candidate_count(candidate_count)
    except ValueError as exc:
        _logger.error("Aggregate batch rejected: %s.", exc)
        return 1
    return None


def log_aggregate_dry_run_preview(batch_issues: Sequence[IssueSummary]) -> None:
    """dry-run 下预览本轮聚合批次的规模与可识别 PRD 路径，不认领也不写入。

    Args:
        batch_issues: 本轮被选中、将要成为批次成员的 Issue。
    """
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    preview_prd_paths = sorted(
        {path for issue in batch_issues if (path := extract_prd_path(issue.body)) is not None}
    )
    _logger.info(
        "DRY RUN: aggregate batch would include %d Issues and %d distinct PRD paths.",
        len(batch_issues),
        len(preview_prd_paths),
    )
    for prd_path in preview_prd_paths:
        _logger.info("DRY RUN: aggregate PRD: %s", prd_path)


def complete_aggregate_queue(request: AggregateQueueCompletionRequest) -> int:
    """所有 Issue worker 完成后，仅在整批成功时执行聚合发布。"""
    if not request.run_request.aggregate_pr:
        return 1 if any(request.exit_codes) else 0
    if any(request.exit_codes):
        failed_issue_numbers = [
            issue_number
            for issue_number, exit_code in zip(
                request.issue_numbers, request.exit_codes, strict=True
            )
            if exit_code
        ]
        _logger.error(
            "Aggregate batch stopped because these Issues failed: %s. "
            "No total PR was created; source PRs remain available for repair.",
            failed_issue_numbers,
        )
        return 1

    from backend.core.use_cases.agent_runner_batch_aggregate import (
        BatchAggregateError,
        BatchAggregateRequest,
        BatchSourceResolutionRequest,
        aggregate_batch,
        resolve_batch_repo_identifier,
        resolve_batch_sources,
    )

    run_request = request.run_request
    effective_repo_id = run_request.repo_id or run_request.repo_path.name
    try:
        repo_identifier = resolve_batch_repo_identifier(
            repo_path=run_request.repo_path,
            repo_id=effective_repo_id,
            config=run_request.config,
        )
        sources = resolve_batch_sources(
            BatchSourceResolutionRequest(
                repo_path=run_request.repo_path,
                github_client=run_request.github_client,
                config=run_request.config,
                repo_id=effective_repo_id,
                issue_numbers=request.issue_numbers,
                repo_identifier=repo_identifier,
            )
        )
        result = aggregate_batch(
            BatchAggregateRequest(
                repo_path=run_request.repo_path,
                repo_id=effective_repo_id,
                config=run_request.config,
                github_client=run_request.github_client,
                process_runner=run_request.process_runner,
                sources=sources,
            )
        )
    except BatchAggregateError as exc:
        _logger.error(
            "Aggregate batch failed during %s: %s%s",
            exc.failure_category,
            exc,
            f" Retry with `{exc.retry_command}`." if exc.retry_command else "",
        )
        return 1
    _logger.info(
        "Batch review entry: %s; source PRs closed=%s; Issues=%d, unique PRDs=%d; "
        "base=%s head=%s tree=%s.",
        result.total_pr_url,
        list(result.source_pr_numbers_closed),
        len(sources),
        len(result.prd_paths),
        result.base_sha,
        result.head_sha,
        result.tree_sha,
    )
    return 0
