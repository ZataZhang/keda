#!/usr/bin/env python3
"""分文件并行运行完整 tests/ 套件，不依赖 pytest-xdist。"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

_SHARD_COUNT = 4
_COLLECTION_TIMEOUT_SECONDS = 30
_SHARD_TIMEOUT_SECONDS = 250


def _collect_test_node_ids() -> list[str]:
    """收集 tests/ 中全部 pytest node id。"""
    collect_process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--collect-only",
            "-q",
            "tests",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_COLLECTION_TIMEOUT_SECONDS,
    )
    if collect_process.returncode != 0:
        print("COLLECTION_FAILED=pytest could not collect tests/.", file=sys.stderr)
        print(collect_process.stderr[-4000:], file=sys.stderr)
        raise SystemExit(collect_process.returncode)

    node_ids = [
        output_line.strip()
        for output_line in collect_process.stdout.splitlines()
        if output_line.startswith("tests/") and "::" in output_line
    ]
    if not node_ids:
        print("COLLECTION_FAILED=no pytest node ids were collected from tests/.", file=sys.stderr)
        raise SystemExit(1)
    return node_ids


def _partition_by_file(node_ids: list[str]) -> list[list[str]]:
    """按测试文件分组并以收集项数量贪心分配到多个分片。"""
    grouped_node_ids: dict[str, list[str]] = {}
    for node_id in node_ids:
        test_file = node_id.split("::", maxsplit=1)[0]
        grouped_node_ids.setdefault(test_file, []).append(node_id)

    shard_node_ids = [[] for _ in range(_SHARD_COUNT)]
    shard_sizes = [0] * _SHARD_COUNT
    largest_files = sorted(
        grouped_node_ids.items(),
        key=lambda file_nodes: len(file_nodes[1]),
        reverse=True,
    )
    for _, file_node_ids in largest_files:
        smallest_shard = min(range(_SHARD_COUNT), key=shard_sizes.__getitem__)
        shard_node_ids[smallest_shard].extend(file_node_ids)
        shard_sizes[smallest_shard] += len(file_node_ids)

    return [shard for shard in shard_node_ids if shard]


def _run_shard(shard_number: int, node_ids: list[str]) -> tuple[int, str, int]:
    """运行一个保持测试文件边界的 pytest 分片。"""
    started_at = time.monotonic()
    try:
        test_process = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "-q",
                *node_ids,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_SHARD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        elapsed_seconds = time.monotonic() - started_at
        return shard_number, "TIMEOUT: pytest exceeded the 250-second shard limit", 124

    elapsed_seconds = time.monotonic() - started_at
    test_output = f"{test_process.stdout}\n{test_process.stderr}"
    summary_lines = [
        output_line.strip()
        for output_line in test_output.splitlines()
        if re.search(r"\b(?:passed|failed|skipped|error|errors)\b", output_line)
        and re.search(r"\bin \d+(?:\.\d+)?s\b", output_line)
    ]
    result_summary = summary_lines[-1] if summary_lines else "pytest returned no summary line"
    if test_process.returncode != 0:
        result_summary = f"{result_summary}; inspect shard output in stderr"
        print(f"SHARD_{shard_number}_FAILURE={test_output[-5000:]}", file=sys.stderr)
    return shard_number, f"{result_summary}; elapsed={elapsed_seconds:.2f}s", test_process.returncode


def main() -> int:
    """收集并完整执行 tests/，以非零退出码保留任一分片失败。"""
    node_ids = _collect_test_node_ids()
    shards = _partition_by_file(node_ids)
    print(f"COLLECTED_NODE_IDS={len(node_ids)}")
    print(f"COLLECTED_TEST_FILES={len({node_id.split('::', 1)[0] for node_id in node_ids})}")
    print(f"PARALLEL_SHARDS={len(shards)}; assignment preserves each test file")

    with ThreadPoolExecutor(max_workers=len(shards)) as executor:
        shard_results = list(
            executor.map(
                lambda indexed_shard: _run_shard(*indexed_shard),
                enumerate(shards, start=1),
            )
        )

    shard_failures = []
    for shard_number, result_summary, exit_code in shard_results:
        print(f"SHARD_{shard_number}_EXIT={exit_code}; {result_summary}")
        if exit_code != 0:
            shard_failures.append(shard_number)

    if shard_failures:
        print(f"FULL_SUITE_FAILED_SHARDS={','.join(map(str, shard_failures))}", file=sys.stderr)
        return 1

    print("FULL_SUITE_RESULT=PASS; every collected node id from tests/ was executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
