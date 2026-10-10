"""夜间批次聚合使用的隔离 Git 操作。"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.agent_runner_git import get_head_sha


class AggregateGitError(RuntimeError):
    """隔离批次 worktree 或本地 Git 操作失败。"""

    def __init__(
        self,
        message: str,
        *,
        failure_category: str,
        head_sha: str | None = None,
        command_result: CommandResult | None = None,
    ) -> None:
        """保存可供生命周期调用方映射的失败阶段。"""
        super().__init__(message)
        self.failure_category = failure_category
        self.head_sha = head_sha
        self.command_result = command_result


@dataclass(frozen=True)
class AggregateGitRequest:
    """创建隔离批次 worktree 所需的 Git 仓库上下文。"""

    repo_path: Path
    remote_name: str
    base_branch: str
    batch_branch: str
    source_branches: tuple[tuple[str, str], ...]
    process_runner: IProcessRunner


@dataclass(frozen=True)
class AggregateWorktree:
    """固定 base 与 source heads 已 fetch 的临时 worktree。"""

    path: Path
    base_sha: str


@dataclass(frozen=True)
class AggregateBranchBuildRequest:
    """本地组合树构建参数。"""

    worktree_path: Path
    batch_branch: str
    base_sha: str
    source_head_shas: tuple[str, ...]
    process_runner: IProcessRunner


@dataclass(frozen=True)
class AggregateBranchResult:
    """隔离集成分支构建结果。"""

    batch_branch: str
    head_sha: str
    tree_sha: str
    merged_head_shas: tuple[str, ...]
    skipped_head_shas: tuple[str, ...]


def _run_git_step(
    repo_path: Path,
    process_runner: IProcessRunner,
    arguments: Sequence[str],
    *,
    failure_category: str,
) -> CommandResult:
    """运行 Git 命令并把非零退出转成带阶段的聚合错误。"""
    result = process_runner.run(["git", *arguments], cwd=repo_path, check=False)
    if result.return_code != 0:
        raise AggregateGitError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.strip() or result.stdout.strip()}",
            failure_category=failure_category,
        )
    return result


def _fetch_remote_commit(
    request: AggregateGitRequest,
    *,
    remote_ref: str,
    failure_category: str,
) -> str:
    """fetch 一个远端 ref 并返回 FETCH_HEAD，不更新用户的 remote-tracking refs。"""
    _run_git_step(
        request.repo_path,
        request.process_runner,
        ["fetch", "--no-tags", request.remote_name, remote_ref],
        failure_category=failure_category,
    )
    fetched_sha = _run_git_step(
        request.repo_path,
        request.process_runner,
        ["rev-parse", "FETCH_HEAD"],
        failure_category=failure_category,
    ).stdout.strip()
    if not fetched_sha:
        raise AggregateGitError(
            f"remote ref {remote_ref} resolved to an empty SHA",
            failure_category=failure_category,
        )
    return fetched_sha


def fetch_latest_base_sha(request: AggregateGitRequest) -> str:
    """读取并 fetch 配置 base 分支当前 SHA。"""
    return _fetch_remote_commit(
        request,
        remote_ref=f"refs/heads/{request.base_branch}",
        failure_category="base-drift",
    )


def verify_source_heads_unchanged(request: AggregateGitRequest) -> None:
    """确认远端来源分支仍指向 Issue PR 上下文声明的 head SHA。"""
    for source_branch, expected_head_sha in request.source_branches:
        fetched_head_sha = _fetch_remote_commit(
            request,
            remote_ref=f"refs/heads/{source_branch}",
            failure_category="eligibility",
        )
        if fetched_head_sha != expected_head_sha:
            raise AggregateGitError(
                f"source branch {source_branch} moved during aggregation "
                f"({expected_head_sha} -> {fetched_head_sha})",
                failure_category="eligibility",
            )


def create_aggregate_worktree(request: AggregateGitRequest) -> AggregateWorktree:
    """从最新远端 base SHA 建立临时 detached worktree 并 fetch 来源 heads。"""
    base_sha = fetch_latest_base_sha(request)
    verify_source_heads_unchanged(request)
    temporary_root = Path(tempfile.mkdtemp(prefix="kc-batch-aggregate-"))
    worktree_path = temporary_root / "worktree"
    add_result = request.process_runner.run(
        ["git", "worktree", "add", "--detach", str(worktree_path), base_sha],
        cwd=request.repo_path,
        check=False,
    )
    if add_result.return_code != 0:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise AggregateGitError(
            f"failed to create isolated aggregate worktree at {worktree_path}: "
            f"{add_result.stderr.strip() or add_result.stdout.strip()}",
            failure_category="base-drift",
        )
    return AggregateWorktree(path=worktree_path, base_sha=base_sha)


def remove_aggregate_worktree(
    *, repo_path: Path, worktree_path: Path, process_runner: IProcessRunner
) -> None:
    """移除临时 worktree 及其系统临时目录，保留本地/远端 batch branch。"""
    temporary_root = worktree_path.parent
    try:
        process_runner.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_path,
            check=False,
        )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def build_aggregate_branch(request: AggregateBranchBuildRequest) -> AggregateBranchResult:
    """从固定 base SHA 按序合入来源提交，冲突时 abort 且不改写来源 refs。"""
    _run_git_step(
        request.worktree_path,
        request.process_runner,
        ["checkout", "-B", request.batch_branch, request.base_sha],
        failure_category="base-drift",
    )
    merged_head_shas: list[str] = []
    skipped_head_shas: list[str] = []
    for source_head_sha in request.source_head_shas:
        ancestor_result = request.process_runner.run(
            ["git", "merge-base", "--is-ancestor", source_head_sha, "HEAD"],
            cwd=request.worktree_path,
            check=False,
        )
        if ancestor_result.return_code == 0:
            skipped_head_shas.append(source_head_sha)
            continue
        merge_result = request.process_runner.run(
            ["git", "merge", "--no-ff", "--no-edit", source_head_sha],
            cwd=request.worktree_path,
            check=False,
        )
        if merge_result.return_code != 0:
            request.process_runner.run(
                ["git", "merge", "--abort"], cwd=request.worktree_path, check=False
            )
            raise AggregateGitError(
                f"aggregate merge conflicted on source head {source_head_sha}: "
                f"{merge_result.stderr.strip() or merge_result.stdout.strip()}",
                failure_category="conflict",
                head_sha=source_head_sha,
                command_result=merge_result,
            )
        merged_head_shas.append(source_head_sha)
    head_sha = get_head_sha(request.worktree_path, request.process_runner)
    tree_sha = _run_git_step(
        request.worktree_path,
        request.process_runner,
        ["rev-parse", "HEAD^{tree}"],
        failure_category="verification",
    ).stdout.strip()
    return AggregateBranchResult(
        batch_branch=request.batch_branch,
        head_sha=head_sha,
        tree_sha=tree_sha,
        merged_head_shas=tuple(merged_head_shas),
        skipped_head_shas=tuple(skipped_head_shas),
    )


def push_aggregate_branch(
    request: AggregateGitRequest,
    *,
    worktree_path: Path,
    allow_update: bool = False,
    expected_remote_sha: str | None = None,
) -> None:
    """推送批次分支；仅按已读总 PR head SHA lease 更新远端 ref。"""
    remote_ref = f"refs/heads/{request.batch_branch}"
    existing_remote_sha = _read_remote_aggregate_sha(
        request,
        remote_ref,
        action="before push",
    )
    push_arguments = ["push"]
    if existing_remote_sha is not None:
        if not allow_update:
            raise AggregateGitError(
                f"remote aggregate branch {request.batch_branch} already exists without a "
                "matching aggregate PR; refusing to overwrite it",
                failure_category="publication",
            )
        if not expected_remote_sha or existing_remote_sha != expected_remote_sha:
            raise AggregateGitError(
                f"remote aggregate branch {request.batch_branch} moved after its PR context "
                "was read; refusing to overwrite an unverified head",
                failure_category="publication",
            )
        push_arguments.append(f"--force-with-lease={remote_ref}:{expected_remote_sha}")
    push_arguments.extend([request.remote_name, f"{request.batch_branch}:{remote_ref}"])
    _run_git_step(
        worktree_path,
        request.process_runner,
        push_arguments,
        failure_category="publication",
    )


def delete_unpublished_aggregate_branch(
    request: AggregateGitRequest,
    *,
    expected_remote_sha: str,
) -> None:
    """删除本次已推送但未能创建总 PR 的 batch ref，且仅当 SHA 未变化时删除。"""
    remote_ref = f"refs/heads/{request.batch_branch}"
    existing_remote_sha = _read_remote_aggregate_sha(
        request,
        remote_ref,
        action="before cleanup",
    )
    if existing_remote_sha is None:
        return
    if existing_remote_sha != expected_remote_sha:
        raise AggregateGitError(
            f"remote aggregate branch {request.batch_branch} moved before cleanup; "
            "refusing to delete an unverified head",
            failure_category="publication",
        )
    _run_git_step(
        request.repo_path,
        request.process_runner,
        [
            "push",
            f"--force-with-lease={remote_ref}:{expected_remote_sha}",
            request.remote_name,
            f":{remote_ref}",
        ],
        failure_category="publication",
    )


def _read_remote_aggregate_sha(
    request: AggregateGitRequest,
    remote_ref: str,
    *,
    action: str,
) -> str | None:
    """读取指定 batch ref 的远端 SHA，区分不存在与查询失败。"""
    listing = request.process_runner.run(
        ["git", "ls-remote", "--heads", request.remote_name, remote_ref],
        cwd=request.repo_path,
        check=False,
    )
    if listing.return_code != 0:
        raise AggregateGitError(
            f"failed to inspect remote aggregate branch {request.batch_branch} {action}: "
            f"{listing.stderr.strip() or listing.stdout.strip()}",
            failure_category="publication",
        )
    reference_line = next(
        (line for line in listing.stdout.splitlines() if line.endswith(f"\t{remote_ref}")),
        None,
    )
    return reference_line.split(maxsplit=1)[0] if reference_line is not None else None


def aggregate_tree_is_clean(*, worktree_path: Path, process_runner: IProcessRunner) -> bool:
    """组合验证后确认 tracked / untracked 工作区没有未提交改动。"""
    status_result = process_runner.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=worktree_path,
        check=False,
    )
    return status_result.return_code == 0 and not status_result.stdout.strip()
