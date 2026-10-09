"""用户级 Skills 的安装与刷新——``kc init`` 与 ``kc skill install`` 的共享实现。

两条入口都调用 :func:`sync_user_skills`：远程模板 Skill 与随包 operator Skill 的
内容来源、目标安装根、幂等跳过、旧名副本清理与 fail-closed 保护全部由
:mod:`backend.core.use_cases.agent_runner_init_assets` 的安装实现决定，本模块只按
安装根逐个编排调用并呈现结果。把它抽出来是因为「只想重装/刷新 Skill」在仓库已有
``.kedacode.toml`` 时曾完全无路可走——唯一入口 ``kc init`` 的第一步就会被已存在的
配置挡死（issue-245）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.api.cli_console import console
from backend.core.use_cases.agent_runner_init_assets import (
    PackagedSkillInstallResult,
    RemoteTemplateSkillInstallOptions,
    RemoteTemplateSkillInstallResult,
    install_packaged_operator_skill,
    install_remote_template_skills,
)

if TYPE_CHECKING:
    from backend.core.shared.interfaces.agent_runner import IProcessRunner


def sync_user_skills(*, process_runner: IProcessRunner, dry_run: bool, force: bool) -> None:
    """把远程模板 Skill 与随包 operator Skill 同步到全部用户级安装根。

    安装根由安装实现自己解析（``KEDACODE_SKILLS_DIR`` 优先，其后是产品自有目录与
    各 agent 的用户级 skills 目录），调用方不传路径，因此 ``kc init`` 与
    ``kc skill install`` 永远写到同一批目录、也永远用同一套冲突判定。

    Args:
        process_runner: 执行受限 Git 命令的端口（远程模板下载使用）。
        dry_run: 是否只打印计划、不触碰文件系统。
        force: 是否覆盖用户改动过的同名 Skill，并直接删除旧名 operator 副本。

    Raises:
        RemoteTemplateSkillInstallError: 远程模板拉取失败，或远程模板 Skill 已被
            用户改动且未声明 ``force``（fail-closed，不覆盖），或随包目录缺少
            ``SKILL.md`` / 目标是指向别处的符号链接。随包 operator Skill 的冲突
            不在此列：它默认保留用户内容并打印冲突提示。
    """
    remote_skill_result = install_remote_template_skills(
        RemoteTemplateSkillInstallOptions(
            process_runner=process_runner,
            dry_run=dry_run,
            force=force,
        )
    )
    _print_remote_template_skill_result(remote_skill_result)
    for skills_root in remote_skill_result.target_skills_roots:
        _print_operator_skill_plan(
            install_packaged_operator_skill(
                target_skills_root=skills_root,
                dry_run=dry_run,
                force=force,
            )
        )


def _print_remote_template_skill_result(result: RemoteTemplateSkillInstallResult) -> None:
    """呈现远程模板 Skill 的同步结果；dry-run 下只呈现计划路径。"""
    skill_names = ", ".join(result.installed_skill_names)
    skills_roots = ", ".join(str(root_path) for root_path in result.target_skills_roots)
    if result.dry_run:
        console.print(
            f"[cyan]Would install remote template skills:[/] {skill_names} -> {skills_roots}",
            markup=False,
        )
        return
    console.print(f"[green]Installed remote template skills:[/] {skill_names} -> {skills_roots}")
    overwritten_names = ", ".join(result.overwritten_skill_names)
    skipped_names = ", ".join(result.skipped_skill_names)
    if overwritten_names:
        console.print(
            f"[yellow]Overwrote user-owned skills (matching remote template):[/] {overwritten_names}"
        )
    if skipped_names:
        console.print(f"[dim]Remote template skills already up to date:[/] {skipped_names}")


def _print_operator_skill_plan(result: PackagedSkillInstallResult) -> None:
    """呈现内置 operator Skill 的安装、冲突保护或覆盖结果，并回显旧名副本处理说明。"""
    if result.action == "preserve-conflict":
        prefix = (
            "Would preserve existing user skill"
            if result.dry_run
            else "Preserved existing user skill"
        )
        print(f"{prefix} (conflict; use --force to replace): {result.target_path}")
    elif result.action == "up-to-date":
        print(f"KedaCode operator skill already up to date: {result.target_path}")
    else:
        if result.action == "overwrite":
            prefix = (
                "Would overwrite packaged KedaCode operator skill"
                if result.dry_run
                else "Overwrote packaged KedaCode operator skill"
            )
        else:
            prefix = (
                "Would install packaged KedaCode operator skill"
                if result.dry_run
                else "Installed KedaCode operator skill"
            )
        print(f"{prefix}: {result.target_path}")
    if result.legacy_notice:
        print(result.legacy_notice)


__all__ = ["sync_user_skills"]
