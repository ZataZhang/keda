"""rv-1 negative control: lie that there are no running Issues, then observe over-dispatch."""

from __future__ import annotations

import re
import time

from fixture_lib import Fixture, write_text

CEILING_RE = re.compile(
    r"Concurrency ceiling: ceiling=(\d+) running=(\d+) ready_budget=(\d+)"
)


def main() -> int:
    fixture = Fixture("rv1-negative", capacity=10)
    for issue_number in (200, 201):
        fixture.seed_issue(
            issue_number, title=f"running {issue_number}", labels=["agent/running"]
        )
    ready_numbers = [100, 101, 102, 103, 104, 105]
    for issue_number in ready_numbers:
        fixture.seed_issue(issue_number, title=f"ready {issue_number}", labels=["agent/ready"])

    console = fixture.start_console(fixture.root / "console.log")
    try:
        status, settings = fixture.http(
            "PATCH",
            "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": 2},
        )
        assert status == 200 and settings["effective_max_parallel"] == 2, settings
    finally:
        Fixture.stop(console)

    daemon_log = fixture.root / "daemon.log"
    daemon = fixture.spawn(
        [
            "daemon",
            "run",
            "--repo-id",
            "rv-fixture",
            "--concurrency",
            "10",
            "--max-issues",
            "10",
            "--interval",
            "1",
        ],
        daemon_log,
        FAKE_AGENT_SLEEP="25",
        FAKE_GH_EMPTY_RUNNING_COUNT="1",
        # Live-count query probes ceiling + 1 to distinguish an exact ceiling
        # from an over-ceiling lower bound.
        FAKE_GH_EMPTY_RUNNING_LIMIT="3",
    )
    observed_started: list[int] = []
    actual_running: list[int] = []
    try:
        observed_started = fixture.wait_for_agent_starts(2)
        # 先在 daemon 仍运行时取到真实 label 状态。仅看到 agent start 探针后立刻
        # SIGTERM 会触发 worker 清理，把新认领的 Issue 改成 failed，掩盖超发现场。
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            issue_state = fixture.gh_state()
            actual_running = sorted(
                int(number)
                for number, issue in issue_state["issues"].items()
                if "agent/running" in issue["labels"]
            )
            if len(actual_running) >= 4:
                break
            time.sleep(0.1)
    finally:
        Fixture.stop(daemon)

    log_text = daemon_log.read_text(encoding="utf-8")
    ceiling_lines = [
        tuple(int(value) for value in row) for row in CEILING_RE.findall(log_text)
    ]
    started = sorted(
        {event["issue"] for event in fixture.probe_events() if event["event"] == "start"}
    )
    report = "\n".join(
        [
            "rv-1 negative control: fake gh returns an empty running-count result despite two live labels.",
            f"faulty_count_budget={ceiling_lines[:1]}",
            f"observed_start_probe_issues={observed_started}",
            f"new_agent_starts={started}",
            f"actual_running_issues={actual_running} count={len(actual_running)} ceiling=2",
            "NEG CONTROL: RED reproduced — the false zero count allowed claims beyond ceiling 2.",
        ]
    ) + "\n"
    assert (2, 0, 2) in ceiling_lines, ceiling_lines
    assert len(started) == 2, started
    assert len(actual_running) >= 4, actual_running
    assert {100, 101, 200, 201}.issubset(actual_running), actual_running
    write_text("rv-1-negative-control.txt", report)
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
