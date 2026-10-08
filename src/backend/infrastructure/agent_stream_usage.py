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

from backend.core.shared.interfaces.agent_runner import (
    AGENT_REPORTED_MODEL_ATTR_NAME,
    AGENT_SESSION_ID_ATTR_NAME,
)
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

#: 执行器自报模型名的字段名与廉价预检子串。
_REPORTED_MODEL_FIELD_NAME = "model"


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


def extract_reported_model_event(line: str) -> str | None:
    """从单行 stdout 解析执行器**自报**的模型名。

    只认事件对象顶层的 ``model`` 字符串字段（claude stream-json 的
    ``system/init`` 事件即此形态）。刻意不读 ``message.model`` 之类嵌套字段，
    也不从 ``modelUsage`` 的键名反推——那些形态的归属语义不确定，宁可记"未提供"
    也不给出可能错误归因的值。

    Args:
        line: 子进程的原始单行输出（可能带尾部换行）。

    Returns:
        该行是 JSON 事件且顶层 ``model`` 为非空字符串时返回去掉首尾空白的模型名；
        非 JSON 行、无该字段、字段非字符串或为空白时返回 ``None``。
    """
    # 廉价预检：模型事件的 JSON 文本必然含字段名，先排除 plain/PTY 的人类可读输出。
    if _REPORTED_MODEL_FIELD_NAME not in line:
        return None
    try:
        event_payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event_payload, dict):
        return None
    raw_model = event_payload.get(_REPORTED_MODEL_FIELD_NAME)
    if not isinstance(raw_model, str):
        return None
    return raw_model.strip() or None


class StreamUsageCollector:
    """流式采集器：逐行观察原始 stdout，保留最近一次解析出的用量、会话 id 与模型名。

    claude 的 stream-json 流里 result 事件出现在末尾且只有一个；若出现
    多个，以最后一次为准（与"最终结果"语义一致）。所有方法都不抛异常——
    用量采集是旁路，任何解析问题都静默降级为"无数据"。
    """

    def __init__(self) -> None:
        """初始化为"尚未观察到任何用量、会话 id 或模型名"。"""
        self._usage: TokenUsage | None = None
        self._session_id: str | None = None
        self._reported_model: str | None = None

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
            parsed_session_id = None
        if parsed_session_id is not None:
            self._session_id = parsed_session_id
        try:
            parsed_model = extract_reported_model_event(line)
        except Exception:  # pragma: no cover - 防御性兜底：同上
            parsed_model = None
        if parsed_model is not None:
            self._reported_model = parsed_model

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

    @property
    def reported_model(self) -> str | None:
        """最近一次解析出的执行器自报模型名；整条流未报告时为 ``None``。

        ``None`` 只说明**没有可信报告值**，不等于"用了配置里的默认模型"——
        消费方必须把它展示为"未提供"，不得回填配置值。
        """
        return self._reported_model


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


def parse_reported_model_from_plain_stdout(stdout: str) -> str | None:
    """对未渲染的 plain / PTY stdout 事后解析执行器自报的模型名。

    与 :func:`parse_usage_from_plain_stdout` 同一适用面与同一宽容度：命中即取
    最后一次，命不中就是"未提供"，绝不估算、绝不回填配置值。

    Args:
        stdout: 子进程完整 stdout 文本。

    Returns:
        最后一个可解析出的模型名；整段输出未报告时为 ``None``。
    """
    final_model: str | None = None
    for line in stdout.splitlines():
        parsed_model = extract_reported_model_event(line)
        if parsed_model is not None:
            final_model = parsed_model
    return final_model


__all__ = [
    "StreamUsageCollector",
    "build_token_usage",
    "extract_reported_model_event",
    "extract_session_id_event",
    "extract_usage_event",
    "parse_reported_model_from_plain_stdout",
    "parse_usage_from_plain_stdout",
]


def attach_agent_observations(
    exc: BaseException,
    *,
    session_id: str | None,
    reported_model: str | None,
) -> None:
    """把 agent 自报的会话 id 与模型名挂到失败异常上。

    超时击杀与非零退出都不返回 ``CommandResult``，而"这轮聊到哪儿了"和"执行器说
    自己跑的是哪个模型"恰恰要在失败的那一刻留下——原始事件流只有执行层可靠可见。
    会话的持有方仍是 agent CLI，模型名也只是执行器的自报值，这里只做观测并把它们
    交给上层（崩溃对账取 ``session_id``，调用观测取 ``reported_model``）。没观察到
    的字段什么都不挂，异常语义不变。

    协议路由执行器（``ProtocolRoutingProcessRunner``）在把中继结果转成失败异常时
    也必须调用本函数：它手上只有 ``CommandResult`` 而没有采集器，若就地新建异常而
    不搬运这两个字段，执行器已经报告过的模型名与会话 id 会在异常边界上凭空消失。

    Args:
        exc: 即将向上抛出的失败异常。
        session_id: 执行器自报的会话 id；``None`` / 空串表示没观察到。
        reported_model: 执行器自报的模型名；``None`` / 空串表示没观察到。
    """
    if session_id:
        setattr(exc, AGENT_SESSION_ID_ATTR_NAME, session_id)
    if reported_model:
        setattr(exc, AGENT_REPORTED_MODEL_ATTR_NAME, reported_model)


def _attach_stream_observations(
    exc: BaseException,
    usage_collector: StreamUsageCollector | None,
) -> None:
    """把采集器已观察到的会话 id 与模型名搬到失败异常上。"""
    if usage_collector is None:
        return
    attach_agent_observations(
        exc,
        session_id=usage_collector.session_id,
        reported_model=usage_collector.reported_model,
    )
