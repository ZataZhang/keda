"""rv-2 global-start real-entry probe (isolated kc console + SQLite + fake gh/agent)."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

from fixture_lib import Fixture, write_pending_prd, write_text

CAPACITY = 4
PRD_COUNT = 6
REAL_PRD_SKILL = str(Path.home() / ".kedacode" / "skills" / "prd" / "SKILL.md")
SKILL_ENV = {
    "IAR_PRD_SKILL_PATH": REAL_PRD_SKILL,
    "KEDACODE_PRD_SKILL_PATH": REAL_PRD_SKILL,
}


def run_global_start(*, name: str, policy: int | None, running_count: int = 0) -> dict:
    """Call the real global-start HTTP route and return captured bounded results."""
    fixture = Fixture(name, capacity=CAPACITY)
    for index in range(running_count):
        fixture.seed_issue(
            590 + index,
            title=f"unanchored running issue {index}",
            labels=["agent/running"],
        )
    for index in range(PRD_COUNT):
        issue_number = 600 + index
        fixture.seed_issue(issue_number, title=f"global issue {issue_number}")
        write_pending_prd(
            fixture,
            f"tasks/pending/P1-FEAT-20260101-000{index:02d}-global.md",
            f"global probe {index}",
            issue_number=issue_number,
        )

    console = fixture.start_console(
        fixture.root / "console.log", {**SKILL_ENV, "FAKE_AGENT_SLEEP": "0.1"}
    )
    runner_pids: list[int] = []
    try:
        if policy is not None:
            status, patch_body = fixture.http(
                "PATCH",
                "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
                {"max_parallel": policy},
            )
            assert status == 200, patch_body

        status, autopilot = fixture.http(
            "GET", "/api/v1/agent-runner/backlog/autopilot?repo_id=rv-fixture"
        )
        assert status == 200, autopilot
        status, result = fixture.http(
            "POST",
            "/api/v1/agent-runner/backlog/start-global",
            {"repo_id": "rv-fixture"},
        )
        assert status == 200, result

        started = result.get("started", [])
        queued = result.get("queued", [])
        process_registry_path = fixture.home / ".kedacode" / "processes.json"
        process_records = (
            json.loads(process_registry_path.read_text(encoding="utf-8"))
            if process_registry_path.is_file()
            else {}
        )
        runner_pids = [
            int(record["pid"])
            for record in process_records.values()
            if record.get("repo_id") == "rv-fixture" and record.get("kind") == "run_once"
        ]
        # Wait for real kc run children to cross and finish the fake-agent boundary.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            agent_events = fixture.probe_events()
            start_events = [event for event in agent_events if event["event"] == "start"]
            end_events = [event for event in agent_events if event["event"] == "end"]
            if len(start_events) >= len(started) and len(end_events) >= len(started):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(
                "fixture kc run children did not finish fake-agent execution: "
                f"starts={len(start_events)} ends={len(end_events)} expected={len(started)}"
            )

        actual_agent_starts = [
            event for event in fixture.probe_events() if event["event"] == "start"
        ]
        row = fixture.read_settings_row()
        return {
            "policy": policy,
            "autopilot": autopilot,
            "started_count": len(started),
            "queued_count": len(queued),
            "started_prds": [entry.get("prd_path") for entry in started],
            "queued_prds": queued,
            "spawned_run_processes": len(runner_pids),
            "agent_start_count": len(actual_agent_starts),
            "settings_row": row,
        }
    finally:
        # Terminate only kc run PIDs recorded by this isolated fixture; fake agents
        # have already emitted their end probe above.
        for runner_pid in runner_pids:
            try:
                os.kill(runner_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        fixture.run_kc(
            ["registry", "stop", "--repo-id", "rv-fixture"], timeout=45, **SKILL_ENV
        )
        Fixture.stop(console)


def main() -> int:
    cases = [
        ("inherit-with-unanchored-running", None, 1, CAPACITY - 1),
        ("policy-with-unanchored-running", 2, 1, 1),
        ("policy-full-unanchored-budget", 2, 2, 0),
    ]
    lines = [
        "rv-2 global-start: real POST /backlog/start-global, fresh API state, isolated fixture",
        "Only GitHub CLI and agent executable boundaries are fake; kc console, route, core batch selection, SQLite and kc run children are real.",
        "",
    ]
    for name, policy, running_count, expected_started in cases:
        result = run_global_start(
            name=f"rv2-global-{name}", policy=policy, running_count=running_count
        )
        source = "inherited" if policy is None else "policy"
        ceiling = CAPACITY if policy is None else policy
        expected_queued = PRD_COUNT - expected_started
        lines.append(
            f"[{name}] policy={policy} source={result['autopilot']['ceiling_source']} "
            f"effective={result['autopilot']['effective_max_parallel']} "
            f"unanchored_running={running_count} "
            f"started={result['started_count']} queued={result['queued_count']} "
            f"spawned_run_processes={result['spawned_run_processes']} "
            f"agent_starts={result['agent_start_count']}"
        )
        lines.append(f"  started_prds={json.dumps(result['started_prds'], ensure_ascii=False)}")
        lines.append(f"  queued_prds={json.dumps(result['queued_prds'], ensure_ascii=False)}")
        lines.append(f"  fresh_settings_row={result['settings_row']}")
        assert result["autopilot"]["ceiling_source"] == source, result
        assert result["autopilot"]["effective_max_parallel"] == ceiling, result
        assert result["started_count"] == expected_started, result
        assert result["queued_count"] == expected_queued, result
        assert result["spawned_run_processes"] == expected_started, result
        assert (
            result["settings_row"]["max_parallel"] if result["settings_row"] else None
        ) == policy, result

    report = "\n".join(lines) + "\nRESULT: PASS\n"
    write_text("rv-2-global-start.txt", report)
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
