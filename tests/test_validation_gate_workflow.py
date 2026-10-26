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
    repository_root = Path(__file__).resolve().parents[1]
    workflow_path = repository_root / ".github/workflows/validation-gate.yml"
    workflow_document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step_command = workflow_document["jobs"]["check-sign-off"]["steps"][0]["run"]
    _shell_prefix, opening_marker, heredoc_body = step_command.partition("<<'CHECK_SCRIPT'\n")
    python_script, closing_marker, _shell_suffix = heredoc_body.rpartition("\nCHECK_SCRIPT")
    assert opening_marker and closing_marker

    check_result = subprocess.run(
        [sys.executable, "-c", python_script],
        env={
            **os.environ,
            "PR_BODY": build_validation_checklist_block(checklist_items),
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert check_result.returncode == expected_exit_code, check_result.stdout + check_result.stderr
    assert "No realistic-validation block" not in check_result.stdout
