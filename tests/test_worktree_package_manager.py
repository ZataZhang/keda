"""包管理器识别与 workspace 冲突行为测试（Issue #290）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.use_cases.worktree_dependency_context import (
    REASON_MANAGER_CONFLICT,
    REASON_NO_LOCKFILE,
    REASON_WORKSPACE_COVERED,
)
from backend.core.use_cases.worktree_frontend import plan_package_manager
from tests.support.worktree_frontend import write_test_file as _write_file


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

    def test_workspace_root_member_manager_conflict_is_exposed(self, tmp_path: Path) -> None:
        """成员与 workspace 根的 packageManager 冲突不能被 workspace skip 吞掉。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "package.json",
            json.dumps({"packageManager": "pnpm@9.12.3"}),
        )
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "apps/*"\n')
        _write_file(worktree_path / "pnpm-lock.yaml", "lockfile\n")
        _write_file(
            worktree_path / "apps" / "web" / "package.json",
            json.dumps({"packageManager": "npm@10.2.0"}),
        )

        install_plan = plan_package_manager(worktree_path / "apps" / "web", worktree_path)

        assert install_plan.is_workspace_member is True
        assert install_plan.package_manager is None
        assert install_plan.reason == REASON_MANAGER_CONFLICT
        assert any(
            "workspace root '.' package.json packageManager declares 'pnpm'" in line
            for line in install_plan.evidence
        )
        assert any("packageManager declares 'npm'" in line for line in install_plan.evidence)

    def test_pnpm_workspace_file_conflicts_with_npm_root_and_member(self, tmp_path: Path) -> None:
        """pnpm workspace 文件不能与 npm 根声明混用后再跳过成员安装。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "package.json",
            json.dumps({"packageManager": "npm@10.2.0"}),
        )
        _write_file(worktree_path / "package-lock.json", '{"lockfileVersion":3}\n')
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "packages/*"\n')
        member_path = worktree_path / "packages" / "app"
        _write_file(member_path / "package.json", '{"name":"app"}\n')

        root_plan = plan_package_manager(worktree_path, worktree_path)
        member_plan = plan_package_manager(member_path, worktree_path)

        assert root_plan.reason == REASON_MANAGER_CONFLICT
        assert member_plan.reason == REASON_MANAGER_CONFLICT
        assert root_plan.package_manager is None
        assert member_plan.package_manager is None
        assert any("pnpm-workspace.yaml declares pnpm" in line for line in root_plan.evidence)

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

    def test_single_star_workspace_pattern_does_not_cover_nested_package(
        self, tmp_path: Path
    ) -> None:
        """单层 ``*`` 不应把 workspace 下一层之外的独立包跳过。"""
        worktree_path = tmp_path / "worktree"
        _write_file(
            worktree_path / "package.json",
            json.dumps({"workspaces": ["packages/*"]}),
        )
        _write_file(
            worktree_path / "packages" / "client" / "nested" / "package.json",
            '{"name":"nested"}\n',
        )
        _write_file(
            worktree_path / "packages" / "client" / "nested" / "pnpm-lock.yaml",
            "lockfileVersion: '9.0'\n",
        )

        install_plan = plan_package_manager(
            worktree_path / "packages" / "client" / "nested", worktree_path
        )

        assert install_plan.is_workspace_member is False
        assert install_plan.package_manager == "pnpm"
        assert any(
            "pnpm install runs with --ignore-workspace" in evidence_line
            for evidence_line in install_plan.evidence
        )

    def test_double_star_workspace_pattern_covers_nested_package(self, tmp_path: Path) -> None:
        """完整路径段 ``**`` 可显式覆盖多层 workspace 包。"""
        worktree_path = tmp_path / "worktree"
        _write_file(worktree_path / "pnpm-workspace.yaml", 'packages:\n  - "packages/**"\n')
        _write_file(
            worktree_path / "packages" / "client" / "nested" / "package.json",
            '{"name":"nested"}\n',
        )

        install_plan = plan_package_manager(
            worktree_path / "packages" / "client" / "nested", worktree_path
        )

        assert install_plan.is_workspace_member is True

    @pytest.mark.parametrize(
        "workspace_pattern_order",
        [
            ("packages/**", "!packages/client/nested"),
            ("!packages/client/nested", "packages/**"),
            (
                "packages/**",
                "!packages/client/nested",
                "packages/client/nested",
            ),
        ],
        ids=["include-before-exclude", "exclude-before-include", "later-include"],
    )
    def test_pnpm_workspace_exclusion_overrides_included_patterns(
        self,
        tmp_path: Path,
        workspace_pattern_order: tuple[str, ...],
    ) -> None:
        """pnpm 的 ``!`` pattern 排除成员，即使其他 pattern 也能匹配。"""
        worktree_path = tmp_path / "worktree"
        workspace_pattern_lines = "\n".join(
            f'  - "{workspace_pattern}"' for workspace_pattern in workspace_pattern_order
        )
        _write_file(
            worktree_path / "pnpm-workspace.yaml",
            f"packages:\n{workspace_pattern_lines}\n",
        )
        excluded_project_path = worktree_path / "packages" / "client" / "nested"
        _write_file(excluded_project_path / "package.json", '{"name":"nested"}\n')
        _write_file(excluded_project_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")

        install_plan = plan_package_manager(excluded_project_path, worktree_path)

        assert install_plan.is_workspace_member is False
        assert install_plan.package_manager == "pnpm"
        assert install_plan.reason != REASON_WORKSPACE_COVERED

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
