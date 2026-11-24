"""测试 Keda 阻塞巡查循环能统计超过 gh 默认 30 条上限的 Issue。"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from backend.core.use_cases.loop_recipe import parse_loop_recipe, render_loop_recipe

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


def test_blocker_monitor_uses_duty_evidence_and_respects_takeover() -> None:
    """仓库巡检配方引用岗位说明并在人工接管后停止。"""
    recipe_text = BLOCKER_MONITOR_RECIPE.read_text(encoding="utf-8")
    assert "references/duty.md" in recipe_text
    assert "explicit human takeover" in recipe_text
    assert "current head" not in recipe_text
    assert "SHA for each check" not in recipe_text
    assert "Attempt History" not in recipe_text
    assert "when the input is unchanged and there is no new progress" in recipe_text
    assert "Patrol result and PR description" in recipe_text
    assert "## Constraints" in recipe_text


def test_blocker_monitor_renders_its_label_and_lists_the_full_queue() -> None:
    """真实配方渲染后排除自身，并保留超过 gh 默认上限的查询要求。"""
    loop_recipe = parse_loop_recipe(BLOCKER_MONITOR_RECIPE)
    rendered_recipe = render_loop_recipe(
        loop_recipe,
        fire_at=datetime(2026, 11, 24, tzinfo=UTC),
        extra_variables={"blocked_count": "37", "failed_count": "12", "needs_attention": "49"},
    )

    assert "loop/keda-blocker-monitor-30m" in rendered_recipe
    assert "loop/{loop_id}" not in rendered_recipe
    assert "`gh issue list` queries must pass `--limit 1000`" in rendered_recipe
