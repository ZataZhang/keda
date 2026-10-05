#!/usr/bin/env python3
"""打开 PRD 的人工审查清单。

交付收尾的人工审查入口：定位 PRD 对应的证据目录（分支副本优先，解析规则与
看板 ``prd_status.py`` 同源（共用 ``prd_locator.py``）——执行发生在 worktree 里，证据要等合并回主线才
出现在主仓库），打开 ``human-review-checklist.html`` 交互版（逐步按钮作答、
内嵌截图；不存在时回退打开静态 ``human-review-checklist.md``）——未完成
Human-Confirmed 项、9.1 人读呈递区与人工待决事项的集中审查页。清单尚不存在
时回退打开证据报告（其「人审导航」节是呈递物入口）；两者都没有时列出证据
目录现状并给出下一步提示。

用法::

    python3 scripts/shared/just/prd_review.py <prd-file> [--print]

``--print`` 只打印解析到的路径、不调用系统打开器，供测试与脚本消费。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import prd_locator
import prd_lock

# 人工审查的打开目标槽位，按优先级排列：清单优先，证据报告兜底。清单槽位内
# 交互 HTML（逐步按钮作答、内嵌截图）又优先于静态 Markdown——两者内容一致，
# HTML 是人审会话的首选呈现面，Markdown 是静态底稿。
_REVIEW_TARGET_SLOTS = (
    ("human-review-checklist", (".html", ".md")),
    ("evidence-report", (".md",)),
)


def resolve_prd_path(
    prd_file_argument: str,
    repo_root: Path,
    worktree_branches_list: Sequence[tuple[str, Path]] = (),
) -> Path | None:
    """把命令行参数解析为 PRD 文件路径。

    参数可以是：绝对路径、相对当前目录/仓库根的路径、**PRD 文件名**，或**PRD slug**
    （文件名里日期时间之后的短标识，例如 ``auto-mode-agent-access-routing``）。后两种
    宽形态解决一个真实痛点：PRD 在交付收尾时由 ``tasks/pending`` 归档到
    ``tasks/archive``，而归档只发生在分支上——于是「该传 pending 还是 archive」随
    「在哪棵树、合并没合并」而变（从主仓库跑时 archive 路径根本不存在）。这里把它
    归一化：只取参数的文件名，先在 ``repo_root``、再在各 linked worktree 的
    ``tasks/pending``、``tasks/archive`` 下查找，调用方不必记住当前该传哪个桶。

    Args:
        prd_file_argument (str): 命令行传入的 PRD 路径、文件名或 slug。
        repo_root (Path): 当前仓库根目录。
        worktree_branches_list (Sequence[tuple[str, Path]]): ``(分支名,
            worktree 路径)`` 列表，来自 ``prd_lock.list_linked_worktree_branches``；
            缺省空元组时只在 ``repo_root`` 内查找（保持既有 2 参调用可用）。

    Returns:
        Path | None: 命中的 PRD 文件路径；任何候选都不存在时为 ``None``。
    """
    direct_candidate_path = Path(prd_file_argument)
    if direct_candidate_path.is_file():
        return direct_candidate_path
    rooted_candidate_path = repo_root / prd_file_argument
    if rooted_candidate_path.is_file():
        return rooted_candidate_path

    # 归一成文件名：``tasks/archive/x.md``、``x.md``、``x`` 都归纳到 ``x.md``。
    file_name_text = direct_candidate_path.name
    if not file_name_text.endswith(".md"):
        file_name_text = f"{file_name_text}.md"

    search_root_paths = [repo_root, *(worktree_path for _, worktree_path in worktree_branches_list)]

    # 桶顺序：参数里点名了 pending/archive 就先查该桶；否则 archive 优先——归档是
    # 收尾终态，分支副本优先于主仓库里可能残留的 pending 快照。
    argument_path_parts = direct_candidate_path.parts
    if prd_locator.PENDING_BUCKET_DIR_NAME in argument_path_parts:
        bucket_dir_names = (
            prd_locator.PENDING_BUCKET_DIR_NAME,
            prd_locator.ARCHIVE_BUCKET_DIR_NAME,
        )
    else:
        bucket_dir_names = (
            prd_locator.ARCHIVE_BUCKET_DIR_NAME,
            prd_locator.PENDING_BUCKET_DIR_NAME,
        )

    # 桶在外、root 在内：点名 archive 时先在**所有** root 的 archive 里找，再退 pending，
    # 而不是被主仓库那份 pending 抢先命中。
    for bucket_dir_name in bucket_dir_names:
        for search_root_path in search_root_paths:
            candidate_prd_path = search_root_path / "tasks" / bucket_dir_name / file_name_text
            if candidate_prd_path.is_file():
                return candidate_prd_path

    # 退一步：参数只给了 slug（非完整文件名）时，按 ``*-<slug>.md`` 后缀唯一匹配。
    raw_slug_text = direct_candidate_path.stem
    if raw_slug_text != file_name_text and raw_slug_text:
        for bucket_dir_name in bucket_dir_names:
            for search_root_path in search_root_paths:
                bucket_dir_path = search_root_path / "tasks" / bucket_dir_name
                if not bucket_dir_path.is_dir():
                    continue
                suffix_matched_paths = sorted(bucket_dir_path.glob(f"*-{raw_slug_text}.md"))
                if len(suffix_matched_paths) == 1:
                    return suffix_matched_paths[0]
    return None


def resolve_evidence_candidate_dirs(
    prd_path: Path, repo_root: Path, worktree_branches_list: list[tuple[str, Path]]
) -> list[Path]:
    """解析该 PRD 的证据目录候选：分支副本在前、主仓库副本兜底。

    worktree 定位与看板 EVIDENCE 列同一套规则（``match_worktree_for_prd``）：
    分支名等于 PRD slug，或分支名等于 PRD 正文 ``GitHub Issue:`` 行指向的
    ``issue-<编号>``（iar runner 的分支命名）。执行发生在 worktree 里，证据要等
    合并回主线才出现在主仓库，因此分支副本必须优先——两处不得分叉。

    Args:
        prd_path (Path): PRD 文件路径。
        repo_root (Path): 仓库根目录。
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, 目录路径)``
            列表，来自 ``prd_lock.list_linked_worktree_branches``。

    Returns:
        list[Path]: 证据目录候选，按查找优先级排列。
    """
    _, _, _, raw_slug_text = prd_locator.parse_prd_filename(prd_path)
    matched_worktree = prd_locator.match_worktree_for_prd(
        worktree_branches_list, raw_slug_text, prd_path
    )
    worktree_path = matched_worktree[1] if matched_worktree is not None else None

    main_evidence_dir = repo_root / "tasks" / "evidence" / prd_path.stem
    candidate_dirs_list = [main_evidence_dir]
    if worktree_path is not None:
        branch_evidence_dir = worktree_path / "tasks" / "evidence" / prd_path.stem
        if branch_evidence_dir.is_dir():
            candidate_dirs_list.insert(0, branch_evidence_dir)
    return candidate_dirs_list


def resolve_review_target(
    prd_path: Path, repo_root: Path, worktree_branches_list: list[tuple[str, Path]]
) -> Path | None:
    """解析人工审查要打开的文件：清单优先（HTML 先于 MD），证据报告兜底。

    槽位之间独立兜底：清单在主仓库、报告在分支时各命中各的，不因一个槽位落空
    整体丢弃。清单槽位内部按 ``.html → .md`` 解析——交互 HTML 与静态 Markdown
    内容一致时优先前者。

    Args:
        prd_path (Path): PRD 文件路径。
        repo_root (Path): 仓库根目录。
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, 目录路径)``
            列表，来自 ``prd_lock.list_linked_worktree_branches``。

    Returns:
        Path | None: 命中的文件路径；所有候选都没命中时为 ``None``。
    """
    candidate_dirs_list = resolve_evidence_candidate_dirs(
        prd_path, repo_root, worktree_branches_list
    )
    for stem_suffix_text, suffix_extensions_tuple in _REVIEW_TARGET_SLOTS:
        for candidate_dir_path in candidate_dirs_list:
            matched_target_path = prd_locator.find_named_evidence_file(
                candidate_dir_path,
                stem_suffix_text,
                suffix_extensions=suffix_extensions_tuple,
            )
            if matched_target_path is not None:
                return matched_target_path
    return None


def open_in_system_viewer(target_path: Path) -> bool:
    """用系统默认应用打开文件。

    Args:
        target_path (Path): 要打开的文件路径。

    Returns:
        bool: 是否成功发起打开动作；打开器缺失（如无 ``xdg-open``）时为 ``False``。
    """
    if sys.platform == "darwin":
        open_command_list = ["open", str(target_path)]
    else:
        open_command_list = ["xdg-open", str(target_path)]
    try:
        subprocess.run(open_command_list, check=False)
    except FileNotFoundError:
        return False
    return True


def _print_evidence_state(evidence_dirs_list: list[Path]) -> None:
    """打印证据目录现状，供"没有可打开文件"时给出下一步提示。"""
    existing_dirs_list = [entry_path for entry_path in evidence_dirs_list if entry_path.is_dir()]
    if not existing_dirs_list:
        print("  证据目录尚不存在：清单在交付收尾（验收横幅翻 🧍 待人工验收）时由执行方生成。")
        return
    for existing_dir_path in existing_dirs_list:
        listed_names = sorted(
            entry_path.name for entry_path in existing_dir_path.iterdir() if entry_path.is_file()
        )
        if listed_names:
            print(f"  证据目录 {existing_dir_path} 现有文件：")
            for listed_name in listed_names:
                print(f"    - {listed_name}")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv (list[str] | None): 参数列表；``None`` 时读 ``sys.argv``。

    Returns:
        int: 进程退出码；0 表示已打开或已打印路径，1 表示定位失败。
    """
    argument_parser = argparse.ArgumentParser(
        description="打开 PRD 的人工审查清单（分支副本优先；无清单时回退证据报告）。"
    )
    argument_parser.add_argument(
        "prd_file",
        help=(
            "PRD 路径、文件名或 slug；例如 tasks/archive/P1-FEAT-20260916-212206-<slug>.md、"
            "P1-FEAT-20260916-212206-<slug>.md 或 <slug>。（交付收尾即归档；仍在执行中的"
            " PRD 在 tasks/pending/。传文件名/slug 时会自动在 pending、archive 及各 worktree"
            " 副本间定位，不必记住当前该传哪个。）"
        ),
    )
    argument_parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="只打印解析到的路径，不调用系统打开器",
    )
    parsed_arguments = argument_parser.parse_args(argv)

    repo_root_path = prd_locator.resolve_repo_root()
    # 锁与 worktree 的唯一事实源在主仓库：worktree 内执行时仍能看到全部分支。
    main_repo_root_path = prd_lock.resolve_main_repo_root()
    worktree_branches_list = prd_lock.list_linked_worktree_branches(main_repo_root_path)

    prd_path = resolve_prd_path(parsed_arguments.prd_file, repo_root_path, worktree_branches_list)
    if prd_path is None:
        print(f"ERROR: PRD 文件不存在：{parsed_arguments.prd_file}")
        print("Usage: just prd review <prd-file> [--print]")
        return 1

    target_path = resolve_review_target(prd_path, repo_root_path, worktree_branches_list)
    if target_path is None:
        print(f"ERROR: {prd_path.name} 还没有人工审查清单或证据报告。")
        _print_evidence_state(
            resolve_evidence_candidate_dirs(prd_path, repo_root_path, worktree_branches_list)
        )
        return 1

    print(target_path)
    if parsed_arguments.print_only:
        return 0
    if "human-review-checklist" not in target_path.name:
        print("  提示：没有找到人工审查清单（html/md），先打开证据报告。")
    if not open_in_system_viewer(target_path):
        print("  提示：系统打开器不可用，请手动打开上面的路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
