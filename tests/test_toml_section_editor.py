"""保留式 TOML 段编辑器的落盘语义测试。

事实源是**磁盘文本**：断言"只动点名的键 / 段"、no-op 写回不产生文件抖动、
数组表原地改写保住位置与注释。生命周期预设 / 绑定 / 回退候选的写回都走本模块。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.infrastructure.config.toml_section_editor import (
    update_toml_array_of_tables,
    update_toml_table_keys,
)

_RUNNER_TABLE = ("agent_runner", "runner")
_CANDIDATES_KEY = "agent_fallback_candidates"

_CONFIG_WITH_ANNOTATED_CANDIDATES = """\
[agent_runner]

[agent_runner.runner]
max_agent_switches = 2

# 段前手写注释：候选链原地改写须保住它
[[agent_runner.runner.agent_fallback_candidates]]
agent = "claude"
[[agent_runner.runner.agent_fallback_candidates]]
agent = "kimi"

[agent_runner.runner.unrelated]
keep = true
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """写一份带注释与后续子表的 config.toml，作为落盘断言的起点。"""
    path = tmp_path / "config.toml"
    path.write_text(_CONFIG_WITH_ANNOTATED_CANDIDATES, encoding="utf-8")
    return path


def test_same_value_write_is_no_op(config_path: Path) -> None:
    """同值写回不落盘：共写但没变的键不产生文件抖动。"""
    before = config_path.read_bytes()
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 2})
    assert config_path.read_bytes() == before

    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 1})
    assert b"max_agent_switches = 1" in config_path.read_bytes()


def test_delete_of_absent_key_never_creates_file(tmp_path: Path) -> None:
    """目标键本就不存在的纯删除不落盘，也不凭空建文件。"""
    missing_path = tmp_path / "absent.toml"
    update_toml_table_keys(missing_path, _RUNNER_TABLE, {"max_agent_switches": None})
    assert not missing_path.exists()


def test_array_table_rewrite_keeps_position_and_comments(config_path: Path) -> None:
    """数组表原地改写：保住段前注释与其后的子表顺序，重复提交同值不落盘。"""
    before = config_path.read_bytes()
    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "claude", "preset": None}, {"agent": "kimi", "preset": None}],
    )
    assert config_path.read_bytes() == before

    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "codex", "preset": "fast"}, {"agent": "kimi", "preset": None}],
    )
    written = config_path.read_text(encoding="utf-8")
    assert "# 段前手写注释：候选链原地改写须保住它" in written
    assert written.index("[[agent_runner.runner.agent_fallback_candidates]]") < written.index(
        "[agent_runner.runner.unrelated]"
    )
    assert 'agent = "codex"' in written
    assert 'preset = "fast"' in written


def test_array_table_grows_and_shrinks_in_order(config_path: Path) -> None:
    """候选数量变化按期望顺序收口：追加补条目、多余弹出、None 字段删键。"""
    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [
            {"agent": "claude", "preset": None},
            {"agent": "kimi", "preset": None},
            {"agent": "codex", "preset": "slow"},
        ],
    )
    grown = config_path.read_text(encoding="utf-8")
    assert grown.count("[[agent_runner.runner.agent_fallback_candidates]]") == 3
    assert 'agent = "codex"' in grown

    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "claude", "preset": None}],
    )
    shrunk = config_path.read_text(encoding="utf-8")
    assert shrunk.count("[[agent_runner.runner.agent_fallback_candidates]]") == 1
    assert 'agent = "kimi"' not in shrunk
    assert 'agent = "codex"' not in shrunk
    assert "preset" not in shrunk.split("[[agent_runner")[1]


def test_empty_entries_removes_the_array_table_key(config_path: Path) -> None:
    """空列表清空该键，其余内容（含注释与后续子表）不动。"""
    update_toml_array_of_tables(config_path, _RUNNER_TABLE, _CANDIDATES_KEY, [])
    written = config_path.read_text(encoding="utf-8")
    assert "[[agent_runner.runner.agent_fallback_candidates]]" not in written
    assert 'agent = "claude"' not in written
    assert 'agent = "kimi"' not in written
    assert "[agent_runner.runner.unrelated]" in written
