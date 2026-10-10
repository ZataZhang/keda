"""rv-5 负控：暂时恢复旧的未兜底恢复候选查询，再运行真实 daemon 验证。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fixture_lib import EVIDENCE_DIR, SCRIPTS_DIR, write_text

SOURCE = EVIDENCE_DIR.parents[2] / "src/backend/core/use_cases/agent_runner_orchestration_runtime.py"
ANCHOR = "                if not running_count_failed:\n                    raise\n"


def main() -> int:
    original = SOURCE.read_bytes()
    source_text = original.decode("utf-8")
    if source_text.count(ANCHOR) != 1:
        raise RuntimeError("无法唯一定位 running-count 失败后的恢复查询保护分支")

    lines = [
        "rv-5 negative control: 暂时注入旧行为（恢复候选查询异常直接抛出）。",
        "执行真实 kc daemon fixture；计数失败与 agent/running 查询失败均由 fake gh 定点注入。",
    ]
    try:
        SOURCE.write_text(source_text.replace(ANCHOR, "                raise\n", 1), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "rv5_failclosed.py"],
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        fixture_log = SCRIPTS_DIR / "work/rv5/daemon.log"
        log_text = fixture_log.read_text(encoding="utf-8", errors="replace")
        daemon_failures = log_text.count("Daemon pass failed")
        running_recovery = "Skipping Issue #200" in log_text
        lines.extend(
            [
                f"故意恢复旧分支后 rv5_failclosed.py exit={result.returncode}",
                f"daemon_pass_failed={daemon_failures} running_recovery_discovered={running_recovery}",
                "--- 断言输出 ---",
                result.stdout[-1400:],
                result.stderr[-1800:],
            ]
        )
        expected_red = (
            result.returncode != 0
            and daemon_failures > 0
            and not running_recovery
        )
        lines.append(
            "NEG CONTROL: RED reproduced — 旧行为让 daemon pass 失败且在途恢复项未发现。"
            if expected_red
            else "NEG CONTROL: 未复现预期红态。"
        )
        if not expected_red:
            raise RuntimeError("负控没有复现恢复候选查询异常导致的红态")
    finally:
        SOURCE.write_bytes(original)
        if SOURCE.read_bytes() != original:
            raise RuntimeError("源文件未能按字节完整还原")
        for cached_module in (SOURCE.parent / "__pycache__").glob(
            "agent_runner_orchestration_runtime.*.pyc"
        ):
            cached_module.unlink(missing_ok=True)
        lines.append("生产源码已按字节还原。")

    write_text("rv-5-negative-control.txt", "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
