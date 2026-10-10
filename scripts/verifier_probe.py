"""独立 verifier 探针（非 builder 交付物）：补位是否真的按生效上限封顶。

builder 的 rv-2 fixture 里没有任何 pending PRD，advance 全程输出 "nothing to do"，
因此它只证明了报告头的 ceiling/source 字符串，没有证明「补入量被上限截断」这一
Measurable Objective（未设置时一轮最多补入 10−在跑，不再压在 2）。本脚本用真实
pending PRD + 真实 kc CLI + 真实 PATCH/SQLite 补上这一段。

A1 容量10 / 未设策略 / 6 pending  → 期望补入 6（旧口径伪造默认 2 只会补 2）
A2 容量10 / 策略=2 / 6 pending    → 期望补入 2、排队 4
A3 容量10 / 策略=2 / 2 在跑+4 pending → 期望补入 0、排队 4（在跑数计入预算）
C  真实仓库 .kedacode.toml（容量 10）+ 空设置库 → 期望 ceiling=10 source=inherited
"""

from __future__ import annotations

import sys
from pathlib import Path

from fixture_lib import Fixture, write_pending_prd, write_text

# 真实安装的 prd skill（builder 的 fixture 用桩替代，导致其 rv-2 根本扫不到 PRD）。
REAL_PRD_SKILL = str(Path.home() / ".kedacode" / "skills" / "prd" / "SKILL.md")
SKILL_ENV = {
    "IAR_PRD_SKILL_PATH": REAL_PRD_SKILL,
    "KEDACODE_PRD_SKILL_PATH": REAL_PRD_SKILL,
}

READY_BASE = 500


def build_repo(*, name: str, prd_count: int, running_prds: int) -> Fixture:
    """capacity=10 的 fixture 仓库，含 prd_count 个 pending PRD。

    running_prds 个 PRD 关联到已带 agent/running 标签的 Issue（占坑），
    其余关联到无标签 Issue（NOT_STARTED，可被补位晋升）。
    """
    fx = Fixture(name, capacity=10, auto_advance=True)
    for index in range(prd_count):
        number = READY_BASE + index
        labels = ["agent/running"] if index < running_prds else []
        fx.seed_issue(number, title=f"prd issue {number}", labels=labels)
        write_pending_prd(
            fx,
            f"tasks/pending/P1-FEAT-20260101-0000{index:02d}-probe.md",
            f"probe prd {index}",
            issue_number=number,
        )
    return fx


def set_policy(fx: Fixture, value: int | None) -> dict:
    """经真实 console PATCH 写入策略（value=None 表示不写，保持未设置）。"""
    if value is None:
        return {}
    proc = fx.start_console(fx.root / "console.log")
    try:
        code, body = fx.http(
            "PATCH",
            "/api/v1/agent-runner/backlog/settings?repo_id=rv-fixture",
            {"max_parallel": value},
        )
        assert code == 200, body
        return body
    finally:
        Fixture.stop(proc)


def advance(fx: Fixture) -> tuple[int, str]:
    result = fx.run_kc(
        ["backlog", "advance", "--repo-id", "rv-fixture"], **SKILL_ENV
    )
    return result.returncode, result.stdout


def count_promoted(stdout: str) -> int:
    return sum(1 for line in stdout.splitlines() if "promoted" in line)


def count_queued(stdout: str) -> int:
    """rich 会把 queued 列表折行打印；带引号的 `probe.md'` 只出现在 queued 条目里。"""
    return stdout.count("probe.md'")


def main() -> int:
    out: list[str] = []
    failures: list[str] = []

    def check(cond: bool, label: str, detail: str) -> None:
        out.append(f"{'ok  ' if cond else 'FAIL'} {label}: {detail}")
        if not cond:
            failures.append(label)

    # Negative control first: apply the real persisted cap, then evaluate the
    # inherited-capacity oracle against that wrong state. It must reject 2 != 6.
    negative_fx = build_repo(name="vp-negative", prd_count=6, running_prds=0)
    negative_patch = set_policy(negative_fx, 2)
    negative_code, negative_stdout = advance(negative_fx)
    negative_promoted = count_promoted(negative_stdout)
    out.append(
        f"[NEG CONTROL] PATCH policy=2 status-effective="
        f"{negative_patch.get('effective_max_parallel')} advance_exit={negative_code} "
        f"promoted={negative_promoted} inherited_expected=6"
    )
    if negative_code != 0 or negative_promoted == 6:
        failures.append("negative control did not turn the inherited-capacity oracle red")
        out.append("NEG CONTROL: FAILED TO REPRODUCE RED")
    else:
        out.append(
            "NEG CONTROL: RED reproduced — setting=2 yields 2 promoted, "
            "so the inherited expectation of 6 rejects this state."
        )

    # ── A1：未设策略 → 继承容量 10，6 个 pending 应全部补入 ─────────────────
    fx = build_repo(name="vp-a1", prd_count=6, running_prds=0)
    code, stdout = advance(fx)
    out.append(f"[A1] exit={code}\n{stdout.strip()}")
    check("ceiling=10 source=inherited" in stdout, "A1 ceiling/source", stdout.splitlines()[1])
    check(count_promoted(stdout) == 6, "A1 promoted==6 (旧口径会是 2)", str(count_promoted(stdout)))
    state = fx.gh_state()
    now_ready = sorted(
        int(n) for n, i in state["issues"].items() if "agent/ready" in i["labels"]
    )
    check(now_ready == list(range(500, 506)), "A1 issues labelled ready", str(now_ready))

    # ── A2：策略=2 → 只补 2，其余排队 ──────────────────────────────────────
    fx = build_repo(name="vp-a2", prd_count=6, running_prds=0)
    body = set_policy(fx, 2)
    check(body.get("effective_max_parallel") == 2, "A2 PATCH effective", str(body))
    code, stdout = advance(fx)
    out.append(f"[A2] exit={code}\n{stdout.strip()}")
    check("ceiling=2 source=policy" in stdout, "A2 ceiling/source", stdout.splitlines()[1])
    check(count_promoted(stdout) == 2, "A2 promoted==2", str(count_promoted(stdout)))
    check(count_queued(stdout) == 4, "A2 queued==4", str(count_queued(stdout)))

    # ── A3：策略=2 且 2 个已在跑 → 预算 0，一个都不补 ──────────────────────
    fx = build_repo(name="vp-a3", prd_count=6, running_prds=2)
    set_policy(fx, 2)
    code, stdout = advance(fx)
    out.append(f"[A3] exit={code}\n{stdout.strip()}")
    check("free_slots=0" in stdout, "A3 free_slots==0", stdout.splitlines()[1])
    check(count_promoted(stdout) == 0, "A3 promoted==0", str(count_promoted(stdout)))
    check(count_queued(stdout) == 4, "A3 queued==4", str(count_queued(stdout)))

    # ── C：真实仓库（本 worktree 的 .kedacode.toml，容量 10）+ 空设置库 ──────
    # 操作员视角的原始抱怨：页面显示 2、配置显示 10。修好后未设置策略时应报 10。
    import os
    import subprocess

    worktree = Path(__file__).resolve().parents[4]
    real = Fixture("vp-c", capacity=10, auto_advance=True)
    home = real.root / "real-home"
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    cfg.write_text(
        "[agent_runner.console]\n"
        f'history_db_path = "{home / "console.db"}"\n'
        f'process_registry_path = "{home / "processes.json"}"\n'
        f'process_log_dir = "{home / "process-logs"}"\n'
        "port = 0\n\n"
        "[agent_runner.repositories.keda]\n"
        f'path = "{worktree}"\n'
        "enabled = true\n"
        'display_name = "keda worktree issue-266"\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "IAR_CONFIG": str(cfg),
            "KEDACODE_CONFIG": str(cfg),
            "IAR_SKIP_GH_AUTH_CHECK": "1",
            "KEDACODE_SKIP_GH_AUTH_CHECK": "1",
            "PATH": f"{Path(__file__).resolve().parent}{os.pathsep}{env['PATH']}",
            "FAKE_GH_STATE": str(real.state_dir),
            "FAKE_AGENT_PROBE": str(real.state_dir / "probe.jsonl"),
        }
    )
    env.update({
        "IAR_PRD_SKILL_PATH": REAL_PRD_SKILL,
        "KEDACODE_PRD_SKILL_PATH": REAL_PRD_SKILL,
    })
    proc = subprocess.run(
        [str(worktree / ".venv" / "bin" / "kc"), "backlog", "advance", "--dry-run",
         "--repo-id", "keda"],
        cwd=str(worktree), capture_output=True, text=True, env=env, timeout=180,
    )
    real_stdout = proc.stdout + proc.stderr
    out.append(f"[C] exit={proc.returncode}\n{real_stdout.strip()[:1500]}")
    check("ceiling=10" in real_stdout, "C ceiling==10（真实 .kedacode.toml 容量）",
          real_stdout.splitlines()[0] if real_stdout.strip() else "no output")
    check("source=inherited" in real_stdout, "C source==inherited", real_stdout[:400])

    report = "\n".join(out)
    print(report)
    print()
    if failures:
        print("PROBE RESULT: FAIL", failures)
        write_text(
            "rv-2-backfill-continuous-scheduling.txt",
            report + "\nPROBE RESULT: FAIL " + repr(failures) + "\n",
        )
        return 1
    print("PROBE RESULT: PASS")
    write_text("rv-2-backfill-continuous-scheduling.txt", report + "\nPROBE RESULT: PASS\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
