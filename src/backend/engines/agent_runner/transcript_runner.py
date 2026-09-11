"""Transcript runner implementation for agent deliberation.

This module provides the subprocess-based transcript runner used to execute
agent commands during deliberation sessions and stream their output back to
the caller.

命令与输出协议完全由声明式注册表决定：
:func:`build_agent_invocation`（profile ``"deliberate"``）组装 argv，
注入的协议注册表解析 ``output_protocol`` 得到中继实现——本模块不再
写死任何 agent 名或命令分支。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backend.core.shared.interfaces.agent_output_protocol import (
    IAgentOutputProtocolRegistry,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_deliberation import DeliberationEvent
from backend.core.shared.models.agent_runner import AppConfig, CommandResult
from backend.core.shared.models.agent_spec import AGENT_PROFILE_DELIBERATE
from backend.core.use_cases.agent_invocation import build_agent_invocation


class SubprocessTranscriptRunner:
    """Run agents and emit deliberation events.

    Implements ``IAgentTranscriptRunner`` via duck typing.
    """

    def __init__(
        self,
        config: AppConfig,
        protocol_registry: IAgentOutputProtocolRegistry,
    ) -> None:
        """注入应用配置与输出协议注册表（组装根负责构造）。"""
        self._config = config
        self._protocol_registry = protocol_registry

    def run(
        self,
        agent_name: str,
        prompt: str,
        *,
        cwd: Path,
        event_sink: "Callable[[DeliberationEvent], None]",
        output_sink: "Callable[[str], None] | None" = None,
        display_sink: "Callable[[str], None] | None" = None,
    ) -> "CommandResult":
        """Run an agent and emit events.

        Streams agent stdout to the terminal in real time while
        collecting it for the deliberation transcript. When
        ``output_sink`` is provided, rendered text chunks are passed
        to it as they arrive. When ``display_sink`` is provided, the
        agent's stderr (its human-readable reasoning/tool log) is routed
        to it for live display only, without being collected into the
        transcript.
        """
        _ = event_sink
        invocation = build_agent_invocation(
            agent_name,
            AGENT_PROFILE_DELIBERATE,
            prompt,
            cwd,
            self._config,
        )
        protocol = self._protocol_registry.resolve(invocation.output_protocol)
        # 提示词全文始终交给协议：流式协议（claude-stream-json）会从 argv
        # 剥离后改经 stdin 投递，避免 transcript 增长后 ``Argument list
        # too long``；stdin 投递的 profile 由协议写 stdin；argv_tail 的
        # plain profile 则提示词留在 argv、不写 stdin——三种情况都由
        # ``prompt_delivery`` 声明驱动，协议实现不做猜测。
        request = OutputRelayRequest(
            argv=invocation.argv,
            cwd=cwd,
            prompt_text=prompt,
            prompt_delivery=invocation.prompt_delivery,
            collect_stdout=True,
            output_sink=output_sink,
            display_sink=display_sink,
        )
        return protocol.relay(request)


def create_transcript_runner(
    config: AppConfig | None = None,
    protocol_registry: IAgentOutputProtocolRegistry | None = None,
) -> SubprocessTranscriptRunner:
    """Create a transcript runner instance.

    Args:
        config: 应用配置；``None`` 时使用内置默认注册表（无仓库级覆盖）。
        protocol_registry: 输出协议注册表；``None`` 时使用进程级单例。
    """
    from backend.engines.agent_runner.output_protocols import (
        get_output_protocol_registry,
    )

    return SubprocessTranscriptRunner(
        config or AppConfig(),
        protocol_registry or get_output_protocol_registry(),
    )
