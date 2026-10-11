"""kc 的机读输出契约：格式解析、单一 emit 出口、结构化错误 envelope。

设计约束（Issue #194 / PRD `P1-FEAT-20260930-141135`）：

- 机器模式必须**显式声明**（``--json`` 或 ``--output json``），默认永远是给人看的
  ``table``；不做"非 TTY 自动切 JSON"，否则会静默改变既有管道脚本的输入。
- 机器模式下 stdout 只承载数据，进度/警告/错误一律走 stderr，因此
  ``route_logs_to_stderr()`` 会把日志的 stdout 流处理器改绑到 stderr。
- JSON 序列化只有本模块这一个出口：命令侧只负责算 payload 和写人类渲染器，
  不再各自 ``print(json.dumps(...))`` / ``console.print_json(...)``。

``console`` / ``error_console`` 复用 :mod:`backend.api.cli_console`，不另建 Console。
"""

from __future__ import annotations

import json
import logging
import sys
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from rich.text import Text

from backend.api.cli_console import console, error_console
from backend.api.cli_exit_codes import ExitCode, error_token_for

__all__ = [
    "CliError",
    "OUTPUT_FORMATS",
    "OUTPUT_FORMAT_JSON",
    "OUTPUT_FORMAT_TABLE",
    "OutputFormat",
    "emit",
    "emit_json",
    "emit_ndjson",
    "error_envelope",
    "json_literal",
    "machine_output_requested",
    "output_format_of",
    "render_cli_error",
    "resolve_output_format",
    "route_logs_to_stderr",
]

OUTPUT_FORMAT_TABLE = "table"
OUTPUT_FORMAT_JSON = "json"
OUTPUT_FORMATS: tuple[str, ...] = (OUTPUT_FORMAT_TABLE, OUTPUT_FORMAT_JSON)


class OutputFormat(str, Enum):
    """``--output`` 的合法取值（click 由枚举成员自动导出 ``choices``）。"""

    table = OUTPUT_FORMAT_TABLE
    json = OUTPUT_FORMAT_JSON


def output_format_of(*, output: Any = None, as_json: bool = False) -> str:
    """把 ``--output`` 取值与 ``--json`` 别名归一成机器/人类形态。

    Args:
        output: ``--output`` 的原始取值。仅 ``table`` / ``json`` 被视为格式；
            其它值（例如 ``kc ask --output <目录>`` 复用同名旗标）忽略。
        as_json: ``--json`` 布尔别名，为真时等价于 ``--output json``。

    Returns:
        ``"json"`` 或 ``"table"``。只有 ``--output json`` 能单独判定机器模式：
        Typer 侧无法区分 ``table`` 是默认值还是显式传入，所以 ``table`` 不
        否决 ``--json``（别名本身就是显式声明）。
    """
    raw_value = output.value if isinstance(output, Enum) else output
    if isinstance(raw_value, str) and raw_value.strip().lower() == OUTPUT_FORMAT_JSON:
        return OUTPUT_FORMAT_JSON
    if as_json:
        return OUTPUT_FORMAT_JSON
    return OUTPUT_FORMAT_TABLE


def resolve_output_format(parsed: Any) -> str:
    """从已解析的命令参数（``argparse.Namespace`` 等）读出输出形态。

    Args:
        parsed: 解析后的命令参数对象。

    Returns:
        ``"json"`` 或 ``"table"``。
    """
    return output_format_of(
        output=getattr(parsed, "output", None),
        as_json=bool(getattr(parsed, "as_json", False)),
    )


def machine_output_requested(args: Sequence[str]) -> bool:
    """从原始参数探测是否声明了机器模式（解析失败时拿不到 Namespace）。

    解析失败的命令无法走 :func:`resolve_output_format` 的正规判定，只能扫描
    原始 token：出现 ``--json``、``--output json`` 或 ``--output=json``
    （取值大小写不敏感）即视为机器模式声明。

    Args:
        args: 传给 CLI 入口的原始参数序列。

    Returns:
        真值表示调用方显式请求了机器输出。
    """
    for index, token in enumerate(args):
        if token == "--json":
            return True
        if token.startswith("--output="):
            if token.removeprefix("--output=").strip().lower() == OUTPUT_FORMAT_JSON:
                return True
        elif token == "--output" and index + 1 < len(args):
            if args[index + 1].strip().lower() == OUTPUT_FORMAT_JSON:
                return True
    return False


def _dump_json(payload: Any) -> str:
    """唯一的 JSON 序列化口径（UTF-8 原文、缩进 2、不可序列化对象回落 str）。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _print_verbatim(text: str, target: Any = None) -> None:
    """原样打印文本：不折行、不解析 markup、不高亮。

    Rich 默认按 80 列折行，会把长 URL / 长 argv 字符串截断成多行从而破坏 JSON；
    ``soft_wrap=True`` 关掉折行，``markup=False`` + ``highlight=False`` 保证管道
    与终端下字节一致。
    """
    (target or console).print(text, markup=False, highlight=False, soft_wrap=True)


def emit_json(payload: Any) -> None:
    """把 payload 作为 JSON 写到 stdout（机器模式的唯一数据出口）。"""
    _print_verbatim(_dump_json(payload))


def emit_ndjson(rows: Iterable[Any]) -> None:
    """按行输出 NDJSON：每行一个 JSON 对象，用于日志/记录流。

    Args:
        rows: 逐条可 JSON 化的记录。
    """
    for row in rows:
        _print_verbatim(json.dumps(row, ensure_ascii=False, default=str))


def emit(
    payload: Any,
    *,
    fmt: str = OUTPUT_FORMAT_TABLE,
    human_renderer: Callable[[], Any] | None = None,
) -> None:
    """按形态输出一次结果：机器模式走 JSON，人类模式走渲染器。

    Args:
        payload: 机器模式要序列化的数据结构。
        fmt: ``resolve_output_format`` 的结果。
        human_renderer: 人类模式下的打印函数（自行写 stdout）。

    Returns:
        None。输出直接落到 stdout。
    """
    if fmt == OUTPUT_FORMAT_JSON:
        emit_json(payload)
        return
    if human_renderer is not None:
        human_renderer()


def json_literal(value: Any) -> str:
    """把单个值序列化成 JSON 字面量，供嵌入人类可读文本使用。

    存在本模块而非调用方，是为了让"JSON 序列化只出现在 `cli_output.py`"这条
    守卫可被 ``rg`` 检查（例如 ``kc config migrate`` 打印被钉住的配置值）。
    """
    return json.dumps(value, ensure_ascii=False, default=str)


class CliError(Exception):
    """带语义退出码与下一步建议的命令失败。

    ``suggestion`` 必须是一条可直接执行的命令：agent 读到结构化错误后无需猜。
    """

    def __init__(
        self,
        message: str,
        *,
        code: ExitCode | int = ExitCode.GENERAL,
        error: str | None = None,
        suggestion: str | None = None,
        retryable: bool = False,
    ) -> None:
        """记录错误消息、语义退出码与可选建议。

        Args:
            message: 面向人类的错误说明。
            code: :class:`backend.api.cli_exit_codes.ExitCode` 成员或等值整数。
            error: 机器可读错误名；省略时按退出码推导。
            suggestion: 下一步可直接执行的命令。
            retryable: 调用方是否值得原样重试。
        """
        super().__init__(message)
        self.message = message
        self.code = ExitCode(int(code))
        self.error = error or error_token_for(self.code)
        self.suggestion = suggestion
        self.retryable = retryable


def error_envelope(error: CliError) -> dict[str, Any]:
    """构造 FR-4 的结构化错误 envelope。"""
    return {
        "error": error.error,
        "message": error.message,
        "suggestion": error.suggestion,
        "retryable": error.retryable,
        "exit_code": int(error.code),
    }


def render_cli_error(error: CliError, *, fmt: str = OUTPUT_FORMAT_TABLE) -> int:
    """把 :class:`CliError` 落到 stderr 并返回其退出码。

    机器模式输出结构化 envelope，人类模式输出等价文本；两者都不污染 stdout。

    Args:
        error: 待渲染的命令失败。
        fmt: 当前输出形态。

    Returns:
        该错误对应的进程退出码。
    """
    if fmt == OUTPUT_FORMAT_JSON:
        _print_verbatim(_dump_json(error_envelope(error)), target=error_console)
        return int(error.code)
    # 人类模式：错误正文可能含方括号（TOML 片段、路径），因此用 Text 拼接而非
    # markup 字符串——样式照旧，但不会被当成富文本标签吞字。
    error_console.print(
        Text.assemble((f"{error.error}: ", "red"), (error.message, "")),
        soft_wrap=True,
    )
    if error.suggestion:
        error_console.print(
            Text.assemble(("next: ", "yellow"), (error.suggestion, "")), soft_wrap=True
        )
    return int(error.code)


def route_logs_to_stderr() -> None:
    """把写向 stdout 的日志流处理器改绑到 stderr，保证机器模式 stdout 纯净。

    应用日志（``backend.infrastructure.logging``）默认挂一个 stdout
    ``StreamHandler``，进度/警告会混进 JSON 输出。这里只改 stdlib 处理器指向，
    不改日志级别与格式，也不触碰人类模式行为。
    """
    stdout_stream = sys.stdout
    loggers: Sequence[logging.Logger | logging.LoggerAdapter] = [
        logging.getLogger(),
        *(
            logger
            for logger in logging.Logger.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ),
    ]
    for logger in loggers:
        for handler in getattr(logger, "handlers", ()):
            if getattr(handler, "stream", None) is not stdout_stream:
                continue
            try:
                handler.setStream(sys.stderr)
            except Exception:  # noqa: BLE001 - 输出契约优先，改绑失败保持原样。
                continue
