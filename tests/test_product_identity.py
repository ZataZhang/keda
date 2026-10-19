"""KedaCode 产品身份与当前路径解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.core.shared.models import product_identity as identity
from backend.infrastructure.config import settings_sources


def test_state_home_always_uses_current_directory(tmp_path: Path) -> None:
    """旧目录存在时也只返回当前状态目录。"""
    (tmp_path / ".iar").mkdir()

    assert identity.state_home(tmp_path) == tmp_path / ".kedacode"


def test_read_product_env_uses_only_current_prefix() -> None:
    """环境变量只读取 KEDACODE 前缀，旧前缀不会触发回退。"""
    assert identity.read_product_env_value("CONFIG", {"IAR_CONFIG": "/tmp/old.toml"}) is None
    assert (
        identity.read_product_env_value("CONFIG", {"KEDACODE_CONFIG": " /tmp/current.toml "})
        == "/tmp/current.toml"
    )
    assert identity.read_product_env_value("CONFIG", {"KEDACODE_CONFIG": "  "}) is None


def test_build_child_env_writes_only_current_names() -> None:
    child_env = identity.build_child_env({"REPO_ID": "demo", "CONFIG": "/tmp/c.toml"})

    assert child_env == {
        "KEDACODE_REPO_ID": "demo",
        "KEDACODE_CONFIG": "/tmp/c.toml",
    }


def test_repository_config_uses_only_current_filename(tmp_path: Path) -> None:
    """旧文件单独存在时不作为当前仓库配置。"""
    (tmp_path / ".iar.toml").write_text("", encoding="utf-8")

    assert identity.effective_repository_config_path(tmp_path) == tmp_path / ".kedacode.toml"
    assert not identity.has_repository_config(tmp_path)

    (tmp_path / ".kedacode.toml").write_text("", encoding="utf-8")
    assert identity.has_repository_config(tmp_path)


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        ("kc", True),
        ("/opt/bin/kedacode", True),
        ("KEDACODE.EXE", True),
        ("iar", False),
        ("/opt/bin/iar.exe", False),
        ("git", False),
        (None, False),
    ],
)
def test_is_own_command_name(command_name: str | None, expected: bool) -> None:
    assert identity.is_own_command_name(command_name) is expected


def test_resolve_own_command_argv_uses_current_names_only() -> None:
    looked_up_names: list[str] = []

    def _fake_which(command_name: str) -> str | None:
        looked_up_names.append(command_name)
        return {"kedacode": "/opt/bin/kedacode"}.get(command_name)

    assert identity.resolve_own_command_argv("python", which_command=_fake_which) == [
        "/opt/bin/kedacode"
    ]
    assert looked_up_names == ["kc", "kedacode"]
    assert identity.resolve_own_command_argv("/opt/bin/kc", which_command=_fake_which) == [
        "/opt/bin/kc"
    ]


@pytest.mark.parametrize(
    ("raw_path_text", "expected_suffix"),
    [
        ("~/.kedacode/console.db", "console.db"),
        ("$HOME/.kedacode/processes.json", "processes.json"),
        ("${HOME}/.kedacode/container-auth/gh/config.yml", "container-auth/gh/config.yml"),
        ("~/.iar/console.db", None),
        ("~/.iardir/console.db", None),
    ],
)
def test_normalize_state_path_only_maps_current_directory(
    tmp_path: Path,
    raw_path_text: str,
    expected_suffix: str | None,
) -> None:
    effective_state_path = tmp_path / "state"
    normalized_path = identity.normalize_state_path(
        raw_path_text,
        home_path=tmp_path,
        state_home_path=effective_state_path,
    )

    if expected_suffix is None:
        assert normalized_path == Path(raw_path_text)
    else:
        assert normalized_path == effective_state_path.joinpath(*expected_suffix.split("/"))


def test_identity_literals_are_defined_only_here() -> None:
    assert identity.PRIMARY_COMMAND_NAME == "kc"
    assert identity.PRODUCT_DISPLAY_NAME == "KedaCode"
    assert identity.OWN_COMMAND_NAMES == ("kc", "kedacode")
    assert identity.STATE_DIR_NAME == ".kedacode"
    assert identity.REPOSITORY_CONFIG_FILENAME == ".kedacode.toml"
    assert identity.ENV_PREFIX == "KEDACODE_"
    assert identity.OPERATOR_SKILL_NAME == "kedacode-operator"
    assert identity.OUTPUT_PROTOCOL_ENTRY_POINT_GROUP == "kedacode.agent_output_protocols"
    assert identity.COMPLETION_ENV_VAR_NAME == "_KC_COMPLETE"


def test_registry_config_seeds_state_home_without_source_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(settings_sources, "_PROJECT_ROOT_PATH", tmp_path / "installed-env")

    resolved_path = settings_sources.resolve_registry_config_toml_path()

    expected_path = fake_home / identity.STATE_DIR_NAME / "config.toml"
    assert resolved_path == expected_path
    assert resolved_path.is_file()
    assert settings_sources._is_product_config_toml(resolved_path) is True  # noqa: SLF001


def test_registry_config_ignores_legacy_state_home_without_source_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".iar").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(settings_sources, "_PROJECT_ROOT_PATH", tmp_path / "installed-env")

    resolved_path = settings_sources.resolve_registry_config_toml_path()

    assert resolved_path == fake_home / identity.STATE_DIR_NAME / "config.toml"
    assert resolved_path.is_file()
    assert not (fake_home / ".iar" / "config.toml").exists()
