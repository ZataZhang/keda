"""全局配置首次播种（seed）的回归测试。

首次创建 ``<state home>/config.toml`` 时写入的必须是**代码内的最小骨架**，
而不是仓库根的 ``config.toml``：仓库模板里的示例预设与阶段绑定一旦被播种给
所有机器，runner 就会在从未配置过的机器上指向本机根本没装的 agent。已有文件
永不覆盖，两个首次启动者之间也不能互相截断。
"""

from __future__ import annotations

import contextlib
import tomllib
from pathlib import Path

import pytest

from backend.core.shared.models import product_identity
from backend.infrastructure.config import settings_sources


@pytest.fixture()
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把状态目录换成临时目录，测试不碰真实的 ``~/.kedacode``。"""
    state_dir = tmp_path / ".kedacode"
    monkeypatch.setattr(product_identity, "state_home", lambda: state_dir)
    return state_dir


def test_seed_creates_a_parsable_skeleton_without_any_binding(state_home: Path) -> None:
    """骨架可解析、只含 ``[agent_runner]``，且没有任何生效的预设或阶段绑定。"""
    created = settings_sources._ensure_global_config_toml()
    assert created == state_home / "config.toml"

    seeded_text = created.read_text(encoding="utf-8")
    assert tomllib.loads(seeded_text) == {"agent_runner": {}}
    live_lines = [
        line.strip()
        for line in seeded_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert live_lines == ["[agent_runner]"]


def test_existing_global_config_is_never_overwritten(state_home: Path) -> None:
    """已有文件逐字节保留：seed 不是"修复"用户配置的入口。"""
    config_path = state_home / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    user_text = (
        "[agent_runner]\n"
        '[agent_runner.presets.fast]\nagent = "claude"\nmodel = "opus"\n\n'
        '[agent_runner.lifecycle_agents]\nimplementation = "claude"\n'
    )
    config_path.write_text(user_text, encoding="utf-8")

    returned = settings_sources._ensure_global_config_toml()
    assert returned == config_path
    assert config_path.read_text(encoding="utf-8") == user_text


def test_seed_is_idempotent_across_repeated_calls(state_home: Path) -> None:
    """重复调用不重写、不追加：同一台机器上的骨架稳定。"""
    first = settings_sources._ensure_global_config_toml()
    first_bytes = first.read_bytes()
    second = settings_sources._ensure_global_config_toml()
    assert second == first
    assert second.read_bytes() == first_bytes


def test_seed_rechecks_existence_inside_the_write_lock(
    state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁内重做存在性检查：后进锁的首次启动者不得截断先落盘的内容。"""
    config_path = state_home / "config.toml"
    real_lock = settings_sources.config_write_lock

    @contextlib.contextmanager
    def racing_lock(path: Path | str):
        with real_lock(path):
            # 模拟等锁期间另一个首次启动者已经写完 seed，并且用户往里加了内容。
            if not config_path.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text('[agent_runner]\nregistry = "kept"\n', encoding="utf-8")
            yield

    monkeypatch.setattr(settings_sources, "config_write_lock", racing_lock)
    returned = settings_sources._ensure_global_config_toml()
    assert returned == config_path
    assert 'registry = "kept"' in config_path.read_text(encoding="utf-8")
