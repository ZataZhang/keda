"""托管凭据的上游只读校验客户端（Issue #265，FR-5）。

实现 core 契约 ``GitHubTokenVerifier`` 与 ``AgentApiKeyProber``：

- GitHub 校验**只调用只读 GET**（身份、仓库可见性、逐权限读取探测），绝不
  创建 Issue / PR / 分支；token 只进请求头，不进 URL、argv 或日志。
- Agent key 探测是**一条最小真实模型请求**，只在调用方（core 用例）确认
  客户已显式同意费用后才发出；transport 注入点供测试替换，生产走 stdlib
  ``urllib``，不新增 HTTP 依赖。
- 返回的记录与 core 同名类型结构一致（infrastructure 禁止 import core）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

_logger = logging.getLogger(__name__)

#: transport 协议：``(method, url, body_json_or_none, headers) -> (status, body_text)``。
JsonTransport = Callable[[str, str, dict | None, dict[str, str]], tuple[int, str]]

_GITHUB_API_ROOT = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 20

#: 各 provider 的最小探测模型（与 runner 镜像预装 CLI 的默认 API 端点配套）。
_CLAUDE_PROBE_MODEL = "claude-3-5-haiku-20241022"
_OPENAI_PROBE_MODEL = "gpt-4o-mini"
_KIMI_PROBE_MODEL = "moonshot-v1-8k"


def urllib_json_transport(
    method: str,
    url: str,
    body_json: dict | None,
    headers: dict[str, str],
) -> tuple[int, str]:
    """默认 transport：stdlib urllib，带超时；非 2xx 也返回状态码与响应体。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(body_json).encode("utf-8") if body_json is not None else None,
        headers={**headers, "User-Agent": "kedacode-hosted-console"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as http_error:
        return http_error.code, http_error.read().decode("utf-8", errors="replace")


@dataclass(frozen=True)
class GitHubCheckView:
    """GitHub 只读校验结果（与 core.GitHubTokenCheck 同构，不含 token）。"""

    accepted: bool
    login: str = ""
    repository_visible: bool = False
    granted_permissions: tuple[str, ...] = ()
    rejection: str | None = None


@dataclass(frozen=True)
class AgentProbeView:
    """最小模型调用结果（与 core.AgentKeyProbeResult 同构，不含 key）。"""

    accepted: bool
    rejection: str | None = None
    detail: str = ""


class GitHubReadOnlyVerifier:
    """用只读 REST 探测 fine-grained PAT 的身份、仓库可见性与读取权限。"""

    def __init__(
        self,
        *,
        transport: JsonTransport = urllib_json_transport,
        api_root: str = _GITHUB_API_ROOT,
    ) -> None:
        """注入 transport 与 API 根（测试用假响应，生产用真实 GitHub）。"""
        self._transport = transport
        self._api_root = api_root

    def _get(self, token: str, path: str) -> tuple[int, dict]:
        """发起一次带 token 头的只读 GET，返回状态码与解析后的 JSON。"""
        status, body_text = self._transport(
            "GET",
            f"{self._api_root}{path}",
            None,
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        try:
            payload = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}

    def verify(self, token: str, owner_repo: str) -> GitHubCheckView:
        """检查身份 → 仓库可见性 → Issues/Pull requests/Contents 读取权限。

        Args:
            token: 客户粘贴的 GitHub fine-grained PAT（不落日志）。
            owner_repo: 目标仓库 ``owner/repo``。

        Returns:
            通过时给出 login 与探测到的读取权限；任何一步不满足即拒绝且
            携带可操作原因码。
        """
        status, user_payload = self._get(token, "/user")
        if status >= 400:
            return self._rejected("github_token_invalid")
        login = str(user_payload.get("login", ""))

        status, _repo_payload = self._get(token, f"/repos/{owner_repo}")
        if status == 404:
            return self._rejected("github_repo_inaccessible")
        if status >= 400:
            return self._rejected("github_token_invalid")

        granted: list[str] = []
        for probe_path, permission_name in (
            (f"/repos/{owner_repo}/issues?per_page=1", "Issues: read"),
            (f"/repos/{owner_repo}/pulls?per_page=1", "Pull requests: read"),
            (f"/repos/{owner_repo}/contents", "Contents: read"),
        ):
            probe_status, _probe_payload = self._get(token, probe_path)
            if probe_status == 403:
                return self._rejected("github_permissions_insufficient")
            if probe_status == 404:
                return self._rejected("github_repo_inaccessible")
            if probe_status >= 400:
                return self._rejected("github_token_invalid")
            granted.append(permission_name)
        return GitHubCheckView(
            accepted=True,
            login=login,
            repository_visible=True,
            granted_permissions=tuple(granted),
        )

    @staticmethod
    def _rejected(rejection_code: str) -> GitHubCheckView:
        """构造拒绝结果（原因码为 core 枚举值的字符串形态）。"""
        return GitHubCheckView(accepted=False, rejection=rejection_code)


class MinimalAgentKeyProber:
    """按 provider 官方默认端点发一条最小模型请求（产生上游费用，须客户确认）。"""

    def __init__(self, *, transport: JsonTransport = urllib_json_transport) -> None:
        """注入 transport；生产使用真实供应商端点。"""
        self._transport = transport

    def probe(self, provider: str, api_key: str) -> AgentProbeView:
        """对单个 provider 发起最小请求；key 只进请求头。"""
        url, body, headers = self._build_request(provider, api_key)
        if url is None:
            return AgentProbeView(accepted=False, rejection="provider_unsupported")
        try:
            status, body_text = self._transport("POST", url, body, headers)
        except OSError as transport_error:
            _logger.warning("Agent key probe transport failed for provider '%s'", provider)
            return AgentProbeView(
                accepted=False, rejection="agent_model_call_failed", detail=str(transport_error)
            )
        if status >= 400:
            return AgentProbeView(
                accepted=False,
                rejection="agent_model_call_failed",
                detail=self._safe_detail(status, body_text),
            )
        return AgentProbeView(accepted=True)

    @staticmethod
    def _build_request(provider: str, api_key: str) -> tuple[str | None, dict | None, dict]:
        """构造各 provider 的最小请求（model、1 token 上限、单条消息）。"""
        one_token_message = [{"role": "user", "content": "ping"}]
        if provider == "claude":
            return (
                "https://api.anthropic.com/v1/messages",
                {"model": _CLAUDE_PROBE_MODEL, "max_tokens": 1, "messages": one_token_message},
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
        if provider == "codex":
            return (
                "https://api.openai.com/v1/chat/completions",
                {
                    "model": _OPENAI_PROBE_MODEL,
                    "max_completion_tokens": 1,
                    "messages": one_token_message,
                },
                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
        if provider == "kimi":
            return (
                "https://api.moonshot.cn/v1/chat/completions",
                {"model": _KIMI_PROBE_MODEL, "max_tokens": 1, "messages": one_token_message},
                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
        return None, None, {}

    @staticmethod
    def _safe_detail(status: int, body_text: str) -> str:
        """从错误响应提取可展示信息；响应体可能含回显请求头，必须截断过滤。"""
        snippet = (body_text or "")[:200]
        for marker in ("api_key", "sk-", "Bearer", "authorization"):
            if marker.lower() in snippet.lower():
                return f"HTTP {status}（响应体已省略以避免回显凭据）"
        return f"HTTP {status}: {snippet}"
