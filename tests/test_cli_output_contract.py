"""机读输出契约测试（FR-1/FR-2/FR-4）。

覆盖三件事：``--json`` 与 ``--output json`` 等价且默认仍是人类表格；机器模式下
stdout 只承载数据、日志与错误改道 stderr；错误在两种模式下都携带语义退出码与
可跑的 ``suggestion``。这里测的是契约单元，真实 CLI 入口的行为由
``.iar/evidence/`` 下的 rv-* 证据把关。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    OUTPUT_FORMAT_TABLE,
    CliError,
    OutputFormat,
    emit,
    emit_ndjson,
    error_envelope,
    json_literal,
    output_format_of,
    render_cli_error,
    resolve_output_format,
    route_logs_to_stderr,
)
from backend.api.cli_output import machine_output_requested
from backend.api.cli_typer_app import main as typer_main


def test_output_format_json_alias_equals_explicit_flag() -> None:
    """``--json`` 是 ``--output json`` 的别名，两者都判定为机器模式。"""
    assert output_format_of(output="json") == OUTPUT_FORMAT_JSON
    assert output_format_of(as_json=True) == OUTPUT_FORMAT_JSON


def test_output_format_defaults_to_table() -> None:
    """默认（两个旗标都没传）保持人类表格，不做非 TTY 自动切 JSON。"""
    assert output_format_of() == OUTPUT_FORMAT_TABLE
    assert output_format_of(output="table") == OUTPUT_FORMAT_TABLE


def test_output_format_json_wins_over_default_table_value() -> None:
    """Typer 无法区分 ``table`` 是默认还是显式，因此默认值不否决 ``--json``。"""
    assert output_format_of(output=OUTPUT_FORMAT_TABLE, as_json=True) == OUTPUT_FORMAT_JSON


def test_output_format_accepts_enum_member() -> None:
    """``--output`` 用枚举声明（click 由此导出 choices），解析时须按成员取值判定。"""
    assert output_format_of(output=OutputFormat.json) == OUTPUT_FORMAT_JSON
    assert output_format_of(output=OutputFormat.table) == OUTPUT_FORMAT_TABLE


def test_output_format_ignores_foreign_output_value() -> None:
    """``iar ask --output <目录>`` 复用同名旗标：非格式取值忽略，不误切机器模式。"""
    assert output_format_of(output="/tmp/outdir") == OUTPUT_FORMAT_TABLE
    assert output_format_of(output=None, as_json=False) == OUTPUT_FORMAT_TABLE


@pytest.mark.parametrize(
    ("parsed", "expected"),
    [
        (argparse.Namespace(), OUTPUT_FORMAT_TABLE),
        (argparse.Namespace(as_json=True), OUTPUT_FORMAT_JSON),
        (argparse.Namespace(output="json"), OUTPUT_FORMAT_JSON),
        (argparse.Namespace(output="table", as_json=True), OUTPUT_FORMAT_JSON),
        (argparse.Namespace(output="table"), OUTPUT_FORMAT_TABLE),
    ],
)
def test_resolve_output_format_reads_parsed_namespace(
    parsed: argparse.Namespace,
    expected: str,
) -> None:
    """中央 dispatcher 只从 ``parsed.output`` / ``parsed.as_json`` 解析一次形态。"""
    assert resolve_output_format(parsed) == expected


def test_emit_json_mode_prints_only_json_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """机器模式 stdout 是可直接 ``json.loads`` 的单一文档，且中文不转义。"""
    emit([{"repo_id": "keda", "标题": "保持原文"}], fmt=OUTPUT_FORMAT_JSON)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == [{"repo_id": "keda", "标题": "保持原文"}]


def test_emit_human_mode_uses_renderer_and_skips_json(capsys: pytest.CaptureFixture[str]) -> None:
    """人类模式只调用渲染器，不产出 JSON。"""
    rendered: list[str] = []
    emit(
        {"ignored": True},
        fmt=OUTPUT_FORMAT_TABLE,
        human_renderer=lambda: rendered.append("rendered"),
    )
    captured = capsys.readouterr()
    assert rendered == ["rendered"]
    assert captured.out == ""


def test_emit_ndjson_writes_one_record_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    """日志流按 NDJSON 输出：每行一条可独立解析的记录。"""
    emit_ndjson([{"line": "a"}, {"line": "b"}])
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert [json.loads(line)["line"] for line in lines] == ["a", "b"]


def test_json_literal_embeds_values_without_markup() -> None:
    """``json_literal`` 供人类文本内嵌取值，序列化口径与 emit 同源。"""
    assert json_literal({"path": "/tmp/示例"}) == '{"path": "/tmp/示例"}'


def test_cli_error_derives_error_token_from_code() -> None:
    """省略 ``error`` 时按退出码推导机器可读错误名。"""
    error = CliError("no such repo", code=ExitCode.NOT_FOUND)
    assert error.error == "not_found"
    assert CliError("boom", code=ExitCode.USAGE).error == "usage_error"
    assert CliError("boom").error == "error"


def test_error_envelope_has_exactly_the_contract_fields() -> None:
    """FR-4 envelope 的字段集合是固定契约，不多也不少。"""
    error = CliError(
        "no such repo",
        code=ExitCode.NOT_FOUND,
        suggestion="iar registry list",
        retryable=False,
    )
    assert error_envelope(error) == {
        "error": "not_found",
        "message": "no such repo",
        "suggestion": "iar registry list",
        "retryable": False,
        "exit_code": 3,
    }


def test_render_cli_error_json_mode_writes_envelope_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """机器模式错误落 stderr 且 stdout 保持干净，返回语义退出码。"""
    code = render_cli_error(
        CliError("no such repo", code=ExitCode.NOT_FOUND, suggestion="iar registry list"),
        fmt=OUTPUT_FORMAT_JSON,
    )
    captured = capsys.readouterr()
    assert code == 3
    assert captured.out == ""
    assert json.loads(captured.err)["exit_code"] == 3


def test_render_cli_error_human_mode_prints_plain_text_with_next_step(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人类模式给出等价文本：错误名、消息、``next:`` 建议，且字面方括号不被吞。"""
    code = render_cli_error(
        CliError(
            "TOML section [agent_runner] is broken",
            code=ExitCode.USAGE,
            suggestion="iar config migrate --repo .",
        ),
        fmt=OUTPUT_FORMAT_TABLE,
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "usage_error: TOML section [agent_runner] is broken" in captured.err
    assert "next: iar config migrate --repo ." in captured.err


def test_route_logs_to_stderr_keeps_machine_stdout_pure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """把挂在 stdout 上的日志处理器改绑到 stderr，进度不再混进 JSON。"""
    stdout_handler = logging.StreamHandler(sys.stdout)
    app_logger = logging.getLogger("iar_contract_test")
    app_logger.addHandler(stdout_handler)
    app_logger.propagate = False
    app_logger.setLevel(logging.INFO)
    try:
        route_logs_to_stderr()
        app_logger.info("progress line")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "progress line" in captured.err
        assert stdout_handler.stream is sys.stderr
    finally:
        stdout_handler.setStream(sys.stdout)
        app_logger.removeHandler(stdout_handler)


def test_json_serialization_stays_in_cli_output() -> None:
    """§7.4 JSON 旁路守卫：序列化只出现在 ``cli_output.py``。"""
    api_root = Path(sys.modules["backend.api"].__file__).parent
    offenders: list[str] = []
    for source_path in sorted(api_root.rglob("*.py")):
        if source_path.name == "cli_output.py":
            continue
        code_lines = source_path.read_text(encoding="utf-8").splitlines()
        for line_number, source_line in enumerate(code_lines, start=1):
            if source_line.lstrip().startswith("#"):
                continue
            if "json.dumps" in source_line or "print_json" in source_line:
                offenders.append(f"{source_path.name}:{line_number}")
    assert offenders == []


def test_machine_output_requested_detects_raw_tokens() -> None:
    """解析失败时只能靠原始 token 探测机器模式：三种写法都算显式声明。"""
    assert machine_output_requested(["logs", "--json"])
    assert machine_output_requested(["logs", "--output", "json"])
    assert machine_output_requested(["logs", "--output=json"])
    assert machine_output_requested(["logs", "--output", "JSON"])
    assert not machine_output_requested(["logs", "--output", "table"])
    assert not machine_output_requested(["logs"])
    assert not machine_output_requested(["logs", "--output"])


def test_parse_failure_in_machine_mode_renders_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析期失败（旗标写错）在机器模式也落 FR-4 envelope，而非 click 纯文本。"""
    exit_code = typer_main(["logs", "--repo-id", "keda", "--issue", "1", "--json", "--bogus-flag"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    envelope = json.loads(captured.err)
    assert set(envelope) == {"error", "exit_code", "message", "retryable", "suggestion"}
    assert envelope["error"] == "usage_error"
    assert envelope["exit_code"] == 2
    assert envelope["suggestion"] == "kc logs --help"
    assert "--bogus-flag" in envelope["message"]


def test_parse_failure_in_human_mode_keeps_click_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人类模式的解析失败保持 click 原文，零回归。"""
    exit_code = typer_main(["logs", "--repo-id", "keda", "--issue", "1", "--bogus-flag"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "No such option: --bogus-flag" in captured.err
