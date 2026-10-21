#!/usr/bin/env python3
"""全量测试档成本棘轮：把 junit 结果与 ``test-budget.json`` 的上限比对。

用法：
    uv run python scripts/shared/testing/check_test_budget.py --enforce-seconds   # CI 的调用方式
    just test-budget                 # 跑全量档并检查（条数强制，墙钟只报告）
    just test-budget .test-budget-junit.xml   # 只检查已有结果

设计要点：
- 数据源用 pytest 内置 ``--junit-xml``，不用 ``--store-durations``：后者在 pytest 9
  已被移除，而 junit 的 ``<testsuite tests= time=>`` 正好给出条数与整进程墙钟秒数，
  与 xdist 并行也一致。读取方式只做正则取属性，不引入 XML 解析器。
- 条数上限在任何环境都强制，墙钟上限只在显式传 ``--enforce-seconds`` 时强制。原因是
  ``test-budget.json`` 的秒数按 CI 口径校准，而同一条命令在本机要慢得多（口径不可互比），
  拿 CI 的数字去卡本机只会天天误报。CI 步骤显式传这个开关，而不是靠探测 ``CI`` 环境变量
  ——探测失灵时墙钟棘轮会悄悄变成只报告，没有人看得出来。
- 超上限时退出码非零，并把「怎么降下来」写进报错，而不是只说"太慢了"：CI 里失败必须
  自带下一步动作，否则只会让人直接抬上限。
- 在 GitHub Actions 里同时把结果写进 ``$GITHUB_STEP_SUMMARY``，让成本趋势在 PR 页面
  直接可见——棘轮的效力来自"看得见"，不是来自"跑一次脚本"。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BUDGET_PATH = PROJECT_ROOT / "test-budget.json"
DEFAULT_JUNIT_PATH = PROJECT_ROOT / ".test-budget-junit.xml"

# <testsuite> 汇总节点是 junit 报告的第二个标签，8KB 足够覆盖报告头部。
JUNIT_HEAD_BYTES = 8192
JUNIT_SUITE_PATTERN = re.compile(r"<testsuite\b[^>]*>")
JUNIT_ATTRIBUTE_PATTERN = re.compile(r'(\w+)="([^"]*)"')


def _resolve_path(raw_path: str) -> Path:
    """把命令行给的路径解析成绝对路径。

    Args:
        raw_path: 用户传入的路径，可以是相对仓库根的相对路径。

    Returns:
        绝对路径对象。
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def load_ceilings(budget_path: Path) -> dict[str, float]:
    """读取成本上限配置。

    Args:
        budget_path: ``test-budget.json`` 路径。

    Returns:
        含 ``seconds`` 与 ``tests`` 两项上限的字典。

    Raises:
        SystemExit: 文件缺失、JSON 非法或缺少必需字段。
    """
    if not budget_path.is_file():
        _fail(f"成本上限文件不存在：{budget_path}")
    try:
        payload: dict[str, Any] = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as parse_error:
        _fail(f"成本上限文件解析失败：{budget_path}（{parse_error}）")
    ceilings = payload.get("ceilings", {})
    missing_keys = {"seconds", "tests"} - set(ceilings)
    if missing_keys:
        _fail(f"{budget_path} 的 ceilings 缺少字段：{', '.join(sorted(missing_keys))}")
    return {key: float(ceilings[key]) for key in ("seconds", "tests")}


def read_junit_summary(junit_path: Path) -> dict[str, float]:
    """从 junit 报告头部取出条数与墙钟秒数。

    只读文件开头并正则提取 ``<testsuite>`` 的属性，不用 XML 解析器：junit 报告里可能
    含用例输出的任意文本，用 XML 解析就等于把仓库外产生的内容交给解析器，而这里需要
    的五个数字全都在第一个 ``<testsuite`` 标签上，没必要承担那层风险。

    Args:
        junit_path: pytest ``--junit-xml`` 生成的报告路径。

    Returns:
        ``{"seconds": 墙钟秒数, "tests": 用例条数, "failures": 失败数,
        "errors": 错误数, "skipped": 跳过数}``。

    Raises:
        SystemExit: 文件缺失或找不到 ``<testsuite>`` 汇总节点。
    """
    if not junit_path.is_file():
        _fail(f"junit 报告不存在：{junit_path}（跑 `just test-budget` 生成）")
    with open(junit_path, encoding="utf-8") as junit_file:
        head_text = junit_file.read(JUNIT_HEAD_BYTES)
    suite_match = JUNIT_SUITE_PATTERN.search(head_text)
    if not suite_match:
        _fail(f"junit 报告里没有 <testsuite> 汇总节点：{junit_path}")
    attributes = dict(JUNIT_ATTRIBUTE_PATTERN.findall(suite_match.group(0)))

    def _number(attribute_name: str) -> float:
        """读取一个数值属性，缺失按 0 计。

        Args:
            attribute_name: junit 属性名。

        Returns:
            属性值，无法转成数字时返回 0.0。
        """
        try:
            return float(attributes.get(attribute_name, "0"))
        except ValueError:
            return 0.0

    return {
        "seconds": _number("time"),
        "tests": _number("tests"),
        "failures": _number("failures"),
        "errors": _number("errors"),
        "skipped": _number("skipped"),
    }


def _fail(message: str) -> None:
    """打印错误并终止进程。

    Args:
        message: 面向开发者或 CI 日志的错误说明。

    Raises:
        SystemExit: 始终以退出码 2 结束。
    """
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _format_report(
    measured: dict[str, float], ceilings: dict[str, float], *, enforce_seconds: bool
) -> str:
    """生成人类可读的成本报告文本。

    Args:
        measured: ``read_junit_summary`` 的结果。
        ceilings: ``load_ceilings`` 的结果。
        enforce_seconds: 墙钟上限是否参与判定；不参与时在报告里注明，避免被当成"已通过"。

    Returns:
        多行报告字符串，含每项的实测、上限与占比。
    """
    seconds_note = "" if enforce_seconds else "  仅供参考：上限按 CI 口径校准，只在 CI 强制"
    lines = [
        '全量测试档成本（`pytest tests/ -m "not slow and not real_api and not redis"`）：',
        f"  墙钟 {measured['seconds']:8.1f}s / 上限 {ceilings['seconds']:8.1f}s "
        f"({measured['seconds'] / ceilings['seconds'] * 100:5.1f}%){seconds_note}",
        f"  条数 {measured['tests']:8.0f}  / 上限 {ceilings['tests']:8.0f}  "
        f"({measured['tests'] / ceilings['tests'] * 100:5.1f}%)",
        f"  失败 {measured['failures']:.0f} / 错误 {measured['errors']:.0f} / "
        f"跳过 {measured['skipped']:.0f}",
    ]
    return "\n".join(lines)


def _append_step_summary(report_text: str, breaches: list[str]) -> None:
    """在 GitHub Actions 里把报告追加到 step summary。

    Args:
        report_text: 报告正文。
        breaches: 超限项说明，空表示通过。
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    status = "✅ 全量档成本在上限内" if not breaches else "❌ 全量档成本超上限"
    body = f"### {status}\n\n```\n{report_text}\n```\n"
    if breaches:
        body += "\n".join(f"- {breach}" for breach in breaches) + "\n"
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(body + "\n")


def check_budget(
    measured: dict[str, float], ceilings: dict[str, float], *, enforce_seconds: bool
) -> list[str]:
    """比对实测与上限，返回超限项。

    Args:
        measured: 实测成本。
        ceilings: 成本上限。
        enforce_seconds: 是否把墙钟也算作超限；条数上限不受它影响，总是强制。

    Returns:
        超限说明列表；为空表示通过。
    """
    breaches: list[str] = []
    if enforce_seconds and measured["seconds"] > ceilings["seconds"]:
        breaches.append(f"墙钟 {measured['seconds']:.1f}s 超过上限 {ceilings['seconds']:.1f}s")
    if measured["tests"] > ceilings["tests"]:
        breaches.append(f"用例 {measured['tests']:.0f} 条超过上限 {ceilings['tests']:.0f} 条")
    return breaches


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        配置好参数的 ``ArgumentParser``。
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--junit-xml",
        default=str(DEFAULT_JUNIT_PATH),
        help=f"pytest junit 报告路径（默认 {DEFAULT_JUNIT_PATH.name}）",
    )
    parser.add_argument(
        "--budget-file",
        default=str(DEFAULT_BUDGET_PATH),
        help="成本上限配置路径（默认 test-budget.json）",
    )
    parser.add_argument(
        "--enforce-seconds",
        action="store_true",
        help="把墙钟上限也算作超限（上限按 CI 口径校准，只应在 CI 里传；默认只强制条数）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口函数。

    Args:
        argv: 命令行参数，默认取 ``sys.argv[1:]``。

    Returns:
        0 表示成本在上限内；1 表示超限；2 表示输入不可用。
    """
    args = build_parser().parse_args(argv)
    ceilings = load_ceilings(_resolve_path(args.budget_file))
    measured = read_junit_summary(_resolve_path(args.junit_xml))
    report_text = _format_report(measured, ceilings, enforce_seconds=args.enforce_seconds)
    breaches = check_budget(measured, ceilings, enforce_seconds=args.enforce_seconds)
    print(report_text)
    _append_step_summary(report_text, breaches)
    if breaches:
        print(
            "\n超上限的处理方式（二选一，不要直接抬上限了事）：\n"
            "  1) 瘦身：`just test-debt` 看最慢文件与 tests/ 里的重复用例，把近似用例\n"
            "     合并成 parametrize、删掉已失效或被 lint/ruff 覆盖的守卫；\n"
            "  2) 抬高 test-budget.json 的 ceilings，并在提交信息里写明它对应哪一项\n"
            "     新增的真实能力——这样这次增长在 git 历史里是可追溯的决定。\n"
            "   详见 docs/ai-standards/testing.md 的「全量档成本棘轮」小节。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
