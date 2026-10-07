"""CLI argument parser construction.

This module builds the argparse parser used by both the direct
``backend.api.cli`` entrypoint and the Typer front-end in
``backend.api.cli_typer``.

命令树本体按域拆在同目录的 ``cli_parser_*_commands`` 模块里（单个文件的
非空行有 CI 硬上限），共用旗标构造块在 :mod:`backend.api.cli_parser_options`；
本模块只负责建顶层 parser（命令名取自身份模块）并按域装配。

``--agent`` 的合法值来自 agent 注册表（``[agent_runner.agents.*]`` 配置
段 + 内置默认），不再写死 agent 名；配置加载失败时回落内置默认，
保证 ``--help`` 等只读路径始终可用。
"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_ops_commands import add_ops_commands
from backend.api.cli_parser_registry_commands import add_registry_commands
from backend.api.cli_parser_runner_commands import add_runner_commands
from backend.api.cli_parser_session_commands import add_session_commands
from backend.api.cli_parser_setup_commands import add_setup_commands

from backend.core.shared.models import product_identity


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(prog=product_identity.PRIMARY_COMMAND_NAME)
    parser.add_argument("--repo", default=None, help="Target repository path.")
    parser.add_argument("--repo-id", default=None, help="Target configured repository ID.")
    parser.add_argument(
        "--config",
        help="Deprecated: config is loaded from config.toml and env vars.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_setup_commands(subparsers)
    add_runner_commands(subparsers)
    add_session_commands(subparsers)
    add_registry_commands(subparsers)
    add_ops_commands(subparsers)
    return parser
