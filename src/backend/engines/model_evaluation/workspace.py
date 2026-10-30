"""评测运行工作区：git 基线探针、隔离快照仓库与暂存目录。

所有 git 调用只走固定 argv（无 shell），写入范围被限制在运行专用根目录
``<state home>/evaluation/`` 下：

- ``runs/<run_id>/`` —— judge payload 暂存（仅 awaiting-judge-consent /
  judging 期间存在，完成、拒绝、取消或删除时清掉）。
- ``worktrees/<run_id>/<case>-<preset>/`` —— 候选执行的单提交快照仓库，
  每个矩阵单元独立，产出 diff 后立即移除；它与源仓库不共享 Git 对象库。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 120


class EvaluationWorkspaceError(RuntimeError):
    """git 基线或 worktree 操作失败；message 不含仓库内容，只含命令语义。"""


def evaluation_root(console_db_path: str | Path) -> Path:
    """评测产物根目录：与 Console 账本同状态目录，随 ``~`` 展开。"""
    return Path(console_db_path).expanduser().parent / "evaluation"


def run_workspace_dir(root: Path, run_id: str) -> Path:
    """返回该 run 的 worktree 根目录（不创建）。"""
    return root / "worktrees" / run_id


def staging_dir(root: Path, run_id: str) -> Path:
    """返回该 run 的 payload 暂存目录（不创建）。"""
    return root / "runs" / run_id


def probe_base(repo_path: str, base_ref: str) -> tuple[str, str]:
    """解析 ``(repo_path, base_ref)`` 为 ``(commit, tree_hash)``。

    Raises:
        ValueError: 路径不是 git 仓库、ref 不可达或命令失败。
    """
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise ValueError(f"仓库路径不存在: {repo}")
    try:
        commit = _git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        tree = _git(repo, ["rev-parse", "--verify", f"{commit}^{{tree}}"])
    except EvaluationWorkspaceError as exc:
        raise ValueError(f"基线不可达: {exc}") from exc
    return commit, tree


def create_snapshot_repo(repo_path: str, commit: str, snapshot_path: Path) -> None:
    """导出基线文件并在新目录初始化只含一个提交的 Git 仓库。

    Raises:
        EvaluationWorkspaceError: Git 命令失败、目录冲突或 tree hash 不一致。
    """
    source_repo_path = Path(repo_path).expanduser()
    if snapshot_path.exists():
        raise EvaluationWorkspaceError(f"快照目录已存在，拒绝复用: {snapshot_path}")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="evaluation-index-") as index_dir:
        environment = _git_environment(Path(index_dir) / "index")
        _git_with_env(source_repo_path, ["read-tree", commit], environment)
        _git_with_env(
            source_repo_path,
            ["checkout-index", "--all", "--force", f"--prefix={snapshot_path}{os.sep}"],
            environment,
        )
    _git(snapshot_path, ["init", "--quiet"])
    _git(snapshot_path, ["config", "user.name", "KedaCode Evaluation"])
    _git(snapshot_path, ["config", "user.email", "evaluation@localhost"])
    _git(snapshot_path, ["add", "-f", "-A"])
    _git(snapshot_path, ["commit", "--quiet", "--allow-empty", "-m", "Frozen evaluation baseline"])
    if _git(snapshot_path, ["rev-parse", "HEAD^{tree}"]) != _git(
        source_repo_path, ["rev-parse", "--verify", f"{commit}^{{tree}}"]
    ):
        raise EvaluationWorkspaceError("快照仓库 tree hash 与冻结基线不一致")


def worktree_tree_hash(worktree_path: Path) -> str:
    """返回 worktree HEAD 的 tree hash，用于与 run 快照做同基线断言。"""
    return _git(worktree_path, ["rev-parse", "HEAD^{tree}"])


def collect_diff(worktree_path: Path, base_commit: str) -> str:
    """收集候选相对基线的完整差异文本（含新增文件）。

    先把工作区变更 ``git add -A`` 落入 index（**不 commit**），再 diff，
    使 untracked 新文件也进入评审可见范围。主工作区与远端仓库不受影响。
    """
    try:
        _git(worktree_path, ["add", "-A"])
    except EvaluationWorkspaceError:
        # add 失败（如权限问题）不阻断：退化为仅跟踪文件的 diff。
        _logger.warning("evaluation diff: git add failed in %s", worktree_path)
    return _git(
        worktree_path,
        ["-c", "core.quotepath=false", "diff", "--no-color", base_commit],
        max_output=8_000_000,
    )


def restore_dependency_manifests(repo_path: str, commit: str, snapshot_path: Path) -> None:
    """把依赖清单恢复到冻结基线版本，并删除候选新增的清单。"""
    source_repo_path = Path(repo_path).expanduser()
    for manifest_name in ("pyproject.toml", "uv.lock"):
        try:
            content = _git_bytes(source_repo_path, ["show", f"{commit}:{manifest_name}"])
        except EvaluationWorkspaceError:
            _remove_snapshot_path(snapshot_path, manifest_name)
            continue
        safe_target = _prepare_snapshot_target(snapshot_path, manifest_name)
        safe_target.write_bytes(content)


def remove_held_out_files(snapshot_path: Path, relative_paths: tuple[str, ...]) -> None:
    """在 Agent 启动前移除基线中同路径的判分文件。"""
    for relative_path in relative_paths:
        _remove_snapshot_path(snapshot_path, relative_path)


def restore_held_out_base_files(
    repo_path: str,
    commit: str,
    snapshot_path: Path,
    relative_paths: tuple[str, ...],
) -> None:
    """丢弃候选对判分路径的改动，还原基线版本供 diff 使用。"""
    source_repo_path = Path(repo_path).expanduser()
    for relative_path in relative_paths:
        try:
            content = _git_bytes(source_repo_path, ["show", f"{commit}:{relative_path}"])
        except EvaluationWorkspaceError:
            _remove_snapshot_path(snapshot_path, relative_path)
            continue
        safe_target = _prepare_snapshot_target(snapshot_path, relative_path)
        safe_target.write_bytes(content)


def overlay_held_out(
    snapshot_path: Path, held_out_dir: Path, relative_paths: tuple[str, ...]
) -> None:
    """在候選结束后整文件覆盖放入受限判分材料。"""
    for relative_path in relative_paths:
        source_path = held_out_dir / relative_path
        if not source_path.is_file():
            raise EvaluationWorkspaceError(f"判分材料缺失: {relative_path}")
        safe_target = _prepare_snapshot_target(snapshot_path, relative_path)
        shutil.copyfile(source_path, safe_target)


def _prepare_snapshot_target(snapshot_path: Path, relative_path: str) -> Path:
    """逐段创建候选内目标父目录，不跟随 Agent 留下的目录符号链接。"""
    path_parts = relative_path.replace("\\", "/").split("/")
    if (
        relative_path.startswith("/")
        or not path_parts
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        raise EvaluationWorkspaceError(f"快照目标路径非法: {relative_path}")
    cursor = snapshot_path
    for part in path_parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            cursor.unlink()
        elif cursor.exists() and not cursor.is_dir():
            cursor.unlink()
        cursor.mkdir(exist_ok=True)
    target_path = cursor / path_parts[-1]
    if target_path.is_symlink():
        target_path.unlink()
    elif target_path.is_dir():
        shutil.rmtree(target_path)
    return target_path


def _remove_snapshot_path(snapshot_path: Path, relative_path: str) -> None:
    """安全删除候选目录内的文件或链接，且不跟随父目录符号链接。"""
    target_path = _prepare_snapshot_target(snapshot_path, relative_path)
    target_path.unlink(missing_ok=True)


def apply_gold_patch(snapshot_path: Path, patch_path: Path) -> None:
    """按冻结基线行号，在判分材料放入前应用零上下文原始修复。"""
    _git(snapshot_path, ["apply", "--binary", "--unidiff-zero", str(patch_path)])


def purge_directory(path: Path) -> None:
    """递归删除目录（存在才删），用于 run 删除时的产物清理。"""
    shutil.rmtree(path, ignore_errors=True)


def remove_directory_if_empty(path: Path) -> None:
    """目录已空时删除之（worktree 逐单元清理后收尾 run 级父目录，不留残壳）。"""
    try:
        path.rmdir()
    except OSError:
        pass


def write_payload(staging: Path, name: str, text: str) -> Path:
    """在受限暂存目录写入 payload 文件并返回路径（目录自动创建）。"""
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / name
    target.write_text(text, encoding="utf-8")
    return target


def read_payload(staging: Path, name: str) -> str | None:
    """读取暂存 payload；文件缺失返回 ``None``（授权重核对会判过期冲突）。"""
    target = staging / name
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8")


def write_json(staging: Path, name: str, payload: dict[str, Any]) -> Path:
    """写入 JSON 文档（manifest 等结构化暂存物）。"""
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / name
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def read_json(staging: Path, name: str) -> dict[str, Any] | None:
    """读取 JSON 文档；缺失或损坏返回 ``None``。"""
    target = staging / name
    if not target.is_file():
        return None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _git(cwd: Path, args: list[str], *, max_output: int = 200_000) -> str:
    """执行固定 argv 的 git 命令并返回 strip 后的 stdout。

    Raises:
        EvaluationWorkspaceError: 非零退出、超时或输出越界。
    """
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = subprocess.run(  # noqa: S603 - argv 固定，无 shell
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 超时") from exc
    except OSError as exc:
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 无法执行: {exc}") from exc
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout or "").strip()[:400]
        raise EvaluationWorkspaceError(
            f"git {' '.join(args)} 失败({completed.returncode}): {reason}"
        )
    output = completed.stdout or ""
    if len(output) > max_output:
        raise EvaluationWorkspaceError("git 输出超过安全上限，拒绝处理")
    return output.strip()


def _git_bytes(cwd: Path, args: list[str]) -> bytes:
    """运行固定 argv 的 git 命令并返回原始字节。"""
    try:
        completed = subprocess.run(  # noqa: S603 - argv 固定，无 shell
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 执行失败") from exc
    if completed.returncode != 0:
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 不可用")
    return completed.stdout


def _git_environment(index_path: Path) -> dict[str, str]:
    """为临时 index 子进程构造最小环境。"""
    environment = {
        key: value for key in ("PATH", "HOME", "LANG", "LC_ALL") if (value := os.environ.get(key))
    }
    environment["GIT_INDEX_FILE"] = str(index_path)
    return environment


def _git_with_env(cwd: Path, args: list[str], environment: dict[str, str]) -> str:
    """使用隔离 index 环境运行固定 Git 命令。"""
    try:
        completed = subprocess.run(  # noqa: S603 - argv 固定，无 shell
            ["git", *args],
            cwd=str(cwd),
            env=environment,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 执行失败") from exc
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout or "").strip()[:400]
        raise EvaluationWorkspaceError(f"git {' '.join(args)} 失败: {reason}")
    return (completed.stdout or "").strip()
