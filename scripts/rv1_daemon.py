"""rv-1：daemon 自动认领按生效上限放行（真实 kc daemon + 真实 SQLite + fake gh/agent）。

场景（capacity=10，间隔 1s）：
- P2   ：真实 PATCH 设并发=2，6 个就绪、0 在跑 → 单轮 ceiling=2 ready_budget=2，恰 2 个被认领，
         任意时刻 fake agent 并发==2。
- R1   ：并发=2，先制造 1 个在跑 → 日志 running=1 ready_budget=1（在跑数计入预算）。
- R0   ：并发=2，先制造 2 个在跑 → 日志 running=2 ready_budget=0，本轮不新认领、进程不崩。
- UNSET：不设置策略（继承容量）→ ceiling=10 ready_budget=10，6 个就绪全被领走（负控同源）。

值来源：daemon 日志的 ceiling/running/ready_budget 行 + fake agent 探针 JSONL；策略行来自真实
PATCH 写入的 console SQLite。只 fake 外部 gh/agent，认领选择、闸门、SQLite 真实。
"""

from __future__ import annotations

import re
import sys
import time

from fixture_lib import Fixture, write_text

CAPACITY = 10
AGENT_SLEEP = "25"
CEILING_RE = re.compile(
    r"Concurrency ceiling: ceiling=(\d+) running=(\d+) ready_budget=(\d+)"
)


def run_scenario(
    name: str,
    *,
    policy: int | None,
    ready_numbers: list[int],
    running_numbers: list[int],
    expected_starts: int,
) -> dict:
    fx = Fixture(name, capacity=CAPACITY, auto_advance=False)
    for number in ready_numbers:
        fx.seed_issue(number, title=f"ready {number}", labels=["agent/ready"])
    for number in running_numbers:
        fx.seed_issue(number, title=f"running {number}", labels=["agent/running"])

    if policy is not None:
        proc = fx.start_console(fx.root / "console.log")
        try:
            code, settings = fx.http(
                "PATCH",
                "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
                {"max_parallel": policy},
            )
            assert code == 200 and settings.get("effective_max_parallel") is not None, settings
        finally:
            Fixture.stop(proc)

    daemon_log = fx.root / "daemon.log"
    proc = fx.spawn(
        [
            "daemon", "run", "--repo-id", "rv-fixture",
            "--concurrency", str(CAPACITY), "--max-issues", str(CAPACITY), "--interval", "1",
        ],
        daemon_log,
        FAKE_AGENT_SLEEP=AGENT_SLEEP,
    )
    try:
        if expected_starts:
            fx.wait_for_agent_starts(expected_starts)
        else:
            # 零预算场景不能仅靠“没有 agent 探针”判断 daemon 已经跑过一轮；
            # 等待真实 ceiling 日志，避免启动慢时提前 SIGTERM 造成假失败。
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if daemon_log.is_file() and "Concurrency ceiling:" in daemon_log.read_text(
                    encoding="utf-8", errors="replace"
                ):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("daemon 未在 45 秒内执行零预算轮次")
    finally:
        Fixture.stop(proc)

    text = daemon_log.read_text(encoding="utf-8")
    matches = [
        (int(c), int(r), int(b)) for c, r, b in CEILING_RE.findall(text)
    ]
    probes = fx.probe_events()
    started = sorted({p["issue"] for p in probes if p["event"] == "start"})
    # ready→running 转换：认领后 ready 标签被摘除、running 标签加上。
    state = fx.gh_state()
    ready_still_ready = [
        number
        for number in ready_numbers
        if "agent/ready" in state["issues"][str(number)]["labels"]
    ]
    return {
        "ceiling_lines": matches,
        "peak_concurrent": fx.max_concurrent_agents(),
        "started_issues": started,
        "ready_still_ready": ready_still_ready,
        "log_tail": "\n".join(text.splitlines()[-25:]),
    }


def main() -> int:
    lines: list[str] = []

    def rec(label: str, result: dict) -> None:
        lines.append(f"[{label}] ceiling_lines={result['ceiling_lines']}")
        lines.append(
            f"[{label}] peak_concurrent={result['peak_concurrent']} "
            f"started_issues={result['started_issues']}"
        )
        lines.append(f"[{label}] ready_still_ready={result['ready_still_ready']}")

    ready6 = [100, 101, 102, 103, 104, 105]

    p2 = run_scenario(
        "rv1-p2", policy=2, ready_numbers=ready6, running_numbers=[], expected_starts=2
    )
    rec("P2 policy=2,6ready,0running", p2)
    assert (2, 0, 2) in p2["ceiling_lines"], p2["ceiling_lines"]
    assert p2["peak_concurrent"] == 2, p2["peak_concurrent"]
    assert len(p2["started_issues"]) == 2, p2["started_issues"]

    r1 = run_scenario(
        "rv1-r1", policy=2, ready_numbers=ready6, running_numbers=[200], expected_starts=1
    )
    rec("R1 policy=2,6ready,1pre-running", r1)
    assert (2, 1, 1) in r1["ceiling_lines"], r1["ceiling_lines"]

    r0 = run_scenario(
        "rv1-r0", policy=2, ready_numbers=ready6, running_numbers=[200, 201], expected_starts=0
    )
    rec("R0 policy=2,6ready,2pre-running", r0)
    assert (2, 2, 0) in r0["ceiling_lines"], r0["ceiling_lines"]
    # 预算为 0：本轮不新认领任何 ready（全部仍带 agent/ready）。
    assert sorted(r0["ready_still_ready"]) == ready6, r0["ready_still_ready"]
    assert r0["peak_concurrent"] <= 2, r0["peak_concurrent"]

    unset = run_scenario(
        "rv1-unset", policy=None, ready_numbers=ready6, running_numbers=[], expected_starts=6
    )
    rec("UNSET inherit-capacity", unset)
    assert (10, 0, 10) in unset["ceiling_lines"], unset["ceiling_lines"]
    # 负控同源证据：不设策略时继承容量=10，6 个就绪被整批领走（>2 并发），
    # 证明「≤2」这一 oracle 能变红——把 ceiling 压到 2 正是修复所要阻止的批量认领。
    assert len(unset["started_issues"]) == 6, unset["started_issues"]
    assert unset["peak_concurrent"] >= 3, unset["peak_concurrent"]

    lines.append("")
    lines.append(
        "INHERIT CONTROL: no policy inherits capacity 10 and all 6 ready Issues are "
        "claimed; injected bad-count negative control is recorded separately in "
        "rv-1-negative-control.txt."
    )
    write_text("rv-1-daemon-claim-budget.log", "\n".join(lines) + "\n")
    print("\n".join(lines))
    print("EVIDENCE rv-1-daemon-claim-budget.log written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
