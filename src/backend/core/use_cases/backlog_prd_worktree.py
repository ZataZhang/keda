"""Backlog PRD 的执行工作区定位（只读）。

看板 ``prd status`` 靠 worktree 把「运行中 PRD」的进度与证据定位到真实执行分支；
详情页要对齐这个口径，就必须先按 PRD 标识（slug / GitHub Issue 锚点）在
``git worktree list --porcelain`` 的结果里找到唯一对应的分支目录。

语义与 :mod:`scripts.shared.just.prd_locator` 同序，但**不 import 运行期脚本**
（console 跨仓库、各仓 scripts 版本不同步）：

- 成功检索但没有任何分支命中 = ``none``（可回落主仓库）；
- 命令执行失败 / 仓库不可读 = ``unavailable``（不能当成「没有工作区」）；
- 多条分支同时命中同一 slug 或 ``issue-<N>`` = ``ambiguous``，绝不按列表顺序任选。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner

#: PRD 文件名语义：``P{n}-{TYPE}-{date}-{time}-{slug}.md``；解析失败回退 stem。
_PRD_FILENAME_PATTERN = re.compile(
    r"^(?:P(?P<priority>\d+)-)?(?:(?P<kind>[A-Z]+)-)?"
    r"(?P<date>\d{8})-(?:(?P<time>\d{6})-)?(?P<slug>.+)\.md$"
)
#: PRD 正文里的 GitHub Issue 锚点行（与 prd_locator 同一条正则），用于 ``issue-<N>`` 兜底。
_ISSUE_ANCHOR_PATTERN = re.compile(
    r"(?im)^\s*[-*]?\s*GitHub Issue:\s*https://[^\s]+/issues/(\d+)\s*$"
)


class WorktreeQueryStatus(str, Enum):
    """``git worktree list`` 这次检索本身成不成功。"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class WorktreeMatchStatus(str, Enum):
    """把某条 PRD 匹配到工作区的结果。"""

    MATCHED = "matched"
    NONE = "none"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class WorktreeEntry:
    """``git worktree list --porcelain`` 解析出的一个带分支的工作区。

    Attributes:
        branch: 短分支名（``refs/heads/`` 之后的部分），如 ``issue-274``。
        path: 工作区根目录（绝对路径）。
    """

    branch: str
    path: Path


@dataclass(frozen=True)
class WorktreeListResult:
    """一次 worktree 检索的返回。

    Attributes:
        status: ``available`` 表示命令成功执行（哪怕是空列表）；
            ``unavailable`` 表示命令失败或仓库不可读，调用方不得据此判定无工作区。
        entries: 解析出的 ``(branch, path)`` 列表；detached 条目已被跳过。
    """

    status: WorktreeQueryStatus
    entries: tuple[WorktreeEntry, ...]

    @property
    def available(self) -> bool:
        """命令是否成功执行。"""
        return self.status is WorktreeQueryStatus.AVAILABLE


@dataclass(frozen=True)
class WorktreeMatchResult:
    """把 PRD 匹配到工作区的结果。

    Attributes:
        status: 见 :class:`WorktreeMatchStatus`。
        entry: 仅 ``matched`` 时给出唯一命中的工作区；其余状态为 ``None``。
    """

    status: WorktreeMatchStatus
    entry: WorktreeEntry | None

    @property
    def branch(self) -> str | None:
        """命中分支名（未命中为 ``None``）。"""
        return self.entry.branch if self.entry is not None else None


def parse_prd_slug(prd_file_name: str) -> str:
    """从 PRD 文件名解析 slug，解析失败时回退文件名 stem。

    Args:
        prd_file_name: PRD 文件名（含 ``.md``），不含目录。

    Returns:
        slug 字符串；旧式命名或无法解析时返回 stem。
    """
    filename_match = _PRD_FILENAME_PATTERN.match(prd_file_name)
    if filename_match is None:
        return Path(prd_file_name).stem
    return filename_match.group("slug") or Path(prd_file_name).stem


def extract_unique_issue_number(prd_text: str) -> int | None:
    """从 PRD 正文解析**唯一**的 GitHub Issue 编号。

    与 prd_locator 一致：出现多个不同的 Issue 编号时视为歧义，返回 ``None``
    （宁可回落到主仓库，也不拿一个不确定的编号去匹配分支）。

    Args:
        prd_text: PRD 全文。

    Returns:
        唯一的 Issue 编号；没有或存在多个不同编号时为 ``None``。
    """
    found_numbers = {int(match) for match in _ISSUE_ANCHOR_PATTERN.findall(prd_text)}
    if len(found_numbers) != 1:
        return None
    return found_numbers.pop()


def list_worktrees(process_runner: IProcessRunner, repo_path: Path) -> WorktreeListResult:
    """用 ``git worktree list --porcelain`` 列出该仓库的所有工作区。

    porcelain 布局同时覆盖 ``.iar-worktrees/<branch>``（issue 流程）与 ``just
    worktree`` 的兄弟目录布局，因此不硬编码任何路径拼接。detached（无 ``branch``
    行）条目被跳过；命令非零退出或执行异常一律记为 ``unavailable``，不能据此
    断定「没有工作区」。

    Args:
        process_runner: 只读命令端口（core 不得直接 subprocess）。
        repo_path: 主仓库根目录。

    Returns:
        带分支的工作区列表与检索状态。
    """
    command = ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"]
    try:
        list_result = process_runner.run(command, cwd=repo_path, check=False)
    except Exception:  # noqa: BLE001 - git 不可用/仓库不可读都是「检索失败」而非「无工作区」
        return WorktreeListResult(WorktreeQueryStatus.UNAVAILABLE, ())
    if list_result.return_code != 0:
        return WorktreeListResult(WorktreeQueryStatus.UNAVAILABLE, ())

    entries: list[WorktreeEntry] = []
    current_path: Path | None = None
    for worktree_line in list_result.stdout.splitlines():
        if worktree_line.startswith("worktree "):
            current_path = Path(worktree_line.removeprefix("worktree ").strip())
            continue
        if worktree_line.startswith("branch refs/heads/") and current_path is not None:
            branch_name = worktree_line.removeprefix("branch refs/heads/").strip()
            if branch_name:
                entries.append(WorktreeEntry(branch=branch_name, path=current_path))
        if not worktree_line.strip():
            current_path = None
    return WorktreeListResult(WorktreeQueryStatus.AVAILABLE, tuple(entries))


def _matches_branch_key(entry: WorktreeEntry, key: str) -> bool:
    """分支全名或最后一段与 ``key`` 相等即命中（覆盖 ``feat/foo`` 布局）。"""
    return key == entry.branch or key == entry.branch.rsplit("/", 1)[-1]


def match_prd_worktree(
    entries: tuple[WorktreeEntry, ...],
    *,
    slug: str,
    issue_number: int | None,
    query_available: bool = True,
) -> WorktreeMatchResult:
    """按 slug 优先、``issue-<N>`` 兜底把 PRD 匹配到唯一工作区。

    匹配规则与 prd_locator 同序：先看 slug（分支名或其最后一段相等），唯一命中即
    ``matched``，多命中即 ``ambiguous``；slug 无命中且给定 Issue 编号时，再用
    ``issue-<N>`` 兜底；两者都不中且检索成功才是 ``none``。检索失败（``query_available``
    为假）直接返回 ``unavailable``，绝不冒充 ``none`` 从而被误当作「无工作区」。

    Args:
        entries: :func:`list_worktrees` 解析出的工作区列表。
        slug: PRD 文件名解析出的 slug。
        issue_number: PRD 唯一 Issue 编号；无或歧义时为 ``None``。
        query_available: worktree 检索是否成功。

    Returns:
        匹配结果（含唯一命中条目，若有）。
    """
    if not query_available:
        return WorktreeMatchResult(WorktreeMatchStatus.UNAVAILABLE, None)

    slug_hits = [entry for entry in entries if _matches_branch_key(entry, slug)]
    if len(slug_hits) == 1:
        return WorktreeMatchResult(WorktreeMatchStatus.MATCHED, slug_hits[0])
    if len(slug_hits) > 1:
        return WorktreeMatchResult(WorktreeMatchStatus.AMBIGUOUS, None)

    if issue_number is not None:
        issue_key = f"issue-{issue_number}"
        issue_hits = [entry for entry in entries if _matches_branch_key(entry, issue_key)]
        if len(issue_hits) == 1:
            return WorktreeMatchResult(WorktreeMatchStatus.MATCHED, issue_hits[0])
        if len(issue_hits) > 1:
            return WorktreeMatchResult(WorktreeMatchStatus.AMBIGUOUS, None)

    return WorktreeMatchResult(WorktreeMatchStatus.NONE, None)
