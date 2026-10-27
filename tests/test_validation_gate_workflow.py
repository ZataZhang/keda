"""生产清单写入格式与 GitHub Actions 签收工作流的契约测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from backend.core.use_cases.agent_runner_validation_checklist import (
    build_validation_checklist_block,
    find_unrecognized_validation_block_marker,
)


def _run_workflow_check(pr_body: str) -> subprocess.CompletedProcess[str]:
    """用真实工作流脚本检查一份 PR 正文，返回执行结果。"""
    repository_root = Path(__file__).resolve().parents[1]
    workflow_path = repository_root / ".github/workflows/validation-gate.yml"
    workflow_document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step_command = workflow_document["jobs"]["check-sign-off"]["steps"][0]["run"]
    _shell_prefix, opening_marker, heredoc_body = step_command.partition("<<'CHECK_SCRIPT'\n")
    python_script, closing_marker, _shell_suffix = heredoc_body.rpartition("\nCHECK_SCRIPT")
    assert opening_marker and closing_marker

    return subprocess.run(
        [sys.executable, "-c", python_script],
        env={**os.environ, "PR_BODY": pr_body},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("checklist_items", "expected_exit_code"),
    [
        (["- [ ] 尚未验证"], 1),
        (["- [x] 已验证", "- [ ] 尚未验证"], 1),
        (["- [x] 已验证"], 0),
        ([], 0),
    ],
)
def test_workflow_checks_production_validation_block(
    checklist_items: list[str], expected_exit_code: int
) -> None:
    """执行真实工作流脚本，确保生产端新标记不会绕过未签收检查。"""
    check_result = _run_workflow_check(build_validation_checklist_block(checklist_items))

    assert check_result.returncode == expected_exit_code, check_result.stdout + check_result.stderr
    assert "No realistic-validation block" not in check_result.stdout


@pytest.mark.parametrize(
    "legacy_block",
    [
        "<!-- iar:realistic-validation version=1 total=1 -->\n- [x] 已验证\n"
        "<!-- iar:realistic-validation-end -->",
        "<!-- iar:realistic-validation version=1 total=1 -->\n- [ ] 尚未验证\n",
    ],
    ids=["legacy-ticked", "legacy-unchecked"],
)
def test_workflow_rejects_legacy_prefix_validation_block(legacy_block: str) -> None:
    """旧前缀签收区块即使全部勾选也必须失败：门禁认不出它就等于零保护。"""
    check_result = _run_workflow_check(f"Closes #42\n\n{legacy_block}\n")

    assert check_result.returncode == 1, check_result.stdout + check_result.stderr
    assert "Unrecognized Realistic Validation sign-off block marker" in check_result.stdout
    assert "No realistic-validation block" not in check_result.stdout


def test_workflow_ignores_validation_block_inside_code_fence() -> None:
    """围栏代码块里的清单示例不是声明，不能触发签收判定。"""
    fenced_example = (
        "Closes #42\n\n## Docs\n\n```markdown\n"
        + "\n".join(
            [
                "<!-- kc:realistic-validation version=1 total=2 -->",
                "- [ ] 示例条目",
                "<!-- kc:realistic-validation-end -->",
            ]
        )
        + "\n```\n"
    )

    check_result = _run_workflow_check(fenced_example)

    assert check_result.returncode == 0, check_result.stdout + check_result.stderr
    assert "No realistic-validation block" in check_result.stdout


def test_workflow_and_runner_share_unreadable_block_verdict() -> None:
    """工作流与 runner 消费方对同一份正文给出一致的可读性判定。"""
    bodies = [
        build_validation_checklist_block(["- [ ] 尚未验证"]),
        "<!-- iar:realistic-validation version=1 total=1 -->\n- [ ] 尚未验证",
        "<!-- kc:realistic-validation version=1 -->\n- [ ] 尚未验证",
        "Closes #42\n\n正文里没有签收清单。\n",
        "Closes #42\n\n```markdown\n<!-- iar:realistic-validation version=1 total=1 -->\n```\n",
    ]

    for pr_body in bodies:
        check_result = _run_workflow_check(pr_body)
        runner_says_unreadable = find_unrecognized_validation_block_marker(pr_body) is not None
        workflow_says_unreadable = "Unrecognized Realistic Validation" in check_result.stdout
        assert runner_says_unreadable == workflow_says_unreadable, pr_body
        if runner_says_unreadable:
            assert check_result.returncode == 1, pr_body
