"""输出协议抽象（端口）。

输出协议负责把一个已按调用形态启动的 agent 子进程的 stdout/stderr
中继成 ：class:`~backend.core.shared.models.agent_runner.CommandResult`：
通用文本协议逐行透传，流式 JSON 协议则把事件流渲染成可读文本。

协议**实现**位于 ``engines/agent_runner/output_protocols/``（经
``iar.agent_output_protocols`` entry point 注册）；core 侧（构造器、
用例）只依赖本接口，满足 ``core`` 不得依赖 ``engines`` 的架构约束；
``infrastructure`` 允许导入本模块（白名单 ``core.shared.interfaces``）。

协议 id 常量（``PLAIN_PROTOCOL_ID`` 等）统一定义在
:mod:`backend.core.shared.models.agent_spec`（叶子模块，避免循环导入），
本模块按需再导出。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.shared.models.agent_spec import (
    CLAUDE_STREAM_JSON_PROTOCOL_ID,
    PI_JSON_LINES_PROTOCOL_ID,
    PLAIN_PROTOCOL_ID,
    PROMPT_DELIVERY_STDIN,
)

if TYPE_CHECKING:
    # 仅类型注解使用；运行时导入会与 models.agent_runner（反向导入协议 id
    # 常量）构成循环。注解经 ``__future__.annotations`` 已是字符串。
    from backend.core.shared.models.agent_runner import CommandResult


@dataclass(frozen=True)
class OutputRelayRequest:
    """一次输出中继的全部输入。

    Attributes:
        argv: 最终要执行的命令（含提示词，若投递方式要求进 argv）。
        cwd: 子进程工作目录。
        prompt_text: 提示词全文。``None`` 表示提示词已随 argv 投递、
            不再经 stdin 补投；非 ``None`` 时协议按 ``prompt_delivery``
            决定是否写 stdin（流式协议如 ``claude-stream-json`` 会先把
            可剥离的提示词参数从 argv 中移除再经 stdin 投递，避免
            transcript 增长后 ``Argument list too long``）。
        prompt_delivery: 调用形态声明的提示词投递方式（见
            ``backend.core.shared.models.agent_spec``）。协议实现据此
            区分"提示词在 argv 里"与"提示词需要写 stdin"，不做猜测。
        timeout: 可选墙钟超时（秒）。
        inactivity_timeout: 可选无输出超时（秒）。
        label: watchdog / 日志上下文标签。
        collect_stdout: 是否把（渲染后的）stdout 收集进返回值。
        output_sink: 渲染文本块回调（驱动 live 面板 / 工作区文件）。
        display_sink: stderr 展示回调（仅供即时显示，不进 transcript）。
    """

    argv: tuple[str, ...]
    cwd: Path
    prompt_text: str | None = None
    prompt_delivery: str = PROMPT_DELIVERY_STDIN
    timeout: int | None = None
    inactivity_timeout: int | None = None
    label: str | None = None
    collect_stdout: bool = False
    output_sink: Callable[[str], None] | None = None
    display_sink: Callable[[str], None] | None = None


class IAgentOutputProtocol(ABC):
    """把 agent 子进程的输出中继成 ``CommandResult`` 的协议接口。"""

    @abstractmethod
    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """启动子进程并中继其输出直至退出。

        Args:
            request: 中继请求（argv、cwd、提示词、sinks、超时）。

        Returns:
            CommandResult: 含退出码与（按需）渲染/收集的 stdout；
            ``output_protocol`` 字段必须回填为本协议 id。

        Raises:
            Exception: 协议实现自身的启动/解析失败原样抛出，不降级。
        """
        ...


class IAgentOutputProtocolRegistry(ABC):
    """输出协议注册表端口。

    实现负责经 entry point group 发现协议（内置协议与第三方插件走
    同一条注册路径）；解析失败必须直接抛错，不允许静默回落到
    ``plain``。
    """

    @abstractmethod
    def resolve(self, protocol_id: str) -> IAgentOutputProtocol:
        """按 id 解析协议实现；未注册或加载失败时抛带上下文的异常。"""
        ...

    @abstractmethod
    def list_ids(self) -> tuple[str, ...]:
        """按注册顺序列出全部已注册协议 id（不触发加载）。"""
        ...


__all__ = [
    "CLAUDE_STREAM_JSON_PROTOCOL_ID",
    "PI_JSON_LINES_PROTOCOL_ID",
    "PLAIN_PROTOCOL_ID",
    "IAgentOutputProtocol",
    "IAgentOutputProtocolRegistry",
    "OutputRelayRequest",
]
