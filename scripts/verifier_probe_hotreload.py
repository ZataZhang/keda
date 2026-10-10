"""verifier 探针：daemon 运行中改策略是否「下一轮生效」（热更新）。

rv-1 的每个场景都是「先 PATCH 再重启 daemon」，所以没有验证过：daemon 进程存活期间
改 console 策略，后续轮次是否会读到新的生效值（用户实际就是这么用的：daemon 常驻、
在页面上调并发）。本探针保持同一个 daemon 进程连续跑，中途通过真实 HTTP PATCH 改策略：

  无策略(继承容量 4) -> PATCH max_parallel=2 -> PATCH max_parallel=null(恢复继承)

断言观察到的 ceiling 序列按时间出现 4 → 2 → 4 的段位切换，证明每轮都重新解析生效值，
且「恢复继承 = 删行」在运行中的 daemon 上立即生效。
"""

from __future__ import annotations

import re
import sys
import time

from fixture_lib import Fixture, write_text

CEILING_RE = re.compile(r"Concurrency ceiling: ceiling=(\d+) running=\d+ ready_budget=\d+")


def main() -> int:
    fx = Fixture("rvhot", capacity=4, auto_advance=False)
    for number in range(100, 125):
        fx.seed_issue(number, title=f"ready {number}", labels=["agent/ready"])

    console = fx.start_console(fx.root / "console.log")
    daemon_log = fx.root / "daemon.log"
    proc = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", "4",
         "--max-issues", "1", "--interval", "1"],
        daemon_log,
        FAKE_AGENT_SLEEP="1",
    )
    patches: list[str] = []
    try:
        time.sleep(12)
        code, body = fx.http(
            "PATCH", "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": 2},
        )
        patches.append(f"PATCH 2 -> http {code} effective={body.get('effective_max_parallel')} source={body.get('ceiling_source')}")
        time.sleep(20)
        row_after_2 = fx.read_settings_row()
        code, body = fx.http(
            "PATCH", "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": None},
        )
        patches.append(f"PATCH null -> http {code} effective={body.get('effective_max_parallel')} source={body.get('ceiling_source')}")
        time.sleep(25)
        row_after_null = fx.read_settings_row()
    finally:
        Fixture.stop(proc)
        Fixture.stop(console)

    text = daemon_log.read_text(encoding="utf-8")
    seq = [int(m) for m in CEILING_RE.findall(text)]
    # 折叠连续相同值，得到「段位」序列
    segments: list[int] = []
    for value in seq:
        if not segments or segments[-1] != value:
            segments.append(value)

    lines = [
        "verifier 探针：daemon 存活期间改策略的热更新生效",
        "",
        *patches,
        f"策略行 PATCH 2 之后：{row_after_2}",
        f"策略行 PATCH null 之后：{row_after_null}",
        "",
        f"daemon 单进程内 ceiling 逐轮序列：{seq}",
        f"折叠后的段位序列：{segments}",
        "",
        "断言 段位序列含 4 -> 2 -> 4 的顺序切换（证明每轮重新解析 + 恢复继承即时生效）：",
        "  " + ("PASS" if _has_order(segments, [4, 2, 4]) else "FAIL"),
        "断言 恢复继承后策略行被删除（None）：",
        "  " + ("PASS" if row_after_null is None else f"FAIL -> {row_after_null}"),
    ]
    write_text("verifier-hot-reload.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if (_has_order(segments, [4, 2, 4]) and row_after_null is None) else 1


def _has_order(segments: list[int], wanted: list[int]) -> bool:
    idx = 0
    for value in segments:
        if value == wanted[idx]:
            idx += 1
            if idx == len(wanted):
                return True
    return False


if __name__ == "__main__":
    sys.exit(main())
