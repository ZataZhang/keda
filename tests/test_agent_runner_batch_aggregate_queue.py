"""队列侧聚合接线的本地测试：批次成立判定、整批失败短路，全程不触碰 GitHub。

这些断言只覆盖 :mod:`backend.core.use_cases.agent_runner_batch_aggregate_queue`
的队列判定；真实来源解析、发布与收尾（rv-4）需要真实 GitHub，不用 fake 作证据。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_batch_aggregate_queue import (
    AggregateQueueCompletionRequest,
    check_aggregate_candidate_shortfall,
    complete_aggregate_queue,
    log_aggregate_dry_run_preview,
)
from backend.core.use_cases.agent_runner_orchestration_runtime import RunOnceRequest


class _ForbiddenGitHubClient:
    """聚合判定不应发起任何 GitHub 调用：一旦取属性就判失败。"""

    def __getattr__(self, dependency_name: str) -> object:
        raise AssertionError(
            f"aggregate queue must not reach GitHub, asked for {dependency_name!r}"
        )


class _ForbiddenProcessRunner:
    """同上：进程侧协作者在这些短路路径里也不应被触达。"""

    def __getattr__(self, dependency_name: str) -> object:
        raise AssertionError(
            f"aggregate queue must not spawn processes, asked for {dependency_name!r}"
        )


def _queue_run_request(*, aggregate_pr: bool) -> RunOnceRequest:
    """构造只带队列声明的 ``RunOnceRequest``，协作者全部换成禁调用哨兵。"""
    return RunOnceRequest(
        repo_path=Path("/tmp/repository"),
        config=AppConfig(),
        dry_run=False,
        agent="auto",
        max_issues=2,
        github_client=_ForbiddenGitHubClient(),  # type: ignore[arg-type]
        process_runner=_ForbiddenProcessRunner(),  # type: ignore[arg-type]
        aggregate_pr=aggregate_pr,
    )


def test_aggregate_candidate_shortfall_rejects_undersized_batch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """本轮选中的 Issue 不足两个时聚合不成立：记 error 并给出退出码 1。"""
    with caplog.at_level(logging.ERROR):
        shortfall_exit_code = check_aggregate_candidate_shortfall(1)
    assert shortfall_exit_code == 1
    assert "Aggregate batch rejected" in caplog.text


def test_aggregate_candidate_shortfall_accepts_full_batch() -> None:
    """选中数够一批时返回 ``None``，调度继续处理这些 Issue。"""
    assert check_aggregate_candidate_shortfall(2) is None
    assert check_aggregate_candidate_shortfall(5) is None


def test_complete_aggregate_queue_without_aggregate_declaration() -> None:
    """未声明聚合的队列运行只按 worker 退出码汇总，不进入聚合生命周期。"""
    for worker_exit_codes, expected_exit_code in (((0, 0), 0), ((0, 1), 1)):
        completion_exit_code = complete_aggregate_queue(
            AggregateQueueCompletionRequest(
                run_request=_queue_run_request(aggregate_pr=False),
                issue_numbers=(101, 102),
                exit_codes=worker_exit_codes,
            )
        )
        assert completion_exit_code == expected_exit_code


def test_complete_aggregate_queue_short_circuits_on_failed_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """整批未全绿时不发布总 PR：失败 Issue 逐项点名，来源 PR 留给修复。"""
    with caplog.at_level(logging.ERROR):
        completion_exit_code = complete_aggregate_queue(
            AggregateQueueCompletionRequest(
                run_request=_queue_run_request(aggregate_pr=True),
                issue_numbers=(101, 102),
                exit_codes=(0, 1),
            )
        )
    assert completion_exit_code == 1
    assert "these Issues failed: [102]" in caplog.text
    assert "No total PR was created" in caplog.text


def test_log_aggregate_dry_run_preview_counts_issues_and_unique_prds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dry-run 预览报告批次规模与去重后的 PRD 路径，不认领也不写入。"""

    def _issue_with_prd(issue_number: int, prd_path: str | None) -> IssueSummary:
        prd_anchor_line = f"PRD path: `{prd_path}`\n" if prd_path else ""
        return IssueSummary(
            number=issue_number,
            title=f"Issue #{issue_number}",
            url=f"https://example.test/issues/{issue_number}",
            body=f"{prd_anchor_line}context",
            labels=("agent/ready",),
        )

    with caplog.at_level(logging.INFO):
        log_aggregate_dry_run_preview(
            [
                _issue_with_prd(101, "tasks/pending/a.md"),
                _issue_with_prd(102, "tasks/pending/a.md"),
                _issue_with_prd(103, None),
            ]
        )
    assert "would include 3 Issues and 1 distinct PRD paths" in caplog.text
    assert "DRY RUN: aggregate PRD: tasks/pending/a.md" in caplog.text
