#!/usr/bin/env python3
"""rv-5 evidence: the real evidence package passes ``kc evidence check --json``.

Runs the real CLI against this PRD's real evidence directory (the manifest
command for item 5 is the bounded ``rv-5-command.py``, so there is no
recursion). Negative control: copy the real directory, corrupt one assertion
in the copy, and confirm exit 1 with that item reported as failed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_harness as harness


def main() -> int:
    print("== phase 1: real evidence package through kc evidence check --json ==")
    real_run = harness.run_kc(
        ["evidence", "check", "--evidence", str(harness.EVIDENCE_DIR), "--json"],
        timeout=280,
    )
    print(f"real_exit={real_run.returncode}")
    harness.require_exit(real_run, 0, "real evidence run")
    real_payload = json.loads(real_run.stdout)
    print("json_parse=ok")
    print(f"real_passed={real_payload['passed']}")
    print(f"real_evidence_dir={real_payload['evidence_dir']}")
    print(f"real_summary={real_payload['summary']}")
    harness.require(real_payload["passed"] is True, "real evidence package did not pass")
    harness.require(real_payload["summary"]["total"] == 5, "real evidence package is not 5 items")
    statuses = [item_entry["status"] for item_entry in real_payload["items"]]
    print(f"real_item_statuses={statuses}")
    harness.require(statuses == ["passed"] * 5, "not every real item passed")

    print("== phase 2: negative control — corrupt one assertion in a copy ==")
    with tempfile.TemporaryDirectory(prefix="rv5-copy-") as temporary_directory_text:
        copied_evidence_dir = Path(temporary_directory_text) / "evidence"
        shutil.copytree(harness.EVIDENCE_DIR, copied_evidence_dir)
        copied_manifest_path = copied_evidence_dir / "evidence.json"
        copied_manifest = json.loads(copied_manifest_path.read_text(encoding="utf-8"))
        copied_manifest["items"][4]["stdout_assertions"].append(
            {"pattern": "RV-5 IMPOSSIBLE PATTERN", "severity": "high"}
        )
        copied_manifest_path.write_text(
            json.dumps(copied_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        red_run = harness.run_kc(
            ["evidence", "check", "--evidence", str(copied_evidence_dir), "--json"],
            timeout=280,
        )
        print(f"corrupted_exit={red_run.returncode}")
        harness.require_exit(red_run, 1, "corrupted copy run")
        red_payload = json.loads(red_run.stdout)
        failed_items = [
            item_entry
            for item_entry in red_payload["items"]
            if item_entry["status"] == "failed"
        ]
        print(f"corrupted_failed_item_numbers={[e['item_number'] for e in failed_items]}")
        harness.require(
            [entry["item_number"] for entry in failed_items] == [5],
            "corrupted copy reported unexpected failed items",
        )
        print(
            "corrupted_assertion_pattern="
            f"{failed_items[0]['assertion_failures'][0]['pattern']}"
        )

    print("== phase 3: default resolution with one candidate (no --evidence) ==")
    with tempfile.TemporaryDirectory(prefix="rv5-default-") as temporary_directory_text:
        default_worktree = Path(temporary_directory_text)
        subprocess.run(
            ["git", "init", "-q", str(default_worktree)],
            check=True,
            capture_output=True,
        )
        (default_worktree / ".kedacode.toml").write_text(
            '[agent_runner.validation]\nevidence_dir = "tasks/evidence"\n',
            encoding="utf-8",
        )
        default_task_dir = default_worktree / "tasks" / "evidence" / "only-task"
        harness.write_manifest(
            default_task_dir,
            [
                harness.base_item(
                    1,
                    command="printf 'ONLY CANDIDATE OK\\n'",
                    assertions=[{"pattern": "ONLY CANDIDATE OK"}],
                )
            ],
        )
        default_run = harness.run_kc(
            ["evidence", "check", "--worktree", str(default_worktree), "--json"]
        )
        print(f"default_exit={default_run.returncode}")
        harness.require_exit(default_run, 0, "default resolution run")
        default_payload = json.loads(default_run.stdout)
        print(f"default_evidence_dir={default_payload['evidence_dir']}")
        harness.require(
            default_payload["evidence_dir"] == "tasks/evidence/only-task",
            "default resolution did not pick the unique candidate",
        )
        print("default_resolution_verdict=unique-candidate-resolved-without-flag")

    print("RV-5 PASS: real package passes with parseable JSON; corruption turns it red")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
