"""裸 ``kc`` 原生执行器会话的准备（engines 层）。

Issue #256 的入口侧：把「配置的默认执行器 + 声明式 interactive profile +
operator skill + 仓库 cwd」组装成一份可启动的 :class:`NativeSessionPlan`。
真正的进程动作在端口另一端
（:class:`backend.core.shared.interfaces.agent_session.IForegroundSessionLauncher`），
本模块只负责**决定启动什么**，因此可以被单元测试完整覆盖。

三条入口纪律：

- **能力来自声明**：只有 agent 注册表里显式声明了 ``interactive`` 才能作为原生
  TUI 启动。未声明就报错并给出可行动的修复路径，绝不拿 ``run`` profile
  （``codex exec`` / ``claude -p`` 这类非交互形态）冒充交互会话，也不静默换成
  权限更宽的跑法。
- **不附加权限**：交互 argv 只含 provider 的原生界面参数，不含无人值守 runner 的
  ``--dangerously-skip-permissions`` / ``exec`` 子命令，provider 自己的审批照旧。
- **skill 不覆盖用户内容**：复用 ``kc init`` 那条 fail-closed 安装路径；用户改动过
  的随包 skill 保留并说明，不 ``force``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_session import NativeSessionPlan
from backend.core.shared.models.agent_spec import (
    INTERACTIVE_PROFILE_ID,
    PROMPT_DELIVERY_NONE,
)
from backend.core.use_cases.agent_invocation import build_agent_invocation
from backend.engines.agent_runner.remote_template_skills import (
    RemoteTemplateSkillInstallError,
    install_packaged_operator_skill,
)

if TYPE_CHECKING:
    from backend.core.shared.models.agent_model_preset import ModelSelection
    from backend.core.shared.models.agent_runner import AppConfig

#: ``--agent auto`` 不是执行器名：入口侧按「未覆盖」处理并说明。
_AUTO_AGENT_PLACEHOLDER = "auto"


@dataclass(frozen=True)
class NativeSessionPreparation:
    """一次原生会话的准备结果。

    Attributes:
        plan: 可直接交给前台启动端口的启动计划。
        notices: 需要在终端向用户说明的事实（skill 冲突保留、``auto`` 回落等）。
    """

    plan: NativeSessionPlan
    notices: tuple[str, ...] = ()


def build_operator_bootstrap(*, repo_root: Path, skills_dir: Path | None) -> str:
    """构造投递给执行器的短 bootstrap 说明（不含凭据、不含完整 CLI 手册）。

    Args:
        repo_root: 当前仓库根，执行器的工作目录。
        skills_dir: 已核实可用的 operator skill 目录；``None`` 表示该执行器未声明
            用户级 skills 目录，只能依赖仓库内的说明文件。

    Returns:
        多行纯文本；由声明式 ``prompt_delivery`` 决定它进 argv 还是被忽略。
    """
    skill_hint = (
        f"The `{product_identity.OPERATOR_SKILL_NAME}` skill (installed at {skills_dir}) "
        "documents the `kc` command surface, its guards and the `kc preview` boundary."
        if skills_dir is not None
        else "The packaged KedaCode operator skill is not discoverable for this executor; "
        "read AGENTS.md in the repository instead."
    )
    return (
        f"This terminal session was started by KedaCode (`kc`) for the repository at "
        f"{repo_root}. You are the native executor: keep your own permission, sandbox and "
        f"approval settings — KedaCode injects none. {skill_hint} "
        "KedaCode runs no chat server and no project dev server: never start one unless the "
        "user explicitly asks to preview the project, and then call `kc preview start` "
        "(loopback URL only, reply with the address and let the user open it). Use "
        "`kc preview status` / `kc preview stop` to manage it, and keep Issue/PR operations on "
        "`kc` subcommands rather than hand-built gh pipelines."
    )


def prepare_native_session_plan(
    *,
    config: AppConfig,
    repo_root: Path,
    agent_override: str | None = None,
    model_selection: ModelSelection | None = None,
) -> NativeSessionPreparation:
    """解析执行器、校验交互能力、核对 operator skill 并组装启动计划。

    Args:
        config: 目标仓库的生效配置（注册表、``[agent_session]`` 都在这里）。
        repo_root: 作为执行器 cwd 的仓库根。
        agent_override: ``--agent`` 取值；``None``/``"auto"`` 表示不覆盖默认值。
        model_selection: 可选的模型/推理档选择，按该 agent 的声明式模板注入 argv。

    Returns:
        NativeSessionPreparation：启动计划与需要向用户说明的事实。

    Raises:
        UnknownAgentError: ``--agent`` 指定了未注册的执行器。
        UnknownProfileError: 选中的执行器未声明 interactive 能力（fail-fast）。
    """
    session_config = config.agent_session
    notices: list[str] = []
    requested_agent = (agent_override or "").strip()
    if requested_agent == _AUTO_AGENT_PLACEHOLDER:
        notices.append(
            "`--agent auto` does not name an executor; using the configured default "
            f"[agent_session].default_agent = {session_config.default_agent!r}."
        )
        requested_agent = ""
    agent_name = requested_agent or session_config.default_agent

    agent_spec = config.agents.get(agent_name)
    skills_dir = (
        agent_spec.user_skills_dir(Path.home())
        if agent_spec is not None and agent_spec.auth_home
        else None
    )
    if session_config.skill_install_check_enabled:
        notices.extend(_ensure_operator_skill(agent_name=agent_name, skills_dir=skills_dir))
    bootstrap_text = (
        build_operator_bootstrap(repo_root=repo_root, skills_dir=skills_dir)
        if session_config.bootstrap_enabled
        else ""
    )

    invocation = build_agent_invocation(
        agent_name,
        INTERACTIVE_PROFILE_ID,
        bootstrap_text,
        Path(repo_root),
        config,
        model_selection=model_selection,
    )
    plan = NativeSessionPlan(
        agent_name=agent_name,
        argv=invocation.argv,
        cwd=Path(repo_root),
        # 只记录**真的投出去**的说明：prompt_delivery="none" 的执行器拿不到它，
        # 计划里留着文本会让下游（测试、审计）误以为 bootstrap 已生效。
        bootstrap_text=(
            bootstrap_text if invocation.prompt_delivery != PROMPT_DELIVERY_NONE else ""
        ),
        skills_dir=skills_dir,
        model_selection=model_selection,
    )
    return NativeSessionPreparation(plan=plan, notices=tuple(notices))


def _ensure_operator_skill(*, agent_name: str, skills_dir: Path | None) -> tuple[str, ...]:
    """核对（必要时安装）该执行器可发现的 operator skill，返回需要展示的事实。

    语义与 ``kc init`` 完全一致（同一个安装器），差别只在**默认不 ``force``**：
    入口不该改写用户内容，改动过的随包副本保留并说明。安装器拒绝时（随包
    ``SKILL.md`` 缺失、目标目录是指向他处的符号链接）本次会话**不启动**并指名
    原因——这两种状态下"skill 内容可信"无法证明，而原生会话会把它当指令来源。

    Raises:
        RuntimeError: skill 目录状态无法核实（安装器 fail-closed 的两种情形）。
    """
    if skills_dir is None:
        return (
            f"Agent '{agent_name}' declares no user-level skills directory, so the packaged "
            "operator skill cannot be verified for it; the session relies on repository "
            "documentation instead.",
        )
    try:
        plan_result = install_packaged_operator_skill(
            target_skills_root=skills_dir, dry_run=True, force=False
        )
        if plan_result.action == "install":
            install_packaged_operator_skill(
                target_skills_root=skills_dir, dry_run=False, force=False
            )
            return (
                f"Installed the packaged `{product_identity.OPERATOR_SKILL_NAME}` skill at "
                f"{plan_result.target_path}.",
            )
    except RemoteTemplateSkillInstallError as install_exc:
        raise RuntimeError(
            f"Refusing to start the native session: {install_exc} "
            "Repair the skills directory (or set skill_install_check_enabled = false in "
            "[agent_session]) and try again."
        ) from install_exc
    if plan_result.action == "preserve-conflict":
        raise RuntimeError(
            f"Refusing to start the native session: the operator skill at "
            f"{plan_result.target_path} differs from the packaged version. Your edits were "
            "kept and nothing was overwritten. Review the conflict, then explicitly repair "
            "the skill before starting a new session."
        )
    return (f"Operator skill at {plan_result.target_path} is up to date.",)


__all__ = [
    "NativeSessionPreparation",
    "build_operator_bootstrap",
    "prepare_native_session_plan",
]
