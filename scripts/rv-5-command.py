#!/usr/bin/env python3
"""rv-5 command: bounded, non-recursive proof that ``--json`` is machine-readable.

This script is the manifest command for item 5, so it must never run
``kc evidence check`` against the whole real evidence directory (that would
recurse into itself). It drives the real CLI against a temporary two-item
fixture instead: a green run whose JSON parses, then a corrupted assertion
whose JSON reports the failed item with exit 1.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness


def _write_fixture(fixture_dir: Path, expected_pattern: str) -> None:
    harness.write_manifest(
        fixture_dir,
        [
            harness.base_item(
                1,
                command="printf 'MACHINE ONE OK\\n'",
                assertions=[{"pattern": "MACHINE ONE OK"}],
            ),
            harness.base_item(
                2,
                command="printf 'MACHINE TWO OK\\n'",
                assertions=[{"pattern": expected_pattern}],
            ),
        ],
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rv5-command-") as temporary_directory_text:
        fixture_dir = Path(temporary_directory_text) / "evidence"

        print("== green run: JSON parses and all items pass ==")
        _write_fixture(fixture_dir, "MACHINE TWO OK")
        green_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"green_exit={green_run.returncode}")
        harness.print_streams(green_run)
        harness.require_exit(green_run, 0, "green JSON run")
        green_payload = json.loads(green_run.stdout)
        harness.require(green_payload["passed"] is True, "green JSON payload is not passed")
        harness.require(
            green_payload["summary"]
            == {"total": 2, "passed": 2, "failed": 0, "timed_out": 0, "skipped": 0},
            "green JSON summary is unexpected",
        )
        print("json_parse=ok")
        print(f"json_summary={green_payload['summary']}")

        print("== corrupted assertion: JSON reports the failed item and exit 1 ==")
        _write_fixture(fixture_dir, "MACHINE TWO IMPOSSIBLE")
        red_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"red_exit={red_run.returncode}")
        harness.print_streams(red_run)
        harness.require_exit(red_run, 1, "red JSON run")
        red_payload = json.loads(red_run.stdout)
        failed_items = [
            item_entry
            for item_entry in red_payload["items"]
            if item_entry["status"] == "failed"
        ]
        harness.require(len(failed_items) == 1, "corrupted run did not report one failed item")
        harness.require(
            failed_items[0]["item_number"] == 2,
            "corrupted run reported the wrong item number",
        )
        print(f"negative_control_status={failed_items[0]['status']}")
        print(f"negative_control_item={failed_items[0]['item_number']}")

        print("== legacy manifest without stdout_assertions: exit code only ==")
        harness.write_manifest(
            fixture_dir,
            [
                harness.base_item(
                    1,
                    command="printf 'LEGACY OUTPUT WITHOUT DECLARED ASSERTIONS\\n'",
                )
            ],
        )
        legacy_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(fixture_dir), "--json"]
        )
        print(f"legacy_exit={legacy_run.returncode}")
        harness.require_exit(legacy_run, 0, "legacy manifest run")
        legacy_payload = json.loads(legacy_run.stdout)
        print(f"legacy_manifest_status={legacy_payload['items'][0]['status']}")
        print(
            "legacy_manifest_assertion_failures="
            f"{len(legacy_payload['items'][0]['assertion_failures'])}"
        )

    print("RV-5 COMMAND PASS: --json parses on green and reports the failed item on red")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
