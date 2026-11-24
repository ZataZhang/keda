"""主执行路径的提示词投递与 Windows 命令行超长报错（Issue #423）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILE_RUN,
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
)
from backend.core.use_cases.agent_runner_failure import AgentUnavailableError
from backend.core.use_cases.run_agent_once import run_agent_with_prompt
from tests.conftest import FakeProcessRunner


class _ProtocolRecordingRunner(FakeProcessRunner):
    """额外记录每次调用的输出协议。"""

    def __init__(self) -> None:
        super().__init__()
        self.output_protocols: list[str | None] = []

    def run(self, command, **kwargs):  # noqa: ANN001, ANN201 - 替身签名与协议一致
        self.output_protocols.append(kwargs.get("output_protocol"))
        return super().run(command, **kwargs)


def test_claude_run_hands_prompt_to_stream_protocol_via_stdin(tmp_path: Path) -> None:
    """claude 流式协议的 run 路径把提示词交给协议经 stdin 投递，不受命令行长度限制。"""
    runner = _ProtocolRecordingRunner()
    long_prompt = "x" * 40_000

    run_agent_with_prompt(
        "claude",
        long_prompt,
        tmp_path,
        runner,
        config=AppConfig(),
        profile=AGENT_PROFILE_RUN,
    )

    assert runner.output_protocols[-1] == CLAUDE_STREAM_JSON_PROTOCOL_ID
    assert runner.input_texts[-1] == long_prompt


def test_command_line_too_long_is_not_reported_as_missing_bin(tmp_path: Path) -> None:
    """Windows 命令行超长（WinError 206）照实报出，不再误报成 command not found。"""

    class _TooLongRunner(FakeProcessRunner):
        def run(self, command, **kwargs):  # noqa: ANN001, ANN201 - 替身签名与协议一致
            error = FileNotFoundError("The filename or extension is too long")
            error.winerror = 206
            raise error

    with pytest.raises(AgentUnavailableError) as exc_info:
        run_agent_with_prompt(
            "claude",
            "Do the work.",
            tmp_path,
            _TooLongRunner(),
            config=AppConfig(),
            profile=AGENT_PROFILE_RUN,
        )

    message = str(exc_info.value)
    assert "WinError 206" in message
    assert "command not found" not in message
