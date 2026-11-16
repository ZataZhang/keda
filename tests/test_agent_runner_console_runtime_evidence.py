"""Agent Runner Console 对 verifier 前人审证据的发现测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.routes import agent_runner_console_runtime as runtime_route
from backend.core.shared.models.runtime_task import RuntimeTask, RuntimeTaskStatus
from backend.core.use_cases.agent_runner_evidence_media import EvidenceMedia


def test_existing_pr_refreshes_evidence_branch_listing_once_per_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PR 已出现时强制刷新远端证据索引，并复用本次请求结果。"""
    refresh_arguments: list[bool] = []

    monkeypatch.setattr(runtime_route, "find_evidence_media", lambda *_args: EvidenceMedia())

    def list_branches(_repo_path: Path, *, refresh: bool = False) -> dict[int, str]:
        refresh_arguments.append(refresh)
        return {7: "iar-evidence/issue-7", 8: "iar-evidence/issue-8"}

    monkeypatch.setattr(runtime_route, "list_evidence_branches", list_branches)

    repository_paths = {"keda": tmp_path}
    refreshed_evidence_repo_ids: set[str] = set()
    issue_seven = RuntimeTask(
        repo_id="keda",
        issue_number=7,
        pr_url="https://github.com/example/keda/pull/7",
        status=RuntimeTaskStatus.RUNNING,
    )
    issue_eight = RuntimeTask(
        repo_id="keda",
        issue_number=8,
        pr_url="https://github.com/example/keda/pull/8",
        status=RuntimeTaskStatus.FAILED,
    )

    issue_seven_media = runtime_route._evidence_media(
        repository_paths, issue_seven, refreshed_evidence_repo_ids
    )
    issue_eight_media = runtime_route._evidence_media(
        repository_paths, issue_eight, refreshed_evidence_repo_ids
    )

    assert issue_seven_media is not None
    assert issue_seven_media.page_ref == "iar-evidence/issue-7"
    assert issue_eight_media is not None
    assert issue_eight_media.page_ref == "iar-evidence/issue-8"
    assert refresh_arguments == [True, False]
