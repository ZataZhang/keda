#!/usr/bin/env python3
"""测试瘦身证据：找出 tests/ 里的重复用例块，并按耗时排出最该动的文件。

用法：
    uv run python scripts/shared/testing/test_debt_report.py [--top 15]
    just test-debt

设计要点：
- 阈值与 ``hooks/shared/run_jscpd_duplication_check.py`` 保持一致（min-lines 5 /
  min-tokens 50），这样这里看到的重复与 ``just lint --reuse`` 拦下的是同一件事，
  不会出现"体检说没有、门禁说有"。
- 只扫 ``tests/``：全仓扫描会把 src/ 与 tests/ 互相牵连，而本工具回答的是"哪些用例
  是复制粘贴出来的"，这是套件膨胀的主要通道。
- 与 ``.test_durations.json`` 关联：重复且慢的文件才是真正该先动的，单纯重复但不值钱
  的用例不值得为它开一个 PR。
- 只出证据不改代码。删或合并用例是会削弱安全网的操作，必须逐条人审。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DURATIONS_PATH = PROJECT_ROOT / ".test_durations.json"
BASELINE_CORE_SET_PATH = PROJECT_ROOT / "tests" / "core_set.txt"

JSCPD_MIN_LINES = "5"
JSCPD_MIN_TOKENS = "50"
JSCPD_FORMATS = "python"
SCAN_TARGET = "tests"
# 与 hooks/shared/run_jscpd_duplication_check.py 的 JSCPD_IGNORE_GLOBS 对齐，另加一条：
# tests/playwright-e2e/node_modules 里 vendored 的 .py（flatted 等）会污染体检结果。
JSCPD_IGNORE_GLOBS = (
    "**/node_modules/**,**/dist/**,**/build/**,**/.next/**,**/coverage/**," "**/playwright-e2e/**"
)


def run_jscpd_clone_report() -> list[dict[str, Any]]:
    """对 ``tests/`` 跑一次 jscpd，返回重复块列表。

    Returns:
        jscpd JSON 报告里的 ``duplicates`` 数组；jscpd 不可用时返回空列表并打印提示。
    """
    if shutil.which("jscpd") is None:
        print(
            "跳过重复检测：jscpd 不在 PATH。装好后重试，"
            "或用 `just lint --reuse` 看改动文件的重复。",
            file=sys.stderr,
        )
        return []
    with tempfile.TemporaryDirectory(prefix="test-debt-") as temp_dir_name:
        report_path = Path(temp_dir_name) / "jscpd-report.json"
        completed = subprocess.run(
            [
                "jscpd",
                "--min-lines",
                JSCPD_MIN_LINES,
                "--min-tokens",
                JSCPD_MIN_TOKENS,
                "--reporters",
                "json",
                "--output",
                temp_dir_name,
                "--format",
                JSCPD_FORMATS,
                "--ignore",
                JSCPD_IGNORE_GLOBS,
                "--exitCode",
                "0",
                "--noTips",
                SCAN_TARGET,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
        if not report_path.is_file():
            print(
                f"jscpd 没有产出报告（exit={completed.returncode}）："
                f"{completed.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return []
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    return list(payload.get("duplicates", []))


def load_file_seconds() -> dict[str, float]:
    """读取每个测试文件的实测串行耗时。

    Returns:
        ``{文件路径: 串行秒数}``；基线缺失时为空字典。
    """
    if not DURATIONS_PATH.is_file():
        return {}
    try:
        payload = json.loads(DURATIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = payload.get("files", {}) if isinstance(payload, dict) else {}
    return {
        str(file_name): float(cost.get("seconds", 0.0))
        for file_name, cost in files.items()
        if isinstance(cost, dict)
    }


def aggregate_by_file_pair(
    duplicates: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, float]]:
    """把重复块按文件对聚合，得到"哪两个文件在互相抄"。

    Args:
        duplicates: jscpd 的 ``duplicates`` 数组。

    Returns:
        ``{(文件A, 文件B): {"blocks": 块数, "lines": 重复行数}}``，键顺序已归一。
    """
    pair_totals: dict[tuple[str, str], dict[str, float]] = {}
    for duplicate in duplicates:
        first_file = str(duplicate.get("firstFile", {}).get("name", ""))
        second_file = str(duplicate.get("secondFile", {}).get("name", ""))
        if not first_file or not second_file:
            continue
        pair_key = tuple(sorted((first_file, second_file)))
        clone_lines = float(duplicate.get("lines", 0) or 0)
        bucket = pair_totals.setdefault(pair_key, {"blocks": 0.0, "lines": 0.0})
        bucket["blocks"] += 1
        bucket["lines"] += clone_lines
    return pair_totals


def aggregate_by_file(duplicates: list[dict[str, Any]]) -> dict[str, float]:
    """统计每个文件参与的重复行数。

    Args:
        duplicates: jscpd 的 ``duplicates`` 数组。

    Returns:
        ``{文件路径: 参与重复的行数}``。
    """
    file_lines: dict[str, float] = defaultdict(float)
    for duplicate in duplicates:
        clone_lines = float(duplicate.get("lines", 0) or 0)
        for side in ("firstFile", "secondFile"):
            file_name = str(duplicate.get(side, {}).get("name", ""))
            if file_name:
                file_lines[file_name] += clone_lines
    return dict(file_lines)


def _core_set_files() -> set[str]:
    """读取本地核心档名单，用于标注重复文件是否也在每次提交时付时间。

    Returns:
        名单内文件路径集合。
    """
    if not BASELINE_CORE_SET_PATH.is_file():
        return set()
    return {
        line.strip()
        for line in BASELINE_CORE_SET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def print_report(top_count: int) -> int:
    """打印测试瘦身证据报告。

    Args:
        top_count: 每个榜单显示多少条。

    Returns:
        进程退出码：始终为 0，本工具只给证据不做判定。
    """
    duplicates = run_jscpd_clone_report()
    file_seconds = load_file_seconds()
    core_files = _core_set_files()

    print("=== 1. 重复用例块（tests/ 内部，jscpd 阈值与 just lint --reuse 一致）===")
    if not duplicates:
        print("  没有检出重复块，或 jscpd 不可用。")
    else:
        pair_totals = aggregate_by_file_pair(duplicates)
        ranked_pairs = sorted(pair_totals.items(), key=lambda kv: kv[1]["lines"], reverse=True)
        print(f"  共 {len(duplicates)} 个重复块，涉及 {len(ranked_pairs)} 个文件对：")
        for (file_a, file_b), total in ranked_pairs[:top_count]:
            print(
                f"  {total['lines']:6.0f} 行 / {total['blocks']:3.0f} 块  "
                f"{file_a}  <->  {file_b}"
            )
        print(
            "\n  处理方式：近似用例合成 parametrize；确有区别的部分抽进同目录的 "
            "*_test_support.py 共享夹具（如 tests/realdb_test_support.py）。"
        )

    print("\n=== 2. 最贵的重复文件（重复行数 x 实测串行耗时）===")
    per_file_lines = aggregate_by_file(duplicates)
    ranked_files = sorted(
        per_file_lines.items(),
        key=lambda kv: kv[1] * max(file_seconds.get(kv[0].split("tests/")[-1], 0.0), 0.001),
        reverse=True,
    )
    if not ranked_files:
        print("  没有可排序的重复文件。")
    for file_name, clone_lines in ranked_files[:top_count]:
        normalized_name = file_name.split("tests/")[-1]
        relative_path = f"tests/{normalized_name}"
        seconds = file_seconds.get(relative_path, 0.0)
        tier_flag = "本地档" if relative_path in core_files else "仅 CI"
        cost_line = f"{clone_lines:6.0f} 行重复 / 串行 {seconds:6.1f}s / {tier_flag:<6} "
        print(f"  {cost_line}{relative_path}")

    print("\n=== 3. 最慢文件榜（判据同 `just test-tier-report`）===")
    if not file_seconds:
        print(f"  耗时基线缺失：先跑 `just test-core-refresh` 生成 {DURATIONS_PATH.name}。")
    else:
        for file_name, seconds in sorted(file_seconds.items(), key=lambda kv: kv[1], reverse=True)[
            :top_count
        ]:
            tier_flag = "本地档" if file_name in core_files else "仅 CI"
            print(f"  {seconds:7.1f}s / {tier_flag:<6} {file_name}")

    print(
        "\n下一步不是脚本能替你决定的：删用例或合并用例等于削弱安全网，"
        "请逐条确认它保护的真实失败模式还在不在，再动手。"
        "全量档的成本上限见 test-budget.json。"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        配置好参数的 ``ArgumentParser``。
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=15, help="每个榜单显示条数")
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口函数。

    Args:
        argv: 命令行参数，默认取 ``sys.argv[1:]``。

    Returns:
        报告打印函数的退出码。
    """
    args = build_parser().parse_args(argv)
    return print_report(args.top)


if __name__ == "__main__":
    raise SystemExit(main())
