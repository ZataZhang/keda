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
  （``{cwd}`` / ``{worktree}`` / ``{prompt}`` / ``{model}`` / ``{effort}``），
  展开器是代码内命名的闭集（目前仅 ``git_writable_roots``），与本仓
  ``verification_commands`` 的既有信任边界一致。
- 任何一步失败（agent 未注册 / profile 未定义 / 未知展开器）抛带
  上下文的异常，不降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.agent_spec import (
    INTERACTIVE_PROFILE_ID,
    PROMPT_DELIVERY_ARGV_TAIL,
    PROMPT_DELIVERY_FLAG,
    PROMPT_DELIVERY_NONE,
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


class ModelNotSupportedError(ValueError):
    """命中模型绑定、但该 agent 未声明对应的模型参数模板（fail-fast）。

    静默忽略会让"切了模型"成为假象，因此模板缺失一律显式报错并指名 agent。
    """


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
        resumed_session_id: 本次调用实际注入的续传会话 id；``None`` 表示未续传
            （agent 未声明能力、无会话记录，或调用方未请求）。调用侧据此判断
            resume 是否真的生效（降级全新会话时该字段为 ``None``）。
    """

    agent_name: str
    profile: str
    argv: tuple[str, ...]
    prompt_delivery: str
    output_protocol: str
    cwd: Path
    read_only: bool
    resumed_session_id: str | None = None


# ---------------------------------------------------------------------------
# 占位符（代码内闭集，配置侧不可自定义）
# ---------------------------------------------------------------------------

_PLACEHOLDER_CWD = "{cwd}"
_PLACEHOLDER_WORKTREE = "{worktree}"
_PLACEHOLDER_PROMPT = "{prompt}"
_PLACEHOLDER_MODEL = "{model}"
_PLACEHOLDER_EFFORT = "{effort}"
_PLACEHOLDER_SESSION_ID = "{session_id}"


def _expand_placeholders(
    argv_fragment: str,
    *,
    worktree_path: Path,
    prompt: str,
    model_value: str | None = None,
    effort_value: str | None = None,
    session_value: str | None = None,
) -> str:
    """替换占位符为字面值；不认识的占位符原样保留（不猜测语义）。

    ``{model}`` / ``{effort}`` / ``{session_id}`` 只在对应值非空时替换——无模型
    绑定、无续传会话的调用（``model_selection is None`` /
    ``resume_session_id is None``）经过本函数时输出与旧版逐字节一致。
    """
    expanded_fragment = (
        argv_fragment.replace(_PLACEHOLDER_CWD, str(worktree_path))
        .replace(_PLACEHOLDER_WORKTREE, str(worktree_path))
        .replace(_PLACEHOLDER_PROMPT, prompt)
    )
    if model_value is not None:
        expanded_fragment = expanded_fragment.replace(_PLACEHOLDER_MODEL, model_value)
    if effort_value is not None:
        expanded_fragment = expanded_fragment.replace(_PLACEHOLDER_EFFORT, effort_value)
    if session_value is not None:
        expanded_fragment = expanded_fragment.replace(_PLACEHOLDER_SESSION_ID, session_value)
    return expanded_fragment


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
            f"Add an [agent_runner.agents.{agent_name}] block to config.toml / .kedacode.toml."
        )
    return agent_spec


def resolve_profile_spec(
    agent_name: str,
    profile: str,
    config: AppConfig,
) -> AgentProfileSpec:
    """返回 agent 指定用途的 spec；未声明时抛 :class:`UnknownProfileError`。

    ``profile`` 为 :data:`INTERACTIVE_PROFILE_ID` 时读的是 agent 上独立的
    ``interactive`` 声明（原生 TUI 不是第四种提示词投递用途，不进
    ``profiles`` 闭集，见该常量的定义），未声明即报错并指名要写的配置段——
    绝不拿 ``run`` 的无人值守形态冒充交互界面。
    """
    agent_spec = resolve_agent_spec(agent_name, config)
    if profile == INTERACTIVE_PROFILE_ID:
        interactive_spec = agent_spec.interactive
        if interactive_spec is None:
            raise UnknownProfileError(
                f"Agent '{agent_name}' does not declare an interactive profile, so it "
                f"cannot run as the native terminal executor for `kc`. Declared "
                f"non-interactive profiles: {', '.join(agent_spec.profiles) or 'none'}. "
                f"To enable it, add an [agent_runner.agents.{agent_name}.interactive] "
                f"block with the argv its terminal UI actually accepts, or pick an "
                f"agent that declares one (see `kc agent list`)."
            )
        return interactive_spec
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


def _model_selection_argv(
    agent_name: str,
    model_selection: ModelSelection,
    agent_spec: AgentSpec,
    *,
    worktree_path: Path,
    prompt: str,
) -> list[str]:
    """按 agent 级模板把模型/推理档参数展开成 argv 片段。

    模板缺失即 fail-fast（:class:`ModelNotSupportedError`），绝不静默忽略；
    ``model`` / ``reasoning_effort`` 为 ``None`` 的字段各自跳过，不注入。
    """
    argv_fragment: list[str] = []
    if model_selection.model is not None:
        if not agent_spec.model_args:
            raise ModelNotSupportedError(
                f"agent '{agent_name}' has no model_args template, so the model binding "
                f"(model='{model_selection.model}') cannot be applied. Declare "
                f"[agent_runner.agents.{agent_name}].model_args "
                '(e.g. ["--model", "{model}"]) in config.toml / .kedacode.toml.'
            )
        for template_entry in agent_spec.model_args:
            argv_fragment.append(
                _expand_placeholders(
                    template_entry,
                    worktree_path=worktree_path,
                    prompt=prompt,
                    model_value=model_selection.model,
                )
            )
    if model_selection.reasoning_effort is not None:
        if not agent_spec.reasoning_effort_args:
            raise ModelNotSupportedError(
                f"agent '{agent_name}' has no reasoning_effort_args template, so the "
                f"reasoning effort binding (effort='{model_selection.reasoning_effort}') "
                "cannot be applied. Declare "
                f"[agent_runner.agents.{agent_name}].reasoning_effort_args in "
                "config.toml / .kedacode.toml."
            )
        for template_entry in agent_spec.reasoning_effort_args:
            argv_fragment.append(
                _expand_placeholders(
                    template_entry,
                    worktree_path=worktree_path,
                    prompt=prompt,
                    effort_value=model_selection.reasoning_effort,
                )
            )
    return argv_fragment


def build_agent_invocation(
    agent_name: str,
    profile: str,
    prompt: str,
    worktree_path: Path,
    config: AppConfig,
    model_selection: ModelSelection | None = None,
    resume_session_id: str | None = None,
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
        model_selection: 可选的模型选择（阶段预设绑定解析结果）；
            ``None`` 表示不注入任何模型参数，argv 与未启用预设时逐字节一致。
        resume_session_id: 可选的会话续传 id（崩溃对账 / recovery 轮次回填上轮
            session）。仅当该 agent 声明 ``supports_resume`` 且给出
            ``resume_args`` 模板时注入；否则静默忽略，调用方经
            ``AgentInvocation.resumed_session_id`` 判断是否真的续传。

    Returns:
        AgentInvocation: 组装结果；调用方把它交给执行层（进程执行器或
        输出协议注册表）。

    Raises:
        UnknownAgentError: agent 未注册。
        UnknownProfileError: agent 未声明该用途。
        UnknownExpanderError: ``expand`` 引用了未命名展开器。
        ModelNotSupportedError: 命中模型绑定但该 agent 未声明对应模板。
        ValueError: ``prompt_delivery="flag"`` 但未声明 ``prompt_flag``。
    """
    agent_spec = resolve_agent_spec(agent_name, config)
    profile_spec = resolve_profile_spec(agent_name, profile, config)
    worktree_path = Path(worktree_path)

    argv: list[str] = [agent_spec.bin]
    for arg in profile_spec.args:
        argv.append(_expand_placeholders(arg, worktree_path=worktree_path, prompt=prompt))
    resumed_session_id: str | None = None
    if resume_session_id and agent_spec.supports_resume and agent_spec.resume_args:
        # 续传模板与模型模板共用同一套占位符展开；注入点固定在基础 args 之后、
        # 模型参数之前，保证提示词的尾部投递位置不变。
        resumed_session_id = resume_session_id
        for resume_entry in agent_spec.resume_args:
            argv.append(
                _expand_placeholders(
                    resume_entry,
                    worktree_path=worktree_path,
                    prompt=prompt,
                    session_value=resume_session_id,
                )
            )
    if model_selection is not None:
        argv.extend(
            _model_selection_argv(
                agent_name,
                model_selection,
                agent_spec,
                worktree_path=worktree_path,
                prompt=prompt,
            )
        )
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
        # 空提示词不占位置参数：原生入口在 bootstrap_enabled = false 时 prompt 为空串，
        # 传 "" 会被 provider 当成"用户提的一条消息"，那是凭空多出来的一轮。
        if prompt:
            argv.append(prompt)
    elif profile_spec.prompt_delivery == PROMPT_DELIVERY_FLAG:
        if not profile_spec.prompt_flag:
            raise ValueError(
                f"Agent '{agent_name}' profile '{profile}' uses prompt_delivery='flag' "
                f"but does not declare prompt_flag."
            )
        if prompt:
            argv.extend([profile_spec.prompt_flag, prompt])
    elif profile_spec.prompt_delivery == PROMPT_DELIVERY_NONE:
        # 交互入口没有可核实的 bootstrap 投递通道：不投递，也不假装投递。
        pass
    # prompt_delivery == "stdin"：提示词不进 argv，由执行层写 stdin。

    return AgentInvocation(
        agent_name=agent_name,
        profile=profile,
        argv=tuple(argv),
        prompt_delivery=profile_spec.prompt_delivery,
        output_protocol=profile_spec.output_protocol,
        cwd=worktree_path,
        read_only=profile_spec.read_only,
        resumed_session_id=resumed_session_id,
    )


__all__ = [
    "AgentInvocation",
    "ModelNotSupportedError",
    "UnknownAgentError",
    "UnknownExpanderError",
    "UnknownProfileError",
    "build_agent_invocation",
    "resolve_agent_spec",
    "resolve_profile_spec",
    "resolve_registered_agents",
]
