"""工具默认模型快照的配置隔离、执行时序与调用账本集成验证。"""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from backend.core.shared.models.agent_model_observation import AgentModelConfigSnapshot
from backend.core.use_cases.agent_invocation_tracing import (
    InvocationStartRequest,
    InvocationTraceContext,
    bound_invocation_trace_context,
    start_invocation,
    finish_invocation,
)
from backend.engines.agent_runner.output_protocols import get_output_protocol_registry
from backend.engines.agent_runner.protocol_routing_runner import ProtocolRoutingProcessRunner
from backend.infrastructure.agent_model_config_snapshot import read_agent_model_config_snapshot
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from backend.infrastructure.process_runner import SubprocessRunner, CommandFailedError


@pytest.fixture
def isolated_agent_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """确保任何用例都不读取操作者真实凭据或默认配置。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    for variable_name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "KIMI_CODE_HOME", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(variable_name, raising=False)
    for variable_name in list(os.environ):
        if variable_name.startswith("KIMI_MODEL_"):
            monkeypatch.delenv(variable_name)
    return tmp_path


@pytest.mark.parametrize(
    "source_case",
    [
        ("codex", ".codex/config.toml", 'model = "gpt-6-luna"', "gpt-6-luna", False),
        ("claude", ".claude/settings.json", '{"model":"sonnet"}', "sonnet", True),
        (
            "codebuddy",
            ".codebuddy/settings.json",
            '{"model":"deepseek-v4.1-flash"}',
            "deepseek-v4.1-flash",
            False,
        ),
        (
            "qodercn",
            ".qoder-cn/settings.json",
            '{"model":{"name":"qmodel_38max"}}',
            "qmodel_38max",
            True,
        ),
        (
            "kimi",
            ".kimi/config.toml",
            'default_model="alias"\n[models.alias]\nmodel="Kimi-K2.8-Preview"',
            "Kimi-K2.8-Preview",
            False,
        ),
    ],
)
def test_default_model_sources(
    isolated_agent_home: Path,
    source_case: tuple[str, str, str, str, bool],
) -> None:
    """各工具只返回模型标识与来源，不把整个配置带进账本。"""
    executable, config_name, config_text, expected_model, is_alias = source_case
    config_path = isolated_agent_home / config_name
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config_text, encoding="utf-8")
    snapshot = read_agent_model_config_snapshot((executable,), isolated_agent_home / "workspace")
    assert snapshot is not None
    assert snapshot.model_id == expected_model
    assert snapshot.is_alias is is_alias
    assert set(asdict(snapshot)) == {"model_id", "source", "is_alias"}


@pytest.mark.parametrize(
    "override",
    [
        "--model=x",
        "-mversion2",
        "--config=x",
        "-cmodel=version2",
        "--resume",
        "resume",
        "--settings=x",
    ],
)
def test_overrides_do_not_masquerade_as_default(
    isolated_agent_home: Path,
    override: str,
) -> None:
    """有未解析覆盖参数时，宁可不提供快照。"""
    config_path = isolated_agent_home / ".codebuddy/settings.json"
    config_path.parent.mkdir()
    config_path.write_text('{"model":"glm-5.3-flash"}', encoding="utf-8")
    assert read_agent_model_config_snapshot(("codebuddy", override), isolated_agent_home) is None


def test_codex_short_profile_and_claude_environment_conflict(
    isolated_agent_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """短 profile 覆盖和不确定的环境合并优先级都不产生错误归属。"""
    codex_config = isolated_agent_home / ".codex/config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text('model="gpt-6-luna"', encoding="utf-8")
    assert read_agent_model_config_snapshot(("codex", "-p", "other"), isolated_agent_home) is None
    claude_config = isolated_agent_home / ".claude/settings.json"
    claude_config.parent.mkdir()
    claude_config.write_text('{"env":{"ANTHROPIC_MODEL":"claude-sonnet-5-5"}}', encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    assert read_agent_model_config_snapshot(("claude",), isolated_agent_home) is None


def test_project_override_and_corrupt_configuration(isolated_agent_home: Path) -> None:
    """项目级配置覆盖用户配置；高优先级配置损坏时不回落到低优先级假值。"""
    user_config = isolated_agent_home / ".codebuddy/settings.json"
    user_config.parent.mkdir()
    user_config.write_text('{"model":"glm-5.3-flash"}', encoding="utf-8")
    workspace = isolated_agent_home / "workspace"
    project_config = workspace / ".codebuddy/settings.local.json"
    project_config.parent.mkdir(parents=True)
    project_config.write_text('{"model":"deepseek-v4.1-flash"}', encoding="utf-8")
    snapshot = read_agent_model_config_snapshot(("codebuddy",), workspace)
    assert snapshot.model_id == "deepseek-v4.1-flash"
    project_config.write_text("broken json", encoding="utf-8")
    assert read_agent_model_config_snapshot(("codebuddy",), workspace) is None


@pytest.mark.parametrize("protocol_id", ["plain", "claude-stream-json"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_snapshot_is_taken_before_process_and_survives_ledger(
    isolated_agent_home: Path,
    protocol_id: str,
    exit_code: int,
) -> None:
    """真实子进程改配置后，落库的仍是启动前快照；失败也保留配置且不冒充报告值。"""
    config_path = isolated_agent_home / ".codebuddy/settings.json"
    config_path.parent.mkdir()
    config_path.write_text('{"model":"glm-5.3-flash"}', encoding="utf-8")
    executable_path = isolated_agent_home / "codebuddy"
    executable_path.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(config_path)!r}).write_text('{{\"model\":\"changed-v2\"}}', encoding='utf-8')\n"
        'print(\'{"type":"result","usage":{"input_tokens":100}}\')\n'
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    executable_path.chmod(0o755)
    store = SqliteConsoleStore(isolated_agent_home / "console.db")
    trace_context = InvocationTraceContext(
        repo_id="repo", run_id="run", issue_number=1, store=store
    )
    runner = ProtocolRoutingProcessRunner(SubprocessRunner(), get_output_protocol_registry())
    with bound_invocation_trace_context(trace_context):
        observation = start_invocation(
            InvocationStartRequest(
                agent_name="codebuddy",
                phase="implementation",
                profile="run",
            )
        )
        try:
            command_result = runner.run(
                [str(executable_path)],
                cwd=isolated_agent_home,
                output_protocol=protocol_id,
                capture_output=True,
            )
        except CommandFailedError as exc:
            assert exit_code == 1
            finish_invocation(observation, exc=exc)
        else:
            assert exit_code == 0
            assert command_result.model_config_snapshot == AgentModelConfigSnapshot(
                model_id="glm-5.3-flash",
                source="codebuddy:user:settings.json",
                is_alias=False,
            )
            finish_invocation(observation, result=command_result)
    fresh_store = SqliteConsoleStore(isolated_agent_home / "console.db")
    finished = fresh_store.list_issue_invocation_events(repo_id="repo", issue_number=1, limit=500)[
        -1
    ]
    detail = json.loads(finished.detail_json)
    assert detail["model_config_snapshot"]["model_id"] == "glm-5.3-flash"
    assert detail["reported_model"] is None
    assert detail["requested_model"] is None
    assert detail["model_source"] == "unknown"
    from backend.core.use_cases.prd_invocation_detail import build_prd_invocation_detail

    invocation_detail = build_prd_invocation_detail(
        store=fresh_store, repo_id="repo", issue_number=1
    )
    assert invocation_detail.rows[0].model_config_snapshot == detail["model_config_snapshot"]
