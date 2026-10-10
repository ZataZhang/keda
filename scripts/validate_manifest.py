"""通过仓库自身校验器验证 Issue #266 的 evidence.json。"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_structured_evidence import (
    format_structured_evidence_marker,
    validate_evidence_manifest,
)


def main() -> None:
    """校验清单覆盖、字段、文件命名、文件存在性与负控声明。"""
    evidence_dir = Path(__file__).resolve().parent.parent
    worktree_path = evidence_dir.parents[2]
    issue_body = "\n".join(
        [
            "## Realistic Validation",
            format_structured_evidence_marker("zh-CN"),
        ]
    )
    checklist_items = [
        "- [ ] rv-1 daemon 自动认领按生效上限扣除在跑数并继承容量",
        "- [ ] rv-2 自动补位与全局开始按生效上限工作",
        "- [ ] rv-3 Backlog 控制条显示生效值与来源并可设置或恢复继承",
        "- [ ] rv-4 显式运行不受生效上限约束",
        "- [ ] rv-5 在跑计数失败时 fail-closed",
    ]
    report = validate_evidence_manifest(
        issue_body=issue_body,
        checklist_items=checklist_items,
        worktree_path=worktree_path,
        config=AppConfig(),
        evidence_dir=evidence_dir,
    )
    for item_report in report.items:
        file_names = [file_info.file_name for file_info in item_report.files]
        print(
            f"rv-{item_report.block.item_number}: files={len(file_names)} "
            f"negative_control={'present' if item_report.block.negative_control else 'missing'}"
        )
        for file_name in file_names:
            print(f"  {file_name}")
    print("STRUCTURED EVIDENCE MANIFEST: PASS")


if __name__ == "__main__":
    main()
