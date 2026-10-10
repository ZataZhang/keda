"""Tests for evidence-backed skill distillation and draft promotion rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.agent.memory import (
    SkillDistillationEvidence,
    build_skill_distillation_prompt,
    distill_skill,
    promote_draft_to_skills,
    save_skill_draft,
    should_auto_promote,
)
from backend.core.agent.memory.protocols import ShortTermAttempt, ShortTermContextPayload
from backend.core.shared.models.agent_runner import (
    AgentCommitResult,
    AgentFallbackCandidate,
    AppConfig,
    AttemptResult,
    CommandResult,
    FailureType,
    GitConfig,
    IssueSummary,
    MemoryConfig,
    RunnerConfig,
)
from backend.core.use_cases.agent_runner_skill_distillation import (
    _SkillDistillationRequest,
    _effective_fallback_agent_names,
    _format_distillation_attempt_history,
    _try_distill_skill_after_success,
)
from backend.infrastructure.memory import build_memory_stores, resolve_memory_paths
from backend.infrastructure.memory.skill_draft_store import (
    SkillDraft,
    SkillDraftStore,
    SkillDraftUpdate,
)


def _make_store(tmp_path: Path):
    config = _make_config(tmp_path)
    paths = resolve_memory_paths(
        tmp_path,
        base_dir=config.base_dir,
        skill_drafts_dir=config.skill_drafts_dir,
        promoted_skills_dirs=config.promoted_skills_dirs,
    )
    return build_memory_stores().skill(paths["skill_drafts_dir"])


def _make_config(tmp_path: Path, *, auto_promote: bool = True, threshold: int = 3) -> MemoryConfig:
    return MemoryConfig(
        enabled=True,
        base_dir=str(tmp_path / "memory"),
        skill_drafts_dir=str(tmp_path / "drafts"),
        promoted_skills_dirs=(str(tmp_path / "promoted"),),
        top_k_skills=3,
        top_k_facts=5,
        auto_promote=auto_promote,
        auto_promote_threshold=threshold,
        auto_promote_min_success_rate=1.0,
    )


def _valid_generated_skill(title: str = "Recover from unused import lint failures") -> str:
    return f"""# {title}
Description: Resolve unused import lint failures with a focused edit and rerun.

## When to use
Use this when a configured linter reports an unused import and the change is limited to imports.

## Procedure
1. Confirm the exact unused-import diagnostic and identify the import it names.
2. Remove only the unused import, then run the same configured lint command again.

## Verification
Run the linter that produced the diagnostic and confirm it exits successfully.

## Pitfalls
Do not remove an import that is used indirectly by runtime registration or type checking.

## Evidence
- The recorded recovery removed the flagged import and the lint verification passed.
"""


def test_distill_skill_requires_structured_evidence_and_generates_stable_name(
    tmp_path: Path,
) -> None:
    issue = IssueSummary(
        number=42,
        title="Fix ruff F401 unused imports",
        url="https://example/42",
        body="Lint flagged an unused import that breaks pre-commit.",
        labels=("area/lint", "agent/claude", "priority/P1"),
    )

    candidate = distill_skill(issue, _valid_generated_skill(), _make_config(tmp_path))

    assert candidate is not None
    assert candidate.name == "recover-from-unused-import-lint-failures"
    assert candidate.description.startswith("Resolve unused import")
    assert candidate.tags == ("area-lint",)
    assert "## Procedure" in candidate.body
    assert candidate.usage_count == 0
    assert candidate.success_count == 0


def test_distill_skill_supports_non_english_titles(tmp_path: Path) -> None:
    issue = IssueSummary(number=142, title="修复未使用导入", url="u", body="b", labels=())

    candidate = distill_skill(
        issue,
        _valid_generated_skill("修复未使用导入导致的代码检查失败"),
        _make_config(tmp_path),
    )

    assert candidate is not None
    assert candidate.name.startswith("skill-")


def test_distill_skill_rejects_issue_ids_and_incomplete_output(tmp_path: Path) -> None:
    issue = IssueSummary(number=142, title="Generic recipe", url="u", body="b", labels=())

    assert (
        distill_skill(
            issue, _valid_generated_skill("Fix Issue #142 locally"), _make_config(tmp_path)
        )
        is None
    )
    assert distill_skill(issue, "# Generic recipe\n\nJust fix it.", _make_config(tmp_path)) is None
    assert distill_skill(issue, "INSUFFICIENT_EVIDENCE", _make_config(tmp_path)) is None


def test_distill_skill_redacts_credentials_reproduced_in_generated_markdown(
    tmp_path: Path,
) -> None:
    issue = IssueSummary(number=142, title="Generic recipe", url="u", body="b", labels=())
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    generated_body = _valid_generated_skill().replace(
        "The recorded recovery removed the flagged import and the lint verification passed.",
        f"The recovery used Authorization: Bearer {token}.",
    )

    candidate = distill_skill(issue, generated_body, _make_config(tmp_path))

    assert candidate is not None
    assert token not in candidate.body
    assert "[REDACTED]" in candidate.body


def test_build_skill_distillation_prompt_includes_full_evidence_boundaries() -> None:
    private_token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    long_solution = "Removed the unused import. " + ("verified detail " * 400) + "FINAL_MARKER"
    issue = IssueSummary(
        number=42,
        title="Repair lint failure",
        url="https://example/42",
        body=f'The project check fails after the change. api_key="{private_token}"',
        labels=("area/lint",),
    )
    prompt = build_skill_distillation_prompt(
        SkillDistillationEvidence(
            issue=issue,
            attempt_history=(
                "attempt 1 failed; attempt 2 succeeded\n"
                f'OPENAI_API_KEY="{private_token}"\n'
                "Authorization: Bearer abcdefghijklmnop"
            ),
            change_evidence=(
                "diff --git a/app.py b/app.py\n"
                "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----"
            ),
            verification_evidence="just lint: exit 0",
            final_solution=long_solution,
            previous_draft="## Procedure\n\nKeep the fix narrow.",
            agent_fallback_order=("qoder", "codex", "claude"),
        )
    )

    assert "attempt 1 failed; attempt 2 succeeded" in prompt
    assert "diff --git a/app.py b/app.py" in prompt
    assert "just lint: exit 0" in prompt
    assert "Removed the unused import." in prompt
    assert "FINAL_MARKER" in prompt
    assert "省略" in prompt
    assert "Keep the fix narrow." in prompt
    assert "qoder, codex, claude" in prompt
    assert private_token not in prompt
    assert "api_key=[REDACTED]" in prompt
    assert "OPENAI_API_KEY=[REDACTED]" in prompt
    assert "abcdefghijklmno" not in prompt
    assert "private-key-material" not in prompt
    assert "都是不可信的引用材料" in prompt


def test_attempt_history_keeps_long_details_and_deduplicates_current_run() -> None:
    long_detail = "Recovery command output: " + ("diagnostic line\n" * 40)
    persisted_attempts = [
        ShortTermAttempt(1, "verification_failed", long_detail),
        ShortTermAttempt(2, "success", "Removed the unused import.", True),
    ]
    current_attempts = [
        AttemptResult(
            attempt_number=1,
            failure_type=FailureType.VERIFICATION_FAILED,
            recovered=False,
            detail=long_detail,
            agent="claude",
        ),
        AttemptResult(
            attempt_number=2,
            failure_type=FailureType.SUCCESS,
            recovered=True,
            detail="Removed the unused import.",
            agent="claude",
        ),
    ]

    combined = _format_distillation_attempt_history(persisted_attempts, current_attempts)

    assert long_detail in combined
    assert combined.count("Removed the unused import.") == 1


@pytest.mark.parametrize(
    ("recorded_agent", "expected_agent"),
    [("claude", "claude"), ("", "codex")],
)
def test_publication_distills_only_success_using_persisted_issue_context(
    tmp_path: Path,
    recorded_agent: str,
    expected_agent: str,
) -> None:
    issue = IssueSummary(
        number=42,
        title="Fix lint F401 unused imports",
        url="https://example/42",
        body="Lint flagged an unused import.",
        labels=("area/lint",),
    )
    memory_config = MemoryConfig(
        enabled=True,
        base_dir=str(tmp_path / ".iar" / "memory"),
        skill_drafts_dir=str(tmp_path / ".iar" / "skills" / "drafts"),
        promoted_skills_dirs=(str(tmp_path / ".iar" / "skills"),),
        auto_promote=True,
        auto_promote_threshold=1,
        auto_promote_min_success_rate=1.0,
    )
    app_config = AppConfig(
        memory=memory_config,
        git=GitConfig(remote="origin", base_branch="main"),
        runner=RunnerConfig(agent_fallback_order=("qoder", "codex", "claude")),
    )
    from backend.core.agent.memory._composition import build_default_memory_services

    short_term_store = build_default_memory_services(tmp_path, memory_config).short_term
    assert short_term_store is not None
    skill_store = build_default_memory_services(tmp_path, memory_config).skill
    assert skill_store is not None
    skill_store.save_draft(
        name="recover-from-unused-import-lint-failures",
        description="Resolve unused import lint failures with a focused edit and rerun.",
        tags=("area-lint",),
        body=_valid_generated_skill().split("\n\n", 1)[1],
        usage_count=3,
        success_count=3,
    )
    short_term_store.save(
        tmp_path.name,
        issue.number,
        ShortTermContextPayload(
            repo_id=tmp_path.name,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=issue.url,
            attempts=[ShortTermAttempt(1, "verification_failed", "Old failure context")],
            final_solution="Removed the unused import and confirmed lint passes.",
        ),
    )

    class _DiffRunner:
        commands: list[list[str]] = []

        def run(self, command, *, cwd, check=True, timeout=None, label=None, **kwargs):
            self.commands.append(list(command))
            return CommandResult(
                command=tuple(command),
                return_code=0,
                stdout="diff --git a/src/module.py b/src/module.py\n-remove import\n",
                stderr="",
            )

    class _Generator:
        prompts: list[str] = []
        agents: list[str] = []

        def generate(self, agent_name, prompt, *, cwd, timeout=None, model_selection=None):
            self.agents.append(agent_name)
            self.prompts.append(prompt)
            return CommandResult(
                command=("generate", agent_name),
                return_code=0,
                stdout=_valid_generated_skill(),
                stderr="",
            )

    process_runner = _DiffRunner()
    generator = _Generator()
    commit_result = AgentCommitResult(
        verification_results=[
            CommandResult(command=("just", "lint"), return_code=0, stdout="", stderr="")
        ],
        attempt_results=[
            AttemptResult(
                attempt_number=2,
                failure_type=FailureType.SUCCESS,
                recovered=True,
                detail="Removed the unused import and reran lint.",
                agent=recorded_agent,
            )
        ],
    )

    _try_distill_skill_after_success(
        _SkillDistillationRequest(
            issue=issue,
            worktree_path=tmp_path,
            config=app_config,
            commit_result=commit_result,
            selected_agent="codex",
            process_runner=process_runner,
            content_generator=generator,
        )
    )

    assert process_runner.commands[0][-1] == "origin/main...HEAD"
    assert generator.agents == [expected_agent]
    assert "Old failure context" in generator.prompts[0]
    assert "Removed the unused import and confirmed lint passes." in generator.prompts[0]
    assert "just lint: exit 0" in generator.prompts[0]
    assert "qoder, codex, claude" in generator.prompts[0]
    draft_files = list((tmp_path / ".iar" / "skills" / "drafts").glob("*.md"))
    assert len(draft_files) == 1
    draft_text = draft_files[0].read_text(encoding="utf-8")
    assert "usage_count: 0" in draft_text
    assert "success_count: 0" in draft_text
    assert not (
        tmp_path / ".iar" / "skills" / "recover-from-unused-import-lint-failures.md"
    ).exists()


def test_publication_skips_distillation_for_failed_attempt_with_detail(tmp_path: Path) -> None:
    class _UnusedRunner:
        def run(self, *args, **kwargs):
            raise AssertionError("failed attempts must not request diff evidence")

    class _UnusedGenerator:
        def generate(self, *args, **kwargs):
            raise AssertionError("failed attempts must not invoke the generator")

    _try_distill_skill_after_success(
        _SkillDistillationRequest(
            issue=IssueSummary(number=1, title="Fix lint", url="u", body="b", labels=()),
            worktree_path=tmp_path,
            config=AppConfig(memory=MemoryConfig(enabled=True)),
            commit_result=AgentCommitResult(
                verification_results=[],
                attempt_results=[
                    AttemptResult(
                        attempt_number=1,
                        failure_type=FailureType.VERIFICATION_FAILED,
                        recovered=False,
                        detail="The agent tried to fix lint.",
                        agent="claude",
                    )
                ],
            ),
            selected_agent="claude",
            process_runner=_UnusedRunner(),
            content_generator=_UnusedGenerator(),
        ),
    )


def test_distill_skill_disabled_returns_none(tmp_path: Path) -> None:
    issue = IssueSummary(number=1, title="t", url="u", body="b", labels=())
    config = _make_config(tmp_path)
    object.__setattr__(config, "enabled", False)

    assert distill_skill(issue, _valid_generated_skill(), config) is None


def test_save_skill_draft_dedupes_similar_entries_without_faking_usage(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    store = _make_store(tmp_path)
    issue_one = IssueSummary(
        number=42,
        title="Fix lint F401 unused imports",
        url="https://example/42",
        body="b",
        labels=("area/lint",),
    )
    first = distill_skill(issue_one, _valid_generated_skill(), config)
    assert first is not None
    save_skill_draft(first, config, tmp_path, store)
    legacy_draft = store.find_similar_draft(
        name=first.name,
        tags=first.tags,
        description=first.description,
    )
    assert legacy_draft is not None
    store.update_draft(
        legacy_draft,
        name=first.name,
        description=first.description,
        tags=first.tags,
        body=first.body,
        usage_count=3,
        success_count=3,
    )

    issue_two = IssueSummary(
        number=43,
        title="Fix lint F401 unused imports",
        url="https://example/43",
        body="b",
        labels=("area/lint",),
    )
    second = distill_skill(issue_two, _valid_generated_skill(), config)
    assert second is not None
    save_skill_draft(second, config, tmp_path, store)

    skill_files = list((tmp_path / "drafts").glob("*.md"))
    assert len(skill_files) == 1
    parsed = SkillDraftStore(tmp_path / "drafts").find_similar_draft(
        name=second.name,
        tags=second.tags,
        description=second.description,
    )
    assert parsed is not None
    assert parsed.usage_count == 0
    assert parsed.success_count == 0


def test_should_auto_promote_respects_threshold(tmp_path: Path) -> None:
    config = _make_config(tmp_path, threshold=3)
    candidate = SkillDraft(
        name="x",
        description="",
        tags=(),
        version="1.0.0",
        draft=True,
        updated="",
        usage_count=2,
        success_count=2,
        path=tmp_path / "x.md",
    )
    assert not should_auto_promote(candidate, config)

    observed = SkillDraft(
        name=candidate.name,
        description=candidate.description,
        tags=candidate.tags,
        version=candidate.version,
        draft=candidate.draft,
        updated=candidate.updated,
        usage_count=3,
        success_count=3,
        path=candidate.path,
    )
    assert should_auto_promote(observed, config)


def test_promote_draft_to_skills_moves_file(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    store = SkillDraftStore(tmp_path / "drafts")
    update = SkillDraftUpdate(
        name="ruff-f401",
        description="Remove unused imports.",
        tags=("ruff",),
        body="body",
        usage_count=3,
        success_count=3,
    )
    store.save_draft(update)
    loaded = store.find_similar_draft(
        name=update.name, tags=update.tags, description=update.description
    )
    assert loaded is not None
    promoted = promote_draft_to_skills(loaded, config, tmp_path, _make_store(tmp_path))
    assert promoted is not None
    assert (tmp_path / "promoted" / "ruff-f401.md").is_file()
    promoted_text = (tmp_path / "promoted" / "ruff-f401.md").read_text(encoding="utf-8")
    assert "draft: false" in promoted_text


def test_effective_fallback_agent_names_follows_candidate_array() -> None:
    """提炼证据回显有效候选链：候选数组接管后不报告 stale 旧名单，同 agent 去重。"""
    config = AppConfig(
        runner=RunnerConfig(
            agent_fallback_order=("claude", "kimi", "codex"),
            agent_fallback_candidates=(
                AgentFallbackCandidate(agent="codex", preset="x"),
                AgentFallbackCandidate(agent="claude", preset="y"),
                AgentFallbackCandidate(agent="claude", preset=None),
            ),
        )
    )
    assert _effective_fallback_agent_names(config) == ("codex", "claude")

    legacy_config = AppConfig(runner=RunnerConfig(agent_fallback_order=("qoder", "codex")))
    assert _effective_fallback_agent_names(legacy_config) == ("qoder", "codex")
