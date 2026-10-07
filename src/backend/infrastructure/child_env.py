"""Agent 子进程环境净化（child env sanitize）。

runner 常从操作者的交互式 AI 会话（CodeBuddy / Claude Code 的 shell）启动，
父环境携带该会话私有注入的变量。headless agent 子进程原样继承这些变量会出问题：
已证实交互式 CodeBuddy 会话注入的 ``SERVER__PORT`` 会使子进程尝试监听同端口，
触发 ``EADDRINUSE`` 并在首个模型请求前永久卡死（stdout 零输出，最终被
inactivity watchdog 杀掉，见 2026-09-28 Issue #156 事故）。

本模块提供统一的净化入口：按固定 denylist 剔除会话私有变量，其余变量
（PATH、HOME、代理、API key 等）原样透传。

浏览器 E2E 验证子进程走另一条更强的**白名单**路径
（:func:`build_e2e_child_env`）：验证脚本来自 Issue 产出，不得看到 runner
凭据（GitHub token、模型 API key、带凭据的连接串等），只放行运行浏览器
与被测应用所需的最小变量集合。
"""

from __future__ import annotations

import os
from collections.abc import Iterable

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

# 本进程内已经 WARNING 过的被剔变量名。默认净化档自 Issue #230 起覆盖每一次
# ``SubprocessRunner.run()``（git/gh/pytest 等工具命令单轮操作可达数十次），
# 逐次 WARNING 会把「名单误剔」这一排障信号淹没在噪声里，故每个变量名只在
# 首次命中时 WARNING，后续降为 DEBUG。并发调用最坏情况是重复一条 WARNING。
_WARNED_DENYLIST_REMOVALS: set[str] = set()


def build_sanitized_child_env() -> dict[str, str]:
    """构建子进程的默认净化环境（透传 + denylist 档）。

    从当前进程环境剔除 :data:`AGENT_CHILD_ENV_DENYLIST` 中的会话私有变量，
    其余变量原样透传。剔除动作按变量名去重告警：某变量在本进程内**首次**
    被剔除时记录一条 WARNING（只含变量名与值长度摘要，不记录完整值，避免
    泄密），之后同一变量再被剔除（每次子进程调用都会重算环境）只记 DEBUG，
    以免工具命令高频路径刷爆日志。调用面覆盖 :meth:`SubprocessRunner.run`
    的全部子进程（agent 内容与工具命令）、agent 流式派发点、console 托管
    runner 与 docker compose 基底——环境构造只有这一处事实源，新增名单变量
    无需改动调用点。

    Returns:
        剔除名单变量后的环境副本（新 dict，不修改 ``os.environ``）。
    """
    child_env = dict(os.environ)
    for key in sorted(AGENT_CHILD_ENV_DENYLIST):
        if key not in child_env:
            continue
        value = child_env.pop(key)
        already_warned = key in _WARNED_DENYLIST_REMOVALS
        _WARNED_DENYLIST_REMOVALS.add(key)
        report_removal = logger.debug if already_warned else logger.warning
        report_removal("child env sanitized: removed %s (value length %d)", key, len(value))
    return child_env


# E2E 白名单：浏览器与被测应用运行所需的最小变量集合。
# - 基础定位类：shell / node / 浏览器进程找到自身运行时与缓存目录所需；
# - 浏览器相关：无头浏览器显示与缓存目录、Chromium/Playwright/Puppeteer
#   的浏览器路径覆盖；
# - Node 生态路径类：nvm / pnpm / npm 定位 node 与包缓存所需。
# 凭据类变量（*_API_KEY、TOKEN、SECRET、带凭据的 DATABASE_URL、代理凭据等）
# 一律不放行——白名单是默认关闭语义，漏配变量由脚本显式声明 env_allow 补。
E2E_CHILD_ENV_BASE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "USER",
        "LOGNAME",
        "HOSTNAME",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "TMPDIR",
        "TMP",
        "TEMP",
        "DISPLAY",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "CI",
        "NODE",
        "NODE_PATH",
        "NODE_OPTIONS",
        "NVM_DIR",
        "NVM_BIN",
        "NVM_INC",
        "PNPM_HOME",
        "npm_config_cache",
        "COREPACK_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        "CHROME_PATH",
        "CHROME_BIN",
        "PUPPETEER_CACHE_DIR",
        "CYPRESS_CACHE_FOLDER",
        "VITEST",
    }
)

# 前缀放行：浏览器自动化框架的运行配置族（不含任何凭据语义的键族）。
E2E_CHILD_ENV_PREFIX_ALLOWLIST: tuple[str, ...] = (
    "PLAYWRIGHT_",
    "PUPPETEER_",
    "CHROMEDRIVER_",
    "GECKODRIVER_",
    "E2E_",
)


def build_e2e_child_env(extra_allowed: Iterable[str] = ()) -> dict[str, str]:
    """构建浏览器 E2E 子进程的白名单过滤环境。

    与 :func:`build_sanitized_child_env` 的"透传 + denylist"相反，本函数是
    默认关闭的白名单：只保留 :data:`E2E_CHILD_ENV_BASE_ALLOWLIST`、命中
    :data:`E2E_CHILD_ENV_PREFIX_ALLOWLIST` 前缀、或运营者在验证命令条目上
    显式追加（``extra_allowed``，对应配置的 ``env_allow``）的变量。runner
    注入的凭据（GitHub token、模型 API key 等）因不在白名单内而对验证脚本
    不可见；如确有需要，必须由运营者在配置中逐名追加，白名单变化可在
    配置 diff 中审计。

    Args:
        extra_allowed: 追加放行的变量名（来自 E2E 条目的 ``env_allow``）。

    Returns:
        仅含放行变量的环境副本（新 dict，不修改 ``os.environ``）。
    """
    allowed_names = set(E2E_CHILD_ENV_BASE_ALLOWLIST)
    allowed_names.update(E2E_CHILD_ENV_PREFIX_ALLOWLIST)
    allowed_names.update(name for name in extra_allowed if name)
    filtered: dict[str, str] = {}
    dropped_credentials: list[str] = []
    for key, value in os.environ.items():
        if key in allowed_names or key.startswith(E2E_CHILD_ENV_PREFIX_ALLOWLIST):
            filtered[key] = value
        elif _looks_like_credential(key):
            dropped_credentials.append(key)
    if dropped_credentials:
        # 只记变量名不记值，提示运营者哪个凭据类变量被白名单挡住（预期行为）。
        logger.info(
            "E2E child env whitelist dropped credential-like vars: %s",
            sorted(dropped_credentials),
        )
    return filtered


def _looks_like_credential(env_key: str) -> bool:
    """判断变量名是否携带凭据语义，仅用于诊断日志分类。"""
    upper_key = env_key.upper()
    return any(
        token in upper_key
        for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "AUTH")
    )
