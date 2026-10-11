"""结构化 JSON Lines agent 协议共用的进程中继。"""

from __future__ import annotations

from collections.abc import Callable

from backend.core.shared.interfaces.agent_output_protocol import OutputRelayRequest
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import PROMPT_DELIVERY_STDIN
from backend.infrastructure.agent_stream_usage import StreamUsageCollector
from backend.infrastructure.process_runner import run_filtered_claude_stream


def relay_structured_json_lines(
    request: OutputRelayRequest,
    *,
    protocol_id: str,
    line_renderer: Callable[[str], str],
) -> CommandResult:
    """运行结构化 JSONL 子进程、渲染可读输出并采集会话观察值。"""
    usage_collector = StreamUsageCollector()
    completed = run_filtered_claude_stream(
        request.argv,
        cwd=request.cwd,
        timeout=request.timeout,
        inactivity_timeout=request.inactivity_timeout,
        collect_stdout=request.collect_stdout,
        prompt_text=(
            request.prompt_text if request.prompt_delivery == PROMPT_DELIVERY_STDIN else None
        ),
        output_sink=request.output_sink,
        display_sink=request.display_sink,
        label=request.label,
        usage_collector=usage_collector,
        line_renderer=line_renderer,
    )
    return CommandResult(
        command=request.argv,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output_protocol=protocol_id,
        token_usage=usage_collector.usage,
        session_id=usage_collector.session_id,
        reported_model=usage_collector.reported_model,
    )


__all__ = ["relay_structured_json_lines"]
