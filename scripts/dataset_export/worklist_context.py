"""给待补全清单补上"改动上下文"：补全员据此判断题目范围与能否用测试判分。

补全员试跑时反馈，清单里只有提交主题和规模，导致他们不得不自己翻仓库去弄清三件事：

- **改了哪些文件、是否夹带了无关改动**：给出改动文件及按区域的分组；
- **测试是否依赖这次新增的内部符号**：测试直接引用新增的函数、类、常量或模块时，任务描述又不能
  写出这些名字，用测试判分对做题的人不公平——这是 ``check_recipe`` 能不能填的关键；
- **提交信息里 ``Closes #N`` 对应的 Issue**：大改动的原始需求往往写在那里。

这里全部是**提示**，不是结论：符号依赖靠对 diff 的文本匹配，会漏也会误报，最终由补全员或人判断。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.dataset_export.export_samples import fetch_gh_json, run_git

MAX_CHANGED_FILES = 60
MAX_LISTED_ITEMS = 20
CLOSING_ISSUE_BODY_MAX_CHARS = 4000
HINT_NO_TESTS = "no_tests"
HINT_ONLY_TEST_CHANGES = "only_test_changes"
HINT_TESTS_USE_NEW_SYMBOLS = "tests_use_new_symbols"
HINT_TESTS_USE_EXISTING_ENTRY_POINTS = "tests_use_existing_entry_points"

TEST_PATH_PATTERN = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*\.py$|\.(test|spec)\.[jt]sx?$"
)
CLOSING_REFERENCE_PATTERN = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE
)
_DIFF_FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_NEW_DEFINITION_PATTERNS = (
    re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_]\w{3,})"),
    re.compile(r"^\+\s*class\s+([A-Za-z_]\w{3,})"),
    re.compile(r"^\+\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w{3,})"),
    re.compile(r"^\+\s*([A-Z][A-Z0-9_]{5,})\s*="),
)
_CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")


def is_test_path(path: str) -> bool:
    """文件路径是否属于测试代码。"""
    return bool(TEST_PATH_PATTERN.search(path))


def change_area(path: str) -> str:
    """把文件归到"区域"：取前两级目录；只有一级目录取它；根目录文件记为 ``.``。"""
    parts = path.split("/")
    if len(parts) == 1:
        return "."
    return "/".join(parts[:2]) if len(parts) >= 3 else parts[0]


def _split_diff_by_file(diff_text: str) -> dict[str, list[str]]:
    """把一次 ``git diff`` 的输出按文件拆成新增行列表（只保留以 ``+`` 开头的实际新增行）。"""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in diff_text.splitlines():
        header = _DIFF_FILE_HEADER.match(line)
        if header:
            current = sections.setdefault(header.group(2), [])
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current.append(line)
    return sections


def _added_definitions(added_lines: list[str]) -> set[str]:
    """从新增行里找出新定义的函数、类、常量名。"""
    names: set[str] = set()
    for line in added_lines:
        for pattern in _NEW_DEFINITION_PATTERNS:
            matched = pattern.match(line)
            if matched:
                names.add(matched.group(1))
    return names


def collect_change_context(
    *, repo_path: Path, base_sha: str, final_sha: str
) -> dict[str, Any] | None:
    """还原一个样本的改动上下文；取不到 diff（对象不在本地）时返回 ``None``。"""
    name_status = run_git(
        repo_path=repo_path, args=["diff", "--name-status", "-M", base_sha, final_sha]
    )
    diff_text = run_git(repo_path=repo_path, args=["diff", base_sha, final_sha])
    if name_status is None or diff_text is None:
        return None
    entries = [line.split("\t") for line in name_status.splitlines() if line.strip()]
    changed_files = [entry[-1] for entry in entries]
    added_files = [entry[-1] for entry in entries if entry[0].startswith("A")]
    sections = _split_diff_by_file(diff_text)
    test_files = [path for path in changed_files if is_test_path(path)]

    new_symbols = {
        symbol
        for path, added in sections.items()
        if not is_test_path(path)
        for symbol in _added_definitions(added)
    }
    new_symbols |= {
        Path(path).stem
        for path in added_files
        if not is_test_path(path) and path.endswith(_CODE_SUFFIXES) and len(Path(path).stem) >= 5
    }
    test_added_text = "\n".join(
        line for path, added in sections.items() if is_test_path(path) for line in added
    )
    used_by_tests = sorted(
        symbol for symbol in new_symbols if re.search(rf"\b{re.escape(symbol)}\b", test_added_text)
    )
    if changed_files and len(test_files) == len(changed_files):
        # 只改了测试：没有产品行为变化，通常不构成一道题。
        hint = HINT_ONLY_TEST_CHANGES
    elif not test_files:
        hint = HINT_NO_TESTS
    elif used_by_tests:
        hint = HINT_TESTS_USE_NEW_SYMBOLS
    else:
        hint = HINT_TESTS_USE_EXISTING_ENTRY_POINTS
    return {
        "changed_file_count": len(changed_files),
        "changed_files": changed_files[:MAX_CHANGED_FILES],
        "files_by_area": dict(Counter(change_area(path) for path in changed_files).most_common()),
        "test_files": test_files[:MAX_LISTED_ITEMS],
        "new_files": [path for path in added_files if not is_test_path(path)][:MAX_LISTED_ITEMS],
        "new_symbols_referenced_by_tests": used_by_tests[:MAX_LISTED_ITEMS],
        "check_recipe_hint": hint,
    }


def parse_closing_issue_numbers(texts: list[str | None]) -> list[int]:
    """从提交主题、提交正文或 PR 描述里解析 ``Closes #N`` / ``Fixes #N`` 引用，去重并保持顺序。"""
    numbers: list[int] = []
    for text in texts:
        for matched in CLOSING_REFERENCE_PATTERN.finditer(text or ""):
            number = int(matched.group(1))
            if number not in numbers:
                numbers.append(number)
    return numbers


def fetch_closing_issues(*, repo_path: Path, numbers: list[int]) -> list[dict[str, Any]]:
    """用 ``gh`` 取这些 Issue 的标题与正文（正文截断）；取不到的跳过。"""
    issues: list[dict[str, Any]] = []
    for number in numbers:
        issue = fetch_gh_json(
            repo_path=repo_path, args=["issue", "view", str(number), "--json", "title,body"]
        )
        if isinstance(issue, dict):
            issues.append(
                {
                    "number": number,
                    "title": issue.get("title"),
                    "body": (issue.get("body") or "")[:CLOSING_ISSUE_BODY_MAX_CHARS],
                }
            )
    return issues
