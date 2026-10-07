"""在发布前将 RV 与独立复核结论绑定到最终提交。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.shared.models.publish_stage import PublishStage
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
    #: 兼容旧调用面的快速通道布尔。唯一事实源是 :attr:`publish_stage`，本字段在
    #: ``__post_init__`` 里归一化为该档位的派生视图（不会出现矛盾组合）。
    fast_merge: bool | None = None
    #: 发布档位：``FAST`` / ``DIRECT`` 都不再重跑 RV 与独立 verifier；``DIRECT``
    #: 额外意味着上游连 pre-PR review 与仓库验证命令都没跑。
    publish_stage: PublishStage = PublishStage.NORMAL

    def __post_init__(self) -> None:
        """把 ``fast_merge`` 布尔收进 :attr:`publish_stage`，并回填其派生视图。"""
        if self.fast_merge and self.publish_stage is PublishStage.NORMAL:
            object.__setattr__(self, "publish_stage", PublishStage.FAST)
        object.__setattr__(self, "fast_merge", self.publish_stage is PublishStage.FAST)


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
    if request.publish_stage.skips_independent_verification:
        # 旁路档位的审计日志：跳过的是验证门禁，不是提交完整性前提（上方 clean-tree
        # 检查照常）。档位来源可能是 CLI 旗标，也可能是 Issue 上的直发标签，因此这里
        # 只声明档位，来源记在认领时的 publish stage resolution 日志里。
        stage_name = "Fast-merge" if request.publish_stage is PublishStage.FAST else "Direct-pr"
        _logger.info(
            "%s: skipping final RV/verifier re-check for Issue #%d at %s; the PR will "
            "carry the unverified annotation (see this Issue's publish stage resolution "
            "log for the origin).",
            stage_name,
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
