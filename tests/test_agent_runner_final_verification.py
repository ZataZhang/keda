"""验证 PR 发布只使用最终提交的 RV 与独立复核结论。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_final_verification import (
    FinalVerificationRequest,
    ensure_final_verifier_verdict,
)
from backend.core.use_cases.agent_runner_structured_evidence import ValidationEvidenceError
from backend.core.use_cases.run_verifier_agent import ValidationVerdict
from tests.conftest import FakeGitHubClient, FakeProcessRunner


def _request(tmp_path: Path, *, verified_sha: str | None) -> FinalVerificationRequest:
    """构造带原复核提交的发布请求。"""
    return FinalVerificationRequest(
        issue=IssueSummary(7, "Example", "https://example.test/7", "body", ()),
        worktree_path=tmp_path,
        config=AppConfig(),
        process_runner=FakeProcessRunner(),
        selected_agent="codex",
        verified_sha=verified_sha,
        verifier_verdict=ValidationVerdict(risk="green"),
    )


def test_final_verification_rechecks_review_patch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """reviewer 新提交后，旧绿灯不能直接发布。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    calls: list[str] = []
    monkeypatch.setattr(final_gate, "has_changes", lambda *args: False)
    monkeypatch.setattr(final_gate, "get_head_sha", lambda *args: "review-sha")
    monkeypatch.setattr(
        final_gate, "ensure_validation_evidence_ready", lambda *args: calls.append("evidence")
    )
    monkeypatch.setattr(
        final_gate, "ensure_no_misplaced_evidence_helpers", lambda *args: calls.append("helpers")
    )
    monkeypatch.setattr(
        final_gate, "ensure_validation_commands_pass", lambda *args: calls.append("rv")
    )

    def _verify_review_head(*args: object) -> ValidationVerdict:
        calls.append("verifier")
        return ValidationVerdict(risk="yellow")

    monkeypatch.setattr(final_gate, "run_verifier_gate", _verify_review_head)

    verdict = ensure_final_verifier_verdict(_request(tmp_path, verified_sha="builder-sha"))

    assert calls == ["evidence", "helpers", "rv", "verifier"]
    assert verdict is not None and verdict.risk == "yellow"


def test_final_verification_reuses_same_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HEAD 未变时不重复运行耗时的 RV 与 verifier。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: False)
    monkeypatch.setattr(final_gate, "get_head_sha", lambda *args: "builder-sha")
    monkeypatch.setattr(
        final_gate,
        "run_verifier_gate",
        lambda *args: pytest.fail("same commit must reuse its verdict"),
    )

    verdict = ensure_final_verifier_verdict(_request(tmp_path, verified_sha="builder-sha"))

    assert verdict is not None and verdict.risk == "green"


def test_final_verification_blocks_dirty_review_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """review 留下未提交改动时不把旧绿灯贴到 PR。"""
    from backend.core.use_cases import agent_runner_final_verification as final_gate

    monkeypatch.setattr(final_gate, "has_changes", lambda *args: True)
    with pytest.raises(ValidationEvidenceError, match="clean committed worktree"):
        ensure_final_verifier_verdict(_request(tmp_path, verified_sha="builder-sha"))


def test_execution_loop_commits_before_independent_verifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """builder 的未提交工作树不能进入独立 verifier。"""
    from backend.core.use_cases import run_agent_execution_loop as execution_loop
    from backend.core.use_cases import run_verifier_agent as verifier_module

    committed = False
    verification_order: list[str] = []

    def _commit(*args: object, **kwargs: object) -> list[object]:
        nonlocal committed
        committed = True
        verification_order.append("commit")
        return []

    def _verify(*args: object, **kwargs: object) -> ValidationVerdict:
        assert committed is True
        verification_order.append("verifier")
        return ValidationVerdict(risk="green")

    monkeypatch.setattr(execution_loop, "run_agent", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution_loop, "run_verification", lambda *args: [])
    monkeypatch.setattr(execution_loop, "get_head_sha", lambda *args: "new" if committed else "old")
    monkeypatch.setattr(execution_loop, "has_changes", lambda *args: not committed)
    monkeypatch.setattr(execution_loop, "commit_requested_changes", _commit)
    monkeypatch.setattr(execution_loop, "ensure_prd_delivery_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        execution_loop, "ensure_validation_evidence_ready", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        execution_loop, "ensure_no_misplaced_evidence_helpers", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        execution_loop, "warn_legacy_evidence_helpers", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        execution_loop, "ensure_validation_commands_pass", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(verifier_module, "run_verifier_gate", _verify)
    verification_request = _request(tmp_path, verified_sha=None)

    commit_result = execution_loop.run_agent_until_committed(
        execution_loop.AgentExecutionRequest(
            selected_agent="codex",
            issue=verification_request.issue,
            worktree_path=tmp_path,
            config=verification_request.config,
            process_runner=verification_request.process_runner,
            before_sha="old",
            expected_branch="issue-7",
        )
    )

    assert verification_order == ["commit", "verifier"]
    assert commit_result.verifier_verdict is not None


def test_failed_final_verifier_prevents_draft_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """review 后的新提交验不过时，不创建带旧绿灯的 PR。"""
    from backend.core.use_cases import agent_runner_publication as publication

    monkeypatch.setattr(publication, "run_pre_pr_review", lambda **kwargs: ("review-sha", []))

    def _reject_review_head(*args: object) -> None:
        raise ValidationEvidenceError("new head failed")

    monkeypatch.setattr(
        publication,
        "ensure_final_verifier_verdict",
        _reject_review_head,
    )
    monkeypatch.setattr(
        publication,
        "_create_draft_pr_with_recovery_context",
        lambda **kwargs: pytest.fail("failed final verification must block PR creation"),
    )

    with pytest.raises(ValidationEvidenceError, match="new head failed"):
        publication._review_verify_create_pr(
            publication._PublicationReviewRequest(
                verification_request=_request(tmp_path, verified_sha="builder-sha"),
                github_client=FakeGitHubClient(),
                expected_branch="issue-7",
                verification_results=[],
                push_callback=lambda: None,
                content_generator=None,
            )
        )
