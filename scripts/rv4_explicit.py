"""rv-4：显式运行不受生效上限约束（真实 kc run + fake agent），--all-ready 互斥仍拒绝。

- 定向：并发=1 且已有 1 个在跑（daemon 会因预算=0 不认领），但 ``kc run --issue N``
  仍照常执行本次运行（fake agent 被真实拉起）→ 证明显式定向豁免。
- 互斥：同仓 daemon 在跑时 ``kc run --all-ready`` 仍按现值拒绝（退出码 5）。
"""

from __future__ import annotations

import subprocess
import sys
import time

from fixture_lib import Fixture, write_text

CAPACITY = 10


def main() -> int:
    lines: list[str] = []
    fx = Fixture("rv4", capacity=CAPACITY, auto_advance=False)
    fx.seed_issue(200, title="pre-running", labels=["agent/running"])
    fx.seed_issue(100, title="target ready", labels=["agent/ready"])

    # 真实 PATCH 把并发设为 1（→ ceiling=min(1,10)=1），已有 1 个在跑占满。
    proc = fx.start_console(fx.root / "console.log")
    try:
        code, settings = fx.http(
            "PATCH", "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": 1},
        )
        lines.append(f"PATCH settings max_parallel=1 -> {code} {settings}")
        assert code == 200 and settings.get("effective_max_parallel") == 1, settings
    finally:
        Fixture.stop(proc)

    # 定向运行：不受上限拦截，本次执行照常完成（fake agent 真实被调用）。
    result = fx.run_kc(
        ["run", "--issue", "100", "--repo-id", "rv-fixture"],
        timeout=120,
        FAKE_AGENT_SLEEP="3",
        FAKE_AGENT_COMMIT_REQUEST="1",
        FAKE_AGENT_COMMIT_REQUEST_MARKER=str(fx.state_dir / "commit-request-issued"),
    )
    started = sorted({p["issue"] for p in fx.probe_events() if p["event"] == "start"})
    ended = sorted({p["issue"] for p in fx.probe_events() if p["event"] == "end"})
    issue_worktree = fx.repo_path / ".iar-worktrees" / "issue-100"
    committed_head = "not-created"
    if issue_worktree.is_dir():
        head_result = subprocess.run(
            ["git", "-C", str(issue_worktree), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        committed_head = head_result.stdout.strip()
    lines.append(
        f"kc run --issue 100 exit={result.returncode} agent_started_issues={started} "
        f"agent_completed_issues={ended} committed_head={committed_head}"
    )
    lines.append("  (ceiling=1 且 1 个在跑时守护进程 ready 认领预算=0；定向运行仍执行=豁免)")
    lines.extend(
        [
            "  --stdout--",
            *(f"  {line}" for line in result.stdout.strip().splitlines()[-8:]),
            "  --stderr--",
            *(f"  {line}" for line in result.stderr.strip().splitlines()[-8:]),
        ]
    )
    assert result.returncode == 0, (
        f"targeted run did not complete: exit={result.returncode}; "
        f"stdout={result.stdout[-1000:]!r}; stderr={result.stderr[-1000:]!r}"
    )
    assert 100 in started and 100 in ended, (started, ended)
    assert committed_head, "runner commit proxy did not produce a fresh issue worktree commit"
    assert (issue_worktree / "rv-agent-output.txt").read_text(encoding="utf-8") == (
        "fake agent completed Issue #100\n"
    )

    # --all-ready 与在跑的 daemon 互斥：拒绝（退出码 5）。
    daemon_log = fx.root / "daemon.log"
    daemon = fx.spawn(
        ["daemon", "run", "--repo-id", "rv-fixture", "--concurrency", str(CAPACITY),
         "--max-issues", str(CAPACITY), "--interval", "1"],
        daemon_log,
        FAKE_AGENT_SLEEP="25",
    )
    try:
        time.sleep(3)
        conflict = fx.run_kc(
            ["run", "--all-ready", "--repo-id", "rv-fixture"],
            timeout=30,
            FAKE_AGENT_SLEEP="3",
        )
        combined = (conflict.stdout + conflict.stderr).strip().splitlines()
        lines.append(f"kc run --all-ready (daemon live) exit={conflict.returncode}")
        lines.extend(f"  | {line}" for line in combined[-6:])
        assert conflict.returncode == 5, conflict.returncode
    finally:
        Fixture.stop(daemon)

    write_text("rv-4-explicit-run-exempt.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    print("EVIDENCE rv-4-explicit-run-exempt.txt written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
