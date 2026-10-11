"""执行工作区定位（backlog_prd_worktree）单测。

事实源是 ``git worktree list --porcelain`` 的原始文本：分支名、路径、detached
条目全部来自解析，不硬编码目录布局。「命令失败」必须区别于「成功但没有工作区」，
多重命中必须报歧义而不是任选一条——否则运行中 PRD 会被误判成无工作区并回落主仓库。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.backlog_prd_worktree import (
    WorktreeMatchStatus,
    WorktreeQueryStatus,
    extract_unique_issue_number,
    list_worktrees,
    match_prd_worktree,
    parse_prd_slug,
)


class _ScriptedRunner:
    """按命令片段返回预设 git 输出的假进程端口。"""

    def __init__(self, handler: Callable[[Sequence[str]], CommandResult]) -> None:
        self._handler = handler
        self.calls: list[list[str]] = []

    def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001, D102
        self.calls.append(list(command))
        return self._handler(list(command))


def _porcelain_runner(stdout_text: str, return_code: int = 0) -> _ScriptedRunner:
    return _ScriptedRunner(
        lambda _cmd: CommandResult(
            command=("git",), return_code=return_code, stdout=stdout_text, stderr=""
        )
    )


PORCELAIN_TWO = (
    "worktree /repo/main\n"
    "HEAD abc\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /repo/.iar-worktrees/issue-274\n"
    "HEAD def\n"
    "branch refs/heads/issue-274\n"
    "\n"
)


def test_list_worktrees_parses_branch_and_path() -> None:
    """porcelain 输出解析出 (branch, path)，路径原样保留。"""
    runner = _porcelain_runner(PORCELAIN_TWO)
    result = list_worktrees(runner, Path("/repo/main"))
    assert result.status is WorktreeQueryStatus.AVAILABLE
    assert result.available is True
    branches = {(entry.branch, entry.path) for entry in result.entries}
    assert ("main", Path("/repo/main")) in branches
    assert ("issue-274", Path("/repo/.iar-worktrees/issue-274")) in branches


def test_list_worktrees_skips_detached_entries() -> None:
    """detached（无 branch 行）条目被跳过，不进入候选。"""
    detached = (
        "worktree /repo/main\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /repo/.iar-worktrees/detached\nHEAD 123\n\n"
    )
    result = list_worktrees(_porcelain_runner(detached), Path("/repo/main"))
    assert len(result.entries) == 1
    assert result.entries[0].branch == "main"


def test_list_worktrees_nonzero_is_unavailable_not_empty() -> None:
    """命令非零退出 = 检索失败（unavailable），绝不等同于「没有工作区」。"""
    result = list_worktrees(_porcelain_runner("", return_code=128), Path("/repo/main"))
    assert result.status is WorktreeQueryStatus.UNAVAILABLE
    assert result.available is False
    assert result.entries == ()


def test_list_worktrees_exception_is_unavailable() -> None:
    """端口抛异常（git 不可用）同样标 unavailable，不冒充空列表。"""

    def _boom(_cmd: Sequence[str]) -> CommandResult:
        raise OSError("git not found")

    result = list_worktrees(_ScriptedRunner(_boom), Path("/repo/main"))
    assert result.status is WorktreeQueryStatus.UNAVAILABLE


def test_match_prefers_slug_over_issue() -> None:
    """slug 唯一命中优先返回 matched。"""
    result = list_worktrees(_porcelain_runner(PORCELAIN_TWO), Path("/repo/main"))
    match = match_prd_worktree(result.entries, slug="issue-274", issue_number=None)
    assert match.status is WorktreeMatchStatus.MATCHED
    assert match.branch == "issue-274"


def test_match_branch_last_segment_counts_as_slug_hit() -> None:
    """``feat/<slug>`` 布局的分支最后一段与 slug 相等也算命中。"""
    entries_text = "worktree /repo/x\nHEAD a\nbranch refs/heads/feat/console-progress\n\n"
    result = list_worktrees(_porcelain_runner(entries_text), Path("/repo/main"))
    match = match_prd_worktree(result.entries, slug="console-progress", issue_number=None)
    assert match.status is WorktreeMatchStatus.MATCHED
    assert match.branch == "feat/console-progress"


def test_match_issue_fallback_when_slug_misses() -> None:
    """slug 未命中时用 ``issue-<N>`` 兜底。"""
    result = list_worktrees(_porcelain_runner(PORCELAIN_TWO), Path("/repo/main"))
    match = match_prd_worktree(result.entries, slug="totally-different", issue_number=274)
    assert match.status is WorktreeMatchStatus.MATCHED
    assert match.branch == "issue-274"


def test_match_none_when_query_succeeds_without_hit() -> None:
    """检索成功但没有任何分支命中 = none（可回落主仓库）。"""
    result = list_worktrees(_porcelain_runner(PORCELAIN_TWO), Path("/repo/main"))
    match = match_prd_worktree(result.entries, slug="missing", issue_number=999)
    assert match.status is WorktreeMatchStatus.NONE


def test_match_ambiguous_on_multiple_slug_hits() -> None:
    """同一 slug 命中多条分支时报歧义，绝不按列表顺序任选。"""
    two_hits = (
        "worktree /repo/a\nHEAD 1\nbranch refs/heads/issue-7\n\n"
        "worktree /repo/b\nHEAD 2\nbranch refs/heads/feat/issue-7\n\n"
    )
    result = list_worktrees(_porcelain_runner(two_hits), Path("/repo/main"))
    match = match_prd_worktree(result.entries, slug="issue-7", issue_number=None)
    assert match.status is WorktreeMatchStatus.AMBIGUOUS
    assert match.entry is None


def test_match_unavailable_propagates_query_failure() -> None:
    """检索失败时匹配结果必须是 unavailable，不能退化成 none。"""
    result = list_worktrees(_porcelain_runner("", return_code=1), Path("/repo/main"))
    match = match_prd_worktree(
        result.entries, slug="x", issue_number=1, query_available=result.available
    )
    assert match.status is WorktreeMatchStatus.UNAVAILABLE


def test_parse_prd_slug_from_canonical_name() -> None:
    """canonical 文件名解析出 slug（最后一个日期/时间段之后）。"""
    name = "P1-FEAT-20261010-093054-console-prd-progress-signals.md"
    assert parse_prd_slug(name) == "console-prd-progress-signals"


def test_parse_prd_slug_falls_back_to_stem() -> None:
    """无法解析的旧命名回退到 stem。"""
    assert parse_prd_slug("legacy-name.md") == "legacy-name"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("GitHub Issue: https://github.com/ZataZhang/keda/issues/274\n", 274),
        ("- GitHub Issue: https://github.com/a/b/issues/12\n", 12),
        ("no issue here\n", None),
        (
            "GitHub Issue: https://github.com/a/b/issues/1\n"
            "GitHub Issue: https://github.com/a/b/issues/2\n",
            None,
        ),
    ],
)
def test_extract_unique_issue_number(body: str, expected: int | None) -> None:
    """唯一 Issue 锚点解析出编号；多个不同编号视为歧义返回 None。"""
    assert extract_unique_issue_number(body) == expected
