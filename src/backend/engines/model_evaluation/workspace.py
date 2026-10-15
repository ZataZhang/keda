"""评测运行工作区：git 基线探针、隔离 worktree 生命周期与暂存目录。

所有 git 调用只走固定 argv（无 shell），写入范围被限制在运行专用根目录
``<state home>/evaluation/`` 下：

- ``runs/<run_id>/`` —— judge payload 暂存（仅 awaiting-judge-consent /
  judging 期间存在，完成、拒绝、取消或删除时清掉）。
- ``worktrees/<run_id>/<case>-<preset>/`` —— 候选执行的干净 worktree，
  每个矩阵单元独立，产出 diff 后立即移除。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
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


def create_detached_worktree(repo_path: str, commit: str, worktree_path: Path) -> None:
    """在 ``worktree_path`` 从 ``commit`` 创建 detached 干净 worktree。

    Raises:
        EvaluationWorkspaceError: git 命令失败或目录冲突。
    """
    repo = Path(repo_path).expanduser()
    if worktree_path.exists():
        raise EvaluationWorkspaceError(f"worktree 目录已存在，拒绝复用: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, ["worktree", "add", "--detach", str(worktree_path), commit])


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


def remove_worktree(repo_path: str, worktree_path: Path) -> None:
    """尽力移除 worktree；失败只告警（删除 run 时还有目录级兜底清理）。"""
    repo = Path(repo_path).expanduser()
    try:
        if worktree_path.exists():
            _git(repo, ["worktree", "remove", "--force", str(worktree_path)])
    except EvaluationWorkspaceError as exc:
        _logger.warning("evaluation worktree 移除失败 (%s): %s", worktree_path, exc)
        shutil.rmtree(worktree_path, ignore_errors=True)


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
