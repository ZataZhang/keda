"""测试 Keda 阻塞巡查循环能统计超过 gh 默认 30 条上限的 Issue。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.core.use_cases.loop_recipe import parse_loop_recipe

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOCKER_MONITOR_RECIPE = REPO_ROOT / "tasks" / "loops" / "keda-blocker-monitor-30m.md"


def test_blocker_monitor_counts_beyond_gh_default_limit(tmp_path: Path) -> None:
    """通过真实 loop 解析与 shell 入口验证大于默认分页数的计数。"""
    loop_recipe = parse_loop_recipe(BLOCKER_MONITOR_RECIPE)
    assert loop_recipe.pre_command is not None

    stub_gh_path = tmp_path / "gh"
    stub_gh_script = """#!/bin/sh
case "$*" in
  *"--label agent/blocked"*"--limit 1000"*) printf '%s\\n' 37 ;;
  *"--label agent/failed"*"--limit 1000"*) printf '%s\\n' 12 ;;
  *) printf 'unexpected gh issue list arguments: %s\\n' "$*" >&2; exit 1 ;;
esac
"""
    stub_gh_path.write_text(stub_gh_script, encoding="utf-8")
    stub_gh_path.chmod(0o755)

    child_environment = os.environ.copy()
    child_environment["PATH"] = f"{tmp_path}{os.pathsep}{child_environment['PATH']}"
    pre_command_result = subprocess.run(
        ["/bin/sh", "-c", loop_recipe.pre_command],
        cwd=REPO_ROOT,
        env=child_environment,
        capture_output=True,
        check=True,
        text=True,
    )

    assert pre_command_result.stdout == ("blocked_count=37\nfailed_count=12\nneeds_attention=49\n")
