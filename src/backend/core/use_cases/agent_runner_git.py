"""Git utilities and verification for the agent runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, CommandResult

__all__ = [
    "expand_changed_path",
    "get_active_rebase_target",
    "get_current_branch",
    "get_head_sha",
    "has_changes",
    "has_rebase_metadata",
    "is_detached_head",
    "list_changed_paths",
    "list_git_remotes",
    "list_stageable_paths",
    "pop_worktree_stash",
    "run_verification",
    "stash_worktree_changes",
]

# ``git status --porcelain`` 状态码：第 1 位是 index 侧，第 2 位是工作区侧。
# ``D `` = 删除已 staged（工作区无对应文件），``R*`` = index 侧记录了重命名。
_INDEX_DELETED_STATUS_CODE = "D "
_INDEX_RENAMED_STATUS = "R"


@dataclass(frozen=True, slots=True)
class _GitStatusEntry:
    """``git status --porcelain -z`` 的单条记录。"""

    status_code: str
    """两字符状态码；第 1 位是 index 侧状态，第 2 位是工作区侧状态。"""

    path: str
    """变更路径；重命名/复制时是目标路径。"""

    rename_source_path: str | None
    """重命名/复制的源路径；其他状态为 ``None``。"""


def get_head_sha(worktree_path: Path, process_runner: IProcessRunner) -> str:
    """Return the full SHA of the current HEAD commit."""
    result = process_runner.run(["git", "rev-parse", "HEAD"], cwd=worktree_path)
    return result.stdout.strip()


def get_current_branch(worktree_path: Path, process_runner: IProcessRunner) -> str:
    """Return the current branch name for a worktree."""
    result = process_runner.run(["git", "branch", "--show-current"], cwd=worktree_path)
    return result.stdout.strip()


def is_detached_head(worktree_path: Path, process_runner: IProcessRunner) -> bool:
    """Return whether the worktree is in detached HEAD state."""
    result = process_runner.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    return result.stdout.strip() == "HEAD"


def get_active_rebase_target(worktree_path: Path, process_runner: IProcessRunner) -> str | None:
    """Return the target branch of an active rebase, or None if not rebasing.

    Reads Git's rebase metadata directories. An active rebase leaves the
    worktree in detached HEAD, so callers must pair this with
    :func:`is_detached_head` to distinguish a rebase from a plain checkout.
    """
    for rebase_dir in ("rebase-merge", "rebase-apply"):
        result = process_runner.run(
            ["git", "rev-parse", "--git-path", f"{rebase_dir}/head-name"],
            cwd=worktree_path,
            check=False,
        )
        if result.return_code != 0 or not result.stdout.strip():
            continue
        head_name_path = Path(result.stdout.strip())
        if not head_name_path.is_absolute():
            head_name_path = worktree_path / head_name_path
        if not head_name_path.is_file():
            continue
        try:
            raw_name = head_name_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw_name.startswith("refs/heads/"):
            return raw_name[len("refs/heads/") :]
        return raw_name
    return None


def has_rebase_metadata(worktree_path: Path, process_runner: IProcessRunner) -> bool:
    """Return whether Git reports active rebase metadata for the worktree."""
    for rebase_dir in ("rebase-merge", "rebase-apply"):
        result = process_runner.run(
            ["git", "rev-parse", "--git-path", rebase_dir],
            cwd=worktree_path,
            check=False,
        )
        if result.return_code != 0 or not result.stdout.strip():
            continue
        rebase_path = Path(result.stdout.strip())
        if not rebase_path.is_absolute():
            rebase_path = worktree_path / rebase_path
        if rebase_path.exists():
            return True
    return False


def run_verification(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> list[CommandResult]:
    """Run configured verification commands.

    验证命令按配置顺序串行执行，第一个失败即短路停止，
    避免在已知失败的情况下继续执行后续耗时命令。

    每条命令以 ``bash -lc <command>`` 形式执行，与
    :func:`backend.core.use_cases.agent_runner_validation.ensure_validation_commands_pass`
    的 RV 复跑行为保持一致，从而支持命令展开（``$(...)`` / 通配符 /
    管道 / 变量插值）。``-l`` 加载登录 shell 配置以复用用户环境；
    命令仍以单字符串形式传入，未改变 ``verification_commands`` 的语义。
    """
    verification_results: list[CommandResult] = []
    for command in config.runner.verification_commands:
        result = process_runner.run(
            ["bash", "-lc", command],
            cwd=worktree_path,
            check=False,
        )
        verification_results.append(result)
        # 短路：第一个验证失败就停止，节省后续验证时间
        if result.return_code != 0:
            break
    return verification_results


def has_changes(worktree_path: Path, process_runner: IProcessRunner) -> bool:
    """Return whether the worktree has uncommitted changes."""
    result = process_runner.run(["git", "status", "--porcelain"], cwd=worktree_path)
    return bool(result.stdout.strip())


def stash_worktree_changes(
    worktree_path: Path,
    process_runner: IProcessRunner,
    cycle: int,
) -> bool:
    """Auto-stash uncommitted changes before a read-only supervisor cycle.

    Stashes both tracked modifications and untracked files so the supervisor
    can review the current PR branch without losing work left by earlier
    stages. Returns True only when the stash command succeeded and the
    worktree is clean afterwards.

    Args:
        worktree_path: worktree 目录
        process_runner: 进程运行器
        cycle: supervisor cycle 编号

    Returns:
        True if changes were stashed and the worktree is now clean.
    """
    stash_message = f"iar: auto-stash before supervisor cycle {cycle}"
    stash_result = process_runner.run(
        ["git", "stash", "push", "-u", "-m", stash_message],
        cwd=worktree_path,
        check=False,
    )
    if stash_result.return_code != 0:
        return False
    return not has_changes(worktree_path, process_runner)


def pop_worktree_stash(
    worktree_path: Path,
    process_runner: IProcessRunner,
) -> None:
    """Restore auto-stashed changes after a supervisor cycle.

    Args:
        worktree_path: worktree 目录
        process_runner: 进程运行器

    Raises:
        RuntimeError: stash pop failed.
    """
    pop_result = process_runner.run(
        ["git", "stash", "pop"],
        cwd=worktree_path,
        check=False,
    )
    if pop_result.return_code != 0:
        raise RuntimeError(f"Failed to restore auto-stashed changes: {pop_result.stderr.strip()}")


def _parse_status_entries(
    worktree_path: Path, process_runner: IProcessRunner
) -> list[_GitStatusEntry]:
    """Parse ``git status --porcelain -z`` into one entry per changed path.

    Uses NUL-separated ``--porcelain -z`` output so paths containing
    non-ASCII or special characters arrive verbatim. Plain ``--porcelain``
    C-quotes such paths (``"secrets/\\345\\257\\206..."``), and the quoted
    text would slip past the fnmatch-based forbidden-path safety checks.
    """
    status_result = process_runner.run(["git", "status", "--porcelain", "-z"], cwd=worktree_path)
    status_tokens = status_result.stdout.split("\0")
    status_entries: list[_GitStatusEntry] = []
    token_index = 0
    while token_index < len(status_tokens):
        status_entry_text = status_tokens[token_index]
        token_index += 1
        # Minimum entry is "XY p": two status chars, a space, one path char.
        if len(status_entry_text) < 4:
            continue
        status_code = status_entry_text[:2]
        rename_source_path: str | None = None
        # Renames/copies emit the source path as the next NUL token.
        if ("R" in status_code or "C" in status_code) and token_index < len(status_tokens):
            rename_source_path = status_tokens[token_index] or None
            token_index += 1
        status_entries.append(
            _GitStatusEntry(
                status_code=status_code,
                path=status_entry_text[3:],
                rename_source_path=rename_source_path,
            )
        )
    return status_entries


def list_changed_paths(worktree_path: Path, process_runner: IProcessRunner) -> list[str]:
    """List changed paths in a worktree.

    Reports **both** sides of a rename/copy so the forbidden-path safety gate
    also sees the source path — moving a secret out of ``secrets/`` must not
    slip through. Callers that turn the result into a ``git add`` pathspec
    must use :func:`list_stageable_paths` instead; see its docstring.
    """
    changed_paths: list[str] = []
    for status_entry in _parse_status_entries(worktree_path, process_runner):
        changed_paths.append(status_entry.path)
        if status_entry.rename_source_path:
            changed_paths.append(status_entry.rename_source_path)
    return changed_paths


def expand_changed_path(worktree_path: Path, changed_path: str) -> list[str]:
    """把一条 :func:`list_changed_paths` 条目展开为具体文件路径。

    ``git status --porcelain`` 对未跟踪目录只输出 ``?? dir/`` 一行而不展开其中
    文件，不展开就只能拿到目录本身，逐文件判定会整体漏掉。

    Args:
        worktree_path: worktree 根目录。
        changed_path: ``git status`` 报出的单条仓库相对路径。

    Returns:
        该条目对应的文件路径列表（POSIX 分隔符，仓库相对）。
    """
    candidate_path = worktree_path / changed_path
    if not changed_path.endswith("/") or not candidate_path.is_dir():
        return [changed_path.strip("/")]
    return [
        file_path.relative_to(worktree_path).as_posix()
        for file_path in candidate_path.rglob("*")
        if file_path.is_file()
    ]


def list_stageable_paths(worktree_path: Path, process_runner: IProcessRunner) -> list[str]:
    """List changed paths that ``git add -- <path>`` can still match.

    ``git add`` resolves each pathspec against the working tree *and* the
    index, and aborts the **whole** command with ``fatal: pathspec ... did
    not match any files`` (exit 128) on the first path found in neither.
    Two states reported by ``git status`` are exactly that:

    - 已 staged 的重命名源路径：PRD 归档门禁执行
      ``git mv tasks/pending/x.md tasks/archive/x.md`` 之后，源路径既不在工作区
      也不在 index 中。
    - 已 staged 的删除（状态码 ``D ``）：``git rm`` 之后同样两侧都不存在。

    这两类路径都已经记录在 index 里，后续 ``git commit`` 会照常带上它们，所以把
    它们从 pathspec 里剔除不会丢内容；反之保留它们会让整批 staging 失败——例如
    checkpoint 会连 agent 真正的在途代码改动一起丢掉。若删除/重命名的源路径随后
    又被重建，``git status`` 会为它单独输出一条 ``??`` 记录，仍然会被保留。
    """
    stageable_paths: list[str] = []
    for status_entry in _parse_status_entries(worktree_path, process_runner):
        if status_entry.status_code != _INDEX_DELETED_STATUS_CODE:
            stageable_paths.append(status_entry.path)
        # 复制不移除源路径（index 里仍然跟踪），只有重命名的源路径不可再 stage。
        if status_entry.rename_source_path and not status_entry.status_code.startswith(
            _INDEX_RENAMED_STATUS
        ):
            stageable_paths.append(status_entry.rename_source_path)
    return stageable_paths


def list_git_remotes(worktree_path: Path, process_runner: IProcessRunner) -> list[str]:
    """Return configured Git remote names for the worktree."""
    remote_result = process_runner.run(["git", "remote"], cwd=worktree_path)
    remote_names = []
    for remote_line in remote_result.stdout.splitlines():
        remote_name = remote_line.strip()
        if remote_name and remote_name not in remote_names:
            remote_names.append(remote_name)
    return remote_names
