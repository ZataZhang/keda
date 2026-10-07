"""CLI 旗标的可复用构造块。

``build_parser`` 的命令树已按域名拆到 :mod:`backend.api.cli_parser_*_commands`；
这里保留跨域共用的 choices 取值源与旗标组构造函数，供那些模块与 Typer
前端共享。
"""

from __future__ import annotations

import argparse


def registered_agent_names() -> tuple[str, ...]:
    """按注册顺序返回全部已注册 agent 名（CLI choices 的唯一来源）。

    读取全局配置（含 ``[agent_runner.agents.*]`` 注册块）；配置加载
    失败时回落内置注册表——choices 只影响输入校验，真正的配置错误
    会在命令执行时原样抛出。
    """
    try:
        from backend.core.use_cases.agent_runner_factory import (
            build_app_config_from_settings,
            load_fresh_agent_runner_settings,
        )
        from backend.core.use_cases.agent_invocation import resolve_registered_agents

        return tuple(
            resolve_registered_agents(
                build_app_config_from_settings(load_fresh_agent_runner_settings())
            )
        )
    except Exception:  # noqa: BLE001 - choices 必须在任何配置状态下可用
        from backend.core.shared.models.agent_spec import builtin_agent_names

        return tuple(builtin_agent_names())


def agent_choices_with(
    *, prefix: tuple[str, ...] = (), suffix: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """组合路由别名（auto / none 等）与注册表 agent 名的 choices 元组。"""
    return (*prefix, *registered_agent_names(), *suffix)


def registered_preset_names() -> tuple[str, ...]:
    """按配置声明顺序返回已定义的模型预设名（``--preset`` choices 的来源）。

    与 :func:`registered_agent_names` 同口径：choices 只影响输入校验，
    未定义预设名时 choices 为空（允许任何自由字符串会在解析期 fail-fast，
    因此这里放宽为不做 choices 校验——``None`` 表示不限制）。
    """
    try:
        from backend.core.use_cases.agent_runner_factory import (
            build_app_config_from_settings,
            load_fresh_agent_runner_settings,
        )

        return tuple(
            build_app_config_from_settings(load_fresh_agent_runner_settings()).agent_presets
        )
    except Exception:  # noqa: BLE001 - choices 必须在任何配置状态下可用
        return ()


def add_model_preset_options(parser: argparse.ArgumentParser) -> None:
    """给生命周期锚定的入口加 ``--preset`` / ``--model`` / ``--reasoning-effort``。

    三个旗标都是可选的一次性覆盖：绑定打底，命令行微调同名字段；
    完全不传时行为与今天逐字节一致。
    """
    preset_group = parser.add_argument_group("model preset")
    preset_group.add_argument(
        "--preset",
        default=None,
        help=(
            "Anchor this command's lifecycle stage to a named model preset "
            "(overrides the stage binding for this run). See `kc agent presets`."
        ),
    )
    preset_group.add_argument(
        "--model",
        default=None,
        help="One-shot model id override for the preset / binding (same field).",
    )
    preset_group.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        help="One-shot reasoning effort override for the preset / binding (same field).",
    )


def add_common_options(parser: argparse.ArgumentParser) -> None:
    """Allow global options before or after the effective subcommand."""
    parser.add_argument("--repo", default=argparse.SUPPRESS, help="Target repository path.")
    parser.add_argument(
        "--repo-id", default=argparse.SUPPRESS, help="Target configured repository ID."
    )
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="Deprecated: config is loaded from config.toml and env vars.",
    )


def add_all_repositories_option(parser: argparse.ArgumentParser) -> None:
    """Allow explicit multi-repository selection for configured repositories."""
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_repositories",
        help="Process all enabled configured repositories.",
    )


def add_machine_output_options(parser: argparse.ArgumentParser) -> None:
    """给产出数据的入口加机读旗标（与 Typer 侧 ``OutputOption``/``JsonOutputOption`` 同口径）。

    ``--output`` 是主形态、``--json`` 是等价别名；默认 ``table``——机器模式
    必须显式声明。这里只声明取值域，序列化和 stdout 纯净度由
    :mod:`backend.api.cli_output` 单点负责。
    """
    parser.add_argument(
        "--output",
        choices=("table", "json"),
        default="table",
        help="Output format: table|json.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Alias of --output json (stdout carries data only).",
    )
