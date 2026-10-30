#!/usr/bin/env python3
"""rv-2 evidence: every assertion failure is reported in one self-check run.

Red: three items each declare a wrong assertion; one command must report all
three failures. Green control: fix the assertions in the same fixture and the
same command reports zero failures with exit 0.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness


def _item_blocks(patterns_by_item: dict[int, str]) -> list[dict]:
    """Build one probe item per entry, asserting the given expected pattern."""
    return [
        harness.base_item(
            item_number,
            command=f"printf 'ACTUAL-{item_number} value\\n'",
            assertions=[{"pattern": expected_pattern}],
        )
        for item_number, expected_pattern in patterns_by_item.items()
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rv2-") as temporary_directory_text:
        fixture_dir = Path(temporary_directory_text) / "evidence"
        wrong_patterns = {
            1: "EXPECTED-ONE",
            2: "EXPECTED-TWO",
            3: "EXPECTED-THREE",
        }
        harness.write_manifest(fixture_dir, _item_blocks(wrong_patterns))

        print("== phase 1: three wrong assertions in one run (expected RED) ==")
        red_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"red_exit={red_run.returncode}")
        harness.require_exit(red_run, 1, "red run")
        red_payload = json.loads(red_run.stdout)
        failed_items = [
            item_entry
            for item_entry in red_payload["items"]
            if item_entry["status"] == "failed"
        ]
        print(f"failed_item_count={len(failed_items)}")
        harness.require(len(failed_items) == 3, "expected exactly three failed items")
        for item_entry in failed_items:
            assertion_failure = item_entry["assertion_failures"][0]
            print(
                f"failed_item_{item_entry['item_number']}"
                f"=pattern:{assertion_failure['pattern']}"
                f"|closest:{assertion_failure['closest_output_line']!r}"
            )
        harness.require(
            [item_entry["item_number"] for item_entry in failed_items] == [1, 2, 3],
            "failed item numbers are not [1, 2, 3]",
        )
        print("collect_all_verdict=three-failures-in-one-run")

        print("== phase 2: corrected assertions, same command (expected GREEN) ==")
        correct_patterns = {
            1: "ACTUAL-1 value",
            2: "ACTUAL-2 value",
            3: "ACTUAL-3 value",
        }
        harness.write_manifest(fixture_dir, _item_blocks(correct_patterns))
        green_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"green_exit={green_run.returncode}")
        harness.require_exit(green_run, 0, "green run")
        green_payload = json.loads(green_run.stdout)
        green_failed_items = [
            item_entry
            for item_entry in green_payload["items"]
            if item_entry["status"] != "passed"
        ]
        print(f"green_failed_item_count={len(green_failed_items)}")
        harness.require(
            len(green_failed_items) == 0, "corrected fixture still reports failures"
        )
        print(f"green_summary={green_payload['summary']}")

    print("RV-2 PASS: three assertion failures reported together, zero after correction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
