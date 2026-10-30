"""守卫测试（guard test）：当前代码与文档不得重新引入已退场的 IAR 名称。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT_PATH = Path(__file__).resolve().parents[2]
LEGACY_NAME_PATTERN = re.compile(
    r"(?i)(?:\biar\b|\.iar|iar:|x-iar|iar[-_./]|_iar_|IAR[A-Z]|IAR_[A-Z])"
)


@dataclass(frozen=True)
class LegacyNameAllowance:
    """一条文件与行内容范围明确的旧名兼容白名单。"""

    relative_path: str
    line_pattern: re.Pattern[str]
    reason: str


def _allowance(relative_path: str, line_pattern: str, reason: str) -> LegacyNameAllowance:
    """声明需要保留的旧名行，调用方必须说明兼容原因。"""
    return LegacyNameAllowance(relative_path, re.compile(line_pattern, re.IGNORECASE), reason)


LEGACY_NAME_ALLOWANCES = (
    _allowance(
        "src/backend/core/use_cases/github_marker_migration.py",
        r"iar:|IAR_EXEC",
        "显式 GitHub 标记迁移器只把旧标记改写为新名，不参与任何读取判定。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/state_home_migration.py",
        r"\.iar|\biar\b|IAR",
        "状态目录迁移仍需识别旧路径及旧 runner 进程，避免搬迁时遗漏占用。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/repository_local_migration.py",
        r"\.iar\.toml|legacy-alias",
        "仓库配置迁移需要将旧配置文件识别为迁移来源。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/remote_template_skills.py",
        r"iar-operator",
        "远程 Skill 安装器需识别用户已有的旧名目录以完成一次性迁移。",
    ),
    _allowance(
        "src/backend/core/shared/models/repository_paths.py",
        r"\.iar",
        "集中声明旧仓库状态目录，并供显式工作树目录迁移使用。",
    ),
    _allowance(
        "src/backend/api/routes/agent_runner_idea_inbox.py",
        r"X-IAR-Signature",
        "外部发送方升级期间继续接受旧签名头。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/repository_gitignore.py",
        r"iar|\.iar",
        "识别并原位升级旧 iar 托管块；不再写入旧目录忽略规则。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/container_ops.py",
        r"iar-runner",
        "Compose service 名是持久化资源标识，保留它以便新版仍能管理既有容器。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/templates/runner_container/docker-compose.runner.yml",
        r"iar-runner",
        "Compose service、image 与 container 名保持稳定，避免升级遗留容器。",
    ),
    _allowance(
        "src/backend/core/use_cases/pr_supervisor_findings.py",
        r"\.iar",
        "旧 findings 文件存在时沿用原目录，不自动搬动用户状态。",
    ),
    _allowance(
        "src/backend/core/use_cases/agent_runner_session_store.py",
        r"\.iar",
        "旧会话记录目录存在时继续读取和写入，避免丢失续传状态。",
    ),
    _allowance(
        "src/backend/core/use_cases/agent_runner_validation.py",
        r"\.iar",
        "保留显式配置的旧证据目录与旧 RV 缓存读取路径。",
    ),
    _allowance(
        "src/backend/core/shared/models/agent_runner_config.py",
        r"\.iar/evidence",
        "配置注释说明显式旧证据目录仍保持既有排除行为。",
    ),
    _allowance(
        "src/backend/core/shared/models/agent_runner_execution.py",
        r"\.iar/evidence",
        "执行配置继续兼容用户显式指定的旧证据路径。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/repository_local.py",
        r"\.iar/evidence",
        "旧仓库配置中显式设置的证据目录仍按用户原值处理。",
    ),
    _allowance(
        "src/backend/infrastructure/config/agent_runner_settings_models.py",
        r'path = "\.iar/evidence/x\.png"',
        "浏览器验证配置示例说明用户显式选择旧证据目录时的路径形态。",
    ),
    _allowance(
        "src/backend/api/cli_parsed_commands/config_migrate.py",
        r"\.iar|旧版 ``iar``",
        "迁移命令文档需要描述其显式处理的旧状态、配置與进程来源。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/factories/__init__.py",
        r"\.iar",
        "托管 runner 仍需说明兼容读取已有仓库状态目录。",
    ),
    _allowance(
        "src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md",
        r"\bold `iar` command",
        "随包 Skill 明确说明旧命令已退场。",
    ),
    _allowance(
        "README.md",
        r"旧版 IAR",
        "README 只保留迁移指南入口，不再用旧名描述当前命令或路径。",
    ),
    _allowance(
        "README.md",
        r"\.iar-worktrees",
        "README 说明旧工作树需迁入新容器后才会由清理命令管理。",
    ),
    _allowance(
        "ROADMAP.md",
        r"P1-FEAT-[0-9]+-[0-9]+-iar-console-bundled-web-terminal",
        "已归档 PRD slug 是历史引用，不作为当前命令示例。",
    ),
    _allowance(
        "docs/ai-standards/tooling.md",
        r"P1-FEAT-[0-9]+-[0-9]+-iar-agent-machine-contract",
        "已归档 PRD slug 是规范来源的历史引用。",
    ),
    _allowance(
        "docs/ai-standards/testing.md",
        r"\.iar/evidence",
        "旧自定义证据目录的行为仍需在测试标准中保留说明。",
    ),
    _allowance(
        "docs/guides/agent-runner.md",
        r"\.iar|iar\.console",
        "指南保留旧状态、证据目录与 localStorage 键的迁移兼容说明。",
    ),
    _allowance(
        "docs/guides/migrating-from-iar.md",
        r"\biar\b|\.iar|X-IAR|IAR_EXEC|IAR_|iar:|iar-operator|iar\.agent|iar-evidence",
        "迁移指南逐项列出需要替换的旧写法及仍受支持的兼容输入。",
    ),
    _allowance(
        "docs/guides/prd-standard.md",
        r"\.iar/evidence",
        "PRD 规范保留用户显式配置的旧证据目录作为兼容场景。",
    ),
    _allowance(
        "docs/getting-started/installation.md",
        r"旧命令 `iar` 已不再安装",
        "安装文档仅说明旧命令已退场，并指向新命令。",
    ),
    _allowance(
        "mkdocs.yml",
        r"从 IAR 升级到 KedaCode",
        "导航保留迁移指南的主题名称。",
    ),
    _allowance(
        ".gitignore",
        r"\.iar|iar-worktrees",
        "根忽略规则继续覆盖旧仓库状态与工作树目录。",
    ),
)


def _scan_paths() -> tuple[Path, ...]:
    """返回当前源码、文档及相关配置，不扫描历史 PRD、证据与原型。"""
    source_suffixes = {".py", ".md", ".yml", ".yaml", ".toml"}
    candidate_paths = [
        source_path
        for source_path in (REPOSITORY_ROOT_PATH / "src/backend").rglob("*")
        if source_path.is_file() and source_path.suffix in source_suffixes
    ]
    candidate_paths.extend(
        doc_path
        for doc_path in (REPOSITORY_ROOT_PATH / "docs").rglob("*.md")
        if doc_path.is_file()
        and "prototypes" not in doc_path.relative_to(REPOSITORY_ROOT_PATH).parts
    )
    candidate_paths.extend(
        REPOSITORY_ROOT_PATH / relative_path
        for relative_path in ("README.md", "ROADMAP.md", "mkdocs.yml", ".gitignore")
    )
    return tuple(sorted(set(candidate_paths)))


def test_old_names_are_limited_to_documented_compatibility_lines() -> None:
    """源码与当前文档只在逐行声明的兼容场景中保留旧名。"""
    allowances_by_path: dict[str, list[tuple[int, LegacyNameAllowance]]] = {}
    for allowance_index, allowance in enumerate(LEGACY_NAME_ALLOWANCES):
        allowances_by_path.setdefault(allowance.relative_path, []).append(
            (allowance_index, allowance)
        )

    matched_allowance_indexes: set[int] = set()
    unexpected_lines: list[str] = []
    for source_path in _scan_paths():
        relative_path = source_path.relative_to(REPOSITORY_ROOT_PATH).as_posix()
        for line_number, source_line in enumerate(
            source_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if LEGACY_NAME_PATTERN.search(source_line) is None:
                continue
            matching_allowance = next(
                (
                    (allowance_index, allowance)
                    for allowance_index, allowance in allowances_by_path.get(relative_path, [])
                    if allowance.line_pattern.search(source_line) is not None
                ),
                None,
            )
            if matching_allowance is None:
                unexpected_lines.append(f"{relative_path}:{line_number}: {source_line.strip()}")
                continue
            matched_allowance_indexes.add(matching_allowance[0])

    stale_allowances = [
        allowance.reason
        for allowance_index, allowance in enumerate(LEGACY_NAME_ALLOWANCES)
        if allowance_index not in matched_allowance_indexes
    ]
    assert not unexpected_lines, "旧命名出现在白名单之外：\n" + "\n".join(unexpected_lines)
    assert not stale_allowances, "以下白名单项不再对应任何源码行，应删除：\n" + "\n".join(
        stale_allowances
    )
