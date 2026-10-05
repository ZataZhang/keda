"""Tests for the shared cross-agent fallback candidate builder."""

from __future__ import annotations

from backend.core.shared.models.agent_runner import AppConfig, RunnerConfig
from backend.core.use_cases.agent_candidate_fallback import build_agent_candidates


def _config(
    order: tuple[str, ...] = ("claude", "kimi", "codex"),
    max_switches: int = 2,
) -> AppConfig:
    return AppConfig(
        runner=RunnerConfig(agent_fallback_order=order, max_agent_switches=max_switches)
    )


def test_build_agent_candidates_excludes_builder_and_caps_switches() -> None:
    """候选 = 首选 + fallback 链里 ≠ builder 的 agent，按 max_agent_switches 封顶。"""
    candidates = build_agent_candidates(_config(), "codex", exclude_agent="qoder")

    assert candidates == ("codex", "claude", "kimi")
    assert "qoder" not in candidates


def test_build_agent_candidates_keeps_primary_when_primary_is_excluded_agent() -> None:
    """首选 == exclude 时首选仍保留，只保证回退尾部不含 exclude（review allow_same_agent）。"""
    candidates = build_agent_candidates(
        _config(order=("claude", "kimi", "codex"), max_switches=2),
        "claude",
        exclude_agent="claude",
    )

    assert candidates[0] == "claude"
    assert candidates == ("claude", "kimi", "codex")


def test_build_agent_candidates_only_primary_without_fallback_order() -> None:
    """未配置回退链时只返回首选（单 agent 行为）。"""
    assert build_agent_candidates(_config(order=()), "claude") == ("claude",)


def test_build_agent_candidates_zero_switches_keeps_only_primary() -> None:
    """max_agent_switches=0 时不补充任何候选。"""
    assert build_agent_candidates(_config(max_switches=0), "claude") == ("claude",)


def test_build_agent_candidates_dedupes_primary_and_skips_blank_entries() -> None:
    """首选在链中重复、或链中有空白项时都要跳过。"""
    candidates = build_agent_candidates(
        _config(order=("claude", "  ", "kimi", "claude"), max_switches=5),
        "claude",
    )

    assert candidates == ("claude", "kimi")
