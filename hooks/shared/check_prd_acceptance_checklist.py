#!/usr/bin/env python3
"""Check that the executor-owed items of PRD acceptance checklists are completed.

归档只代表执行侧交付完成：``Human-Confirmed`` 分组内的空框是留给人的待答项，不拦
提交；其余空框照旧拦截。分组识别语义与 prd skill 的 ``prd_contract.parse_checklist``
一致（标题 ``###``–``######`` 或整行加粗的分组标签、嵌套分组按"包含它的任一层是否
人属"判定、前缀匹配且大小写不敏感）。skill 是可选安装的，本 hook 随模板同步，二者
不能互相 import，所以这里自带一份轻量实现；模板仓库的守卫测试会逐例校验两者等价，
改动分组语义时必须两处一起改。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ACTIVE_PRD_PATH_RE = re.compile(r"^tasks/([^/]+-prd-[^/]+|P[0-3]-[A-Z]+-\d{8}-\d{6}-[^/]+)\.md$")
ARCHIVED_PRD_PATH_RE = re.compile(
    r"^tasks/archive/([^/]+-prd-[^/]+|P[0-3]-[A-Z]+-\d{8}-\d{6}-[^/]+)\.md$"
)
ACCEPTANCE_CHECKLIST_HEADING_RE = re.compile(
    r"^##\s+(?:\d+\.\s+)?(?:Acceptance Checklist\b.*|验收清单.*)\s*$"
)
TOP_LEVEL_HEADING_RE = re.compile(r"^##\s+")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s*(?P<label>.*)$")
CODE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
GROUP_HEADING_RE = re.compile(r"^(#{3,6})\s+(.+?)\s*$")
BOLD_GROUP_LABEL_RE = re.compile(r"^\s*(?:\*\*|__)\s*(?P<label>.+?)\s*(?:\*\*|__)\s*$")
BOLD_GROUP_DEPTH = 3
HUMAN_CONFIRMED_GROUP_PREFIX = "human-confirmed"


def _repo_root() -> Path:
    """Return the repository root inferred from this file location."""

    return Path(__file__).resolve().parents[2]


def _relative_path(path: Path, repo_root: Path) -> Path | None:
    """Return a repository-relative path when the file is inside the repo."""

    try:
        return path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None


def _is_active_prd_path(path: Path, repo_root: Path) -> bool:
    """Return whether a path is an active root-level PRD markdown file."""

    relative_path = _relative_path(path, repo_root)
    if relative_path is None:
        return False

    if relative_path.parent != Path("tasks"):
        return False
    return bool(ACTIVE_PRD_PATH_RE.match(relative_path.as_posix()))


def _is_archived_prd_path(path: Path, repo_root: Path) -> bool:
    """Return whether a path is an archived PRD markdown file."""

    relative_path = _relative_path(path, repo_root)
    if relative_path is None:
        return False

    return bool(ARCHIVED_PRD_PATH_RE.match(relative_path.as_posix()))


def _staged_archive_prd_paths(repo_root: Path) -> set[Path]:
    """Return PRDs newly added, copied, or renamed into the archive in git index."""

    git_diff_process = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--name-status",
            "--diff-filter=ACR",
            "--",
            "tasks/archive",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    staged_archive_paths: set[Path] = set()
    for raw_status_line in git_diff_process.stdout.splitlines():
        status_parts = raw_status_line.split("\t")
        if not status_parts:
            continue

        staged_relative_path_text = status_parts[-1].strip()
        if not staged_relative_path_text:
            continue

        staged_relative_path = Path(staged_relative_path_text)
        if ARCHIVED_PRD_PATH_RE.match(staged_relative_path.as_posix()):
            staged_archive_paths.add(staged_relative_path)

    return staged_archive_paths


def _candidate_prd_paths(
    repo_root: Path,
    provided_paths: Iterable[Path],
    staged_archive_prd_paths: set[Path] | None = None,
) -> list[Path]:
    """Return active PRD paths to validate."""

    staged_archive_prd_paths = (
        _staged_archive_prd_paths(repo_root)
        if staged_archive_prd_paths is None
        else staged_archive_prd_paths
    )
    provided_paths_list = list(provided_paths)
    if provided_paths_list:
        candidate_paths: list[Path] = []
        for path in provided_paths_list:
            relative_path = _relative_path(path, repo_root)
            if relative_path is None:
                continue
            if _is_active_prd_path(path, repo_root):
                candidate_paths.append(path)
                continue
            if _is_archived_prd_path(path, repo_root) and relative_path in staged_archive_prd_paths:
                candidate_paths.append(path)
        return candidate_paths

    tasks_dir = repo_root / "tasks"
    if not tasks_dir.exists():
        return []

    discovered_paths: list[Path] = []
    for prd_path in sorted(tasks_dir.glob("*.md")):
        if _is_active_prd_path(prd_path, repo_root):
            discovered_paths.append(prd_path)
    for archived_prd_path in sorted(staged_archive_prd_paths):
        discovered_paths.append(repo_root / archived_prd_path)
    return discovered_paths


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return the line bounds for the Acceptance Checklist section."""

    start_index: int | None = None
    for line_index, line in enumerate(lines):
        if ACCEPTANCE_CHECKLIST_HEADING_RE.match(line):
            start_index = line_index
            break

    if start_index is None:
        return None

    end_index = len(lines)
    for line_index in range(start_index + 1, len(lines)):
        if TOP_LEVEL_HEADING_RE.match(lines[line_index]):
            end_index = line_index
            break

    return start_index + 1, end_index


def _enter_group(group_stack: list[tuple[int, bool]], depth: int, label: str) -> None:
    """Open a checklist group, closing every group of the same or a deeper level.

    栈元素是 ``(层级, 是否人属组)``：条目归属"包含它的所有分组"，所以人属判定看整条栈，
    嵌套的子标题不会把人属项踢出人属组。
    """

    while group_stack and depth <= group_stack[-1][0]:
        group_stack.pop()
    is_human_group = label.strip().casefold().startswith(HUMAN_CONFIRMED_GROUP_PREFIX)
    group_stack.append((depth, is_human_group))


def _unchecked_items_in_acceptance_section(file_content: str) -> list[tuple[int, str]]:
    """Return unchecked checklist items the executing agent still owes.

    ``Human-Confirmed`` 分组作用范围内的空框不计入：它们是人的待答项，执行侧不得代勾。
    """

    lines = file_content.splitlines()
    section_bounds = _section_bounds(lines)
    if section_bounds is None:
        return [(-1, "Missing Acceptance Checklist section")]

    start_index, end_index = section_bounds
    unchecked_items: list[tuple[int, str]] = []
    in_code_block = False
    group_stack: list[tuple[int, bool]] = []

    for line_index in range(start_index, end_index):
        line = lines[line_index]
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        heading_match = GROUP_HEADING_RE.match(line)
        if heading_match:
            _enter_group(group_stack, len(heading_match.group(1)), heading_match.group(2))
            continue

        bold_group_match = BOLD_GROUP_LABEL_RE.match(line)
        if bold_group_match:
            _enter_group(group_stack, BOLD_GROUP_DEPTH, bold_group_match.group("label"))
            continue

        checkbox_match = CHECKBOX_RE.match(line)
        if checkbox_match and checkbox_match.group("mark") == " ":
            if not any(is_human_group for _, is_human_group in group_stack):
                unchecked_items.append((line_index + 1, line.rstrip()))

    return unchecked_items


def _validate_file(path: Path) -> list[tuple[int, str]]:
    """Read a PRD file and return any checklist issues."""

    file_content = path.read_text(encoding="utf-8")
    return _unchecked_items_in_acceptance_section(file_content)


def main() -> int:
    """Run the acceptance checklist validation."""

    repo_root = _repo_root()
    provided_paths = [repo_root / Path(argument) for argument in sys.argv[1:]]
    candidate_paths = _candidate_prd_paths(repo_root, provided_paths)

    if not candidate_paths:
        return 0

    has_errors = False
    print("🔍 Checking PRD acceptance checklists...\n")

    for prd_path in candidate_paths:
        relative_path = prd_path.resolve().relative_to(repo_root.resolve())
        issues = _validate_file(prd_path)
        if not issues:
            print(f"✅ {relative_path.as_posix()}")
            continue

        has_errors = True
        print(f"❌ {relative_path.as_posix()}")
        for line_number, issue_text in issues:
            if line_number < 0:
                print(f"   - {issue_text}")
            else:
                print(f"   - L{line_number}: {issue_text}")
        print()

    if has_errors:
        print(
            "⚠️  One or more PRD acceptance checklists still contain unchecked items the "
            "executor owes (Human-Confirmed items may stay open)."
        )
        return 1

    print("\n🎉 All PRD acceptance checklists are complete (Human-Confirmed items excluded).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
