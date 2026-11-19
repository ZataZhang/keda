"""守卫测试（guard test）：PR 交付的 Git 状态、合并边界与逐轮任务记录。

守护 scripts/shared/just/ai_pr*.py；Git 与 just 为真实入口，GitHub 网络由传输替身替代，
临时项目测试命令使用受控退出码；本仓库检查另由 just test 执行。
失败应修复生产脚本，不修改断言放宽交付约定。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from tests.guards.shared.ai_pr_test_support import delivery_context, make_harness

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/shared/just"
sys.path.insert(0, str(_SCRIPTS))
from ai_pr import can_merge  # noqa: E402
from ai_pr_records import DeliveryContext, render_iteration, update_pr_body  # noqa: E402


def test_auto_merge_returns_to_updated_default_and_preserves_input(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    starting_sha = harness.git("rev-parse", "HEAD")
    context = delivery_context()
    context["user_messages"].append('literal "quote" $(touch INJECTED) `touch INJECTED`\n下一行')
    completion = harness.deliver(context)
    assert completion.returncode == 0, completion.stderr
    assert harness.git("branch", "--show-current") == "main"
    assert harness.git("rev-parse", "HEAD") == harness.git("rev-parse", "origin/main")
    assert harness.git("status", "--porcelain") == ""
    assert (harness.root / "feature.txt").read_text(encoding="utf-8") == "after\n"
    github_state = harness.state()
    assert len(github_state["issues"]) == len(github_state["prs"]) == 1
    assert "AI 整理" in github_state["issues"]["1"]["body"]
    assert "Changed the requested behavior" not in github_state["issues"]["1"]["body"]
    assert "Closes #1" in github_state["prs"]["2"]["body"]
    assert not (harness.root / "INJECTED").exists()
    pr_comment = next(
        comment["body"] for comment in github_state["comments"] if comment["kind"] == "pr"
    )
    assert starting_sha in pr_comment and "literal" in pr_comment
    journal_files = list((harness.root / ".git/ai-pr").glob("*/events.jsonl"))
    events = [
        json.loads(line) for line in journal_files[0].read_text(encoding="utf-8").splitlines()
    ]
    observation = next(event for event in events if event["event"] == "delivery_observed")
    assert observation["context"] == context
    assert observation["base_sha"] == starting_sha
    assert events[-1]["event"] == "returned_to_default"


def test_multiple_rounds_reuse_issue_pr_and_keep_failed_observations(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context("pending")).returncode == 0
    branch = harness.git("branch", "--show-current")
    assert branch != "main" and harness.state()["prs"]["2"]["isDraft"]
    original_issue = harness.state()["issues"]["1"]["body"]
    first_head = harness.git("rev-parse", "HEAD")
    (harness.root / "feature.txt").write_text("second attempt\n", encoding="utf-8")
    failed_context = delivery_context("failed")
    failed_context["user_messages"] = ["真实验证之后仍然没有解决，请继续修复"]
    assert harness.deliver(failed_context).returncode == 0
    second_head = harness.git("rev-parse", "HEAD")
    assert second_head != first_head and harness.git("branch", "--show-current") == branch
    github_state = harness.state()
    assert len(github_state["issues"]) == len(github_state["prs"]) == 1
    assert github_state["issues"]["1"]["body"] == original_issue
    pr_comments = [
        comment["body"] for comment in github_state["comments"] if comment["kind"] == "pr"
    ]
    assert len(pr_comments) == 2
    assert '"status": "failed"' in pr_comments[-1] and first_head in pr_comments[-1]
    # 无需新增提交：真实验证通过后，可以完成同一 PR。
    passed_context = delivery_context()
    passed_context["files"] = []
    assert harness.deliver(passed_context).returncode == 0
    assert harness.git("branch", "--show-current") == "main"
    assert harness.state()["prs"]["2"]["state"] == "MERGED"
    assert len(harness.state()["prs"]) == 1


@pytest.mark.parametrize(
    "settings",
    [
        {"review": "REVIEW_REQUIRED"},
        {"permission": "READ"},
        {"merge_state": "BLOCKED"},
        {"merge_state": "UNKNOWN"},
        {"merge_state": "DIRTY"},
        {"checks": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]},
        {"checks": [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}]},
    ],
)
def test_repository_rules_and_checks_keep_work_branch(tmp_path: Path, settings: dict) -> None:
    harness = make_harness(tmp_path)
    harness.configure(**settings)
    completion = harness.deliver(delivery_context())
    assert completion.returncode == 0, completion.stderr
    assert harness.git("branch", "--show-current") != "main"
    assert not any(call[:2] == ["pr", "merge"] for call in harness.state()["calls"])


@pytest.mark.parametrize("policy", ["only", "confirm"])
def test_optional_policy_does_not_merge_without_confirmation(tmp_path: Path, policy: str) -> None:
    harness = make_harness(tmp_path)
    completion = harness.deliver(delivery_context(), policy)
    assert completion.returncode == 0, completion.stderr
    assert harness.git("branch", "--show-current") != "main"
    assert not any(call[:2] == ["pr", "merge"] for call in harness.state()["calls"])


def test_validation_failure_stops_before_push_or_pr(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    justfile = harness.root / "justfile"
    justfile.write_text(
        justfile.read_text(encoding="utf-8").replace("@true", "exit 19"), encoding="utf-8"
    )
    harness.git("add", "justfile")
    harness.git("commit", "-qm", "configure failing checks")
    completion = harness.deliver(delivery_context())
    assert completion.returncode != 0
    assert harness.git("branch", "--show-current") != "main"
    assert not harness.state().get("prs")
    assert not any(call[:2] == ["pr", "merge"] for call in harness.state()["calls"])


def test_unrelated_staged_changes_are_rejected_before_branch_creation(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    (harness.root / "unrelated.txt").write_text("keep me", encoding="utf-8")
    harness.git("add", "unrelated.txt")
    completion = harness.deliver(delivery_context())
    assert completion.returncode != 0
    assert harness.git("branch", "--show-current") == "main"
    assert harness.git("diff", "--cached", "--name-only") == "unrelated.txt"
    assert not harness.state().get("issues")


@pytest.mark.parametrize(
    "settings", [{"merge_error": True}, {"queued": True}, {"dirty_after_merge": True}]
)
def test_unconfirmed_merge_or_local_changes_never_switch_branch(
    tmp_path: Path, settings: dict
) -> None:
    harness = make_harness(tmp_path)
    harness.configure(**settings)
    harness.deliver(delivery_context())
    assert harness.git("branch", "--show-current") != "main"


def test_changed_pr_head_is_not_merged(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.configure(head_override="a" * 40)
    completion = harness.deliver(delivery_context())
    assert completion.returncode != 0
    assert not any(call[:2] == ["pr", "merge"] for call in harness.state()["calls"])


def test_existing_branch_is_reused_and_existing_body_is_preserved(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.git("switch", "-c", "feature/existing")
    assert harness.deliver(delivery_context(), "only").returncode == 0
    assert harness.git("branch", "--show-current") == "feature/existing"
    github_state = harness.state()
    github_state["prs"]["2"]["body"] = "Reviewer notes\n\n" + github_state["prs"]["2"]["body"]
    harness.configure(prs=github_state["prs"])
    completion = harness.deliver(delivery_context(), "only")
    assert completion.returncode == 0, completion.stderr
    assert harness.state()["prs"]["2"]["body"].startswith("Reviewer notes")
    assert len(harness.state()["prs"]) == 1


def test_new_failure_returns_ready_pr_to_draft(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context(), "only").returncode == 0
    assert not harness.state()["prs"]["2"]["isDraft"]
    feedback = delivery_context("failed")
    feedback["files"] = []
    feedback["user_messages"] = ["刚才试了还是有问题，先不要合并"]
    completion = harness.deliver(feedback)
    assert completion.returncode == 0, completion.stderr
    assert harness.state()["prs"]["2"]["isDraft"]
    assert harness.git("branch", "--show-current") != "main"


def test_existing_issue_is_recovered_without_local_cache(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context(), "only").returncode == 0
    shutil.rmtree(harness.root / ".git/ai-pr")
    completion = harness.deliver(delivery_context(), "only")
    assert completion.returncode == 0, completion.stderr
    assert len(harness.state()["issues"]) == len(harness.state()["prs"]) == 1


def test_failure_after_merge_reopens_task_and_creates_followup_pr(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context()).returncode == 0
    previous_issues = harness.state()["issues"]
    previous_issues["1"]["state"] = "CLOSED"
    original_body = previous_issues["1"]["body"]
    harness.configure(issues=previous_issues)
    (harness.root / "feature.txt").write_text("follow-up correction\n", encoding="utf-8")
    feedback = delivery_context("pending")
    feedback["issue"] = 1
    feedback["task"] = "PR #2 合并后真实入口出现反例，原要求未满足，继续修复。"
    completion = harness.deliver(feedback)
    assert completion.returncode == 0, completion.stderr
    github_state = harness.state()
    assert len(github_state["issues"]) == 1 and len(github_state["prs"]) == 2
    assert github_state["issues"]["1"]["state"] == "OPEN"
    assert github_state["issues"]["1"]["body"] == original_body
    assert github_state["prs"]["2"]["state"] == "MERGED"
    assert github_state["prs"]["3"]["state"] == "OPEN"
    assert github_state["prs"]["3"]["isDraft"]


def test_actual_default_branch_is_used_instead_of_hardcoded_main(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.git("branch", "-m", "trunk")
    harness.git("push", "-u", "origin", "trunk")
    harness.configure(default_branch="trunk")
    completion = harness.deliver(delivery_context())
    assert completion.returncode == 0, completion.stderr
    assert harness.git("branch", "--show-current") == "trunk"
    assert harness.git("rev-parse", "HEAD") == harness.git("rev-parse", "origin/trunk")


def test_diverged_local_default_is_not_reset_after_merge(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert harness.deliver(delivery_context(), "only").returncode == 0
    local_default_sha = harness.git("commit-tree", "main^{tree}", "-p", "main", "-m", "local work")
    harness.git("update-ref", "refs/heads/main", local_default_sha)
    completion = harness.deliver(delivery_context())
    assert completion.returncode != 0 and "diverged" in completion.stderr
    assert harness.git("branch", "--show-current") != "main"
    assert harness.git("rev-parse", "main") == local_default_sha
    assert harness.state()["prs"]["2"]["state"] == "MERGED"


def test_context_rejects_missing_provenance_and_unsafe_file_scope(tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    for field, invalid_value in (
        ("request_source", "inferred"),
        ("files", ["../secret"]),
        ("user_messages", []),
    ):
        context = delivery_context()
        context[field] = invalid_value
        context_path.write_text(json.dumps(context), encoding="utf-8")
        with pytest.raises(ValueError):
            DeliveryContext.read(context_path)


def test_unknown_or_missing_merge_information_fails_closed() -> None:
    assert not can_merge({}, "abc")
    assert not can_merge({"state": "OPEN", "headRefOid": "abc", "mergeStateStatus": "CLEAN"}, "abc")


def test_record_fences_preserve_original_text_and_manual_pr_sections() -> None:
    context = delivery_context()
    context["user_messages"] = ["原文包含 ````json 和 <details>\n下一行"]
    comment = render_iteration({"context": context, "head_sha": "head", "base_sha": "base"})
    assert "`````json" in comment
    assert "原文包含" in comment
    first_body = update_pr_body("Manual notes", "first")
    second_body = update_pr_body(first_body + "\nReviewer's addition", "second")
    assert "Manual notes" in second_body and "Reviewer's addition" in second_body
    assert "first" not in second_body and second_body.count("just-ai-pr:begin") == 1
