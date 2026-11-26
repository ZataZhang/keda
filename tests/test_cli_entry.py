"""kc console script 入口（:mod:`backend.api.cli_entry`）的缺依赖拦截测试。

源码更新新增依赖但环境未重装时，入口应给出修复命令而不是抛堆栈；
``backend`` 自身子模块缺失属于代码问题，必须原样抛出。
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.api.cli
from backend.api import cli_entry

_REAL_IMPORT = builtins.__import__


def _fail_cli_import(monkeypatch: pytest.MonkeyPatch, missing_module_name: str) -> None:
    """让导入 ``backend.api.cli`` 时抛出指定模块缺失，模拟依赖链中途断开。"""

    def _import_with_missing_module(name: str, *args: object, **kwargs: object) -> object:
        if name == "backend.api.cli":
            raise ModuleNotFoundError(
                f"No module named '{missing_module_name}'", name=missing_module_name
            )
        return _REAL_IMPORT(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_with_missing_module)


def test_missing_third_party_dependency_prints_fix_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺第三方依赖时不抛堆栈：退出码 1，stderr 指明模块与重装命令。"""
    _fail_cli_import(monkeypatch, "requests")
    monkeypatch.setattr(cli_entry, "_reinstall_hint", lambda: "uv tool install --reinstall x")

    exit_code = cli_entry.main(["--version"])

    assert exit_code == 1
    captured_err = capsys.readouterr().err
    assert "'requests' is not installed" in captured_err
    assert "uv tool install --reinstall x" in captured_err


def test_missing_own_backend_module_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """``backend`` 子模块缺失是代码问题，保留原始异常与堆栈。"""
    _fail_cli_import(monkeypatch, "backend.core.use_cases.removed_module")

    with pytest.raises(ModuleNotFoundError):
        cli_entry.main(["--version"])


def test_healthy_environment_delegates_to_full_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """依赖齐全时原样转交完整 CLI，并透传参数与退出码。"""
    received_argv: list[list[str] | None] = []

    def _fake_cli_main(argv: list[str] | None = None) -> int:
        received_argv.append(argv)
        return 7

    monkeypatch.setattr(backend.api.cli, "main", _fake_cli_main)

    assert cli_entry.main(["issue", "list"]) == 7
    assert received_argv == [["issue", "list"]]


def test_editable_source_directory_reads_pep610_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """可编辑安装记录里的 ``file:`` URL 还原成本机源码目录。"""
    direct_url_text = json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})
    monkeypatch.setattr(
        cli_entry.importlib_metadata,
        "distribution",
        lambda name: SimpleNamespace(read_text=lambda file_name: direct_url_text),
    )

    source_directory = cli_entry.editable_source_directory()

    assert source_directory is not None
    assert Path(source_directory) == tmp_path


@pytest.mark.parametrize(
    ("tool_prefix", "editable_directory", "expected_fragment"),
    [
        pytest.param(
            "/home/x/.local/share/uv/tools/kedacode",
            "/home/x/code/keda",
            'uv tool install --reinstall --editable "/home/x/code/keda"',
            id="uv-tool-editable",
        ),
        pytest.param(
            r"C:\Users\x\AppData\Roaming\uv\tools\kedacode",
            None,
            "uv tool install --reinstall kedacode",
            id="windows-uv-tool",
        ),
        pytest.param(
            "/home/x/code/keda/.venv",
            "/home/x/code/keda",
            "uv sync",
            id="project-venv-editable",
        ),
        pytest.param(
            "/tmp/other-venv",
            "/home/x/code/keda",
            '-e "/home/x/code/keda"',
            id="separate-venv-editable",
        ),
    ],
)
def test_reinstall_hint_matches_install_method(
    monkeypatch: pytest.MonkeyPatch,
    tool_prefix: str,
    editable_directory: str | None,
    expected_fragment: str,
) -> None:
    """修复命令按 uv tool / 项目 venv、可编辑与否给出。"""
    monkeypatch.setattr(sys, "prefix", tool_prefix)
    monkeypatch.setattr(cli_entry, "editable_source_directory", lambda: editable_directory)

    assert expected_fragment in cli_entry._reinstall_hint()
