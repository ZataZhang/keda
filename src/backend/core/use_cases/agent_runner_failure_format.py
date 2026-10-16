"""Agent Runner 失败评论与 attempt history 的 Markdown 渲染。

从 :mod:`backend.core.use_cases.agent_runner_failure` 拆分而来：单文件非空行有
CI 硬上限（1000），异常类型与失败分类留在原模块，本模块只负责把分类结果、
attempt 历史与发布失败渲染成 Issue 评论；公共名由原模块再导出，消费方入口不变。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.models.agent_runner import (
    AttemptResult,
    CommandResult,
    PublishFailureCategory,
)
from backend.core.use_cases.agent_runner_events import format_failure_context_marker
from backend.core.use_cases.agent_runner_feedback import (
    failed_verification_results,
    format_result_for_recovery,
    truncate_recovery_output,
)

__all__ = [
    "ATTEMPT_HISTORY_HEADING",
    "build_publish_failure_comment_body",
    "detect_usage_limit_root_cause",
    "format_agent_execution_failure",
    "format_attempt_duration",
    "format_attempt_history",
    "format_blocked_failure_comment",
    "format_failure_comment",
    "format_failure_context_comment",
    "format_minimal_failure_comment",
    "format_publish_failure_comment",
    "format_recovery_failure_summary",
    "summarize_failure_exception",
]

_ATTEMPT_DETAIL_SUMMARY_MAX_LENGTH = 200
_ATTEMPT_DETAIL_SCAFFOLD_LINES = frozenset({"```", "```text", "stdout:", "stderr:"})
_ATTEMPT_START_DISPLAY_FORMAT = "%Y-%m-%d %H:%MZ"
_ATTEMPT_START_MISSING_DISPLAY = "-"
_ATTEMPT_NUMBER_SCOPE_NOTE = (
    "_`Attempt` counts within one agent run: it restarts at 1 on every agent "
    "switch and every re-claim, so use `Started` to tell consecutive runs apart. "
    "Rows are ordered oldest-first._"
)

_USAGE_LIMIT_HINT_PATTERN = re.compile(
    r"usage limit|insufficient_quota|request rejected \(429\)",
    re.IGNORECASE,
)
#: 两种重置时刻写法都要能取到：Claude 的 "resets at <ISO 时间戳>"（单个 token）
#: 与 codex 的 "try again at Oct 5th, 2026 10:24 AM"（带空格的自然语言）。
_USAGE_LIMIT_RESET_AT_PATTERN = re.compile(
    r"resets\s+at\s+(?P<iso>\S+)" r"|try again\s+at\s+(?P<prose>[^\n]+)",
    re.IGNORECASE,
)

#: Workflow labels that represent a completed implementation phase. When a
#: failure occurs while transitioning to one of these labels, the agent's local
#: work is already done; recovery should retry the transition rather than
#: re-run the agent from ``agent/ready``.
_COMPLETION_WORKFLOW_LABELS = frozenset({"agent/supervising", "agent/automated-review-passed"})

#: Matches ``gh issue edit <number> ... --add-label <label>`` and captures the
#: target label, regardless of option order or extra flags.
_GH_ISSUE_EDIT_ADD_LABEL_PATTERN = re.compile(
    r"gh issue edit\s+\d+\s+.*--add-label\s+(\S+)",
    re.IGNORECASE,
)

_REBASE_HEAD_MISMATCH_PATTERN = re.compile(
    r"Rebase aborted:\s*HEAD\s+[0-9a-f]{7,40}\s+does not match expected\s+[0-9a-f]{7,40}",
    re.IGNORECASE,
)


def _detect_transition_target_label(exc: BaseException) -> str | None:
    """Return the target workflow label if ``exc`` is a failed transition.

    The GitHub CLI labels the transition command as
    ``gh issue edit <n> --add-label <target> --remove-label <current>``.
    When this command fails after the agent has already finished its work,
    the operator should retry the transition instead of re-running the agent.

    Args:
        exc: The exception that caused the failure.

    Returns:
        The target label string if one can be parsed, otherwise ``None``.
    """
    if not isinstance(exc, subprocess.CalledProcessError):
        return None
    cmd = getattr(exc, "cmd", None)
    if cmd is None:
        return None
    if isinstance(cmd, (list, tuple)):
        cmd_str = " ".join(str(part) for part in cmd)
    else:
        cmd_str = str(cmd)
    match = _GH_ISSUE_EDIT_ADD_LABEL_PATTERN.search(cmd_str)
    if match is None:
        return None
    return match.group(1)


def _is_completion_workflow_label(label: str) -> bool:
    """Return whether ``label`` represents a post-implementation workflow state."""
    return label in _COMPLETION_WORKFLOW_LABELS


def _is_rebase_head_mismatch_failure(failure_text: str) -> bool:
    """判断失败是否为 PR rebase 后 HEAD 与旧预期值不匹配。"""
    return _REBASE_HEAD_MISMATCH_PATTERN.search(failure_text) is not None


def detect_usage_limit_root_cause(failure_text: str) -> str | None:
    """Return a human-readable root-cause line for API usage limit failures.

    Args:
        failure_text: Combined exception messages and attempt details to inspect.

    Returns:
        A Markdown summary line with the reset time when available,
        or None when no usage-limit signature is found.
    """
    if _USAGE_LIMIT_HINT_PATTERN.search(failure_text) is None:
        return None
    reset_match = _USAGE_LIMIT_RESET_AT_PATTERN.search(failure_text)
    if reset_match is not None:
        reset_at = (reset_match.group("iso") or reset_match.group("prose") or "").strip()
        reset_at = reset_at.rstrip(".,;)")
        return (
            "**Root cause:** the agent provider's usage limit is exhausted. "
            f"The limit resets at `{reset_at}`; retries before then will fail the same way."
        )
    return (
        "**Root cause:** the agent provider's usage limit is exhausted. "
        "Retries will keep failing until the usage window resets."
    )


def _summarize_attempt_detail(detail: str) -> str:
    """Pick the most informative single line of an attempt detail.

    Attempt details are multi-line reports whose actual error almost always
    sits on the last content line (command output tail, exception message),
    so the summary keeps that line instead of head-truncating boilerplate.
    """
    stripped_lines = (line.strip() for line in detail.splitlines())
    informative_lines = [
        line for line in stripped_lines if line and line not in _ATTEMPT_DETAIL_SCAFFOLD_LINES
    ]
    if not informative_lines:
        return ""
    summary = informative_lines[-1].replace("|", "\\|")
    if len(summary) > _ATTEMPT_DETAIL_SUMMARY_MAX_LENGTH:
        return summary[: _ATTEMPT_DETAIL_SUMMARY_MAX_LENGTH - 1] + "…"
    return summary


def _format_attempt_start(started_at: str) -> str:
    """Render an attempt's ISO-8601 UTC start time for the history table.

    表格里的 ``Attempt`` 编号只在"本轮 agent 运行"内递增，跨 agent fallback 和
    重新 claim 都会从 1 重新开始，而实时评论把同一个 Issue 的全部轨迹渲染进同一
    张表，因此必须给每行一个时间锚点，读者才能分辨相邻两行属于同一轮还是两轮。

    历史行可能没有 ``started_at``（旧库记录，或没有配置 SQLite 时的内存兜底），
    空值渲染为 ``-``，无法解析的值原样回显，不让整张表因为一个时间戳失败。
    不带时区的取值按 UTC 解释，与 ``AttemptResult.started_at`` 的约定一致。
    """
    if not started_at:
        return _ATTEMPT_START_MISSING_DISPLAY
    try:
        started_datetime = datetime.fromisoformat(started_at)
    except ValueError:
        return started_at
    if started_datetime.tzinfo is None:
        started_datetime = started_datetime.replace(tzinfo=timezone.utc)
    return started_datetime.astimezone(timezone.utc).strftime(_ATTEMPT_START_DISPLAY_FORMAT)


_MAX_RENDERED_PHASES = 3
_PHASE_RENDER_FLOOR_SECONDS = 0.5


def format_attempt_duration(result: AttemptResult) -> str:
    """把 attempt 总时长渲染成"总时长（耗时最大的几个阶段）"。

    只显示一个总时长时，"卡了很久"无法定位——几千秒可能是 agent 自己在想，也可能
    是 ``just test`` 挂住或某条 RV 命令一路超时。这里把耗时最大的几个阶段直接摊在
    表格里，一眼能看出时间去哪了；阶段明细缺失（旧记录）时退回纯总时长。

    Args:
        result: 要渲染的 attempt 记录。

    Returns:
        形如 ``7153.9s (agent 7100.2s · verification 40.1s)`` 的单元格文本。
    """
    total_duration = f"{result.duration_seconds:.1f}s"
    rendered_phases = [
        f"{phase.name} {phase.seconds:.1f}s"
        for phase in result.phase_durations[:_MAX_RENDERED_PHASES]
        if phase.seconds >= _PHASE_RENDER_FLOOR_SECONDS
    ]
    if not rendered_phases:
        return total_duration
    return f"{total_duration} ({' · '.join(rendered_phases)})"


ATTEMPT_HISTORY_HEADING = "### Attempt History"
"""Attempt History 表格的小节标题（渲染与检索方共用，勿在各处重复字面量）。"""


def format_attempt_history(
    attempt_results: list[AttemptResult],
    *,
    include_title: bool = True,
) -> str:
    """Format attempt results as a Markdown table.

    Args:
        attempt_results: Attempts to render, oldest first.
        include_title: Whether to prefix the table with its own heading. The
            live attempt-history comment already carries one, so it renders the
            table headless instead of repeating the title.
    """
    if not attempt_results:
        return ""

    lines = [ATTEMPT_HISTORY_HEADING, ""] if include_title else []
    lines.extend(
        [
            "| Attempt | Started (UTC) | Agent | Failure Type | Recovered | Duration | Detail |",
            "|---------|---------------|-------|-------------|-----------|----------|--------|",
        ]
    )
    for result in attempt_results:
        detail = _summarize_attempt_detail(result.detail)
        recovered = "Yes" if result.recovered else "No"
        agent = result.agent or "-"
        duration = format_attempt_duration(result)
        lines.append(
            f"| {result.attempt_number} | {_format_attempt_start(result.started_at)} | {agent} | "
            f"{result.failure_type.value} | {recovered} | {duration} | {detail} |"
        )
    lines.extend(["", _ATTEMPT_NUMBER_SCOPE_NOTE])
    return "\n".join(lines)


def format_failure_comment(
    exc: BaseException,
    attempt_results: list[AttemptResult] | None = None,
    *,
    issue_number: int | None = None,
) -> str:
    """构建包含根因、尝试历史和恢复建议的失败评论。

    已知错误特征（当前包括 API 限额）会在评论顶部展示根因摘要。
    ``CalledProcessError`` 的原因会使用简短命令名和截断后的输出，避免把完整
    Agent 提示词写进 Issue。rebase 后 HEAD 与旧预期值不匹配时，评论提示续接
    已有 PR 的发布/监督流程，不会建议重新启动实现任务。

    Args:
        exc: 导致失败的异常。
        attempt_results: 可选的尝试历史表格。
        issue_number: 提供后会在评论末尾添加恢复建议；具体命令根据失败阶段决定。

    Returns:
        格式化后的 Markdown 评论正文。
    """
    cause = exc.__cause__
    if isinstance(cause, subprocess.CalledProcessError):
        cause_text = "\n".join([str(cause.output or ""), str(cause.stderr or "")])
    elif cause is not None:
        cause_text = str(cause)
    else:
        cause_text = ""
    searchable_failure_text = "\n".join(
        [str(exc), cause_text] + [result.detail for result in attempt_results or []]
    )

    lines = ["## Agent Runner Failed", ""]

    root_cause_summary = detect_usage_limit_root_cause(searchable_failure_text)
    if root_cause_summary is not None:
        lines.extend([root_cause_summary, ""])

    if attempt_results:
        lines.append(format_attempt_history(attempt_results))
        lines.append("")

    lines.extend(["```text", summarize_failure_exception(exc), "```", ""])
    if isinstance(cause, subprocess.CalledProcessError):
        lines.extend([format_agent_execution_failure(cause), ""])
    elif cause is not None:
        lines.extend(["```text", summarize_failure_exception(cause), "```", ""])
    if issue_number is not None:
        transition_label = _detect_transition_target_label(exc)
        if transition_label is not None and _is_completion_workflow_label(transition_label):
            lines.extend(
                [
                    "### How To Recover",
                    "",
                    "The agent finished its work, but the final workflow "
                    "label transition failed. You can retry the transition "
                    "without re-running the agent:",
                    "",
                    "```bash",
                    f"gh issue edit {issue_number} "
                    f"--add-label {transition_label} --remove-label agent/failed",
                    "```",
                    "",
                    "If the worktree is dirty, remove it first "
                    f"(`git worktree remove <repo>-worktrees/tasks/issue-{issue_number}`). "
                    "See the 失败重跑 section in `docs/guides/agent-runner.md` "
                    "for the full procedure.",
                    "",
                ]
            )
        elif _is_rebase_head_mismatch_failure(searchable_failure_text):
            lines.extend(
                [
                    "### How To Recover",
                    "",
                    "The supervisor found that the PR branch HEAD changed during rebase. "
                    "The rebase may already have completed, so do not relabel this Issue "
                    "to `agent/ready` and start a new implementation pass.",
                    "",
                    "If the Issue worktree contains the rebased branch and the PR already "
                    "exists, resume publication and supervision with:",
                    "",
                    "```bash",
                    f"kc recover --issue {issue_number}",
                    "```",
                    "",
                    "If the worktree and PR head do not match, inspect both before retrying; "
                    "do not delete a dirty worktree. See the “已有 PR 的 rebase HEAD 不匹配” "
                    "row in the `docs/guides/agent-runner.md` recovery table.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "### How To Recover",
                    "",
                    "After fixing the root cause, relabel the Issue so the "
                    "runner picks it up on its next poll:",
                    "",
                    "```bash",
                    f"gh issue edit {issue_number} "
                    "--add-label agent/ready --remove-label agent/failed",
                    "```",
                    "",
                    "If the worktree is dirty, remove it first "
                    f"(`git worktree remove <repo>-worktrees/tasks/issue-{issue_number}`). "
                    "See the 失败重跑 section in `docs/guides/agent-runner.md` "
                    "for the full procedure.",
                    "",
                ]
            )
    return "\n".join(lines)


_DELIVERABLE_NAME_PATTERN = re.compile(r"rv-\d+(?:-[A-Za-z0-9_.-]+)?(?:\.[A-Za-z0-9]+)?")


def _extract_named_deliverables(attempt_results: list[AttemptResult]) -> list[str]:
    """从 attempt 历史里原样摘出门禁点名的 ``rv-<n>`` 呈递物名。

    FR-2 要求记录含"缺失呈递物"。这里只做**文本转述**——把门禁报告自己写出来的
    ``rv-3`` / ``rv-3-prd-render.png`` 这类名字收集起来，不判断它到底缺没缺、也不
    为它建枚举；判断仍留在门禁原文里，由人或下一轮自己读。
    """
    named: list[str] = []
    seen: set[str] = set()
    for result in attempt_results:
        for match in _DELIVERABLE_NAME_PATTERN.findall(result.detail):
            if match not in seen:
                seen.add(match)
                named.append(match)
    return named


def format_failure_context_comment(
    exc: BaseException,
    attempt_results: list[AttemptResult],
    *,
    issue_number: int,
    checkpoint_sha: str | None,
    evidence_dir: str,
) -> str:
    """渲染 recovery 耗尽时写到 Issue 上的交接记录。

    耗尽不是终点而是交班点：本轮"做到哪、卡在哪、缺什么、快照在哪个 SHA"必须落成
    一份下一轮能读回、人能定位的记录。本函数只做**事实转述**——verifier 说过什么、
    agent 试过几轮、最后一道门禁的报告原文、快照 SHA——不新增"产品失败 / 审核事故"
    的机器判定，因此也不产出任何标签或状态。

    正文刻意写清快照性质：耗尽时可发布的只是 :func:`checkpoint_uncommitted_progress`
    建的 WIP 中途快照，它可能连编译都不过，不是"实现完成但没过验收"。

    Args:
        exc: 触发耗尽的异常（``MaxRetriesExceededError``）。
        attempt_results: 本轮的 attempt 历史，来自异常携带的 attempt 列表。
        issue_number: 目标 Issue 编号。
        checkpoint_sha: WIP 快照 commit SHA；``None`` 表示本轮没有留下可安全推送的
            快照（干净工作树、分支不符或改动全为禁改路径）。
        evidence_dir: 该 Issue 的证据目录相对路径，供人自行核对原始诊断。

    Returns:
        以 ``iar:failure-context`` marker 开头的 Markdown 评论正文。
    """
    # Local import: run_verifier_agent 依赖 run_agent_once，而后者又导入本模块，
    # 模块级导入会成环。verdict 的解析沿用既有确定性 parser，不另起一套判定。
    from backend.core.use_cases.run_verifier_agent import parse_verifier_verdict

    last_gate_report = attempt_results[-1].detail if attempt_results else str(exc)
    # verdict 从最近一次 attempt 往前找**第一个真正形成的判定**，而不是只看最后一跳：
    # 耗尽时最后一轮常见的是更早某道门禁（验证命令 / PRD 交付 / 证据）拦住的，它会把
    # verifier 那句真 RED 挤出末位。只读末位会把"上一轮明确判红"误写成"没有形成结论",
    # 误导接手方去查一个并不存在的问题。
    verdict = parse_verifier_verdict(last_gate_report)
    verdict_attempt_number = attempt_results[-1].attempt_number if attempt_results else None
    if not verdict.marker_found:
        for earlier_result in reversed(attempt_results):
            earlier_verdict = parse_verifier_verdict(earlier_result.detail)
            if earlier_verdict.marker_found:
                verdict = earlier_verdict
                verdict_attempt_number = earlier_result.attempt_number
                break
    marker_state = verdict.risk if verdict.marker_found else "no-verdict"
    missing_deliverables = _extract_named_deliverables(attempt_results)

    lines = [
        format_failure_context_marker(
            checkpoint_sha=checkpoint_sha,
            attempt_count=len(attempt_results),
            verifier_state=marker_state,
            evidence_dir=evidence_dir,
        ),
        "",
        "## Agent Runner Handoff — Recovery Budget Exhausted",
        "",
        "This Issue's runner exhausted its recovery attempts without clearing the "
        "delivery gates. The record below is a **factual handoff** for the next "
        "claim, not an acceptance result: it transcribes what the gates reported "
        "and does not judge whether that is a product defect or a review incident.",
        "",
        "",
    ]
    if checkpoint_sha is None:
        # 没有快照时不能把上一行那句"WIP checkpoint"照抄下来，否则读者会以为工作树上
        # 有个半成品提交可以续作——恰好是"WIP 快照 ≠ 完成品"这条边界最容易被误读的方向。
        lines.extend(
            [
                "- Snapshot nature: none — this round left no commit at all, so the "
                "next claim continues from whatever the working tree holds, not from a "
                "WIP snapshot.",
                "- Checkpoint commit: none — this round left no safely publishable "
                "snapshot (clean worktree, branch mismatch, or only forbidden paths).",
            ]
        )
    else:
        lines.extend(
            [
                "- Snapshot nature: `WIP checkpoint` — a mid-progress commit, "
                "**not** a finished implementation.",
                f"- Checkpoint commit: `{checkpoint_sha}`",
            ]
        )
    lines.append(f"- Attempts this claim: {len(attempt_results)}")

    if verdict.marker_found and verdict.risk == "red":
        lines.append(
            "- Verifier: formed an explicit **red** verdict; its findings are quoted "
            "in the gate report below."
        )
    elif verdict.marker_found:
        lines.append(
            f"- Verifier: formed a `{verdict.risk}` verdict, but a later gate still "
            "blocked the run; see the gate report below."
        )
    else:
        lines.append(
            "- Verifier: **no verdict was formed** this round. The run stopped before "
            "the independent verifier produced a parseable decision, so nothing here "
            "says the implementation is wrong — and nothing says it is right either."
        )
    if verdict_attempt_number is not None and attempt_results:
        last_attempt_number = attempt_results[-1].attempt_number
        if verdict_attempt_number != last_attempt_number:
            lines.append(
                f"  - That verdict was recorded on attempt {verdict_attempt_number}; "
                f"the run then continued to attempt {last_attempt_number}, whose gate "
                "report is the one quoted below."
            )
    if missing_deliverables:
        rendered_deliverables = ", ".join(f"`{name}`" for name in missing_deliverables)
        lines.append(
            f"- Deliverables named by the gate reports: {rendered_deliverables} "
            "(transcribed from the reports below — this record does not judge whether "
            "each one is actually missing)."
        )
    else:
        lines.append(
            "- Deliverables: no `rv-<n>` artifact was named in the recorded gate "
            "reports; read them below to see what the last round actually lacked."
        )

    lines.append(f"- Evidence directory: `{evidence_dir}`")
    lines.extend(
        [
            "- Acceptance label: `validation/verifier-passed` is **absent**, so the "
            "existing sign-off, merge and archive gates keep refusing this work. "
            "That is the gate doing its job, not a new gate introduced here.",
            "",
            "### Last gate report",
            "",
            "```text",
            truncate_recovery_output(last_gate_report),
            "```",
            "",
        ]
    )

    if attempt_results:
        lines.append(format_attempt_history(attempt_results))
        lines.append("")

    lines.extend(
        [
            "### What The Next Claim Should Do",
            "",
            "Continue **from the checkpoint commit** instead of restarting: inspect "
            "`git log`, the PRD Acceptance Checklist, and the gate report above, then "
            "implement only what remains and produce the missing deliverables.",
            "Treat everything on this line as the previous round's *statement*, not a "
            "ruling — re-verify against the PRD before acting on it.",
            "",
            "```bash",
            f"gh issue edit {issue_number} " "--add-label agent/ready --remove-label agent/failed",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def format_minimal_failure_comment(exc: BaseException, *, issue_number: int | None = None) -> str:
    """构建完整失败评论无法发布时使用的精简评论。

    完整报告可能因包含过长输出或控制字符而被 GitHub 拒绝。此时使用短摘要保留
    失败原因和恢复方式；rebase HEAD 不匹配仍引导续接已有 PR，而不是重新排队。

    Args:
        exc: 导致失败的异常。
        issue_number: 提供后会按失败阶段附加可复制的恢复命令。

    Returns:
        精简版 Markdown 失败评论正文。
    """
    exc_message = str(exc).strip()
    failure_summary = exc_message.splitlines()[0] if exc_message else type(exc).__name__
    lines = [
        "## Agent Runner Failed",
        "",
        "The full failure report could not be posted to GitHub; "
        "showing a summary instead. See the runner logs for full details.",
        "",
        f"- Failure: `{failure_summary}`",
    ]
    if issue_number is not None:
        transition_label = _detect_transition_target_label(exc)
        if transition_label is not None and _is_completion_workflow_label(transition_label):
            lines.extend(
                [
                    "",
                    "### How To Recover",
                    "",
                    "The agent finished its work, but the final workflow "
                    "label transition failed. You can retry the transition "
                    "without re-running the agent:",
                    "",
                    "```bash",
                    f"gh issue edit {issue_number} "
                    f"--add-label {transition_label} --remove-label agent/failed",
                    "```",
                ]
            )
        elif _is_rebase_head_mismatch_failure(exc_message):
            lines.extend(
                [
                    "",
                    "### How To Recover",
                    "",
                    "The rebase may already have completed. If the PR branch contains the "
                    "rebased head, resume its publish/review workflow instead of marking "
                    "the Issue `agent/ready`:",
                    "",
                    "```bash",
                    f"kc recover --issue {issue_number}",
                    "```",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### How To Recover",
                    "",
                    "After fixing the root cause, relabel the Issue so the "
                    "runner picks it up on its next poll:",
                    "",
                    "```bash",
                    f"gh issue edit {issue_number} "
                    "--add-label agent/ready --remove-label agent/failed",
                    "```",
                ]
            )
    return "\n".join(lines)


def format_publish_failure_comment(
    exc: BaseException,
    issue_number: int,
    *,
    worktree_path: Path | None = None,
    failure_category: PublishFailureCategory = PublishFailureCategory.UNKNOWN,
) -> str:
    """Build a failure comment for publish phase failures.

    Args:
        exc: The exception that caused the failure.
        issue_number: GitHub Issue number.
        worktree_path: Path to the worktree, if available.
        failure_category: Category of the publish failure.

    Returns:
        Markdown comment body.
    """
    return build_publish_failure_comment_body(
        header="## Agent Runner Publish Failed",
        intro="The agent produced a local commit but publishing failed.",
        action_intro="To resume publishing without re-running the agent:",
        issue_number=issue_number,
        failure_category=failure_category.value,
        worktree_path=worktree_path,
        exc=exc,
    )


def build_publish_failure_comment_body(
    *,
    header: str,
    intro: str,
    action_intro: str,
    issue_number: int,
    failure_category: str,
    worktree_path: Path | None,
    exc: BaseException,
) -> str:
    """Build the shared body for publish / publish-recovery failure comments.

    Both the publish phase and the ``kc recover`` flow render the same
    structure (failure category, optional worktree, error text + cause, and the
    ``kc recover`` retry hint), differing only in the heading, intro line, and
    action sentence.

    Args:
        header: Markdown heading line (e.g. ``"## Agent Runner Publish Failed"``).
        intro: One-line description shown under the heading.
        action_intro: Sentence introducing the ``kc recover`` command block.
        issue_number: GitHub Issue number used in the recover command.
        failure_category: Human-readable publish failure category.
        worktree_path: Worktree path to surface, if available.
        exc: The exception that caused the failure.

    Returns:
        Markdown comment body.
    """
    lines = [
        header,
        "",
        intro,
        "",
        f"- Failure category: `{failure_category}`",
    ]

    if worktree_path is not None:
        lines.append(f"- Worktree: `{worktree_path}`")

    lines.extend(
        [
            "",
            "```text",
            str(exc),
            "",
        ]
    )

    if exc.__cause__ is not None:
        lines.append(str(exc.__cause__))

    lines.extend(
        [
            "```",
            "",
            action_intro,
            "",
            "```bash",
            f"uv run kc recover --issue {issue_number}",
            "```",
        ]
    )

    return "\n".join(lines)


def format_blocked_failure_comment(
    exc: BaseException,
    attempt_results: list[AttemptResult] | None = None,
    *,
    issue_number: int | None = None,
) -> str:
    """Build a blocked failure comment for forbidden path interception.

    Args:
        exc: The exception that caused the blockage.
        attempt_results: Optional attempt history rendered as a table.
        issue_number: When provided, recovery guidance is appended.
    """
    exc_message = str(exc)
    blocked_paths: list[str] = []
    if "Refusing to publish forbidden paths:" in exc_message:
        paths_part = exc_message.split("Refusing to publish forbidden paths:", 1)[1]
        blocked_paths = [p.strip() for p in paths_part.split(",") if p.strip()]

    lines = [
        "## Agent Runner Blocked",
        "",
        "The agent was blocked because it attempted to modify forbidden paths.",
        "",
        "- Block type: `blocked_forbidden`",
    ]

    if blocked_paths:
        lines.append("- Blocked paths:")
        for path in blocked_paths:
            lines.append(f"  - `{path}`")

    lines.extend(
        [
            "",
            "### How to Resume",
            "",
            "1. Review the blocked files above.",
            "2. Resolve each file appropriately (commit, revert, or modify).",
            "3. Ensure the worktree is clean (`git status` shows no pending changes).",
            "4. Run the following command to continue:",
            "",
            "```bash",
            f"uv run kc blocked-continue --issue {issue_number}",
            "```",
            "",
        ]
    )

    if attempt_results:
        lines.append(format_attempt_history(attempt_results))
        lines.append("")

    lines.extend(["```text", exc_message, "```", ""])
    return "\n".join(lines)


def format_recovery_failure_summary(
    heading: str,
    verification_results: list[CommandResult],
) -> str:
    """Build the failure section for a verification recovery prompt."""
    failed_results = failed_verification_results(verification_results)
    if not failed_results:
        return heading
    result_sections = "\n\n".join(format_result_for_recovery(result) for result in failed_results)
    return "\n\n".join([heading, result_sections])


def format_agent_execution_failure(exc: BaseException) -> str:
    """Build the failure section for a failed agent CLI invocation."""
    lines = ["Agent command failed before runner verification could start."]
    if isinstance(exc, subprocess.CalledProcessError):
        lines.extend(
            [
                f"Command: `{_agent_command_name(exc.cmd)}`",
                f"Exit code: {exc.returncode}",
                "stdout:",
                "```text",
                truncate_recovery_output(str(exc.output or "")),
                "```",
                "stderr:",
                "```text",
                truncate_recovery_output(str(exc.stderr or "")),
                "```",
            ]
        )
        if not exc.output and not exc.stderr:
            lines.append("The command streamed its details to the terminal.")
    else:
        lines.extend(
            [
                f"Exception type: {type(exc).__name__}",
                "Exception:",
                "```text",
                truncate_recovery_output(summarize_failure_exception(exc)),
                "```",
            ]
        )
    return "\n".join(lines)


def summarize_failure_exception(exc: BaseException) -> str:
    """Render an exception for failure comments without echoing the agent prompt.

    ``subprocess.TimeoutExpired.__str__`` 把整条命令原样拼进消息里,而 agent 的
    命令里带着完整 prompt——一次 verifier 超时因此让 Issue 评论变成几千行 prompt,
    真正有用的信息(谁超时了、超时前输出了什么)反而被埋掉。``CalledProcessError``
    早就在这里做了短命令名 + 截断输出的处理,超时只是漏了同一条路。

    非超时异常按 ``str(exc)`` 原样返回,保持既有行为不变。
    """
    if not isinstance(exc, subprocess.TimeoutExpired):
        return str(exc)
    summary_lines = [
        f"Command `{_agent_command_name(exc.cmd)}` timed out after {exc.timeout}s "
        "and was terminated."
    ]
    for stream_label, stream_content in (("stdout", exc.output), ("stderr", exc.stderr)):
        if not stream_content:
            continue
        summary_lines.extend(
            [
                f"Partial {stream_label} captured before the kill:",
                truncate_recovery_output(str(stream_content)),
            ]
        )
    if not exc.output and not exc.stderr:
        summary_lines.append("No output was captured before the kill.")
    return "\n".join(summary_lines)


def _agent_command_name(command: object) -> str:
    """Return a short command name without echoing a potentially huge prompt."""
    if isinstance(command, (list, tuple)) and command:
        return str(command[0])
    return str(command)
