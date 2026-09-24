"""IAR 自带 operator Skill 的发行资源与用户目录冲突保护。"""

from __future__ import annotations

from pathlib import Path

from backend.engines.agent_runner.remote_template_skills import install_packaged_operator_skill


def test_packaged_operator_skill_dry_run_reports_install_and_writes_nothing(tmp_path: Path) -> None:
    """干净 home 的 init dry-run 应发现随包 Skill 而不落盘。"""
    skills_root = tmp_path / ".codex" / "skills"

    result = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=True,
        force=False,
    )

    assert result.action == "install"
    assert result.target_path == skills_root / "iar-operator"
    assert not skills_root.exists()


def test_packaged_operator_skill_preserves_user_conflict_by_default(tmp_path: Path) -> None:
    """已有同名用户 Skill 默认保留，dry-run 与实际安装都报告冲突。"""
    skills_root = tmp_path / "skills"
    user_skill = skills_root / "iar-operator" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("user content\n", encoding="utf-8")

    preview = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=True,
        force=False,
    )
    result = install_packaged_operator_skill(
        target_skills_root=skills_root,
        dry_run=False,
        force=False,
    )

    assert preview.action == result.action == "preserve-conflict"
    assert user_skill.read_text(encoding="utf-8") == "user content\n"
