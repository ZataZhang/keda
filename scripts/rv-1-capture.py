#!/usr/bin/env python3
"""rv-1 evidence: self-check and delivery gate reach the same assertion verdict.

Red first: the fixture assertion is off by one word, mirroring Issue #334.
Then the same fixture is evaluated by the delivery gate in a fresh process.
Finally a negative control disables only the assertion judgment to prove the
assertion — not the exit code — is the discriminating check.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness

EXPECTED_PATTERN = "LEGACY GREEN: v1 example-derived suite"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rv1-worktree-") as temporary_directory_text:
        worktree_path = Path(temporary_directory_text)
        fixture_dir = worktree_path / "tasks" / "evidence" / "P1-rv1-fixture"
        harness.write_manifest(
            fixture_dir,
            [
                harness.base_item(
                    1,
                    command="printf 'LEGACY GREEN: v1 suite\\n run 3 tests\\n'",
                    assertions=[
                        {
                            "severity": "high",
                            "pattern": EXPECTED_PATTERN,
                            "source": "stdout",
                            "must_match": True,
                        }
                    ],
                )
            ],
        )
        (fixture_dir / "rv-1-probe.txt").write_text("fixture probe evidence\n", encoding="utf-8")

        print("== phase 1: same fixture through kc evidence check (expected RED) ==")
        self_check = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"self_check_exit={self_check.returncode}")
        harness.require_exit(self_check, 1, "self-check")
        self_check_payload = json.loads(self_check.stdout)
        failed_items = [
            item_entry
            for item_entry in self_check_payload["items"]
            if item_entry["status"] != "passed"
        ]
        harness.require(
            len(failed_items) == 1,
            f"expected exactly one failed item, got {len(failed_items)}",
        )
        self_check_item = failed_items[0]
        self_check_assertion = self_check_item["assertion_failures"][0]
        print(f"self_check_item={self_check_item['item_number']}")
        print(f"self_check_status={self_check_item['status']}")
        print(f"self_check_pattern={self_check_assertion['pattern']}")
        print(f"self_check_closest_line={self_check_assertion['closest_output_line']!r}")

        print("== phase 2: same fixture through the delivery gate (fresh process) ==")
        gate_probe = harness.run_python(
            harness.SCRIPTS_DIR / "rv1_gate_probe.py", str(worktree_path)
        )
        harness.print_streams(gate_probe)
        harness.require_exit(gate_probe, 0, "gate probe")
        gate_payload = json.loads(gate_probe.stdout)
        harness.require(
            gate_payload["gate_failed"] is True,
            "the delivery gate did not fail the mismatching fixture",
        )
        print(f"gate_item={gate_payload['gate_item']}")
        print(f"gate_pattern={gate_payload['gate_pattern']}")
        harness.require(
            gate_payload["gate_item"] == self_check_item["item_number"],
            "self-check and gate report different item numbers",
        )
        harness.require(
            gate_payload["gate_pattern"] == self_check_assertion["pattern"],
            "self-check and gate report different assertion patterns",
        )
        print("consistent_verdict=item-number-and-pattern-identical")

        print("== phase 3: negative control — assertion judgment disabled ==")
        negative_control = harness.run_python(
            harness.SCRIPTS_DIR / "rv1_negative_control.py", str(worktree_path)
        )
        harness.print_streams(negative_control)
        harness.require_exit(negative_control, 0, "negative control")
        negative_payload = json.loads(negative_control.stdout)
        harness.require(
            negative_payload["negative_control_status"] == "passed",
            "negative control did not flip the fixture to passed",
        )
        print("negative_control_verdict=exit-0-command-passes-without-assertion-judgment")

    print("RV-1 PASS: self-check and gate agree on the same pattern; assertions are discriminating")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
