"""pre-PR review 的"审-修分工"构件。

从 ``agent_review.py`` 拆出：review gate 主体已接近单文件非空行上限，而
"审核者只出结论、由另一个 agent 落实 findings"这一段是自洽的一小块——
只读审核者的用途解析、同轮提交请求提醒、以及修复者的启动循环都在这里，
``agent_review.py`` 只负责把它们编排进收敛循环。

自修和审-修分工共用实际补丁检查与同轮提醒。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    ReviewFinding,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_DELIBERATE,
    AGENT_PROFILE_RUN,
)
from backend.core.use_cases.agent_invocation_tracing import PHASE_REVIEW_REPAIR
from backend.core.use_cases.agent_review_prompts import REPAIR_VALIDATION_GUIDANCE
from backend.core.use_cases.agent_runner_feedback import (
    build_finding_bullet_lines,
    build_repair_prompt,
)
from backend.core.use_cases.agent_runner_git import has_changes
from backend.core.use_cases.run_agent_once import run_agent_with_prompt_resilient

_logger = logging.getLogger(__name__)

COMMIT_REQUEST_RELATIVE_PATH = Path(".agent-runner/commit-request.json")
"""agent 表达提交意图的文件（worktree 相对路径）；本模块与 review 主体共用一份。"""


def prepare_review_patch_retry(worktree_path: Path, process_runner: IProcessRunner) -> bool:
    """在缺少补丁时清理空提交请求并准备同轮重试。

    Args:
        worktree_path: Git 工作树根目录。
        process_runner: 命令执行器。

    Returns:
        提交请求缺失或没有实际改动时返回 True。
    """
    request_path = worktree_path / COMMIT_REQUEST_RELATIVE_PATH
    if not request_path.is_file():
        return True
    if has_changes(worktree_path, process_runner, exclude_path=COMMIT_REQUEST_RELATIVE_PATH):
        return False
    request_path.unlink()
    return True


def resolve_reviewer_profile(reviewer_agent: str, config: AppConfig) -> str:
    """返回只读审核者应使用的调用用途。

    优先取该 agent 声明的 ``deliberate`` 用途——对提供沙箱的 agent（如 codex）
    这是硬性只读。未声明该用途时回落到 ``run`` 并打 WARNING，此时"审核者不得
    提交"只由提示词与"丢弃其提交请求"两条软约束兜底。
    """
    agent_spec = config.agents.get(reviewer_agent)
    if agent_spec is not None and AGENT_PROFILE_DELIBERATE in agent_spec.profiles:
        return AGENT_PROFILE_DELIBERATE
    _logger.warning(
        "Reviewer agent '%s' declares no '%s' profile; falling back to '%s'. "
        "The read-only guarantee for this agent is prompt-only.",
        reviewer_agent,
        AGENT_PROFILE_DELIBERATE,
        AGENT_PROFILE_RUN,
    )
    return AGENT_PROFILE_RUN


def build_commit_request_reminder_prompt(
    base_prompt: str,
    findings: tuple[ReviewFinding, ...],
    reminder_index: int,
) -> str:
    """在同一轮内提醒 Agent 产出实际补丁和提交请求。

    Args:
        base_prompt: 审核或修复提示词。
        findings: 本轮需要处理的审核发现。
        reminder_index: 从 1 开始的同轮提醒序号。

    Returns:
        附有修复指令和 findings 的提示词。
    """
    findings_block = "\n".join(build_finding_bullet_lines(findings)) or "(no structured findings)"
    opening = (
        f"\n\nREMINDER #{reminder_index}: The findings still need an actual patch and a "
        "commit request. Apply fixes for actionable findings in the worktree and write "
        "`.agent-runner/commit-request.json` with a descriptive `commit_message` for the patch. "
        f"{REPAIR_VALIDATION_GUIDANCE}"
    )
    return base_prompt + opening + "\n\nFindings that must be addressed:\n" + findings_block


def run_review_repair_agent(
    *,
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
    repair_agent: str,
    findings: tuple[ReviewFinding, ...],
) -> None:
    """让解析出的修复者按本轮 findings 改代码并写提交请求。

    修复者以普通用途启动，收到共享修复提示词。没有实际补丁或未写出
    ``.agent-runner/commit-request.json`` 时沿用现有的"同轮提醒重试"上限
    （``commit_request_reminder_attempts``），只是提醒对象从审核者变成修复者。
    提交仍由调用方的提交代理完成。
    """
    review_config = config.pre_pr_review
    repair_prompt = build_repair_prompt(
        issue=issue,
        worktree_path=worktree_path,
        findings=findings,
    )
    repair_prompt += f"\n- {REPAIR_VALIDATION_GUIDANCE}"
    max_reminder_attempts = max(0, review_config.commit_request_reminder_attempts)
    for repair_attempt in range(max_reminder_attempts + 1):
        _logger.info(
            "Pre-PR review repair for Issue #%d: running repairer '%s' (attempt %d/%d).",
            issue.number,
            repair_agent,
            repair_attempt + 1,
            max_reminder_attempts + 1,
        )
        run_agent_with_prompt_resilient(
            repair_agent,
            repair_prompt,
            worktree_path,
            process_runner,
            config=config,
            capture_output=True,
            timeout_seconds=max(1, review_config.timeout_seconds),
            issue=issue,
            transient_retry_attempts=config.runner.transient_retry_attempts,
            transient_retry_delay_seconds=config.runner.transient_retry_delay_seconds,
            invocation_phase=PHASE_REVIEW_REPAIR,
            invocation_attempt=repair_attempt + 1,
        )
        if repair_attempt >= max_reminder_attempts or not prepare_review_patch_retry(
            worktree_path, process_runner
        ):
            break
        _logger.info(
            "Pre-PR review repair for Issue #%d: repairer '%s' did not produce "
            "a patch with a commit request; re-prompting.",
            issue.number,
            repair_agent,
        )
        repair_prompt = build_commit_request_reminder_prompt(
            repair_prompt,
            findings,
            reminder_index=repair_attempt + 1,
        )


__all__ = [
    "COMMIT_REQUEST_RELATIVE_PATH",
    "build_commit_request_reminder_prompt",
    "prepare_review_patch_retry",
    "resolve_reviewer_profile",
    "run_review_repair_agent",
]
