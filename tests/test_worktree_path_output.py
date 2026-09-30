"""``iar worktree path`` stdout 解析的回归测试。

路径命令的 stdout 可能被日志行污染：应用日志器固定写 stdout，而配置加载发生在
命令主体之前，于是 ``WARNING`` 会出现在路径行之前。朴素的 ``Path(stdout.strip())``
会把日志与路径拼成非法路径，导致路径「不存在」并把 Issue 误判为失败。这里锁定
加固后的行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    WorktreeConfig,
)
from backend.core.use_cases.agent_runner_worktree_probe import (
    _find_worktree_path_for_issue,
)
from backend.core.use_cases.worktree_path_output import parse_worktree_path_stdout
from tests.conftest import FakeProcessRunner

# 复刻真实污染：配置加载期打到 stdout 的弃用告警，内部还带一对引号（``"template"``），
# 正是会让旧的 shell-quote 正则提取错值的那类文本。
_POLLUTED_STDOUT_PREFIX = (
    "2026-09-30 13:17:01 - backend.infrastructure.config - WARNING - "
    'config pins generated_content.<target>.mode = "template"; '
    "run `iar config migrate`.\n"
)


def test_parses_clean_single_line() -> None:
    """无污染的单行 stdout 应直接解析为绝对路径。"""
    assert parse_worktree_path_stdout("/repo/.iar-worktrees/issue-53\n") == Path(
        "/repo/.iar-worktrees/issue-53"
    )


def test_ignores_leading_log_noise() -> None:
    """路径之前的日志行不得混进解析结果。"""
    polluted = _POLLUTED_STDOUT_PREFIX + "/repo/.iar-worktrees/issue-53\n"
    assert parse_worktree_path_stdout(polluted) == Path("/repo/.iar-worktrees/issue-53")


def test_uses_last_non_empty_line_with_trailing_blanks() -> None:
    """取最后一行非空文本，忽略尾随空白行。"""
    assert parse_worktree_path_stdout("/repo/wt\n\n  \n") == Path("/repo/wt")


def test_strips_surrounding_quotes() -> None:
    """整体被引号包裹的路径应去掉成对引号。"""
    assert parse_worktree_path_stdout('"/repo/wt"\n') == Path("/repo/wt")


def test_empty_stdout_raises() -> None:
    """完全空的 stdout 必须显式报错，而非退化成当前目录。"""
    with pytest.raises(ValueError):
        parse_worktree_path_stdout("\n   \n")


def test_probe_tolerates_polluted_stdout(tmp_path: Path) -> None:
    """集成回归：被日志污染的 path_command 输出仍能解析出真实 worktree。"""
    worktree_path = tmp_path / ".iar-worktrees" / "issue-53"
    worktree_path.mkdir(parents=True)
    config = AppConfig(worktree=WorktreeConfig(path_command="echo {issue_number}"))
    issue = IssueSummary(
        number=53,
        title="worktree path hardening",
        url="https://example.invalid/issues/53",
        body="",
        labels=(),
    )
    runner = FakeProcessRunner(
        responses={
            ("echo", "53"): CommandResult(
                command=("echo", "53"),
                return_code=0,
                stdout=f"{_POLLUTED_STDOUT_PREFIX}{worktree_path}\n",
                stderr="",
            )
        }
    )

    resolved = _find_worktree_path_for_issue(tmp_path, issue, config, runner)

    assert resolved == worktree_path.resolve()


def test_probe_raises_file_not_found_on_empty_stdout(tmp_path: Path) -> None:
    """空 stdout 应统一归为 FileNotFoundError，供上游 blocked 流程接住。"""
    config = AppConfig(worktree=WorktreeConfig(path_command="echo {issue_number}"))
    issue = IssueSummary(
        number=53,
        title="worktree path hardening",
        url="https://example.invalid/issues/53",
        body="",
        labels=(),
    )
    runner = FakeProcessRunner(
        responses={
            ("echo", "53"): CommandResult(
                command=("echo", "53"),
                return_code=0,
                stdout="",
                stderr="",
            )
        }
    )

    with pytest.raises(FileNotFoundError):
        _find_worktree_path_for_issue(tmp_path, issue, config, runner)
