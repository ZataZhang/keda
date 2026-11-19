"""证据分支的保留期清理：算出哪些可以删，删除动作留给调用方。

清理只信 PR 的 mergedAt：合并后超过保留期才可删。PR 未合并、查不到、或
mergedAt 缺失一律保留——宁可多留几个分支，也不误删还在评审或没合并的证据。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CalledProcessError, run
from typing import Mapping

#: GitHub 远端地址里的 owner/repo 部分。
_GITHUB_URL_PATTERN = re.compile(
    r"github\.com(?::\d+)?[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE
)

#: 保留期写法：30、30d、1w。
_RETENTION_PATTERN = re.compile(r"^(\d+)([dw])?$", re.IGNORECASE)


@dataclass(frozen=True)
class EvidencePruneDecision:
    """一个证据分支的清理判定。

    Attributes:
        branch: 分支名。
        issue_number: 该分支对应的 Issue 编号。
        merged_at: 关联 PR 的合并时间；未合并或查不到时为 None。
        age_days: 距合并的天数；无法判定时为 None。
        eligible: 是否可删。
        reason: 判定原因，直接进人读输出。
    """

    branch: str
    issue_number: int
    merged_at: datetime | None
    age_days: float | None
    eligible: bool
    reason: str


def parse_github_slug(remote_url: str) -> str | None:
    """从远端地址解析 owner/repo；不是 GitHub 地址时返回 None。

    Args:
        remote_url: git remote -v 里看到的地址。

    Returns:
        str | None: owner/repo，例如 ZataZhang/keda。
    """
    match = _GITHUB_URL_PATTERN.search(remote_url.strip())
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def parse_retention_days(text: str) -> int:
    """把 30 / 30d / 2w 解析成天数。

    Args:
        text: 保留期写法。

    Returns:
        int: 天数。

    Raises:
        ValueError: 写法非法或小于 1 天。
    """
    match = _RETENTION_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(f"Unsupported retention value: {text!r} (use 30, 30d or 2w).")
    amount = int(match.group(1))
    unit = (match.group(2) or "d").lower()
    days = amount * 7 if unit == "w" else amount
    if days < 1:
        raise ValueError("Retention must be at least one day.")
    return days


def plan_evidence_prune(
    branches: Mapping[int, str],
    merged_at_by_issue: Mapping[int, datetime | None],
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[EvidencePruneDecision]:
    """按合并时间算出每个证据分支能否清理。

    Args:
        branches: Issue 编号到证据分支名的映射。
        merged_at_by_issue: Issue 编号到关联 PR 合并时间的映射；未合并为 None。
        older_than_days: 合并后需要保留的天数。
        now: 参考时间，注入以便单测确定性。

    Returns:
        list[EvidencePruneDecision]: 每个分支一条判定。
    """
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decisions: list[EvidencePruneDecision] = []
    for issue_number, branch in sorted(branches.items()):
        merged_at = merged_at_by_issue.get(issue_number)
        if merged_at is None:
            decisions.append(
                EvidencePruneDecision(
                    branch=branch,
                    issue_number=issue_number,
                    merged_at=None,
                    age_days=None,
                    eligible=False,
                    reason="未找到已合并的 PR，保留（评审中或未合并）",
                )
            )
            continue
        merged_utc = merged_at.astimezone(timezone.utc)
        age_days = (reference - merged_utc).total_seconds() / 86400
        if age_days < older_than_days:
            decisions.append(
                EvidencePruneDecision(
                    branch=branch,
                    issue_number=issue_number,
                    merged_at=merged_utc,
                    age_days=age_days,
                    eligible=False,
                    reason=f"仍在保留期内（合并 {age_days:.0f} 天，保留 {older_than_days} 天）",
                )
            )
            continue
        decisions.append(
            EvidencePruneDecision(
                branch=branch,
                issue_number=issue_number,
                merged_at=merged_utc,
                age_days=age_days,
                eligible=True,
                reason=f"PR 已合并 {age_days:.0f} 天，超过保留期 {older_than_days} 天",
            )
        )
    return decisions


def _run(command: list[str], timeout_seconds: int = 20) -> str | None:
    """执行外部命令并返回标准输出；失败或超时返回 None。"""
    try:
        completed = run(command, capture_output=True, timeout=timeout_seconds, check=True)
    except (CalledProcessError, OSError):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def find_github_remote(repo_path: Path) -> tuple[str, str] | None:
    """返回仓库指向 GitHub 的远端名与 owner/repo；没有时返回 None。

    Args:
        repo_path: 仓库根目录。

    Returns:
        tuple[str, str] | None: (远端名, owner/repo)。
    """
    output = _run(["git", "-C", str(repo_path), "remote", "-v"])
    if output is None:
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        slug = parse_github_slug(parts[1])
        if slug is not None:
            return parts[0], slug
    return None


def read_merge_dates(slug: str, branches: Mapping[int, str]) -> dict[int, datetime | None]:
    """查询每个证据分支对应 PR 的合并时间。

    用 gh 逐个分支查已合并的 PR；查不到、命令失败或时间解析失败都记为 None，
    由清理判定按「保留」处理。

    Args:
        slug: owner/repo。
        branches: Issue 编号到分支名的映射。

    Returns:
        dict[int, datetime | None]: Issue 编号到合并时间的映射。
    """
    dates: dict[int, datetime | None] = {}
    for issue_number, branch in branches.items():
        output = _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                slug,
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "state,mergedAt",
                "--limit",
                "5",
            ]
        )
        dates[issue_number] = _merged_at_from_output(output)
    return dates


def _merged_at_from_output(output: str | None) -> datetime | None:
    """从 gh pr list 的 JSON 输出里取最早的已合并时间。"""
    if not output:
        return None
    try:
        entries = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("state") != "MERGED":
            continue
        merged_at = entry.get("mergedAt")
        if not isinstance(merged_at, str):
            continue
        try:
            return datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def delete_remote_branch(repo_path: Path, remote: str, branch: str) -> bool:
    """删除远端证据分支；成功返回 True。

    Args:
        repo_path: 仓库根目录。
        remote: 远端名。
        branch: 分支名。

    Returns:
        bool: 是否删除成功。
    """
    return _run(["git", "-C", str(repo_path), "push", remote, "--delete", branch], 60) is not None


__all__ = [
    "EvidencePruneDecision",
    "delete_remote_branch",
    "find_github_remote",
    "parse_github_slug",
    "parse_retention_days",
    "plan_evidence_prune",
    "read_merge_dates",
]
