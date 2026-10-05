"""从真实 Typer/click 命令树派生 ``iar`` 的命令与参数元数据。

不维护静态 schema 文件：CLI 演进时手工表必然漂移，运行时派生才是 agent 可以
无条件相信的自省入口（``iar schema --json``）。

类型判定一律走鸭子类型（``param_type_name`` / ``commands``）而不是
``isinstance(param, click.Option)``：Typer 0.26 起自带一份 click
（``typer._click``），注册出来的命令对象继承那份，与仓库依赖的 ``click``
类不是同一身份，isinstance 全 False 会让派生结果静默为空。
"""

from __future__ import annotations

import inspect
from enum import Enum
from typing import Any, TYPE_CHECKING

import click

from backend.api.cli_exit_codes import EXIT_CODE_HELP, ExitCode, error_token_for

if TYPE_CHECKING:
    import typer

__all__ = ["build_command_schema", "render_schema_human"]


def _enum_value(value: Any) -> Any:
    """把 Enum（含 str-mixin Enum）还原成其取值，避免序列化出 ``Name.MEMBER``。"""
    if isinstance(value, Enum):
        return value.value
    return value


def _is_group(command: Any) -> bool:
    """命令节点是否为可继续下钻的分组（Group / MultiCommand）。"""
    return hasattr(command, "commands") and hasattr(command, "list_commands")


def _is_argument(param: Any) -> bool:
    """参数是否为位置参数（用 click 自己的类型名，不做 isinstance 判定）。"""
    return getattr(param, "param_type_name", None) == "argument"


def _clean_help(command: Any) -> str:
    """取命令的一行帮助文本（click 的 short help 优先，回落长帮助首段）。"""
    short_help = getattr(command, "short_help", None)
    if short_help:
        return short_help.strip()
    raw_help = (getattr(command, "help", None) or "").strip()
    if not raw_help:
        return ""
    return inspect.cleandoc(raw_help).splitlines()[0].strip()


def _type_name(param: Any) -> str:
    """返回参数类型的名字（``string`` / ``integer`` / ``boolean`` / ``choice`` …）。"""
    return getattr(param.type, "name", str(param.type))


def _enum_choices(param: Any) -> list[str] | None:
    """枚举/候选参数的合法取值列表，其它参数返回 ``None``。"""
    choices = getattr(param.type, "choices", None)
    if choices is None:
        return None
    return [str(choice) for choice in choices]


def _is_bool_flag(param: Any) -> bool:
    """选项是否为布尔开关（开关型示例只写旗标本身，不带取值）。"""
    flag = getattr(param, "is_flag", None)
    if flag is not None:
        return bool(flag)
    is_bool_flag = getattr(param, "is_bool_flag", False)
    return bool(is_bool_flag() if callable(is_bool_flag) else is_bool_flag)


def _example_token(param: Any) -> str | None:
    """给参数造一个可直接抄的示例片段（枚举取首个合法值）。"""
    choices = _enum_choices(param)
    if _is_argument(param):
        return choices[0] if choices else f"<{_type_name(param)}>"
    if _is_bool_flag(param):
        return param.opts[0]
    value_token = choices[0] if choices else f"<{_type_name(param)}>"
    return f"{param.opts[0]} {value_token}"


def _param_entry(param: Any) -> dict[str, Any]:
    """把单个命令参数导出为 schema 条目。"""
    return {
        "name": param.opts[0] if param.opts else param.name,
        "names": list(param.opts) + list(getattr(param, "secondary_opts", None) or []),
        "type": _type_name(param),
        "required": bool(param.required),
        "multiple": bool(getattr(param, "multiple", False)),
        "enum": _enum_choices(param),
        "default": _enum_value(param.default),
        "help": getattr(param, "help", None),
        "example": _example_token(param),
    }


def _leaf_entry(command: Any, path: tuple[str, ...]) -> dict[str, Any]:
    """把叶子命令导出为 ``{name, path, help, arguments, options}``。"""
    arguments: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    for param in command.params:
        if _is_argument(param):
            arguments.append(_param_entry(param))
        else:
            options.append(_param_entry(param))
    return {
        "name": " ".join(path),
        "path": list(path),
        "help": _clean_help(command),
        "arguments": arguments,
        "options": options,
    }


def _walk(command: Any, path: tuple[str, ...], collected: list[dict[str, Any]]) -> None:
    """深度优先展开命令树，把每个可见叶子命令收进 ``collected``。"""
    if _is_group(command):
        context = click.Context(command)
        for name in command.list_commands(context):
            subcommand = command.get_command(context, name)
            if subcommand is None or subcommand.hidden:
                continue
            _walk(subcommand, (*path, name), collected)
        return
    if getattr(command, "hidden", False) or not hasattr(command, "params"):
        return
    collected.append(_leaf_entry(command, path))


def build_command_schema(typer_app: "typer.Typer") -> dict[str, Any]:
    """从 Typer app 的真实命令树派生自省 schema。

    Args:
        typer_app: 根 Typer app（``backend.api.cli_typer_app.app``）。

    Returns:
        含 ``commands``（每个叶子命令的名称、参数、类型、必填、枚举、默认、示例）
        与退出码表的 JSON 可序列化 dict。
    """
    from typer.main import get_command

    root = get_command(typer_app)
    collected: list[dict[str, Any]] = []
    _walk(root, (), collected)
    return {
        "name": root.name or "iar",
        "help": _clean_help(root),
        "exit_codes": {
            "help": EXIT_CODE_HELP,
            # 导出的必须是 envelope 的 ``error`` 名，而不是枚举成员名：
            # 消费方要把 ``$?`` 和 stderr envelope 对上，两套名字会逼它再猜一层。
            "values": {int(code): error_token_for(code) for code in ExitCode},
        },
        "command_count": len(collected),
        "commands": collected,
    }


def render_schema_human(schema: dict[str, Any]) -> str:
    """把 schema 渲染成缩进文本，供人类模式直接阅读。"""
    lines: list[str] = [f"{schema['name']} — {schema['help']}"]
    for command in schema["commands"]:
        lines.append(f"\n{command['name']}")
        if command["help"]:
            lines.append(f"    {command['help']}")
        for argument in command["arguments"]:
            lines.append(
                f"    <{argument['name']}>  {argument['type']}"
                f"{' required' if argument['required'] else ''}"
            )
        for option in command["options"]:
            detail = f"    {option['name']}  {option['type']}"
            if option["enum"]:
                detail += f" [{'|'.join(option['enum'])}]"
            if option["default"] is not None:
                detail += f" (default: {option['default']})"
            if option["help"]:
                detail += f" — {option['help']}"
            lines.append(detail)
    lines.append("")
    lines.append(schema["exit_codes"]["help"])
    return "\n".join(lines)
