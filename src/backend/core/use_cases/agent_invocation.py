"""全仓库唯一的 agent argv 构造器（纯函数）。

:func:`build_agent_invocation` 从 ``config.agents[agent_name].profiles[profile]``
取出声明式 spec，组装 argv 并决定提示词投递方式，返回
:class:`AgentInvocation`。三处历史构造点（``run_agent_once`` 主执行、
``transcript_runner`` 辩论、``content_generators`` 内容生成/REPL）
全部收敛到这里。

设计约束：

- **无插件加载、无配置加载、无子进程**——协议 id 只作为数据返回，
  解析交给执行层的注册表；唯一的"环境读取"是 ``git_writable_roots``
  展开器读取 worktree 的 ``.git`` 指针文件（保留既有行为）。
- **不做 shell 展开**——argv 元素是字面量，占位符是闭集
  （``{cwd}`` / ``{worktree}`` / ``{prompt}``），展开器是代码内命名的
  闭集（目前仅 ``git_writable_roots``），与本仓 ``verification_commands``
  的既有信任边界一致。
- 任何一步失败（agent 未注册 / profile 未定义 / 未知展开器）抛带
  上下文的异常，不降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    PROMPT_DELIVERY_ARGV_TAIL,
    PROMPT_DELIVERY_FLAG,
    AgentProfileSpec,
    AgentSpec,
)

# ---------------------------------------------------------------------------
# 异常（全部是 ValueError 子类，调用方可统一按 ValueError 捕获）
# ---------------------------------------------------------------------------


class UnknownAgentError(ValueError):
    """请求的 agent 未在注册表中声明。"""


class UnknownProfileError(ValueError):
    """请求的 agent 未声明该用途的调用形态。"""


class UnknownExpanderError(ValueError):
    """配置引用了未命名的展开器。"""


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentInvocation:
    """一次 agent 调用的完整形态。

    Attributes:
        agent_name: agent 注册名。
        profile: 用途名（run / deliberate / generate / repl）。
        argv: 组装后的最终命令。``prompt_delivery="stdin"`` 时不含提示词，
            由执行层写 stdin；其余投递方式提示词已在 argv 中。
        prompt_delivery: 提示词投递方式（argv_tail / flag / stdin）。
        output_protocol: 输出协议 id，执行层据此从注册表取中继实现。
        cwd: 子进程工作目录。
        read_only: 该调用是否为声明的只读用途。
    """

    agent_name: str
    profile: str
    argv: tuple[str, ...]
    prompt_delivery: str
    output_protocol: str
    cwd: Path
    read_only: bool


# ---------------------------------------------------------------------------
# 占位符（闭集，不可扩展）
# ---------------------------------------------------------------------------

_PLACEHOLDER_CWD = "{cwd}"
_PLACEHOLDER_WORKTREE = "{worktree}"
_PLACEHOLDER_PROMPT = "{prompt}"


def _expand_placeholders(
    argv_fragment: str,
    *,
    worktree_path: Path,
    prompt: str,
) -> str:
    """替换占位符为字面值；不认识的占位符原样保留（不猜测语义）。"""
    return (
        argv_fragment.replace(_PLACEHOLDER_CWD, str(worktree_path))
        .replace(_PLACEHOLDER_WORKTREE, str(worktree_path))
        .replace(_PLACEHOLDER_PROMPT, prompt)
    )


# ---------------------------------------------------------------------------
# 展开器（闭集，代码内命名）
# ---------------------------------------------------------------------------


def _resolve_worktree_git_writable_roots(worktree_path: Path) -> list[str]:
    """解析 linked worktree 需要额外放行写入的 git 元数据目录。

    `git worktree add` 建出的 worktree 里 `.git` 是一个指向主仓
    `.git/worktrees/<name>/` 的指针文件，而该目录落在 codex `--cd` 的可写根之外。
    不放行会让 lint flag（`scripts/shared/hooks/quality_flag.sh` 写
    `.last_linted_commit`）等 git 侧写入报 `Operation not permitted`。

    Args:
        worktree_path: worktree 根目录。

    Returns:
        需要放行写入的绝对路径列表。普通 checkout（`.git` 是目录，本就在可写根内）
        或指针文件无法解析时返回空列表。
    """
    git_pointer_path = worktree_path / ".git"
    if not git_pointer_path.is_file():
        return []
    try:
        pointer_text = git_pointer_path.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    gitdir_prefix = "gitdir:"
    if not pointer_text.startswith(gitdir_prefix):
        return []
    worktree_git_dir = Path(pointer_text[len(gitdir_prefix) :].strip())
    if not worktree_git_dir.is_absolute():
        worktree_git_dir = worktree_path / worktree_git_dir
    writable_roots = [worktree_git_dir]
    common_dir_pointer_path = worktree_git_dir / "commondir"
    if common_dir_pointer_path.is_file():
        try:
            common_dir = Path(common_dir_pointer_path.read_text(encoding="utf-8").strip())
        except OSError:
            common_dir = None
        if common_dir is not None:
            if not common_dir.is_absolute():
                common_dir = worktree_git_dir / common_dir
            writable_roots.append(common_dir)
    resolved_roots = (root.resolve() for root in writable_roots)
    return [str(root) for root in dict.fromkeys(resolved_roots)]


def _expand_git_writable_roots(worktree_path: Path) -> list[str]:
    """``git_writable_roots`` 展开器：返回需放行写入的 git 元数据目录。"""
    return _resolve_worktree_git_writable_roots(worktree_path)


_EXPANDERS: dict[str, object] = {
    "git_writable_roots": _expand_git_writable_roots,
}


# ---------------------------------------------------------------------------
# 核心构造函数
# ---------------------------------------------------------------------------


def resolve_agent_spec(agent_name: str, config: AppConfig) -> AgentSpec:
    """返回 agent 的 spec；未注册时抛 :class:`UnknownAgentError`。"""
    agent_spec = config.agents.get(agent_name)
    if agent_spec is None:
        raise UnknownAgentError(
            f"Agent '{agent_name}' is not registered. "
            f"Registered agents: {', '.join(resolve_registered_agents(config))}. "
            f"Add an [agent_runner.agents.{agent_name}] block to config.toml / .iar.toml."
        )
    return agent_spec


def resolve_profile_spec(
    agent_name: str,
    profile: str,
    config: AppConfig,
) -> AgentProfileSpec:
    """返回 agent 指定用途的 spec；未声明时抛 :class:`UnknownProfileError`。"""
    agent_spec = resolve_agent_spec(agent_name, config)
    profile_spec = agent_spec.profiles.get(profile)
    if profile_spec is None:
        raise UnknownProfileError(
            f"Agent '{agent_name}' has no '{profile}' profile. "
            f"Declared profiles: {', '.join(agent_spec.profiles)}."
        )
    return profile_spec


def resolve_registered_agents(config: AppConfig) -> list[str]:
    """按注册顺序返回全部已注册 agent 名（CLI choices 与校验共用）。"""
    return list(config.agents)


def build_agent_invocation(
    agent_name: str,
    profile: str,
    prompt: str,
    worktree_path: Path,
    config: AppConfig,
) -> AgentInvocation:
    """按声明式 spec 组装一次 agent 调用。

    Args:
        agent_name: agent 注册名（如 ``"claude"``）。
        profile: 用途名（``"run"`` / ``"deliberate"`` / ``"generate"`` /
            ``"repl"``）。
        prompt: 完整提示词文本。
        worktree_path: 子进程工作目录；``{cwd}`` / ``{worktree}`` 占位符
            与 ``git_writable_roots`` 展开器都以它为锚。
        config: 应用配置，注册表取自 ``config.agents``。

    Returns:
        AgentInvocation: 组装结果；调用方把它交给执行层（进程执行器或
        输出协议注册表）。

    Raises:
        UnknownAgentError: agent 未注册。
        UnknownProfileError: agent 未声明该用途。
        UnknownExpanderError: ``expand`` 引用了未命名展开器。
        ValueError: ``prompt_delivery="flag"`` 但未声明 ``prompt_flag``。
    """
    agent_spec = resolve_agent_spec(agent_name, config)
    profile_spec = resolve_profile_spec(agent_name, profile, config)
    worktree_path = Path(worktree_path)

    argv: list[str] = [agent_spec.bin]
    for arg in profile_spec.args:
        argv.append(_expand_placeholders(arg, worktree_path=worktree_path, prompt=prompt))
    for expand_entry in profile_spec.expand:
        expander_name, separator, flag = expand_entry.partition(":")
        expander = _EXPANDERS.get(expander_name)
        if expander is None or not callable(expander):
            raise UnknownExpanderError(
                f"Unknown expander '{expander_name}' in "
                f"agents.{agent_name}.profiles.{profile}.expand. "
                f"Known expanders: {', '.join(_EXPANDERS)}."
            )
        if not separator:
            raise UnknownExpanderError(
                f"Malformed expander entry '{expand_entry}' (expected "
                f"'<expander_name>:<flag>') in "
                f"agents.{agent_name}.profiles.{profile}.expand."
            )
        expanded_values = expander(worktree_path)
        for expanded_value in expanded_values:
            argv.extend([flag, expanded_value])
    for tail_arg in profile_spec.tail_args:
        argv.append(_expand_placeholders(tail_arg, worktree_path=worktree_path, prompt=prompt))

    if profile_spec.prompt_delivery == PROMPT_DELIVERY_ARGV_TAIL:
        argv.append(prompt)
    elif profile_spec.prompt_delivery == PROMPT_DELIVERY_FLAG:
        if not profile_spec.prompt_flag:
            raise ValueError(
                f"Agent '{agent_name}' profile '{profile}' uses prompt_delivery='flag' "
                f"but does not declare prompt_flag."
            )
        argv.extend([profile_spec.prompt_flag, prompt])
    # prompt_delivery == "stdin"：提示词不进 argv，由执行层写 stdin。

    return AgentInvocation(
        agent_name=agent_name,
        profile=profile,
        argv=tuple(argv),
        prompt_delivery=profile_spec.prompt_delivery,
        output_protocol=profile_spec.output_protocol,
        cwd=worktree_path,
        read_only=profile_spec.read_only,
    )


__all__ = [
    "AgentInvocation",
    "UnknownAgentError",
    "UnknownExpanderError",
    "UnknownProfileError",
    "build_agent_invocation",
    "resolve_agent_spec",
    "resolve_profile_spec",
    "resolve_registered_agents",
]
