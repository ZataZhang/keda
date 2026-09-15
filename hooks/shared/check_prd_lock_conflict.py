#!/usr/bin/env python3
"""warn-only 提交钩子：staged 变更触及他人新鲜锁持有的 PRD 时输出警告。

通过 pre-commit 调用（``files`` 过滤为 ``tasks/pending`` / ``tasks/evidence`` 下的
路径）。该钩子是宽松兜底：只警告、不阻断，任何情况下退出码恒为 0。
锁的判定逻辑复用 ``scripts/shared/just/prd_lock.py``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# prd_lock.py 不是包的一部分，import 前需把它所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_lock  # noqa: E402

_PENDING_PREFIX = "tasks/pending/"
_EVIDENCE_PREFIX = "tasks/evidence/"


def extract_prd_stem(staged_file_name: str) -> str | None:
    """从 staged 文件路径解析对应的 PRD stem。

    Args:
        staged_file_name (str): pre-commit 传入的仓库相对路径。

    Returns:
        str | None: ``tasks/pending/<stem>.md`` 或 ``tasks/evidence/<stem>/...``
        命中的 stem；不相关路径返回 ``None``。
    """
    normalized_name = staged_file_name.replace("\\", "/")
    if normalized_name.startswith(_PENDING_PREFIX) and normalized_name.endswith(".md"):
        pending_basename = normalized_name[len(_PENDING_PREFIX) :]
        if "/" not in pending_basename:
            return pending_basename[: -len(".md")]
        return None
    if normalized_name.startswith(_EVIDENCE_PREFIX):
        evidence_relative = normalized_name[len(_EVIDENCE_PREFIX) :]
        evidence_first_segment = evidence_relative.split("/", 1)[0]
        return evidence_first_segment or None
    return None


def main(argv: list[str] | None = None) -> int:
    """入口函数；宽松兜底语义下退出码恒为 0。

    Returns:
        int: 进程退出码（恒为 0）。
    """
    raw_argument_parser = argparse.ArgumentParser(
        description="staged 变更触及他人新鲜锁持有的 PRD 时输出警告（永不阻断）。",
    )
    raw_argument_parser.add_argument(
        "--warn-only",
        action="store_true",
        help="兼容 warn-only 钩子惯例的形式参数；本钩子无论是否传入都只警告。",
    )
    raw_argument_parser.add_argument("files", nargs="*", help="staged 文件路径列表。")
    raw_parsed_arguments = raw_argument_parser.parse_args(argv)

    touched_stem_set: set[str] = set()
    for staged_file_name in raw_parsed_arguments.files:
        parsed_stem = extract_prd_stem(staged_file_name)
        if parsed_stem:
            touched_stem_set.add(parsed_stem)
    if not touched_stem_set:
        return 0

    main_repo_root = prd_lock.resolve_main_repo_root()
    current_worktree_label = prd_lock.resolve_current_worktree_label(main_repo_root)

    for touched_stem in sorted(touched_stem_set):
        lock_snapshot = prd_lock.inspect_prd_lock(main_repo_root, touched_stem)
        if lock_snapshot.state != "fresh":
            continue
        lock_metadata = lock_snapshot.metadata
        holder_worktree_label = str(lock_metadata.get("worktree") or "")
        if holder_worktree_label == current_worktree_label:
            continue
        print(
            f"[WARNING] tasks 路径触及 PRD '{touched_stem}'，该 PRD 正被其他会话执行："
            f"工具 {lock_metadata.get('ai_tool') or 'unknown'}、"
            f"分支 {lock_metadata.get('branch') or 'unknown'}、"
            f"worktree {holder_worktree_label or '主仓库'}、"
            f"最后心跳 {lock_metadata.get('heartbeat_at') or 'unknown'}。"
            f"提交不被阻断，但请确认没有与对方会话重复开工。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
