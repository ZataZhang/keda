#!/usr/bin/env python3
"""PRD 的"定位"逻辑：文件名解析、仓库根、worktree 匹配、分支副本与证据文件查找。

看板 ``prd_status.py`` 与审查入口 ``prd_review.py`` 都要回答同一组问题——这条 PRD
叫什么、它的分支副本在哪个 worktree、证据文件落在哪个目录——答案必须逐字一致，
否则两处会各自读到不同的"当前状态"。这些规则因此集中在本模块，是纯路径 / 命名
逻辑，不依赖看板的数据模型，也不依赖任何其他 ``prd_*`` 兄弟模块，谁都可以安全 import。

要点（细节见各函数 docstring）：

- 执行发生在 worktree 里，进度与证据按**分支副本优先**读取；每个 worktree 都带一份
  未改动的同名 pending 副本，所以只认 slug 匹配到的那个 worktree，绝不拿别的
  worktree 的副本充数。
- 主仓库已是归档副本时不看分支：``tasks/archive`` 是终态，分支里残留的
  ``tasks/pending`` 只是合并前的快照。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PRD_FILENAME_PATTERN = re.compile(
    r"^(?:P(?P<priority>\d+)-)?(?:(?P<kind>[A-Z]+)-)?"
    r"(?P<date>\d{8})-(?:(?P<time>\d{6})-)?(?P<slug>.+)\.md$"
)


# 两个 PRD 桶目录名：``pending`` 是执行态，``archive`` 是收尾态。
ARCHIVE_BUCKET_DIR_NAME = "archive"
PENDING_BUCKET_DIR_NAME = "pending"


def parse_prd_filename(prd_path: Path) -> tuple[str, str, str, str]:
    """从 PRD 文件名解析优先级、类型、创建日期与短标识。

    Args:
        prd_path (Path): PRD 文件路径。

    Returns:
        tuple[str, str, str, str]: ``(priority, kind, created, slug)``，任何一项
        无法解析时返回空串；旧式命名（无 ``P{n}-{TYPE}`` 前缀）只解析日期与标识。
    """
    filename_match = PRD_FILENAME_PATTERN.match(prd_path.name)
    if not filename_match:
        return "", "", "", prd_path.stem

    raw_priority_text = filename_match.group("priority") or ""
    raw_kind_text = filename_match.group("kind") or ""
    raw_date_text = filename_match.group("date") or ""
    if len(raw_date_text) != 8:
        formatted_created_date = ""
    else:
        formatted_created_date = f"{raw_date_text[0:4]}-{raw_date_text[4:6]}-{raw_date_text[6:8]}"

    return (
        f"P{raw_priority_text}" if raw_priority_text else "",
        raw_kind_text,
        formatted_created_date,
        filename_match.group("slug") or prd_path.stem,
    )


def resolve_repo_root() -> Path:
    """定位仓库根目录。

    Returns:
        Path: 优先使用 git 仓库根；不在 git 仓库内时，从当前目录向上查找含
        ``tasks/`` 的目录；仍找不到时回退到当前工作目录。
    """
    try:
        raw_git_root_text = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if raw_git_root_text:
            return Path(raw_git_root_text)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    for candidate_root_path in [Path.cwd(), *Path.cwd().parents]:
        if (candidate_root_path / "tasks").is_dir():
            return candidate_root_path
    return Path.cwd()


def find_named_evidence_file(
    evidence_dir: Path,
    stem_suffix: str,
    suffix_extensions: tuple[str, ...] = (".md",),
) -> Path | None:
    """在证据目录中查找 ``<prd-basename>.<suffix><ext>`` 或 ``<suffix><ext>``。

    Args:
        evidence_dir (Path): 该 PRD 的证据目录。
        stem_suffix (str): 文件种类后缀，例如 ``verification-plan``。
        suffix_extensions (tuple[str, ...]): 按优先级排列的候选扩展名，
            默认仅 ``.md``；需要交互 HTML 优先于 Markdown 的调用方（如
            ``prd_review.py``）传入 ``(".html", ".md")``，命名模式仍由本函数
            单一事实源维护。

    Returns:
        Path | None: 命中的文件路径（按扩展名优先级），均不存在时返回 ``None``。
    """
    if not evidence_dir.is_dir():
        return None

    for suffix_extension_text in suffix_extensions:
        for evidence_filename in (
            f"{evidence_dir.name}.{stem_suffix}{suffix_extension_text}",
            f"{stem_suffix}{suffix_extension_text}",
        ):
            candidate_evidence_path = evidence_dir / evidence_filename
            if candidate_evidence_path.is_file():
                return candidate_evidence_path
    return None


def is_main_repo_archived(prd_path: Path) -> bool:
    """判断该 PRD 文件是否来自主仓库的 ``tasks/archive``。

    主仓库已有归档副本即"这条 PRD 已在主线完成收尾"——归档是终态。此时分支上
    残留的 ``tasks/pending`` 副本只是合并前的历史快照，不能再用它遮蔽主线事实。

    Args:
        prd_path (Path): 主仓库内的 PRD 文件路径。

    Returns:
        bool: 文件位于 ``tasks/archive`` 之下时为 ``True``。
    """
    return prd_path.parent.name == ARCHIVE_BUCKET_DIR_NAME


def match_worktree_by_slug(
    worktree_branches_list: list[tuple[str, Path]], slug_text: str
) -> tuple[str, Path] | None:
    """在 worktree 列表中找分支名（或其最后一段）与 PRD slug 相等的条目。

    Args:
        worktree_branches_list (list[tuple[str, Path]]): ``(分支名, worktree 路径)``
            列表，来自 ``prd_lock.list_linked_worktree_branches``。
        slug_text (str): PRD 文件名解析出的 slug。

    Returns:
        tuple[str, Path] | None: 命中的 ``(分支名, worktree 路径)``；无匹配为 ``None``。
    """
    for branch_name_text, worktree_path in worktree_branches_list:
        branch_basename_text = branch_name_text.rsplit("/", 1)[-1]
        if slug_text == branch_name_text or slug_text == branch_basename_text:
            return branch_name_text, worktree_path
    return None


def match_worktree_for_prd(
    worktree_branches_list: list[tuple[str, Path]], slug_text: str, prd_path: Path
) -> tuple[str, Path] | None:
    """按 PRD slug 或其明确关联的 Issue 编号定位 worktree。"""
    slug_match = match_worktree_by_slug(worktree_branches_list, slug_text)
    if slug_match is not None:
        return slug_match
    try:
        prd_text = prd_path.read_text(encoding="utf-8")
    except OSError:
        return None
    issue_matches = re.findall(
        r"(?im)^\s*[-*]?\s*GitHub Issue:\s*https://[^\s]+/issues/(\d+)\s*$",
        prd_text,
    )
    if len(set(issue_matches)) != 1:
        return None
    issue_branch_name = f"issue-{issue_matches[0]}"
    return match_worktree_by_slug(worktree_branches_list, issue_branch_name)


def resolve_branch_prd_path(worktree_path: Path | None, prd_file_name: str) -> Path | None:
    """在 worktree 内定位该 PRD 的分支副本。

    执行发生在 worktree 里，主仓库的 ``tasks/pending`` 副本要等合并才会更新，
    因此进度与依赖都该读分支副本。归档副本是收尾态，优先于同一分支里可能残留的
    pending 副本。调用方需先用 ``is_main_repo_archived`` 排除"主仓库已归档"的情形
    ——那时分支副本按定义已过时。

    Args:
        worktree_path (Path | None): 分支名匹配到的 worktree 目录；无匹配时为 ``None``。
        prd_file_name (str): PRD 文件名（含 ``.md``）。

    Returns:
        Path | None: 分支副本路径；worktree 内两处都没有时返回 ``None``。
    """
    if worktree_path is None:
        return None

    for bucket_dir_name in (ARCHIVE_BUCKET_DIR_NAME, PENDING_BUCKET_DIR_NAME):
        branch_prd_path = worktree_path / "tasks" / bucket_dir_name / prd_file_name
        if branch_prd_path.is_file():
            return branch_prd_path
    return None


def resolve_evidence_dirs(evidence_dir: Path, worktree_path: Path | None) -> list[Path]:
    """按优先级返回证据目录候选：分支副本在前，主仓库副本兜底。

    Args:
        evidence_dir (Path): 主仓库内的证据目录 ``tasks/evidence/<prd-stem>``。
        worktree_path (Path | None): 匹配到的 worktree 目录；无匹配时为 ``None``。

    Returns:
        list[Path]: 证据目录候选，按查找优先级排列。
    """
    if worktree_path is None:
        return [evidence_dir]

    branch_evidence_dir = worktree_path / "tasks" / "evidence" / evidence_dir.name
    if branch_evidence_dir.is_dir():
        return [branch_evidence_dir, evidence_dir]
    return [evidence_dir]


def find_evidence_file_in_dirs(candidate_dirs: list[Path], stem_suffix: str) -> Path | None:
    """按候选顺序查找证据文件，每个槽位独立兜底。

    分支只写了 plan、report 仍在主仓库时，两个槽位各自命中不同目录，不会因为
    分支证据目录存在就整体丢弃主仓库已有文件。

    Args:
        candidate_dirs (list[Path]): 证据目录候选，来自 ``resolve_evidence_dirs``。
        stem_suffix (str): 文件种类后缀，例如 ``verification-plan``。

    Returns:
        Path | None: 第一个命中的证据文件路径；都没命中时返回 ``None``。
    """
    for candidate_dir in candidate_dirs:
        matched_evidence_path = find_named_evidence_file(candidate_dir, stem_suffix)
        if matched_evidence_path is not None:
            return matched_evidence_path
    return None
