"""Worktree 创建与复用：命令模板格式化 + three-command 流水线。

本模块承接原先落在 ``run_agent_once.py`` 的 worktree 供给职责：

1. :func:`format_command` —— 把配置里的命令模板（``create_command`` /
   ``reuse_command`` / ``path_command``）渲染成 argv。
2. :func:`create_or_reuse_worktree` —— 按 create → reuse → path 三段流水线
   取得 worktree 的权威绝对路径，并补齐 gitignore 掉、``git worktree add``
   不会落地的产物（``.env*``、前端 ``node_modules``）。依赖准备是**可恢复**的：
   装不动只把诊断写进 ``.agent-runner/dependency-preparation.json`` 交给 Agent，
   绝不因环境缺口终止 Issue。

``_SHARED_GIT_LOCK`` 用于串行化共享仓库上的 ``git worktree add`` 写入：并行
Issue worker（``kc daemon --concurrency``）会同时写主仓库的
``.git/worktrees`` 与 refs。默认顺序路径下无竞争、无开销。
"""

from __future__ import annotations

import logging
import shlex
import threading
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import (
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
)
from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker
from backend.core.use_cases.agent_runner_validation import (
    ensure_evidence_dir_excluded,
)
from backend.core.use_cases.agent_runner_worktree_branch import (
    _ensure_worktree_branch,
    _reconcile_worktree_with_remote_branch,
)
from backend.core.use_cases.issue_prd_publication import published_issue_prd_branch
from backend.core.use_cases.worktree_database import (
    WorktreeDatabaseProvisionRequest,
    provision_worktree_database,
)
from backend.core.use_cases.worktree_dependency_context import (
    write_dependency_preparation_report,
)
from backend.core.use_cases.worktree_env import copy_missing_env_files
from backend.core.use_cases.worktree_frontend import (
    ensure_frontend_node_modules,
    exclude_frontend_node_modules_from_git,
)
from backend.core.use_cases.worktree_path_output import parse_worktree_path_stdout
from backend.core.use_cases.issue_workspace import (
    bind_issue_workspace,
    resolve_issue_branch,
    related_issue,
    issue_branch_candidates,
    validate_issue_workspace,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "_resolve_repo_id",
    "create_or_reuse_worktree",
    "format_command",
]


def format_command(
    template: str,
    *,
    issue_number: int,
    base_branch: str | None = None,
    issue_branch: str | None = None,
) -> list[str]:
    """渲染任务命令，并兼容旧版 issue-{issue_number} 模板。

    Args:
        template: 可含 issue_number、issue_branch 和 base_branch 占位符的命令。
        issue_number: GitHub Issue 编号。
        base_branch: 创建工作树的起点；模板引用该占位符时必须提供。
        issue_branch: 已解析的完整任务分支；省略时保留离线兼容行为。

    Returns:
        可直接交给进程端口执行的参数列表。

    Raises:
        ValueError: 模板需要基础分支但调用方未提供。
    """
    if "{base_branch}" in template and base_branch is None:
        raise ValueError(
            "Command template references {base_branch} but no base_branch was provided."
        )
    normalized_template = (
        template.replace("issue-{issue_number}", "{issue_branch}") if issue_branch else template
    )
    return shlex.split(
        normalized_template.format(
            issue_number=issue_number,
            base_branch=base_branch,
            issue_branch=issue_branch or f"issue-{issue_number}",
        )
    )


# Serializes the shared-repository git mutation in worktree creation across
# parallel Issue workers (``kc daemon --concurrency``). ``git worktree add``
# writes the main repo's ``.git/worktrees`` and refs, which can race when
# several issues are set up at once. Uncontended (no overhead) in the default
# sequential path. The long agent run happens after this lock is released.
_SHARED_GIT_LOCK = threading.Lock()


def _fetch_issue_ref(
    repo_path: Path, remote: str, branch: str, process_runner: IProcessRunner
) -> str | None:
    """刷新指定远端任务分支，不使用旧的 remote-tracking 缓存代替成功 fetch。"""
    remote_ref = f"{remote}/{branch}"
    fetched = process_runner.run(
        ["git", "fetch", remote, f"+{branch}:refs/remotes/{remote}/{branch}"],
        cwd=repo_path,
        check=False,
    )
    if fetched.return_code != 0:
        return None
    verified = process_runner.run(
        ["git", "rev-parse", "--verify", "--quiet", remote_ref], cwd=repo_path, check=False
    )
    return remote_ref if verified.return_code == 0 else None


def _resolve_fork_base(
    repo_path: Path,
    issue: IssueSummary,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> str:
    """Resolve the ref a new Issue worktree forks from.

    - ``stack`` sequencing: fork from the upstream Issue's branch
      (``issue-<N>``), refreshed from the remote first so the downstream
      worktree contains the upstream tip even when the local branch lags a
      newer remote push. Falls back to the local branch only when the fetch
      fails but the branch exists locally; raises when neither is available.
    - An existing branch for this Issue is the resume point, refreshed before
      applying dependency or base-branch sequencing.
    - ``via-main`` (default): refresh the remote base and fork from the fetched
      remote-tracking ref, so the worktree always contains the latest upstream
      changes. This closes the silent-staleness gap where a downstream run
      forks from a local base that lags the remote.
    """
    declaration = parse_dependency_marker(issue.body)
    remote = config.git.remote
    local_base = config.worktree.base_branch or config.git.base_branch
    issue_branch = resolve_issue_branch(repo_path, issue)
    published_prd_branch = published_issue_prd_branch(
        issue_number=issue.number,
        issue_body=issue.body,
        issue_url=issue.url,
    )
    issue_ref = _fetch_issue_ref(repo_path, remote, issue_branch, process_runner)
    if issue_ref is not None:
        return issue_ref
    if published_prd_branch is not None:
        published_ref = _fetch_issue_ref(repo_path, remote, published_prd_branch, process_runner)
        if published_ref is not None:
            return published_ref
        raise RuntimeError(
            f"Issue #{issue.number} publishes its PRD on '{remote}/{published_prd_branch}', "
            "but the task branch could not be fetched."
        )

    if declaration is not None and declaration.sequence == "stack" and declaration.issue_numbers:
        upstream_issue = related_issue(issue, declaration.issue_numbers[0])
        upstream_branch = resolve_issue_branch(repo_path, upstream_issue)
        for candidate_branch in dict.fromkeys(
            (upstream_branch, *issue_branch_candidates(upstream_issue))
        ):
            remote_ref = _fetch_issue_ref(repo_path, remote, candidate_branch, process_runner)
            if remote_ref is not None:
                return remote_ref
        local_exists = process_runner.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{upstream_branch}"],
            cwd=repo_path,
            check=False,
        )
        if local_exists.return_code == 0:
            _logger.warning(
                "Could not refresh stack upstream '%s' from '%s'; forking from local branch.",
                upstream_branch,
                remote,
            )
            return upstream_branch
        raise RuntimeError(
            f"Stack sequencing requires upstream branch '{upstream_branch}' to exist "
            f"on '{remote}' (or in a confirmed local workspace); fetch failed."
        )

    fetch_base = process_runner.run(
        ["git", "fetch", remote, local_base],
        cwd=repo_path,
        check=False,
    )
    if fetch_base.return_code == 0:
        remote_ref = f"{remote}/{local_base}"
        verify = process_runner.run(
            ["git", "rev-parse", "--verify", "--quiet", remote_ref],
            cwd=repo_path,
            check=False,
        )
        if verify.return_code == 0:
            return remote_ref
    _logger.warning(
        "Could not refresh base '%s' from '%s'; forking from local base.",
        local_base,
        remote,
    )
    return local_base


def create_or_reuse_worktree(
    repo_path: Path,
    issue: IssueSummary,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> Path:
    """创建或续用完整远端身份对应的工作树。

    先解析稳定任务分支，再依次执行 create、失败时的 reuse 和 path 命令。
    在分支自愈、复制环境与准备依赖前核对归属；新建成功后保存完整 Issue URL。
    归属不明的旧任务保留原目录，新任务使用独立命名空间。

    Args:
        repo_path: 主仓库路径。
        issue: 当前任务及其完整 URL。
        config: 工作树、Git 与依赖准备配置。
        process_runner: 命令执行端口。

    Returns:
        已确认归属、准备好运行环境的工作树绝对路径。

    Raises:
        FileNotFoundError: 创建、复用与定位后目录仍不存在。
        ValueError: 归属记录无效或属于其他任务。
        RuntimeError: 分支不匹配或存在不能自动恢复的分叉。
    """
    fork_base = _resolve_fork_base(repo_path, issue, config, process_runner)
    expected_branch = resolve_issue_branch(repo_path, issue)
    # Hold the shared-git lock only for the worktree-add writes; the agent run
    # (the long pole) happens later in the caller, outside this lock.
    with _SHARED_GIT_LOCK:
        create_result = process_runner.run(
            format_command(
                config.worktree.create_command,
                issue_number=issue.number,
                base_branch=fork_base,
                issue_branch=expected_branch,
            ),
            cwd=repo_path,
            check=False,
        )
        if create_result.return_code != 0:
            reuse_result = process_runner.run(
                format_command(
                    config.worktree.reuse_command,
                    issue_number=issue.number,
                    issue_branch=expected_branch,
                ),
                cwd=repo_path,
                check=False,
            )
        else:
            reuse_result = None
        path_result = process_runner.run(
            format_command(
                config.worktree.path_command,
                issue_number=issue.number,
                issue_branch=expected_branch,
            ),
            cwd=repo_path,
        )
    # path_command runs with cwd=repo_path, so a relative output must be
    # anchored there too — bare resolve() would anchor it to the daemon
    # process cwd instead.
    worktree_path_output = parse_worktree_path_stdout(path_result.stdout)
    if not worktree_path_output.is_absolute():
        worktree_path_output = repo_path / worktree_path_output
    worktree_path = worktree_path_output.resolve()
    if not worktree_path.exists():
        raise FileNotFoundError(
            "worktree path does not exist after create/reuse/path pipeline: "
            f"{worktree_path}. "
            f"create_command return_code={create_result.return_code}, "
            f"reuse_command return_code="
            f"{reuse_result.return_code if reuse_result is not None else 'skipped'}, "
            f"path_command return_code={path_result.return_code}, "
            f"path_command stdout={path_result.stdout!r}."
        )
    validate_issue_workspace(worktree_path, issue, allow_unbound=create_result.return_code == 0)
    expected_branch = resolve_issue_branch(repo_path, issue, worktree_path=worktree_path)
    # 分支恢复可能提交修复，先排除已经核对归属的本地状态，避免其混入提交。
    ensure_evidence_dir_excluded(worktree_path, config, process_runner)
    _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)
    bind_issue_workspace(repo_path, issue, worktree_path, branch=expected_branch)
    copied_env_paths = copy_missing_env_files(repo_path, worktree_path)
    if copied_env_paths:
        _logger.info(
            "Copied %d missing env file(s) into worktree %s: %s",
            len(copied_env_paths),
            worktree_path,
            ", ".join(str(env_path) for env_path in copied_env_paths),
        )
    _reconcile_worktree_with_remote_branch(worktree_path, config, process_runner)
    if config.worktree.provision_database:
        provision_worktree_database(
            WorktreeDatabaseProvisionRequest(
                repository_path=repo_path,
                worktree_path=worktree_path,
                issue_number=issue.number,
                migration_command=config.worktree.migration_command,
                workspace_key=expected_branch if expected_branch.startswith("kc/") else None,
            ),
            process_runner,
        )
    # node_modules is gitignored, so `git worktree add` never materializes it.
    # 准备只做「安全可行」的动作（复用已有依赖、按锁文件装），装不动只写诊断：
    # 环境缺口绝不终止 Issue，Agent 仍会启动并拿到结构化上下文自行决定下一步。
    # 已有 worktree 重跑时同样只补缺空，永不覆盖已存在的 node_modules。
    dependency_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
    dependency_report = exclude_frontend_node_modules_from_git(
        worktree_path,
        dependency_report,
        process_runner,
    )
    # 只有真的有前端项目时才落盘：无前端仓库不需要交给 Agent 的上下文，也不该在
    # worktree 里凭空造出 .agent-runner/ 目录。
    if dependency_report.records:
        write_dependency_preparation_report(worktree_path, dependency_report)
    unprepared_records = [
        project_record
        for project_record in dependency_report.records
        if not project_record.has_usable_dependencies
    ]
    if unprepared_records:
        _logger.warning(
            "Frontend dependencies are not ready for %d project(s) in worktree %s; "
            "diagnostics go to the agent instead of failing the Issue: %s",
            len(unprepared_records),
            worktree_path,
            ", ".join(f"{record.project_path} ({record.reason})" for record in unprepared_records),
        )
    return worktree_path


def _resolve_repo_id(issue: IssueSummary, worktree_path: Path) -> str:
    """为未注入仓库身份的兼容调用推导短期记忆分区键。

    正常 runner 应传入 registry 提供的稳定仓库 ID；只有旧调用面没有该值时，
    才用 worktree 目录名作为回退。核心层不读取 engines 或 infrastructure 中的 registry。
    """
    try:
        return worktree_path.resolve().name or "default"
    except OSError:
        return "default"
