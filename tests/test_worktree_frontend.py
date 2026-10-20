"""Worktree 前端依赖准备的可恢复行为测试（Issue #290）。

覆盖三组事实：

- 环境缺口**绝不**终止 Issue：可执行文件缺失、进程启动失败、退出码 0 却没产出依赖、
  准备逻辑自身崩溃，全部只写成结构化诊断记录。
- 包管理器识别结合 ``packageManager`` 字段、workspace 根配置与锁文件三类证据，
  互相矛盾时不静默硬选，而是把证据全部交给 Agent。
- 复用主检出 ``node_modules`` 之前先核对 ``package.json`` 与锁文件，证据不足时宁可不复用。

``create_or_reuse_worktree`` 的真实接线见 ``tests/test_worktree_cli.py``。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases import worktree_frontend
from backend.core.use_cases.worktree_dependency_context import (
    OUTCOME_INSTALLED,
    OUTCOME_NOT_PREPARED,
    OUTCOME_PRESENT,
    OUTCOME_REUSED,
    OUTCOME_SKIPPED,
    REASON_DANGLING_NODE_MODULES_SYMLINK,
    REASON_INSTALL_FAILED,
    REASON_INSTALL_NO_OUTPUT,
    REASON_MANAGER_CONFLICT,
    REASON_NO_ACTION_NEEDED,
    REASON_NO_LOCKFILE,
    REASON_PREPARATION_CRASHED,
    REASON_TOOL_MISSING,
    REASON_WORKSPACE_COVERED,
    USER_ACTION_CREDENTIALS,
    USER_ACTION_HOST_TOOLCHAIN,
    USER_ACTION_PRIVILEGE,
    DependencyPreparationReport,
    FrontendProjectRecord,
)
from backend.core.use_cases.worktree_frontend import (
    ensure_frontend_node_modules,
    link_frontend_node_modules,
    plan_package_manager,
)
from tests.conftest import FakeProcessRunner

_PNPM_MEMBER_COMMAND = ("pnpm", "install", "--ignore-scripts", "--ignore-workspace")
_PNPM_ROOT_COMMAND = ("pnpm", "install", "--ignore-scripts")
_NPM_CI_COMMAND = ("npm", "ci", "--ignore-scripts")
_YARN_COMMAND = ("yarn", "install", "--ignore-scripts")
_BUN_COMMAND = ("bun", "install", "--ignore-scripts")

# 准备流程可能发出的全部安装命令，供失败替身逐条登记。
_ALL_INSTALL_COMMANDS: tuple[tuple[str, ...], ...] = (
    _PNPM_MEMBER_COMMAND,
    _PNPM_ROOT_COMMAND,
    _NPM_CI_COMMAND,
    _YARN_COMMAND,
    _BUN_COMMAND,
)

_LOCKFILE_COMMANDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("pnpm-lock.yaml", _PNPM_MEMBER_COMMAND, "pnpm"),
    ("package-lock.json", _NPM_CI_COMMAND, "npm"),
    ("yarn.lock", _YARN_COMMAND, "yarn"),
    ("bun.lock", _BUN_COMMAND, "bun"),
    ("bun.lockb", _BUN_COMMAND, "bun"),
)


def _write_file(file_path: Path, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def _make_frontend_project(
    project_dir: Path,
    *,
    manifest: str = "{}\n",
    lockfile_names: tuple[str, ...] = (),
) -> None:
    """Create a minimal frontend project with installed dependencies."""
    _write_file(project_dir / "package.json", manifest)
    _write_file(project_dir / "node_modules" / ".bin" / "vite", "#!/bin/sh\n")
    for lockfile_name in lockfile_names:
        _write_file(project_dir / lockfile_name, "lockfile\n")


class _StubShutil:
    """只替换 ``which`` 的 shutil 替身，把「工具链是否在 PATH 上」变成可控开关。"""

    def __init__(self, resolved_path: str | None) -> None:
        self.resolved_path = resolved_path

    def which(self, executable_name: str) -> str | None:
        del executable_name
        return self.resolved_path


def _stub_toolchain(monkeypatch: pytest.MonkeyPatch, *, available: bool) -> None:
    """不碰真实主机 PATH，只改本模块看到的可执行文件查找结果。"""
    monkeypatch.setattr(
        worktree_frontend,
        "shutil",
        _StubShutil("/usr/local/bin/package-manager" if available else None),
    )


class _InstallerProcessRunner(FakeProcessRunner):
    """成功时真的在 ``cwd`` 产出 ``node_modules`` 的假安装执行器。

    准备流程以「依赖是否落地」判定成功（Issue #290 要防的正是退出码 0 但依赖缺失的
    假绿灯），所以替身必须复刻这个副作用，而不是只回一个 0。
    """

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        **runner_kwargs: object,
    ) -> CommandResult:
        command_result = super().run(command, cwd=cwd, check=check, **runner_kwargs)
        command_tokens = list(command)
        if command_result.return_code == 0 and command_tokens[1:2] in (["install"], ["ci"]):
            (cwd / "node_modules").mkdir(parents=True, exist_ok=True)
        return command_result


class _ExplodingProcessRunner(FakeProcessRunner):
    """任何命令都直接抛异常的执行器，用于验证准备逻辑把崩溃也收敛成诊断。"""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        **runner_kwargs: object,
    ) -> CommandResult:
        del cwd, check, runner_kwargs
        self.calls.append(list(command))
        raise RuntimeError("installer exploded")


def _installer_runner(
    *,
    return_code: int = 0,
    stderr: str = "",
) -> _InstallerProcessRunner:
    """构造会真实产出 node_modules（或按配置失败）的假安装执行器。"""
    installer_runner = _InstallerProcessRunner()
    if return_code != 0:
        for install_command in _ALL_INSTALL_COMMANDS:
            installer_runner.responses[install_command] = CommandResult(
                command=install_command,
                return_code=return_code,
                stdout="",
                stderr=stderr,
            )
    return installer_runner


def _record_for(
    preparation_report: DependencyPreparationReport,
    project_path: str,
) -> FrontendProjectRecord:
    """按项目相对路径取回它那条诊断记录。"""
    matched = [
        project_record
        for project_record in preparation_report.records
        if project_record.project_path == project_path
    ]
    assert (
        len(matched) == 1
    ), f"expected exactly one record for {project_path!r}, got {preparation_report.records}"
    return matched[0]


class TestPlanPackageManager:
    """包管理器识别的三类证据与冲突行为。"""

    def test_declared_package_manager_matching_lockfile_is_selected(self, tmp_path: Path) -> None:
        """packageManager 字段与锁文件一致时选定该工具。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "frontend" / "package.json",
            json.dumps({"packageManager": "pnpm@9.12.3+sha256.abc"}),
        )
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")

        install_plan = plan_package_manager(worktree_path / "frontend", worktree_path)

        assert install_plan.package_manager == "pnpm"
        assert install_plan.reason == ""
        assert install_plan.evidence[-1].startswith("pnpm install runs with --ignore-workspace")

    def test_declared_package_manager_conflicting_with_lockfile_is_not_resolved(
        self, tmp_path: Path
    ) -> None:
        """packageManager 与锁文件指向不同工具时不静默硬选，证据全留给 Agent。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "frontend" / "package.json",
            json.dumps({"packageManager": "npm@10.2.0"}),
        )
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")

        install_plan = plan_package_manager(worktree_path / "frontend", worktree_path)

        assert install_plan.package_manager is None
        assert install_plan.reason == REASON_MANAGER_CONFLICT
        assert any("conflict" in evidence_line for evidence_line in install_plan.evidence)

    def test_multiple_lockfile_kinds_conflict(self, tmp_path: Path) -> None:
        """同一目录同时存在两种包管理器的锁文件时判为冲突。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        _write_file(worktree_path / "frontend" / "package-lock.json", "{}\n")

        install_plan = plan_package_manager(worktree_path / "frontend", worktree_path)

        assert install_plan.package_manager is None
        assert install_plan.reason == REASON_MANAGER_CONFLICT

    def test_manifest_without_lockfile_is_not_installed_from(self, tmp_path: Path) -> None:
        """没有锁文件时不猜版本来源，只记录证据（安装会写出未固定的依赖）。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "frontend" / "package.json",
            json.dumps({"packageManager": "pnpm@9.12.3"}),
        )

        install_plan = plan_package_manager(worktree_path / "frontend", worktree_path)

        assert install_plan.package_manager is None
        assert install_plan.reason == REASON_NO_LOCKFILE
        assert any("no lockfile" in evidence_line for evidence_line in install_plan.evidence)

    def test_pnpm_workspace_member_is_covered_by_root_install(self, tmp_path: Path) -> None:
        """pnpm-workspace.yaml 声明的成员目录由根安装覆盖，不在成员里重复装。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "frontend-*"\n')
        _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend-admin" / "pnpm-lock.yaml", "lockfile\n")

        install_plan = plan_package_manager(worktree_path / "frontend-admin", worktree_path)

        assert install_plan.is_workspace_member is True
        assert any(
            "covered by root '.'" in evidence_line for evidence_line in install_plan.evidence
        )

    def test_nested_workspace_member_matches_ancestor_pattern(self, tmp_path: Path) -> None:
        """祖先 workspace 用 glob 声明成员时同样识别为被覆盖。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "apps/*"\n')
        _write_file(worktree_path / "apps" / "web" / "package.json", "{}\n")

        install_plan = plan_package_manager(worktree_path / "apps" / "web", worktree_path)

        assert install_plan.is_workspace_member is True
        assert any(
            "covered by root '.' pattern 'apps/*'" in evidence_line
            for evidence_line in install_plan.evidence
        )

    def test_workspace_root_itself_is_not_a_member(self, tmp_path: Path) -> None:
        """workspace 根不在成员集合里，它自己需要执行整仓安装。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "frontend-*"\n')
        _write_file(worktree_path / "package.json", "{}\n")
        _write_file(worktree_path / "pnpm-lock.yaml", "lockfile\n")

        install_plan = plan_package_manager(worktree_path, worktree_path)

        assert install_plan.is_workspace_root is True
        assert install_plan.is_workspace_member is False

    def test_package_json_workspaces_field_marks_membership(self, tmp_path: Path) -> None:
        """npm/yarn 的 package.json workspaces 声明同样算 workspace 根配置证据。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "package.json",
            json.dumps({"workspaces": ["packages/*"]}),
        )
        _write_file(worktree_path / "packages" / "ui" / "package.json", "{}\n")

        install_plan = plan_package_manager(worktree_path / "packages" / "ui", worktree_path)

        assert install_plan.is_workspace_member is True

    def test_pattern_not_matching_project_is_not_a_member(self, tmp_path: Path) -> None:
        """workspace 声明未覆盖该目录时照常按锁文件安装。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "other-*"\n')
        _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend-admin" / "pnpm-lock.yaml", "lockfile\n")

        install_plan = plan_package_manager(worktree_path / "frontend-admin", worktree_path)

        assert install_plan.is_workspace_member is False


class TestEnsureFrontendNodeModules:
    """``ensure_frontend_node_modules`` 的可恢复准备行为。"""

    @pytest.mark.parametrize(
        ("lockfile_name", "expected_command", "expected_manager"),
        _LOCKFILE_COMMANDS,
        ids=("pnpm", "npm", "yarn", "bun-lock", "bun-lockb"),
    )
    def test_lockfile_selects_its_install_command(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        lockfile_name: str,
        expected_command: tuple[str, ...],
        expected_manager: str,
    ) -> None:
        """每种锁文件都用它自己的包管理器安装，并记录成功事实。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / lockfile_name, "lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert process_runner.calls == [list(expected_command)]
        assert project_record.outcome == OUTCOME_INSTALLED
        assert project_record.package_manager == expected_manager
        assert project_record.attempted_commands == (" ".join(expected_command),)
        assert project_record.exit_code == 0
        assert project_record.has_usable_dependencies is True
        assert preparation_report.installed_relative_paths == [Path("frontend")]
        assert not (worktree_path / "frontend" / "node_modules").is_symlink()

    def test_missing_executable_is_diagnosed_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #290 主案：项目级包管理器不在 PATH 上时绝不向上抛异常。"""
        _stub_toolchain(monkeypatch, available=False)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert project_record.reason == REASON_TOOL_MISSING
        assert project_record.package_manager == "pnpm"
        assert project_record.requires_user_action == USER_ACTION_HOST_TOOLCHAIN
        # 命令从未运行：FileNotFoundError 无从产生，Agent 照常启动。
        assert process_runner.calls == []
        assert project_record.attempted_commands == ()
        assert project_record.missing_executables == ("pnpm",)
        assert "not on PATH" in project_record.detail

    def test_missing_executable_still_reuses_main_checkout_dependencies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """装不动但主检出已有依赖时复用之：项目就绪，不再要求人做什么。"""
        _stub_toolchain(monkeypatch, available=False)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend", lockfile_names=("pnpm-lock.yaml",))
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_REUSED
        assert project_record.has_usable_dependencies is True
        assert project_record.requires_user_action is None
        assert (worktree_path / "frontend" / "node_modules").is_symlink()
        assert preparation_report.linked_relative_paths == [Path("frontend")]

    def test_pnpm_install_at_workspace_root_keeps_workspace_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workspace 根的安装不能加 --ignore-workspace，否则成员依赖不会被装上。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "package.json", "{}\n")
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "frontend-*"\n')
        _write_file(worktree_path / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)

        assert process_runner.calls == [list(_PNPM_ROOT_COMMAND)]
        assert _record_for(preparation_report, ".").outcome == OUTCOME_INSTALLED

    def test_install_exit_zero_without_dependencies_is_not_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """退出码 0 但目录里没出现依赖时不判成功（pnpm 假绿灯的防线）。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = FakeProcessRunner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert project_record.reason == REASON_INSTALL_NO_OUTPUT
        assert project_record.exit_code == 0
        assert project_record.attempted_commands == (" ".join(_PNPM_MEMBER_COMMAND),)
        assert "no node_modules appeared" in project_record.detail
        assert not (worktree_path / "frontend" / "node_modules").exists()

    def test_install_failure_keeps_diagnostics_and_falls_back_to_reuse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真实安装失败：失败原因与命令都留在记录里，同时回退复用主检出依赖。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend", lockfile_names=("pnpm-lock.yaml",))
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner(return_code=1, stderr="ERR_PNPM_OUTDATED_LOCKFILE")

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_REUSED
        assert project_record.reason == REASON_INSTALL_FAILED
        assert project_record.exit_code == 1
        assert project_record.attempted_commands == (" ".join(_PNPM_MEMBER_COMMAND),)
        assert "ERR_PNPM_OUTDATED_LOCKFILE" in project_record.detail
        assert project_record.has_usable_dependencies is True

    def test_privilege_failure_is_flagged_as_human_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """需要提权的安装失败被标成人类决定，Agent 不代做提权动作。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package-lock.json", "{}\n")
        process_runner = _installer_runner(
            return_code=1, stderr="npm ERR! code EACCES: permission denied /usr/local/lib"
        )

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert project_record.requires_user_action == USER_ACTION_PRIVILEGE
        assert project_record in preparation_report.escalation_requests

    def test_credential_failure_is_flagged_and_token_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """缺凭据导致的失败标成凭据类人类决定，且不会把 token 原文写进诊断。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner(
            return_code=1,
            stderr="npm ERR! 401 Unauthorized - _authToken=s3cr3t-token-value",
        )

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.requires_user_action == USER_ACTION_CREDENTIALS
        assert "s3cr3t-token-value" not in project_record.detail
        assert "[redacted]" in project_record.detail

    def test_workspace_member_is_skipped_without_installing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """被祖先 workspace 覆盖的成员目录不重复安装，只记录归属证据。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "frontend-*"\n')
        _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend-admin" / "pnpm-lock.yaml", "lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend-admin")

        assert process_runner.calls == []
        assert project_record.outcome == OUTCOME_SKIPPED
        assert project_record.reason == REASON_WORKSPACE_COVERED

    def test_stale_main_checkout_lockfiles_are_not_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """分支已改动锁文件时拒绝复用主检出依赖，避免把陈旧环境伪装成就绪。"""
        _stub_toolchain(monkeypatch, available=False)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(repo_path / "frontend" / "pnpm-lock.yaml", "old-lockfile\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "new-lockfile\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert not (worktree_path / "frontend" / "node_modules").exists()
        assert "lockfiles differ" in project_record.detail

    def test_existing_node_modules_is_left_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """worktree 里已有的真实依赖目录永不覆盖，也不重复安装。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        _write_file(worktree_path / "frontend" / "node_modules" / "marker.txt", "local\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)

        assert process_runner.calls == []
        assert _record_for(preparation_report, "frontend").outcome == OUTCOME_PRESENT
        assert _record_for(preparation_report, "frontend").reason == REASON_NO_ACTION_NEEDED

    def test_dangling_symlink_is_left_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """悬空符号链接也是「已存在」，不能被覆盖（exists() 跟随链接会误判缺失）。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        dangling_target = tmp_path / "missing-deps"
        (worktree_path / "frontend" / "node_modules").symlink_to(dangling_target)
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)

        assert process_runner.calls == []
        project_record = _record_for(preparation_report, "frontend")
        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert project_record.reason == REASON_DANGLING_NODE_MODULES_SYMLINK
        assert (worktree_path / "frontend" / "node_modules").readlink() == dangling_target

    def test_launch_failure_is_recorded_without_aborting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """进程启动阶段的 OSError（如 cwd 消失）只写诊断，不终止准备。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        launch_failure_runner = _InstallerProcessRunner()

        def _raise_os_error(*args: object, **kwargs: object) -> CommandResult:
            del args, kwargs
            raise PermissionError("spawn failed")

        monkeypatch.setattr(launch_failure_runner, "run", _raise_os_error)

        preparation_report = ensure_frontend_node_modules(
            repo_path, worktree_path, launch_failure_runner
        )
        project_record = _record_for(preparation_report, "frontend")

        assert project_record.outcome == OUTCOME_NOT_PREPARED
        assert "PermissionError" in project_record.detail

    def test_preparation_crash_becomes_a_record_instead_of_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任何未预期异常都被收敛成诊断记录，Agent 仍会拿到任务。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")

        preparation_report = ensure_frontend_node_modules(
            repo_path, worktree_path, _ExplodingProcessRunner()
        )

        assert _record_for(preparation_report, "frontend").outcome == OUTCOME_NOT_PREPARED
        assert _record_for(preparation_report, "frontend").reason == REASON_PREPARATION_CRASHED

    def test_every_frontend_project_gets_exactly_one_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """多项目 worktree 逐条成记录，Agent 能看到每个项目的独立状态。"""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend-admin" / "package-lock.json", "{}\n")
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)

        assert [record.project_path for record in preparation_report.records] == [
            "frontend",
            "frontend-admin",
        ]
        assert _record_for(preparation_report, "frontend").outcome == OUTCOME_INSTALLED
        assert _record_for(preparation_report, "frontend-admin").outcome == OUTCOME_INSTALLED

    def test_prunes_vcs_cache_and_nested_node_modules(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Manifests under pruned directories are ignored during install scan."""
        _stub_toolchain(monkeypatch, available=True)
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        _write_file(worktree_path / "node_modules" / "dep" / "package.json", "{}\n")
        _write_file(worktree_path / ".git" / "package.json", "{}\n")
        _write_file(
            worktree_path / ".iar-worktrees" / "issue-9" / "frontend" / "package.json",
            "{}\n",
        )
        process_runner = _installer_runner()

        preparation_report = ensure_frontend_node_modules(repo_path, worktree_path, process_runner)

        assert [record.project_path for record in preparation_report.records] == ["frontend"]

    def test_returns_empty_report_when_worktree_is_repo_root(self, tmp_path: Path) -> None:
        """Operating on the repo root itself is a no-op."""
        repo_path = tmp_path / "repo"
        _make_frontend_project(repo_path / "frontend")

        preparation_report = ensure_frontend_node_modules(repo_path, repo_path, _installer_runner())

        assert preparation_report.records == ()
        assert preparation_report.worktree_path == str(repo_path.resolve())


class TestLinkFrontendNodeModules:
    """Legacy symlink-only fallback behavior."""

    def test_links_node_modules_for_root_and_nested_projects(self, tmp_path: Path) -> None:
        """Every frontend project in the worktree gets a symlink to main's deps."""
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        # Main checkout has installed dependencies for both frontends.
        _make_frontend_project(repo_path / "frontend", lockfile_names=("pnpm-lock.yaml",))
        _make_frontend_project(repo_path / "frontend-admin", lockfile_names=("pnpm-lock.yaml",))
        # Worktree has the manifests (materialized by git) but no node_modules.
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
        _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
        _write_file(worktree_path / "frontend-admin" / "pnpm-lock.yaml", "lockfile\n")

        linked_relative_paths = link_frontend_node_modules(repo_path, worktree_path)

        assert sorted(str(path) for path in linked_relative_paths) == [
            "frontend",
            "frontend-admin",
        ]
        linked_vite_path = worktree_path / "frontend" / "node_modules" / ".bin" / "vite"
        assert (worktree_path / "frontend" / "node_modules").is_symlink()
        # Following the link reaches the real binary in the main checkout.
        assert linked_vite_path.exists()
        assert (
            linked_vite_path.resolve()
            == (repo_path / "frontend" / "node_modules" / ".bin" / "vite").resolve()
        )

    def test_never_overwrites_existing_worktree_node_modules(self, tmp_path: Path) -> None:
        """A real ``node_modules`` already installed in the worktree is untouched."""
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        # A previous `npm install` left a real directory in the worktree.
        _write_file(worktree_path / "frontend" / "node_modules" / "marker.txt", "local\n")

        linked_relative_paths = link_frontend_node_modules(repo_path, worktree_path)

        assert linked_relative_paths == []
        assert not (worktree_path / "frontend" / "node_modules").is_symlink()
        assert (worktree_path / "frontend" / "node_modules" / "marker.txt").read_text(
            encoding="utf-8"
        ) == "local\n"

    def test_does_not_clobber_existing_dangling_symlink(self, tmp_path: Path) -> None:
        """An existing (even broken) ``node_modules`` symlink is left in place."""
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _make_frontend_project(repo_path / "frontend")
        _write_file(worktree_path / "frontend" / "package.json", "{}\n")
        dangling_target = tmp_path / "missing-deps"
        (worktree_path / "frontend" / "node_modules").symlink_to(dangling_target)

        linked_relative_paths = link_frontend_node_modules(repo_path, worktree_path)

        assert linked_relative_paths == []
        assert (worktree_path / "frontend" / "node_modules").readlink() == dangling_target

    def test_returns_empty_when_no_frontend_projects(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        worktree_path = tmp_path / "worktree"
        _write_file(repo_path / "README.md", "no frontend here\n")
        _write_file(worktree_path / "README.md", "no frontend here\n")

        assert link_frontend_node_modules(repo_path, worktree_path) == []

    def test_returns_empty_when_worktree_is_repo_root(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _make_frontend_project(repo_path / "frontend")

        assert link_frontend_node_modules(repo_path, repo_path) == []
