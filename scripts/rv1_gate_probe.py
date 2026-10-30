"""Fresh-process probe: run the delivery gate on the same rv-1 fixture.

The gate is driven through its real public entry point
(``ensure_validation_commands_pass``) with a real ``SubprocessRunner``; no
in-process state from the self-check run is reused. Prints one JSON object.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from backend.core.shared.models.agent_runner import (
    AppConfig,
    IssueSummary,
    ValidationConfig,
)
from backend.core.use_cases.agent_runner_validation import ensure_validation_commands_pass
from backend.infrastructure.process_runner import SubprocessRunner

_FIXTURE_DIR_RELATIVE = Path("tasks/evidence/P1-rv1-fixture")


def main() -> int:
    worktree_path = Path(sys.argv[1]).resolve()
    fixture_manifest = json.loads(
        (worktree_path / _FIXTURE_DIR_RELATIVE / "evidence.json").read_text(encoding="utf-8")
    )
    expected_pattern = fixture_manifest["items"][0]["stdout_assertions"][0]["pattern"]
    issue = IssueSummary(
        number=1,
        title="rv-1 fixture",
        url="https://example.invalid/issues/1",
        body="\n".join(
            [
                "PRD path: `tasks/pending/P1-rv1-fixture.md`",
                '<!-- kc:structured-evidence version=1 language="zh-CN" -->',
                "",
                "## Realistic Validation",
                "",
                "- [ ] **rv-1 fixture**: legacy green output.",
            ]
        ),
        labels=(),
    )
    config = AppConfig(
        validation=ValidationConfig(
            evidence_dir="tasks/evidence",
            reexecute_cache_enabled=False,
        )
    )
    try:
        ensure_validation_commands_pass(issue, worktree_path, config, SubprocessRunner())
    except Exception as gate_error:  # noqa: BLE001 - the failure payload is the evidence
        gate_message = str(gate_error)
        item_match = re.search(r"=== Item (?P<item>\d+)", gate_message)
        print(
            json.dumps(
                {
                    "gate_failed": True,
                    "gate_item": int(item_match.group("item")) if item_match else None,
                    "gate_pattern": expected_pattern if expected_pattern in gate_message else None,
                    "gate_message_excerpt": gate_message.splitlines()[:6],
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(json.dumps({"gate_failed": False, "gate_message_excerpt": []}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
