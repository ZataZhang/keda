"""评审调用边界测试：评审复用执行器的 judge 形态（空目录 + 禁工具），不再走 provider 工厂。

外部边界只替换进程调用本身（``run_agent_with_prompt``）与 PATH 探测；
其余（argv 装配、预设解析、profile 校验）走真实代码。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.core.shared.models.agent_spec import AGENT_PROFILE_JUDGE, BUILTIN_AGENT_SPECS
from backend.core.shared.models.model_evaluation import EvaluationScoreStatus
from backend.engines.model_evaluation import judge_client
from backend.infrastructure.config.agent_runner_settings_models import AgentRunnerPresetSettings
from backend.infrastructure.config.settings import AgentRunnerSettings


class _FakeResult:
    """最小 CommandResult 替身：只带评审边界读取的字段。"""

    def __init__(self, *, return_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.token_usage = None


def _settings() -> AgentRunnerSettings:
    return AgentRunnerSettings(
        presets={
            "judge-claude": AgentRunnerPresetSettings(agent="claude", model="model-judge"),
            "judge-codex": AgentRunnerPresetSettings(agent="codex", model="gpt-x"),
        }
    )


def test_tools_disabled_judge_profile_declared_only_where_verified() -> None:
    """只有声明了"禁用全部工具"开关的 agent 才有 judge 形态；codex/kimi 不得假声明。"""
    with_judge = {
        name for name, spec in BUILTIN_AGENT_SPECS.items() if AGENT_PROFILE_JUDGE in spec.profiles
    }
    assert with_judge == {"claude", "codebuddy", "qoder", "pi"}
    for name in with_judge:
        judge_profile = BUILTIN_AGENT_SPECS[name].profiles[AGENT_PROFILE_JUDGE]
        assert judge_profile.read_only is True
        assert "--dangerously-skip-permissions" not in judge_profile.args
        assert ("--tools", "") == judge_profile.args[:2] or "--no-tools" in judge_profile.args


def test_probe_rejects_agent_without_judge_profile() -> None:
    availability = judge_client.probe_judge_availability("judge-codex", _settings())
    assert availability.available is False
    assert "judge 形态" in (availability.reason or "")


def test_probe_rejects_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_client.shutil, "which", lambda binary: None)
    availability = judge_client.probe_judge_availability("judge-claude", _settings())
    assert availability.available is False
    assert "claude" in (availability.reason or "")


def test_probe_accepts_isolated_agent_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_client.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    availability = judge_client.probe_judge_availability("judge-claude", _settings())
    assert availability.available is True
    assert (availability.agent, availability.model) == ("claude", "model-judge")


def test_invoke_runs_judge_profile_in_empty_dir_with_payload_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评审只在空临时目录里、以 judge profile、逐字节原样 payload 调用，调用后目录被清除。"""
    captured: dict[str, Any] = {}

    def _fake_run(agent_name: str, prompt: str, cwd: Path, *args: Any, **kwargs: Any) -> Any:
        captured.update(agent=agent_name, prompt=prompt, cwd=cwd, kwargs=kwargs)
        captured["cwd_entries"] = list(cwd.iterdir())
        return _FakeResult(stdout='  {"scores": {}}\n')

    monkeypatch.setattr(judge_client, "run_agent_with_prompt", _fake_run)
    monkeypatch.setattr(judge_client, "create_process_runner", lambda: object())

    outcome = judge_client.invoke_judge(
        preset_name="judge-claude",
        payload_text="PAYLOAD-BYTES",
        settings=_settings(),
        timeout_seconds=30,
    )

    assert outcome.error_status is None
    assert outcome.response_text == '{"scores": {}}'
    assert captured["agent"] == "claude"
    assert captured["prompt"] == "PAYLOAD-BYTES"
    assert captured["kwargs"]["profile"] == AGENT_PROFILE_JUDGE
    assert captured["kwargs"]["timeout_seconds"] == 30
    assert captured["cwd_entries"] == []
    assert not captured["cwd"].exists()


def test_invoke_maps_nonzero_exit_to_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        judge_client,
        "run_agent_with_prompt",
        lambda *args, **kwargs: _FakeResult(return_code=2, stderr="auth required"),
    )
    monkeypatch.setattr(judge_client, "create_process_runner", lambda: object())
    outcome = judge_client.invoke_judge(
        preset_name="judge-claude", payload_text="p", settings=_settings(), timeout_seconds=30
    )
    assert outcome.error_status is EvaluationScoreStatus.provider_error
    assert outcome.response_text == "auth required"


def test_invoke_maps_timeout_exception_to_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_timeout(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("agent timed out")

    monkeypatch.setattr(judge_client, "run_agent_with_prompt", _raise_timeout)
    monkeypatch.setattr(judge_client, "create_process_runner", lambda: object())
    outcome = judge_client.invoke_judge(
        preset_name="judge-claude", payload_text="p", settings=_settings(), timeout_seconds=30
    )
    assert outcome.error_status is EvaluationScoreStatus.timeout


def test_invoke_unknown_preset_is_provider_error_without_process_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _never_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("预设不存在时不得起进程")

    monkeypatch.setattr(judge_client, "run_agent_with_prompt", _never_run)
    outcome = judge_client.invoke_judge(
        preset_name="ghost", payload_text="p", settings=_settings(), timeout_seconds=30
    )
    assert outcome.error_status is EvaluationScoreStatus.provider_error
