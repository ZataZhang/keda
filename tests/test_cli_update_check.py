"""CLI 启动更新检查（Issue #264）的行为测试。

覆盖三层：

- 跳过矩阵：机器模式 / 补全协议 / --help / --version / 非 TTY / 显式关闭，
  更新检查必须整段不发生。
- 检查与缓存：TTL 内不访问 PyPI、离线静默并记录时间戳、缓存绑定已安装版本、
  PEP 440 比较（含预发布不算更新）。
- 询问与升级：确认后执行识别出的安装方式命令；拒绝或识别不出时只给可复制命令；
  任何内部异常都不影响 ``main()`` 的退出码。
"""

from __future__ import annotations

import json
import site
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.api import cli_update_check
from backend.api.cli_update_check import (
    UpdateNotice,
    UpgradeCommand,
    _should_skip_startup_update_check,
    check_for_update,
    detect_upgrade_command,
    fetch_latest_pypi_version,
    startup_update_check,
)

_COMPLETE_ENV = "_IAR_COMPLETE"

_INTERACTIVE_ENVIRON: dict[str, str] = {}


class _Tty:
    """isatty 恒真的流替身。"""

    def isatty(self) -> bool:
        """始终判定为交互式终端。"""
        return True


class _Pipe:
    """isatty 恒假的流替身（管道 / CI）。"""

    def isatty(self) -> bool:
        """始终判定为非交互。"""
        return False


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 HOME 指向空目录，让状态目录与缓存落在可丢弃的 tmp 里。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def _always_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让交互判定为真，好单独测其余闸门。"""
    monkeypatch.setattr(cli_update_check, "_is_interactive_terminal", lambda *a, **k: True)


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 stdin/stderr 双双换成 TTY 替身。"""
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stderr", _Tty())


def _make_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 stdin/stderr 换成管道替身（等价 CI / 非交互执行）。"""
    monkeypatch.setattr(sys, "stdin", _Pipe())
    monkeypatch.setattr(sys, "stderr", _Pipe())


def _cache_file(home: Path) -> Path:
    """当前测试 HOME 下的缓存文件路径。"""
    return home / ".kedacode" / "update-check.json"


def _write_cache(home: Path, record: dict[str, Any]) -> None:
    """直接落一份缓存记录，模拟上次检查的结论。"""
    cache_path = _cache_file(home)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(record), encoding="utf-8")


# ---------------------------------------------------------------------------
# 跳过矩阵：机器路径必须整段不发生
# ---------------------------------------------------------------------------


def test_skip_in_machine_output_mode() -> None:
    """--json / --output json 属机器路径：不检查、不提示。"""
    assert _should_skip_startup_update_check(["logs", "--json"], environ=_INTERACTIVE_ENVIRON)
    assert _should_skip_startup_update_check(
        ["schema", "--output", "json"], environ=_INTERACTIVE_ENVIRON
    )


def test_skip_during_completion_protocol(_always_tty: None) -> None:
    """补全协议（_IAR_COMPLETE 存在）不打断补全输出。"""
    assert _should_skip_startup_update_check([], environ={_COMPLETE_ENV: "kc complete"})


def test_skip_on_help_and_version(_always_tty: None) -> None:
    """帮助与版本查询是只读路径，不触发检查。"""
    assert _should_skip_startup_update_check(["--help"], environ=_INTERACTIVE_ENVIRON)
    assert _should_skip_startup_update_check(
        ["issue", "create", "-h"], environ=_INTERACTIVE_ENVIRON
    )
    assert _should_skip_startup_update_check(["--version"], environ=_INTERACTIVE_ENVIRON)


def test_skip_when_env_disabled_both_names(_always_tty: None) -> None:
    """KEDACODE_NO_UPDATE_CHECK / 旧名 IAR_NO_UPDATE_CHECK 都能显式关闭。"""
    assert _should_skip_startup_update_check(
        ["registry", "list"],
        environ={"KEDACODE_NO_UPDATE_CHECK": "1"},
    )
    assert _should_skip_startup_update_check(
        ["registry", "list"], environ={"IAR_NO_UPDATE_CHECK": "1"}
    )
    assert not _should_skip_startup_update_check(
        ["registry", "list"], environ={"KEDACODE_NO_UPDATE_CHECK": "0"}
    )


def test_skip_when_not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdin 或 stderr 不是 TTY 时不访问网络也不询问。"""
    _make_non_interactive(monkeypatch)
    assert _should_skip_startup_update_check(["registry", "list"], environ=_INTERACTIVE_ENVIRON)


def test_interactive_terminal_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """人坐在终端前（stdin + stderr 皆 TTY）才放行检查。"""
    _make_interactive(monkeypatch)
    assert not _should_skip_startup_update_check(["registry", "list"], environ=_INTERACTIVE_ENVIRON)


def test_stdout_pipe_still_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """提示走 stderr，因此 stdout 被管道接住时人仍然看得到问得到。"""
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stderr", _Tty())
    assert not _should_skip_startup_update_check(["registry", "list"], environ=_INTERACTIVE_ENVIRON)


def test_skip_on_bare_invocation(_always_tty: None) -> None:
    """无参数调用只打印用法帮助，同属帮助路径：不检查、不提示。"""
    assert _should_skip_startup_update_check([], environ=_INTERACTIVE_ENVIRON)


def test_startup_check_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """更新检查内部任何异常都被吞掉，不能掀翻真正的命令。"""
    monkeypatch.setattr(cli_update_check, "_is_interactive_terminal", lambda *a, **k: True)

    def _boom() -> UpdateNotice:
        raise RuntimeError("internal update-check failure")

    monkeypatch.setattr(cli_update_check, "check_for_update", _boom)
    assert startup_update_check(["registry", "list"]) is None


def test_startup_check_skips_before_touching_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过路径上连缓存与网络都不碰：check_for_update 一次都不该被调用。"""
    _make_non_interactive(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(cli_update_check, "check_for_update", lambda *a, **k: calls.append("check"))
    startup_update_check(["daemon", "start"])
    assert calls == []


# ---------------------------------------------------------------------------
# check_for_update：TTL 缓存、离线静默、PEP 440 比较
# ---------------------------------------------------------------------------


def test_fresh_cache_avoids_second_pypi_visit(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTL 内的缓存直接复用，不再访问 PyPI。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {"checked_at": 1_000.0, "current_version": "0.2.1", "latest_version": "9.9.9"},
    )

    def _forbidden_fetch() -> str | None:
        raise AssertionError("fresh cache must not hit PyPI")

    notice = check_for_update(now_epoch=1_500.0, fetch_latest=_forbidden_fetch)
    assert notice == UpdateNotice(current_version="0.2.1", latest_version="9.9.9")


def test_suppressed_cached_notice_is_not_returned(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """当前缓存周期内已处理的版本提示不会再次触发通知。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {
            "checked_at": 1_000.0,
            "current_version": "0.2.1",
            "latest_version": "9.9.9",
            "suppressed_version": "9.9.9",
        },
    )

    def _forbidden_fetch() -> str | None:
        raise AssertionError("fresh suppressed cache must not hit PyPI")

    assert check_for_update(now_epoch=1_500.0, fetch_latest=_forbidden_fetch) is None


def test_cache_uses_legacy_state_directory_when_it_is_the_only_one(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仅有旧状态目录时，更新检查缓存继续写入 ``~/.iar``。"""
    legacy_state_dir = _isolated_home / ".iar"
    legacy_state_dir.mkdir()
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")

    notice = check_for_update(now_epoch=1_500.0, fetch_latest=lambda: "0.5.0")

    assert notice == UpdateNotice(current_version="0.2.1", latest_version="0.5.0")
    assert (legacy_state_dir / "update-check.json").is_file()
    assert not (_isolated_home / ".kedacode").exists()


def test_cache_is_bound_to_installed_version(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """升级后已安装版本变化，旧缓存必须失效重查。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.3.0")
    _write_cache(
        _isolated_home,
        {"checked_at": 1_000.0, "current_version": "0.2.1", "latest_version": "9.9.9"},
    )
    fetched: list[bool] = []

    def _fetch() -> str | None:
        fetched.append(True)
        return "0.3.0"

    notice = check_for_update(now_epoch=1_500.0, fetch_latest=_fetch)
    assert fetched == [True]
    assert notice is None


def test_offline_fetch_is_silent_and_recorded(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网络失败返回 None（静默），且落盘时间戳，TTL 内不再重试轰炸 PyPI。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    attempts: list[bool] = []

    def _offline() -> str | None:
        attempts.append(True)
        return None

    assert check_for_update(now_epoch=2_000.0, fetch_latest=_offline) is None
    assert check_for_update(now_epoch=2_600.0, fetch_latest=_offline) is None
    assert attempts == [True]
    record = json.loads(_cache_file(_isolated_home).read_text(encoding="utf-8"))
    assert record["latest_version"] is None


def test_stale_cache_triggers_refetch_and_updates_cache(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 TTL 后重新访问 PyPI，并把新结论写回缓存。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {"checked_at": 0.0, "current_version": "0.2.1", "latest_version": "0.2.1"},
    )
    notice = check_for_update(now_epoch=25 * 3600.0, fetch_latest=lambda: "0.4.0")
    assert notice == UpdateNotice(current_version="0.2.1", latest_version="0.4.0")
    record = json.loads(_cache_file(_isolated_home).read_text(encoding="utf-8"))
    assert record["latest_version"] == "0.4.0"


def test_expired_notice_suppression_triggers_new_notice(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提示抑制随检查缓存过期；重新检查仍发现同一版本时会再次通知。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {
            "checked_at": 0.0,
            "current_version": "0.2.1",
            "latest_version": "0.4.0",
            "suppressed_version": "0.4.0",
        },
    )

    notice = check_for_update(now_epoch=25 * 3600.0, fetch_latest=lambda: "0.4.0")

    assert notice == UpdateNotice(current_version="0.2.1", latest_version="0.4.0")


def test_corrupt_cache_is_treated_as_absent(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存文件被手工改坏时按无缓存处理，重新检查而不是报错。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _cache_file(_isolated_home).parent.mkdir(parents=True, exist_ok=True)
    _cache_file(_isolated_home).write_text("not json", encoding="utf-8")
    notice = check_for_update(now_epoch=100.0, fetch_latest=lambda: "0.5.0")
    assert notice is not None and notice.latest_version == "0.5.0"


def test_cache_with_non_numeric_timestamp_is_treated_as_absent(
    _isolated_home: Path, _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON 合法但 ``checked_at`` 不是数字（手工改坏）时同样按无缓存处理。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {"checked_at": "not-a-number", "current_version": "0.2.1", "latest_version": "9.9.9"},
    )
    notice = check_for_update(now_epoch=1_500.0, fetch_latest=lambda: "0.5.0")
    assert notice == UpdateNotice(current_version="0.2.1", latest_version="0.5.0")


def test_uninstalled_source_tree_skips_check(
    _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拿不到发行版元数据（源码直跑）时不比对、不访问网络。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.0.0+unknown")

    def _forbidden() -> str | None:
        raise AssertionError("unknown version must not hit PyPI")

    assert check_for_update(now_epoch=1.0, fetch_latest=_forbidden) is None


def test_prerelease_behind_release_is_not_an_update(
    _always_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PEP 440 语义：已装 1.0.0 时 PyPI 上的 1.0.0rc2 不算新版本。"""
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "1.0.0")
    assert cli_update_check._is_newer("1.0.0rc2", "1.0.0") is False
    assert cli_update_check._is_newer("0.10.0", "0.9.0") is True
    assert cli_update_check._is_newer("not-a-version", "0.9.0") is False


def test_fetch_latest_parses_pypi_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyPI JSON 端点的 info.version 是取最新版的唯一入口。"""

    class _Response:
        def __enter__(self) -> "_Response":
            """上下文协议入口。"""
            return self

        def __exit__(self, *exc: object) -> None:
            """上下文协议出口。"""
            return None

        def read(self) -> bytes:
            """返回模拟的 PyPI payload。"""
            return json.dumps({"info": {"version": "0.9.9"}}).encode("utf-8")

    monkeypatch.setattr(cli_update_check.urllib.request, "urlopen", lambda *a, **k: _Response())
    assert fetch_latest_pypi_version() == "0.9.9"


def test_fetch_latest_swallows_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """urlopen 抛出（离线 / 超时 / DNS 失败）时返回 None，不向上抛。"""

    def _raise(*a: object, **k: object) -> None:
        raise OSError("network down")

    monkeypatch.setattr(cli_update_check.urllib.request, "urlopen", _raise)
    assert fetch_latest_pypi_version() is None


# ---------------------------------------------------------------------------
# detect_upgrade_command：按安装方式给出可执行命令或识别失败
# ---------------------------------------------------------------------------


@pytest.fixture()
def _pypi_managed_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """假定当前安装来自 PyPI（无 direct_url.json）。"""
    monkeypatch.setattr(cli_update_check, "_installed_from_non_pypi_source", lambda: False)


def test_non_pypi_source_is_never_auto_upgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """源码 / editable / tarball 直链安装只给命令，不自动执行。"""
    monkeypatch.setattr(cli_update_check, "_installed_from_non_pypi_source", lambda: True)
    assert detect_upgrade_command() is None


def test_uv_tool_install_maps_to_uv_tool_upgrade(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv tool 隔离环境（prefix 含 /uv/tools/）→ `uv tool upgrade kedacode`。"""
    monkeypatch.setattr(sys, "prefix", "/Users/x/.local/share/uv/tools/kedacode")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: f"/usr/bin/{name}")
    plan = detect_upgrade_command()
    assert plan is not None and plan.argv == ("uv", "tool", "upgrade", "kedacode")


def test_windows_uv_tool_install_maps_to_uv_tool_upgrade(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows uv tool 路径使用反斜杠时仍识别为 uv tool。"""
    monkeypatch.setattr(sys, "prefix", r"C:\Users\x\AppData\Local\uv\tools\kedacode")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: f"C:/bin/{name}")
    plan = detect_upgrade_command()
    assert plan is not None and plan.argv == ("uv", "tool", "upgrade", "kedacode")


def test_pipx_install_maps_to_pipx_upgrade(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pipx 隔离环境（prefix 含 /pipx/venvs/）→ `pipx upgrade kedacode`。"""
    monkeypatch.setattr(sys, "prefix", "/Users/x/.local/pipx/venvs/kedacode")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: f"/usr/bin/{name}")
    plan = detect_upgrade_command()
    assert plan is not None and plan.argv == ("pipx", "upgrade", "kedacode")


def test_windows_pipx_install_maps_to_pipx_upgrade(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows pipx 路径使用反斜杠时仍识别为 pipx。"""
    monkeypatch.setattr(sys, "prefix", r"C:\Users\x\pipx\venvs\kedacode")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: f"C:/bin/{name}")
    plan = detect_upgrade_command()
    assert plan is not None and plan.argv == ("pipx", "upgrade", "kedacode")


def test_homebrew_prefix_maps_to_brew_upgrade(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Homebrew Cellar 路径 → `brew upgrade kedacode`。"""
    monkeypatch.setattr(sys, "prefix", "/opt/homebrew/Cellar/kedacode/0.2.1/libexec")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    plan = detect_upgrade_command()
    assert plan is not None and plan.argv == ("brew", "upgrade", "kedacode")


def test_missing_installer_binary_is_not_offered(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv tool 路径命中但 uv 不在 PATH 时，不给跑不起来的命令。"""
    monkeypatch.setattr(sys, "prefix", "/Users/x/.local/share/uv/tools/kedacode")
    monkeypatch.setattr(cli_update_check.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    monkeypatch.setattr(site, "USER_SITE", "/nonexistent-user-site")
    assert detect_upgrade_command() is None


def test_venv_pip_install_maps_to_python_m_pip(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通 venv 内 pip 安装的版本 → `python -m pip install --upgrade kedacode`。"""
    monkeypatch.setattr(sys, "prefix", "/tmp/venv")
    monkeypatch.setattr(sys, "base_prefix", "/usr/local")
    monkeypatch.setattr(cli_update_check.importlib.util, "find_spec", lambda name: object())
    plan = detect_upgrade_command()
    assert plan is not None
    assert plan.argv == (sys.executable, "-m", "pip", "install", "--upgrade", "kedacode")


def test_system_python_falls_back_to_none(
    _pypi_managed_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统解释器且 user-site 不在 sys.path：识别不出，交给可复制命令兜底。"""
    monkeypatch.setattr(sys, "prefix", "/usr/local")
    monkeypatch.setattr(sys, "base_prefix", "/usr/local")
    monkeypatch.setattr(site, "USER_SITE", "/nonexistent-user-site")
    assert detect_upgrade_command() is None


# ---------------------------------------------------------------------------
# 询问与执行：确认升级 / 拒绝 / 识别失败三条分叉
# ---------------------------------------------------------------------------


def _interactive_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """让 startup 钩子走到「已发现新版本」为止。"""
    monkeypatch.setattr(cli_update_check, "_is_interactive_terminal", lambda *a, **k: True)
    monkeypatch.setattr(
        cli_update_check,
        "check_for_update",
        lambda: UpdateNotice(current_version="0.2.1", latest_version="0.3.0"),
    )


def test_confirmed_upgrade_runs_detected_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认后按识别出的安装方式实际执行升级命令。"""
    _interactive_ready(monkeypatch)
    monkeypatch.setattr(
        cli_update_check,
        "detect_upgrade_command",
        lambda: UpgradeCommand(("uv", "tool", "upgrade", "kedacode")),
    )
    monkeypatch.setattr(cli_update_check.typer, "confirm", lambda *a, **k: True)
    run_calls: list[list[str]] = []

    class _Result:
        returncode = 0

    def _fake_run(argv: list[str], **k: object) -> _Result:
        run_calls.append(argv)
        return _Result()

    monkeypatch.setattr(cli_update_check.subprocess, "run", _fake_run)
    startup_update_check(["registry", "list"])
    assert run_calls == [["uv", "tool", "upgrade", "kedacode"]]


def test_declined_upgrade_prints_copyable_command_only(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """用户拒绝时不执行任何东西，但把命令留在屏上随时可复制。"""
    _interactive_ready(monkeypatch)
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {
            "checked_at": cli_update_check.time.time(),
            "current_version": "0.2.1",
            "latest_version": "0.3.0",
        },
    )
    monkeypatch.setattr(
        cli_update_check,
        "detect_upgrade_command",
        lambda: UpgradeCommand(("pipx", "upgrade", "kedacode")),
    )
    confirm_calls: list[str] = []

    def _decline(*args: object, **kwargs: object) -> bool:
        confirm_calls.append("confirm")
        return False

    monkeypatch.setattr(cli_update_check.typer, "confirm", _decline)
    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        cli_update_check.subprocess, "run", lambda argv, **k: run_calls.append(argv)
    )
    startup_update_check(["registry", "list"])
    assert run_calls == []
    captured = capsys.readouterr()
    assert "pipx upgrade kedacode" in captured.err
    cache_record = json.loads(_cache_file(_isolated_home).read_text(encoding="utf-8"))
    assert cache_record["suppressed_version"] == "0.3.0"

    def _forbidden_fetch() -> str | None:
        raise AssertionError("a fresh dismissed cache must not hit PyPI")

    monkeypatch.setattr(
        cli_update_check,
        "check_for_update",
        lambda: check_for_update(fetch_latest=_forbidden_fetch),
    )
    startup_update_check(["registry", "list"])
    assert capsys.readouterr().err == ""
    assert confirm_calls == ["confirm"]


def test_unrecognized_install_lists_candidate_commands(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """识别不出安装方式时不追问、不执行，列出各安装器的可复制命令。"""
    monkeypatch.setattr(cli_update_check, "_is_interactive_terminal", lambda *a, **k: True)
    monkeypatch.setattr(cli_update_check, "resolve_keda_version", lambda: "0.2.1")
    _write_cache(
        _isolated_home,
        {
            "checked_at": cli_update_check.time.time(),
            "current_version": "0.2.1",
            "latest_version": "0.3.0",
        },
    )
    monkeypatch.setattr(cli_update_check, "detect_upgrade_command", lambda: None)
    confirm_calls: list[str] = []
    monkeypatch.setattr(
        cli_update_check.typer,
        "confirm",
        lambda *a, **k: confirm_calls.append("confirm"),
    )
    startup_update_check(["registry", "list"])
    assert confirm_calls == []
    captured = capsys.readouterr()
    assert "uv tool install --force kedacode" in captured.err
    assert "brew upgrade kedacode" in captured.err
    cache_record = json.loads(_cache_file(_isolated_home).read_text(encoding="utf-8"))
    assert cache_record["suppressed_version"] == "0.3.0"

    def _forbidden_fetch() -> str | None:
        raise AssertionError("a fresh noticed cache must not hit PyPI")

    monkeypatch.setattr(
        cli_update_check,
        "check_for_update",
        lambda: check_for_update(fetch_latest=_forbidden_fetch),
    )
    startup_update_check(["registry", "list"])
    assert capsys.readouterr().err == ""


def test_failed_upgrade_keeps_command_for_manual_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """升级命令非零退出时给出可手动重跑的原命令。"""
    _interactive_ready(monkeypatch)
    monkeypatch.setattr(
        cli_update_check,
        "detect_upgrade_command",
        lambda: UpgradeCommand(("brew", "upgrade", "kedacode")),
    )
    monkeypatch.setattr(cli_update_check.typer, "confirm", lambda *a, **k: True)

    class _Result:
        returncode = 1

    monkeypatch.setattr(cli_update_check.subprocess, "run", lambda argv, **k: _Result())
    startup_update_check(["registry", "list"])
    captured = capsys.readouterr()
    assert "brew upgrade kedacode" in captured.err


# ---------------------------------------------------------------------------
# main() 接线：只读入口不触发，人类入口触发
# ---------------------------------------------------------------------------


def test_main_version_flag_short_circuits_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kc --version 在更新检查之前返回，绝不联网。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.api.cli_typer_app.startup_update_check",
        lambda argv: calls.append("check"),
    )
    from backend.api.cli_typer_app import main as typer_main

    assert typer_main(["--version"]) == 0
    assert calls == []


def test_main_invokes_startup_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """人类路径进入命令分发前先过更新检查钩子。"""
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "backend.api.cli_typer_app.startup_update_check",
        lambda argv: seen.append(list(argv)),
    )
    monkeypatch.setattr("backend.api.cli_typer_app.app", lambda **k: 0)
    from backend.api.cli_typer_app import main as typer_main

    assert typer_main(["registry", "list"]) == 0
    assert seen == [["registry", "list"]]
