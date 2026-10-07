"""流式输出的行首时间戳格式化。

Issue #223 要求 per-Issue 实时输出带上时间线，但时间戳属于**消费侧展示**，
不是 agent 生产者的输出内容：谁需要时间线，谁在自己的边界上调用本模块。
这样 ``output_sink`` 一路传递的始终是可读原文，落盘内容仍可被复制、被行首
锚定的解析器读取。当前只有两个消费侧：

- 终端实时视图：``infrastructure/process_runner.py`` 与
  ``engines/agent_runner/output_protocols/`` 直接 ``print`` 的路径。
- per-Issue 输出路由 sink：``core/use_cases/agent_runner_output_routing.py``
  在写日志文件 / 上看板 / 镜像回前台之前统一加前缀，三个消费端因此逐行一致。

合议（``iar deliberate``）的 workspace 文件与 live 面板读的是生产者原文，
所以不带时间戳——那些文件是要能渲染、能复制的 markdown 答案。

放在 ``core/shared/interfaces/`` 是因为两侧消费端都要用同一份实现：架构检查
（``hooks/shared/check_architecture.py``）只允许 ``infrastructure`` 依赖
``core.shared.interfaces`` / ``core.shared.models``，而终端打印路径住在
``infrastructure``、路由 sink 住在 ``core``。两边各自实现同一套行首规则会立刻
漂移，所以这里作为跨层共享的展示契约单点存在。
"""

from __future__ import annotations

from datetime import datetime

_LINE_TIMESTAMP_FORMAT = "%H:%M:%S"


def _current_timestamp_text() -> str:
    """返回当前时刻的 ``HH:MM:SS`` 文本。

    Returns:
        str: 已去掉日期部分、只保留时分秒的时间文本。
    """
    return datetime.now().strftime(_LINE_TIMESTAMP_FORMAT)


def format_timestamped_line(text: str) -> str:
    """在文本的每个非空物理行行首加时间戳前缀。

    Args:
        text (str): 待格式化的文本，可以是一行、多行或以换行结尾。

    Returns:
        str: 每个非空物理行行首带 ``[HH:MM:SS] `` 的文本；空行原样保留，
            不会凭空生出只有前缀的空行。
    """
    timestamp_prefix = f"[{_current_timestamp_text()}] "
    lines = text.split("\n")
    formatted_lines: list[str] = []
    for index, line in enumerate(lines):
        prefix = timestamp_prefix if line else ""
        if index == len(lines) - 1:
            formatted_lines.append(f"{prefix}{line}")
        else:
            formatted_lines.append(f"{prefix}{line}\n")
    return "".join(formatted_lines)


class TimestampedStreamFormatter:
    """跨流式碎片维护行首状态，保证每个物理行只加一次时间戳。

    文本增量（agent 的 delta token、PTY 读到的半行）会以任意碎片到达，逐块
    套用 :func:`format_timestamped_line` 会把同一行切断，并在每个碎片前塞一
    条时间线。该状态机按字符跟踪行边界，因此同一物理行只有一行首前缀。
    """

    def __init__(self) -> None:
        """初始化为「位于行首」状态：首个非换行字符前会加时间戳。"""
        self._at_line_start = True

    def format_chunk(self, text: str) -> str:
        """返回只在物理行起点插入时间戳的碎片。

        Args:
            text (str): 流式输出的一个碎片，可能不含换行。

        Returns:
            str: 加前缀后的碎片文本；空碎片返回空串。
        """
        if not text:
            return ""
        timestamped_characters: list[str] = []
        for character in text:
            if self._at_line_start and character != "\n":
                timestamped_characters.append(f"[{_current_timestamp_text()}] ")
                self._at_line_start = False
            timestamped_characters.append(character)
            if character == "\n":
                self._at_line_start = True
        return "".join(timestamped_characters)


__all__ = ["TimestampedStreamFormatter", "format_timestamped_line"]
