"""在发布前将 RV 与独立复核结论绑定到最终提交。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.agent_runner_validation import (
    ensure_no_misplaced_evidence_helpers,
    ensure_validation_commands_pass,
    ensure_validation_evidence_ready,
)
from backend.core.use_cases.run_agent_once import get_head_sha, has_changes
from backend.core.use_cases.run_verifier_agent import ValidationVerdict, run_verifier_gate

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalVerificationRequest:
    """发布前复核需要的提交和运行上下文。"""

    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    process_runner: IProcessRunner
    selected_agent: str
    verified_sha: str | None
    verifier_verdict: ValidationVerdict | None
    #: 快速通道（``iar run --fast-merge``）：HEAD 变化时不再重跑 RV 与独立
    #: verifier，直接沿用（空的）初次结论；PR 正文由发布路径打未验证标注。
    fast_merge: bool = False


def ensure_final_verifier_verdict(request: FinalVerificationRequest) -> ValidationVerdict | None:
    """复用同一提交的结论；review 改动提交后重新执行 RV 与 verifier。

    Args:
        request: 包含初次复核提交、结论和最终工作树的上下文。

    Returns:
        与当前提交绑定的独立复核结论；无需复核时返回 ``None``。

    Raises:
        ValidationEvidenceError: 最终工作树未提交或 RV / 独立复核失败。
    """
    if has_changes(request.worktree_path, request.process_runner):
        raise ValidationEvidenceError(
            "Final verification requires a clean committed worktree after pre-PR review."
        )
    final_sha = get_head_sha(request.worktree_path, request.process_runner)
    if request.fast_merge:
        # 快速通道的审计日志：跳过的是验证门禁，不是提交完整性前提（上方 clean-tree
        # 检查照常），且旗标来源必须可事后追溯。
        _logger.info(
            "Fast-merge (origin: --fast-merge run flag): skipping final RV/verifier "
            "re-check for Issue #%d at %s; the PR will carry the unverified annotation.",
            request.issue.number,
            final_sha,
        )
        return request.verifier_verdict
    if final_sha == request.verified_sha:
        return request.verifier_verdict

    _logger.info(
        "Final HEAD changed after verification for Issue #%d (%s -> %s); "
        "re-running RV and independent verifier before creating the PR.",
        request.issue.number,
        request.verified_sha or "unverified",
        final_sha,
    )
    ensure_validation_evidence_ready(
        request.issue, request.worktree_path, request.config, request.process_runner
    )
    ensure_no_misplaced_evidence_helpers(
        request.worktree_path, request.config, request.process_runner
    )
    ensure_validation_commands_pass(
        request.issue, request.worktree_path, request.config, request.process_runner
    )
    final_verdict = run_verifier_gate(
        request.issue,
        request.worktree_path,
        request.config,
        request.process_runner,
        request.selected_agent,
    )
    if (
        has_changes(request.worktree_path, request.process_runner)
        or get_head_sha(request.worktree_path, request.process_runner) != final_sha
    ):
        raise ValidationEvidenceError(
            "Final RV or verifier changed the committed code tree; the result is stale."
        )
    return final_verdict
