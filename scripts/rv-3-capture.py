#!/usr/bin/env python3
"""rv-3 evidence: one timed-out item does not stop the remaining items.

Red: with ``--timeout 1`` the slow item is marked TIMEOUT, the fast item is
still executed and reported, and the output names the delivery gate limit.
Negative control: the same fixture without ``--timeout`` passes (the sleep
finishes under the gate's 300s limit), proving the timeout is local.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rv3-") as temporary_directory_text:
        fixture_dir = Path(temporary_directory_text) / "evidence"
        harness.write_manifest(
            fixture_dir,
            [
                harness.base_item(1, command="sleep 5"),
                harness.base_item(
                    2,
                    command="printf 'FAST OK\\n'",
                    assertions=[{"pattern": "FAST OK"}],
                ),
            ],
        )

        print("== phase 1: local --timeout 1 (expected RED with TIMEOUT) ==")
        local_timeout_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--timeout", "1"]
        )
        harness.print_streams(local_timeout_run)
        print(f"local_timeout_exit={local_timeout_run.returncode}")
        harness.require_exit(local_timeout_run, 1, "local timeout run")
        local_timeout_output = local_timeout_run.stdout
        for expected_substring in (
            "TIMEOUT",
            "delivery gate limit is 300s",
            "PASSED",
            "Result: 1/2 passed, 1 timed out",
        ):
            harness.require(
                expected_substring in local_timeout_output,
                f"missing expected output {expected_substring!r}",
            )
        print("timeout_annotation_verdict=marked-TIMEOUT-with-gate-limit-and-rest-still-evaluated")

        print("== phase 2: above the gate limit --timeout 301 (expected usage error) ==")
        above_limit_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--timeout", "301"]
        )
        harness.print_streams(above_limit_run)
        print(f"above_limit_exit={above_limit_run.returncode}")
        harness.require_exit(above_limit_run, 2, "above-limit run")
        harness.require(
            "between 1 and 300" in above_limit_run.stderr,
            "above-limit run did not explain the 1..300 range",
        )
        print("gate_limit_guard_verdict=timeout-above-gate-limit-rejected-with-usage-error")

        print("== phase 3: negative control — default gate timeout (expected GREEN) ==")
        default_timeout_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"default_timeout_exit={default_timeout_run.returncode}")
        harness.require_exit(default_timeout_run, 0, "default timeout run")
        default_timeout_payload = json.loads(default_timeout_run.stdout)
        print(f"default_timeout_summary={default_timeout_payload['summary']}")
        print(
            "default_timeout_local_flag="
            f"{default_timeout_payload['local_timeout']}"
        )
        harness.require(
            default_timeout_payload["summary"]["timed_out"] == 0,
            "default timeout run still reports a timeout",
        )
        print("negative_control_verdict=no-timeout-at-gate-limit-so-local-flag-caused-it")

    print("RV-3 PASS: local timeout marked and isolated; other items still evaluated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
