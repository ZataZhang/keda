"""rv-4 负控：暂时把上限传入显式命令并移除定向豁免，确认真实 CLI 探针变红。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fixture_lib import EVIDENCE_DIR, SCRIPTS_DIR, write_text

REPO_ROOT = EVIDENCE_DIR.parents[2]
RUNNER_SOURCE = REPO_ROOT / "src/backend/core/use_cases/run_agent_repositories_once.py"
RUNTIME_SOURCE = REPO_ROOT / "src/backend/core/use_cases/agent_runner_orchestration_runtime.py"
RUNNER_ANCHOR = "                publish_stage=publish_stage,\n            )\n"
RUNTIME_ANCHOR = "    if request.execution_ceiling is not None and target_issue_summary is None:\n"


def main() -> int:
    originals = {path: path.read_bytes() for path in (RUNNER_SOURCE, RUNTIME_SOURCE)}
    runner_text = originals[RUNNER_SOURCE].decode("utf-8")
    runtime_text = originals[RUNTIME_SOURCE].decode("utf-8")
    if runner_text.count(RUNNER_ANCHOR) != 1 or runtime_text.count(RUNTIME_ANCHOR) != 1:
        raise RuntimeError("显式运行上限负控锚点不唯一或已变化")

    lines = [
        "rv-4 negative control: 暂时注入回归——显式 CLI 传入 ceiling，定向请求也进入预算门控。",
        "执行真实 kc run --issue fixture；外部 GitHub 与 agent 仅使用隔离 fake 边界。",
    ]
    try:
        RUNNER_SOURCE.write_text(
            runner_text.replace(
                RUNNER_ANCHOR,
                "                publish_stage=publish_stage,\n                execution_ceiling=1,\n            )\n",
                1,
            ),
            encoding="utf-8",
        )
        RUNTIME_SOURCE.write_text(
            runtime_text.replace(
                RUNTIME_ANCHOR,
                "    if request.execution_ceiling is not None:\n",
                1,
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "rv4_explicit.py"],
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        fixture_probe = SCRIPTS_DIR / "work/rv4/gh-state/agent-probes.jsonl"
        events = fixture_probe.read_text(encoding="utf-8", errors="replace") if fixture_probe.exists() else ""
        issue_100_started = '"issue": 100' in events and '"event": "start"' in events
        expected_red = result.returncode != 0 and not issue_100_started and "AssertionError" in result.stderr
        lines.extend(
            [
                f"故意注入旧行为后 rv4_explicit.py exit={result.returncode}",
                f"target_issue_100_agent_started={issue_100_started}",
                "--- 断言输出 ---",
                result.stdout[-1200:],
                result.stderr[-1800:],
                (
                    "NEG CONTROL: RED reproduced — ceiling=1 且已有 1 个在跑时，回归门控会拦下定向运行。"
                    if expected_red
                    else "NEG CONTROL: 未复现预期红态。"
                ),
            ]
        )
        if not expected_red:
            lines.extend(
                [
                    "负控本身未达到红态预期。",
                    f"子进程 stdout={result.stdout[-1600:]!r}",
                    f"子进程 stderr={result.stderr[-1600:]!r}",
                ]
            )
            write_text("rv-4-negative-control.txt", "\n".join(lines) + "\n")
            print("\n".join(lines))
            raise RuntimeError("负控未能让显式运行豁免断言变红")
    finally:
        for path, content in originals.items():
            path.write_bytes(content)
            if path.read_bytes() != content:
                raise RuntimeError(f"源文件未能按字节还原：{path.name}")
            for cached_module in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
                cached_module.unlink(missing_ok=True)
        lines.append("生产源码已按字节还原并清除临时字节码缓存。")

    write_text("rv-4-negative-control.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
