"""``kc run`` 的目标解析：PRD 路径 → 回链 GitHub Issue 编号。

``kc run`` 目标必填（PRD：run-daemon-autopilot-control-surface FR-1）。
目标为 PRD 路径时，解析其头部的 ``- GitHub Issue: .../issues/N`` 回链行；
没有回链则报错并提示先 ``kc issue create``，绝不静默回退为"捞队列"。
复用 :mod:`backend.core.use_cases.create_issue_from_prd` 的既有回链正则。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.use_cases.create_issue_from_prd import (
    ISSUE_LINK_LINE_RE,
    ISSUE_NUMBER_RE,
)


class RunTargetResolveError(ValueError):
    """PRD 目标无法解析出回链 Issue。"""


def resolve_prd_target_issue_number(*, repo_path: Path, prd_path: str | Path) -> int:
    """解析 PRD 头部回链的 GitHub Issue 编号。

    Args:
        repo_path: 仓库根路径（相对 PRD 路径基于它解析）。
        prd_path: PRD 文件路径（仓库相对或绝对）。

    Returns:
        回链的 Issue 编号。

    Raises:
        RunTargetResolveError: PRD 文件不存在、回链格式非法，或 PRD 尚无
            Issue 回链（提示先 ``kc issue create``）。
    """
    candidate_path = Path(prd_path)
    absolute_prd_path = (
        candidate_path if candidate_path.is_absolute() else Path(repo_path) / candidate_path
    )
    try:
        prd_text = absolute_prd_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunTargetResolveError(f"Cannot read PRD file {absolute_prd_path}: {exc}") from exc

    for line in prd_text.splitlines():
        if not ISSUE_LINK_LINE_RE.match(line):
            # 占位符（如 "- GitHub Issue: (待创建)"）视为尚未关联 Issue。
            continue
        issue_number_match = ISSUE_NUMBER_RE.search(line)
        if issue_number_match is None:
            raise RunTargetResolveError(f"Invalid GitHub Issue link in PRD: {line}")
        return int(issue_number_match.group("issue_number"))

    raise RunTargetResolveError(
        f"PRD {absolute_prd_path} has no '- GitHub Issue: .../issues/N' link. "
        "Create the Issue first with `kc issue create`."
    )


__all__ = ["RunTargetResolveError", "resolve_prd_target_issue_number"]
