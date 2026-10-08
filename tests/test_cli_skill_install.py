"""``kc skill install`` 的 CLI 层测试（issue-245）。

这条入口存在的理由：**仓库已有 ``.kedacode.toml`` 时 ``kc init`` 会在第一步就退出**，
用户级 Skills 因此再无重装/刷新路径，改名前的旧名 ``iar-operator`` 副本也一直留在各
安装根里。这里在真实 CLI 入口（``backend.api.cli.main``）上验证四条验收口径：
装进全部安装根、清理/保留旧名副本、绝不改动本地配置、远程模板冲突保持 fail-closed，
并锁住与 ``kc init`` 的退出码一致性与机器可读输出。

引擎侧的幂等/冲突判定由 ``tests/test_kedacode_operator_skill.py`` 覆盖，本模块不重复；
这里只把 ``install_remote_template_skills`` 打桩（避免访问 GitHub），随包 operator
Skill 走真实安装实现。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from backend.api.cli import main
from backend.api.cli_schema import build_command_schema
from backend.api.cli_typer_app import app
from backend.core.shared.models import product_identity
from backend.engines.agent_runner import remote_template_skills
from backend.engines.agent_runner.remote_template_skills import (
    RemoteTemplateSkillInstallError,
    RemoteTemplateSkillInstallResult,
    install_packaged_operator_skill,
)


def _run_git(repo_path: Path, *git_args: str) -> None:
    subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_git_repository(tmp_path: Path, name: str) -> Path:
    repo_path = tmp_path / name
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "checkout", "-b", "main")
    return repo_path


def _isolate_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把机器级配置指向临时文件，避免测试读写真实 ``config.toml``。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent_runner]\n", encoding="utf-8")
    monkeypatch.setenv("KEDACODE_CONFIG", str(config_path))
    return config_path


def _stub_remote_template_roots(
    monkeypatch: pytest.MonkeyPatch,
    skills_roots: tuple[Path, ...],
) -> None:
    """打桩远程模板同步：返回给定安装根，不访问网络。"""
    monkeypatch.setattr(
        "backend.api.cli_skill.install_remote_template_skills",
        lambda options: RemoteTemplateSkillInstallResult(
            target_skills_roots=skills_roots,
            installed_skill_names=("prd", "code-reviewer"),
            dry_run=options.dry_run,
        ),
    )


def _use_real_packaged_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """随包 operator Skill 走真实安装实现（本模块要验证的就是它的编排）。"""
    monkeypatch.setattr(
        "backend.api.cli_skill.install_packaged_operator_skill",
        install_packaged_operator_skill,
    )


def _write_diverged_local_config(repo_path: Path) -> Path:
    """模拟「这个仓库已经 kc init 过」：本地配置文件已存在。"""
    config_path = repo_path / product_identity.REPOSITORY_CONFIG_FILENAME
    config_path.write_text('[registry]\nid = "already-initialized"\n', encoding="utf-8")
    return config_path


def test_skill_install_writes_packaged_skill_into_every_user_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """验收口径 1：多个用户级安装根全部装上 kedacode-operator，且退出码为 0。"""
    skills_roots = (tmp_path / "roots" / "owned", tmp_path / "roots" / "codex")
    _stub_remote_template_roots(monkeypatch, skills_roots)
    _use_real_packaged_install(monkeypatch)

    assert main(["skill", "install"]) == 0

    output = " ".join(capsys.readouterr().out.split())
    for skills_root in skills_roots:
        installed_contract_path = skills_root / product_identity.OPERATOR_SKILL_NAME / "SKILL.md"
        assert installed_contract_path.is_file(), f"未安装到 {skills_root}"
        # CLI 的安装回显指向 skill 目录本身（不含 SKILL.md）。
        assert str(installed_contract_path.parent) in output


def test_skill_install_keeps_existing_local_config_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收口径 3：在 ``kc init`` 会挡死的仓库里，本命令照常安装且不碰本地配置。"""
    repo_path = _init_git_repository(tmp_path, "target")
    local_config_path = _write_diverged_local_config(repo_path)
    config_bytes_before = local_config_path.read_bytes()
    skills_root = tmp_path / "skills"
    monkeypatch.chdir(repo_path)
    _isolate_global_config(tmp_path, monkeypatch)
    _stub_remote_template_roots(monkeypatch, (skills_root,))
    _use_real_packaged_install(monkeypatch)

    assert main(["skill", "install"]) == 0

    assert local_config_path.read_bytes() == config_bytes_before
    assert (skills_root / product_identity.OPERATOR_SKILL_NAME / "SKILL.md").is_file()


def test_skill_install_dry_run_reports_plan_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` 只打印计划：安装根不被创建，退出码仍为 0。"""
    skills_root = tmp_path / "skills"
    _stub_remote_template_roots(monkeypatch, (skills_root,))
    _use_real_packaged_install(monkeypatch)

    assert main(["skill", "install", "--dry-run"]) == 0

    output = " ".join(capsys.readouterr().out.split())
    assert "Would install packaged KedaCode operator skill" in output
    assert not skills_root.exists()


def test_skill_install_removes_pristine_legacy_copy_and_keeps_modified_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """验收口径 2：未改动的旧名副本被清理，改动过的保留并把路径报给用户。"""
    pristine_root = tmp_path / "skills" / "pristine"
    modified_root = tmp_path / "skills" / "modified"
    pristine_contract_text = "historic packaged operator skill\n"
    modified_contract_text = "user tuned operator skill\n"
    for skills_root, contract_text in (
        (pristine_root, pristine_contract_text),
        (modified_root, modified_contract_text),
    ):
        legacy_skill_path = skills_root / product_identity.LEGACY_OPERATOR_SKILL_NAME
        legacy_skill_path.mkdir(parents=True)
        (legacy_skill_path / "SKILL.md").write_text(contract_text, encoding="utf-8")
    monkeypatch.setattr(
        remote_template_skills,
        "_LEGACY_OPERATOR_SKILL_DIGESTS",
        frozenset({hashlib.sha256(pristine_contract_text.encode("utf-8")).hexdigest()}),
    )
    _stub_remote_template_roots(monkeypatch, (pristine_root, modified_root))
    _use_real_packaged_install(monkeypatch)

    assert main(["skill", "install"]) == 0

    output = " ".join(capsys.readouterr().out.split())
    assert not (pristine_root / product_identity.LEGACY_OPERATOR_SKILL_NAME).exists()
    assert "Removed the legacy operator skill copy at" in output
    preserved_contract_path = (
        modified_root / product_identity.LEGACY_OPERATOR_SKILL_NAME / "SKILL.md"
    )
    assert preserved_contract_path.read_text(encoding="utf-8") == modified_contract_text
    assert "Kept the modified legacy operator skill copy at" in output
    assert str(preserved_contract_path.parent) in output


def test_skill_install_force_replaces_diverged_packaged_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """随包 Skill 被用户改过时：默认保留并报告冲突，``--force`` 才覆盖。"""
    skills_root = tmp_path / "skills"
    user_contract_path = skills_root / product_identity.OPERATOR_SKILL_NAME / "SKILL.md"
    user_contract_path.parent.mkdir(parents=True)
    user_contract_path.write_text("user version\n", encoding="utf-8")
    _stub_remote_template_roots(monkeypatch, (skills_root,))
    _use_real_packaged_install(monkeypatch)

    assert main(["skill", "install"]) == 0
    preserved_output = " ".join(capsys.readouterr().out.split())
    assert "Preserved existing user skill (conflict; use --force to replace)" in preserved_output
    assert user_contract_path.read_text(encoding="utf-8") == "user version\n"

    assert main(["skill", "install", "--force"]) == 0
    forced_output = " ".join(capsys.readouterr().out.split())
    assert "Overwrote packaged KedaCode operator skill" in forced_output
    assert user_contract_path.read_text(encoding="utf-8") != "user version\n"


def test_skill_install_remote_template_failure_exits_one_like_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """远程模板不可用/冲突时两条入口给出同一退出码（不新增错误语义）。"""
    skills_root = tmp_path / "skills"

    def _fail(options: object) -> RemoteTemplateSkillInstallResult:  # noqa: ARG001
        raise RemoteTemplateSkillInstallError("Refusing to overwrite user-owned skill 'prd'")

    monkeypatch.setattr("backend.api.cli_skill.install_remote_template_skills", _fail)
    _isolate_global_config(tmp_path, monkeypatch)
    repo_path = _init_git_repository(tmp_path, "target")
    monkeypatch.chdir(repo_path)

    assert main(["skill", "install"]) == 1
    assert main(["init"]) == 1
    assert not skills_root.exists()


def test_skill_install_rejects_repository_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """本命令写用户级目录、不针对仓库：仓库 selector 一律以用法错误拒绝。"""
    repo_path = _init_git_repository(tmp_path, "target")
    skills_root = tmp_path / "skills"
    monkeypatch.chdir(repo_path)
    _stub_remote_template_roots(monkeypatch, (skills_root,))
    _use_real_packaged_install(monkeypatch)

    for selector_argv in (
        ["skill", "install", "--repo", str(repo_path)],
        ["skill", "install", "--repo-id", "keda"],
        ["skill", "install", "--config", str(tmp_path / "config.toml")],
    ):
        assert main(selector_argv) == 2, selector_argv

    assert "takes no repository" in " ".join(capsys.readouterr().err.split())
    assert not skills_root.exists()


def test_skill_install_is_visible_in_machine_readable_schema() -> None:
    """``kc schema --json`` 自动收录新命令：agent 侧不再只能靠 ``kc init`` 装 Skill。"""
    command_paths = {tuple(command["path"]) for command in build_command_schema(app)["commands"]}

    assert ("skill", "install") in command_paths


def test_init_config_conflict_points_at_skill_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """新入口的可发现性：init 被已存在配置挡死时，把用户导向 ``kc skill install``。"""
    repo_path = _init_git_repository(tmp_path, "target")
    local_config_path = _write_diverged_local_config(repo_path)
    monkeypatch.chdir(repo_path)
    _isolate_global_config(tmp_path, monkeypatch)

    assert main(["init"]) == 1

    # init 的冲突提示走 error_console（stderr），与既有失败输出同一条通道。
    output = " ".join(capsys.readouterr().err.split())
    assert "kc skill install" in output
    assert "kc init --force" in output
    assert (
        local_config_path.read_text(encoding="utf-8") == '[registry]\nid = "already-initialized"\n'
    )
