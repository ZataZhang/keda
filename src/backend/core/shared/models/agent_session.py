"""裸 ``kc`` 原生执行器入口与按需项目预览的声明式领域模型。

本模块只放**纯数据与纯校验**：入口的配置视图（默认 executor、bootstrap 开关）、
一次原生会话的启动计划（argv / cwd / bootstrap / skill 目录），以及项目预览的
受控声明（精确 argv、ready URL 回环校验、进程记录）。真正的进程动作在
:mod:`backend.core.shared.interfaces.agent_session` 的端口另一端。

设计约束（Issue #256）：

- **交互能力是声明，不是猜测**：``NativeSessionPlan`` 只由 agent registry 的
  ``interactive`` 声明生成；未声明的 agent 一律报错，绝不复用非交互 profile。
- **预览只执行精确 argv**：配置里存的永远是 argv 数组而非 shell 文本，且 ready
  地址的主机必须落在 :data:`LOOPBACK_PREVIEW_HOSTS` 内。非回环主机是加载期错误，
  因此"绑到可路由网卡"这条路径在任何进程启动之前就被排除。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

#: 预览 ready 地址允许的主机闭集（仅回环）。这是该规则的**唯一**来源：
#: 配置层（``infrastructure/config``）与执行层都从这里取，避免两份白名单漂移。
LOOPBACK_PREVIEW_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")


def parse_preview_url(url_text: str) -> tuple[str, int | None, str]:
    """把预览地址拆成 ``(host, port, path)``，不合规时抛 :class:`ValueError`。

    只接受带 scheme 的 http(s) 绝对地址：进程自报的往往是 ``http://127.0.0.1:3000/``
    这一整串，而相对地址既无法判定主机、也无法判定端口，属于"没有报告地址"。

    Args:
        url_text: 待解析的地址文本（配置值或子进程自报值）。

    Returns:
        ``(host, port, path)``；``port`` 在 URL 未显式给出时为 ``None``
        （由 scheme 隐含，调用方不需要猜）。

    Raises:
        ValueError: 地址为空、缺 scheme，或 scheme 不是 http/https。
    """
    if not url_text or not url_text.strip():
        raise ValueError("Preview URL must be a non-empty http(s) URL.")
    parsed_url = urlsplit(url_text.strip())
    if not parsed_url.scheme:
        raise ValueError(
            f"Preview URL {url_text!r} must include a scheme (http:// or https://); "
            "a relative path does not identify the host."
        )
    if parsed_url.scheme not in ("http", "https"):
        raise ValueError(
            f"Preview URL {url_text!r} uses scheme {parsed_url.scheme!r}; "
            "only http(s) preview addresses are accepted."
        )
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError("Preview URL must not contain user information or credentials.")
    host = (parsed_url.hostname or "").strip("[]")
    if not host:
        raise ValueError(f"Preview URL {url_text!r} does not name a host.")
    return host, parsed_url.port, parsed_url.path or "/"


def is_loopback_preview_url(url_text: str) -> bool:
    """该预览地址是否指向回环主机（可安全回复给终端用户）。"""
    try:
        host, _port, _path = parse_preview_url(url_text)
    except ValueError:
        return False
    return host in LOOPBACK_PREVIEW_HOSTS


def require_loopback_preview_url(url_text: str, *, source: str) -> str:
    """校验预览地址是回环 http(s) 地址并返回规范化文本，否则抛错。

    Args:
        url_text: 待校验地址。
        source: 报错上下文（配置字段名或"进程自报"），让失败信息指名来源。

    Returns:
        去除首尾空白后的地址文本。

    Raises:
        ValueError: 地址不合规，或主机不属于 :data:`LOOPBACK_PREVIEW_HOSTS`。
    """
    host, _port, _path = parse_preview_url(url_text)
    if host not in LOOPBACK_PREVIEW_HOSTS:
        raise ValueError(
            f"{source} must point at a loopback host "
            f"({', '.join(LOOPBACK_PREVIEW_HOSTS)}); got host {host!r} in {url_text!r}. "
            "A preview dev server reachable from outside this machine is out of scope."
        )
    return url_text.strip()


@dataclass(frozen=True)
class PreviewProfile:
    """``[agent_session.preview]`` 的运行时视图（仓库层已覆盖全局层）。

    Attributes:
        argv: 已批准的 dev 命令 argv；空表示仓库未声明，走"唯一候选 + 人工确认"路径。
        ready_url: 期望的 ready 地址（已校验为回环）；``None`` 表示只信任进程自报。
        ready_timeout_seconds: 等待地址就绪的上限。
    """

    argv: tuple[str, ...] = ()
    ready_url: str | None = None
    ready_timeout_seconds: int = 60

    @property
    def is_declared(self) -> bool:
        """仓库是否显式声明了预览命令（决定 ``kc preview start`` 走哪条路径）。"""
        return bool(self.argv)


@dataclass(frozen=True)
class AgentSessionConfig:
    """``[agent_session]`` 的运行时视图。

    Attributes:
        default_agent: 裸 ``kc`` 使用的执行器注册名（``--agent`` 覆盖它）。
        bootstrap_enabled: 是否向 provider 投递 operator bootstrap 说明。
        skill_install_check_enabled: 启动前是否用 fail-closed 安装器校验随包 skill。
        preview: 按需项目预览的受控声明。
    """

    default_agent: str = "claude"
    bootstrap_enabled: bool = True
    skill_install_check_enabled: bool = True
    preview: PreviewProfile = field(default_factory=PreviewProfile)


@dataclass(frozen=True)
class NativeSessionPlan:
    """一次原生交互会话的启动计划（纯数据，不含进程动作）。

    Attributes:
        agent_name: 选中的执行器注册名。
        argv: 完整 argv（``bin`` 在前），由声明式 interactive profile 组装。
        cwd: 子进程工作目录（目标仓库根，KC 不改工作区）。
        bootstrap_text: 投递给 provider 的启动说明；未投递时为空串。
        skills_dir: 期望随会话可用的用户级 operator skill 目录；``None`` 表示该
            agent 未声明 ``auth_home``，只能依赖项目级 skill 路径。
        model_selection: 可选的模型/推理档选择，按该 agent 声明式模板注入 argv；
            为空时 argv 与"未启用模型选择"逐字节一致。
    """

    agent_name: str
    argv: tuple[str, ...]
    cwd: Path
    bootstrap_text: str = ""
    skills_dir: Path | None = None
    model_selection: object | None = None


@dataclass(frozen=True)
class NativeSessionResult:
    """原生会话结束后的事实。

    Attributes:
        agent_name: 实际启动的执行器注册名。
        argv: 实际用于启动的 argv。
        exit_code: provider 进程的退出码（原样转发给 ``kc`` 自己）。
        timed_out: 是否由 KC 的等待上限结束（交互会话默认不设上限）。
    """

    agent_name: str
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool = False


@dataclass(frozen=True)
class PreviewProcessRecord:
    """KC 持有的预览进程记录（stop 时的所有权判据）。

    ``pid`` / ``process_group`` / ``process_started_at`` / ``argv`` 共同构成
    "这条记录确实描述当前那个由 KC 启动的进程组"的证据：PID 或 PGID 被复用后，
    创建时刻不匹配时 stop 必须拒绝而不是盲杀。

    Attributes:
        key: 注册表键（固定为单槽位 ``"current"``，预览同一仓库同时只允许一个）。
        pid: 直接子进程 pid（独立进程组的组长，组 id 等于它）。
        process_group: 记录的进程组 id。
        argv: 启动用的精确 argv。
        cwd: 启动时的工作目录（目标仓库根）。
        log_path: 输出落盘路径（ready 地址从这里解析）。
        url: 已验证的 loopback ready 地址；未就绪时为 ``None``。
        started_at_iso: 启动时间（ISO8601，人读）。
        started_at_mono: 启动时的单调时钟值（用于计算存活时长，不受系统改时影响）。
        owner_pid: 启动该进程的 KC 进程 pid（跨进程 stop 时识别持有者）。
        process_started_at: psutil 读取的操作系统进程创建时刻；缺失时所有权不可证实。
    """

    key: str
    pid: int
    process_group: int
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    url: str | None = None
    started_at_iso: str = ""
    started_at_mono: float = 0.0
    owner_pid: int = 0
    process_started_at: float | None = None


class PreviewState:
    """``kc preview status`` 的状态取值（闭集，便于机器输出与终端呈现）。"""

    NONE = "none"
    """注册表里没有记录（未启动过，或已 stop）。"""

    STARTING = "starting"
    """进程活着但还没有验证过的 loopback 地址。"""

    READY = "ready"
    """进程活着且已报告回环地址。"""

    EXITED = "exited"
    """记录还在，但进程已经不在了（陈旧记录，不触发任何 kill）。"""

    FOREIGN = "foreign"
    """记录存在，但实时读到的 pid/组与记录不符（所有权不可证实，拒绝操作）。"""


__all__ = [
    "LOOPBACK_PREVIEW_HOSTS",
    "AgentSessionConfig",
    "NativeSessionPlan",
    "NativeSessionResult",
    "PreviewProcessRecord",
    "PreviewProfile",
    "PreviewState",
    "is_loopback_preview_url",
    "parse_preview_url",
    "require_loopback_preview_url",
]
