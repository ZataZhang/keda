"""``iar schema`` 运行时自省测试（FR-5）。

schema 不维护静态表，而是从真实 Typer/click 命令树派生，因此测试的重心是
"同源"与"旗标对齐"：条目形状稳定、机器旗标在 Typer 侧成对出现、argparse 门面
不漂移。注意 typer 0.26 自带 vendored click，``isinstance(x, click.*)`` 恒为
False，这里的断言只依赖派生结果本身。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ERROR_TOKEN_BY_EXIT_CODE
from backend.api.cli_parser import build_parser
from backend.api.cli_schema import build_command_schema, render_schema_human
from backend.api.cli_typer_app import app

#: ``argparse`` 门面未收录的命令：只在 Typer 侧存在，不参与旗标对齐检查。
FACADE_EXEMPT_COMMANDS = frozenset({"schema", "tokens"})

#: ``--output`` 在这些命令上是**输出目录**，不是格式取值域，必须被排除在机读契约之外。
DIRECTORY_OUTPUT_COMMANDS = frozenset({"ask", "deliberate"})

PARAM_ENTRY_KEYS = {
    "name",
    "names",
    "type",
    "required",
    "multiple",
    "enum",
    "default",
    "help",
    "example",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """真实命令树派生出的 schema 文档（模块级，避免每个用例重复走 click）。"""
    return build_command_schema(app)


def test_schema_root_documents_the_machine_contract(schema: dict[str, Any]) -> None:
    """根文档带退出码表与命令总数，agent 一次调用就能拿到全貌。"""
    assert schema["name"] == "iar"
    assert set(schema) == {"name", "help", "exit_codes", "command_count", "commands"}
    assert set(schema["exit_codes"]["values"]) == {0, 1, 2, 3, 4, 5, 10}
    # 导出的必须是 envelope ``error`` 名：消费方靠它把 $? 与 stderr envelope 对上。
    assert schema["exit_codes"]["values"] == {
        int(code): token for code, token in ERROR_TOKEN_BY_EXIT_CODE.items()
    }
    assert schema["command_count"] == len(schema["commands"])


def test_every_command_entry_has_a_stable_shape(schema: dict[str, Any]) -> None:
    """命令条目字段集合是契约的一部分，不随 click 内部结构漂移。"""
    for entry in schema["commands"]:
        assert set(entry) == {"name", "path", "help", "arguments", "options"}, entry["name"]
        assert entry["path"], entry["name"]
        assert " ".join(entry["path"]) == entry["name"]
        for parameter in [*entry["arguments"], *entry["options"]]:
            assert set(parameter) == PARAM_ENTRY_KEYS, parameter["name"]


def test_schema_covers_every_leaf_of_the_real_command_tree(schema: dict[str, Any]) -> None:
    """同源断言：独立遍历 click 树得到的叶子命令集与 schema 完全一致。

    独立遍历用鸭子类型（``commands``/``list_commands``）而非 ``isinstance``，因为
    typer 0.26 自带 vendored click；这也是漏挂子 app 时唯一可靠的判定方式。
    """
    import typer.main

    def _leaf_names(command: Any, prefix: tuple[str, ...]) -> set[str]:
        children = getattr(command, "commands", None)
        if not isinstance(children, dict):
            return {" ".join(prefix)}
        return {
            name
            for child_name, child in children.items()
            for name in _leaf_names(child, (*prefix, child_name))
        }

    expected = _leaf_names(typer.main.get_command(app), ())
    collected = {entry["name"] for entry in schema["commands"]}
    assert collected == expected
    assert len(schema["commands"]) == len(collected)
    assert {"schema", "tokens", "issue list", "agent doctor"} <= collected


def _machine_output_entries(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """挑出把 ``--output`` 当**格式**用的命令（排除目录语义的 ``ask``/``deliberate``）。"""
    entries = []
    for entry in schema["commands"]:
        options = {option["name"]: option for option in entry["options"]}
        output_option = options.get("--output")
        if output_option is None or output_option["type"] != "choice":
            continue
        assert entry["name"] not in DIRECTORY_OUTPUT_COMMANDS
        entries.append(entry)
    return entries


def test_machine_flag_commands_expose_output_enum(schema: dict[str, Any]) -> None:
    """``--output`` 必须是枚举取值域 ``[table, json]``，``--json`` 成对出现。"""
    machine_commands = _machine_output_entries(schema)
    assert machine_commands, "机读旗标应已挂到产数据的命令上"
    for entry in machine_commands:
        output_option = next(option for option in entry["options"] if option["name"] == "--output")
        assert output_option["enum"] == ["table", "json"], entry["name"]
        assert output_option["default"] == "table", entry["name"]
        json_option = next(
            (option for option in entry["options"] if option["name"] == "--json"), None
        )
        assert json_option is not None, entry["name"]
        assert json_option["type"] == "boolean"
        assert json_option["required"] is False


def test_directory_output_commands_keep_their_own_output_semantics(schema: dict[str, Any]) -> None:
    """``ask``/``deliberate`` 的 ``--output <目录>`` 不被机读契约误伤。"""
    entries = {entry["name"]: entry for entry in schema["commands"]}
    for command_name in DIRECTORY_OUTPUT_COMMANDS:
        options = {option["name"]: option for option in entries[command_name]["options"]}
        assert options["--output"]["type"] == "text"
        assert options["--output"]["enum"] is None
        assert "--json" not in options


def test_argparse_facade_matches_typer_machine_flags(schema: dict[str, Any]) -> None:
    """§7.4 隐藏入口守卫：两套解析器的机读旗标取值域一致（门面未收录的除外）。"""
    parser = build_parser()
    subparsers = _collect_argparse_subcommands(parser)
    checked = 0
    for entry in _machine_output_entries(schema):
        command_name = entry["name"]
        if command_name in FACADE_EXEMPT_COMMANDS:
            continue
        facade_parser = subparsers.get(command_name)
        assert facade_parser is not None, f"argparse 门面缺少 {command_name}"
        output_actions = [
            action for action in facade_parser._actions if "--output" in action.option_strings
        ]
        assert [action.choices for action in output_actions] == [("table", "json")], command_name
        json_actions = [
            action for action in facade_parser._actions if "--json" in action.option_strings
        ]
        assert [action.dest for action in json_actions] == ["as_json"], command_name
        checked += 1
    assert checked >= 10, "对齐检查不应只覆盖一两条命令"


def _collect_argparse_subcommands(parser: Any) -> dict[str, Any]:
    """把 argparse 门面展开全部子命令解析器，按空格分隔的命令名索引。"""
    found: dict[str, Any] = {}

    def _walk_parser(current: Any, prefix: tuple[str, ...]) -> None:
        for action in current._actions:
            choices = getattr(action, "choices", None)
            if type(action).__name__ != "_SubParsersAction" or not isinstance(choices, dict):
                continue
            for name, sub_parser in choices.items():
                path = (*prefix, name)
                found[" ".join(path)] = sub_parser
                _walk_parser(sub_parser, path)

    _walk_parser(parser, ())
    return found


def test_schema_json_and_output_json_are_byte_identical() -> None:
    """``--json`` 与 ``--output json`` 是同一份文档，不产生第二种序列化口径。"""
    runner = CliRunner()
    as_json = runner.invoke(app, ["schema", "--json"])
    explicit = runner.invoke(app, ["schema", "--output", "json"])
    assert as_json.exit_code == 0 and explicit.exit_code == 0
    assert as_json.stdout == explicit.stdout
    payload = json.loads(as_json.stdout)
    assert payload["name"] == "iar"


def test_schema_human_render_lists_commands_without_json(
    schema: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """默认（不传新旗标）仍是人类可读清单，不泄漏 JSON。"""
    rendered = render_schema_human(schema)
    assert "iar —" in rendered
    assert "issue list" in rendered
    assert "{" not in rendered


def test_schema_command_registered_on_root_app() -> None:
    """``iar schema`` 是只读命令，默认退出码 0 且不触达任何仓库配置。"""
    runner = CliRunner()
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "init" in result.stdout


def _spy_route_logs_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    command_module: Any,
    calls: list[str],
) -> None:
    """把命令模块内的 ``route_logs_to_stderr`` 换成记录调用的间谍。

    命令经 ``from ... import route_logs_to_stderr`` 持有模块级名字，间谍必须
    打在导入方模块（``cli_typer_schema`` / ``cli_typer_tokens``）上才会被命中；
    间谍替换掉真实改绑不影响这里的断言目标（是否调用了改绑点本身）。
    """
    monkeypatch.setattr(
        command_module,
        "route_logs_to_stderr",
        lambda: calls.append(command_module.__name__),
    )


@pytest.mark.parametrize("json_flags", [["--json"], ["--output", "json"]])
def test_schema_json_routes_logs_to_stderr(
    json_flags: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归：``iar schema`` 直接从 Typer app emit JSON、绕过中央 dispatcher，
    机器模式下必须自行调用 ``route_logs_to_stderr()``，人类表格模式不得调用。"""
    from backend.api import cli_typer_schema

    calls: list[str] = []
    _spy_route_logs_to_stderr(monkeypatch, cli_typer_schema, calls)
    runner = CliRunner()

    machine_result = runner.invoke(app, ["schema", *json_flags])
    assert machine_result.exit_code == 0
    assert calls == [cli_typer_schema.__name__]

    table_result = runner.invoke(app, ["schema"])
    assert table_result.exit_code == 0
    assert calls == [cli_typer_schema.__name__]


def test_tokens_json_routes_logs_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归：``iar tokens`` 同样绕过中央 dispatcher，机器模式必须自行改绑日志，
    否则任何一条日志都会混进 stdout 上的 JSON 文档。"""
    from backend.api import cli_typer_tokens
    from backend.infrastructure.persistence.console_store import SqliteConsoleStore

    calls: list[str] = []
    _spy_route_logs_to_stderr(monkeypatch, cli_typer_tokens, calls)
    monkeypatch.setattr(
        cli_typer_tokens,
        "create_console_store",
        lambda: SqliteConsoleStore(tmp_path / "console.db"),
    )
    runner = CliRunner()

    machine_result = runner.invoke(app, ["tokens", "--json"])
    assert machine_result.exit_code == 0
    assert json.loads(machine_result.stdout)["token_usage"]["by_flow"] == {}
    assert calls == [cli_typer_tokens.__name__]

    table_result = runner.invoke(app, ["tokens"])
    assert table_result.exit_code == 0
    assert "暂无 token 用量数据" in table_result.stdout
    assert calls == [cli_typer_tokens.__name__]
