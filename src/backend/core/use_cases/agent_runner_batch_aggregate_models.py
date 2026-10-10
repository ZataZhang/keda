"""夜间批次聚合生命周期共享的数据结构与诊断错误。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    PullRequestContext,
)

REQUIRED_CHECKS_STATE = "SUCCESS"


class BatchAggregateError(RuntimeError):
    """批次聚合生命周期任一阶段失败时抛出的可诊断错误。

    Attributes:
        failure_category: 失败阶段分类，供调用方按阶段决定恢复动作。
        retry_command: 可复制的显式重试命令；任务本身失败等不安全重试时为 ``None``。
    """

    def __init__(
        self,
        message: str,
        *,
        failure_category: str,
        retry_command: str | None = None,
    ) -> None:
        """记录错误文案、失败阶段与可选重试命令。"""
        super().__init__(message)
        self.failure_category = failure_category
        self.retry_command = retry_command


class AggregateConflictError(BatchAggregateError):
    """集成分支合并来源 head 时发生 Git 冲突。"""

    def __init__(
        self,
        head_sha: str,
        conflict_result: CommandResult,
        *,
        retry_command: str | None = None,
    ) -> None:
        """记录冲突来源 head 与 ``git merge`` 的返回结果。"""
        super().__init__(
            f"aggregate merge conflicted on source head {head_sha}; "
            "aborted before changing the batch branch further",
            failure_category="conflict",
            retry_command=retry_command,
        )
        self.head_sha = head_sha
        self.conflict_result = conflict_result


@dataclass(frozen=True)
class BatchSource:
    """一个已合格、可进入聚合批次的来源任务视图。"""

    issue_number: int
    issue_url: str
    pr_number: int
    pr_url: str
    branch: str
    head_sha: str
    base_sha: str
    checks_state: str
    prd_path: str | None = None
    dependency_issue_numbers: tuple[int, ...] = ()
    issue: IssueSummary | None = None
    pr_context: PullRequestContext | None = None
    source_pr_was_closed: bool = False


@dataclass(frozen=True)
class AggregateResult:
    """一次聚合交付的最终结果。"""

    total_pr_url: str
    batch_branch: str
    base_sha: str
    head_sha: str
    tree_sha: str
    source_pr_numbers_closed: tuple[int, ...]
    source_pr_numbers_pending: tuple[int, ...]
    prd_paths: tuple[str, ...]
    verification_passed: bool = True


@dataclass(frozen=True)
class BatchSourceResolutionRequest:
    """从 Issue / PR 服务解析一个显式批次的来源上下文。"""

    repo_path: Path
    github_client: IGitHubClient
    config: AppConfig
    repo_id: str
    issue_numbers: tuple[int, ...]
    repo_identifier: str


@dataclass(frozen=True)
class BatchAggregateRequest:
    """运行批次聚合生命周期所需的依赖与已解析来源。"""

    repo_path: Path
    repo_id: str
    config: AppConfig
    github_client: IGitHubClient
    process_runner: IProcessRunner
    sources: tuple[BatchSource, ...]


@dataclass(frozen=True)
class AggregatePRBodyContext:
    """总 PR 正文生成所需的来源、验证和 tree provenance。"""

    sources: tuple[BatchSource, ...]
    prd_paths: tuple[str, ...]
    verification_results: tuple[CommandResult, ...]
    verification_passed: bool
    base_sha: str
    head_sha: str
    tree_sha: str
    verifier_summaries: tuple[str, ...] = ()
