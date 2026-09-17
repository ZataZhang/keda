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

import pytest

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
from backend.engines.agent_runner.factory import build_app_config

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


# PRD map check 契约（PRD: 执行开工前的 PRD 引用核验, FR-1 / FR-2）。
# 规则段是默认 execution 模板里的固定文本，渲染自仓库根 ``config.toml``。
_MAP_CHECK_HEADER = "PRD map check (before coding):"
_MAP_CHECK_FOLLOW_CURRENT_CODE = "follow the current code and adapt the plan"
_MAP_CHECK_IMPLEMENT_AS_SPECIFIED = "implement it as specified"
_MAP_CHECK_NONE_DECLARATION = "PRD map check: none"


def _execution_prompt_from_root_config(tmp_path: Path, issue: IssueSummary) -> str:
    """从真实仓库根 ``config.toml`` 渲染 execution prompt。

    必须用 ``build_app_config()`` 装配的 ``PromptConfig``：裸 ``PromptConfig()``
    的 ``phases`` 为空字典，会静默回退到代码内置模板，测不到 config.toml。

    Args:
        tmp_path: pytest 临时目录，用作 worktree 根。
        issue: 待渲染的 Issue。

    Returns:
        渲染后的 execution prompt 全文。
    """
    config = build_app_config()
    return build_prompt(
        issue,
        _worktree_with_prd(tmp_path),
        config.prompts,
        validation_line=build_validation_prompt_line(
            issue,
            config,
            evidence_dir=resolve_issue_evidence_relpath(config, issue),
        ),
    )


def _assert_map_check_contract(prompt: str, *, prompt_name: str) -> None:
    """PRD map check 契约：规则段在场、两条相反方向约束齐备、声明格式与位置正确。"""
    assert _MAP_CHECK_HEADER in prompt, f"{prompt_name} 缺少 PRD map check 规则段"
    assert _MAP_CHECK_FOLLOW_CURRENT_CODE in prompt, f"{prompt_name} 缺少「已变更按当前代码」约束"
    assert (
        _MAP_CHECK_IMPLEMENT_AS_SPECIFIED in prompt
    ), f"{prompt_name} 缺少「计划新增按计划实施」约束"
    assert _MAP_CHECK_NONE_DECLARATION in prompt, f"{prompt_name} 缺少收尾声明格式"
    # 插入位置：Issue body 段之后、Execution rules 段之前。
    assert (
        prompt.index("Issue body:")
        < prompt.index(_MAP_CHECK_HEADER)
        < prompt.index("Execution rules:")
    ), f"{prompt_name} 的 PRD map check 段位置不符合约定"


def _strip_map_check_block(template: str) -> str:
    """删除模板中的 PRD map check 规则段（负控用）。

    Args:
        template: 完整的 execution 模板文本。

    Returns:
        去掉规则段后的模板文本；模板不含该段时原样返回。
    """
    kept_lines: list[str] = []
    inside_map_check_block = False
    for line in template.splitlines():
        if line.strip() == _MAP_CHECK_HEADER:
            inside_map_check_block = True
            continue
        if inside_map_check_block and line.startswith("Execution rules:"):
            inside_map_check_block = False
        if not inside_map_check_block:
            kept_lines.append(line)
    return "\n".join(kept_lines)


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


def test_prompt_contract_execution_prompt_map_check(tmp_path: Path) -> None:
    """execution prompt（真实 config.toml）含 PRD map check 规则段与声明格式。"""
    prompt = _execution_prompt_from_root_config(tmp_path, _issue())
    _assert_map_check_contract(prompt, prompt_name="execution")


def test_prompt_contract_execution_prompt_map_check_without_prd(tmp_path: Path) -> None:
    """未关联 PRD 的 Issue：规则段同样在场（核验无对象，声明写 none）。

    规则段是模板里的静态文本，不依赖 ``{prd_line}`` 是否解析出 PRD 锚点。
    """
    issue = _issue(body="## Summary\n\nNo canonical PRD is attached here.\n")
    prompt = _execution_prompt_from_root_config(tmp_path, issue)
    _assert_map_check_contract(prompt, prompt_name="execution（无 PRD）")


def test_prompt_contract_execution_prompt_map_check_negative_control(
    tmp_path: Path,
) -> None:
    """负控：从真实模板删掉 PRD map check 段后，同一断言必须转红。

    证明上面的绿色来自模板里真实存在的规则段，而不是断言写得过于宽松。
    """
    config = build_app_config()
    template = config.prompts.phases["execution"]
    stripped_template = _strip_map_check_block(template)
    assert stripped_template != template, "负控失效：真实模板里找不到可删除的规则段"

    issue = _issue()
    prompt = build_prompt(
        issue,
        _worktree_with_prd(tmp_path),
        PromptConfig(phases={"execution": stripped_template}),
        validation_line=build_validation_prompt_line(
            issue,
            config,
            evidence_dir=resolve_issue_evidence_relpath(config, issue),
        ),
    )
    with pytest.raises(AssertionError):
        _assert_map_check_contract(prompt, prompt_name="negative-control")
    # 正控：剥离只去掉规则段，模板其余结构完好（排除"渲染本身就坏了"）。
    assert _MAP_CHECK_HEADER not in prompt
    assert "Issue body:" in prompt and "Execution rules:" in prompt


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
