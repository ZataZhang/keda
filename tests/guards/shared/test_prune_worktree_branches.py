"""守护 ``just worktree --prune`` 批量清理语义的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定本身需要变更
时才改本文件，并同步更新相关约定文档。详见 ``docs/ai-standards/testing.md`` 的
Guard Tests 小节。

被测对象：``scripts/shared/worktree/merge.sh`` 的 ``--prune`` 模式。核心不变量：

1. **默认只删「已经并进 base」的分支。** squash merge 之后远端分支被删、本地提交又不在
   base 的祖先链上，git 无法区分「合过了」和「PR 关掉没合」。这类分支（upstream 为
   ``[gone]`` 且仍有独有提交）默认只能列出来，必须显式 ``--force`` 才允许删除——
   判据一旦放宽成「远端没了就删」，一次误判就丢掉未合并的工作。
2. **受保护的分支永不触碰。** base 分支与当前检出分支必须在任何 flag 组合下存活；
   删掉当前检出分支会让所在 worktree 直接失去 HEAD。
3. **``--dry-run`` 零副作用。** 预览路径必须与执行路径彻底分离，否则「先看一眼」本身
   就成了破坏性操作。
4. **``--yes`` 不依赖 stdin。** 无人值守场景没有 TTY，若仍去读 stdin 会挂起或把 EOF
   误判成用户取消。
5. **交互拒绝等于什么都不做。** 回答非 y 时不得留下半清理状态。
6. **已合并分支的 worktree 必须一起消失。** 只删分支会留下一个 ``checked out at``
   的孤儿 worktree，下次同名分支就再也建不出来。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_MERGE_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "shared" / "worktree" / "merge.sh"
)

_MERGED_BRANCH = "feat/merged"
_GONE_BRANCH = "feat/gone"
_ACTIVE_BRANCH = "feat/active"
_IN_USE_BRANCH = "feat/in-use"


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _run_prune(
    repo_path: Path,
    *cli_args: str,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """运行 ``merge.sh --prune``；未提供 stdin 文本时显式关闭 stdin。

    关闭 stdin 是为了复现无人值守场景：若实现仍在读 stdin，会拿到 EOF，
    从而暴露「--yes 也依赖 TTY」的回归，而不是在 pytest 里静静挂起。
    """
    if stdin_text is None:
        return subprocess.run(
            [str(_MERGE_SCRIPT_PATH), "--prune", *cli_args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    return subprocess.run(
        [str(_MERGE_SCRIPT_PATH), "--prune", *cli_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        input=stdin_text,
    )


def _worktree_path_for(repo_path: Path, branch_name: str) -> Path:
    """按 ``create.sh`` 的真实落盘约定 ``<repo_parent>/<repo>-worktrees/<branch>`` 计算路径。"""
    return repo_path.parent / f"{repo_path.name}-worktrees" / branch_name


def _add_worktree(repo_path: Path, branch_name: str) -> Path:
    """为已有分支建立 worktree，返回其磁盘路径。"""
    worktree_path = _worktree_path_for(repo_path, branch_name)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "worktree", "add", str(worktree_path), branch_name)
    return worktree_path


def _commit_on_new_branch(repo_path: Path, branch_name: str, file_name: str) -> None:
    """从当前 HEAD 拉出新分支并提交一个文件。"""
    _run_git(repo_path, "checkout", "-b", branch_name)
    (repo_path / file_name).write_text(f"{branch_name}\n", encoding="utf-8")
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", f"work on {branch_name}")


def _local_branch_names(repo_path: Path) -> set[str]:
    """列出本地分支名。"""
    git_result = _run_git(repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return {line for line in git_result.stdout.splitlines() if line}


def _registered_worktree_paths(repo_path: Path) -> set[str]:
    """列出 ``git worktree list`` 注册的路径。"""
    git_result = _run_git(repo_path, "worktree", "list", "--porcelain")
    prefix = "worktree "
    return {
        line[len(prefix) :] for line in git_result.stdout.splitlines() if line.startswith(prefix)
    }


def _upstream_track_of(repo_path: Path, branch_name: str) -> str:
    """读取分支的 upstream 追踪状态，例如 ``[gone]``。"""
    git_result = _run_git(
        repo_path,
        "for-each-ref",
        "--format=%(upstream:track)",
        f"refs/heads/{branch_name}",
    )
    return git_result.stdout.strip()


def _init_fixture_repo(tmp_path: Path) -> Path:
    """建一个带本地 bare remote 的仓库，覆盖三类分支。

    - ``feat/merged``：已合并进 main，且保留一个 worktree。
    - ``feat/gone``：远端分支已删除，本地仍有独有提交（squash merge 的典型残留）。
    - ``feat/active``：远端分支仍在且有独有提交，不该进入任何候选组。
    """
    remote_path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", "init")
    _run_git(repo_path, "remote", "add", "origin", str(remote_path))
    _run_git(repo_path, "push", "-u", "origin", "main")

    _commit_on_new_branch(repo_path, _MERGED_BRANCH, "merged.txt")
    _run_git(repo_path, "checkout", "main")
    _run_git(repo_path, "merge", "--no-ff", "-m", f"merge {_MERGED_BRANCH}", _MERGED_BRANCH)

    _commit_on_new_branch(repo_path, _GONE_BRANCH, "gone.txt")
    _run_git(repo_path, "push", "-u", "origin", _GONE_BRANCH)
    _run_git(repo_path, "checkout", "main")
    _run_git(repo_path, "push", "origin", "--delete", _GONE_BRANCH)
    _run_git(repo_path, "fetch", "--prune", "origin")

    _commit_on_new_branch(repo_path, _ACTIVE_BRANCH, "active.txt")
    _run_git(repo_path, "push", "-u", "origin", _ACTIVE_BRANCH)
    _run_git(repo_path, "checkout", "main")

    return repo_path


def test_fixture_records_gone_upstream(tmp_path: Path) -> None:
    """fixture 必须真的造出 ``[gone]`` 与「已合并」两种状态，否则后续断言会空转。"""
    repo_path = _init_fixture_repo(tmp_path)

    assert _upstream_track_of(repo_path, _GONE_BRANCH) == "[gone]"
    assert _upstream_track_of(repo_path, _ACTIVE_BRANCH) != "[gone]"
    merged_unique_commit_count = _run_git(
        repo_path, "rev-list", "--count", f"main..{_MERGED_BRANCH}"
    ).stdout.strip()
    gone_unique_commit_count = _run_git(
        repo_path, "rev-list", "--count", f"main..{_GONE_BRANCH}"
    ).stdout.strip()
    assert merged_unique_commit_count == "0"
    assert gone_unique_commit_count != "0"


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    """``--dry-run`` 只列计划：分支、worktree 注册表都必须原样保留。"""
    repo_path = _init_fixture_repo(tmp_path)
    worktree_path = _add_worktree(repo_path, _MERGED_BRANCH)
    branches_before = _local_branch_names(repo_path)
    worktrees_before = _registered_worktree_paths(repo_path)

    prune_result = _run_prune(repo_path, "--dry-run")

    assert prune_result.returncode == 0, prune_result.stdout + prune_result.stderr
    assert "--dry-run" in prune_result.stdout
    assert _local_branch_names(repo_path) == branches_before
    assert _registered_worktree_paths(repo_path) == worktrees_before
    assert worktree_path.exists()


def test_interactive_decline_keeps_everything(tmp_path: Path) -> None:
    """回答非 y 时必须什么都不做，不得留下半清理状态。"""
    repo_path = _init_fixture_repo(tmp_path)
    worktree_path = _add_worktree(repo_path, _MERGED_BRANCH)
    branches_before = _local_branch_names(repo_path)
    worktrees_before = _registered_worktree_paths(repo_path)

    prune_result = _run_prune(repo_path, stdin_text="n\n")

    assert prune_result.returncode == 0, prune_result.stdout + prune_result.stderr
    assert _local_branch_names(repo_path) == branches_before
    assert _registered_worktree_paths(repo_path) == worktrees_before
    assert worktree_path.exists()


def test_yes_removes_merged_branch_and_its_worktree(tmp_path: Path) -> None:
    """``--yes`` 无人值守清理已合并分支，并连带摘掉它的 worktree。"""
    repo_path = _init_fixture_repo(tmp_path)
    worktree_path = _add_worktree(repo_path, _MERGED_BRANCH)

    prune_result = _run_prune(repo_path, "--yes")

    assert prune_result.returncode == 0, prune_result.stdout + prune_result.stderr
    remaining_branches = _local_branch_names(repo_path)
    assert _MERGED_BRANCH not in remaining_branches
    assert "main" in remaining_branches
    assert not worktree_path.exists()
    assert str(worktree_path) not in _registered_worktree_paths(repo_path)


def test_base_and_current_branch_are_protected(tmp_path: Path) -> None:
    """从 linked worktree 内运行时，当前检出分支与 base 分支都必须存活。"""
    repo_path = _init_fixture_repo(tmp_path)
    _run_git(repo_path, "branch", _IN_USE_BRANCH, "main")
    in_use_worktree_path = _add_worktree(repo_path, _IN_USE_BRANCH)

    prune_result = _run_prune(in_use_worktree_path, "--yes")

    assert prune_result.returncode == 0, prune_result.stdout + prune_result.stderr
    remaining_branches = _local_branch_names(repo_path)
    assert _IN_USE_BRANCH in remaining_branches
    assert "main" in remaining_branches
    assert in_use_worktree_path.exists()


def test_gone_branch_with_unique_commits_needs_force(tmp_path: Path) -> None:
    """``[gone]`` 但仍有独有提交的分支默认只列出，``--force`` 才删除。"""
    repo_path = _init_fixture_repo(tmp_path)

    default_result = _run_prune(repo_path, "--yes")

    assert default_result.returncode == 0, default_result.stdout + default_result.stderr
    assert _GONE_BRANCH in _local_branch_names(repo_path)
    assert _GONE_BRANCH in default_result.stdout
    assert "--force" in default_result.stdout

    forced_result = _run_prune(repo_path, "--yes", "--force")

    assert forced_result.returncode == 0, forced_result.stdout + forced_result.stderr
    assert _GONE_BRANCH not in _local_branch_names(repo_path)


def test_active_branch_is_never_a_candidate(tmp_path: Path) -> None:
    """远端仍在的分支即使未合并也不进候选组，输出里也不该被提及。"""
    repo_path = _init_fixture_repo(tmp_path)

    prune_result = _run_prune(repo_path, "--yes", "--force")

    assert prune_result.returncode == 0, prune_result.stdout + prune_result.stderr
    assert _ACTIVE_BRANCH in _local_branch_names(repo_path)
    assert _ACTIVE_BRANCH not in prune_result.stdout
