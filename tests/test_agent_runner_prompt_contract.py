"""prompt 契约 golden 测试（PRD: iar-prd-skill-alignment, FR-5 / rv-2）。

直接调用真实的 prompt 构建函数（execution / recovery / continuation / closeout
四类 prompt + validation 行 + Issue body 区块），断言：

1. 格式教学片段（Change Log 六字段示例、rv-id 命名/截图分工教学）**零命中**——
   它们已并入 prd skill 的 Machine Contract v1；
2. 契约指针 :data:`PRD_MACHINE_CONTRACT_POINTER` 恰好出现一次；
3. runner 私有语义（归档归属、``[~]`` 出口规则、manifest 规范）仍在且单源。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models.agent_runner import (
    DeliveryGateFailureKind,
    IssueSummary,
    PromptConfig,
)
from backend.core.shared.prd_machine_contract import PRD_MACHINE_CONTRACT_POINTER
from backend.core.use_cases.agent_runner_closeout import (
    CloseoutPromptContext,
    build_closeout_allowed_scope,
    build_closeout_prompt,
)
from backend.core.use_cases.agent_runner_feedback import (
    PRD_ARCHIVE_OWNERSHIP_RULE,
    RUNNER_OWNED_CHECKLIST_ITEM_RULE,
    build_progress_continuation_prompt,
    build_prompt,
    build_recovery_prompt,
)
from backend.core.use_cases.agent_runner_validation import (
    AppConfig,
    build_issue_validation_section,
    build_validation_prompt_line,
    resolve_issue_evidence_relpath,
)

_PRD_RELATIVE_PATH = "tasks/pending/P1-FEAT-20990101-000000-demo.md"

_ISSUE_BODY_WITH_PRD = f"""## Summary

Tracked task.

- PRD path: `{_PRD_RELATIVE_PATH}`

## Realistic Validation

- [ ] **行为 A 真实验证**：通过 `demo run` 验证输出。
"""

_PRD_TEXT = """# PRD: Demo

## 1. Introduction & Goals

### Realistic Validation

- [ ] **行为 A 真实验证**：通过 `demo run` 验证输出。

## 9. Acceptance Checklist

- [ ] demo run 输出符合预期
"""

# 已剥离进 prd skill Machine Contract 的格式教学片段；任一命中即回归。
_TEACHING_FRAGMENTS = (
    "Markdown tables are NOT parsed",
    "- Type: <scope",
    "- Before: <prior wording or state>",
    "- Review: <review status>",
    "with Type, Before, After, Reason, Impact, and Review",
    "(PNG screenshots for UI",
    "named `rv-<item-number>-<slug>.<ext>` (PNG",
    "截图/screenshot, pdf",
)


def _issue(body: str = _ISSUE_BODY_WITH_PRD, number: int = 42) -> IssueSummary:
    return IssueSummary(
        number=number,
        title="Demo",
        url=f"https://github.com/example/repo/issues/{number}",
        body=body,
        labels=("agent/ready",),
    )


def _worktree_with_prd(tmp_path: Path) -> Path:
    prd_path = tmp_path / _PRD_RELATIVE_PATH
    prd_path.parent.mkdir(parents=True)
    prd_path.write_text(_PRD_TEXT, encoding="utf-8")
    return tmp_path


def _assert_prompt_contract(prompt: str, *, prompt_name: str) -> None:
    """四类 prompt 共用的契约断言：教学零命中、指针与归档归属各一次。"""
    for fragment in _TEACHING_FRAGMENTS:
        assert fragment not in prompt, f"{prompt_name} 仍含格式教学片段: {fragment!r}"
    assert ".iar/evidence" not in prompt, f"{prompt_name} 仍含 legacy 证据目录字面量"
    assert (
        prompt.count(PRD_MACHINE_CONTRACT_POINTER) == 1
    ), f"{prompt_name} 的契约指针应恰好出现一次"
    assert (
        prompt.count(PRD_ARCHIVE_OWNERSHIP_RULE) == 1
    ), f"{prompt_name} 的归档归属规则应恰好出现一次"


def test_prompt_contract_execution_prompt(tmp_path: Path) -> None:
    """execution prompt：教学零命中，指针与 runner 语义各一次。"""
    worktree_path = _worktree_with_prd(tmp_path)
    issue = _issue()
    prompt = build_prompt(
        issue,
        worktree_path,
        PromptConfig(),
        validation_line=build_validation_prompt_line(
            issue, AppConfig(), evidence_dir=resolve_issue_evidence_relpath(AppConfig(), issue)
        ),
    )
    _assert_prompt_contract(prompt, prompt_name="execution")
    # 证据落点按 PRD stem 子目录解析。
    assert "tasks/evidence/P1-FEAT-20990101-000000-demo/" in prompt


def test_prompt_contract_recovery_prompt(tmp_path: Path) -> None:
    """recovery prompt：教学零命中，指针与 runner 语义各一次。"""
    worktree_path = _worktree_with_prd(tmp_path)
    issue = _issue()
    prompt = build_recovery_prompt(
        issue,
        worktree_path,
        recovery_attempt=1,
        max_recovery_attempts=3,
        failure_summary="verification failed",
        evidence_dir=resolve_issue_evidence_relpath(AppConfig(), issue),
    )
    _assert_prompt_contract(prompt, prompt_name="recovery")


def test_prompt_contract_continuation_prompt(tmp_path: Path) -> None:
    """continuation prompt：教学零命中，指针与 runner 语义各一次。"""
    worktree_path = _worktree_with_prd(tmp_path)
    prompt = build_progress_continuation_prompt(
        _issue(),
        worktree_path,
        failure_summary="previous attempt failed",
    )
    _assert_prompt_contract(prompt, prompt_name="continuation")


def test_prompt_contract_closeout_prompt(tmp_path: Path) -> None:
    """closeout prompt：教学零命中，指针、归档归属与 [~] 出口规则各一次。"""
    worktree_path = _worktree_with_prd(tmp_path)
    config = AppConfig()
    issue = _issue()
    prompt = build_closeout_prompt(
        CloseoutPromptContext(
            issue=issue,
            worktree_path=worktree_path,
            gate_failure_message="Acceptance Checklist has unchecked items",
            kind=DeliveryGateFailureKind.CHECKLIST_UNCHECKED,
            allowed_scope=build_closeout_allowed_scope(issue, config),
        )
    )
    _assert_prompt_contract(prompt, prompt_name="closeout")
    # runner 私有语义：[~] 出口规则保留且恰好一次。
    assert prompt.count(RUNNER_OWNED_CHECKLIST_ITEM_RULE) == 1


def test_prompt_contract_validation_line() -> None:
    """validation 行：门禁语义保留、rv 命名教学消失、指针不复述（单源纪律）。"""
    config = AppConfig()
    issue = _issue()
    prompt_line = build_validation_prompt_line(
        issue, config, evidence_dir=resolve_issue_evidence_relpath(config, issue)
    )
    # 指针由 PRD 块与 Issue body 各携带一次；本行只指方向、不整段复述。
    assert PRD_MACHINE_CONTRACT_POINTER not in prompt_line
    assert "Machine Contract" in prompt_line
    assert "tasks/evidence/P1-FEAT-20990101-000000-demo/" in prompt_line
    for fragment in _TEACHING_FRAGMENTS:
        assert fragment not in prompt_line
    # runner 门禁语义（RV 脚本不进代码 diff）保留。
    assert "must never enter the code diff" in prompt_line


def test_prompt_contract_issue_body_section() -> None:
    """Issue body 的 Realistic Validation 区块：指针在场、无 legacy 字面量。"""
    section = build_issue_validation_section(
        checklist_items=["- [ ] item"],
        waiver_reason=None,
        evidence_dir="tasks/evidence/P1-FEAT-20990101-000000-demo",
    )
    assert PRD_MACHINE_CONTRACT_POINTER in section
    assert "tasks/evidence/P1-FEAT-20990101-000000-demo/" in section
    assert ".iar/evidence" not in section


def test_prompt_contract_pointer_single_source() -> None:
    """指针文本只在一处定义，各构建函数引用同一常量（不可复述变体）。"""
    assert "Machine Contract v1" in PRD_MACHINE_CONTRACT_POINTER
    assert "prd skill" in PRD_MACHINE_CONTRACT_POINTER
