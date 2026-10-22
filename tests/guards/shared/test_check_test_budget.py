"""守护全量档成本棘轮脚本契约的守卫测试（guard test；被测对象：
``scripts/shared/testing/check_test_budget.py``）。

棘轮的价值全在"超限时真的变红、没超限时不误报"，这两件事都不会让别的测试变红——脚本
被改坏之后 CI 只会一直绿。所以这里直接驱动脚本入口，覆盖它最关键的一条分叉：墙钟上限
只在显式 ``--enforce-seconds`` 时才算数（上限按 CI 口径校准，本机同一条命令慢得多，
数字不可互比），条数上限则在任何环境都强制。

本文件位于 ``tests/guards/shared/``，失败意味着共享脚本违反了它的契约。正确做法是修复
脚本，而不是修改本文件让测试通过；仅当约定本身需要变更时才改本文件，并同步更新
``docs/ai-standards/testing.md`` 的「本地核心档」「全量档成本棘轮」小节。详见该文档的
Guard Tests 小节。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# 仓库根推导而非 CWD 相对路径：守卫必须与 pytest 的启动目录解耦。
_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "testing" / "check_test_budget.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("check_test_budget_under_guard", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
budget_script = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(budget_script)

_CEILINGS = {"seconds": 170.0, "tests": 1500.0}
JUNIT_FILE_NAME = "junit.xml"


@pytest.fixture(autouse=True)
def _isolate_github_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """在 GitHub Actions 里跑本文件时，别把被测脚本的报告写进真实的 step summary。"""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _write_inputs(directory: Path, *, tests: int, seconds: float) -> list[str]:
    """写一份上限文件与 ``junit.xml``，返回把脚本指向它们的命令行参数。"""
    budget_path = directory / "test-budget.json"
    junit_path = directory / JUNIT_FILE_NAME
    budget_path.write_text(json.dumps({"ceilings": _CEILINGS}), encoding="utf-8")
    junit_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="0" failures="0" skipped="1" tests="{tests}" '
        f'time="{seconds:.3f}" timestamp="2026-10-03T00:00:00"></testsuite></testsuites>',
        encoding="utf-8",
    )
    return ["--budget-file", str(budget_path), "--junit-xml", str(junit_path)]


def _measured(*, seconds: float, tests: float) -> dict[str, float]:
    """构造 ``read_junit_summary`` 形状的实测结果。"""
    return {"seconds": seconds, "tests": tests, "failures": 0.0, "errors": 0.0, "skipped": 0.0}


def test_read_junit_summary_extracts_counts_and_wall_clock(tmp_path: Path) -> None:
    """条数与整进程墙钟都取自第一个 ``<testsuite>`` 标签。"""
    _write_inputs(tmp_path, tests=1306, seconds=125.5)

    assert budget_script.read_junit_summary(tmp_path / JUNIT_FILE_NAME) == {
        "seconds": 125.5,
        "tests": 1306.0,
        "failures": 0.0,
        "errors": 0.0,
        "skipped": 1.0,
    }


def test_read_junit_summary_exits_when_the_report_is_missing_or_has_no_suite(
    tmp_path: Path,
) -> None:
    """没有输入就没有判定：缺报告或报告里没有汇总节点都以退出码 2 终止，而不是当成通过。"""
    with pytest.raises(SystemExit) as missing_report:
        budget_script.read_junit_summary(tmp_path / "absent.xml")
    suiteless_report_path = tmp_path / "suiteless.xml"
    suiteless_report_path.write_text("<testsuites></testsuites>", encoding="utf-8")
    with pytest.raises(SystemExit) as suiteless_report:
        budget_script.read_junit_summary(suiteless_report_path)

    assert missing_report.value.code == 2
    assert suiteless_report.value.code == 2


@pytest.mark.parametrize(
    ("enforce_seconds", "expected_breach_count"),
    [(True, 2), (False, 1)],
)
def test_check_budget_counts_wall_clock_only_when_seconds_are_enforced(
    enforce_seconds: bool, expected_breach_count: int
) -> None:
    """条数超限总是算超限；墙钟超限只在显式强制时才算。"""
    breaches = budget_script.check_budget(
        _measured(seconds=200.0, tests=1600.0), _CEILINGS, enforce_seconds=enforce_seconds
    )

    assert len(breaches) == expected_breach_count
    assert any("用例 1600 条超过上限 1500 条" in breach for breach in breaches)


def test_check_budget_allows_a_run_exactly_at_the_ceilings() -> None:
    """上限是"不得超过"：恰好等于上限不算超限。"""
    breaches = budget_script.check_budget(
        _measured(seconds=170.0, tests=1500.0), _CEILINGS, enforce_seconds=True
    )

    assert breaches == []


def test_main_reports_but_does_not_fail_on_wall_clock_without_the_enforce_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """本机偏慢时的墙钟（330s）远超 CI 校准的上限，但不带开关时只出报告、不判红。"""
    cli_arguments = _write_inputs(tmp_path, tests=1306, seconds=330.0)

    exit_code = budget_script.main(cli_arguments)

    assert exit_code == 0
    assert "仅供参考" in capsys.readouterr().out


def test_main_fails_on_wall_clock_when_seconds_are_enforced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI 带 ``--enforce-seconds``：同样的 330s 判红，并给出下一步动作而不是只说"太慢"。"""
    cli_arguments = _write_inputs(tmp_path, tests=1306, seconds=330.0)

    exit_code = budget_script.main([*cli_arguments, "--enforce-seconds"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "仅供参考" not in captured.out
    assert "超上限的处理方式" in captured.err


def test_main_fails_on_test_count_even_without_the_enforce_flag(tmp_path: Path) -> None:
    """条数是确定值，在任何环境都强制；本机跑 `just test-budget` 照样会被它拦住。"""
    cli_arguments = _write_inputs(tmp_path, tests=1600, seconds=100.0)

    assert budget_script.main(cli_arguments) == 1


def test_main_passes_inside_both_ceilings(tmp_path: Path) -> None:
    """两项都在上限内且开了墙钟强制时通过。"""
    cli_arguments = _write_inputs(tmp_path, tests=1306, seconds=125.0)

    assert budget_script.main([*cli_arguments, "--enforce-seconds"]) == 0


def test_main_appends_the_verdict_to_the_github_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成本趋势要在 PR 页面可见：判定与超限项都写进 ``$GITHUB_STEP_SUMMARY``。"""
    summary_path = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    cli_arguments = _write_inputs(tmp_path, tests=1600, seconds=100.0)

    budget_script.main(cli_arguments)

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "❌" in summary_text
    assert "用例 1600 条超过上限 1500 条" in summary_text
