"""verifier 探针：多轮时间维度上界 + 补位续跑（daemon 真实入口）。

rv-1 只看单轮峰值，证明不了「上限生效的同时还能持续补位」这一条组合性质：
  - 若上限过紧（比如把 free_slots 算成 0），peak 会很小但 total started 也不会超过 ceiling；
  - 若上限根本没生效，total started 会超过 ceiling 且 peak 也超。
本探针用短 sleep 的 fake agent + 短 interval，让 daemon 跑约 25 秒多轮，
断言 峰值并发 <= ceiling 且 累计启动数 > ceiling（证明确实发生了补位续跑）。

外加 resolve_execution_ceiling 的边界值（policy==capacity、capacity==1、非法输入）。
"""

from __future__ import annotations

import re
import sys
import time

from fixture_lib import Fixture, write_text

from backend.core.use_cases.backlog_concurrency import (
    describe_ceiling_source,
    resolve_execution_ceiling,
)


def probe_temporal() -> dict:
    fx = Fixture("rvtemporal", capacity=3, auto_advance=False)
    for number in range(100, 108):
        fx.seed_issue(number, title=f"ready {number}", labels=["agent/ready"])
    log = fx.root / "daemon.log"
    proc = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", "3",
         "--max-issues", "3", "--interval", "1"],
        log,
        FAKE_AGENT_SLEEP="3",
    )
    try:
        time.sleep(25)
    finally:
        Fixture.stop(proc)
    events = fx.probe_events()
    starts = sorted({p["issue"] for p in events if p["event"] == "start"})
    return {
        "ceiling": 3,
        "peak": fx.max_concurrent_agents(),
        "total_started": len(starts),
        "started": starts,
        "passes": len(re.findall(r"Daemon pass for repository", log.read_text(encoding="utf-8"))),
        "ceiling_lines": sorted(set(re.findall(
            r"Concurrency ceiling: ceiling=\d+ running=\d+ ready_budget=\d+",
            log.read_text(encoding="utf-8"),
        ))),
    }


def probe_resolver() -> list[str]:
    cases = [
        (None, 10, 10, "inherited"),
        (2, 10, 2, "policy"),
        (10, 10, 10, "policy"),      # policy == capacity
        (20, 10, 10, "capped_by_capacity"),
        (1, 1, 1, "policy"),          # capacity == 1
        (None, 1, 1, "inherited"),
    ]
    out = []
    for policy, capacity, expected, expected_source in cases:
        got = resolve_execution_ceiling(policy, capacity)
        source = describe_ceiling_source(policy, got)
        flag = "OK " if (got == expected and source == expected_source) else "MISMATCH"
        out.append(
            f"  {flag} policy={policy} capacity={capacity} -> {got} "
            f"(expected {expected}) source={source}"
        )
    for bad in [(0, 10), (-1, 10), (5, 0), (5, -3)]:
        try:
            resolve_execution_ceiling(bad[0], bad[1])
            out.append(f"  MISMATCH policy={bad[0]} capacity={bad[1]} -> 未抛异常")
        except ValueError as exc:
            out.append(f"  OK  rejected policy={bad[0]} capacity={bad[1]}: {exc}")
    return out


def main() -> int:
    t = probe_temporal()
    lines = [
        "verifier 探针：时间维度上界 + 补位续跑 + 解析器边界",
        "",
        f"daemon 多轮（capacity=3，8 个 ready Issue，agent sleep=3s，~25s）：",
        f"  passes={t['passes']} peak_concurrent={t['peak']} "
        f"total_started={t['total_started']} started={t['started']}",
        f"  ceiling_lines={t['ceiling_lines']}",
        "",
        "  断言：peak <= ceiling  -> " + ("PASS" if t["peak"] <= t["ceiling"] else "FAIL"),
        "  断言：total_started > ceiling（证明确实补位续跑） -> "
        + ("PASS" if t["total_started"] > t["ceiling"] else "FAIL"),
        "",
        "resolve_execution_ceiling / describe_ceiling_source 边界：",
        *probe_resolver(),
    ]
    write_text("verifier-temporal-and-resolver.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    ok = t["peak"] <= t["ceiling"] and t["total_started"] > t["ceiling"]
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
