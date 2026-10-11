"""``kc skill`` 子命令 handlers。

与 ``kc init`` 的区别只在于范围：本组命令不读写仓库本地配置，只把随包
operator Skill 与远程模板 Skill 同步到用户级安装根，因此在已经 ``kc init``
过的仓库里也能直接重装（issue-245）。
"""

from __future__ import annotations

from pathlib import Path

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_parsed_context import ParsedCommandContext
from backend.api.cli_skill import repair_operator_skill, sync_user_skills
from backend.core.use_cases.agent_runner_init_assets import RemoteTemplateSkillInstallError


def run_skill_install_command(ctx: ParsedCommandContext) -> int:
    """``kc skill install``：安装或刷新全部用户级 Skills。

    Args:
        ctx: 已解析的命令上下文，``parsed.dry_run`` / ``parsed.force`` 控制写入。

    Returns:
        命令退出码：``0`` 表示已同步（或已给出计划），冲突保护不是失败。

    Raises:
        CliError: 传入了仓库选择器（本命令与目标仓库无关），或远程模板 Skill
            不可用 / 被用户改动且未声明 ``--force``。
    """
    # argparse 门面的仓库 selector 用 ``argparse.SUPPRESS``，未传时属性不存在，
    # 统一走 ``getattr`` 才能同时覆盖 Typer 与门面两条解析路径。
    if (
        ctx.repo_id is not None
        or ctx.repo_override is not None
        or getattr(ctx.parsed, "config", None) is not None
    ):
        raise CliError(
            "kc skill install writes to user-level skill roots; "
            "it takes no repository. Omit --repo/--repo-id/--config.",
            code=ExitCode.USAGE,
            suggestion="kc skill install --dry-run",
        )
    try:
        sync_user_skills(
            process_runner=ctx.process_runner,
            dry_run=ctx.parsed.dry_run,
            force=ctx.parsed.force,
        )
    except RemoteTemplateSkillInstallError as exc:
        # 与 ``kc init`` 对同一失败给出同一退出码：这个异常既覆盖远程模板不可用，
        # 也覆盖 fail-closed 的改名拒绝，命令树无法只认后者，因此不升级为 ``conflict``。
        raise CliError(
            str(exc),
            code=ExitCode.GENERAL,
            suggestion="kc skill install --dry-run",
        ) from exc
    return 0


def run_skill_repair_operator_command(ctx: ParsedCommandContext) -> int:
    """``kc skill repair-operator``：只处理用户指定的一个 operator Skill 目录。"""
    if (
        ctx.repo_id is not None
        or ctx.repo_override is not None
        or getattr(ctx.parsed, "config", None) is not None
    ):
        raise CliError(
            "kc skill repair-operator writes to one user-level skill directory; "
            "it takes no repository. Omit --repo/--repo-id/--config.",
            code=ExitCode.USAGE,
            suggestion="kc skill repair-operator --help",
        )
    skill_dir = Path(ctx.parsed.skill_dir).expanduser()
    if not skill_dir.is_absolute():
        raise CliError(
            "--skill-dir must be an absolute path.",
            code=ExitCode.USAGE,
            suggestion="kc skill repair-operator --help",
        )
    try:
        repair_operator_skill(
            skill_dir=skill_dir,
            dry_run=ctx.parsed.dry_run,
            force=ctx.parsed.force,
        )
    except RemoteTemplateSkillInstallError as exc:
        raise CliError(
            str(exc),
            code=ExitCode.USAGE,
            suggestion="kc skill repair-operator --help",
        ) from exc
    return 0


__all__ = ["run_skill_install_command", "run_skill_repair_operator_command"]
