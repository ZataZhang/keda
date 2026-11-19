"""Agent Runner 资源准入回调的用例测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_orchestration_models import RunOnceRequest
from backend.core.use_cases.agent_runner_issue_admission import _process_issue_if_admitted
from tests.conftest import FakeGitHubClient, FakeProcessRunner


@pytest.mark.parametrize(("admitted", "expected_exit_code"), [(False, 0), (True, 17)])
def test_process_issue_respects_optional_admission_check(
    admitted: bool,
    expected_exit_code: int,
) -> None:
    """资源准入拒绝时保留队列项，通过时委托给既有处理流程。"""
    github_client = FakeGitHubClient()
    process_runner = FakeProcessRunner()
    request = RunOnceRequest(
        repo_path=Path("."),
        config=AppConfig(),
        dry_run=False,
        agent="auto",
        max_issues=1,
        github_client=github_client,
        process_runner=process_runner,
        issue_admission_check=lambda: admitted,
    )
    issue = IssueSummary(
        number=42,
        title="Issue #42",
        url="https://github.com/example/repo/issues/42",
        body="",
        labels=("agent/ready",),
    )
    issue_processor = Mock(return_value=17)

    exit_code = _process_issue_if_admitted(
        issue,
        "ready",
        request=request,
        process_issue=issue_processor,
        github_client=github_client,
        process_runner=process_runner,
    )

    assert exit_code == expected_exit_code
    if admitted:
        issue_processor.assert_called_once_with(
            issue,
            "ready",
            github_client=github_client,
            process_runner=process_runner,
        )
    else:
        issue_processor.assert_not_called()
