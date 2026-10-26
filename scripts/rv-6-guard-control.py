#!/usr/bin/env python3
"""验证旧名守卫对新回潮变红，并在移除注入后变绿。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = PROJECT_ROOT / "docs/rv6-residual-name-probe.md"


def _run_and_record(filename: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/guards/test_residual_iar_naming_guard.py",
        "-q",
        "--no-testmon",
    ]
    completed_process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (EVIDENCE_ROOT / filename).write_text(
        f"$ {' '.join(command)}\n"
        f"exit_code={completed_process.returncode}\n"
        "stdout:\n"
        f"{completed_process.stdout.rstrip()}\n"
        "stderr:\n"
        f"{completed_process.stderr.rstrip()}\n",
        encoding="utf-8",
    )
    return completed_process


def main() -> int:
    if PROBE_PATH.exists():
        raise RuntimeError(f"refusing to overwrite existing probe: {PROBE_PATH}")
    try:
        PROBE_PATH.write_text(
            "A new iar:claim constant must be rejected by the guard.\n",
            encoding="utf-8",
        )
        negative_result = _run_and_record("rv-6-negative-control.txt")
        if negative_result.returncode == 0 or "docs/rv6-residual-name-probe.md" not in negative_result.stdout:
            raise RuntimeError("residual-name guard did not identify the injected legacy name")
        with (EVIDENCE_ROOT / "rv-6-negative-control.txt").open(
            "a", encoding="utf-8"
        ) as record_file:
            record_file.write("EXPECTED_FAIL: the guard identifies the injected out-of-allowlist docs line.\n")
    finally:
        PROBE_PATH.unlink(missing_ok=True)

    green_result = _run_and_record("rv-6-guard-green.txt")
    if green_result.returncode != 0:
        raise RuntimeError("residual-name guard remains red after removing the injected line")
    with (EVIDENCE_ROOT / "rv-6-guard-green.txt").open(
        "a", encoding="utf-8"
    ) as record_file:
        record_file.write("PASS: the guard is green after the injected file was removed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
