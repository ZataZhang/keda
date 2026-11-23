"""保留旧开发命令行与归档工具的导入路径。"""

import argparse
import json
import os
from pathlib import Path

from backend.core.use_cases.model_benchmark.matrix import (
    DEFAULT_SUITE,
    BenchmarkInputError,
    BenchmarkRequest,
    execute_cell,
    is_calibrated,
    run_benchmark,
    summarize,
)

__all__ = ["execute_cell", "is_calibrated", "main", "summarize"]


def main() -> int:
    """将旧开发参数映射到随 kc 分发的同一评测用例。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("calibrate", "run", "report"))
    parser.add_argument("--suite", default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    arguments = parser.parse_args()
    try:
        if arguments.mode == "report":
            print(json.dumps(summarize(arguments.output.resolve())["arms"], ensure_ascii=False))
            return 0
        _, successful = run_benchmark(
            BenchmarkRequest(
                mode=arguments.mode,
                repo=Path(__file__).resolve().parents[2],
                output=arguments.output,
                suite=arguments.suite,
                cases=tuple(arguments.cases or ()),
                seconds=arguments.seconds,
                concurrency=arguments.concurrency,
                repeats=arguments.repeats,
                calibration=arguments.calibration,
                live=os.environ.get("KEDA_LIVE_EVAL") == "1",
            ),
            progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True),
        )
    except (OSError, ValueError, BenchmarkInputError) as error:
        parser.error(str(error))
    return int(not successful)
