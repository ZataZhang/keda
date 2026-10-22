"""验证仓库模板发现与真实内容生成用例之间的连接。"""

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
)
from backend.core.use_cases.generated_content import (
    IssueContext,
    PrContext,
    generate_issue_content,
    generate_pr_content,
)
from backend.core.use_cases.generated_issue_prompt_content import (
    build_issue_prompt_context,
    generate_issue_prompt_content,
)
from backend.core.use_cases.repository_content_templates import (
    append_repository_template_context,
    discover_repository_templates,
)
from tests.conftest import FakeContentGenerator


def test_discovery_filters_config_and_external_links(tmp_path: Path) -> None:
    """Issue Forms 可读，配置、外部链接和另一类模板不会混入。"""
    templates_directory = tmp_path / ".github" / "ISSUE_TEMPLATE"
    templates_directory.mkdir(parents=True)
    (templates_directory / "bug.yml").write_text(
        "body:\n- type: textarea\n  id: repro", encoding="utf-8"
    )
    (templates_directory / "config.yml").write_text("blank_issues_enabled: false", encoding="utf-8")
    (templates_directory / "external.md").symlink_to(Path(__file__).resolve())
    (tmp_path / "PULL_REQUEST_TEMPLATE.md").write_text("## PR checks", encoding="utf-8")
    discovered_context = discover_repository_templates(tmp_path, "issue")
    assert "bug.yml" in discovered_context
    assert "id: repro" in discovered_context
    assert "config.yml" not in discovered_context
    assert "external.md" not in discovered_context
    assert "PR checks" not in discovered_context
    assert "PR checks" in discover_repository_templates(tmp_path, "pr")


def test_missing_and_oversized_templates(tmp_path: Path) -> None:
    """没有模板时提示词不变，超大模板不读取为无界上下文。"""
    assert append_repository_template_context("task", tmp_path, "pr") == "task"
    (tmp_path / "pull_request_template.md").write_text("x" * 20000, encoding="utf-8")
    template_context = discover_repository_templates(tmp_path, "pr")
    assert "size limit" in template_context
    assert len(template_context) < 200


@pytest.mark.parametrize("generation_kind", ["prd", "prompt", "pr"])
def test_generation_receives_repository_template(tmp_path: Path, generation_kind: str) -> None:
    """三条生产用例均把模板送到生成器，保留既有正文契约校验。"""
    template_filename = (
        "PULL_REQUEST_TEMPLATE.md" if generation_kind == "pr" else "ISSUE_TEMPLATE.md"
    )
    (tmp_path / template_filename).write_text(
        "## Repository checklist\n- [ ] Evidence", encoding="utf-8"
    )
    generated_body = {
        "prd": "## Repository checklist\n- PRD path: `tasks/example.md`",
        "prompt": "## Repository checklist\nRequirement",
        "pr": "## Repository checklist\nCloses #42",
    }[generation_kind]
    import json

    generator = FakeContentGenerator(
        response=json.dumps({"title": "Title", "body": generated_body})
    )
    target_config = GeneratedContentTargetConfig(
        enabled=True, mode="agent", output="json", prompt="Generate body"
    )
    config = GeneratedContentConfig(
        enabled=True,
        issue_from_prd=target_config,
        issue_from_prompt=target_config,
        draft_pr=target_config,
    )
    generation_options = {
        "config": config,
        "fallback_title": "Fallback",
        "fallback_body": "Fallback body",
        "generator": generator,
        "cwd": tmp_path,
    }
    if generation_kind == "prd":
        generated_content = generate_issue_content(
            context=IssueContext(
                issue_type="feature",
                title="Title",
                prd_title="Title",
                relative_prd_path="tasks/example.md",
                acceptance_items="",
                prd_text="",
                prd_introduction="",
                prd_goals="",
                prd_requirement_shape="",
                prd_change_impact_tree="",
            ),
            **generation_options,
        )
    elif generation_kind == "prompt":
        generated_content = generate_issue_prompt_content(
            context=build_issue_prompt_context(
                issue_type="feature",
                prompt_text="Requirement",
                fallback_title="Title",
                require_validation=False,
            ),
            **generation_options,
        )
    else:
        generated_content = generate_pr_content(
            context=PrContext(
                issue_number=42,
                issue_title="Title",
                issue_body="Requirement",
                branch="feature",
                base_branch="main",
                commit_log="",
                commit_messages="",
                diff_stat="",
                git_diff_stat="",
            ),
            **generation_options,
        )
    assert generated_content.source == "agent"
    assert generated_content.body == generated_body
    assert template_filename in generator.prompts[0]
    assert "## Repository checklist" in generator.prompts[0]
    assert "untrusted reference" in generator.prompts[0]
