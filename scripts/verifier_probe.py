"""独立 verifier 探针：使用真实 SQLite 写入路径 + 真实聚合，覆盖 builder 未直接验证的核心场景。

不 mock 被测对象：通过真实 SqliteConsoleStore.append_attempt / append_run 落库
（含 preset/model 快照列），再由 build_agent_performance_stats 读取聚合。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src" / "backend"))

from backend.core.use_cases.console_stats import build_agent_performance_stats  # noqa: E402
from backend.infrastructure.persistence.console_store import SqliteConsoleStore  # noqa: E402
from backend.infrastructure.persistence.console_store_history import (  # noqa: E402
    AttemptRecord,
    RunRecord,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
RECENT = (NOW - timedelta(days=1)).isoformat()


def make_attempt(agent, failure_type, duration, preset, model):
    return AttemptRecord(
        repo_id="keda-main",
        issue_number=99,
        agent=agent,
        attempt_number=1,
        failure_type=failure_type,
        recovered=False,
        detail="probe",
        started_at=RECENT,
        finished_at=RECENT,
        duration_seconds=duration,
        preset=preset,
        model=model,
    )


def make_run(outcome, duration):
    return RunRecord(
        repo_id="keda-main",
        repo_path="/tmp/x",
        issue_number=99,
        trigger="probe",
        agent="codex",
        outcome=outcome,
        error_summary=None,
        started_at=RECENT,
        finished_at=RECENT,
        duration_seconds=duration,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteConsoleStore(Path(tmp) / "console.db")
        # 核心解读样例 #2：同一 Issue 由 codex 失败、claude 成功；最终任务 completed。
        store.append_attempt(make_attempt("codex", "verification_failed", 100, "preset-gone", "m1"))
        store.append_attempt(make_attempt("claude", "success", 10, "preset-gone", "m1"))
        # 未绑定预设的 attempt（preset 为空字符串，模拟 writer 无绑定）+ 一个 None（旧记录）
        store.append_attempt(make_attempt("gemini", "success", 20, "", ""))
        store.append_attempt(make_attempt("gemini", "success", 40, None, None))
        # 负 duration：应计入样本但不进入分位数
        store.append_attempt(make_attempt("claude", "success", -5, "preset-gone", "m1"))
        store.append_run(make_run("completed", 300))

        stats = build_agent_performance_stats(
            store=store, repo_id="keda-main", days=30, reference_now=NOW
        )

        failures = []

        def check(cond, msg):
            if not cond:
                failures.append(msg)

        agents = {g.agent: g for g in stats.agents}
        # codex：1 次、全非成功 → success_rate 0（有样本，允许 0%），不继承 claude 的成功
        check("codex" in agents, "codex group missing")
        if "codex" in agents:
            g = agents["codex"]
            check(g.attempt_count == 1, f"codex count {g.attempt_count}")
            check(g.success_count == 0, f"codex success {g.success_count}")
            check(g.success_rate == 0.0, f"codex rate {g.success_rate}")
        # claude：3 次（含负样本），2 成功；分位数只用非负 [10]→但还有 gemini? no claude durations 10,-5 → valid [10]
        if "claude" in agents:
            g = agents["claude"]
            check(g.attempt_count == 2, f"claude count {g.attempt_count}")
            check(g.success_count == 2, f"claude success {g.success_count}")
            # durations [10, -5] → valid [10] → p50=p90=10
            check(g.p50_duration_seconds == 10, f"claude p50 {g.p50_duration_seconds}")
            check(g.p90_duration_seconds == 10, f"claude p90 {g.p90_duration_seconds}")
        # gemini 空/None preset 不应进入任何 preset 组，而进入 unbound 计数
        check(stats.unbound_preset_attempt_count == 2, f"unbound {stats.unbound_preset_attempt_count}")
        preset_agents = {g.agent for g in stats.presets}
        check("gemini" not in preset_agents, "gemini leaked into preset groups")
        # 每个 attempt 只归属到实际执行的 agent：claude 成功没有复制给 codex
        check(agents.get("codex") and agents["codex"].success_count == 0, "double attribution!")

        # runs 独立、不含 agent 字段（整项任务口径）
        run_by_outcome = {g.outcome: g for g in stats.runs}
        check(run_by_outcome["completed"].run_count == 1, "run count")
        check(run_by_outcome["completed"].p50_duration_seconds == 300, "run p50")

        # 空窗口
        empty_stats = build_agent_performance_stats(
            store=store, repo_id="nope", days=30, reference_now=NOW
        )
        check(empty_stats.agents == () and empty_stats.runs == (), "empty repo produced groups")

        # 全部仓库视图：同预设按仓库分行，不混淆
        all_stats = build_agent_performance_stats(
            store=store, repo_id=None, days=30, reference_now=NOW
        )
        check(all_stats.repo_id is None, "all-repo repo_id not None")
        check(all(g.repo_id == "keda-main" for g in all_stats.presets), "all-repo preset rows repo tag")

    if failures:
        print("PROBE FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PROBE OK: all independent assertions passed")
    print(f"  agents={[(g.agent, g.attempt_count, g.success_count) for g in stats.agents]}")
    print(f"  unbound={stats.unbound_preset_attempt_count} presets={[(g.agent,g.preset,g.model,g.attempt_count) for g in stats.presets]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
