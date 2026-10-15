"""单 Issue 人工接管在 Console HTTP 层的响应投影（Issue #278）。

``take_over_verifier`` 的裁决必须按 PRD §7.3 映射成不同状态码（``accepted`` /
``pending`` 回 202、``handed_over`` 回 200、``binding_mismatch`` /
``completed_conflict`` / ``not_found`` 回 409），而 Console 的 Issue 动作端点对
其余动作（retry_failed / blocked_continue / recover_failed_publish）一直是
「返回 dict、由 FastAPI 以 200 序列化」。

把这段状态码投影单独成模块，端点就只多一次函数调用：无接管语义时（``http_status``
为 ``None``）原样返回那个 dict，序列化路径与本特性之前逐字节一致；有接管语义时
返回已带状态码的 ``JSONResponse``，响应体仍是同一个 payload，绝不把「请求已登记」
伪装成 200 成功、也不把它降级成 ``HTTPException`` 的 ``{"detail": ...}`` 形状。
"""

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def build_issue_action_response(
    payload: dict[str, Any], http_status: int | None
) -> dict[str, Any] | JSONResponse:
    """按接管裁决构造 Issue 动作响应。

    Args:
        payload: 已 ``_serialize`` 的动作结果。
        http_status: 接管动作映射出的 HTTP 状态码；非接管动作为 ``None``。

    Returns:
        ``http_status`` 为 ``None`` 时原样返回 ``payload``（端点默认 200）；否则返回
        指定状态码的 ``JSONResponse``，响应体与 FastAPI 默认序列化保持一致。
    """
    if http_status is None:
        return payload
    return JSONResponse(status_code=http_status, content=jsonable_encoder(payload))
