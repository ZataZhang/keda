"""从历史判分抽取的冻结契约检查；不覆盖候选已有回归文件。"""

from __future__ import annotations


import subprocess


from pathlib import Path


import pytest


from backend.core.shared.models.agent_runner import (
    AppConfig,
    CommandResult,
    IssueSummary,
    ValidationConfig,
)


from backend.core.use_cases.agent_runner_validation import (
    ValidationEvidenceError,
    ensure_validation_commands_pass,
)


from tests.conftest import FakeProcessRunner


_ISSUE_BODY_WITH_VALIDATION = """## Summary

Tracked task.

## Realistic Validation

The executing agent MUST run each item.

- [ ] **行为 A 真实验证**：通过 `demo run` 验证输出。
- [ ] **行为 B 真实验证**：通过 `demo serve` 验证页面。
"""


def _issue(body: str = _ISSUE_BODY_WITH_VALIDATION, number: int = 42) -> IssueSummary:
    return IssueSummary(
        number=number,
        title="Demo",
        url=f"https://github.com/example/repo/issues/{number}",
        body=body,
        labels=("agent/automated-review-passed",),
    )


def _legacy_config(**validation_overrides: object) -> AppConfig:
    """显式 legacy 证据目录（``.iar/evidence``）配置。

    锁定 PRD 证据选址翻转前的行为：显式配置该值的仓库逐字节不变——证据落
    ``.iar/evidence/`` 扁平目录、整目录排除、发布前拦截所有证据路径。
    """
    return AppConfig(
        validation=ValidationConfig(evidence_dir=".iar/evidence", **validation_overrides)
    )


_STRUCTURED_ISSUE_BODY = """## Summary

Tracked task.

<!-- kc:structured-evidence version=1 language="zh-CN" -->

## Realistic Validation

The executing agent MUST run each item.

- [ ] **行为 A 真实验证**：通过 `demo run` 验证输出。
- [ ] **行为 B 真实验证**：通过 `demo serve` 验证页面。
"""


def _write_manifest(evidence_dir: Path, **overrides: object) -> None:
    manifest = {
        "version": 1,
        "language": "zh-CN",
        "items": [
            {
                "item_number": 1,
                "item_name": "行为 A 真实验证",
                "command": "demo run",
                "evidence_files": ["rv-1-run.txt"],
                "output_summary": "demo run 输出匹配预期。",
                "explanation": "真实运行了 demo run 命令。",
                "risks": "无",
                "negative_control": "改坏 demo run 后重跑",
                "expected_fail": "demo run 非零退出",
            },
            {
                "item_number": 2,
                "item_name": "行为 B 真实验证",
                "command": "demo serve",
                "evidence_files": ["rv-2-serve.txt"],
                "output_summary": "demo serve 启动成功。",
                "explanation": "真实启动了 demo serve。",
                "risks": "仅本地验证",
                "negative_control": "停掉 demo serve 后访问",
                "expected_fail": "连接被拒绝",
            },
        ],
    }
    manifest.update(overrides)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    import json

    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def test_ensure_validation_commands_pass_reports_every_failing_item(
    tmp_path: Path,
) -> None:
    """门禁聚合全部失败：两条不相干失败一次报出，而不是只报第一条（决策二）。"""
    _write_manifest(tmp_path / ".iar" / "evidence")
    failing_responses = {
        ("bash", "-lc", "demo run"): CommandResult(
            command=("bash", "-lc", "demo run"),
            return_code=1,
            stdout="",
            stderr="run boom",
        ),
        ("bash", "-lc", "demo serve"): CommandResult(
            command=("bash", "-lc", "demo serve"),
            return_code=2,
            stdout="",
            stderr="serve boom",
        ),
    }
    runner = FakeProcessRunner(responses=failing_responses)

    with pytest.raises(ValidationEvidenceError) as exc_info:
        ensure_validation_commands_pass(
            _issue(body=_STRUCTURED_ISSUE_BODY), tmp_path, _legacy_config(), runner
        )

    failure_text = str(exc_info.value)
    assert "run boom" in failure_text
    assert "serve boom" in failure_text
    assert "demo run" in failure_text
    assert "demo serve" in failure_text
    # 此处验证门禁调用顺序；真实子进程行为由同题 CLI 用例覆盖。
    assert ["bash", "-lc", "demo run"] in runner.raw_calls
    assert ["bash", "-lc", "demo serve"] in runner.raw_calls


def test_ensure_validation_commands_pass_timeout_does_not_stop_remaining_items(
    tmp_path: Path,
) -> None:
    """单题超时后其余题目照常复跑，并在聚合错误中标注超时（决策二）。"""
    _write_manifest(tmp_path / ".iar" / "evidence")

    class _TimeoutOnFirstRunner(FakeProcessRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            command_tuple = tuple(command)
            self.calls.append(list(command))
            self.raw_calls.append(list(command))
            if command_tuple == ("bash", "-lc", "demo run"):
                raise subprocess.TimeoutExpired(cmd=list(command), timeout=kwargs.get("timeout"))
            return CommandResult(command_tuple, 0, "", "")

    runner = _TimeoutOnFirstRunner()

    with pytest.raises(ValidationEvidenceError) as exc_info:
        ensure_validation_commands_pass(
            _issue(body=_STRUCTURED_ISSUE_BODY), tmp_path, _legacy_config(), runner
        )

    failure_text = str(exc_info.value)
    assert "timed out" in failure_text.casefold()
    assert "demo run" in failure_text
    assert ["bash", "-lc", "demo serve"] in runner.raw_calls


def test_ensure_validation_commands_pass_aggregates_assertion_and_timeout(
    tmp_path: Path,
) -> None:
    """一条断言不匹配 + 一条超时：门禁一次报出两条（决策二验收）。"""
    import json

    evidence_dir = tmp_path / ".iar" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "rv-1-fake.txt").write_text("placeholder", encoding="utf-8")
    (evidence_dir / "rv-2-fake.txt").write_text("placeholder", encoding="utf-8")
    manifest = {
        "version": 1,
        "language": "zh-CN",
        "items": [
            {
                "item_number": 1,
                "item_name": "断言不匹配检查点",
                "command": "demo run",
                "evidence_files": ["rv-1-fake.txt"],
                "output_summary": "命令 exit 0。",
                "explanation": "说明。",
                "risks": "无",
                "negative_control": "改坏断言",
                "expected_fail": "断言不命中",
                "stdout_assertions": [{"pattern": "EXPECTED-ASSERTION"}],
            },
            {
                "item_number": 2,
                "item_name": "超时检查点",
                "command": "demo serve",
                "evidence_files": ["rv-2-fake.txt"],
                "output_summary": "命令超时。",
                "explanation": "说明。",
                "risks": "无",
                "negative_control": "让命令睡过头",
                "expected_fail": "超时",
            },
        ],
    }
    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    class _AssertionAndTimeoutRunner(FakeProcessRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            command_tuple = tuple(command)
            self.calls.append(list(command))
            self.raw_calls.append(list(command))
            if command_tuple == ("bash", "-lc", "demo run"):
                return CommandResult(command_tuple, 0, "ACTUAL-OUTPUT", "")
            if command_tuple == ("bash", "-lc", "demo serve"):
                raise subprocess.TimeoutExpired(cmd=list(command), timeout=kwargs.get("timeout"))
            return CommandResult(command_tuple, 0, "", "")

    runner = _AssertionAndTimeoutRunner()

    with pytest.raises(ValidationEvidenceError) as exc_info:
        ensure_validation_commands_pass(
            _issue(body=_STRUCTURED_ISSUE_BODY), tmp_path, _legacy_config(), runner
        )

    failure_text = str(exc_info.value)
    assert "EXPECTED-ASSERTION" in failure_text
    assert "timed out" in failure_text.casefold()
    assert "demo run" in failure_text
    assert "demo serve" in failure_text


def test_ensure_validation_commands_pass_aggregates_assertion_failures(
    tmp_path: Path,
) -> None:
    """两条 item 的断言各自不命中时，门禁一次列出两处的期望 pattern。"""
    import json

    evidence_dir = tmp_path / ".iar" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "rv-1-fake.txt").write_text("placeholder", encoding="utf-8")
    (evidence_dir / "rv-2-fake.txt").write_text("placeholder", encoding="utf-8")
    manifest = {
        "version": 1,
        "language": "zh-CN",
        "items": [
            {
                "item_number": 1,
                "item_name": "假绿灯检查点 A",
                "command": "demo run",
                "evidence_files": ["rv-1-fake.txt"],
                "output_summary": "命令 exit 0。",
                "explanation": "说明。",
                "risks": "无",
                "negative_control": "改坏命令",
                "expected_fail": "断言不命中",
                "stdout_assertions": [{"pattern": "EXPECTED-ONE"}],
            },
            {
                "item_number": 2,
                "item_name": "假绿灯检查点 B",
                "command": "demo serve",
                "evidence_files": ["rv-2-fake.txt"],
                "output_summary": "命令 exit 0。",
                "explanation": "说明。",
                "risks": "无",
                "negative_control": "改坏命令",
                "expected_fail": "断言不命中",
                "stdout_assertions": [{"pattern": "EXPECTED-TWO"}],
            },
        ],
    }
    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    runner = FakeProcessRunner(
        responses={
            ("bash", "-lc", "demo run"): CommandResult(
                command=("bash", "-lc", "demo run"),
                return_code=0,
                stdout="ACTUAL-ONE",
                stderr="",
            ),
            ("bash", "-lc", "demo serve"): CommandResult(
                command=("bash", "-lc", "demo serve"),
                return_code=0,
                stdout="ACTUAL-TWO",
                stderr="",
            ),
        }
    )

    with pytest.raises(ValidationEvidenceError) as exc_info:
        ensure_validation_commands_pass(
            _issue(body=_STRUCTURED_ISSUE_BODY), tmp_path, _legacy_config(), runner
        )

    failure_text = str(exc_info.value)
    assert "EXPECTED-ONE" in failure_text
    assert "EXPECTED-TWO" in failure_text
    assert "ACTUAL-ONE" in failure_text
    assert "ACTUAL-TWO" in failure_text
