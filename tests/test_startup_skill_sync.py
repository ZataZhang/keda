"""kc 启动随包 operator Skill 自动同步的行为测试。

覆盖三层：

- 跳过矩阵：机器模式 / 补全协议 / ``--help`` / 非 TTY / ``NO_UPDATE_CHECK`` /
  专用 ``NO_SKILL_SYNC`` 关闭时，同步整段不发生（不调用安装、不写缓存）。
- 版本门控：无缓存时同步并记录当前包版本；缓存版本与当前一致时跳过；版本变化后
  再次同步。
- 呈现与容错：仅当有 ``install`` / ``refresh`` / ``overwrite`` 动作时提示；全
  ``up-to-date`` 静默；安装内部抛异常时钩子不向外传播，也不写缓存。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api import cli_skill
from backend.api import cli_startup_skill_sync
from backend.api.cli_startup_skill_sync import startup_skill_sync
from backend.api.version_info import _UNKNOWN_VERSION
from backend.core.use_cases.agent_runner_init_assets import PackagedSkillInstallResult


def _result(action: str) -> PackagedSkillInstallResult:
    """构造一条随包 operator Skill 安装结果，供钩子测试断言呈现分支。"""
    return PackagedSkillInstallResult(
        target_path=Path("/tmp/skills/kedacode-operator"),
        action=action,
        dry_run=False,
    )


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 HOME 指向空目录，让 ``<state_home>/skill-sync.json`` 落在可丢弃的 tmp 里。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    for env_name in ("KEDACODE_SKILLS_DIR", "KEDACODE_NO_UPDATE_CHECK", "KEDACODE_NO_SKILL_SYNC"):
        monkeypatch.delenv(env_name, raising=False)
    return tmp_path


@pytest.fixture()
def _gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """放行同步闸门（视为交互式、非机器路径），隔离版本门控与呈现逻辑。"""
    monkeypatch.setattr(
        cli_startup_skill_sync, "_should_skip_startup_update_check", lambda *a, **k: False
    )
    monkeypatch.setattr(cli_startup_skill_sync, "resolve_keda_version", lambda: "0.9.0")


@pytest.fixture()
def _spy_sync(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """把 sync_packaged_operator_skills 打桩，记录调用次数并允许编排下次返回结果。"""
    state = SimpleNamespace(calls=0, next_results=[_result("refresh")])

    def _fake_sync(**_kwargs: object) -> list[PackagedSkillInstallResult]:
        state.calls += 1
        return state.next_results

    monkeypatch.setattr(cli_startup_skill_sync, "sync_packaged_operator_skills", _fake_sync)
    return state


def test_machine_and_disabled_paths_skip_sync(
    _spy_sync: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复用更新检查跳过矩阵：任一抑制条件成立时既不安装也不写缓存。"""
    monkeypatch.setattr(
        cli_startup_skill_sync, "_should_skip_startup_update_check", lambda *a, **k: True
    )
    startup_skill_sync(["run"])
    assert _spy_sync.calls == 0
    assert not cli_startup_skill_sync._cache_path().exists()


def test_no_skill_sync_env_disables(_spy_sync: SimpleNamespace, _gate_open: None) -> None:
    """``KEDACODE_NO_SKILL_SYNC=1`` 是专用关闭开关，优先于版本门控。"""
    os.environ["KEDACODE_NO_SKILL_SYNC"] = "1"
    try:
        startup_skill_sync(["logs"])
    finally:
        os.environ.pop("KEDACODE_NO_SKILL_SYNC", None)
    assert _spy_sync.calls == 0


def test_sync_records_version_and_prints_notice(
    _spy_sync: SimpleNamespace, _gate_open: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """首次（无缓存）同步执行、记录当前版本，并在有改动动作时提示。"""
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 1
    assert cli_startup_skill_sync._read_cached_synced_version() == "0.9.0"
    assert "kc skill install" in capsys.readouterr().err


def test_same_version_skips_second_run(_spy_sync: SimpleNamespace, _gate_open: None) -> None:
    """缓存版本与当前包版本一致时不再重复触碰文件系统。"""
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 1
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 1


def test_version_change_re_triggers_sync(
    _spy_sync: SimpleNamespace, _gate_open: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包版本变化（升级后）让旧缓存失效，重新同步。"""
    startup_skill_sync(["logs"])
    monkeypatch.setattr(cli_startup_skill_sync, "resolve_keda_version", lambda: "0.9.1")
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 2
    assert cli_startup_skill_sync._read_cached_synced_version() == "0.9.1"


def test_unknown_version_skips(_spy_sync: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """源码检出等无法解析版本时不写缓存、不同步。"""
    monkeypatch.setattr(
        cli_startup_skill_sync, "_should_skip_startup_update_check", lambda *a, **k: False
    )
    monkeypatch.setattr(cli_startup_skill_sync, "resolve_keda_version", lambda: _UNKNOWN_VERSION)
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 0


def test_up_to_date_result_is_silent(
    _spy_sync: SimpleNamespace, _gate_open: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """全部安装根都已 ``up-to-date`` 时静默，不打扰用户。"""
    _spy_sync.next_results = [_result("up-to-date")]
    startup_skill_sync(["logs"])
    assert _spy_sync.calls == 1
    assert "kc skill install" not in capsys.readouterr().err


def test_sync_exception_is_swallowed_and_not_cached(
    _spy_sync: SimpleNamespace, _gate_open: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安装内部抛异常时钩子不向外传播，也不写缓存（下次仍会尝试）。"""

    def _boom(**_kwargs: object) -> list[PackagedSkillInstallResult]:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(cli_startup_skill_sync, "sync_packaged_operator_skills", _boom)
    startup_skill_sync(["logs"])  # 不得抛出
    assert cli_startup_skill_sync._read_cached_synced_version() is None


def test_packaged_sync_targets_env_skills_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api 编排真实落盘：用 ``KEDACODE_SKILLS_DIR`` 把随包 operator Skill 装进临时根。"""
    skills_root = tmp_path / "skills"
    monkeypatch.setenv("KEDACODE_SKILLS_DIR", str(skills_root))
    results = cli_skill.sync_packaged_operator_skills()
    assert [result.action for result in results] == ["install"]
    assert (skills_root / "kedacode-operator" / "SKILL.md").is_file()
