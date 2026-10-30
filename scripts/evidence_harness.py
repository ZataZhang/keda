"""Shared helpers for the RV capture scripts of this PRD.

This module is an RV script: it lives under the evidence directory only and must
never enter the code diff. It intentionally depends on nothing outside the
standard library so the capture scripts can run with the project's interpreter
while driving the real ``kc`` console entry point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = SCRIPTS_DIR.parent
#: tasks/evidence/<prd-stem>/scripts -> repository root
REPO_ROOT = EVIDENCE_DIR.parent.parent.parent

#: 本 PRD 的真实证据 manifest（rv-5 会自检它）。
REAL_MANIFEST_PATH = EVIDENCE_DIR / "evidence.json"


def kc_executable() -> str:
    """Return the worktree's console script, falling back to PATH."""
    venv_kc = REPO_ROOT / ".venv" / "bin" / "kc"
    return str(venv_kc) if venv_kc.is_file() else "kc"


def run_kc(arguments: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run the real ``kc`` entry point (never a TestClient / in-process fake)."""
    environment = dict(os.environ)
    environment.setdefault("KEDACODE_NO_UPDATE_CHECK", "1")
    return subprocess.run(
        [kc_executable(), *arguments],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


def run_python(script_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run another RV script in a fresh process with the project interpreter."""
    environment = dict(os.environ)
    environment.setdefault("KEDACODE_NO_UPDATE_CHECK", "1")
    return subprocess.run(
        [sys.executable, str(script_path), *arguments],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )


def write_manifest(evidence_dir: Path, item_blocks: list[dict]) -> None:
    """Write a fixture ``evidence.json`` (commands are self-terminating probes)."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "language": "zh-CN", "items": item_blocks}
    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def base_item(
    item_number: int,
    *,
    command: str,
    assertions: list[dict] | None = None,
    evidence_file: str | None = None,
) -> dict:
    """Build a manifest item with every required field populated."""
    item_block = {
        "item_number": item_number,
        "item_name": f"rv-{item_number} probe",
        "command": command,
        "evidence_files": [evidence_file or f"rv-{item_number}-probe.txt"],
        "output_summary": f"rv-{item_number} probe output captured.",
        "explanation": "The probe runs the real CLI entry point and its fixture.",
        "risks": "Fixture-only evidence directory; no external service involved.",
        "negative_control": "Break the fixture expectation and re-run the probe.",
        "expected_fail": "The probe reports the mismatching checkpoint.",
    }
    if assertions is not None:
        item_block["stdout_assertions"] = assertions
    return item_block


def fail(message: str) -> None:
    """Print a deterministic RV failure and exit non-zero."""
    print(f"RV FAIL: {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    """Fail the RV script when ``condition`` is false."""
    if not condition:
        fail(message)


def require_exit(
    completed: subprocess.CompletedProcess[str],
    expected_exit_code: int,
    label: str,
) -> None:
    """Assert a subprocess exit code and surface its output on mismatch."""
    if completed.returncode != expected_exit_code:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        fail(f"{label}: expected exit {expected_exit_code}, got {completed.returncode}")


def print_streams(completed: subprocess.CompletedProcess[str]) -> None:
    """Print captured stdout/stderr verbatim for the evidence file."""
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print("[stderr]")
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
