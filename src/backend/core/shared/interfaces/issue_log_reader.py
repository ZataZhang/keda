"""按 Issue 读取 Agent 输出日志的窄端口与数据模型。

本模块定义「仓库 + Issue 编号 + 尝试」三维定位的只读日志读取契约：

- :class:`IssueLogReader` 是 core 侧消费的唯一端口；Infrastructure 提供
  面向固定日志子树（``<repo>/logs/agent-runner/issues/<repo_id>/``）的
  实现。Core 不感知文件布局细节，只处理选择规则与状态映射。
- 读取结果被限制在已注册仓库的受控日志子树内：不接受绝对路径、任意
  目录或客户端传入的文件名，防路径逃逸与跨 Issue 混读。

配合 :mod:`backend.core.use_cases.issue_logs` 的选择规则，CLI 与 Console
API 共享同一日志事实源与同一状态语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class IssueLogStatus(Enum):
    """一次 Issue 日志读取的结果状态。

    - ``OK``：命中目标尝试文件，``content``/``next_offset``/``eof`` 有效。
    - ``NO_ATTEMPT``：该 Issue 当前没有任何日志文件（尚未开始或已清理）。
    - ``ATTEMPT_GONE``：调用方指定的 ``attempt_id`` 曾存在但已不可读
      （被清理或轮转）；调用方应回到「最新尝试」重新定位。
    - ``TRUNCATED``：文件在两次读取之间被替换或截断（``offset`` 越过
      当前文件大小）；调用方应重置偏移重读。
    - ``REPO_NOT_FOUND``：``repo_id`` 未注册或未启用，服务端拒绝读取。
    """

    OK = "ok"
    NO_ATTEMPT = "no_attempt"
    ATTEMPT_GONE = "attempt_gone"
    TRUNCATED = "truncated"
    REPO_NOT_FOUND = "repo_not_found"


@dataclass(frozen=True)
class IssueLogReadRequest:
    """一次 Issue 日志读取的显式选择条件。

    ``attempt_id`` 为 ``None`` 表示「读最新尝试」；否则要求服务端返回该
    尝试，若文件已不存在则以 ``ATTEMPT_GONE`` 显式报告，而不是静默改读
    别的尝试或别的 Issue。

    ``tail`` 为 ``True`` 时忽略 ``offset``，返回文件末尾最多
    ``max_bytes`` 字节的尾部窗口（用于「首次给尾部」场景）；返回的
    ``next_offset`` 对齐到文件末尾，便于调用方原地转入续读。
    """

    repo_id: str
    issue_number: int
    attempt_id: str | None = None
    offset: int = 0
    max_bytes: int = 64 * 1024
    tail: bool = False


@dataclass(frozen=True)
class IssueLogReadResult:
    """一次 Issue 日志读取的结果。

    ``attempt_id`` 是服务端按文件身份生成的不透明标识（文件名），调用方
    续读时原样回传；``next_offset`` 是下一次读取的字节偏移；``eof`` 表示
    本次已读到当前文件末尾。非 ``OK`` 状态下 ``content`` 为空、
    ``attempt_id``/``next_offset`` 仅在同名尝试仍存在时有意义。
    """

    status: IssueLogStatus
    attempt_id: str | None
    content: str
    next_offset: int
    eof: bool


class IssueLogReader(Protocol):
    """按已注册仓库、Issue 编号与字节偏移读取日志的最窄端口。"""

    def read_issue_log(self, request: IssueLogReadRequest) -> IssueLogReadResult:
        """读取一次有界日志块。

        实现方负责：把 ``repo_id`` 解析到已注册仓库根、把读取范围限制在
        该仓库固定日志子树内、处理 UTF-8 分段与文件消失/截断，并返回稳定
        的 ``attempt_id`` 与 ``next_offset``。实现方不得接受或拼接调用方
        提供的任意文件路径。
        """
        ...


__all__ = [
    "IssueLogReader",
    "IssueLogReadRequest",
    "IssueLogReadResult",
    "IssueLogStatus",
]
