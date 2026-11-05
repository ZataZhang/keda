"""受限 deterministic check recipe 的执行边界。

安全约束（PRD §7.1-3）：命令只允许 core 白名单里的固定 argv + 受限 cwd
（候选快照仓库），禁止 shell 拼接、suite 提供任意解释器脚本或继承完整
用户环境；输出与超时都有硬上限。
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.models.model_evaluation import (
    CHECK_OUTPUT_SUMMARY_LIMIT,
    CHECK_RECIPES,
    EvaluationCheckStatus,
)

_logger = logging.getLogger(__name__)

#: check 子进程允许继承的最小环境变量集：解释器与包管理只需要这几个，
#: 其余（token、云凭据、代理注入等）一律不带进评测检查进程。
_CHECK_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "TMPDIR", "LC_ALL")

#: 输出采集硬上限（字符），超过即截断，防止恶意/失控输出打爆内存。
_CHECK_OUTPUT_CAPTURE_LIMIT = 100_000


@dataclass(frozen=True)
class CheckRunOutcome:
    """一次 check recipe 执行的结构化结果（写库前已收敛长度）。"""

    status: EvaluationCheckStatus
    exit_code: int | None
    output_summary: str | None


def run_check_recipe(
    recipe: str,
    worktree_path: Path,
    timeout_seconds: int,
    check_targets: tuple[str, ...] = (),
) -> CheckRunOutcome:
    """在 ``worktree_path`` 下执行白名单 check recipe。

    Args:
        recipe: suite 声明的 recipe 名；必须属于 ``CHECK_RECIPES``。
        worktree_path: 候选快照仓库（受限 cwd）。
        timeout_seconds: 来自 run 快照（已收敛到硬上限内）。
        check_targets: suite 已校验的 pytest 文件路径或节点 id。

    Returns:
        CheckRunOutcome: passed / failed / timeout；未知 recipe 判 not_run。

    Raises:
        ValueError: recipe 不在白名单（调用方应在 run 创建前就挡住）。
    """
    argv = CHECK_RECIPES.get(recipe)
    if argv is None:
        raise ValueError(f"check recipe '{recipe}' 不在允许清单: {sorted(CHECK_RECIPES)}")
    if any(
        not target or target.startswith("-") or target.startswith("/") or ".." in Path(target).parts
        for target in check_targets
    ):
        raise ValueError("check_targets 含非法路径或参数")
    argv = (*argv, *check_targets)
    env = {key: value for key in _CHECK_ENV_ALLOWLIST if (value := os.environ.get(key))}
    env["PYTHONUNBUFFERED"] = "1"
    try:
        completed = subprocess.run(  # noqa: S603 - argv 来自 core 白名单常量，无 shell
            list(argv),
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckRunOutcome(
            status=EvaluationCheckStatus.timeout,
            exit_code=None,
            output_summary=f"check 超时（>{timeout_seconds}s）: {' '.join(argv)}",
        )
    except OSError as exc:
        return CheckRunOutcome(
            status=EvaluationCheckStatus.failed,
            exit_code=None,
            output_summary=f"check 无法执行: {exc}"[:CHECK_OUTPUT_SUMMARY_LIMIT],
        )
    tail = _tail_summary(completed.stdout, completed.stderr)
    status = (
        EvaluationCheckStatus.passed if completed.returncode == 0 else EvaluationCheckStatus.failed
    )
    return CheckRunOutcome(status=status, exit_code=completed.returncode, output_summary=tail)


def _tail_summary(stdout: str | None, stderr: str | None) -> str:
    """拼接 stdout/stderr 尾部并截断到摘要上限。"""
    combined = ((stdout or "") + "\n" + (stderr or "")).strip()
    if len(combined) > _CHECK_OUTPUT_CAPTURE_LIMIT:
        combined = combined[-_CHECK_OUTPUT_CAPTURE_LIMIT:]
    if len(combined) > CHECK_OUTPUT_SUMMARY_LIMIT:
        combined = "…" + combined[-(CHECK_OUTPUT_SUMMARY_LIMIT - 1) :]
    return combined or None
