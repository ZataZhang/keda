"""Tests for the first agent-daemon loop application."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from backend.core.use_cases.loop_recipe import parse_loop_recipe, render_loop_recipe

RECIPE_PATH = Path(__file__).resolve().parents[1] / "tasks" / "loops" / "nightly-main-ci.md"


def test_nightly_main_ci_recipe_uses_agent_daemon() -> None:
    """主线 CI recipe 应由普通 daemon 负责推进。"""
    recipe = parse_loop_recipe(RECIPE_PATH)

    assert recipe.id == "nightly-main-ci"
    assert recipe.repo_id == "keda"
    assert recipe.schedule.expression == "0 1 * * *"
    assert recipe.managed_by == "agent-daemon"
    assert recipe.timezone_name == "Asia/Shanghai"
    assert recipe.labels == ("scope/ci",)
    assert recipe.pre_command is not None
    # 每晚只建普通 Issue，不生成 PRD；CI 正常的夜晚由 skip_when 直接跳过。
    assert recipe.output == "issue"
    assert recipe.skip_when == "last_ci_status=success,none"


def test_nightly_main_ci_recipe_renders_ci_status() -> None:
    """CI 结论应进入 Issue body，供 runner 判断是否需要修复。"""
    recipe = parse_loop_recipe(RECIPE_PATH)
    rendered = render_loop_recipe(
        recipe,
        fire_at=datetime(2026, 10, 15, 1, 0),
        extra_variables={"last_ci_status": "failure"},
    )

    assert "最近一次 main workflow 的结论是：" in rendered
    assert "`failure`" in rendered
