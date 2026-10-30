#!/usr/bin/env python3
"""rv-4 evidence: a malformed manifest executes no evidence command.

Red: the manifest misses the required ``risks`` field and the item's command
would create a probe file. The self-check must report the format error and
leave no probe file behind. Negative control: fixing only the missing field
lets the same command enter the normal per-item flow and create the probe.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rv4-") as temporary_directory_text:
        fixture_dir = Path(temporary_directory_text) / "evidence"
        probe_marker = Path(temporary_directory_text) / "probe-ran.txt"
        broken_block = harness.base_item(1, command=f"touch {probe_marker}")
        del broken_block["risks"]
        harness.write_manifest(fixture_dir, [broken_block])

        print("== phase 1: missing required field (expected RED, no execution) ==")
        broken_run = harness.run_kc(["evidence", "check", "--evidence", str(fixture_dir)])
        harness.print_streams(broken_run)
        print(f"broken_exit={broken_run.returncode}")
        harness.require_exit(broken_run, 1, "broken manifest run")
        harness.require(
            "risks" in broken_run.stdout,
            "format error does not name the missing `risks` field",
        )
        harness.require(
            "no evidence command was executed" in broken_run.stdout,
            "output does not state that no evidence command was executed",
        )
        print(f"probe_executed={probe_marker.exists()}")
        harness.require(not probe_marker.exists(), "probe command ran despite format error")
        print("no_execution_verdict=format-error-left-no-execution-trace")

        print("== phase 2: negative control — fix only the missing field (expected GREEN) ==")
        fixed_block = harness.base_item(1, command=f"touch {probe_marker}")
        harness.write_manifest(fixture_dir, [fixed_block])
        fixed_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"fixed_exit={fixed_run.returncode}")
        harness.require_exit(fixed_run, 0, "fixed manifest run")
        print(f"probe_executed_after_fix={probe_marker.exists()}")
        harness.require(probe_marker.exists(), "fixed manifest did not execute the probe")
        print("fixed_flow_verdict=normal-per-item-execution-resumed")

    print("RV-4 PASS: format errors block execution; a fixed manifest runs normally")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
