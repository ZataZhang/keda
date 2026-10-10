"""Agent Runner 成功执行后的可复用 skill 提炼流程。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.core.agent.memory import (
    DistilledSkill,
    SkillDistillationEvidence,
    build_skill_distillation_prompt,
    distill_skill,
    find_similar_draft,
    find_similar_draft_for_issue,
    promote_draft_to_skills,
    save_skill_draft,
    should_auto_promote,
)
from backend.core.agent.memory.protocols import (
    IShortTermMemoryStore,
    ISkillStore,
    ShortTermAttempt,
)
from backend.core.shared.interfaces.agent_runner import IContentGenerator, IProcessRunner
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AppConfig,
    AttemptResult,
    FailureType,
    IssueSummary,
)
from backend.core.use_cases.agent_candidate_fallback import effective_fallback_candidates

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SkillDistillationRequest:
    """一次发布成功后的自动提炼上下文。"""

    issue: IssueSummary
    worktree_path: Path
    config: AppConfig
    commit_result: AgentCommitResult
    selected_agent: str
    process_runner: IProcessRunner
    content_generator: IContentGenerator | None


def _try_distill_skill_after_success(request: _SkillDistillationRequest) -> None:
    """Generate an evidence-backed draft without delaying publication on failure."""
    if not request.config.memory.enabled or request.content_generator is None:
        return
    successful_attempts = [
        attempt
        for attempt in request.commit_result.attempt_results
        if attempt.failure_type is FailureType.SUCCESS
    ]
    if not successful_attempts:
        _logger.debug(
            "Skipping skill distillation for Issue #%d: no recorded successful agent attempt.",
            request.issue.number,
        )
        return

    try:
        from backend.core.agent.memory._composition import build_default_memory_services

        memory_services = build_default_memory_services(
            request.worktree_path, request.config.memory
        )
        if memory_services.short_term is None or memory_services.skill is None:
            return
        evidence = _build_skill_distillation_evidence(
            request,
            memory_services.short_term,
            memory_services.skill,
        )
        if evidence is None:
            return
        successful_agent = next(
            (attempt.agent for attempt in reversed(successful_attempts) if attempt.agent),
            request.selected_agent,
        )
        if not successful_agent or successful_agent == "auto":
            _logger.info(
                "Skipping skill distillation for Issue #%d: no concrete agent selected.",
                request.issue.number,
            )
            return
        candidate = _generate_skill_candidate(request, evidence, successful_agent)
    except Exception as exc:  # noqa: BLE001 - distillation must not block publication.
        _logger.warning(
            "Skill distillation raised for Issue #%d: %s",
            request.issue.number,
            exc,
        )
        return
    if candidate is None:
        return

    try:
        saved_path = save_skill_draft(
            candidate,
            request.config.memory,
            request.worktree_path,
            memory_services.skill,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "Failed to save skill draft for Issue #%d: %s",
            request.issue.number,
            exc,
        )
        return
    _logger.info("Distilled skill draft for Issue #%d at %s.", request.issue.number, saved_path)
    _logger.info(
        "Skill draft for Issue #%d is awaiting review; automatic generation does not count as use.",
        request.issue.number,
    )
    _try_promote_observed_skill(request, candidate, memory_services.skill)


def _effective_fallback_agent_names(config: AppConfig) -> tuple[str, ...]:
    """提炼证据里的回退顺序取**有效候选链**，按首次出现顺序去重成 agent 名单。

    候选数组非空时整体接管候选链（同 agent 换预设是另一个候选步）；直接读
    ``runner.agent_fallback_order`` 会在页面 / CLI 写候选后报告 stale 名单。
    空数组时折叠结果与旧名单逐字节一致。
    """
    ordered_agents: list[str] = []
    for candidate in effective_fallback_candidates(config):
        if candidate.agent not in ordered_agents:
            ordered_agents.append(candidate.agent)
    return tuple(ordered_agents)


def _build_skill_distillation_evidence(
    request: _SkillDistillationRequest,
    short_term_store: IShortTermMemoryStore,
    skill_store: ISkillStore,
) -> SkillDistillationEvidence | None:
    """Load durable history, final solution, committed diff, and verification evidence."""
    from backend.core.use_cases.agent_runner_worktree_create import _resolve_repo_id

    repo_id = _resolve_repo_id(request.issue, request.worktree_path)
    short_term_context = short_term_store.load(repo_id, request.issue.number)
    attempt_history = _format_distillation_attempt_history(
        short_term_context.attempts if short_term_context is not None else (),
        request.commit_result.attempt_results,
    )
    patch_result = request.process_runner.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=2",
            f"{request.config.git.remote}/{request.config.git.base_branch}...HEAD",
        ],
        cwd=request.worktree_path,
        check=False,
        timeout=30,
        label=f"Issue #{request.issue.number}: skill distillation evidence",
    )
    if patch_result.return_code != 0 or not patch_result.stdout.strip():
        _logger.info(
            "Skipping skill distillation for Issue #%d: committed diff unavailable.",
            request.issue.number,
        )
        return None

    verification_evidence = (
        "\n".join(
            f"- {' '.join(result.command)}: exit {result.return_code}"
            for result in request.commit_result.verification_results
        )
        or "No verification commands were recorded."
    )
    previous_draft = find_similar_draft_for_issue(
        request.issue,
        request.config.memory,
        skill_store,
    )
    return SkillDistillationEvidence(
        issue=request.issue,
        attempt_history=attempt_history,
        change_evidence=patch_result.stdout,
        verification_evidence=verification_evidence,
        agent_fallback_order=_effective_fallback_agent_names(request.config),
        final_solution=(short_term_context.final_solution if short_term_context else ""),
        previous_draft=previous_draft.body if previous_draft is not None else "",
    )


def _generate_skill_candidate(
    request: _SkillDistillationRequest,
    evidence: SkillDistillationEvidence,
    agent_name: str,
) -> DistilledSkill | None:
    """Run the selected agent in generate mode and validate its Markdown draft."""
    from backend.core.use_cases.generated_content import _run_content_generator

    if request.content_generator is None:
        return None
    generated_body = _run_content_generator(
        request.content_generator,
        agent_name,
        build_skill_distillation_prompt(evidence),
        request.worktree_path,
        timeout_seconds=120,
    )
    return distill_skill(request.issue, generated_body, request.config.memory)


def _try_promote_observed_skill(
    request: _SkillDistillationRequest,
    candidate: DistilledSkill,
    skill_store: ISkillStore,
) -> None:
    """Promote only when separately recorded skill-use metrics exist."""
    if not request.config.memory.auto_promote or candidate.usage_count <= 0:
        return
    try:
        existing = find_similar_draft(
            candidate,
            request.config.memory,
            request.worktree_path,
            skill_store,
        )
        if existing is None or not should_auto_promote(existing, request.config.memory):
            return
        promote_draft_to_skills(
            existing,
            request.config.memory,
            request.worktree_path,
            skill_store,
        )
    except Exception as exc:  # noqa: BLE001 - promotion remains a publication side channel.
        _logger.warning(
            "Auto-promote failed for Issue #%d: %s",
            request.issue.number,
            exc,
        )


def _format_distillation_attempt_history(
    persisted_attempts: Sequence[ShortTermAttempt],
    current_attempts: list[AttemptResult],
) -> str:
    """Combine durable Issue history with this run without duplicating its tail."""
    persisted_rows = [
        (attempt.attempt_number, attempt.failure_type, attempt.detail, attempt.recovered)
        for attempt in persisted_attempts
    ]
    current_rows = [
        (
            attempt.attempt_number,
            attempt.failure_type.value,
            attempt.detail,
            attempt.recovered,
        )
        for attempt in current_attempts
    ]
    persisted_tail = persisted_rows[-len(current_rows) :] if current_rows else []
    if current_rows and persisted_tail == current_rows:
        combined_rows = persisted_rows
    else:
        combined_rows = [*persisted_rows, *current_rows]
    if not combined_rows:
        return "No persisted attempt history."
    return "\n".join(
        f"- attempt {attempt_number} ({failure_type}; recovered={recovered}): {detail}"
        for attempt_number, failure_type, detail, recovered in combined_rows
    )
