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
from backend.api.cli_parsed_commands.agent import run_agent_doctor_command
from backend.api.cli_parser import build_parser
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import AgentProfileSpec, AgentSpec


def _make_context(**parsed_kwargs: Any) -> ParsedCommandContext:
    """构造 doctor 命令的最小上下文（doctor 只读，其余依赖不触达）。"""
    defaults: dict[str, Any] = {
        "agent_names": [],
        "all_profiles": False,
        "json_output": False,
        "protocols": False,
        "prompt": "golden-prompt",
    }
    defaults.update(parsed_kwargs)
    return ParsedCommandContext(
        parsed=argparse.Namespace(**defaults),
        process_runner=None,
        runner_settings=None,
        repo_id=None,
        repo_override=None,
        github_client_factory=None,
    )


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
        _make_context(agent_names=["claude"], all_profiles=True, json_output=True)
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
    """未注册 agent 非零退出，并列出全部已注册名。"""
    exit_code = run_agent_doctor_command(_make_context(agent_names=["ghost"]))
    assert exit_code == 1
    assert "not registered" in capsys.readouterr().err


def test_doctor_missing_executable_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """可执行文件不在 PATH 时非零退出。"""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        exit_code = run_agent_doctor_command(_make_context(agent_names=["claude"]))
    finally:
        monkeypatch.undo()
    assert exit_code == 1
    assert "not found in PATH" in capsys.readouterr().err


def test_doctor_missing_profile_fails(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """agent 未声明所查用途时非零退出，并列出已声明用途。"""
    stub_app_config.agents["partial"] = AgentSpec(
        bin="partial-bin",
        label="agent/partial",
        label_color="5319E7",
        label_description="",
        profiles={"deliberate": AgentProfileSpec(prompt_delivery="stdin")},
    )
    exit_code = run_agent_doctor_command(_make_context(agent_names=["partial"]))
    assert exit_code == 1
    assert "has no 'run' profile" in capsys.readouterr().err


def test_doctor_unregistered_protocol_fails(
    stub_app_config: AppConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """profile 引用未注册输出协议时非零退出，不降级成 plain。"""
    stub_app_config.agents["badproto"] = AgentSpec(
        bin="badproto-bin",
        label="agent/badproto",
        label_color="5319E7",
        label_description="",
        profiles={"run": AgentProfileSpec(output_protocol="no-such-protocol")},
    )
    exit_code = run_agent_doctor_command(_make_context(agent_names=["badproto"]))
    assert exit_code == 1
    assert "no-such-protocol" in capsys.readouterr().err


def test_doctor_no_agent_name_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """不给 agent 名（且非 --protocols）时非零退出。"""
    exit_code = run_agent_doctor_command(_make_context())
    assert exit_code == 1


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
    exit_code = run_agent_doctor_command(_make_context(agent_names=["noguard"], json_output=True))
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
