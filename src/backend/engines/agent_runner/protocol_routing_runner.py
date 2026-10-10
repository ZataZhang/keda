"""run 路径的协议路由执行器。

:func:`build_agent_invocation` 产出的非 ``plain`` 输出协议在这里经注册表
解析为协议实现并执行；``plain`` / 通用命令（git / gh / 验证命令）原样
委托给内层的通用执行器（``SubprocessRunner``），保持其 PTY 中继、
watchdog 超时等既有行为。

core 侧的 ``run_agent_with_prompt`` 只把 ``output_protocol`` 作为**数据**
传给 ``IProcessRunner`` 端口；本模块是 engines 层组装根注入的路由实现，
保证 core 不依赖 engines 的架构约束不被打破。
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from pathlib import Path
from typing import Sequence

from backend.core.shared.interfaces.agent_output_protocol import (
    PLAIN_PROTOCOL_ID,
    IAgentOutputProtocolRegistry,
    OutputRelayRequest,
)
from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import CommandResult
from backend.core.shared.models.agent_spec import (
    PROMPT_DELIVERY_ARGV_TAIL,
    PROMPT_DELIVERY_STDIN,
)
from backend.core.shared.models.agent_stall import AttemptOwnership, StallCancelOutcome
from backend.infrastructure.agent_stream_usage import attach_agent_observations
from backend.infrastructure.process_runner import CommandFailedError


class ProtocolRoutingProcessRunner(IProcessRunner):
    """按 ``output_protocol`` 把非 plain 协议路由到注册表实现的执行器。

    活跃 attempt 的登记册在**内层**通用执行器手里（它才是持有 ``Popen`` 的一层），
    所以进程归属复核与精确取消都原样委托给内层；本类不实现第二套取消语义，
    也不会在内层没有能力时假装成功。
    """

    def __init__(
        self,
        inner: IProcessRunner,
        protocol_registry: IAgentOutputProtocolRegistry,
    ) -> None:
        """注入内层通用执行器与输出协议注册表。"""
        self._inner = inner
        self._protocol_registry = protocol_registry

    def probe_live_attempt(self, attempt_key: str) -> AttemptOwnership:
        """把进程归属复核委托给内层执行器。"""
        return self._inner.probe_live_attempt(attempt_key)

    def cancel_live_attempt(
        self, attempt_key: str, expected: AttemptOwnership
    ) -> StallCancelOutcome:
        """把精确取消委托给内层执行器（只有它知道自己的进程组）。"""
        return self._inner.cancel_live_attempt(attempt_key, expected)

    # 这里实现 IProcessRunner 的同一关键字契约；只排除签名重复，路由逻辑仍参与扫描。
    # jscpd:ignore-start
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int | None = None,
        inactivity_timeout: int | None = None,
        capture_output: bool = True,
        input_text: str | None = None,
        label: str | None = None,
        output_sink: Callable[[str], None] | None = None,
        output_protocol: str | None = None,
        attempt_key: str | None = None,
    ) -> CommandResult:
        # jscpd:ignore-end
        """执行一条命令；非 plain 输出协议交由注册表解析出的实现中继。"""
        if output_protocol is None or output_protocol == PLAIN_PROTOCOL_ID:
            return self._inner.run(
                command,
                cwd=cwd,
                check=check,
                timeout=timeout,
                inactivity_timeout=inactivity_timeout,
                capture_output=capture_output,
                input_text=input_text,
                label=label,
                output_sink=output_sink,
                # plain agent 调用也把协议 id 传下去：内层据此区分"agent 调用"
                # 与"普通命令"，只有前者才做 token 用量的事后解析。
                output_protocol=output_protocol,
                attempt_key=attempt_key,
            )
        protocol = self._protocol_registry.resolve(output_protocol)
        started_mono = time.monotonic()
        result = protocol.relay(
            OutputRelayRequest(
                argv=tuple(command),
                cwd=cwd,
                prompt_text=input_text,
                # run 路径的 stdin 投递由调用方经 input_text 表达；
                # 其余情况提示词已在 argv 中（argv_tail / flag）。
                prompt_delivery=(
                    PROMPT_DELIVERY_STDIN if input_text is not None else PROMPT_DELIVERY_ARGV_TAIL
                ),
                timeout=timeout,
                inactivity_timeout=inactivity_timeout,
                label=label,
                collect_stdout=True,
                output_sink=output_sink,
                attempt_key=attempt_key,
            )
        )
        result = dataclasses.replace(
            result,
            duration_seconds=round(time.monotonic() - started_mono, 3),
        )
        if check and result.return_code != 0:
            failure = CommandFailedError(
                result.return_code,
                list(command),
                output=result.stdout,
                stderr=result.stderr,
            )
            # 中继结果里执行器自报的会话 id 与模型名必须跟着异常一起上去：
            # 崩溃对账靠 session_id 续传，调用观测靠 reported_model 诚实记录
            # "执行器说了什么"。就地新建异常而不搬运会让两者在协议边界消失。
            attach_agent_observations(
                failure,
                session_id=result.session_id,
                reported_model=result.reported_model,
            )
            raise failure
        return result


__all__ = ["ProtocolRoutingProcessRunner"]
