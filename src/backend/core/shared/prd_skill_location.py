"""定位 prd skill 的安装位置与其脚本产物。

从 ``core/use_cases/generated_prd_content`` 搬过来：解析 skill 安装路径是"关于
skill 的事"，而消费它的一般是 ``core/shared`` 下的模块（如 PRD 格式解析客户端）。
留在 use_cases 里会让 shared 反向依赖 use_cases，并且 ``generated_prd_content``
本身就大量 import use_cases，容易成环。

**自有副本优先**：解析顺序把产品自己的 ``<本机状态目录>/skills/prd`` 排在 agent
用户级目录之前。PRD 解析是 runner 自身的能力，不该取决于 agent 目录是否还在——那些
目录归 agent / 用户所有，用户删掉里面的 prd skill 不应让 runner 失去解析能力。
``kc init`` 会把副本写进该自有目录（详见
:mod:`backend.engines.agent_runner.remote_template_skills`）。

这里刻意与 :mod:`backend.core.shared.prd_machine_contract` 分开：后者只依赖标准库
（架构验收要求），而本模块需要按 agent 注册表派生用户级 skills 目录。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS

_PRD_SKILL_PATH_ENV_SUFFIX = "PRD_SKILL_PATH"
_SKILLS_DIR_ENV_SUFFIX = "SKILLS_DIR"
_PRD_SKILL_RELATIVE_PATH = Path("prd") / "SKILL.md"
_PRD_CONTRACT_SCRIPT_RELATIVE_PATH = Path("scripts") / "prd_contract.py"

_SKILLS_ROOT_DIRECTORY_NAME = "skills"
"""状态目录下的 skill 根目录名。"""


def keda_owned_skills_root(user_home_path: Path | None = None) -> Path:
    """返回产品自有 skill 根目录 ``<本机状态目录>/skills``（不保证存在）。

    PRD 解析是 runner 自身的能力，不能只依赖各 agent 的用户级 skills 目录——那些
    目录归 agent / 用户所有，随时可能被删除或替换（实测：本机三个 agent 目录里的
    ``prd`` 都是指向共享副本的符号链接）。``kc init`` 会把一份副本写进产品自己的
    状态目录下，解析时优先取它，于是"用户删掉 agent 里的 prd skill"不再影响
    runner 正常工作。状态目录本身按新旧两个名字双读，因此迁移前后的机器都指向同一处。

    Args:
        user_home_path: 用户主目录覆盖，主要供测试使用；``None`` 时取实际主目录。

    Returns:
        自有 skill 根目录路径（不保证存在）。
    """
    return product_identity.state_home(user_home_path) / _SKILLS_ROOT_DIRECTORY_NAME


def default_prd_skill_candidate_paths(user_home_path: Path | None = None) -> tuple[Path, ...]:
    """按优先级返回 prd skill 的候选 ``SKILL.md`` 路径。

    优先级：``SKILLS_DIR`` 产品环境变量覆盖（设置时即唯一候选）→ 产品自有
    ``<状态目录>/skills`` → 各 agent 注册表派生的用户级 skills 目录（注册顺序即优先级）。

    ``SKILLS_DIR`` 覆盖是**安装根的覆盖**（CI / 测试把 skill 装到临时目录时用），
    设置后不再探测用户目录，避免测试跑到真实 home 下的安装。

    Args:
        user_home_path: 用户主目录覆盖，主要供测试使用；``None`` 时取实际主目录。

    Returns:
        候选路径元组（均不保证存在）。
    """
    configured_skills_root = product_identity.read_product_env_value(_SKILLS_DIR_ENV_SUFFIX)
    if configured_skills_root:
        return (Path(configured_skills_root).expanduser() / _PRD_SKILL_RELATIVE_PATH,)

    effective_home_path = Path.home() if user_home_path is None else user_home_path
    candidate_skill_paths: list[Path] = [
        keda_owned_skills_root(effective_home_path) / _PRD_SKILL_RELATIVE_PATH
    ]
    candidate_skill_paths.extend(
        skills_dir / _PRD_SKILL_RELATIVE_PATH
        for agent_spec in BUILTIN_AGENT_SPECS.values()
        if (skills_dir := agent_spec.user_skills_dir(effective_home_path)) is not None
    )
    return tuple(candidate_skill_paths)


def resolve_prd_skill_path(explicit_path: Path | None = None) -> Path:
    """解析模板安装器管理的 ``prd`` skill 路径。

    解析优先级：显式入参 → ``PRD_SKILL_PATH`` 产品环境变量 →
    :func:`default_prd_skill_candidate_paths`（``SKILLS_DIR`` 覆盖 →
    产品自有 ``<状态目录>/skills`` → 各 agent 用户级 skills 目录）。
    候选中优先返回存在的 ``SKILL.md``；全部缺失时返回第一个候选，
    由调用方保留现有 fallback 行为。

    Args:
        explicit_path: 调用方显式指定的路径；为 ``None`` 时回落到环境变量/默认。

    Returns:
        待读取的 skill 规范文件路径（不保证存在）。
    """
    if explicit_path is not None:
        return explicit_path
    env_value = product_identity.read_product_env_value(_PRD_SKILL_PATH_ENV_SUFFIX)
    if env_value:
        return Path(env_value).expanduser()
    candidate_skill_paths = default_prd_skill_candidate_paths()
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
