"""mcode 端到端探针：用真实 mcode CLI 跑一轮，验证协议渲染与观测。

用途：PRD `rv-3` 的证据采集脚本。它**真的**调用 mcode（消耗模型额度），
因此不属于默认测试套件的一部分，由执行者按需手动运行并把输出存进
证据目录。

脚本做三件事：

1. 直接跑 ``mcode exec`` 捕获**原始** JSONL 事件流；
2. 把**同一份**事件流交给平台的观测器与渲染器（与生产路径同源），
   得到"平台看到的 session id / token 用量"与"渲染后的可读文本"，
   再与原始上报逐字段对照——同源对照，避免两轮会话互相干扰；
3. 再走一次**生产协议**端到端中继，证明真实调用链可用。

用法::

    uv run python scripts/probe_mcode_protocol.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.core.shared.interfaces.agent_output_protocol import OutputRelayRequest
from backend.engines.agent_runner.output_protocols import get_output_protocol_registry
from backend.engines.agent_runner.output_protocols.mcode_json_lines import _render_line
from backend.infrastructure.agent_stream_usage import StreamUsageCollector

WORKDIR = Path("/tmp/mcode-protocol-probe")
PROMPT = "用一句话回答：协议探针已就绪。"
ARGV_TAIL = ("--permission", "full", "--output-format", "stream-json", "--input", "-")


def _capture_raw_lines() -> list[str]:
    """直接跑一次 mcode，捕获原始 JSONL 行。"""
    completed = subprocess.run(
        ["mcode", "exec", "--cwd", str(WORKDIR), *ARGV_TAIL],
        input=PROMPT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def main() -> int:
    """跑真实 mcode，把原始上报与平台观测并排对照。"""
    WORKDIR.mkdir(parents=True, exist_ok=True)

    print("=== 1) 原始事件流（mcode 直接上报） ===")
    raw_lines = _capture_raw_lines()
    print(f"事件行数：{len(raw_lines)}")
    for raw_line in raw_lines[:3]:
        print("  ", raw_line[:200])
    print("   ...")
    for raw_line in raw_lines[-3:]:
        print("  ", raw_line[:200])

    raw_session_id = None
    raw_usage = None
    raw_agent_messages: list[str] = []
    for raw_line in raw_lines:
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if raw_session_id is None and isinstance(payload.get("sessionId"), str):
            raw_session_id = payload["sessionId"]
        if payload.get("type") == "item.completed":
            item_payload = payload.get("item")
            if (
                isinstance(item_payload, dict)
                and item_payload.get("type") == "agent_message"
                and isinstance(item_payload.get("content"), str)
                and item_payload["content"]
            ):
                raw_agent_messages.append(item_payload["content"])
        if payload.get("type") == "turn.completed":
            raw_usage = payload.get("usage")
    print(f"原始 sessionId = {raw_session_id!r}")
    print(f"原始 usage（camelCase）= {json.dumps(raw_usage, ensure_ascii=False)}")

    print()
    print("=== 2) 平台观测（同一份事件流，与生产路径同源） ===")
    collector = StreamUsageCollector()
    collector.observe_text("\n".join(raw_lines))
    observed_usage = collector.usage
    print(f"观测 session_id = {collector.session_id!r}")
    print(f"观测 token_usage = {observed_usage!r}")
    print("--- 渲染后的可读输出 ---")
    rendered = "".join(f"{_render_line(raw_line)}\n" for raw_line in raw_lines)
    print(rendered)

    print("=== 3) 映射对照 ===")
    session_id_matches = raw_session_id is not None and collector.session_id == raw_session_id
    usage_fields_match = isinstance(raw_usage, dict) and observed_usage is not None
    if usage_fields_match:
        print(
            f"inputTokens  {raw_usage.get('inputTokens')} → input_tokens  {observed_usage.input_tokens}"
        )
        print(
            f"outputTokens {raw_usage.get('outputTokens')} → output_tokens "
            f"{observed_usage.output_tokens}"
        )
        print(
            f"cacheReadTokens {raw_usage.get('cacheReadTokens')} → cache_read_input_tokens "
            f"{observed_usage.cache_read_input_tokens}"
        )
        print(f"total_tokens（平台口径）= {observed_usage.total_tokens}")
        usage_fields_match = (
            raw_usage.get("inputTokens") == observed_usage.input_tokens
            and raw_usage.get("outputTokens") == observed_usage.output_tokens
            and raw_usage.get("cacheReadTokens") == observed_usage.cache_read_input_tokens
            and raw_usage.get("totalTokens") == observed_usage.total_tokens
        )
    rendered_messages_match = bool(raw_agent_messages) and all(
        message_text in rendered for message_text in raw_agent_messages
    )
    print(f"session id 一致：{session_id_matches}")
    print(f"usage 字段与总量一致：{usage_fields_match}")
    print(f"最终正文被渲染：{rendered_messages_match}")

    print()
    print("=== 4) 生产协议端到端中继 ===")
    registry = get_output_protocol_registry()
    protocol = registry.resolve("mcode-json-lines")
    result = protocol.relay(
        OutputRelayRequest(
            argv=("mcode", "exec", "--cwd", str(WORKDIR), *ARGV_TAIL),
            cwd=WORKDIR,
            prompt_text=PROMPT,
            prompt_delivery="stdin",
            timeout=600,
            collect_stdout=True,
        )
    )
    print("argv:", " ".join(result.command))
    print(f"exit code = {result.return_code}")
    print(f"session_id = {result.session_id!r}")
    print(f"token_usage = {result.token_usage!r}")
    print("--- 渲染后的可读输出 ---")
    print(result.stdout)
    production_relay_succeeded = (
        result.return_code == 0
        and result.session_id is not None
        and result.token_usage is not None
        and bool(result.stdout.strip())
    )
    print(f"生产协议中继有完整结果：{production_relay_succeeded}")
    return (
        0
        if session_id_matches
        and usage_fields_match
        and rendered_messages_match
        and production_relay_succeeded
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
