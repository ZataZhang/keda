"""agent 输出流中的旁路观测：token 用量与会话 id。

从 agent CLI 的 stdout 事件流解析官方自报的 usage 统计。当前识别
claude stream-json 的 ``result`` 事件形态（``usage`` 携带 input /
output / cache_read / cache_creation 四个整数字段）；其他 agent 的
result 事件若同形也能命中，形状不同或拿不到时宽容降级为"无数据"
（``None``），绝不估算、绝不抛异常影响主流程。

同一逐行观测点顺带捕获 agent CLI 自报的 ``session_id``（claude 的
``system/init`` 首行事件与末尾 ``result`` 事件都带），崩溃对账与恢复
轮次用它回传给 CLI 的 resume 参数。会话的**持有方始终是 agent CLI**，
本模块只做观测，不自建会话存储。
"""

from __future__ import annotations

import json

from backend.core.shared.models.agent_runner import TokenUsage

#: usage dict 里的 token 字段名与 :class:`TokenUsage` 字段一一对应。
_USAGE_FIELD_NAMES: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

#: 会话 id 事件的廉价预检子串（JSON 文本里必然带该字段名）。
_SESSION_ID_FIELD_NAME = "session_id"


def extract_usage_event(line: str) -> TokenUsage | None:
    """从单行 stdout 解析 result 事件的 usage。

    Args:
        line: 子进程的原始单行输出（可能带尾部换行）。

    Returns:
        该行是 ``type == "result"`` 且 ``usage`` 含至少一个合法非负整数字段时
        返回 :class:`TokenUsage`；非 JSON 行、非 result 事件、usage 缺失或
        全部字段非法时返回 ``None``。
    """
    # 廉价预检：result 事件的 JSON 文本必然含 "result" 子串，先排除绝大多数
    # 非 JSON 行（plain/PTY 的人类可读输出），避免逐行 json.loads 的异常开销。
    if "result" not in line:
        return None
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event_payload, dict) or event_payload.get("type") != "result":
        return None
    return build_token_usage(event_payload.get("usage"))


def build_token_usage(usage_payload: object) -> TokenUsage | None:
    """把 usage dict 宽容地转成 :class:`TokenUsage`。

    Args:
        usage_payload: result 事件里的 ``usage`` 值；非 dict 直接 ``None``。

    Returns:
        四个字段中至少一个为合法非负整数时返回实例（缺失字段记 0，与
        "总量 = 四项之和"的聚合口径一致）；全部缺失或非法（bool / 非整数 /
        负数）时返回 ``None``。
    """
    if not isinstance(usage_payload, dict):
        return None
    field_values: dict[str, int] = {}
    for field_name in _USAGE_FIELD_NAMES:
        raw_value = usage_payload.get(field_name)
        # bool 是 int 的子类，必须显式排除；负数视为畸形同样排除。
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
            continue
        field_values[field_name] = raw_value
    if not field_values:
        return None
    return TokenUsage(
        input_tokens=field_values.get("input_tokens", 0),
        output_tokens=field_values.get("output_tokens", 0),
        cache_read_input_tokens=field_values.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=field_values.get("cache_creation_input_tokens", 0),
    )


def extract_session_id_event(line: str) -> str | None:
    """从单行 stdout 解析 agent CLI 自报的会话 id。

    Args:
        line: 子进程的原始单行输出（可能带尾部换行）。

    Returns:
        该行是 JSON 事件且 ``session_id`` 为非空字符串时返回去掉首尾空白的
        会话 id；非 JSON 行、无该字段、字段非字符串或为空白时返回 ``None``。
    """
    # 廉价预检：会话事件的 JSON 文本必然含字段名，先排除 plain/PTY 的人类可读输出。
    if _SESSION_ID_FIELD_NAME not in line:
        return None
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event_payload, dict):
        return None
    raw_session_id = event_payload.get(_SESSION_ID_FIELD_NAME)
    if not isinstance(raw_session_id, str):
        return None
    return raw_session_id.strip() or None


class StreamUsageCollector:
    """流式采集器：逐行观察原始 stdout，保留最近一次解析出的用量与会话 id。

    claude 的 stream-json 流里 result 事件出现在末尾且只有一个；若出现
    多个，以最后一次为准（与"最终结果"语义一致）。所有方法都不抛异常——
    用量采集是旁路，任何解析问题都静默降级为"无数据"。
    """

    def __init__(self) -> None:
        """初始化为"尚未观察到任何用量或会话 id"。"""
        self._usage: TokenUsage | None = None
        self._session_id: str | None = None

    def observe_line(self, line: str) -> None:
        """观察一行原始输出；解析失败静默跳过。"""
        try:
            parsed_usage = extract_usage_event(line)
        except Exception:  # pragma: no cover - 防御性兜底：采集绝不影响主流程
            parsed_usage = None
        if parsed_usage is not None:
            self._usage = parsed_usage
        try:
            parsed_session_id = extract_session_id_event(line)
        except Exception:  # pragma: no cover - 防御性兜底：同上
            return
        if parsed_session_id is not None:
            self._session_id = parsed_session_id

    @property
    def usage(self) -> TokenUsage | None:
        """最近一次解析出的用量；整条流无 result usage 时为 ``None``。"""
        return self._usage

    @property
    def session_id(self) -> str | None:
        """最近一次解析出的会话 id；整条流无该字段时为 ``None``。

        续传调用（``--resume``）会开启**新**会话，因此"最后一次"命中正是
        下一轮崩溃最该续传的会话，而不是本次接过来的那个。
        """
        return self._session_id


def parse_usage_from_plain_stdout(stdout: str) -> TokenUsage | None:
    """对未渲染的 plain / PTY stdout 做事后容错解析。

    适用于走通用执行路径的 agent（kimi / codex 等）：它们的 stdout 没有被
    渲染改写，若末尾恰好有同形的 result 事件行即可命中；否则返回 ``None``。

    Args:
        stdout: 子进程完整 stdout 文本。

    Returns:
        最后一条可解析出的用量；整段输出无 result usage 时为 ``None``。
    """
    final_usage: TokenUsage | None = None
    for line in stdout.splitlines():
        parsed_usage = extract_usage_event(line)
        if parsed_usage is not None:
            final_usage = parsed_usage
    return final_usage


__all__ = [
    "StreamUsageCollector",
    "build_token_usage",
    "extract_session_id_event",
    "extract_usage_event",
    "parse_usage_from_plain_stdout",
]
