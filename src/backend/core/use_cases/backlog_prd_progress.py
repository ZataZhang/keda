"""PRD 交付进度聚合（只读）：验证计划 / 产出归属 / 报告状态 / 清单 / 影响树 / 工作区。

把「已经存在、但页面看不见」的交付进度接进详情页：详情面板一次拉取一份聚合读数，
头部三读数与证据标签复用同一份数据。所有解析收口在服务端（前端不解析 PRD 原文），
读取口径与命令行看板一致——运行中的读数来自真实执行工作区（主仓库兜底），主仓库
归档副本是终态事实来源，读取失败与「确认无数据」严格分开表达。

弱信号定位，不是验收判定：这里只回答「计划几项 / 产出几项 / 影响树碰了几何」，不解析
验证结论、不做通过率或完成度的强判定。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.prd_checklist import PrdChecklistResult, parse_prd_checklist
from backend.core.use_cases.agent_runner_structured_evidence import (
    ValidationEvidenceError,
    load_evidence_manifest,
)
from backend.core.use_cases.agent_runner_validation_parsing import (
    _iterate_validation_section_lines,
)
from backend.core.use_cases.backlog_prd_evidence import (
    BacklogEvidenceFile,
    BacklogPrdEvidenceError,
    build_evidence_manifest,
    resolve_evidence_dir_for_root,
    resolve_prd_evidence_dir,
)
from backend.core.use_cases.backlog_prd_impact import (
    ImpactMeasureStatus,
    extract_impact_tree_lines,
    measure_branch_impact,
    parse_impact_tree,
)
from backend.core.use_cases.backlog_prd_worktree import (
    WorktreeMatchStatus,
    WorktreeEntry,
    extract_unique_issue_number,
    list_worktrees,
    match_prd_worktree,
    parse_prd_slug,
)
from backend.core.use_cases.prd_content_reader import resolve_prd_content_path

_logger = logging.getLogger(__name__)

#: 计划条目锚：行首（允许缩进）``- id: rv-<n>``。
_PLAN_ITEM_PATTERN = re.compile(r"^\s*-\s+id:\s*rv-(?P<n>\d+)\s*$")
#: 条目内摘要字段（``behavior:`` 优先，退而求其次 ``summary:``）。
_PLAN_BEHAVIOR_PATTERN = re.compile(r"^\s+behavior:\s*(?P<value>.+?)\s*$")
_PLAN_SUMMARY_PATTERN = re.compile(r"^\s+summary:\s*(?P<value>.+?)\s*$")
#: 条目内评审者：仅承认 ``human`` / ``verifier``，其余或缺失记 null。
_PLAN_REVIEWER_PATTERN = re.compile(r"^\s+reviewer:\s*(?P<value>\S.*?)\s*$")
#: 文件名前缀回退归属：``rv-<n>-...`` / ``rv-<n>....``（大小写不敏感）。
_RV_FILE_PREFIX_PATTERN = re.compile(r"^rv-(?P<n>\d+)[-.]", re.IGNORECASE)
#: 摘要展示上限（过长截断，避免撑爆 chip）。
_SUMMARY_MAX_CHARS = 80

_ARCHIVE_BUCKET = "archive"
_PENDING_BUCKET = "pending"
_SOURCE_WORKTREE = "worktree"
_SOURCE_MAIN = "main"

_ATTRIB_MANIFEST = "manifest"
_ATTRIB_FILENAME = "filename"


@dataclass(frozen=True)
class PlannedItem:
    """验证计划（§7.6）里的一个顶层条目。"""

    id: str
    summary: str
    reviewer: str | None


@dataclass(frozen=True)
class ChecklistReading:
    """验收清单勾选读数。"""

    status: str
    checked: int | None
    total: int | None


@dataclass(frozen=True)
class EvidenceReports:
    """报告三件套的「是否已产出」布尔状态；读取不完整时未知项为 None。"""

    verification_plan: bool | None
    evidence_report: bool | None
    verifier_report: bool | None


@dataclass(frozen=True)
class EvidenceReading:
    """证据计划 vs 产出的聚合读数。"""

    planned: list[PlannedItem] | None
    produced_ids: list[str]
    produced_planned_count: int | None
    file_count: int | None
    attribution_source: str
    status: str
    reports: EvidenceReports


@dataclass(frozen=True)
class ImpactReading:
    """影响树触达读数（弱信号）。"""

    touched: int
    judgeable: int
    unresolvable: int


@dataclass(frozen=True)
class WorktreeReading:
    """工作区命中信息（不返回本地路径）。"""

    branch: str | None


@dataclass(frozen=True)
class PrdProgress:
    """详情头部与证据标签共用的聚合读数。"""

    prd_path: str
    prd_stem: str
    prd_source: str | None
    worktree_status: str
    worktree: WorktreeReading | None
    checklist: ChecklistReading
    evidence: EvidenceReading
    impact_status: str
    impact: ImpactReading | None


@dataclass(frozen=True)
class _ResolvedSource:
    """来源解析的内部结果（工作区定位 + PRD 文本 + 证据候选目录）。"""

    prd_stem: str
    prd_source: str | None
    worktree_status: str
    worktree_entry: WorktreeEntry | None
    prd_text: str | None
    source_usable: bool
    archived: bool
    candidates: Sequence[tuple[Path, str]]


def _strip_quotes(value_text: str) -> str:
    """剥掉摘要两端成对的单/双引号。"""
    stripped_value = value_text.strip()
    if (
        len(stripped_value) >= 2
        and stripped_value[0] == stripped_value[-1]
        and stripped_value[0] in {'"', "'"}
    ):
        return stripped_value[1:-1].strip()
    return stripped_value


def _truncate_summary(summary_text: str) -> str:
    """把摘要截断到展示上限，超长时以省略号收尾。"""
    normalized_summary = " ".join(summary_text.split())
    if len(normalized_summary) <= _SUMMARY_MAX_CHARS:
        return normalized_summary
    return normalized_summary[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _extract_plan_block_lines(prd_text: str) -> tuple[str, ...]:
    """截取验证计划小节的第一个围栏代码块内容行。

    小节边界沿用权威解析器 :func:`_iterate_validation_section_lines` 的口径：标题以
    ``Realistic Validation`` 前缀匹配（兼容 ``7.6`` 编号前缀与 ``(Oracle 块)`` 后缀），
    遇到同级或更高级标题才退出，标题与围栏之间的 ``#### rv-N`` 子标题按小节内容收集、
    不会截断解析。
    """
    section_lines = _iterate_validation_section_lines(prd_text)
    block_lines_list: list[str] = []
    is_inside_fence = False
    for raw_line_text in section_lines:
        if raw_line_text.lstrip().startswith("```"):
            if is_inside_fence:
                break
            is_inside_fence = True
            continue
        if is_inside_fence:
            block_lines_list.append(raw_line_text)
    return tuple(block_lines_list)


def parse_validation_plan(prd_text: str) -> list[PlannedItem]:
    """解析 §7.6 验证计划的顶层条目（编号 / 摘要 / 人验机验）。

    成功读取但无 §7.6 或块内无条目时返回空列表（前端据此显示 `-`，不当作「计划 0 项」）。

    Args:
        prd_text: 已确定来源的 PRD 全文。

    Returns:
        计划条目列表（按文档顺序）。
    """
    planned_items: list[PlannedItem] = []
    current_id: str | None = None
    current_summary: str | None = None
    current_reviewer: str | None = None

    def _flush_current() -> None:
        if current_id is not None:
            planned_items.append(
                PlannedItem(
                    id=current_id,
                    summary=_truncate_summary(current_summary or ""),
                    reviewer=current_reviewer,
                )
            )

    for block_line_text in _extract_plan_block_lines(prd_text):
        item_match = _PLAN_ITEM_PATTERN.match(block_line_text)
        if item_match is not None:
            _flush_current()
            current_id = f"rv-{item_match.group('n')}"
            current_summary = None
            current_reviewer = None
            continue
        if current_id is None:
            continue
        if current_summary is None:
            behavior_match = _PLAN_BEHAVIOR_PATTERN.match(block_line_text)
            summary_match = _PLAN_SUMMARY_PATTERN.match(block_line_text)
            candidate_match = behavior_match or summary_match
            if candidate_match is not None:
                current_summary = _strip_quotes(candidate_match.group("value"))
        if current_reviewer is None:
            reviewer_match = _PLAN_REVIEWER_PATTERN.match(block_line_text)
            if reviewer_match is not None:
                reviewer_value = _strip_quotes(reviewer_match.group("value")).lower()
                if reviewer_value in {"human", "verifier"}:
                    current_reviewer = reviewer_value

    _flush_current()
    return planned_items


def _checklist_reading_from_result(checklist_result: PrdChecklistResult) -> ChecklistReading:
    """把清单解析结果折算成勾选读数（无清单区块时 0/0）。"""
    checked_count = len(checklist_result.checked_items)
    total_count = checked_count + len(checklist_result.unchecked_items)
    return ChecklistReading(status="available", checked=checked_count, total=total_count)


def _is_main_repo_archived(prd_path: str) -> bool:
    """主仓库 PRD 是否落在 ``tasks/archive/`` 下（归档即终态）。"""
    path_parts = PurePosixPath(prd_path).parts
    return len(path_parts) >= 2 and path_parts[1] == _ARCHIVE_BUCKET


def _read_text_if_present(prd_abs_path: Path) -> tuple[str | None, str]:
    """读取一份可能不存在的 PRD 副本，区分 found / missing / error。"""
    if not prd_abs_path.exists():
        return None, "missing"
    try:
        return prd_abs_path.read_text(encoding="utf-8"), "found"
    except (OSError, UnicodeDecodeError):
        return None, "error"


def _resolve_main_prd_text(repo_path: Path, prd_path: str) -> tuple[str | None, bool]:
    """读取主仓库 PRD 原文；返回 ``(文本或 None, 读取错误)``。

    文件不存在不视作错误（pending PRD 一定在主仓库存在，归档同理）；权限/解码失败
    才是错误，调用方据此标不可用而不是伪装成空读数。
    """
    try:
        resolved_path = resolve_prd_content_path(repo_path, prd_path)
    except Exception as exc:  # noqa: BLE001 - 非法/缺失路径交由调用方（路由）先 400；此处保守
        _logger.debug("PRD 路径解析失败: %s", exc)
        return None, False
    if not resolved_path.is_file():
        return None, False
    try:
        return resolved_path.read_text(encoding="utf-8"), False
    except (OSError, UnicodeDecodeError):
        return None, True


def _worktree_prd_candidates(
    worktree_entry: WorktreeEntry, prd_file_name: str
) -> tuple[Path, Path]:
    """给出工作区内归档优先、pending 兜底的两个 PRD 副本路径。"""
    tasks_dir = worktree_entry.path / "tasks"
    return (
        tasks_dir / _ARCHIVE_BUCKET / prd_file_name,
        tasks_dir / _PENDING_BUCKET / prd_file_name,
    )


def resolve_prd_source(
    *,
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    process_runner: IProcessRunner,
) -> _ResolvedSource:
    """确定 PRD 文本来源、工作区命中与证据候选目录（分支优先、主仓库兜底）。

    归档终态规则：主仓库已在 ``tasks/archive`` 时直接读主仓库、跳过工作区匹配、证据只用
    主仓库目录（即便残留工作区副本）。pending 时按 slug / ``issue-<N>`` 匹配工作区，唯一命中
    读工作区副本（archive 优先、pending 兜底，两处皆无再回落主仓库）；发现失败或多重匹配
    时不以主仓库冒充最终来源，来源相关读数标不可用。
    """
    prd_stem = PurePosixPath(prd_path).stem
    prd_file_name = PurePosixPath(prd_path).name

    if _is_main_repo_archived(prd_path):
        archived_text, _archived_error = _resolve_main_prd_text(repo_path, prd_path)
        main_dir = resolve_prd_evidence_dir(repo_path, config, prd_path)[0]
        return _ResolvedSource(
            prd_stem=prd_stem,
            prd_source=_SOURCE_MAIN,
            worktree_status=WorktreeMatchStatus.NOT_APPLICABLE.value,
            worktree_entry=None,
            prd_text=archived_text,
            source_usable=True,
            archived=True,
            candidates=[(main_dir, _SOURCE_MAIN)],
        )

    slug_text = parse_prd_slug(prd_file_name)
    # Issue 锚点只用于匹配兜底；从主仓库 pending 副本取（列表来源，必然存在），稳定可靠。
    main_text, _main_read_error = _resolve_main_prd_text(repo_path, prd_path)
    issue_number = extract_unique_issue_number(main_text) if main_text is not None else None

    worktree_list = list_worktrees(process_runner, repo_path)
    match_result = match_prd_worktree(
        worktree_list.entries,
        slug=slug_text,
        issue_number=issue_number,
        query_available=worktree_list.available,
    )
    main_dir = resolve_prd_evidence_dir(repo_path, config, prd_path)[0]

    if match_result.status in {WorktreeMatchStatus.UNAVAILABLE, WorktreeMatchStatus.AMBIGUOUS}:
        # 不以主仓库内容冒充最终来源：来源相关读数标不可用，证据候选为空。
        return _ResolvedSource(
            prd_stem=prd_stem,
            prd_source=None,
            worktree_status=match_result.status.value,
            worktree_entry=None,
            prd_text=None,
            source_usable=False,
            archived=False,
            candidates=[],
        )

    if match_result.status is WorktreeMatchStatus.NONE:
        pending_text, pending_error = _resolve_main_prd_text(repo_path, prd_path)
        return _ResolvedSource(
            prd_stem=prd_stem,
            prd_source=_SOURCE_MAIN,
            worktree_status=WorktreeMatchStatus.NONE.value,
            worktree_entry=None,
            prd_text=None if pending_error else pending_text,
            source_usable=True,
            archived=False,
            candidates=[(main_dir, _SOURCE_MAIN)],
        )

    worktree_entry = match_result.entry
    assert worktree_entry is not None  # matched 时必有条目
    archive_copy, pending_copy = _worktree_prd_candidates(worktree_entry, prd_file_name)
    worktree_text, worktree_read_state = _read_text_if_present(archive_copy)
    if worktree_read_state == "missing":
        worktree_text, worktree_read_state = _read_text_if_present(pending_copy)

    if worktree_read_state == "found" and worktree_text is not None:
        prd_source = _SOURCE_WORKTREE
        resolved_text: str | None = worktree_text
    elif worktree_read_state == "missing":
        # 工作区两处都没有副本：回落到主仓库 pending 副本（仅「确实不存在」才回落）。
        pending_text, pending_error = _resolve_main_prd_text(repo_path, prd_path)
        prd_source = _SOURCE_MAIN
        resolved_text = None if pending_error else pending_text
    else:
        # 命中工作区但读取失败：不得回退到看似空的主仓库内容。
        prd_source = None
        resolved_text = None

    candidate_dirs: list[tuple[Path, str]] = []
    try:
        worktree_dir = resolve_evidence_dir_for_root(worktree_entry.path, config, prd_stem)
        candidate_dirs.append((worktree_dir, _SOURCE_WORKTREE))
    except BacklogPrdEvidenceError:
        _logger.warning(
            "工作区证据目录 containment 校验失败，回落主仓库目录: %s", worktree_entry.path
        )
    candidate_dirs.append((main_dir, _SOURCE_MAIN))

    return _ResolvedSource(
        prd_stem=prd_stem,
        prd_source=prd_source,
        worktree_status=WorktreeMatchStatus.MATCHED.value,
        worktree_entry=worktree_entry,
        prd_text=resolved_text,
        source_usable=True,
        archived=False,
        candidates=candidate_dirs,
    )


def resolve_evidence_lookup(
    *,
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    process_runner: IProcessRunner,
) -> tuple[list[tuple[Path, str]], str]:
    """为证据端点解析「候选目录 + 读取状态」（分支优先、主仓库兜底）。

    与 :func:`build_prd_progress` 共用同一套来源解析，保证 manifest、artifact 与 progress
    三条链路对「读哪个目录」口径一致。工作区发现失败或多重匹配时候选为空、状态记
    ``unavailable``，调用方据此区分「无法确定来源」与「确认没有文件」。

    Args:
        repo_path: 主仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径（路由层已解码，白名单校验在 core 内完成）。
        process_runner: 只读命令端口（worktree 检索）。

    Returns:
        ``(候选证据目录序列, status)``；status 为 ``complete`` 或 ``unavailable``。

    Raises:
        BacklogPrdEvidenceError: PRD 路径非法或证据目录越界（由路由映射 400）。
    """
    source = resolve_prd_source(
        repo_path=repo_path, config=config, prd_path=prd_path, process_runner=process_runner
    )
    if source.worktree_status in {
        WorktreeMatchStatus.UNAVAILABLE.value,
        WorktreeMatchStatus.AMBIGUOUS.value,
    }:
        return [], "unavailable"
    return list(source.candidates), "complete"


def _build_evidence_reading(
    *,
    source: _ResolvedSource,
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    planned: list[PlannedItem] | None,
) -> EvidenceReading:
    """组装产出归属读数：合并候选文件、清单映射优先 / 前缀回退、报告状态。

    目录枚举失败（无权限等）时清单状态为 ``unavailable``：``file_count`` 与
    ``produced_planned_count`` 保持 ``null``（未知），不折算成零；成功读取但确实没有
    文件时才返回 ``0``。
    """
    if not source.source_usable:
        return EvidenceReading(
            planned=None,
            produced_ids=[],
            produced_planned_count=None,
            file_count=None,
            attribution_source="none",
            status="unavailable",
            reports=EvidenceReports(None, None, None),
        )

    try:
        manifest = build_evidence_manifest(
            repo_path=repo_path, config=config, prd_path=prd_path, candidates=source.candidates
        )
        evidence_files: list[BacklogEvidenceFile] = manifest.files
    except BacklogPrdEvidenceError as exc:
        _logger.warning("证据 manifest 读取失败: %s", exc)
        return EvidenceReading(
            planned=planned,
            produced_ids=[],
            produced_planned_count=None,
            file_count=None,
            attribution_source="none",
            status="unavailable",
            reports=EvidenceReports(None, None, None),
        )

    # 逐候选目录加载结构化清单，工作区在前先到先得，构成 文件名 → 验证编号 映射。
    # load_evidence_manifest 只用第一个参数拼展示路径，读文件走 evidence_dir 本身，
    # 因此对 worktree 目录同样传主仓库根即可，不影响解析正确性。
    declared_number_by_name: dict[str, int] = {}
    for candidate_dir, _source_tag in source.candidates:
        try:
            candidate_manifest = load_evidence_manifest(
                repo_path, config, evidence_dir=candidate_dir
            )
        except ValidationEvidenceError:
            # 清单缺失或损坏：该目录的文件走文件名前缀回退（既有语义）。
            candidate_manifest = None
        except OSError as exc:
            # 清单本身读不动（无权限等）：同样回退前缀，不使端点整体失败。
            _logger.warning("结构化证据清单读取失败，按前缀回退: %s: %s", candidate_dir, exc)
            candidate_manifest = None
        if candidate_manifest is None:
            continue
        for block in candidate_manifest.items:
            for file_name in block.evidence_files:
                declared_number_by_name.setdefault(file_name, block.item_number)

    attributed_sources: set[str] = set()
    produced_numbers: set[int] = set()
    for evidence_file in evidence_files:
        file_number = declared_number_by_name.get(evidence_file.name)
        if file_number is not None:
            attributed_sources.add(_ATTRIB_MANIFEST)
        else:
            prefix_match = _RV_FILE_PREFIX_PATTERN.match(evidence_file.name)
            if prefix_match is not None:
                file_number = int(prefix_match.group("n"))
                attributed_sources.add(_ATTRIB_FILENAME)
        if file_number is not None:
            produced_numbers.add(file_number)

    produced_ids = [f"rv-{number}" for number in sorted(produced_numbers)]
    planned_ids = {item.id for item in planned} if planned is not None else set()
    produced_planned_count = (
        len({f"rv-{number}" for number in produced_numbers} & planned_ids)
        if planned is not None
        else None
    )

    if not attributed_sources:
        attribution_source = "none"
    elif attributed_sources == {_ATTRIB_MANIFEST}:
        attribution_source = _ATTRIB_MANIFEST
    elif attributed_sources == {_ATTRIB_FILENAME}:
        attribution_source = _ATTRIB_FILENAME
    else:
        attribution_source = "mixed"

    report_flags = _report_flags(evidence_files)
    read_complete = manifest.status == "complete"
    if not read_complete:
        # 目录枚举失败：文件集合只是可读取到的部分，计数与报告状态未知必须为 null，
        # 不能把不完整读取伪装成「已产出 0 项」。
        return EvidenceReading(
            planned=planned,
            produced_ids=produced_ids,
            produced_planned_count=None,
            file_count=None,
            attribution_source=attribution_source,
            status="unavailable",
            reports=EvidenceReports(None, None, None),
        )
    return EvidenceReading(
        planned=planned,
        produced_ids=produced_ids,
        produced_planned_count=produced_planned_count,
        file_count=len(evidence_files),
        attribution_source=attribution_source,
        status="complete",
        reports=EvidenceReports(
            verification_plan=report_flags["verification_plan"],
            evidence_report=report_flags["evidence_report"],
            verifier_report=report_flags["verifier_report"],
        ),
    )


def _report_flags(evidence_files: list[BacklogEvidenceFile]) -> dict[str, bool]:
    """按文件角色判定报告三件套是否已产出。"""
    present_roles = {evidence_file.role for evidence_file in evidence_files}
    return {
        "verification_plan": "verification_plan" in present_roles,
        "evidence_report": "evidence_report" in present_roles,
        "verifier_report": "verifier_report" in present_roles,
    }


def _build_impact_reading(
    *,
    source: _ResolvedSource,
    process_runner: IProcessRunner,
) -> tuple[str, ImpactReading | None]:
    """按归档终态与工作区命中情况给出影响树读数与状态（弱信号）。"""
    if source.archived:
        return "terminal_archive", None
    if not source.source_usable:
        return "unavailable", None
    if source.worktree_status == WorktreeMatchStatus.NONE.value:
        return "no_worktree", None
    if source.worktree_status != WorktreeMatchStatus.MATCHED.value:
        return "unavailable", None
    if source.prd_text is None:
        return "unavailable", None

    impact_nodes = parse_impact_tree(source.prd_text)
    if not impact_nodes:
        if not extract_impact_tree_lines(source.prd_text):
            return "no_tree", None
        return "no_nodes", None

    measure_status, progress = measure_branch_impact(
        process_runner,
        source.worktree_entry.path,
        impact_nodes,  # type: ignore[union-attr]
    )
    if measure_status is ImpactMeasureStatus.NO_BASE:
        return "no_base", None
    if measure_status is ImpactMeasureStatus.UNAVAILABLE:
        return "unavailable", None
    if progress is None:
        return "unavailable", None
    return (
        "available",
        ImpactReading(
            touched=progress.touched_total,
            judgeable=progress.judgeable_total,
            unresolvable=progress.unresolvable_total,
        ),
    )


def _build_checklist_reading(source: _ResolvedSource) -> ChecklistReading:
    """从已确定来源的文本解析清单读数；文本不可用时报 unavailable。"""
    if source.prd_text is None:
        return ChecklistReading(status="unavailable", checked=None, total=None)
    try:
        checklist_result = parse_prd_checklist(source.prd_text)
    except Exception as exc:  # noqa: BLE001 - 契约解析器不可用时不伪造读数
        _logger.warning("清单解析失败: %s", exc)
        return ChecklistReading(status="unavailable", checked=None, total=None)
    return _checklist_reading_from_result(checklist_result)


def build_prd_progress(
    *,
    repo_path: Path,
    config: AppConfig,
    prd_path: str,
    process_runner: IProcessRunner,
) -> PrdProgress:
    """聚合单个 PRD 的交付进度读数（每次请求 fresh 读盘，不复用缓存）。

    Args:
        repo_path: 主仓库根目录。
        config: 生效配置。
        prd_path: PRD 的仓库相对路径（已在路由层通过白名单校验）。
        process_runner: 只读命令端口（worktree / 影响树 git 调用）。

    Returns:
        :class:`PrdProgress`：清单 / 证据计划与产出 / 影响树 / 工作区的聚合读数。

    Raises:
        backend.core.use_cases.prd_content_reader.PrdContentError: PRD 路径非法或不存在。
    """
    resolve_prd_content_path(repo_path, prd_path)
    source = resolve_prd_source(
        repo_path=repo_path, config=config, prd_path=prd_path, process_runner=process_runner
    )

    planned: list[PlannedItem] | None = (
        parse_validation_plan(source.prd_text) if source.prd_text is not None else None
    )
    checklist = _build_checklist_reading(source)
    evidence = _build_evidence_reading(
        source=source, repo_path=repo_path, config=config, prd_path=prd_path, planned=planned
    )
    impact_status, impact = _build_impact_reading(source=source, process_runner=process_runner)

    worktree_reading: WorktreeReading | None = None
    if source.worktree_entry is not None:
        worktree_reading = WorktreeReading(branch=source.worktree_entry.branch)

    return PrdProgress(
        prd_path=prd_path,
        prd_stem=source.prd_stem,
        prd_source=source.prd_source,
        worktree_status=source.worktree_status,
        worktree=worktree_reading,
        checklist=checklist,
        evidence=evidence,
        impact_status=impact_status,
        impact=impact,
    )
