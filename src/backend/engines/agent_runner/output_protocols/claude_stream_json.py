"""``claude-stream-json`` 输出协议：Claude 流式 JSON 中继。

包裹现有 :func:`run_filtered_claude_stream`，行为不变：

- 提供 ``prompt_text`` 时（辩论/transcript 路径），先从 argv 剥离
  ``-p`` 与尾部提示词，再经 stdin 投递，避免 transcript 增长后
  ``Argument list too long``；
- 未提供 ``prompt_text`` 时（主执行路径），提示词留在 argv 尾部，
  stdin 直接关闭——与历史 ``SubprocessRunner`` 内部分支一致。
"""

from __future__ import annotations

from backend.core.shared.interfaces.agent_output_protocol import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.infrastructure.process_runner import run_filtered_claude_stream


class ClaudeStreamJsonOutputProtocol:
    """Claude ``stream-json`` 事件流渲染协议。"""

    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """启动 claude 子进程，把事件流渲染成可读文本。"""
        argv = list(request.argv)
        prompt_text = request.prompt_text
        if prompt_text is not None:
            # 流式路径剥离提示词：去掉所有 `-p` 形参，再移除尾部提示词，
            # 改经 stdin 投递（与历史 transcript_runner 行为逐字一致）。
            argv = [arg for arg in argv if arg != "-p"]
            if argv and argv[-1] == prompt_text:
                argv = argv[:-1]
        completed = run_filtered_claude_stream(
            argv,
            cwd=request.cwd,
            timeout=request.timeout,
            inactivity_timeout=request.inactivity_timeout,
            collect_stdout=request.collect_stdout,
            prompt_text=prompt_text,
            output_sink=request.output_sink,
            display_sink=request.display_sink,
            label=request.label,
        )
        return CommandResult(
            command=tuple(argv),
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
        )


__all__ = ["ClaudeStreamJsonOutputProtocol"]
