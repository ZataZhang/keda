"""证据分支清理判定的单元测试。

清理只按 PR 合并时间判断，所以这里的重点是「不该删的一律不删」：未合并、查不到
合并时间、保留期内都必须保留；只有合并时间超过保留期才可删。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.use_cases.agent_runner_evidence_prune import (
    _merged_at_from_output,
    parse_github_slug,
    parse_retention_days,
    plan_evidence_prune,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:ZataZhang/keda.git", "ZataZhang/keda"),
        ("https://github.com/ZataZhang/keda.git", "ZataZhang/keda"),
        ("ssh://git@ssh.github.com:443/ZataZhang/keda.git", "ZataZhang/keda"),
        ("git@gitlab.com:a/b.git", None),
    ],
)
def test_parse_github_slug(url: str, expected: str | None) -> None:
    """只有 GitHub 地址才解析出 owner/repo，端口写法也要认。"""
    assert parse_github_slug(url) == expected


@pytest.mark.parametrize(
    ("text", "days"),
    [("30", 30), ("30d", 30), ("2w", 14)],
)
def test_parse_retention_days(text: str, days: int) -> None:
    """保留期接受天与周两种写法。"""
    assert parse_retention_days(text) == days


@pytest.mark.parametrize("text", ["", "abc", "0", "0d"])
def test_parse_retention_days_rejects_bad_values(text: str) -> None:
    """非法或小于一天的保留期直接拒绝。"""
    with pytest.raises(ValueError):
        parse_retention_days(text)


def test_merged_long_ago_is_eligible() -> None:
    """合并超过保留期的分支可删。"""
    merged = NOW - timedelta(days=40)
    decisions = plan_evidence_prune(
        {55: "iar-evidence/issue-55"},
        {55: merged},
        older_than_days=30,
        now=NOW,
    )
    assert decisions[0].eligible is True
    assert "40" in decisions[0].reason


def test_recent_merge_is_kept() -> None:
    """保留期内的分支不删。"""
    decisions = plan_evidence_prune(
        {49: "iar-evidence/issue-49"},
        {49: NOW - timedelta(days=10)},
        older_than_days=30,
        now=NOW,
    )
    assert decisions[0].eligible is False
    assert "保留期" in decisions[0].reason


def test_unknown_merge_state_is_kept() -> None:
    """查不到合并时间（PR 未合并或查询失败）时一律保留。"""
    decisions = plan_evidence_prune(
        {7: "evidence/manual-review"},
        {},
        older_than_days=1,
        now=NOW,
    )
    assert decisions[0].eligible is False
    assert decisions[0].merged_at is None


def test_each_branch_gets_its_own_decision() -> None:
    """多个分支各自判定，互不影响。"""
    decisions = plan_evidence_prune(
        {1: "evidence/issue-1", 2: "evidence/issue-2"},
        {1: NOW - timedelta(days=90), 2: NOW - timedelta(days=3)},
        older_than_days=30,
        now=NOW,
    )
    assert [decision.eligible for decision in decisions] == [True, False]


def test_merged_at_from_output_reads_merged_pr() -> None:
    """从 gh 输出里取已合并 PR 的合并时间。"""
    payload = '[{"state": "CLOSED", "mergedAt": null}, {"state": "MERGED", "mergedAt": "2026-10-01T10:00:00Z"}]'
    parsed = _merged_at_from_output(payload)
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 10


def test_merged_at_from_output_returns_none_without_merge() -> None:
    """没有已合并 PR、或输出不是 JSON 时返回 None。"""
    assert _merged_at_from_output('[{"state": "OPEN", "mergedAt": null}]') is None
    assert _merged_at_from_output("not json") is None
    assert _merged_at_from_output(None) is None
