"""处理标记为 PRD rework 的 Issue（Phase 1：生成/重写 PRD 并排队 ready）。

从 :mod:`backend.core.use_cases.agent_runner_orchestration_runtime` 拆分而来：
单文件非空行有 CI 硬上限，而 PRD rework 是 ready/rework/blocked 派发循环之前的
独立阶段，与 :func:`run_once` 不共享任何状态。
"""

from __future__ import annotations

import logging

from backend.core.use_cases.agent_runner_orchestrate import create_or_reuse_worktree
from backend.core.use_cases.agent_runner_orchestration_models import PrdReworkRequest
from backend.core.use_cases.create_prd_from_issue import (
    CreatePrdFromIssueRequest,
    create_prd_from_issue,
)

_logger = logging.getLogger(__name__)

# 在被派发前由 ``agent_runner_orchestrate._orchestration_runtime_module``
# 从 orchestrate 的命名空间同步过来（与 runtime / issue_handlers 同一套机制，
# 保持单一补丁中枢，测试与集成方仍在 ``agent_runner_orchestrate`` 上替换）。
RUNTIME_DEPENDENCY_NAMES = ("create_or_reuse_worktree",)


def process_prd_rework_issues(request: PrdReworkRequest) -> None:
    """处理标记为 PRD rework 的 Issue。

    ready 执行前为每个 Issue 建/复用 ``issue-<N>`` worktree，生成/重写 PRD，
    提交到对应分支并经 draft PR 发布，再更新 Issue；失败只记录并继续，不影响主工作树和 ready 队列。

    Args:
        repo_path: 目标仓库路径。
        config: 应用配置。
        github_client: GitHub 客户端。
        process_runner: git 命令执行器（建/复用 worktree、commit、push）。
        content_generator: 可选的 AI 内容生成器。
        max_issues: 本轮最多处理的 rework-prd Issue 数量。
    """
    repo_path = request.repo_path
    config = request.config
    github_client = request.github_client
    process_runner = request.process_runner
    content_generator = request.content_generator
    issues = github_client.list_rework_prd_issues(
        config.labels.rework_prd,
        limit=request.max_issues,
    )
    for issue in issues:
        _logger.info("Processing PRD rework for Issue #%d: %s", issue.number, issue.title)
        try:
            worktree_path = create_or_reuse_worktree(repo_path, issue, config, process_runner)
            create_prd_from_issue(
                request=CreatePrdFromIssueRequest(
                    repo_path=repo_path,
                    issue=issue,
                    config=config,
                    generated_content_config=config.generated_content,
                    content_generator=content_generator,
                    queue_ready=True,
                    worktree_path=worktree_path,
                    process_runner=process_runner,
                ),
                github_client=github_client,
            )
        except Exception as exc:  # noqa: BLE001 - isolate PRD rework failures.
            _logger.exception("PRD rework failed for Issue #%d", issue.number)
            try:
                github_client.edit_issue_labels(
                    issue.number,
                    add=[config.labels.failed],
                    remove=[config.labels.rework_prd],
                )
            except Exception as label_exc:  # noqa: BLE001 - best-effort label update.
                _logger.error(
                    "Failed to mark Issue #%d as %s: %s",
                    issue.number,
                    config.labels.failed,
                    label_exc,
                )
            try:
                github_client.comment_issue(
                    issue.number,
                    f"PRD generation failed: {exc}\n\n"
                    "Please review the error and re-add the "
                    f"`{config.labels.rework_prd}` label to retry.",
                )
            except Exception as comment_exc:  # noqa: BLE001 - best-effort comment.
                _logger.error(
                    "Failed to comment on Issue #%d PRD failure: %s",
                    issue.number,
                    comment_exc,
                )
