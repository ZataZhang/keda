"""探测：真实 daemon 跑一轮，看日志是否输出 Concurrency ceiling 行、格式如何。"""

from __future__ import annotations

import os
import sys
import time

from fixture_lib import Fixture


def main() -> int:
    fx = Fixture("probe", capacity=4, auto_advance=False)
    # 1 个 ready issue，policy 未设置。
    fx.seed_issue(100, title="probe ready", labels=["agent/ready"])
    log_path = fx.root / "daemon.log"
    proc = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", "4", "--max-issues", "4", "--interval", "1"],
        log_path,
        FAKE_AGENT_SLEEP="8",
    )
    time.sleep(6)
    fx.stop(proc)
    text = log_path.read_text(encoding="utf-8")
    print("=== LOG (first 120 lines) ===")
    print("\n".join(text.splitlines()[:120]))
    print("=== probe events ===", fx.probe_events())
    return 0


if __name__ == "__main__":
    sys.exit(main())
