"""Agent Runner Console 对 verifier 前人审证据的发现测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.routes import agent_runner_console_runtime as runtime_route
from backend.core.shared.models.runtime_task import RuntimeTask, RuntimeTaskStatus
from backend.core.use_cases.agent_runner_evidence_media import EvidenceMedia


def test_runtime_summary_uses_cached_evidence_listing_and_requests_refresh_for_pr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """任务摘要只查缓存索引，并合并每仓库的后台刷新需求。"""
    branch_lookup_arguments: list[tuple[bool, bool]] = []

    monkeypatch.setattr(runtime_route, "find_evidence_media", lambda *_args: EvidenceMedia())

    def list_branches(
        _repo_path: Path,
        *,
        refresh: bool = False,
        use_cache_only: bool = False,
    ) -> dict[int, str]:
        branch_lookup_arguments.append((refresh, use_cache_only))
        return {7: "iar-evidence/issue-7", 8: "iar-evidence/issue-8"}

    monkeypatch.setattr(runtime_route, "list_evidence_branches", list_branches)

    repository_paths = {"keda": tmp_path}
    evidence_branch_refresh_by_repo: dict[str, bool] = {}
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
        repository_paths, issue_seven, evidence_branch_refresh_by_repo
    )
    issue_eight_media = runtime_route._evidence_media(
        repository_paths, issue_eight, evidence_branch_refresh_by_repo
    )

    assert issue_seven_media is not None
    assert issue_seven_media.page_ref == "iar-evidence/issue-7"
    assert issue_eight_media is not None
    assert issue_eight_media.page_ref == "iar-evidence/issue-8"
    assert branch_lookup_arguments == [(False, True), (False, True)]
    assert evidence_branch_refresh_by_repo == {"keda": True}
