"""Agent 子进程环境净化（child env sanitize）。

runner 常从操作者的交互式 AI 会话（CodeBuddy / Claude Code 的 shell）启动，
父环境携带该会话私有注入的变量。headless agent 子进程原样继承这些变量会出问题：
已证实交互式 CodeBuddy 会话注入的 ``SERVER__PORT`` 会使子进程尝试监听同端口，
触发 ``EADDRINUSE`` 并在首个模型请求前永久卡死（stdout 零输出，最终被
inactivity watchdog 杀掉，见 2026-09-28 Issue #156 事故）。

本模块提供统一的净化入口：按固定 denylist 剔除会话私有变量，其余变量
（PATH、HOME、代理、API key 等）原样透传。
"""

from __future__ import annotations

import os

from backend.infrastructure.logging.logger import logger

# agent 子进程必须剔除的会话私有变量名单（2026-09-28 与用户拍板的 8 变量）：
# - SERVER__PORT：交互式 CodeBuddy 会话 daemon 的监听端口，子进程继承后
#   会尝试绑定同一端口（EADDRINUSE）并永久卡死，是唯一已证实的致毒变量；
# - 其余 7 个指向父会话私有状态的注入变量，单独存在无害，但对 headless
#   子进程纯属噪声，剔除以消除一类未来漂移风险。
AGENT_CHILD_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "SERVER__PORT",
        "CODEBUDDY_SERVICE_PROXY_URL",
        "CODEBUDDY_SESSION_ID",
        "CODEBUDDY_CONVERSATION_REQUEST_ID",
        "CODEBUDDY_ROOT_REQUEST_ID",
        "CODEBUDDY_CONVERSATION_MESSAGE_ID",
        "CODEBUDDY_PROJECT_DIR",
        "CODEBUDDY_CURRENT_MODEL_ID",
    }
)


def build_sanitized_child_env() -> dict[str, str]:
    """构建 agent 子进程的净化环境。

    从当前进程环境剔除 :data:`AGENT_CHILD_ENV_DENYLIST` 中的会话私有变量，
    其余变量原样透传。每剔除一个变量记录一条 WARNING（只含变量名与值长度
    摘要，不记录完整值，避免泄密）；误剔变量可从该日志立即发现。

    Returns:
        剔除名单变量后的环境副本（新 dict，不修改 ``os.environ``）。
    """
    child_env = dict(os.environ)
    for key in sorted(AGENT_CHILD_ENV_DENYLIST):
        if key in child_env:
            value = child_env.pop(key)
            logger.warning("child env sanitized: removed %s (value length %d)", key, len(value))
    return child_env
