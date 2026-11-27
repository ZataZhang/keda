"""自修节点逐项修复报告与同轮收尾的回归验证。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, IssueSummary, PrePrReviewConfig
from backend.core.use_cases.agent_review import parse_reviewer_decision, run_pre_pr_review
from backend.core.use_cases.agent_runner_failure import HumanActionRequiredError
from tests.conftest import FakeGitHubClient
from tests.test_agent_review import _PatchingReviewRunner, _write_commit_request


@pytest.mark.parametrize("fenced", [False, True])
def test_repair_report_keeps_fixed_problems_without_requesting_another_review(fenced: bool) -> None:
    """已修问题保留修复证据，且不会被计为未解决发现。"""
    report = json.dumps(
        {
            "summary": "已完成修复",
            "repairs": [
                {
                    "title": "旧任务覆盖撤权",
                    "severity": "critical",
                    "status": "fixed",
                    "fix": "在同一事务校验租约和授权修订",
                    "verification": "撤权再授权交错测试通过",
                }
            ],
        }
    )
    decision = parse_reviewer_decision(f"```json\n{report}\n```" if fenced else report)
    assert decision.verdict == "approved"
    assert not decision.has_findings
    assert decision.repairs is not None
    assert decision.repairs[0].fix == "在同一事务校验租约和授权修订"


@pytest.mark.parametrize("raw_repairs", [None, [None], "fixed"])
def test_malformed_repair_report_cannot_approve(raw_repairs: object) -> None:
    """损坏的修复报告不能被当作空问题清单放行。"""
    decision = parse_reviewer_decision(json.dumps({"repairs": raw_repairs}))
    assert not decision.parseable


@pytest.mark.parametrize("status", ["fixed", "blocked"])
def test_repair_report_commits_patch_and_finishes_in_one_call(tmp_path: Path, status: str) -> None:
    """完整修复同轮结束，实际阻塞在保存补丁后直接交接。"""
    _write_commit_request(tmp_path)
    report = json.dumps(
        {
            "summary": "已修清理去重，核对远端恢复",
            "repairs": [
                {
                    "title": "清理任务去重",
                    "status": "fixed",
                    "fix": "按远端文件 ID 区分清理任务",
                    "verification": "10 tests passed",
                },
                {
                    "title": "远端恢复",
                    "severity": "high",
                    "status": status,
                    "fix": "已持久化远端身份并重试验证",
                    "verification": "崩溃恢复检查已执行",
                    "blocker": "远端沙箱拒绝访问，需要恢复账号权限" if status == "blocked" else "",
                },
            ],
        }
    )

    class RepairReportRunner(_PatchingReviewRunner):
        """复用提交门禁替身，仅替换审查 Agent 的报告。"""

        def run(self, command, **kwargs):
            """让实际编排消费新版修复报告。"""
            result = super().run(command, **kwargs)
            return replace(result, stdout=report) if command[0] == "codex" else result

    runner = RepairReportRunner("approved")
    github = FakeGitHubClient()
    config = AppConfig(pre_pr_review=PrePrReviewConfig(max_attempts=2))
    issue = IssueSummary(number=140, title="RAG", url="U", body="B", labels=())
    call_arguments = dict(
        issue=issue,
        worktree_path=tmp_path,
        config=config,
        github_client=github,
        process_runner=runner,
        selected_agent="codex",
        head_sha_before="before-sha",
        expected_branch="issue-1",
        verification_results=[],
    )
    if status == "blocked":
        with pytest.raises(HumanActionRequiredError, match="远端沙箱拒绝访问") as failure:
            run_pre_pr_review(**call_arguments)
        assert failure.value.directive.block_type == "pre_pr_review_repair"
    else:
        head, _ = run_pre_pr_review(**call_arguments)
        assert head == "after-sha"
    assert len([command for command in runner.calls if command[0] == "codex"]) == 1
    assert any(command[:2] == ["git", "commit"] for command in runner.calls)
    comment = next(call["body"] for call in github.calls if call["method"] == "comment_issue")
    assert "Repair results" in comment
    assert "按远端文件 ID 区分清理任务" in comment
    assert "10 tests passed" in comment
    if status == "blocked":
        assert "远端沙箱拒绝访问" in comment
