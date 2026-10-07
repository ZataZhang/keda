"""产品身份模块（``product_identity``）的双读解析契约单元测试。

覆盖 PRD §7.6 rv-4 / rv-5 的判定核心：状态目录五种情况、环境变量四种情况、
仓库配置三种情况、自有命令名判定、自我调用顺序、状态路径归一化、子进程双注入。
守护的规范见 ``docs/guides/migrating-from-iar.md``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from backend.core.shared.models import product_identity as identity
from backend.infrastructure.config import settings_sources


@pytest.fixture(autouse=True)
def _clear_notice_dedup_ledger() -> None:
    """每条用例从空的提示去重台账开始，避免跨用例串扰。"""
    identity._reset_emitted_notices()  # noqa: SLF001


def _make_legacy_only_home(home_path: Path) -> Path:
    legacy_state_path = home_path / identity.LEGACY_STATE_DIR_NAME
    legacy_state_path.mkdir(parents=True)
    return legacy_state_path


def test_resolve_state_home_returns_new_dir_when_only_new_exists(tmp_path: Path) -> None:
    """只有新目录时用新目录且不出声。"""
    (tmp_path / identity.STATE_DIR_NAME).mkdir()

    resolution = identity.resolve_state_home(tmp_path)

    assert resolution.path == tmp_path / identity.STATE_DIR_NAME
    assert resolution.source == "new"
    assert resolution.notice is None


def test_resolve_state_home_is_silent_when_legacy_links_to_new_dir(tmp_path: Path) -> None:
    """迁移后旧路径成为指向新目录的链接：判为同一目录，静默使用新目录。"""
    new_state_path = tmp_path / identity.STATE_DIR_NAME
    new_state_path.mkdir()
    (tmp_path / identity.LEGACY_STATE_DIR_NAME).symlink_to(Path(identity.STATE_DIR_NAME))

    resolution = identity.resolve_state_home(tmp_path)

    assert resolution.path == new_state_path
    assert resolution.source == "new"
    assert resolution.notice is None


def test_resolve_state_home_prefers_new_dir_and_warns_on_two_real_dirs(tmp_path: Path) -> None:
    """两个独立真实目录并存：用新目录并警告旧目录被忽略，不自动合并。"""
    (tmp_path / identity.STATE_DIR_NAME).mkdir()
    (tmp_path / identity.LEGACY_STATE_DIR_NAME).mkdir()

    resolution = identity.resolve_state_home(tmp_path)

    assert resolution.path == tmp_path / identity.STATE_DIR_NAME
    assert resolution.source == "both"
    assert resolution.notice is not None
    assert "warning" in resolution.notice
    assert "kc config migrate" in resolution.notice


def test_resolve_state_home_keeps_legacy_dir_with_one_notice(tmp_path: Path) -> None:
    """只有旧目录：沿用旧目录、提示迁移，且不创建新目录。"""
    legacy_state_path = _make_legacy_only_home(tmp_path)

    resolution = identity.resolve_state_home(tmp_path)

    assert resolution.path == legacy_state_path
    assert resolution.source == "legacy"
    assert resolution.notice is not None
    assert "kc config migrate" in resolution.notice
    assert not (tmp_path / identity.STATE_DIR_NAME).exists()


def test_resolve_state_home_defers_to_new_dir_on_a_fresh_machine(tmp_path: Path) -> None:
    """全新机器：解析到新目录但不落盘，由调用方首次写入时创建。"""
    resolution = identity.resolve_state_home(tmp_path)

    assert resolution.path == tmp_path / identity.STATE_DIR_NAME
    assert resolution.source == "absent"
    assert resolution.notice is None
    assert not resolution.path.exists()


def test_state_home_emits_notice_only_once_per_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``state_home()`` 的迁移提示在同一进程内只写一次 stderr。"""
    _make_legacy_only_home(tmp_path)

    identity.state_home(tmp_path)
    identity.state_home(tmp_path)

    captured = capsys.readouterr()
    assert captured.err.count("kc config migrate") == 1
    assert captured.out == ""


def test_read_product_env_prefers_new_name_silently() -> None:
    """只有新名时直接采用，不出声。"""
    env_value = identity.read_product_env(
        {"KEDACODE_CONFIG": "/tmp/new.toml"},
        "CONFIG",
    )

    assert env_value.value == "/tmp/new.toml"
    assert env_value.source == "new"
    assert env_value.notice is None


def test_read_product_env_falls_back_to_legacy_name_with_notice() -> None:
    """只有旧名时沿用旧值并提示一次，两个名字都点名。"""
    env_value = identity.read_product_env({"IAR_CONFIG": "/tmp/old.toml"}, "CONFIG")

    assert env_value.value == "/tmp/old.toml"
    assert env_value.source == "legacy"
    assert env_value.notice is not None
    assert "IAR_CONFIG" in env_value.notice
    assert "KEDACODE_CONFIG" in env_value.notice


def test_read_product_env_warns_on_conflicting_values() -> None:
    """新旧都设且取值不同：新名生效并警告一次。"""
    env_value = identity.read_product_env(
        {"IAR_CONFIG": "/tmp/old.toml", "KEDACODE_CONFIG": "/tmp/new.toml"},
        "CONFIG",
    )

    assert env_value.value == "/tmp/new.toml"
    assert env_value.source == "conflict"
    assert env_value.notice is not None
    assert "warning" in env_value.notice


def test_conflicting_secret_notice_never_exposes_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """密钥新旧名冲突时只提示变量名，不把任一密钥写入输出。"""
    secret_value = identity.read_product_env_value(
        "IDEA_INBOX_INBOUND_SECRET",
        {
            "IAR_IDEA_INBOX_INBOUND_SECRET": "legacy-secret-sensitive",
            "KEDACODE_IDEA_INBOX_INBOUND_SECRET": "new-secret-sensitive",
        },
    )

    captured = capsys.readouterr()
    assert secret_value == "new-secret-sensitive"
    assert captured.out == ""
    assert "KEDACODE_IDEA_INBOX_INBOUND_SECRET" in captured.err
    assert "legacy-secret-sensitive" not in captured.err
    assert "new-secret-sensitive" not in captured.err


def test_read_product_env_is_silent_when_both_names_agree() -> None:
    """新旧都设且取值相同：静默采用。"""
    env_value = identity.read_product_env(
        {"IAR_CONFIG": "/tmp/same.toml", "KEDACODE_CONFIG": "/tmp/same.toml"},
        "CONFIG",
    )

    assert env_value.value == "/tmp/same.toml"
    assert env_value.source == "new"
    assert env_value.notice is None


def test_read_product_env_reports_unset_without_default() -> None:
    """两个名字都没设：返回 ``None``，由调用方决定是否走默认值。"""
    env_value = identity.read_product_env({}, "CONFIG")

    assert env_value.value is None
    assert env_value.source == "unset"


def test_read_product_env_value_emits_to_stderr_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """提示只走 stderr，stdout 永远干净（机器可读输出不得夹带提示）。"""
    value = identity.read_product_env_value("SKILLS_DIR", {"IAR_SKILLS_DIR": str(tmp_path)})

    captured = capsys.readouterr()
    assert value == str(tmp_path)
    assert captured.out == ""
    assert "IAR_SKILLS_DIR" in captured.err


def test_build_child_env_aliases_writes_both_names() -> None:
    """派生子进程同时拿到新旧两个变量名。"""
    alias_env = identity.build_child_env_aliases({"REPO_ID": "demo", "CONFIG": "/tmp/c.toml"})

    assert alias_env == {
        "KEDACODE_REPO_ID": "demo",
        "IAR_REPO_ID": "demo",
        "KEDACODE_CONFIG": "/tmp/c.toml",
        "IAR_CONFIG": "/tmp/c.toml",
    }


def test_repository_config_resolution_cases(tmp_path: Path) -> None:
    """仓库配置三种情况：新文件优先并存警告、只有旧文件静默、都没有时指向新文件名。"""
    new_only_root = tmp_path / "new-only"
    new_only_root.mkdir()
    (new_only_root / identity.REPOSITORY_CONFIG_FILENAME).write_text("", encoding="utf-8")
    legacy_only_root = tmp_path / "legacy-only"
    legacy_only_root.mkdir()
    (legacy_only_root / identity.LEGACY_REPOSITORY_CONFIG_FILENAME).write_text("", encoding="utf-8")
    both_root = tmp_path / "both"
    both_root.mkdir()
    (both_root / identity.REPOSITORY_CONFIG_FILENAME).write_text("", encoding="utf-8")
    (both_root / identity.LEGACY_REPOSITORY_CONFIG_FILENAME).write_text("", encoding="utf-8")
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    new_resolution = identity.resolve_repository_config_path(new_only_root)
    legacy_resolution = identity.resolve_repository_config_path(legacy_only_root)
    both_resolution = identity.resolve_repository_config_path(both_root)
    empty_resolution = identity.resolve_repository_config_path(empty_root)

    assert new_resolution.path.name == identity.REPOSITORY_CONFIG_FILENAME
    assert new_resolution.exists and new_resolution.notice is None
    assert legacy_resolution.path.name == identity.LEGACY_REPOSITORY_CONFIG_FILENAME
    assert legacy_resolution.exists and legacy_resolution.notice is None
    assert both_resolution.path.name == identity.REPOSITORY_CONFIG_FILENAME
    assert both_resolution.notice is not None and "warning" in both_resolution.notice
    assert not empty_resolution.exists
    assert empty_resolution.path.name == identity.REPOSITORY_CONFIG_FILENAME
    assert identity.has_repository_config(legacy_only_root)
    assert not identity.has_repository_config(empty_root)


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        ("kc", True),
        ("kedacode", True),
        ("iar", True),
        ("IAR", True),
        ("/usr/local/bin/iar", True),
        ("iar.exe", True),
        ("kedacode.cmd", True),
        (None, False),
        ("", False),
        ("kubectl", False),
        ("kc-agent", False),
        (".iar", False),
    ],
)
def test_is_own_command_name(command_name: str | None, expected: bool) -> None:
    """自有命令名判定：basename 去扩展名、大小写不敏感。"""
    assert identity.is_own_command_name(command_name) is expected


def test_resolve_own_command_argv_prefers_current_argv0() -> None:
    """以自有名字启动时，自我调用就用它（保证与安装态同源）。"""
    argv = identity.resolve_own_command_argv(
        "/Users/dev/.local/bin/iar", which_command=lambda _: None
    )

    assert argv == ["/Users/dev/.local/bin/iar"]


def test_resolve_own_command_argv_follows_new_to_legacy_which_order() -> None:
    """argv0 不是自有名字时，按 kc → kedacode → iar 查 PATH。"""

    def _fake_which(command_name: str) -> str | None:
        return {"kedacode": "/opt/bin/kedacode", "iar": "/opt/bin/iar"}.get(command_name)

    assert identity.resolve_own_command_argv("python", which_command=_fake_which) == [
        "/opt/bin/kedacode"
    ]
    assert identity.resolve_own_command_argv("uvicorn", which_command=lambda _: None) == [
        "uv",
        "run",
        "kc",
    ]


@pytest.mark.parametrize(
    ("raw_path_text", "expected_suffix"),
    [
        ("~/.iar/console.db", "console.db"),
        ("~/.kedacode/console.db", "console.db"),
        ("$HOME/.iar/processes.json", "processes.json"),
        ("${HOME}/.iar/processes.json", "processes.json"),
        ("~/.iar/container-auth/gh/config.yml", "container-auth/gh/config.yml"),
        ("~/.iardir/console.db", None),
    ],
)
def test_normalize_state_path_maps_every_prefix_form(
    tmp_path: Path,
    raw_path_text: str,
    expected_suffix: str | None,
) -> None:
    """配置里写死的各种状态目录前缀形态都归一化到当前生效目录；相似目录名不误伤。"""
    effective_state_path = tmp_path / "state"
    normalized_path = identity.normalize_state_path(
        raw_path_text,
        home_path=tmp_path,
        state_home_path=effective_state_path,
    )

    if expected_suffix is None:
        assert normalized_path == Path(raw_path_text)
        return
    assert normalized_path == effective_state_path.joinpath(*expected_suffix.split("/"))


def test_normalize_state_path_keeps_unrelated_paths(tmp_path: Path) -> None:
    """不含状态目录前缀的路径原样返回（仓库工作树路径等）。"""
    unrelated_path_text = str(tmp_path / "work" / ".iar-worktrees" / "issue-1")

    normalized_path = identity.normalize_state_path(
        unrelated_path_text,
        home_path=tmp_path,
        state_home_path=tmp_path / "state",
    )

    assert normalized_path == Path(unrelated_path_text)


def test_normalize_state_path_uses_home_absolute_form(tmp_path: Path) -> None:
    """家目录绝对路径形态同样归一化。"""
    absolute_legacy_text = os.path.join(str(tmp_path), identity.LEGACY_STATE_DIR_NAME, "logs")
    normalized_path = identity.normalize_state_path(
        absolute_legacy_text,
        home_path=tmp_path,
        state_home_path=tmp_path / "state",
    )

    assert normalized_path == tmp_path / "state" / "logs"


def test_identity_literals_are_defined_only_here() -> None:
    """新派生名的字面量唯一事实源在本模块（PRD §7.4 单点定义检索的单元层镜像）。"""
    assert identity.PRIMARY_COMMAND_NAME == "kc"
    assert identity.PRODUCT_DISPLAY_NAME == "KedaCode"
    assert identity.STATE_DIR_NAME == ".kedacode"
    assert identity.REPOSITORY_CONFIG_FILENAME == ".kedacode.toml"
    assert identity.ENV_PREFIX == "KEDACODE_"
    assert identity.OPERATOR_SKILL_NAME == "kedacode-operator"
    assert identity.OUTPUT_PROTOCOL_ENTRY_POINT_GROUP == "kedacode.agent_output_protocols"
    assert identity.COMPLETION_ENV_VAR_NAME == "_IAR_COMPLETE"


def test_registry_config_seeds_state_home_without_source_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全局安装的 kc 没有源码模板时，registry 配置落在新状态目录里。

    ``uv tool install kedacode`` 之后在任意目录首次执行 registry 命令：状态目录里还没有
    ``config.toml``，解析也找不到 keda 源码根的模板。此前解析结果指向一个不存在的模板路径，
    命令以 ``FileNotFoundError``（退出码 3）失败。
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(settings_sources, "_PROJECT_ROOT_PATH", tmp_path / "installed-env")

    resolved_path = settings_sources.resolve_registry_config_toml_path()

    expected_path = fake_home / identity.STATE_DIR_NAME / "config.toml"
    assert resolved_path == expected_path
    assert resolved_path.is_file()
    assert settings_sources._is_product_config_toml(resolved_path) is True  # noqa: SLF001


def test_registry_config_seeds_legacy_state_home_without_source_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有旧状态目录的机器上，兜底载体写进旧目录，不新建 ``~/.kedacode``。"""
    legacy_state_path = _make_legacy_only_home(tmp_path / "home")
    monkeypatch.setenv("HOME", str(legacy_state_path.parent))
    monkeypatch.setattr(settings_sources, "_PROJECT_ROOT_PATH", tmp_path / "installed-env")

    resolved_path = settings_sources.resolve_registry_config_toml_path()

    assert resolved_path == legacy_state_path / "config.toml"
    assert resolved_path.is_file()
    assert not (legacy_state_path.parent / identity.STATE_DIR_NAME).exists()
