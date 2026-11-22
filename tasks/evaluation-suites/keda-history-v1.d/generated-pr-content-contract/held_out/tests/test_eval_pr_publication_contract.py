"""通过真实 Git 分支和 Draft PR 发布入口核对标题、正文与关闭引用。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import (
    AppConfig,
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    GitConfig,
    IssueSummary,
)
from backend.core.use_cases.agent_runner_publish import create_draft_pr
from backend.infrastructure.process_runner import SubprocessRunner
from tests.conftest import FakeContentGenerator, FakeGitHubClient


@pytest.mark.parametrize(
    ("mode", "leading_line", "body_reference"),
    (
        ("agent", "Closes #42", True),
        ("agent", "Closes: #42.", True),
        ("agent", "Fixes #42", True),
        ("agent", "Resolved #42", True),
        ("template", "Closes #42", True),
        ("template", "Closes #42", False),
        ("disabled", "", True),
    ),
)
def test_draft_pr_preserves_meaningful_title_and_closes_issue(
    tmp_path: Path,
    mode: str,
    leading_line: str,
    body_reference: bool,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "-b", "issue-42", str(tmp_path)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    issue = IssueSummary(
        number=42,
        title="Improve retry behavior",
        url="https://github.com/example/fixture/issues/42",
        body="Keep the retry operation available.",
        labels=(),
    )
    body_text = "## Summary\nRetries can resume after the failure."
    if body_reference:
        body_text = f"Closes #42\n\n{body_text}"
    target = GeneratedContentTargetConfig(
        enabled=True,
        mode=mode if mode != "disabled" else "agent",
        output="markdown",
        title_template=leading_line,
        body_template=body_text,
        include_commit_log=False,
        include_diff_stat=False,
    )
    config = AppConfig(
        git=GitConfig(base_branch="main"),
        generated_content=GeneratedContentConfig(enabled=mode != "disabled", draft_pr=target),
    )
    simulated_agent = FakeContentGenerator(response=f"{leading_line}\n\n{body_text}")
    github = FakeGitHubClient()
    create_draft_pr(
        issue, tmp_path, config, github, SubprocessRunner(), content_generator=simulated_agent
    )
    draft = next(call for call in github.calls if call["method"] == "create_draft_pr")
    assert "retry" in draft["title"].lower(), draft["title"]
    assert re.search(
        r"\b(?:closes|fixes|resolves)\s*:?\s*#42\b", draft["body"], re.IGNORECASE
    ), draft["body"]
    if mode != "disabled" and body_reference:
        assert "Retries can resume after the failure." in draft["body"], draft["body"]
