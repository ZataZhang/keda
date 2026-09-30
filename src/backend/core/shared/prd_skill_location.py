"""定位 prd skill 的安装位置与其脚本产物。

从 ``core/use_cases/generated_prd_content`` 搬过来：解析 skill 安装路径是"关于
skill 的事"，而消费它的一般是 ``core/shared`` 下的模块（如 PRD 格式解析客户端）。
留在 use_cases 里会让 shared 反向依赖 use_cases，并且 ``generated_prd_content``
本身就大量 import use_cases，容易成环。

这里刻意与 :mod:`backend.core.shared.prd_machine_contract` 分开：后者只依赖标准库
（架构验收要求），而本模块需要按 agent 注册表派生用户级 skills 目录。
"""

from __future__ import annotations

import os
from pathlib import Path

from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS

_PRD_SKILL_PATH_ENV_VAR = "IAR_PRD_SKILL_PATH"
_CC_SWITCH_SKILLS_DIR_ENV_VAR = "CC_SWITCH_SKILLS_DIR"
_PRD_SKILL_RELATIVE_PATH = Path("prd") / "SKILL.md"
_PRD_CONTRACT_SCRIPT_RELATIVE_PATH = Path("scripts") / "prd_contract.py"


def default_prd_skill_candidate_paths() -> tuple[Path, ...]:
    """按 agent 注册表派生 prd skill 的用户级候选路径。

    与 ``iar init`` 的安装目标同源（:meth:`AgentSpec.user_skills_dir`，
    即各 agent ``auth_home`` 下的 ``skills/``），注册顺序即优先级；
    不再包含已废弃的 ``~/.cc-switch/skills`` 固定候选。
    """
    user_home_path = Path.home()
    return tuple(
        skills_dir / _PRD_SKILL_RELATIVE_PATH
        for agent_spec in BUILTIN_AGENT_SPECS.values()
        if (skills_dir := agent_spec.user_skills_dir(user_home_path)) is not None
    )


def resolve_prd_skill_path(explicit_path: Path | None = None) -> Path:
    """解析模板安装器管理的 ``prd`` skill 路径。

    解析优先级：显式入参 → ``IAR_PRD_SKILL_PATH`` 环境变量 →
    ``CC_SWITCH_SKILLS_DIR`` → agent 注册表各 agent 的用户级 skills 目录
    （``auth_home`` 派生，与 ``iar init`` 安装目标同源）。
    默认候选中优先返回存在的 ``SKILL.md``；全部缺失时返回第一个候选，
    由调用方保留现有 fallback 行为。

    Args:
        explicit_path: 调用方显式指定的路径；为 ``None`` 时回落到环境变量/默认。

    Returns:
        待读取的 skill 规范文件路径（不保证存在）。
    """
    if explicit_path is not None:
        return explicit_path
    env_value = os.environ.get(_PRD_SKILL_PATH_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    configured_skills_root = os.environ.get(_CC_SWITCH_SKILLS_DIR_ENV_VAR)
    candidate_skill_paths: list[Path] = []
    if configured_skills_root:
        candidate_skill_paths.append(
            Path(configured_skills_root).expanduser() / _PRD_SKILL_RELATIVE_PATH
        )
    candidate_skill_paths.extend(default_prd_skill_candidate_paths())
    for candidate_skill_path in candidate_skill_paths:
        if candidate_skill_path.is_file():
            return candidate_skill_path
    return candidate_skill_paths[0]


def resolve_prd_contract_script(skill_path: Path | None = None) -> Path:
    """返回 prd skill 解析脚本 ``scripts/prd_contract.py`` 的路径（不保证存在）。

    PRD 格式的解析实现只有这一份，随 skill 一起安装；它的位置永远是
    ``SKILL.md`` 所在目录的兄弟 ``scripts/`` 下。

    Args:
        skill_path: 已解析的 ``SKILL.md`` 路径；``None`` 时按
            :func:`resolve_prd_skill_path` 解析。

    Returns:
        ``scripts/prd_contract.py`` 的路径。
    """
    resolved_skill_path = resolve_prd_skill_path(skill_path)
    return resolved_skill_path.parent / _PRD_CONTRACT_SCRIPT_RELATIVE_PATH
