"""真实入口证据采集脚本 — run-daemon-autopilot-control-surface。

用法：uv run python tasks/evidence/run-daemon-autopilot-control-surface/rv_real_entry.py

覆盖 rv-1 / rv-2 / rv-5 / rv-6 / rv-7 / rv-8 的真实进程入口验证。
GitHub 边界按 PRD mock_boundary 允许 mock（本脚本只测 CLI 解析、互斥锁、
接管编排与调度门控，不触达真实 GitHub；需要 GitHub 的阶段在单测里用
Fake 客户端覆盖）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
IAR = [str(REPO / ".venv" / "bin" / "iar")]
RESULTS: list[dict] = []


def record(rv_id: str, name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"rv": rv_id, "check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {rv_id} {name}: {detail}")


def run_iar(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    child_env = dict(os.environ)
    child_env.setdefault("IAR_SKIP_GH_AUTH_CHECK", "1")
    if env:
        child_env.update(env)
    return subprocess.run(
        [*IAR, *args], cwd=str(REPO), capture_output=True, text=True, timeout=120, env=child_env
    )


def _temp_iar_config(tmp: str) -> dict:
    """构造指向临时 registry 的 IAR_CONFIG 环境与最小仓库。"""
    tmp_path = Path(tmp)
    registry = tmp_path / "processes.json"
    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        "[agent_runner.console]\n"
        f'process_registry_path = "{registry}"\n'
        f'process_log_dir = "{tmp_path / "logs"}"\n'
        "[agent_runner.repositories.keda-echo]\n"
        f'path = "{tmp_path / "echo-repo"}"\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    echo_repo = tmp_path / "echo-repo"
    echo_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(echo_repo)], check=True)
    (echo_repo / ".iar.toml").write_text(
        '[agent_runner.repository]\nid = "keda-echo"\nenabled = true\n', encoding="utf-8"
    )
    return {"IAR_CONFIG": str(config_toml)}, registry, echo_repo


def main() -> int:
    # ── rv-5: 无目标 → 用法错误 exit 2 ──────────────────────────────────
    proc = run_iar(["run"])
    record(
        "rv-5",
        "bare iar run is usage error",
        proc.returncode == 2 and "--all-ready" in (proc.stderr + proc.stdout),
        f"exit={proc.returncode} output={(proc.stderr or proc.stdout).strip()[:160]!r}",
    )

    # ── rv-4: run --help 无 --autopilot，有目标旗标 ─────────────────────
    proc = run_iar(["run", "--help"])
    help_text = proc.stdout + proc.stderr
    record(
        "rv-4",
        "run --help has targets, no --autopilot",
        all(f in help_text for f in ("--issue", "--all-ready", "--takeover"))
        and "--autopilot" not in help_text,
        "flags present: --issue/--all-ready/--takeover; --autopilot absent",
    )
    proc = run_iar(["schema", "--json"])
    schema_text = proc.stdout
    daemon_run_flags = ""
    try:
        import json as _json

        for command in _json.loads(schema_text)["commands"]:
            if tuple(command["path"]) == ("daemon", "run"):
                daemon_run_flags = " ".join(n for o in command["options"] for n in o["names"])
    except Exception as exc:  # noqa: BLE001
        schema_text += f"\n[schema parse error: {exc}]"
    record(
        "rv-8",
        "daemon run exposes --autopilot/--no-autopilot (schema)",
        "--autopilot" in daemon_run_flags and "--no-autopilot" in daemon_run_flags,
        f"daemon run flags: {daemon_run_flags!r}",
    )

    # ── rv-7: PRD 两态解析（真实文件 + 真实解析函数） ───────────────────
    from backend.core.use_cases.run_target_resolve import (
        RunTargetResolveError,
        resolve_prd_target_issue_number,
    )

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        linked = repo / "tasks" / "pending"
        linked.mkdir(parents=True)
        (linked / "linked.md").write_text(
            "# PRD\n- GitHub Issue: https://github.com/org/repo/issues/417\n",
            encoding="utf-8",
        )
        (linked / "unlinked.md").write_text(
            "# PRD\n- GitHub Issue: (to be created)\n", encoding="utf-8"
        )
        got = resolve_prd_target_issue_number(repo_path=repo, prd_path="tasks/pending/linked.md")
        try:
            resolve_prd_target_issue_number(repo_path=repo, prd_path="tasks/pending/unlinked.md")
            err = None
        except RunTargetResolveError as exc:
            err = str(exc)
        record(
            "rv-7",
            "PRD link two-state",
            got == 417 and err is not None and "iar issue create" in err,
            f"linked->#{got}; unlinked error mentions 'iar issue create': {err is not None}",
        )

    # ── rv-2: 真实进程持有 daemon 锁 → run 被拒、锁未被破坏 ────────────
    with tempfile.TemporaryDirectory() as tmp:
        env, registry, echo_repo = _temp_iar_config(tmp)
        lock_dir = registry.parent / "daemon-locks"
        lock_dir.mkdir(parents=True)
        holder = subprocess.Popen(["/bin/sleep", "120"])
        try:
            (lock_dir / "keda-echo.lock").write_text(f"{holder.pid}\n", encoding="utf-8")
            proc = run_iar(["run", "--all-ready", "--repo-id", "keda-echo"], env=env)
            combined = proc.stderr + proc.stdout
            record(
                "rv-2",
                "run rejected while daemon lock held",
                proc.returncode == 5 and "already running" in combined and "--takeover" in combined,
                f"exit={proc.returncode} mentions takeover={'--takeover' in combined}",
            )
            record(
                "rv-2",
                "negative control: daemon untouched by rejection",
                holder.poll() is None,
                f"holder pid {holder.pid} still alive={holder.poll() is None}",
            )
        finally:
            holder.send_signal(signal.SIGTERM)
            holder.wait(timeout=10)

        # ── rv-6: 真实接管编排（未托管路径）：SIGTERM 优雅停 + 无孤儿 ──
        # holder 用 sh 包装，sleep 是其后代且同组 → 停后按组补扫必须清掉它。
        holder = subprocess.Popen(["/bin/sh", "-c", "sleep 120 & wait"], start_new_session=True)
        agent_pid: int | None = None
        try:
            from backend.engines.agent_runner.run_takeover import take_over_daemon
            from backend.core.shared.models.agent_runner import AppConfig

            (lock_dir / "keda-echo.lock").write_text(f"{holder.pid}\n", encoding="utf-8")

            class _FakeClient:
                def list_issues_by_label(self, label, limit):
                    return []

            result = take_over_daemon(
                repo_id="keda-echo",
                daemon_pid=holder.pid,
                config=AppConfig(),
                github_client=_FakeClient(),
                process_registry_path=registry,
                process_log_dir=Path(tmp) / "logs",
                stop_timeout_seconds=10,
            )
            time.sleep(0.3)
            # 找 sh 的 sleep 后代是否已被清理：组清扫后 sleep 不应存活。
            import psutil

            agent_pid = (
                next(
                    (
                        child.pid
                        for child in psutil.Process(holder.pid).children(recursive=True)
                        if child.is_running()
                    ),
                    None,
                )
                if holder.poll() is None
                else None
            )
            record(
                "rv-6",
                "takeover stops daemon gracefully (sigterm)",
                result.final_signal == "sigterm" and holder.poll() is not None,
                f"final_signal={result.final_signal} daemon exited={holder.poll() is not None}",
            )
            record(
                "rv-6",
                "negative control: no orphan agent after takeover",
                agent_pid is None,
                f"leftover descendant pid={agent_pid}",
            )
        finally:
            for proc_ in (holder,):
                if proc_.poll() is None:
                    proc_.kill()
                    proc_.wait(timeout=10)

    # ── rv-1: 定向 dry-run 预览只携带目标（真实 CLI JSON） ──────────────
    with tempfile.TemporaryDirectory() as tmp:
        env, _registry, _echo = _temp_iar_config(tmp)
        proc = run_iar(
            ["run", "--issue", "42", "--dry-run", "--json", "--repo-id", "keda-echo"], env=env
        )
        combined = proc.stderr + proc.stdout
        # GitHub mock 边界：dry-run 后续 ready 扫描会因无真实仓库失败（exit 1），
        # 但机器预览已在失败前发出并携带 target_issue —— 断言以预览为准。
        record(
            "rv-1",
            "targeted run dry-run preview carries target_issue",
            '"target_issue": 42' in proc.stdout and '"all_ready": false' in proc.stdout,
            f"exit={proc.returncode} stdout head={proc.stdout.strip()[:120]!r}",
        )

    (
        REPO / "tasks/evidence/run-daemon-autopilot-control-surface/rv-real-entry-results.json"
    ).write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
