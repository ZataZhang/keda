"""Worktree 创建与复用：命令模板格式化 + three-command 流水线。

本模块承接原先落在 ``run_agent_once.py`` 的 worktree 供给职责：

1. :func:`format_command` —— 把配置里的命令模板（``create_command`` /
   ``reuse_command`` / ``path_command``）渲染成 argv。
2. :func:`create_or_reuse_worktree` —— 按 create → reuse → path 三段流水线
   取得 worktree 的权威绝对路径，并补齐 gitignore 掉、``git worktree add``
   不会落地的产物（``.env*``、前端 ``node_modules``）。

``_SHARED_GIT_LOCK`` 用于串行化共享仓库上的 ``git worktree add`` 写入：并行
Issue worker（``iar daemon --concurrency``）会同时写主仓库的
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
from backend.core.use_cases.agent_runner_validation import (
    ensure_evidence_dir_excluded,
)
from backend.core.use_cases.agent_runner_worktree_branch import (
    _ensure_worktree_branch,
    _reconcile_worktree_with_remote_branch,
)
from backend.core.use_cases.worktree_database import (
    WorktreeDatabaseProvisionRequest,
    provision_worktree_database,
)
from backend.core.use_cases.worktree_env import copy_missing_env_files
from backend.core.use_cases.worktree_frontend import (
    ensure_frontend_node_modules,
    exclude_frontend_node_modules_from_git,
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
) -> list[str]:
    """Format a configured command template for an Issue.

    Args:
        template: Command template string. May reference ``{issue_number}``
            and optionally ``{base_branch}`` placeholders.
        issue_number: GitHub issue number the runner is processing.
        base_branch: Repository base branch. Required when ``template``
            contains the ``{base_branch}`` placeholder; ignored otherwise.

    Returns:
        Tokenized command list ready for subprocess execution.
    """
    if "{base_branch}" in template:
        if base_branch is None:
            raise ValueError(
                "Command template references {base_branch} but no base_branch was provided."
            )
        return shlex.split(template.format(issue_number=issue_number, base_branch=base_branch))
    return shlex.split(template.format(issue_number=issue_number))


# Serializes the shared-repository git mutation in worktree creation across
# parallel Issue workers (``iar daemon --concurrency``). ``git worktree add``
# writes the main repo's ``.git/worktrees`` and refs, which can race when
# several issues are set up at once. Uncontended (no overhead) in the default
# sequential path. The long agent run happens after this lock is released.
_SHARED_GIT_LOCK = threading.Lock()


def create_or_reuse_worktree(
    repo_path: Path,
    issue: IssueSummary,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> Path:
    """Create or reuse a worktree for the Issue.

    The three configured commands are run in sequence:

    1. ``create_command`` (best effort, ``check=False``) attempts to create
       the worktree. Failures are tolerated here because the next command
       may be able to recover.
    2. ``reuse_command`` runs only when ``create_command`` failed. It
       usually re-resolves the worktree path and is a no-op on disk.
    3. ``path_command`` always runs to obtain the canonical absolute path.

    After the three commands complete, the returned path is verified to
    exist. If it does not, a :class:`FileNotFoundError` is raised that
    carries the three commands' return codes and stdout excerpts so the
    next engineer can see exactly which step went wrong.

    Finally, gitignored artifacts that ``git worktree add`` never
    materializes are restored from the main checkout: missing ``.env*``
    files are copied (so agent commands see the same configuration), and
    each frontend project's ``node_modules`` is symlinked from the main
    checkout (so worktree builds like ``vite`` work without a per-worktree
    install). Reused worktrees are healed the same way; existing files and
    ``node_modules`` are not touched.
    """
    # Hold the shared-git lock only for the worktree-add writes; the agent run
    # (the long pole) happens later in the caller, outside this lock.
    with _SHARED_GIT_LOCK:
        create_result = process_runner.run(
            format_command(
                config.worktree.create_command,
                issue_number=issue.number,
                base_branch=config.worktree.base_branch,
            ),
            cwd=repo_path,
            check=False,
        )
        if create_result.return_code != 0:
            reuse_result = process_runner.run(
                format_command(
                    config.worktree.reuse_command,
                    issue_number=issue.number,
                ),
                cwd=repo_path,
                check=False,
            )
        else:
            reuse_result = None
        path_result = process_runner.run(
            format_command(config.worktree.path_command, issue_number=issue.number),
            cwd=repo_path,
        )
    # path_command runs with cwd=repo_path, so a relative output must be
    # anchored there too — bare resolve() would anchor it to the daemon
    # process cwd instead.
    worktree_path_output = Path(path_result.stdout.strip())
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
    # 证据目录本地排除：截图/输出证据永远不进代码 diff。
    ensure_evidence_dir_excluded(worktree_path, config, process_runner)
    copied_env_paths = copy_missing_env_files(repo_path, worktree_path)
    if copied_env_paths:
        _logger.info(
            "Copied %d missing env file(s) into worktree %s: %s",
            len(copied_env_paths),
            worktree_path,
            ", ".join(str(env_path) for env_path in copied_env_paths),
        )
    if config.worktree.provision_database:
        provision_worktree_database(
            WorktreeDatabaseProvisionRequest(
                repository_path=repo_path,
                worktree_path=worktree_path,
                issue_number=issue.number,
            ),
            process_runner,
        )
    # node_modules is gitignored, so `git worktree add` never materializes it.
    # Install deps directly in the worktree when a lockfile is present; this is
    # the only form guaranteed to work with every frontend toolchain (including
    # Next.js/Turbopack). If no lockfile exists or the install fails, fall back
    # to symlinking from the main checkout. Reused worktrees are healed the same
    # way; existing node_modules are left untouched.
    installed_frontend_paths, linked_frontend_paths = ensure_frontend_node_modules(
        repo_path, worktree_path, process_runner
    )
    if linked_frontend_paths:
        exclude_frontend_node_modules_from_git(worktree_path, linked_frontend_paths, process_runner)
    handled_frontend_paths = installed_frontend_paths + linked_frontend_paths
    if handled_frontend_paths:
        _logger.info(
            "Prepared node_modules for %d frontend project(s) in worktree %s: "
            "installed=%s, linked=%s",
            len(handled_frontend_paths),
            worktree_path,
            ", ".join(str(frontend_path) for frontend_path in installed_frontend_paths),
            ", ".join(str(frontend_path) for frontend_path in linked_frontend_paths),
        )
    expected_branch = f"issue-{issue.number}"
    _ensure_worktree_branch(worktree_path, expected_branch, issue, config, process_runner)
    _reconcile_worktree_with_remote_branch(worktree_path, config, process_runner)
    return worktree_path


def _resolve_repo_id(issue: IssueSummary, worktree_path: Path) -> str:
    """Derive a stable per-repository identifier for short-term memory paths.

    Uses the worktree directory name as a stable stand-in when no registry
    lookup is available. Kept dependency-light on purpose: this function
    lives in the ``core/`` layer and must not reach into ``engines/`` or
    ``infrastructure/`` to read the registry.
    """
    try:
        return worktree_path.resolve().name or "default"
    except OSError:
        return "default"
