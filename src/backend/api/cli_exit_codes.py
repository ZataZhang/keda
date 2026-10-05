"""iar 的语义退出码常量与「异常 → 退出码」翻译。

POSIX 习惯只区分 ``0``（成功）/ ``1``（失败）/ ``2``（用法错误），调用方
只能去 parse stderr 的自然语言才能知道失败原因。本模块在保持 ``0/1/2``
不变的前提下补 ``3/4/5/10`` 四个语义码，让外部 agent 能用 ``$?`` 分流：
换仓库重试（``3``）、提示鉴权（``4``）、改名或加 ``--force``（``5``）、
把 dry-run 当门禁（``10``）。

未归入任何类别的失败仍然返回 ``1``——新码只在「有明确类别」的失败点启用，
以缩小对既有「只看是否非零」脚本的影响面。
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "ERROR_TOKEN_BY_EXIT_CODE",
    "EXIT_CODE_HELP",
    "ExitCode",
    "error_token_for",
    "translate_exit_code",
]


class ExitCode(IntEnum):
    """``iar`` 对外的退出码契约。"""

    SUCCESS = 0
    GENERAL = 1
    USAGE = 2
    NOT_FOUND = 3
    PERMISSION = 4
    CONFLICT = 5
    DRY_RUN_OK = 10


ERROR_TOKEN_BY_EXIT_CODE: dict[ExitCode, str] = {
    ExitCode.SUCCESS: "ok",
    ExitCode.GENERAL: "error",
    ExitCode.USAGE: "usage_error",
    ExitCode.NOT_FOUND: "not_found",
    ExitCode.PERMISSION: "permission_denied",
    ExitCode.CONFLICT: "conflict",
    ExitCode.DRY_RUN_OK: "dry_run_ok",
}

#: 公布在 ``iar --help`` 与各命令帮助里的码表（FR-3）。
EXIT_CODE_HELP = (
    "Exit codes: 0 ok · 1 unclassified failure · 2 usage error · "
    "3 not found · 4 permission denied · 5 conflict · 10 dry-run passed. "
    "Machine output: pass --json (alias of --output json); stdout then "
    "carries data only and failures are reported as a JSON envelope on stderr. "
    "Introspect flags with `iar schema --json`."
)


def error_token_for(code: ExitCode | int) -> str:
    """返回退出码对应的机器可读错误名（未知码回落 ``error``）。

    Args:
        code: :class:`ExitCode` 成员或等值整数。

    Returns:
        结构化错误 envelope 的 ``error`` 字段取值。
    """
    try:
        return ERROR_TOKEN_BY_EXIT_CODE[ExitCode(int(code))]
    except ValueError:
        return ERROR_TOKEN_BY_EXIT_CODE[ExitCode.GENERAL]


def translate_exit_code(exc: BaseException) -> int:
    """把异常翻译成进程退出码（异常 → 语义码的唯一落点）。

    Args:
        exc: 命令处理函数抛出的异常。

    Returns:
        :class:`CliError` 携带的语义码；标准 OS 错误按其语义（未找到 / 无权限 /
        已存在）映射；其余异常按未分类失败处理（``1``）。
    """
    from backend.api.cli_output import CliError  # 延迟导入：cli_output 依赖本模块的常量

    if isinstance(exc, CliError):
        return int(exc.code)
    if isinstance(exc, FileNotFoundError):
        return int(ExitCode.NOT_FOUND)
    if isinstance(exc, PermissionError):
        return int(ExitCode.PERMISSION)
    if isinstance(exc, FileExistsError):
        return int(ExitCode.CONFLICT)
    return int(ExitCode.GENERAL)
