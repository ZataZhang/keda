#!/usr/bin/env python3
"""为签名头门禁分别保留故障注入与修复后的真实路由测试输出。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path(__file__).resolve().parents[1]
ROUTE_SOURCE = PROJECT_ROOT / "src/backend/api/routes/agent_runner_idea_inbox.py"
OLD_ALIAS = "alias=INBOUND_SIGNATURE_HEADER),"
FAULT_ALIAS = 'alias="X-KC-Signature-RV-NEGATIVE"),'


def _run_and_record(filename: str, command: list[str]) -> subprocess.CompletedProcess[str]:
    completed_process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    record_path = EVIDENCE_ROOT / filename
    record_path.write_text(
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
    source_before = ROUTE_SOURCE.read_text(encoding="utf-8")
    if source_before.count(OLD_ALIAS) != 1:
        raise RuntimeError("expected exactly one KC header alias before fault injection")
    negative_command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_idea_inbox.py::test_api_inbound_accepts_valid_signature",
        "-q",
        "--no-testmon",
    ]
    try:
        ROUTE_SOURCE.write_text(
            source_before.replace(OLD_ALIAS, FAULT_ALIAS, 1), encoding="utf-8"
        )
        negative_result = _run_and_record("rv-5-negative-control.txt", negative_command)
        if negative_result.returncode == 0:
            raise RuntimeError("KC signature acceptance test unexpectedly passed after alias fault")
        with (EVIDENCE_ROOT / "rv-5-negative-control.txt").open(
            "a", encoding="utf-8"
        ) as record_file:
            record_file.write("EXPECTED_FAIL: X-KC-Signature acceptance fails after the route alias is fault-injected.\n")
    finally:
        ROUTE_SOURCE.write_text(source_before, encoding="utf-8")
    if ROUTE_SOURCE.read_text(encoding="utf-8") != source_before:
        raise RuntimeError("route source was not restored after the fault injection")

    green_command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_idea_inbox.py",
        "-k",
        "inbound and (accepts_valid_signature or rejects_missing_signature)",
        "-q",
        "--no-testmon",
    ]
    green_result = _run_and_record("rv-5-signature-headers.txt", green_command)
    if green_result.returncode != 0:
        raise RuntimeError("signature header acceptance/rejection tests failed")
    with (EVIDENCE_ROOT / "rv-5-signature-headers.txt").open(
        "a", encoding="utf-8"
    ) as record_file:
        record_file.write("PASS: both signature aliases validate and a missing signature is rejected.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
