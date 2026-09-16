"""交付收尾层（Closeout Agent）的 prompt、快照、越界判定与差异计算。

修复阶梯的第三层。前两层是 Fix Agent（提交前验证失败）与完整 Recovery Agent；
本层只接住"代码已经正确、只差交付收尾动作"的那一桶门禁失败：验收清单未勾、
PRD 改了没写 Change Log、证据清单字段格式非法、前端改了没截图。

本层的两条硬约束**都不靠 prompt 保证**：

- 不许改代码 —— 由 :func:`find_closeout_scope_violations` 比对收尾前后的
  worktree 改动快照强制，越界即判本次收尾失败并升级为整轮重跑。
- 留痕不采信 agent 自述 —— "勾了哪几项 / 追加了哪条 Change Log / 新增了哪些
  证据文件"由 :func:`summarize_closeout_changes` 从收尾前后的 PRD 文本与证据
  目录清单算出。agent 可以在自述里说谎，前后文本差异不会。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    DeliveryGateFailureKind,
    IssueSummary,
)
from backend.core.shared.prd_change_log import parse_prd_change_log
from backend.core.shared.prd_checklist import CHECKBOX_RE, parse_prd_checklist
from backend.core.shared.prd_machine_contract import PRD_MACHINE_CONTRACT_POINTER
from backend.core.use_cases.agent_runner_feedback import (
    PRD_ARCHIVE_OWNERSHIP_RULE,
    RUNNER_OWNED_CHECKLIST_ITEM_RULE,
    extract_prd_path,
    resolve_prd_archive_path,
)
from backend.core.use_cases.agent_runner_git import (
    expand_changed_path,
    list_changed_paths,
)
from backend.core.use_cases.agent_runner_validation import list_evidence_files
from backend.core.use_cases.run_agent_once import run_agent_with_prompt_resilient

_logger = logging.getLogger(__name__)

# 被删除（或从未存在）的路径在快照里用这个哨兵占位，好让"文件消失"也算一次改动。
_MISSING_PATH_DIGEST = "<absent>"


@dataclass(frozen=True)
class CloseoutAllowedScope:
    """收尾 pass 唯一被允许改动的路径集合。

    Attributes:
        prd_paths: canonical PRD 路径及其归档目标路径（仓库相对，POSIX 分隔符）。
        evidence_dir: 证据目录（仓库相对），其下任意路径都被允许。
    """

    prd_paths: tuple[str, ...]
    evidence_dir: str

    def allows(self, repo_relative_path: str) -> bool:
        """判断一个仓库相对路径是否落在允许集内。"""
        normalized_path = repo_relative_path.strip("/")
        if normalized_path in self.prd_paths:
            return True
        evidence_prefix = self.evidence_dir.strip("/") + "/"
        return normalized_path.startswith(evidence_prefix)


@dataclass(frozen=True)
class CloseoutSnapshot:
    """收尾前后各取一次的确定性 worktree 快照。

    Attributes:
        prd_text: canonical PRD 的全文；文件不存在时为空串。
        evidence_file_names: 证据目录第一层的证据文件名，已排序。
        changed_path_digests: worktree 改动路径（含未跟踪）到内容摘要的映射。
            比对的是**摘要**而不是路径集合：实现 agent 的改动此刻尚未提交，
            改动路径集合里本来就有 ``src/`` 文件，只比集合的话收尾 pass 再改一次
            同一个文件不会引起任何差异。
    """

    prd_text: str
    evidence_file_names: tuple[str, ...]
    changed_path_digests: dict[str, str]


@dataclass(frozen=True)
class CloseoutChangeSummary:
    """runner 自行比对得出的收尾改动摘要（不采信 agent 自述）。

    Attributes:
        checked_items: 收尾前未勾、收尾后已勾的验收条目文本。
        resolved_items: 收尾前未勾、收尾后既不在未勾也不在已勾集合里的条目——
            被改写成 ``[~]`` 或被整条删掉。单列出来，删除才无法伪装成勾选。
        new_change_log_entries: 本轮新增的 Change Log 条目标题。
        added_evidence_files: 本轮新增的证据文件名。
    """

    checked_items: tuple[str, ...]
    resolved_items: tuple[str, ...]
    new_change_log_entries: tuple[str, ...]
    added_evidence_files: tuple[str, ...]


@dataclass(frozen=True)
class CloseoutPromptContext:
    """构造收尾 prompt 与调用收尾 agent 所需的全部上下文。

    Attributes:
        issue: 当前处理的 Issue。
        worktree_path: agent 工作的 worktree 路径。
        gate_failure_message: 触发本次收尾的门禁失败原文。
        kind: 该门禁失败的分类，决定 prompt 侧重点与超时预算。
        allowed_scope: 收尾 pass 被允许改动的路径集合。
    """

    issue: IssueSummary
    worktree_path: Path
    gate_failure_message: str
    kind: DeliveryGateFailureKind
    allowed_scope: CloseoutAllowedScope


def build_closeout_allowed_scope(issue: IssueSummary, config: AppConfig) -> CloseoutAllowedScope:
    """解析本次收尾允许改动的路径集合。

    Args:
        issue: 当前 Issue，其 body 指向 canonical PRD。
        config: 提供证据目录位置。

    Returns:
        允许集；Issue 未关联 PRD 时只剩证据目录。
    """
    prd_relative_path = extract_prd_path(issue.body)
    if prd_relative_path is None:
        return CloseoutAllowedScope(
            prd_paths=(),
            evidence_dir=config.validation.evidence_dir,
        )
    archive_relative_path = resolve_prd_archive_path(prd_relative_path)
    prd_paths = [prd_relative_path]
    if archive_relative_path is not None:
        prd_paths.append(archive_relative_path)
    return CloseoutAllowedScope(
        prd_paths=tuple(prd_paths),
        evidence_dir=config.validation.evidence_dir,
    )


def resolve_prd_worktree_path(issue: IssueSummary, worktree_path: Path) -> Path | None:
    """返回 canonical PRD 在 worktree 里的实际位置。

    Issue body 记录的永远是归档前的 ``tasks/pending/`` 路径，而 runner 可能已在
    上一次门禁通过时把它 ``git mv`` 到了 ``tasks/archive/``；两个位置都要认。

    Args:
        issue: 当前 Issue。
        worktree_path: worktree 根目录。

    Returns:
        PRD 文件的绝对路径；Issue 未关联 PRD 或文件不存在时返回 ``None``。
    """
    prd_relative_path = extract_prd_path(issue.body)
    if prd_relative_path is None:
        return None
    pending_path = worktree_path / prd_relative_path
    if pending_path.is_file():
        return pending_path
    archive_relative_path = resolve_prd_archive_path(prd_relative_path)
    if archive_relative_path is None:
        return None
    archive_path = worktree_path / archive_relative_path
    return archive_path if archive_path.is_file() else None


def restore_prd_from_snapshot(
    issue: IssueSummary,
    worktree_path: Path,
    snapshot: CloseoutSnapshot,
) -> bool:
    """收尾失败时把 canonical PRD 还原到收尾前的文本。

    验收清单是这几道门禁里**唯一没有独立验证源**的一道：收尾 pass 勾上一个
    举不出证据的条目，门禁重跑未必抓得到（它只问"还有没有未勾项"）。因此收尾
    一旦判失败，它对 PRD 的任何编辑都必须原样撤销——否则那个凭空的勾会留在
    worktree 里，被随后的完整重跑照单全收，"举证后才可勾选"就成了空话。

    还原用的是收尾前的快照文本而不是 ``git checkout``，因此实现阶段对 PRD 的
    合法修改被完整保留；只有收尾 pass 这一段编辑被抹掉。证据目录不还原：证据
    文件不进代码 diff，且证据门禁本就会独立重判。越界写入的**其他**文件同样不
    还原，按设计交给随后的完整重跑重新处理。

    Args:
        issue: 当前 Issue。
        worktree_path: worktree 根目录。
        snapshot: 收尾前采集的快照。

    Returns:
        实际发生还原时为 ``True``；PRD 不存在或内容未变时为 ``False``。
    """
    prd_path = resolve_prd_worktree_path(issue, worktree_path)
    if prd_path is None or not snapshot.prd_text:
        return False
    if prd_path.read_text(encoding="utf-8") == snapshot.prd_text:
        return False
    prd_path.write_text(snapshot.prd_text, encoding="utf-8")
    return True


def _digest_worktree_file(file_path: Path) -> str:
    """返回文件内容摘要；文件不存在或不可读时返回缺失哨兵。"""
    try:
        return hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]
    except OSError:
        return _MISSING_PATH_DIGEST


def capture_closeout_snapshot(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> CloseoutSnapshot:
    """采集一次收尾快照：PRD 全文、证据文件清单、改动路径内容摘要。

    Args:
        issue: 当前 Issue。
        worktree_path: worktree 根目录。
        config: 提供证据目录位置。
        process_runner: 用于读取 worktree 改动的进程执行器。

    Returns:
        供越界判定与留痕差异计算使用的 :class:`CloseoutSnapshot`。
    """
    prd_path = resolve_prd_worktree_path(issue, worktree_path)
    prd_text = prd_path.read_text(encoding="utf-8") if prd_path is not None else ""
    evidence_file_names = tuple(
        evidence_path.name for evidence_path in list_evidence_files(worktree_path, config, issue)
    )
    changed_path_digests: dict[str, str] = {}
    for changed_path in list_changed_paths(worktree_path, process_runner):
        for expanded_path in expand_changed_path(worktree_path, changed_path):
            changed_path_digests[expanded_path] = _digest_worktree_file(
                worktree_path / expanded_path
            )
    return CloseoutSnapshot(
        prd_text=prd_text,
        evidence_file_names=evidence_file_names,
        changed_path_digests=changed_path_digests,
    )


def find_closeout_scope_violations(
    before: CloseoutSnapshot,
    after: CloseoutSnapshot,
    allowed_scope: CloseoutAllowedScope,
) -> tuple[str, ...]:
    """返回收尾 pass 改动过、但不在允许集内的仓库相对路径。

    "改动过"= 收尾前后内容摘要不同（含新增与删除），而不是"出现在改动路径集合
    里"——实现 agent 的改动此刻仍未提交，路径集合里本来就有源码。

    Args:
        before: 收尾前快照。
        after: 收尾后快照。
        allowed_scope: 允许改动的路径集合。

    Returns:
        越界路径，已排序；无越界时为空元组。
    """
    all_paths = set(before.changed_path_digests) | set(after.changed_path_digests)
    touched_paths = [
        path
        for path in all_paths
        if before.changed_path_digests.get(path, _MISSING_PATH_DIGEST)
        != after.changed_path_digests.get(path, _MISSING_PATH_DIGEST)
    ]
    return tuple(sorted(path for path in touched_paths if not allowed_scope.allows(path)))


def _checklist_label(checklist_line: str) -> str:
    """把一行验收清单条目归一为不含复选框前缀的标签文本。"""
    checkbox_match = CHECKBOX_RE.match(checklist_line)
    if checkbox_match is None:
        return checklist_line.strip()
    return checkbox_match.group("label").strip()


def summarize_closeout_changes(
    before: CloseoutSnapshot,
    after: CloseoutSnapshot,
) -> CloseoutChangeSummary:
    """由收尾前后的快照算出本轮真实发生的收尾改动。

    Args:
        before: 收尾前快照。
        after: 收尾后快照。

    Returns:
        勾选差异、新增 Change Log 条目与新增证据文件。
    """
    before_checklist = parse_prd_checklist(before.prd_text)
    after_checklist = parse_prd_checklist(after.prd_text)
    unchecked_before = [_checklist_label(text) for _, text in before_checklist.unchecked_items]
    unchecked_after = {_checklist_label(text) for _, text in after_checklist.unchecked_items}
    checked_after = {_checklist_label(text) for _, text in after_checklist.checked_items}

    checked_items = [label for label in unchecked_before if label in checked_after]
    resolved_items = [
        label
        for label in unchecked_before
        if label not in checked_after and label not in unchecked_after
    ]

    before_entry_titles = parse_prd_change_log(before.prd_text).entry_titles
    after_entry_titles = parse_prd_change_log(after.prd_text).entry_titles
    new_change_log_entries = list(after_entry_titles[len(before_entry_titles) :])

    before_evidence_names = set(before.evidence_file_names)
    added_evidence_files = [
        file_name
        for file_name in after.evidence_file_names
        if file_name not in before_evidence_names
    ]

    return CloseoutChangeSummary(
        checked_items=tuple(checked_items),
        resolved_items=tuple(resolved_items),
        new_change_log_entries=tuple(new_change_log_entries),
        added_evidence_files=tuple(added_evidence_files),
    )


def _render_summary_section(section_title: str, entries: tuple[str, ...]) -> list[str]:
    """渲染留痕里的一节；无内容时返回一行 ``(none)``。"""
    if not entries:
        return [f"{section_title}: (none)"]
    return [f"{section_title}:", *(f"- {entry}" for entry in entries)]


def _render_summary_rollup(noun: str, entries: tuple[str, ...]) -> str:
    """把一类差异压成"数量 + 具体名字"的一小段，供汇总行拼接。

    汇总行是 attempt 历史表 Detail 列唯一会显示的内容，所以必须点名——只报数字
    的话，读者在 Issue 上看不到"到底勾了哪一项"，得去翻运行历史库。
    """
    if not entries:
        return f"0 {noun}"
    return f"{len(entries)} {noun}: " + " / ".join(entries)


def format_closeout_attempt_detail(summary: CloseoutChangeSummary) -> str:
    """把收尾差异渲染成写入 attempt 历史与 Issue 评论的 detail 文本。

    最后一行刻意是一句自足的滚动汇总：attempt 历史表的 Detail 列只保留 detail 的
    最后一条有效行（``_summarize_attempt_detail``），逐节明细排在最后会让表格里
    只剩"Evidence files added: (none)"这种最没有信息量的一行。

    Args:
        summary: :func:`summarize_closeout_changes` 的结果。

    Returns:
        多行 detail 文本；内容全部来自 runner 的确定性比对。
    """
    detail_lines = ["Delivery closeout succeeded (diff computed by the runner, not self-reported)."]
    detail_lines.extend(
        _render_summary_section("Acceptance Checklist items ticked", summary.checked_items)
    )
    detail_lines.extend(
        _render_summary_section(
            "Acceptance Checklist items resolved without a tick (rewritten or removed)",
            summary.resolved_items,
        )
    )
    detail_lines.extend(
        _render_summary_section("Change Log entries appended", summary.new_change_log_entries)
    )
    detail_lines.extend(
        _render_summary_section("Evidence files added", summary.added_evidence_files)
    )
    detail_lines.append(
        "Closeout diff: "
        + "; ".join(
            [
                _render_summary_rollup("ticked", summary.checked_items),
                _render_summary_rollup("resolved without a tick", summary.resolved_items),
                _render_summary_rollup("Change Log entry", summary.new_change_log_entries),
                _render_summary_rollup("evidence file", summary.added_evidence_files),
            ]
        )
    )
    return "\n".join(detail_lines)


_CLOSEOUT_KIND_INSTRUCTIONS: dict[DeliveryGateFailureKind, str] = {
    DeliveryGateFailureKind.CHECKLIST_UNCHECKED: (
        "The Acceptance Checklist still has unchecked items. For each unchecked item, first "
        "find the concrete evidence that its stated behavior was already executed — an "
        "evidence file, a command output, a test, a committed file. Tick the item ONLY when "
        "you can name that evidence, and write the evidence you relied on next to it. If an "
        "item's work was not actually done, leave it unchecked and say so in your summary; "
        "the runner will escalate to a full re-run, which is the correct outcome. Ticking an "
        "item edits the PRD, so the same pass must also append one `## Change Log` entry "
        "describing the ticks — the delivery gate rejects a modified PRD without one."
    ),
    DeliveryGateFailureKind.CHANGE_LOG_INCOMPLETE: (
        "The canonical PRD was modified during this run but its `## Change Log` does not "
        "record the change. Append one entry that describes what actually changed in the "
        "PRD; do not invent changes that did not happen."
    ),
    DeliveryGateFailureKind.EVIDENCE_MANIFEST_FORMAT: (
        "The structured evidence manifest has an illegal field format. Fix only the manifest "
        "file's structure and fields so it matches the required schema, using the evidence "
        "files that already exist. Do not fabricate items, commands, or outputs, and do not "
        "re-point a command at something you did not run."
    ),
    DeliveryGateFailureKind.FRONTEND_VISUAL_EVIDENCE_MISSING: (
        "This run changed frontend code but the evidence directory holds no screenshot or "
        "screen recording. Actually start the app, drive the changed UI through its real "
        "entry point, and save a real screenshot or screen recording into the evidence "
        "directory. A hand-drawn image, a text log, or a screenshot of unrelated UI does "
        "not satisfy this."
    ),
}


def build_closeout_prompt(context: CloseoutPromptContext) -> str:
    """构造收尾 agent 的 prompt：只含该门禁失败与收尾约束。

    刻意不复用实现 / recovery prompt 的模板：收尾层存在的前提是"代码已经正确"，
    prompt 里一旦出现"重新规划实现"的措辞，它就会退化成一个约束更松、超时更短、
    上下文更少的第二实现 Agent，而它的下一步紧接着就是发布路径。

    Args:
        context: 本次收尾的 Issue、worktree、门禁失败与允许改动范围。

    Returns:
        收尾 agent 的 prompt 文本。
    """
    issue = context.issue
    allowed_paths_text = (
        ", ".join(f"`{allowed_path}`" for allowed_path in context.allowed_scope.prd_paths)
        or "(this Issue references no canonical PRD)"
    )
    kind_instruction = _CLOSEOUT_KIND_INSTRUCTIONS.get(
        context.kind,
        "Complete the delivery closeout action the runner's gate is asking for.",
    )
    return "\n".join(
        [
            f"Finish the delivery closeout for GitHub Issue #{issue.number}: {issue.title}",
            "",
            f"Issue URL: {issue.url}",
            f"Worktree: {context.worktree_path}",
            "",
            "The implementation is already written, already verified, and already staged for "
            "commit. Only the delivery closeout is missing. The runner's gate reported:",
            context.gate_failure_message,
            "",
            kind_instruction,
            "",
            "Closeout rules (the runner enforces the first two by comparing the worktree "
            "before and after this pass — violating them fails the closeout and restarts the "
            "whole attempt):",
            f"- You may only modify the canonical PRD ({allowed_paths_text}) and files under "
            f"`{context.allowed_scope.evidence_dir}/`. Any other changed file — source, tests, "
            "config, docs — aborts this closeout.",
            "- Never tick an Acceptance Checklist item whose evidence you cannot point at. "
            "Ticking an item you did not verify is worse than leaving it unchecked: the "
            "runner re-runs the full gate chain after you finish and will catch it.",
            "- Do not modify source code, tests, or the Realistic Validation commands, and do "
            "not weaken, delete, or reword any Acceptance Checklist item to make it pass.",
            f"- {PRD_ARCHIVE_OWNERSHIP_RULE}",
            f"- {RUNNER_OWNED_CHECKLIST_ITEM_RULE}",
            "- Do not run `git add`, `git commit`, `git reset`, `git checkout`, or any other "
            "command that mutates the git index, and do not push or open PRs. The runner "
            "already holds this attempt's commit request and will commit your closeout edits "
            "together with the implementation.",
            "",
            PRD_MACHINE_CONTRACT_POINTER,
            "",
            "Finish with a concise summary listing, per Acceptance Checklist item you ticked, "
            "the evidence you relied on — and every item you deliberately left unchecked, "
            "with the reason.",
        ]
    )


def run_closeout_agent(
    agent_name: str,
    config: AppConfig,
    process_runner: IProcessRunner,
    *,
    prompt_context: CloseoutPromptContext,
) -> CommandResult:
    """运行一次受限的收尾 agent。

    复用当前轮次已选定的 agent 与既有的 :func:`run_agent_with_prompt_resilient`
    调用链（含瞬时错误就地重试），只替换 prompt 与超时预算——收尾层刻意不引入
    "收尾用哪个 agent"这个新的配置维度。

    Args:
        agent_name: 当前轮次使用的 agent 名。
        config: Agent Runner 配置，提供超时与重试参数。
        process_runner: 命令执行器。
        prompt_context: 本次收尾的上下文。

    Returns:
        收尾 agent 的命令结果。
    """
    closeout_timeout_seconds = config.runner.resolve_closeout_timeout_seconds(prompt_context.kind)
    _logger.info(
        "Starting Closeout Agent for Issue #%d (kind=%s, timeout=%ss).",
        prompt_context.issue.number,
        prompt_context.kind.value,
        closeout_timeout_seconds,
    )
    return run_agent_with_prompt_resilient(
        agent_name,
        build_closeout_prompt(prompt_context),
        prompt_context.worktree_path,
        process_runner,
        issue=prompt_context.issue,
        transient_retry_attempts=config.runner.transient_retry_attempts,
        transient_retry_delay_seconds=config.runner.transient_retry_delay_seconds,
        timeout_seconds=closeout_timeout_seconds,
        inactivity_timeout_seconds=config.runner.inactivity_timeout_seconds,
    )
