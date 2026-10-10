"""rv-2：补位 / 全局开始按生效上限工作，报告显示三态来源（真实 CLI + 真实 PATCH）。

真入口：``kc backlog advance --dry-run`` 与一次真实 ``kc backlog advance``；
策略行经真实 ``PATCH /backlog/settings`` 写入（→ console SQLite）后，由独立
CLI 进程 fresh 读取。只 fake 外部 gh，kc CLI、补位逻辑、SQLite、配置读取真实。
"""

from __future__ import annotations

import sys

from fixture_lib import Fixture, write_text

CAPACITY = 4
LOG = []


def record(line: str) -> None:
    LOG.append(line)
    print(line)


def main() -> int:
    fx = Fixture("rv2", capacity=CAPACITY, auto_advance=True)
    # This Issue intentionally has no Backlog PRD anchor. It must still consume
    # one slot in the repository-wide running budget.
    fx.seed_issue(590, title="unanchored running issue", labels=["agent/running"])
    console_log = fx.root / "console.log"
    proc = fx.start_console(console_log)
    try:
        code, autopilot = fx.http(
            "GET", "/api/v1/agent-runner/backlog/autopilot?repo_id=rv-fixture"
        )
        record(f"GET /backlog/autopilot -> {code} {autopilot}")

        # 状态 A：从未设置 → 继承容量。
        result = fx.run_kc(["backlog", "advance", "--dry-run", "--repo-id", "rv-fixture"])
        record(f"[A] advance --dry-run (unset) exit={result.returncode}")
        record(result.stdout.strip())
        assert (
            "ceiling=4" in result.stdout
            and "source=inherited" in result.stdout
            and "running=1 free_slots=3" in result.stdout
        ), result.stdout

        # PATCH 策略=2（真实写库），独立 CLI 进程 fresh 读（持久性由新进程证明）。
        code, settings = fx.http(
            "PATCH", "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": 2},
        )
        record(f"PATCH settings max_parallel=2 -> {code} {settings}")
        assert code == 200 and settings.get("effective_max_parallel") == 2, settings

        result = fx.run_kc(["backlog", "advance", "--dry-run", "--repo-id", "rv-fixture"])
        record(f"[B] advance --dry-run (policy=2, fresh CLI) exit={result.returncode}")
        record(result.stdout.strip())
        assert (
            "ceiling=2" in result.stdout
            and "source=policy" in result.stdout
            and "running=1 free_slots=1" in result.stdout
        ), result.stdout

        result = fx.run_kc(["backlog", "advance", "--repo-id", "rv-fixture"])
        record(f"[B2] advance (policy=2, applied) exit={result.returncode}")
        record(result.stdout.strip())
        assert "ceiling=2" in result.stdout and "running=1 free_slots=1" in result.stdout, result.stdout

        # 状态 C：策略高于容量 → 受容量限制（同一 console 会话内继续 PATCH）。
        code, settings = fx.http(
            "PATCH", "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": 8},
        )
        record(f"PATCH settings max_parallel=8 -> {code} {settings}")
        assert settings.get("ceiling_source") == "capped_by_capacity", settings

        result = fx.run_kc(["backlog", "advance", "--dry-run", "--repo-id", "rv-fixture"])
        record(f"[C] advance --dry-run (policy=8>capacity, fresh CLI) exit={result.returncode}")
        record(result.stdout.strip())
        assert (
            "ceiling=4" in result.stdout
            and "source=capped_by_capacity" in result.stdout
            and "running=1 free_slots=3" in result.stdout
        ), result.stdout

        Fixture.stop(proc)
        row = fx.read_settings_row()
        record(f"只读 SQLite 核对设置行: {row}")
        assert row and row["max_parallel"] == 8, row

        gh_calls = fx.gh_call_lines()
        record(f"fake gh 调用条数: {len(gh_calls)}")
    finally:
        try:
            Fixture.stop(proc)
        except Exception:
            pass

    write_text("rv-2-advance-ceiling-report.txt", "\n".join(LOG) + "\n")
    record("EVIDENCE rv-2-advance-ceiling-report.txt written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
