"""语义退出码契约测试（FR-3）。

码值本身是对外承诺，一旦发布就不能漂：这里钉住 ``0/1/2/3/4/5/10`` 的取值、
异常到码的唯一翻译点，以及公布在 ``--help`` 里的码表文案。
"""

from __future__ import annotations

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import (
    ERROR_TOKEN_BY_EXIT_CODE,
    EXIT_CODE_HELP,
    ExitCode,
    error_token_for,
    translate_exit_code,
)
from backend.api.cli_output import CliError


def test_exit_code_values_are_the_published_contract() -> None:
    """POSIX 的 ``0/1/2`` 不变，只在其上补 ``3/4/5/10``。"""
    assert ExitCode.SUCCESS == 0
    assert ExitCode.GENERAL == 1
    assert ExitCode.USAGE == 2
    assert ExitCode.NOT_FOUND == 3
    assert ExitCode.PERMISSION == 4
    assert ExitCode.CONFLICT == 5
    assert ExitCode.DRY_RUN_OK == 10


def test_error_tokens_cover_every_exit_code() -> None:
    """每个语义码都有稳定的机器可读错误名，供 envelope 的 ``error`` 字段使用。"""
    assert {code: token for code, token in ERROR_TOKEN_BY_EXIT_CODE.items()} == {
        ExitCode.SUCCESS: "ok",
        ExitCode.GENERAL: "error",
        ExitCode.USAGE: "usage_error",
        ExitCode.NOT_FOUND: "not_found",
        ExitCode.PERMISSION: "permission_denied",
        ExitCode.CONFLICT: "conflict",
        ExitCode.DRY_RUN_OK: "dry_run_ok",
    }


@pytest.mark.parametrize(
    ("code", "expected_token"),
    [
        (ExitCode.NOT_FOUND, "not_found"),
        (int(ExitCode.PERMISSION), "permission_denied"),
        (99, "error"),
    ],
)
def test_error_token_for_falls_back_to_error_on_unknown_code(
    code: ExitCode | int,
    expected_token: str,
) -> None:
    """未登记的码不抛异常，回落 ``error``，保证 envelope 永远可解析。"""
    assert error_token_for(code) == expected_token


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (CliError("gone", code=ExitCode.NOT_FOUND), 3),
        (CliError("denied", code=ExitCode.PERMISSION), 4),
        (CliError("exists", code=ExitCode.CONFLICT), 5),
        (CliError("plain boom"), 1),
        (FileNotFoundError("no such file"), 3),
        (PermissionError("not allowed"), 4),
        (FileExistsError("already there"), 5),
        (ValueError("bad value"), 1),
        (RuntimeError("whatever"), 1),
    ],
)
def test_translate_exit_code_maps_exceptions_to_semantic_codes(
    exception: BaseException,
    expected_code: int,
) -> None:
    """异常 → 退出码只有一个落点：明确类别给语义码，其余一律 ``1`` 兜底。"""
    assert translate_exit_code(exception) == expected_code


def test_help_text_publishes_the_full_code_table() -> None:
    """FR-3 要求码表出现在 ``--help``：文案必须逐个点名七个码与机读入口。"""
    for fragment in (
        "Exit codes:",
        "0 ok",
        "1 unclassified",
        "2 usage",
        "3 not found",
        "4 permission",
        "5 conflict",
        "10 dry-run",
        "--json",
        "kc schema --json",
    ):
        assert fragment in EXIT_CODE_HELP


def test_help_text_lists_every_declared_code_value() -> None:
    """码表不能漏项：``ExitCode`` 新增成员时必须同步文案。"""
    for code in ExitCode:
        assert f"{int(code)} " in EXIT_CODE_HELP
