"""Tests for generated content use case."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    CommandResult,
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
)
from backend.core.use_cases.generated_content import (
    IssueContext,
    PrContext,
    _parse_json_output,
    _parse_markdown_output,
    _strip_markdown_preamble,
    _validate_issue_body,
    _validate_pr_body,
    build_issue_context,
    extract_first_h2_section,
    generate_issue_content,
    generate_pr_content,
)
from tests.conftest import FailingContentGenerator, FakeContentGenerator, FakeProcessRunner


def test_validate_issue_body_passes_with_anchor() -> None:
    """Issue body with PRD path anchor should be valid."""
    body = "Some text\n\n- PRD path: `tasks/pending/example.md`\n"
    assert _validate_issue_body(body, "tasks/pending/example.md") is True


def test_validate_issue_body_fails_without_anchor() -> None:
    """Issue body missing PRD path anchor should be invalid."""
    assert _validate_issue_body("No anchor here.", "tasks/pending/example.md") is False


def test_validate_issue_body_fails_empty() -> None:
    """Empty Issue body should be invalid."""
    assert _validate_issue_body("", "tasks/pending/example.md") is False


def test_validate_pr_body_passes_with_closes() -> None:
    """PR body with Closes anchor should be valid."""
    assert _validate_pr_body("Closes #42\n\nSome description.", 42) is True


def test_validate_pr_body_fails_without_closes() -> None:
    """PR body missing Closes anchor should be invalid."""
    assert _validate_pr_body("No anchor here.", 42) is False


def test_validate_pr_body_fails_empty() -> None:
    """Empty PR body should be invalid."""
    assert _validate_pr_body("", 42) is False


def test_parse_json_output_extracts_title_and_body() -> None:
    """JSON output with title and body keys should be parsed."""
    title, body = _parse_json_output('{"title": "T", "body": "B"}')
    assert title == "T"
    assert body == "B"


def test_parse_json_output_returns_empty_for_invalid() -> None:
    """Invalid JSON should return empty strings."""
    title, body = _parse_json_output("not json")
    assert title == ""
    assert body == ""


def test_parse_markdown_output_extracts_title() -> None:
    """First non-empty line should become title."""
    title, body = _parse_markdown_output("# Title\n\nBody text.")
    assert title == "Title"
    assert body == "# Title\n\nBody text."


def test_generate_issue_content_disabled_uses_fallback() -> None:
    """When generated content is disabled, fallback should be returned."""
    config = GeneratedContentConfig(enabled=False)
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="- [ ] Item",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback Title",
        fallback_body="Fallback Body",
    )
    assert result.title == "Fallback Title"
    assert result.body == "Fallback Body"
    assert result.source == "fallback"


def test_generate_issue_content_template_mode() -> None:
    """Template mode should render configured templates."""
    config = GeneratedContentConfig(
        enabled=True,
        issue_from_prd=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="[{issue_type}] {prd_title}",
            body_template="## Summary\n\n{prd_introduction}\n\n- PRD path: `{relative_prd_path}`",
        ),
    )
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="- [ ] Item",
        prd_text="",
        prd_introduction="Intro text",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
    )
    assert result.title == "[feature] PRD Title"
    assert "- PRD path: `tasks/example.md`" in result.body
    assert result.source == "template"


def test_generate_issue_content_template_missing_anchor_fallback() -> None:
    """Template output missing PRD path anchor should fallback."""
    config = GeneratedContentConfig(
        enabled=True,
        issue_from_prd=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="Title",
            body_template="No anchor here.",
        ),
    )
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback Title",
        fallback_body="Fallback Body",
    )
    assert result.title == "Fallback Title"
    assert result.body == "Fallback Body"
    assert result.source == "fallback"


def test_generate_issue_content_agent_mode_json() -> None:
    """Agent mode with JSON output should parse and validate."""
    generator = FakeContentGenerator(
        response='{"title": "AI Title", "body": "- PRD path: `tasks/example.md`\\n\\nDetails."}'
    )
    config = GeneratedContentConfig(
        enabled=True,
        issue_from_prd=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="json",
            prompt="Generate Issue for {relative_prd_path}",
        ),
    )
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert result.title == "AI Title"
    assert "- PRD path: `tasks/example.md`" in result.body
    assert result.source == "agent"


def test_generate_issue_content_agent_invalid_json_fallback() -> None:
    """Agent mode with invalid JSON should fallback."""
    generator = FakeContentGenerator(response="not json")
    config = GeneratedContentConfig(
        enabled=True,
        issue_from_prd=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="json",
            prompt="Generate Issue",
        ),
    )
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert result.title == "Fallback"
    assert result.body == "Fallback Body"
    assert result.source == "fallback"


def test_generate_issue_content_agent_fallback_to_template() -> None:
    """Agent failure with fallback=template should render templates before hard fallback."""
    generator = FakeContentGenerator(response="not json")
    config = GeneratedContentConfig(
        enabled=True,
        fallback="template",
        issue_from_prd=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="json",
            title_template="[Template] {prd_title}",
            body_template="- PRD path: `{relative_prd_path}`\n\nTemplate body.",
            prompt="Generate Issue",
        ),
    )
    context = IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )
    result = generate_issue_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert result.title == "[Template] PRD Title"
    assert "- PRD path: `tasks/example.md`" in result.body
    assert "Template body." in result.body
    assert result.source == "template"


def test_generate_pr_content_disabled_uses_fallback() -> None:
    """When PR generation is disabled, fallback should be returned."""
    config = GeneratedContentConfig(enabled=False)
    context = PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="commit 1",
        commit_messages="commit 1",
        diff_stat="1 file changed",
        git_diff_stat="1 file changed",
    )
    result = generate_pr_content(
        config=config,
        context=context,
        fallback_title="Fallback Title",
        fallback_body="Fallback Body",
    )
    assert result.title == "Fallback Title"
    assert result.body == "Fallback Body"
    assert result.source == "fallback"


def test_generate_pr_content_template_mode() -> None:
    """Template mode should render PR templates."""
    config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="[Agent] {issue_title}",
            body_template="Closes #{issue_number}\n\n{diff_stat}",
        ),
    )
    context = PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="commit 1",
        commit_messages="commit 1",
        diff_stat="1 file changed",
        git_diff_stat="1 file changed",
    )
    result = generate_pr_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
    )
    assert result.title == "[Agent] Title"
    assert "Closes #42" in result.body
    assert result.source == "template"


def test_generate_pr_content_missing_closes_fallback() -> None:
    """PR body missing Closes anchor should fallback."""
    config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="template",
            title_template="Title",
            body_template="No closes here.",
        ),
    )
    context = PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="",
        commit_messages="",
        diff_stat="",
        git_diff_stat="",
    )
    result = generate_pr_content(
        config=config,
        context=context,
        fallback_title="Fallback Title",
        fallback_body="Fallback Body",
    )
    assert result.title == "Fallback Title"
    assert result.body == "Fallback Body"
    assert result.source == "fallback"


def test_generate_pr_content_agent_mode_markdown() -> None:
    """Agent mode with markdown output should parse and validate."""
    generator = FakeContentGenerator(
        response="Closes #42\n\n## Summary\n\nThis PR implements the feature."
    )
    config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            prompt="Generate PR for #{issue_number}",
        ),
    )
    context = PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="",
        commit_messages="",
        diff_stat="",
        git_diff_stat="",
    )
    result = generate_pr_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    # 首行 ``Closes #42`` 是正文锚点而非标题，标题落到 fallback_title。
    assert result.title == "Fallback"
    assert "Closes #42" in result.body
    assert result.source == "agent"


def test_generate_pr_content_agent_fallback_to_template() -> None:
    """Agent failure with fallback=template should render PR templates before hard fallback."""
    generator = FakeContentGenerator(response="invalid markdown")
    config = GeneratedContentConfig(
        enabled=True,
        fallback="template",
        draft_pr=GeneratedContentTargetConfig(
            enabled=True,
            mode="agent",
            output="markdown",
            title_template="[Template] {issue_title}",
            body_template="Closes #{issue_number}\n\nTemplate PR body.",
            prompt="Generate PR",
        ),
    )
    context = PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="",
        commit_messages="",
        diff_stat="",
        git_diff_stat="",
    )
    result = generate_pr_content(
        config=config,
        context=context,
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert result.title == "[Template] Title"
    assert "Closes #42" in result.body
    assert "Template PR body." in result.body
    assert result.source == "template"


def _pr_context() -> PrContext:
    """最小 PR 上下文：Issue #42，标题 ``Title``。"""
    return PrContext(
        issue_number=42,
        issue_title="Title",
        issue_body="Body",
        branch="issue-42",
        base_branch="main",
        commit_log="",
        commit_messages="",
        diff_stat="",
        git_diff_stat="",
    )


def _issue_context() -> IssueContext:
    """最小 Issue 上下文：PRD 路径 ``tasks/example.md``，标题 ``PRD Title``。"""
    return IssueContext(
        issue_type="feature",
        title="Title",
        prd_title="PRD Title",
        relative_prd_path="tasks/example.md",
        acceptance_items="",
        prd_text="",
        prd_introduction="",
        prd_goals="",
        prd_requirement_shape="",
        prd_change_impact_tree="",
    )


def _generate_agent_pr_content(agent_output: str, *, output: str):
    """用 ``FakeContentGenerator`` 回放 ``agent_output``，走 agent 模式的 PR 内容生成。"""
    config = GeneratedContentConfig(
        enabled=True,
        draft_pr=GeneratedContentTargetConfig(
            enabled=True, mode="agent", output=output, prompt="Generate PR"
        ),
    )
    return generate_pr_content(
        config=config,
        context=_pr_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=FakeContentGenerator(response=agent_output),
        cwd=Path("."),
    )


def test_generate_pr_content_markdown_keeps_real_title_before_closes() -> None:
    """首行是真实标题（Closes 在后）时标题原样保留，只有纯 closing 引用行才作废。"""
    generated_pr_content = _generate_agent_pr_content(
        "# Add PR contract\n\nCloses #42\n\n## Summary\n\nDone.", output="markdown"
    )
    assert generated_pr_content.title == "Add PR contract"
    assert generated_pr_content.source == "agent"


@pytest.mark.parametrize(
    ("agent_title", "expected_title"),
    [
        ("Closes #42", "Fallback"),
        ("closes: #42.", "Fallback"),
        ("Fixes #42", "Fallback"),
        ("Resolved #42", "Fallback"),
        ("Fix #42 crash on startup", "Fix #42 crash on startup"),
        ("[Agent] Closes #42 follow-up", "[Agent] Closes #42 follow-up"),
    ],
)
def test_generate_pr_content_closing_reference_title_uses_fallback_title(
    agent_title: str, expected_title: str
) -> None:
    """只含 closing keyword + Issue 引用的标题作废，夹带其他文字的标题保留。"""
    agent_json_output = json.dumps({"title": agent_title, "body": "Closes #42\n\nBody."})
    generated_pr_content = _generate_agent_pr_content(agent_json_output, output="json")
    assert generated_pr_content.title == expected_title
    assert generated_pr_content.body == "Closes #42\n\nBody."
    assert generated_pr_content.source == "agent"


def test_strip_markdown_preamble_removes_lead_and_horizontal_rule() -> None:
    """开场白与水平分隔线被剥离，正文从 ``Closes`` 锚点开始。"""
    raw_output = "Here is the draft PR description:\n\n---\n\nCloses #42\n\n## Summary\n\nDone."

    assert _strip_markdown_preamble(raw_output) == "Closes #42\n\n## Summary\n\nDone."


def test_strip_markdown_preamble_unwraps_outer_code_fence() -> None:
    """开场白 + 外层 ```markdown 围栏被一起剥离。"""
    raw_output = (
        "Here is the draft PR body:\n\n```markdown\nCloses #42\n\n## Summary\n\nDone.\n```\n"
    )

    assert _strip_markdown_preamble(raw_output) == "Closes #42\n\n## Summary\n\nDone."


def test_strip_markdown_preamble_keeps_real_title() -> None:
    """首行是真实标题（无开场白）时原样保留，不受剥离逻辑影响。"""
    raw_output = "Add PR contract\n\nCloses #42\n\n## Summary\n\nDone."

    assert _strip_markdown_preamble(raw_output) == raw_output


def test_generate_pr_content_markdown_preamble_uses_fallback_title() -> None:
    """agent 以 ``Here is the draft PR description:`` 开场时，标题回落 fallback。"""
    generated_pr_content = _generate_agent_pr_content(
        "Here is the draft PR description:\n\n---\n\nCloses #42\n\n## Summary\n\nDone.",
        output="markdown",
    )

    assert generated_pr_content.title == "Fallback"
    assert generated_pr_content.source == "agent"
    assert "Here is the draft PR description:" not in generated_pr_content.body
    assert generated_pr_content.body.startswith("Closes #42")


def test_generate_pr_content_markdown_preamble_with_fence_uses_fallback_title() -> None:
    """开场白 + ```markdown 围栏时，标题回落 fallback，正文去掉开场白与围栏。"""
    generated_pr_content = _generate_agent_pr_content(
        "Here is the draft PR body:\n\n```markdown\nCloses #42\n\n## Summary\n\nDone.\n```\n",
        output="markdown",
    )

    assert generated_pr_content.title == "Fallback"
    assert generated_pr_content.source == "agent"
    assert "Here is the draft PR body:" not in generated_pr_content.body
    assert "```" not in generated_pr_content.body
    assert generated_pr_content.body.startswith("Closes #42")


def test_build_issue_context_extracts_sections() -> None:
    """Issue context should extract PRD sections correctly."""
    prd_text = """# PRD: Example

## Introduction

This is the intro.

## Goals

- Goal 1
- Goal 2

## Acceptance Checklist

- [x] done
"""
    context = build_issue_context(
        issue_type="feature",
        title="[Feature] Example",
        relative_prd_path=Path("tasks/example.md"),
        prd_text=prd_text,
        acceptance_items=["- [x] done"],
    )
    assert context.issue_type == "feature"
    assert context.prd_title == "Example"
    assert context.relative_prd_path == "tasks/example.md"
    assert context.prd_introduction == "This is the intro."
    assert "Goal 1" in context.prd_goals


def test_extract_first_h2_section_extracts_first_section() -> None:
    """extract_first_h2_section should return content under the first ## heading."""
    prd_text = """# PRD: Example

## 1. Background and Goals

First section content.

## 2. Requirements

Second section content.
"""
    assert extract_first_h2_section(prd_text) == "First section content."


def test_extract_first_h2_section_returns_empty_when_no_h2() -> None:
    """extract_first_h2_section should return empty string when there is no ## heading."""
    assert extract_first_h2_section("# Only H1\n\nSome text.") == ""


def test_build_issue_context_falls_back_to_first_h2_section() -> None:
    """When no known introduction keyword matches, use the first ## section as introduction."""
    prd_text = """# PRD: Example

## 1. 背景与目标

This should be used as introduction.

## 2. 需求形态

Requirements here.
"""
    context = build_issue_context(
        issue_type="feature",
        title="[Feature] Example",
        relative_prd_path=Path("tasks/example.md"),
        prd_text=prd_text,
        acceptance_items=[],
    )
    assert context.prd_introduction == "This should be used as introduction."
    assert "Requirements here." in context.prd_requirement_shape


def test_build_pr_context_collects_git_info() -> None:
    """PR context should collect commit log and diff stat."""
    worktree_path = Path("/tmp/fake-worktree")
    process_runner = FakeProcessRunner(
        responses={
            ("git", "log", "main..HEAD", "--pretty=format:%s"): CommandResult(
                command=("git", "log", "main..HEAD", "--pretty=format:%s"),
                return_code=0,
                stdout="commit one\ncommit two",
                stderr="",
            ),
            ("git", "diff", "--stat", "main...HEAD"): CommandResult(
                command=("git", "diff", "--stat", "main...HEAD"),
                return_code=0,
                stdout="1 file changed, 10 insertions",
                stderr="",
            ),
        }
    )
    from backend.core.shared.models.agent_runner import IssueSummary

    issue = IssueSummary(number=42, title="Test", url="https://example.com", body="Body", labels=())
    from backend.core.use_cases.generated_content import build_pr_context

    target_config = GeneratedContentTargetConfig(include_commit_log=True, include_diff_stat=True)
    context = build_pr_context(
        issue=issue,
        branch="issue-42",
        base_branch="main",
        worktree_path=worktree_path,
        process_runner=process_runner,
        target_config=target_config,
    )
    assert context.issue_number == 42
    assert context.issue_title == "Test"
    assert context.commit_log == "commit one\ncommit two"
    assert context.commit_messages == "commit one\ncommit two"
    assert context.diff_stat == "1 file changed, 10 insertions"
    assert context.git_diff_stat == "1 file changed, 10 insertions"


def test_resolve_generation_agent_auto_resolves_to_claude() -> None:
    """auto/auto 收敛到 claude；显式 target / 默认值按优先级生效。"""
    from backend.core.use_cases.generated_content import _resolve_generation_agent

    assert _resolve_generation_agent("auto", "auto") == "claude"
    assert _resolve_generation_agent("auto", "codex") == "codex"
    assert _resolve_generation_agent("kimi", "auto") == "kimi"
    assert _resolve_generation_agent("claude", "codex") == "claude"


# ---------------------------------------------------------------------------
# agent 是默认路径：失败必须落到 template / fallback，而不是抛出或静默丢弃
# ---------------------------------------------------------------------------


_AGENT_RUNTIME_ERRORS = pytest.mark.parametrize(
    "runtime_error",
    [subprocess.TimeoutExpired(cmd="claude", timeout=120), FileNotFoundError("claude")],
    ids=["timeout", "cli-missing"],
)


@_AGENT_RUNTIME_ERRORS
def test_generate_pr_content_agent_runtime_error_falls_back_to_template(
    runtime_error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """agent 超时或 CLI 缺失不得抛出：按 fallback=template 渲染模板并留下警告。"""
    generator = FailingContentGenerator(runtime_error)
    config = GeneratedContentConfig(
        draft_pr=GeneratedContentTargetConfig(
            mode="agent",
            output="markdown",
            prompt="Generate PR",
            title_template="[Template] {issue_title}",
            body_template="Closes #{issue_number}\n\nTemplate PR body.",
        ),
    )
    with caplog.at_level(logging.WARNING, logger="backend.core.use_cases.generated_content"):
        generated_pr_content = generate_pr_content(
            config=config,
            context=_pr_context(),
            fallback_title="Fallback",
            fallback_body="Fallback Body",
            generator=generator,
            cwd=Path("."),
        )
    assert len(generator.calls) == 1
    assert generated_pr_content.source == "template"
    assert generated_pr_content.title == "[Template] Title"
    assert "did not finish" in caplog.text
    assert "draft_pr: agent produced no usable content" in caplog.text


@_AGENT_RUNTIME_ERRORS
def test_generate_issue_content_agent_runtime_error_falls_back_to_template(
    runtime_error: Exception,
) -> None:
    """Issue 生成同样吸收 agent 超时 / CLI 缺失，退回 template 渲染。"""
    generator = FailingContentGenerator(runtime_error)
    config = GeneratedContentConfig(
        issue_from_prd=GeneratedContentTargetConfig(
            mode="agent",
            prompt="Generate Issue",
            title_template="[Template] {prd_title}",
            body_template="- PRD path: `{relative_prd_path}`\n\nTemplate body.",
        ),
    )
    generated_issue_content = generate_issue_content(
        config=config,
        context=_issue_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert len(generator.calls) == 1
    assert generated_issue_content.source == "template"
    assert generated_issue_content.title == "[Template] PRD Title"


def test_generate_pr_content_agent_without_prompt_never_runs_agent() -> None:
    """prompt 为空（未配置也没播种）时不拉起 agent，直接走 template 兜底。"""
    generator = FakeContentGenerator(response="Closes #42\n\nMust not be used.")
    config = GeneratedContentConfig(
        draft_pr=GeneratedContentTargetConfig(
            mode="agent",
            output="markdown",
            prompt="",
            title_template="[Template] {issue_title}",
            body_template="Closes #{issue_number}\n\nTemplate PR body.",
        ),
    )
    generated_pr_content = generate_pr_content(
        config=config,
        context=_pr_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert generator.calls == []
    assert generated_pr_content.source == "template"
    assert "Must not be used" not in generated_pr_content.body


def test_generate_issue_content_agent_without_prompt_never_runs_agent() -> None:
    """Issue 生成在 prompt 为空时同样不拉起 agent。"""
    generator = FakeContentGenerator(response="{}")
    config = GeneratedContentConfig(
        issue_from_prd=GeneratedContentTargetConfig(
            mode="agent",
            prompt="",
            title_template="[Template] {prd_title}",
            body_template="- PRD path: `{relative_prd_path}`\n\nTemplate body.",
        ),
    )
    generated_issue_content = generate_issue_content(
        config=config,
        context=_issue_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=generator,
        cwd=Path("."),
    )
    assert generator.calls == []
    assert generated_issue_content.source == "template"


def test_opt_in_config_generates_pr_with_agent_markdown() -> None:
    """显式 enabled=true 后不写 mode / output，沿用 agent + markdown。"""
    default_draft_pr = GeneratedContentConfig().draft_pr
    config = GeneratedContentConfig(
        draft_pr=replace(default_draft_pr, enabled=True, prompt="Generate PR")
    )
    generated_pr_content = generate_pr_content(
        config=config,
        context=_pr_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=FakeContentGenerator(response="# Add contract\n\nCloses #42\n\nDone."),
        cwd=Path("."),
    )
    assert generated_pr_content.source == "agent"
    assert generated_pr_content.title == "Add contract"


def test_default_config_generates_issue_with_agent_json() -> None:
    """issue_from_prd 默认 agent + json：与它的 JSON 提示词匹配，不需要额外配置 output。"""
    default_issue_from_prd = GeneratedContentConfig().issue_from_prd
    config = GeneratedContentConfig(
        issue_from_prd=replace(default_issue_from_prd, prompt="Generate Issue")
    )
    agent_json_output = json.dumps(
        {"title": "AI Title", "body": "- PRD path: `tasks/example.md`\n\nDetails."}
    )
    generated_issue_content = generate_issue_content(
        config=config,
        context=_issue_context(),
        fallback_title="Fallback",
        fallback_body="Fallback Body",
        generator=FakeContentGenerator(response=agent_json_output),
        cwd=Path("."),
    )
    assert generated_issue_content.source == "agent"
    assert generated_issue_content.title == "AI Title"


def test_parse_json_output_warns_when_agent_replies_with_markdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """output 与提示词格式错配（json 配 Markdown 回复）曾静默退回 template，必须留痕。"""
    with caplog.at_level(logging.WARNING, logger="backend.core.use_cases.generated_content"):
        assert _parse_json_output("# Add contract\n\nCloses #42") == ("", "")
    assert "not a JSON object" in caplog.text


def test_parse_json_output_is_quiet_for_empty_output(caplog: pytest.LogCaptureFixture) -> None:
    """agent 没有输出时上游已记过失败原因，解析层不再重复告警。"""
    with caplog.at_level(logging.WARNING, logger="backend.core.use_cases.generated_content"):
        assert _parse_json_output("") == ("", "")
    assert caplog.text == ""
