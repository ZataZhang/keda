"""Issue 生命周期查询命令的 argparse 构造。"""

from __future__ import annotations

import argparse

from backend.api.cli_parser_options import add_machine_output_options
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    LifecyclePhase,
)


def add_lifecycle_commands(subparsers: argparse._SubParsersAction) -> None:
    """注册只读生命周期查询命令。"""
    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help="Show Issue lifecycle phases and milestone history.",
    )
    lifecycle_parser.set_defaults(command="lifecycle")
    lifecycle_parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="Show one Issue's current phase and timeline.",
    )
    lifecycle_parser.add_argument(
        "--repo-id",
        default=None,
        help="Repository ID; omit to query every local ledger repository.",
    )
    lifecycle_parser.add_argument(
        "--phase",
        choices=tuple(phase.value for phase in LifecyclePhase),
        default=None,
        help="Filter by exact current phase.",
    )
    lifecycle_parser.add_argument(
        "--reached",
        choices=tuple(
            event.value
            for event in LifecycleEventType
            if event is not LifecycleEventType.AGENT_TOKEN_USAGE
        ),
        default=None,
        help="Filter Issues that reached this milestone in their history.",
    )
    add_machine_output_options(lifecycle_parser)
