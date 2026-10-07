"""``iar agent doctor`` CLI 正负路径测试（rv-5 的自动化部分）。

doctor 是只读自检入口：打印每个 agent/profile 解析出的完整 argv 与
提示词投递方式，可执行文件存在且协议可解析时以退出码 0 报告"可用"。
负路径（缺可执行文件、未注册协议、缺 profile、未注册 agent）必须
非零退出，绝不静默降级。
"""

from __future__ import annotations

import argparse
import json
import shutil
from typing import Any

import pytest

from backend.api import cli  # noqa: F401  先导入调度器，避免解析命令模块的循环导入
from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import (
    OUTPUT_FORMAT_JSON,
    OUTPUT_FORMAT_TABLE,
    CliError,
    render_cli_error,
)
from backend.api.cli_parsed_commands.agent import run_agent_doctor_command
from backend.api.cli_parser import build_parser
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AgentProfileSpec, AgentSpec


def _make_context(**parsed_kwargs: Any) -> ParsedCommandContext:
    """构造 doctor 命令的最小上下文（doctor 只读，其余依赖不触达）。

    ``as_json`` 是 CLI 旗标名，同时决定中央 dispatcher 会注入的
    ``output_format``——测试直接建上下文时也要把这条链补上。
    """
    defaults: dict[str, Any] = {
        "agent_names": [],
        "all_profiles": False,
        "as_json": False,
        "protocols": False,
        "prompt": "golden-prompt",
    }
    defaults.update(parsed_kwargs)
    as_json = bool(defaults["as_json"])
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=None,
        output_format=OUTPUT_FORMAT_JSON if as_json else OUTPUT_FORMAT_TABLE,
    )


def _doctor_exit_code_through_renderer(
    context: ParsedCommandContext,
    capsys: pytest.CaptureFixture[str],
) -> int:
    """执行 doctor 并把失败交给中央渲染点，等价于真实进程里的一条退出路径。

    handler 现在只负责抛出带类别的 :class:`CliError`；落码与 stderr 归属由
    ``cli.py`` 调用 ``render_cli_error`` 完成，因此测试也要走完这后半段，
    而不是只断言异常。
    """
    try:
        return run_agent_doctor_command(context)
    except CliError as error:
        return render_cli_error(error, fmt=context.output_format)
    raise AssertionError("expected CliError")  # pragma: no cover - 负路径守卫


@pytest.fixture()
def stub_app_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """隔离真实 config.toml，用内置注册表驱动 doctor。"""
    config = AppConfig()
    monkeypatch.setattr("backend.api.cli_parsed_commands.agent.build_app_config", lambda: config)
    return config


@pytest.fixture(autouse=True)
def stub_executables_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认所有可执行文件"存在"，让用例按需单独控制 which 结果。"""
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")


# ---------------------------------------------------------------------------
# 正路径
# ---------------------------------------------------------------------------


def test_doctor_json_all_profiles_matches_golden(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doctor --json 输出稳定排序的 agent/profile/argv/prompt_delivery。"""
    exit_code = run_agent_doctor_command(
        _make_context(agent_names=["claude"], all_profiles=True, as_json=True)
    )
    assert exit_code == 0
    entries = json.loads(capsys.readouterr().out)
    assert [entry["profile"] for entry in entries] == [
        "deliberate",
        "generate",
        "repl",
        "run",
    ]
    run_entry = entries[-1]
    assert run_entry["agent"] == "claude"
    assert run_entry["argv"] == [
        "claude",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "golden-prompt",
    ]
    assert run_entry["prompt_delivery"] == "argv_tail"


def test_doctor_human_output_prints_argv_and_delivery(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人类可读输出含 argv（shlex 转义）与提示词投递方式。"""
    exit_code = run_agent_doctor_command(_make_context(agent_names=["pi"]))
    assert exit_code == 0
    output = capsys.readouterr()
    combined = output.out + output.err
    assert "pi" in combined
    assert "--mode" in combined and "json" in combined
    assert "stdin" in combined


def test_doctor_protocols_lists_registered_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--protocols 列出注册表里的全部协议 id。"""
    exit_code = run_agent_doctor_command(_make_context(protocols=True))
    assert exit_code == 0
    listed_ids = capsys.readouterr().out.split()
    assert {"plain", "claude-stream-json", "pi-json-lines"} <= set(listed_ids)


# ---------------------------------------------------------------------------
# 负路径（必须真的能变红）
# ---------------------------------------------------------------------------


def test_doctor_unknown_agent_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """未注册 agent → NOT_FOUND(3)，列出全部已注册名，stdout 保持为空。"""
    exit_code = _doctor_exit_code_through_renderer(_make_context(agent_names=["ghost"]), capsys)
    assert exit_code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert "not registered" in captured.err
    assert captured.out == ""


def test_doctor_missing_executable_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """可执行文件不在 PATH 时 → NOT_FOUND(3)。"""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        exit_code = _doctor_exit_code_through_renderer(
            _make_context(agent_names=["claude"]), capsys
        )
    finally:
        monkeypatch.undo()
    assert exit_code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert "not found in PATH" in captured.err
    assert "kc agent list" in captured.err


def test_doctor_missing_profile_fails(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """agent 未声明所查用途时 → NOT_FOUND(3)，并列出已声明用途。"""
    stub_app_config.agents["partial"] = AgentSpec(
        bin="partial-bin",
        label="agent/partial",
        label_color="5319E7",
        label_description="",
        profiles={"deliberate": AgentProfileSpec(prompt_delivery="stdin")},
    )
    exit_code = _doctor_exit_code_through_renderer(_make_context(agent_names=["partial"]), capsys)
    assert exit_code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert "has no 'run' profile" in captured.err
    assert "kc agent doctor partial --all-profiles" in captured.err


def test_doctor_unregistered_protocol_fails(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """profile 引用未注册输出协议时 → NOT_FOUND(3)，不降级成 plain。"""
    stub_app_config.agents["badproto"] = AgentSpec(
        bin="badproto-bin",
        label="agent/badproto",
        label_color="5319E7",
        label_description="",
        profiles={"run": AgentProfileSpec(output_protocol="no-such-protocol")},
    )
    exit_code = _doctor_exit_code_through_renderer(_make_context(agent_names=["badproto"]), capsys)
    assert exit_code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert "no-such-protocol" in captured.err
    assert "kc agent doctor --protocols" in captured.err


def test_doctor_failure_json_envelope_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """机器模式下 doctor 失败只落 stderr envelope，stdout 不吐半个 JSON。"""
    exit_code = _doctor_exit_code_through_renderer(
        _make_context(agent_names=["ghost"], as_json=True), capsys
    )
    assert exit_code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert captured.out == ""
    envelope = json.loads(captured.err)
    assert envelope["exit_code"] == int(ExitCode.NOT_FOUND)
    assert envelope["error"] == "not_found"
    assert envelope["suggestion"] == "kc agent list"
    assert envelope["retryable"] is False


def test_doctor_no_agent_name_fails() -> None:
    """不给 agent 名（且非 --protocols）时抛用法错误，由中央调度落成退出码 2。"""
    with pytest.raises(CliError) as exc_info:
        run_agent_doctor_command(_make_context())

    assert exc_info.value.code == ExitCode.USAGE
    assert exc_info.value.suggestion == "kc agent list"


def test_doctor_warns_writable_profile_without_sandbox(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """可写用途缺沙箱/审批标记时给 WARN（走 stderr，不污染 stdout JSON）。"""
    stub_app_config.agents["noguard"] = AgentSpec(
        bin="noguard-bin",
        label="agent/noguard",
        label_color="5319E7",
        label_description="",
        profiles={"run": AgentProfileSpec(prompt_delivery="stdin")},
    )
    exit_code = run_agent_doctor_command(_make_context(agent_names=["noguard"], as_json=True))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    # stdout 是纯 JSON，快照 diff 不被告警污染
    assert json.loads(captured.out)[0]["agent"] == "noguard"


# ---------------------------------------------------------------------------
# 解析层：choices 来自注册表
# ---------------------------------------------------------------------------


def test_parser_accepts_registered_agent_name() -> None:
    """agent doctor 的位置参数接受注册表里的任意 agent（含 pi）。"""
    parsed = build_parser().parse_args(["agent", "doctor", "pi"])
    assert parsed.command == "agent doctor"
    assert parsed.agent_names == ["pi"]


def test_parser_rejects_unregistered_agent_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未注册的 agent 名在解析层直接被 choices 拒绝。"""
    from backend.core.use_cases.agent_invocation import resolve_registered_agents
    from backend.engines.agent_runner.factories import (
        build_app_config_from_settings,
        load_fresh_agent_runner_settings,
    )

    config = build_app_config_from_settings(load_fresh_agent_runner_settings())
    registered = resolve_registered_agents(config)
    ghost_name = next(
        (name for name in ("definitely-not-registered",) if name not in registered),
        None,
    )
    assert ghost_name is not None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["agent", "doctor", ghost_name])
    assert "invalid choice" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 模型预设视角（--preset / --lifecycle / agent presets）
# ---------------------------------------------------------------------------


def _preset_app_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """带 ``plan`` 预设（codebuddy + glm + max）的隔离配置。"""
    from backend.core.shared.models.agent_model_preset import AgentModelPreset

    config = AppConfig(
        agent_presets={
            "plan": AgentModelPreset(
                agent="codebuddy", model="glm-5.3-flash", reasoning_effort="max"
            ),
        }
    )
    monkeypatch.setattr("backend.api.cli_parsed_commands.agent.build_app_config", lambda: config)
    return config


def test_doctor_preset_view_injects_model_and_effort_args(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rv-1：doctor --preset 打印的 argv 同时含模型与推理档参数。"""
    _preset_app_config(monkeypatch)
    exit_code = run_agent_doctor_command(
        _make_context(agent_names=["codebuddy"], as_json=True, preset="plan")
    )
    assert exit_code == 0
    entries = json.loads(capsys.readouterr().out)
    argv = entries[0]["argv"]
    assert "--model" in argv
    assert "glm-5.3-flash" in argv
    assert "--settings" in argv
    assert '{"reasoningEffort":"max"}' in argv
    assert entries[0]["preset"] == "plan"


def test_doctor_preset_missing_template_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rv-5：未声明模型模板的 agent 命中带模型的预设时指名报错 → USAGE(2)。"""
    _preset_app_config(monkeypatch)
    exit_code = _doctor_exit_code_through_renderer(
        _make_context(agent_names=["kimi"], as_json=True, preset="plan"), capsys
    )
    assert exit_code == int(ExitCode.USAGE)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "kimi" in captured.err


def test_doctor_preset_unknown_name_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
) -> None:
    _preset_app_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_doctor_command(
            _make_context(agent_names=["claude"], as_json=True, preset="ghost")
        )

    assert "ghost" in exc_info.value.message
    assert exc_info.value.code == ExitCode.USAGE


def test_doctor_preset_requires_preset_for_model_flags(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
) -> None:
    _preset_app_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_doctor_command(
            _make_context(agent_names=["claude"], as_json=True, model="glm-5.3-flash")
        )

    assert "--preset" in exc_info.value.message
    assert exc_info.value.code == ExitCode.USAGE


def test_doctor_lifecycle_view_reports_bound_stage(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rv-9：doctor --lifecycle verifier 打印绑定的 agent 与模型参数。"""
    from backend.core.shared.models.agent_model_preset import AgentModelPreset
    from backend.core.shared.models.lifecycle_agent import LifecycleAgentsConfig

    config = AppConfig(
        agent_presets={
            "plan": AgentModelPreset(
                agent="codebuddy", model="glm-5.3-flash", reasoning_effort="max"
            ),
        },
        lifecycle_presets=LifecycleAgentsConfig(global_layer={"verifier": "plan"}),
    )
    monkeypatch.setattr("backend.api.cli_parsed_commands.agent.build_app_config", lambda: config)
    exit_code = run_agent_doctor_command(_make_context(as_json=True, lifecycle="verifier"))
    assert exit_code == 0
    entries = json.loads(capsys.readouterr().out)
    entry = entries[0]
    assert entry["agent"] == "codebuddy"
    assert "--model" in entry["argv"]
    assert "glm-5.3-flash" in entry["argv"]


def test_doctor_lifecycle_view_unbound_stage_has_no_model_args(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未绑定阶段：doctor --lifecycle 回落既有 agent，argv 不含模型参数。"""
    _preset_app_config(monkeypatch)
    exit_code = run_agent_doctor_command(_make_context(as_json=True, lifecycle="verifier"))
    assert exit_code == 0
    entry = json.loads(capsys.readouterr().out)[0]
    assert "--model" not in entry["argv"]


def test_doctor_lifecycle_rejects_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
    stub_executables_exist,  # noqa: ANN001
) -> None:
    _preset_app_config(monkeypatch)
    with pytest.raises(CliError) as exc_info:
        run_agent_doctor_command(_make_context(as_json=True, lifecycle="not-a-stage"))

    assert exc_info.value.code == ExitCode.USAGE
    assert exc_info.value.suggestion


def test_parser_accepts_doctor_preset_and_lifecycle_flags() -> None:
    namespace = build_parser().parse_args(
        ["agent", "doctor", "codebuddy", "--json", "--preset", "plan", "--lifecycle", "verifier"]
    )
    assert namespace.preset == "plan"
    assert namespace.lifecycle == "verifier"
    assert namespace.reasoning_effort is None


def test_parser_accepts_runner_model_preset_flags() -> None:
    namespace = build_parser().parse_args(
        [
            "run",
            "--preset",
            "plan",
            "--model",
            "glm-5.3-flash",
            "--reasoning-effort",
            "max",
        ]
    )
    assert namespace.preset == "plan"
    assert namespace.model == "glm-5.3-flash"
    assert namespace.reasoning_effort == "max"
