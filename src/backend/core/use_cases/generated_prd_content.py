"""从 GitHub Issue 生成 / 重写 PRD 的内容生成。

从 :mod:`backend.core.use_cases.generated_content` 拆出（原文件已逼近行数上限）：
Issue / PR 内容生成留在原模块，PRD 生成与 ``prd`` skill 规范来源集中在这里。

- :func:`build_prd_context` / :func:`generate_prd_content`：为 rework-prd 流程生成
  PRD markdown，遵循与 Issue / PR 相同的 agent → template → hard fallback 级联。
- :func:`resolve_prd_skill_path` / :func:`load_prd_skill_spec` /
  :func:`ensure_prd_machine_contract_available`：``prd`` skill 规范的单一来源与启动预检。

上下文对象（``PrdContext`` / ``GeneratedPrdContent``）与共用的渲染 / agent 调用
helper 仍在 ``generated_content`` 中，本模块单向依赖它。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IContentGenerator
from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.agent_runner import (
    GeneratedContentConfig,
    IssueSummary,
)
from backend.core.shared.prd_machine_contract import (
    PrdSkillPreflightError,
    format_supported_machine_contract_versions,
    is_supported_machine_contract_version,
    parse_machine_contract_version,
)
from backend.core.shared.prd_skill_location import (
    resolve_prd_contract_script,
    resolve_prd_skill_path,
)
from backend.core.use_cases.generated_content import (
    GeneratedPrdContent,
    PrdContext,
    _render_template,
    _resolve_generation_agent,
    _run_content_generator,
    _truncate_text,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PRD 上下文构建
# ---------------------------------------------------------------------------


_IGNORED_REPO_ENTRIES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".iar",
        ".agent-runner",
        "dist",
        "build",
        ".DS_Store",
    }
)


def _is_ignored_repo_entry(path: Path) -> bool:
    """判断目录项是否应被排除在仓库结构摘要之外。"""
    name = path.name
    if name in _IGNORED_REPO_ENTRIES:
        return True
    if name.startswith(".") and name not in {".github", ".claude"}:
        return True
    return False


def _build_repo_structure_summary(
    repo_path: Path,
    *,
    max_depth: int = 3,
    max_entries_per_dir: int = 30,
) -> str:
    """为 PRD prompt 构建仓库结构摘要。

    遍历仓库根目录下有限深度的目录树，输出目录和文件列表。
    用于给 agent 提供项目布局上下文，避免暴露大量无关细节。

    Args:
        repo_path: 仓库根目录。
        max_depth: 最大遍历深度。
        max_entries_per_dir: 每个目录最多列出的条目数。

    Returns:
        格式化的仓库结构摘要文本。
    """
    if not repo_path.exists():
        return ""

    summary_lines: list[str] = []

    def _walk(current_path: Path, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        try:
            entries = [
                entry for entry in current_path.iterdir() if not _is_ignored_repo_entry(entry)
            ]
        except OSError:
            return
        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
        visible_entries = entries[:max_entries_per_dir]
        for entry in visible_entries:
            suffix = "/" if entry.is_dir() else ""
            summary_lines.append(f"{prefix}{entry.name}{suffix}")
            if entry.is_dir():
                _walk(entry, depth + 1, f"{prefix}  ")
        if len(entries) > max_entries_per_dir:
            summary_lines.append(f"{prefix}... ({len(entries) - max_entries_per_dir} more)")

    _walk(repo_path, 1, "")
    return "\n".join(summary_lines)


def build_prd_context(
    *,
    issue: IssueSummary,
    comments: list[str],
    existing_prd_text: str,
    repo_path: Path,
) -> PrdContext:
    """为 PRD 内容生成构建上下文变量。

    Args:
        issue: 关联的 GitHub Issue。
        comments: Issue 评论列表。
        existing_prd_text: 现有 PRD 文本（如有）。
        repo_path: 仓库根目录路径。

    Returns:
        供模板渲染或 agent prompt 使用的 ``PrdContext`` 实例。
    """
    return PrdContext(
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_comments="\n\n".join(f"Comment:\n{c}" for c in comments),
        existing_prd_text=existing_prd_text,
        repo_structure_summary=_build_repo_structure_summary(repo_path),
    )


def _validate_prd_output(text: str) -> bool:
    """验证生成的 PRD 文本是否符合基本结构要求。

    检查项：

    1. 文本必须以 ``# PRD:`` 开头。
    2. 必须包含至少一个 ``## `` 二级标题。
    3. 必须包含 ``- GitHub Issue:`` 锚点行。

    Args:
        text: 待验证的 PRD 文本。

    Returns:
        符合基本要求时返回 ``True``，否则 ``False``。
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if not stripped.startswith("# PRD:"):
        return False
    if "## " not in stripped:
        return False
    if "- GitHub Issue:" not in stripped:
        return False
    return True


# ---------------------------------------------------------------------------
# prd skill 规范来源（单一来源；禁止硬编码安装路径）
# ---------------------------------------------------------------------------
# 路径解析已搬到 ``backend.core.shared.prd_skill_location``：消费它的是 core/shared
# 下的模块（如 PRD 格式解析客户端），留在 use_cases 会造成反向依赖。


def load_prd_skill_spec(explicit_path: Path | None = None) -> str | None:
    """读取 ``prd`` skill 规范文本，不可达时安全返回 ``None``。

    Args:
        explicit_path: 显式 skill 路径；为 ``None`` 时按 :func:`resolve_prd_skill_path`
            的优先级解析。

    Returns:
        skill 规范文本（已 strip）；文件缺失/不可读/为空时返回 ``None``，
        由调用方回退到现有模板 prompt。
    """
    skill_path = resolve_prd_skill_path(explicit_path)
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError:
        _logger.warning(
            "prd skill spec unreachable at %s; falling back to template prompt.",
            skill_path,
        )
        return None
    skill_text = skill_text.strip()
    return skill_text or None


def ensure_prd_machine_contract_available(explicit_path: Path | None = None) -> Path:
    """启动预检：prd skill 必须可解析且 Machine Contract 主版本匹配。

    daemon 起执行循环前调用（``run_preflight_checks``）。kc 的 prompt 只持有
    指向 skill Machine Contract 的指针，skill 缺失或版本不符时执行 agent 将
    拿不到格式约定，因此必须 fail fast 而不是跑到交付门禁才失败。

    Args:
        explicit_path: 显式 skill 路径；为 ``None`` 时按
            :func:`resolve_prd_skill_path` 的优先级解析（含
            ``KEDACODE_PRD_SKILL_PATH`` 环境变量覆盖）。

    Returns:
        通过预检的 skill 路径。

    Raises:
        PrdSkillPreflightError: skill 不可读，或契约主版本不在
            ``SUPPORTED_MACHINE_CONTRACT_VERSIONS`` 内；报错含修复指引。
    """
    skill_path = resolve_prd_skill_path(explicit_path)
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError as read_error:
        raise PrdSkillPreflightError(
            f"prd skill is not readable at {skill_path}. The agent runner delegates "
            "PRD format conventions to the prd skill's Machine Contract, so it "
            "cannot run without the skill installed. Run `kc init` to install the "
            "remote template skills, or point KEDACODE_PRD_SKILL_PATH at a prd SKILL.md."
        ) from read_error
    contract_version = parse_machine_contract_version(skill_text)
    if not is_supported_machine_contract_version(contract_version):
        declared_version_text = (
            f"v{contract_version}"
            if contract_version is not None
            else "no Machine-Contract-Version marker"
        )
        raise PrdSkillPreflightError(
            f"prd skill at {skill_path} declares {declared_version_text}, but this "
            f"runner supports only Machine Contract "
            f"{format_supported_machine_contract_versions()}; unknown versions are "
            "unsupported. Use `kc init` only after its dry-run "
            "shows the intended Skill plan, or select a supported prd skill "
            "without overwriting user-owned files, or point KEDACODE_PRD_SKILL_PATH at its SKILL.md."
        )
    return _ensure_prd_contract_script_present(skill_path)


def _ensure_prd_contract_script_present(skill_path: Path) -> Path:
    """预检第二关：skill 必须带 PRD 解析脚本（keda 已不自带解析实现）。

    ``SKILL.md`` 与 ``scripts/prd_contract.py`` 是**一起安装**的一对：契约文本说
    的格式、与真正被执行的解析实现必须同源。只检查 ``SKILL.md`` 会漏掉"装了半套"
    ——例如手工只更新了 SKILL.md，解析却仍走不到脚本，于是 daemon 起得来、直到交付
    门禁才失败。这里在启动时就把这条接线检查掉。

    Args:
        skill_path: 已通过版本预检的 ``SKILL.md`` 路径。

    Returns:
        通过预检的 skill 路径。

    Raises:
        PrdSkillPreflightError: 兄弟 ``scripts/`` 目录下缺 ``prd_contract.py``。
    """
    contract_script_path = resolve_prd_contract_script(skill_path)
    if not contract_script_path.is_file():
        raise PrdSkillPreflightError(
            f"prd skill at {skill_path} has no parser script at {contract_script_path}. "
            "The runner no longer implements PRD format parsing itself; it calls the "
            "skill's scripts/prd_contract.py, so SKILL.md and scripts/ must be installed "
            "together. Re-run `kc init` to reinstall the skill, or point "
            "KEDACODE_PRD_SKILL_PATH at a complete prd SKILL.md whose sibling scripts/ "
            "directory is present."
        )
    return skill_path


def _build_prd_agent_prompt(skill_spec: str, context: PrdContext, max_context_chars: int) -> str:
    """用 ``prd`` skill 规范 + PRD 上下文组合 agent prompt。

    skill 规范是方法论与输出契约的单一来源，始终完整注入（不截断）；
    ``max_context_chars`` 只约束可变的输入上下文（Issue 正文/评论/现有 PRD/仓库
    结构），避免超长 Issue 线程撑爆 prompt 而又不丢失规范本身。

    Args:
        skill_spec: ``prd`` skill ``SKILL.md`` 全文。
        context: PRD 上下文变量。
        max_context_chars: 可变上下文部分的最大字符数。

    Returns:
        发送给内容生成器的完整 prompt。
    """
    context_block = "\n".join(
        [
            f"GitHub Issue #{context.issue_number}: {context.issue_title}",
            "",
            "Issue Body:",
            context.issue_body,
            "",
            "Issue Comments (chronological):",
            context.issue_comments,
            "",
            "Existing PRD (rewrite if present, otherwise empty):",
            context.existing_prd_text,
            "",
            "Repository Structure Summary:",
            context.repo_structure_summary,
        ]
    )
    context_block = _truncate_text(context_block, max_context_chars)
    return "\n".join(
        [
            skill_spec,
            "",
            "---",
            "",
            "Follow the PRD methodology and output contract above. Apply it to the "
            "GitHub Issue and repository context below.",
            "",
            context_block,
            "",
            "Output rules:",
            "- Write the PRD in the same language as the Issue title.",
            "- The PRD MUST start with `# PRD: <title>` and include a `- GitHub Issue:` line.",
            "- Output only the PRD markdown, with no extra commentary.",
        ]
    )


def generate_prd_content(
    *,
    config: GeneratedContentConfig,
    context: PrdContext,
    fallback_prd_text: str,
    generator: IContentGenerator | None = None,
    cwd: Path | None = None,
    prd_skill_path: Path | None = None,
    model_selection: ModelSelection | None = None,
) -> GeneratedPrdContent:
    """生成 PRD markdown，支持多级回退。

    执行流程：

    1. 如果 ``generated_content`` 被禁用，直接返回 fallback。
    2. 根据 ``target.mode`` 选择生成策略：
       - ``"agent"``（默认）：调用 AI agent 生成内容。
       - ``"template"``（已废弃）：跳过 agent，直接用 ``_render_template`` 渲染正文模板。
    3. 验证输出是否满足 ``_validate_prd_output``。
    4. 验证通过则返回生成结果，source 标记为 ``target.mode``。
    5. 如果 agent 模式失败且 ``config.fallback == "template"``，尝试 template 兜底。
    6. 最终仍失败则返回 ``fallback_prd_text``，source 标记为 ``"fallback"``。

    Args:
        config: 生成内容配置。
        context: PRD 上下文。
        fallback_prd_text: 当所有生成方式失败时使用的 PRD 文本。
        generator: agent 模式所需的内容生成器。
        cwd: agent 工作目录。
        prd_skill_path: 可选的 ``prd`` skill ``SKILL.md`` 显式路径；为 ``None`` 时按
            :func:`resolve_prd_skill_path` 解析。agent 模式优先用 skill 规范构建
            prompt（单一来源），skill 不可达时回退到配置的 ``target.prompt`` 模板。

    Returns:
        包含 text 和 source 的 ``GeneratedPrdContent`` 实例。
    """
    target = config.prd_from_issue
    if not config.enabled or not target.enabled:
        return GeneratedPrdContent(text=fallback_prd_text, source="fallback")

    generated_text = ""

    if target.mode == "template" and target.body_template:
        try:
            generated_text = _render_template(target.body_template, context)
        except (KeyError, ValueError):
            pass
    elif target.mode == "agent" and generator is not None and cwd is not None:
        # 阶段绑定预设时预设整体决定该阶段的 agent（遮蔽矩阵同键声明）。
        agent_name = (
            model_selection.agent
            if model_selection is not None
            else _resolve_generation_agent(
                target.agent, config.default_agent, override_agent=config.lifecycle_default_agent
            )
        )
        # PRD 规范单一来源：优先注入 prd skill 规范；不可达时回退到配置模板 prompt。
        skill_spec = load_prd_skill_spec(prd_skill_path)
        if skill_spec:
            prompt = _build_prd_agent_prompt(skill_spec, context, config.max_input_chars)
        else:
            prompt = _truncate_text(
                _render_template(target.prompt, context), config.max_input_chars
            )
        generated_text = _run_content_generator(
            generator,
            agent_name,
            prompt,
            cwd,
            target.timeout_seconds,
            model_selection=model_selection,
        )

    if generated_text and _validate_prd_output(generated_text):
        return GeneratedPrdContent(text=generated_text, source=target.mode)

    # Agent 失败：按配置尝试 template 中间兜底。
    if target.mode == "agent" and config.fallback == "template" and target.body_template:
        try:
            generated_text = _render_template(target.body_template, context)
        except (KeyError, ValueError):
            pass
        if generated_text and _validate_prd_output(generated_text):
            return GeneratedPrdContent(text=generated_text, source="template")

    return GeneratedPrdContent(text=fallback_prd_text, source="fallback")
