"""Post-PR supervisor 的跨 cycle finding 累积（补丁 4）。

从 ``pr_supervisor.py`` 拆出：补丁 4 让整文件超过 1000 非空行硬上限
（CI ``check_max_file_lines.py --max-lines 1000``），而 allowlist 明确禁止新增豁免，
故按职责切成独立模块。这里只放 finding artifact 的读写与 prompt 段渲染。

artifact 落在 worktree 内的 ``.iar/state/issue-<N>/findings.json``（已被 ``.iar/``
gitignore 排除），不污染 GitHub PR 评论流。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig, FindingDetail

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 补丁 4：跨 cycle finding 累积（worktree 内 artifact，不污染 PR 评论流）
# ---------------------------------------------------------------------------

_FINDINGS_ARTIFACT_FILE_NAME = "findings.json"
_FINDINGS_ARTIFACT_VERSION = 1
_OPEN_FINDING_STATUS = "open"
_RESOLVED_FINDING_STATUS = "resolved"


def _findings_artifact_path(worktree_path: Path, config: AppConfig, issue_number: int) -> Path:
    """finding artifact 路径：``<worktree>/<artifact_dir>/issue-<N>/findings.json``。"""
    return (
        worktree_path
        / config.post_pr_supervisor.findings_artifact_dir
        / f"issue-{issue_number}"
        / _FINDINGS_ARTIFACT_FILE_NAME
    )


def _parse_finding_entry(raw_finding: object, default_cycle: int) -> FindingDetail | None:
    """解析一条 finding JSON 记录；缺 title 的条目无法跨 cycle 去重，直接丢弃。"""
    if not isinstance(raw_finding, dict):
        return None
    title = str(raw_finding.get("title") or "").strip()
    if not title:
        return None
    try:
        line_number = int(raw_finding.get("line") or 0)
    except (TypeError, ValueError):
        line_number = 0
    try:
        reported_cycle = int(raw_finding.get("cycle_reported") or 0) or default_cycle
    except (TypeError, ValueError):
        reported_cycle = default_cycle
    return FindingDetail(
        severity=str(raw_finding.get("severity") or "medium"),
        title=title,
        description=str(raw_finding.get("description") or ""),
        file=str(raw_finding.get("file") or ""),
        line=line_number,
        status=str(raw_finding.get("status") or _OPEN_FINDING_STATUS),
        cycle_reported=reported_cycle,
    )


def _load_previous_findings(
    worktree_path: Path, config: AppConfig, issue_number: int
) -> tuple[FindingDetail, ...]:
    """读取历轮仍未解决的 findings。

    artifact 不存在、JSON 损坏或结构不符时一律静默返回空：这是给模型看的
    上下文增强，任何解析问题都不应阻断 supervisor cycle。

    Returns:
        tuple[FindingDetail, ...]: 状态为 ``open`` 的累积 findings。
    """
    artifact_path = _findings_artifact_path(worktree_path, config, issue_number)
    if not artifact_path.is_file():
        return ()
    try:
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as artifact_error:
        _logger.warning(
            "Ignoring unreadable supervisor findings artifact %s: %s",
            artifact_path,
            artifact_error,
        )
        return ()
    raw_findings = artifact_payload.get("findings") if isinstance(artifact_payload, dict) else None
    if not isinstance(raw_findings, list):
        return ()
    parsed_findings = (
        _parse_finding_entry(raw_finding, default_cycle=0) for raw_finding in raw_findings
    )
    return tuple(
        finding
        for finding in parsed_findings
        if finding is not None and finding.status == _OPEN_FINDING_STATUS
    )


def _persist_findings(
    worktree_path: Path,
    config: AppConfig,
    issue_number: int,
    cycle: int,
    current_findings: tuple[FindingDetail, ...],
) -> None:
    """把本 cycle 的 findings 合并进 artifact。

    合并规则（FR-10）：``(file, title)`` 为去重键——status 为 ``resolved`` 的
    finding 从累积列表移除，``open`` 的 finding 首次出现时记下 ``cycle_reported``，
    重复出现只更新内容不新增条目。
    """
    artifact_path = _findings_artifact_path(worktree_path, config, issue_number)
    previous_findings = _load_previous_findings(worktree_path, config, issue_number)
    if not previous_findings and not current_findings and not artifact_path.exists():
        return

    merged_findings: dict[tuple[str, str], FindingDetail] = {
        (finding.file, finding.title): finding for finding in previous_findings
    }
    for finding in current_findings:
        finding_key = (finding.file, finding.title)
        if finding.status == _RESOLVED_FINDING_STATUS:
            merged_findings.pop(finding_key, None)
            continue
        already_reported_finding = merged_findings.get(finding_key)
        merged_findings[finding_key] = replace(
            finding,
            cycle_reported=(
                already_reported_finding.cycle_reported
                if already_reported_finding is not None
                else cycle
            ),
        )

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            {
                "version": _FINDINGS_ARTIFACT_VERSION,
                "issue_number": issue_number,
                "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "findings": [asdict(finding) for finding in merged_findings.values()],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _extract_supervisor_findings(payload: dict[str, object]) -> tuple[FindingDetail, ...]:
    """从 supervisor JSON 输出里抽取可选 ``findings[]``（FR-11，向后兼容）。

    旧模型不输出该字段时返回空；单条记录畸形只丢弃该条，不影响已解析出的
    action——action 的 fail-closed 解析始终先于 findings 抽取。
    """
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return ()
    parsed_findings = (
        _parse_finding_entry(raw_finding, default_cycle=0) for raw_finding in raw_findings
    )
    return tuple(finding for finding in parsed_findings if finding is not None)


def _build_previous_findings_section(
    previous_findings: tuple[FindingDetail, ...],
) -> tuple[str, ...]:
    """渲染 ``Previous unresolved findings`` 段；无累积 finding 时返回空。"""
    if not previous_findings:
        return ()
    reported_cycles = sorted({finding.cycle_reported for finding in previous_findings})
    cycle_range_text = f"{reported_cycles[0]}..{reported_cycles[-1]}"
    finding_lines = [
        f"- [{finding.severity}] {finding.file}:{finding.line} "
        f"{finding.title} (cycle {finding.cycle_reported})"
        for finding in previous_findings
    ]
    return (
        "",
        f"Previous unresolved findings from cycles {cycle_range_text}:",
        *finding_lines,
        "Re-check each of them; a finding stays in this list until it is resolved.",
    )
