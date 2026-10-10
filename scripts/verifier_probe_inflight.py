"""verifier 探针：计数读取失败 fail-closed 时，已在跑 Issue 是否照常推进。

rv-5 的 fixture 里没有任何 agent/running Issue，所以「不中断已在跑任务」这一条
PRD oracle 实际上没被验证过。本探针把一个 Issue 预置成 running（带 rework marker，
正常轮次应当被识别并推进），再注入计数失败，对比 on / off 两种情况：

  - OFF：running Issue 被处理（日志出现 rework / publish 相关推进）
  - ON ：若整轮 pass 抛异常（Daemon pass failed），则 in-flight 推进被中断

只读断言 + 报告，不改任何源码。
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

from fixture_lib import Fixture, write_text

PASS_RE = re.compile(r"Daemon pass for repository")


def run_case(fail_injection: bool) -> dict:
    name = "rv5probe-on" if fail_injection else "rv5probe-off"
    fx = Fixture(name, capacity=2, auto_advance=False)
    # 一个已在跑 Issue（带 rework marker），两个 ready Issue 作为补位候选。
    fx.seed_issue(
        200,
        title="running 200",
        labels=["agent/running"],
        body="## Rework Requested\n\nverifier probe: please rework this PRD.\n",
    )
    for number in (100, 101):
        fx.seed_issue(number, title=f"ready {number}", labels=["agent/ready"])

    daemon_log = fx.root / "daemon.log"
    env = dict(
        FAKE_AGENT_SLEEP="25",
        FAKE_GH_FAIL_LABEL_COUNT="1" if fail_injection else "0",
        # counting query probes ceiling+1=3; recovery query asks for remaining=2.
        FAKE_GH_FAIL_COUNT_LIMIT="2,3",
    )
    proc = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", "2",
         "--max-issues", "2", "--interval", "1"],
        daemon_log,
        **env,
    )
    try:
        time.sleep(9)
    finally:
        Fixture.stop(proc)

    text = daemon_log.read_text(encoding="utf-8")
    return {
        "passes": len(PASS_RE.findall(text)),
        "pass_failed": len(re.findall(r"Daemon pass failed", text)),
        "fail_closed": len(re.findall(r"fail-closed", text)),
        "running_touched": bool(
            re.search(r"#200", text)
            or re.search(r"running 200", text)
            or re.search(r"rework", text, re.I)
        ),
        "started": sorted({p["issue"] for p in fx.probe_events() if p["event"] == "start"}),
        "log_tail": text[-400:],
    }


def main() -> int:
    off = run_case(False)
    on = run_case(True)
    lines = [
        "verifier probe：fail-closed 对「已在跑任务照常推进」的实际影响",
        "",
        "== OFF（不注入，正常轮次基线）==",
        f"  passes={off['passes']} pass_failed={off['pass_failed']} "
        f"fail_closed={off['fail_closed']} running_touched={off['running_touched']} "
        f"started={off['started']}",
        "== ON（注入 agent/running 计数失败）==",
        f"  passes={on['passes']} pass_failed={on['pass_failed']} "
        f"fail_closed={on['fail_closed']} running_touched={on['running_touched']} "
        f"started={on['started']}",
        "",
        "== ON 日志尾部 ==",
        on["log_tail"],
        "",
        "结论：",
        (
            f"  ON 时 pass_failed={on['pass_failed']}、fail_closed={on['fail_closed']}、"
            f"running_touched={on['running_touched']}、ready_started={on['started']}。"
        ),
    ]
    assert on["fail_closed"] > 0, on
    assert on["pass_failed"] == 0, on
    assert on["started"] == [], on
    assert on["running_touched"], on
    write_text("rv-5-inflight-probe.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
