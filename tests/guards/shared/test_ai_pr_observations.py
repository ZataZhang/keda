"""守卫测试（guard test）：验证状态互不冒充，评论区分代码变更与验收观察。

守护 ai_pr_records.py 及真实 just ai pr 入口；状态拆分是产品契约，不放宽合并与验证约束。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.guards.shared.ai_pr_test_support import delivery_context, make_harness

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/shared/just"))
from ai_pr_records import DeliveryContext, build_observations, render_iteration  # noqa: E402


def test_local_success_does_not_claim_ci_or_human_success(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.configure(
        checks=[{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]
    )
    context = delivery_context()
    context["iteration_summary"] = "Verified publication; CI still fails."
    completion = harness.deliver(context)
    assert completion.returncode == 0, completion.stderr
    github_state = harness.state()
    pr_body = github_state["prs"]["2"]["body"]
    assert "| Local checks | passed |" in pr_body
    assert "| GitHub CI | failed |" in pr_body
    assert "| Human acceptance | not_requested |" in pr_body
    assert "## Validation: passed" not in pr_body
    comment = next(
        comment["body"] for comment in github_state["comments"] if comment["kind"] == "pr"
    )
    visible_comment = comment.split("<details>")[0]
    assert "Verified publication; CI still fails." in visible_comment
    assert context["summary"] not in visible_comment
    assert context["task"] not in visible_comment
    assert '"observations"' in comment and '"schema_version": 2' in comment
    assert not any(call[:2] == ["pr", "merge"] for call in github_state["calls"])


def test_same_head_comments_only_describe_the_new_observation(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context("pending")).returncode == 0
    context = delivery_context()
    context["files"] = []
    context["iteration_summary"] = "The real entry was checked successfully."
    assert harness.deliver(context, "only").returncode == 0
    comments = [
        comment["body"] for comment in harness.state()["comments"] if comment["kind"] == "pr"
    ]
    visible_comment = comments[-1].split("<details>")[0]
    assert "No new code commit" in visible_comment
    assert context["iteration_summary"] in visible_comment
    assert context["summary"] not in visible_comment
    assert "| Human acceptance | not_requested |" in visible_comment


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_explicit_human_acceptance_requirement_is_preserved(tmp_path: Path, status: str) -> None:
    harness = make_harness(tmp_path)
    context = delivery_context()
    context["human_validation"] = {"status": status, "details": "User acceptance evidence."}
    completion = harness.deliver(context)
    assert completion.returncode == 0, completion.stderr
    assert harness.state()["prs"]["2"]["isDraft"]
    assert f"| Human acceptance | {status} |" in harness.state()["prs"]["2"]["body"]
    assert not any(call[:2] == ["pr", "merge"] for call in harness.state()["calls"])


def test_legacy_context_does_not_invent_missing_observations(tmp_path: Path) -> None:
    context = delivery_context()
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    parsed_context = DeliveryContext.read(context_path)
    assert parsed_context.validation_passed
    observations = build_observations(parsed_context, "pending")
    assert observations["local"]["status"] == "passed"
    assert observations["github_ci"]["status"] == "pending"
    assert observations["human"]["status"] == "not_requested"
    event = {"schema_version": 1, "context": context, "base_sha": "base", "head_sha": "head"}
    visible_comment = render_iteration(event).split("<details>")[0]
    assert "| GitHub CI | unknown |" in visible_comment
    assert "| Local checks | unknown |" in visible_comment
    assert "Delivery observation: passed" not in visible_comment


@pytest.mark.parametrize("human_validation", [None, {"status": "passed"}])
def test_invalid_human_acceptance_evidence_is_rejected(
    tmp_path: Path, human_validation: object
) -> None:
    context = delivery_context()
    context["human_validation"] = human_validation
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    with pytest.raises(ValueError, match="human_validation"):
        DeliveryContext.read(context_path)


def test_missing_ci_checks_are_not_reported_as_ci_pass(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.configure(checks=[])
    completion = harness.deliver(delivery_context(), "only")
    assert completion.returncode == 0, completion.stderr
    assert "| GitHub CI | not_observed |" in harness.state()["prs"]["2"]["body"]


def test_ci_completion_adds_a_status_update_without_claiming_a_new_commit(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    pending_checks = [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}]
    passed_checks = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    harness.configure(checks_by_view=[pending_checks, pending_checks, passed_checks])
    completion = harness.deliver(delivery_context())
    assert completion.returncode == 0, completion.stderr
    assert harness.git("branch", "--show-current") == "main"
    comments = [
        comment["body"] for comment in harness.state()["comments"] if comment["kind"] == "pr"
    ]
    assert len(comments) == 2
    assert "| GitHub CI | pending |" in comments[0].split("<details>")[0]
    last_visible = comments[-1].split("<details>")[0]
    assert "| GitHub CI | passed |" in last_visible
    assert "No new code commit" in last_visible
    assert "| Human acceptance | not_requested |" in last_visible
    assert "| GitHub CI | passed |" in harness.state()["prs"]["2"]["body"]
