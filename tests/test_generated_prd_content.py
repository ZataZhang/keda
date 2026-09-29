"""Tests for PRD content generation (``generated_prd_content``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    IssueSummary,
)
from backend.core.use_cases.generated_content import PrdContext
from backend.core.use_cases.generated_prd_content import (
    build_prd_context,
    generate_prd_content,
    load_prd_skill_spec,
    resolve_prd_skill_path,
)
from tests.conftest import FailingContentGenerator, FakeContentGenerator


def test_build_prd_context_collects_issue_and_comments() -> None:
    """PRD context should include issue fields and formatted comments."""
    issue = IssueSummary(number=42, title="Test", url="https://example.com", body="Body", labels=())
    context = build_prd_context(
        issue=issue,
        comments=["first", "second"],
        existing_prd_text="old prd",
        repo_path=Path("."),
    )
    assert context.issue_number == 42
    assert context.issue_title == "Test"
    assert context.issue_body == "Body"
    assert "Comment:\nfirst" in context.issue_comments
    assert "Comment:\nsecond" in context.issue_comments
    assert context.existing_prd_text == "old prd"


def test_generate_prd_content_disabled_uses_fallback() -> None:
    """When generated content is disabled, fallback PRD should be returned."""
    config = GeneratedContentConfig(enabled=False)
    context = PrdContext(
        issue_number=1,
        issue_title="T",
        issue_body="B",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
    )
    assert result.text == "fallback"
    assert result.source == "fallback"


def test_generate_prd_content_template_mode() -> None:
    """Template mode should render body_template as PRD."""
    config = GeneratedContentConfig(
        enabled=True,
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            body_template=(
                "# PRD: {issue_title}\n\n"
                "- GitHub Issue: #{issue_number}\n\n"
                "## Acceptance Checklist\n\n"
                "- [ ] item"
            ),
        ),
    )
    context = PrdContext(
        issue_number=3,
        issue_title="Feature",
        issue_body="",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
    )
    assert result.text == (
        "# PRD: Feature\n\n" "- GitHub Issue: #3\n\n" "## Acceptance Checklist\n\n" "- [ ] item"
    )
    assert result.source == "template"


def test_generate_prd_content_invalid_output_fallback() -> None:
    """Template output missing required PRD structure should fallback."""
    config = GeneratedContentConfig(
        enabled=True,
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            body_template="No structure here.",
        ),
    )
    context = PrdContext(
        issue_number=1,
        issue_title="T",
        issue_body="",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
    )
    assert result.text == "fallback"
    assert result.source == "fallback"


def test_generate_prd_content_agent_mode() -> None:
    """Agent mode should use generator output when valid."""
    generator = FakeContentGenerator(
        response=(
            "# PRD: AI Title\n\n"
            "- GitHub Issue: #1\n\n"
            "## Acceptance Checklist\n\n"
            "- [ ] AI item\n"
        )
    )
    config = GeneratedContentConfig(
        enabled=True,
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            prompt="Generate PRD for {issue_title}",
        ),
    )
    context = PrdContext(
        issue_number=1,
        issue_title="Title",
        issue_body="Body",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
        generator=generator,
        cwd=Path("."),
    )
    assert "# PRD: AI Title" in result.text
    assert result.source == "agent"


def test_generate_prd_content_agent_invalid_uses_fallback() -> None:
    """Invalid agent output should fall back."""
    generator = FakeContentGenerator(response="not a prd")
    config = GeneratedContentConfig(
        enabled=True,
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            prompt="Generate",
        ),
    )
    context = PrdContext(
        issue_number=1,
        issue_title="Title",
        issue_body="Body",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
        generator=generator,
        cwd=Path("."),
    )
    assert result.text == "fallback"
    assert result.source == "fallback"


_VALID_AGENT_PRD = "# PRD: Generated\n\n- GitHub Issue: #1\n\n## 1. Goals\n\nbody\n"


def _prd_context() -> PrdContext:
    return PrdContext(
        issue_number=1,
        issue_title="Generated",
        issue_body="Body",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="src/",
    )


def _agent_prd_config() -> GeneratedContentConfig:
    return GeneratedContentConfig(
        enabled=True,
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            agent="claude",
            prompt="You are a technical product manager. PRD for {issue_title}",
        ),
    )


def test_generate_prd_content_agent_prompt_uses_skill_spec(tmp_path: Path) -> None:
    """Agent prompt should be built from the prd skill spec (single source)."""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "# PRD Generator (Architecture-First)\n\n## Output Contract\n"
        "Follow the required PRD structure.\n",
        encoding="utf-8",
    )
    generator = FakeContentGenerator(response=_VALID_AGENT_PRD)
    result = generate_prd_content(
        config=_agent_prd_config(),
        context=_prd_context(),
        fallback_prd_text="fallback",
        generator=generator,
        cwd=tmp_path,
        prd_skill_path=skill_file,
    )
    assert result.source == "agent"
    assert result.text == _VALID_AGENT_PRD.strip()
    # The captured prompt embeds the skill spec, not the hardcoded template.
    sent_prompt = generator.prompts[0]
    assert "PRD Generator (Architecture-First)" in sent_prompt
    assert "Output Contract" in sent_prompt
    assert "technical product manager" not in sent_prompt
    # And it still carries the PRD input context.
    assert "GitHub Issue #1: Generated" in sent_prompt


def test_generate_prd_content_agent_prompt_falls_back_when_skill_missing(
    tmp_path: Path,
) -> None:
    """When the skill is unreachable, the agent prompt falls back to target.prompt."""
    missing_skill = tmp_path / "nope" / "SKILL.md"
    generator = FakeContentGenerator(response=_VALID_AGENT_PRD)
    result = generate_prd_content(
        config=_agent_prd_config(),
        context=_prd_context(),
        fallback_prd_text="fallback",
        generator=generator,
        cwd=tmp_path,
        prd_skill_path=missing_skill,
    )
    assert result.source == "agent"
    sent_prompt = generator.prompts[0]
    assert "technical product manager" in sent_prompt
    assert "PRD Generator (Architecture-First)" not in sent_prompt


def test_load_prd_skill_spec_reads_and_handles_missing(tmp_path: Path) -> None:
    """load_prd_skill_spec reads an explicit path and returns None when unreachable."""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("spec body", encoding="utf-8")
    assert load_prd_skill_spec(skill_file) == "spec body"
    assert load_prd_skill_spec(tmp_path / "missing.md") is None


def test_resolve_prd_skill_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式覆盖、环境变量与模板用户级目录按优先级解析。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    explicit = tmp_path / "explicit.md"
    assert resolve_prd_skill_path(explicit) == explicit
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(tmp_path / "env.md"))
    assert resolve_prd_skill_path() == tmp_path / "env.md"
    monkeypatch.delenv("IAR_PRD_SKILL_PATH", raising=False)
    configured_skill_path = tmp_path / "configured-skills" / "prd" / "SKILL.md"
    configured_skill_path.parent.mkdir(parents=True)
    configured_skill_path.write_text("configured", encoding="utf-8")
    monkeypatch.setenv("CC_SWITCH_SKILLS_DIR", str(tmp_path / "configured-skills"))
    assert resolve_prd_skill_path() == configured_skill_path
    monkeypatch.delenv("CC_SWITCH_SKILLS_DIR", raising=False)

    codex_skill_path = tmp_path / ".codex" / "skills" / "prd" / "SKILL.md"
    claude_skill_path = tmp_path / ".claude" / "skills" / "prd" / "SKILL.md"
    kimi_code_skill_path = tmp_path / ".kimi-code" / "skills" / "prd" / "SKILL.md"
    for skill_path in (
        codex_skill_path,
        claude_skill_path,
        kimi_code_skill_path,
    ):
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_path.parent.parent.parent.name, encoding="utf-8")

    assert resolve_prd_skill_path() == codex_skill_path
    codex_skill_path.unlink()
    assert resolve_prd_skill_path() == claude_skill_path
    claude_skill_path.unlink()
    assert resolve_prd_skill_path() == kimi_code_skill_path
    kimi_code_skill_path.unlink()
    # 全部缺失时回落到注册表首个 agent 的候选路径。
    assert resolve_prd_skill_path() == codex_skill_path


def test_generate_prd_content_agent_fallback_to_template() -> None:
    """Agent failure with fallback=template should render template before hard fallback."""
    generator = FakeContentGenerator(response="invalid")
    config = GeneratedContentConfig(
        enabled=True,
        fallback="template",
        prd_from_issue=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            body_template=(
                "# PRD: {issue_title}\n\n"
                "- GitHub Issue: #{issue_number}\n\n"
                "## Acceptance Checklist\n\n"
                "- [ ] template item"
            ),
            prompt="Generate",
        ),
    )
    context = PrdContext(
        issue_number=1,
        issue_title="Title",
        issue_body="Body",
        issue_comments="",
        existing_prd_text="",
        repo_structure_summary="",
    )
    result = generate_prd_content(
        config=config,
        context=context,
        fallback_prd_text="fallback",
        generator=generator,
        cwd=Path("."),
    )
    assert "- [ ] template item" in result.text
    assert result.source == "template"


def test_generate_prd_content_agent_timeout_uses_fallback() -> None:
    """agent 超时不得抛出：没有可用模板时退回 fallback PRD，而不是中断 rework-prd。"""
    generator = FailingContentGenerator(subprocess.TimeoutExpired(cmd="claude", timeout=120))
    result = generate_prd_content(
        config=_agent_prd_config(),
        context=_prd_context(),
        fallback_prd_text="fallback",
        generator=generator,
        cwd=Path("."),
    )
    assert len(generator.calls) == 1
    assert result.text == "fallback"
    assert result.source == "fallback"
