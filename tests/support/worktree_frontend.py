"""Issue #290 前端 worktree 测试共用的文件与诊断辅助函数。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.use_cases import worktree_frontend
from backend.core.use_cases.worktree_dependency_context import (
    DependencyPreparationReport,
    FrontendProjectRecord,
)


def write_test_file(file_path: Path, content: str) -> None:
    """以 UTF-8 写入测试文件，并在需要时创建父目录。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def make_frontend_project(
    project_dir: Path,
    *,
    manifest: str = "{}\n",
    lockfile_names: tuple[str, ...] = (),
) -> None:
    """创建包含 package.json 和已安装依赖标记的最小前端项目。"""
    write_test_file(project_dir / "package.json", manifest)
    write_test_file(project_dir / "node_modules" / ".bin" / "vite", "#!/bin/sh\n")
    for lockfile_name in lockfile_names:
        write_test_file(project_dir / lockfile_name, "lockfile\n")


class _StubShutil:
    """只替换 ``which`` 的 shutil 替身，把工具链可用性变成可控开关。"""

    def __init__(self, resolved_path: str | None) -> None:
        self.resolved_path = resolved_path

    def which(self, executable_name: str) -> str | None:
        del executable_name
        return self.resolved_path


def stub_toolchain(monkeypatch: pytest.MonkeyPatch, *, available: bool) -> None:
    """不读取主机 PATH，只改 worktree 前端模块看到的可执行文件查找结果。"""
    monkeypatch.setattr(
        worktree_frontend,
        "shutil",
        _StubShutil("/usr/local/bin/package-manager" if available else None),
    )


def record_for(
    preparation_report: DependencyPreparationReport,
    project_path: str,
) -> FrontendProjectRecord:
    """按项目相对路径取回它对应的唯一准备诊断记录。"""
    matched = [
        project_record
        for project_record in preparation_report.records
        if project_record.project_path == project_path
    ]
    assert (
        len(matched) == 1
    ), f"expected exactly one record for {project_path!r}, got {preparation_report.records}"
    return matched[0]
