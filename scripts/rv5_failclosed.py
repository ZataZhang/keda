"""rv-5：计数失败时 fail-closed，同时继续发现既有 running 恢复项。

fake gh 对 ``agent/running + --state open + --limit in {ceiling, ceiling + 1}`` 失败；
计数与同标签恢复查询均失败，以触发生产代码的有界 open-Issue 回扫。确认 ready 未认领、预置 running 项进入
恢复判定、daemon 后续轮询且没有 pass 异常。
"""

from __future__ import annotations

import re
import sys
import time

from fixture_lib import Fixture, write_text

PASS_RE = re.compile(r"Daemon pass for repository")
MIN_DAEMON_PASSES = 2
PASS_WAIT_TIMEOUT_SECONDS = 45
PASS_POLL_INTERVAL_SECONDS = 0.1


def main() -> int:
    fx = Fixture("rv5", capacity=2, auto_advance=False)
    fx.seed_issue(200, title="running recovery candidate", labels=["agent/running"])
    for number in (100, 101, 102, 103):
        fx.seed_issue(number, title=f"ready {number}", labels=["agent/ready"])

    daemon_log = fx.root / "daemon.log"
    proc = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", "2",
         "--max-issues", "2", "--interval", "1"],
        daemon_log,
        FAKE_AGENT_SLEEP="25",
        FAKE_GH_FAIL_LABEL_COUNT="1",
        # recovery 查询 limit=2；统一计数查询 limit=ceiling+1=3。
        FAKE_GH_FAIL_COUNT_LIMIT="2,3",  # ceiling = 并发 2（未设策略→继承容量 2）
    )
    try:
        # Wait for the stated minimum evidence instead of assuming a fixed duration.
        # The daemon pass duration varies with fixture subprocess scheduling.
        deadline = time.monotonic() + PASS_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            log_text = daemon_log.read_text(encoding="utf-8", errors="replace")
            observed_passes = len(PASS_RE.findall(log_text))
            failed_pass_observed = "Daemon pass failed" in log_text
            if observed_passes >= MIN_DAEMON_PASSES or failed_pass_observed:
                break
            if proc.poll() is not None:
                break
            time.sleep(PASS_POLL_INTERVAL_SECONDS)
        else:
            raise TimeoutError(
                "daemon did not complete the required passes within "
                f"{PASS_WAIT_TIMEOUT_SECONDS} seconds"
            )
    finally:
        Fixture.stop(proc)

    text = daemon_log.read_text(encoding="utf-8")
    lines: list[str] = []
    fail_closed_warnings = [
        line for line in text.splitlines() if "fail-closed" in line.lower()
    ]
    ceiling_lines = re.findall(
        r"Concurrency ceiling: ceiling=\d+ running=\d+ ready_budget=\d+", text
    )
    passes = len(PASS_RE.findall(text))
    failed_passes = len(re.findall(r"Daemon pass failed", text))
    started = sorted({p["issue"] for p in fx.probe_events() if p["event"] == "start"})
    running_recovery_discovered = "Skipping Issue #200" in text
    recovery_lines = [line for line in text.splitlines() if "Issue #200" in line]
    state = fx.gh_state()
    ready_still = [n for n in (100, 101, 102, 103) if "agent/ready" in state["issues"][str(n)]["labels"]]

    lines.append(f"fail_closed_warnings={fail_closed_warnings[:2]}")
    lines.append(f"ceiling_lines={ceiling_lines}")
    lines.append(
        f"daemon_passes_observed={passes} failed_passes={failed_passes} "
        f"running_recovery_discovered={running_recovery_discovered} agent_started={started}"
    )
    lines.extend(f"  | {line}" for line in recovery_lines[:4])
    lines.append(f"ready_still_ready={ready_still}")
    lines.append(
        "\n".join(
            f"  | {line}"
            for line in text.splitlines()
            if "count live" in line or "fail-closed" in line.lower()
        )[:1200]
    )

    assert fail_closed_warnings, "expected a fail-closed warning"
    assert started == [], started  # 计数失败 → 本轮不认领任何新 Issue
    assert ready_still == [100, 101, 102, 103], ready_still
    assert running_recovery_discovered, "existing running recovery candidate was not discovered"
    assert passes >= MIN_DAEMON_PASSES, (
        f"expected at least {MIN_DAEMON_PASSES} daemon passes, observed {passes}"
    )
    assert failed_passes == 0, "daemon pass failed after count failure"

    write_text("rv-5-fail-closed.log", "\n".join(lines) + "\n")
    print("\n".join(lines))
    print("EVIDENCE rv-5-fail-closed.log written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
