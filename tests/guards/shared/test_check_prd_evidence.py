"""守护证据图片「就地嵌图」约定的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/check_prd_evidence.sh``。核心不变量：

1. **人审图必须在证据报告里就地嵌入，`open` 命令不算呈递。** PRD 流程的人审
   导航要求 ``![说明](图片名.png)``——报告与图片同目录，本地 Markdown 预览能
   直接渲染。历史失败模式：执行者只写了一行 ``open "<路径>"`` 加一句「图片不入
   Git，GitHub 上看不到」，人审时要自己去终端粘命令才看得见图，而所有门禁全绿。
2. **图片不入 Git 不是不嵌图的理由。** ``.gitignore`` 白名单只提交 ``*.md``，
   所以嵌图在 GitHub 上是坏图——这正是那句本地标注要解释的事；标注与 open
   命令是嵌图的补充，不是替代品。
3. **不得误伤。** 没有证据报告、没有图片，以及录屏（Markdown 无法内联渲染）都
   不能因为「没嵌图」被判失败，否则门禁会在正常场景下变成噪音。
4. **失败必须点名具体文件。** 只说「有图没嵌」会让执行者无从下手，错误信息要
   列出漏嵌的文件名和正确写法。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_CHECK_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just" / "check_prd_evidence.sh"
)

_FIXTURE_PRD_NAME = "P1-FEAT-20260101-000000-avatar-upload"
_BACKEND_ONLY_PRD_BODY = "# fixture PRD\n\nNo frontend impact — 纯后端改动。\n"


def _build_evidence_layout(search_root: Path, *, evidence_file_names: tuple[str, ...]) -> Path:
    """按 PRD 流程的真实布局建出 PRD 文件与证据目录，返回证据目录路径。"""
    prd_file_path = search_root / "tasks" / "pending" / f"{_FIXTURE_PRD_NAME}.md"
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(_BACKEND_ONLY_PRD_BODY, encoding="utf-8")

    evidence_dir_path = search_root / "tasks" / "evidence" / _FIXTURE_PRD_NAME
    evidence_dir_path.mkdir(parents=True, exist_ok=True)
    for evidence_file_name in evidence_file_names:
        (evidence_dir_path / evidence_file_name).write_bytes(b"fake-binary-evidence")
    return evidence_dir_path


def _write_evidence_report(evidence_dir_path: Path, report_body: str) -> None:
    """写入证据报告，文件名遵循 ``<prd-stem>.evidence-report.md`` 约定。"""
    report_path = evidence_dir_path / f"{_FIXTURE_PRD_NAME}.evidence-report.md"
    report_path.write_text(report_body, encoding="utf-8")


def _run_evidence_check(search_root: Path) -> subprocess.CompletedProcess[str]:
    """按 ``just ai implement`` 的真实调用方式跑脚本（显式传 worktree root）。

    ``/bin/bash`` 固定 macOS 自带的 3.2，保证脚本的兼容性声明真的被跑到。
    """
    return subprocess.run(
        [
            "/bin/bash",
            str(_CHECK_SCRIPT_PATH),
            str(search_root / "tasks" / "pending" / f"{_FIXTURE_PRD_NAME}.md"),
            str(search_root),
        ],
        cwd=search_root,
        capture_output=True,
        text=True,
    )


def test_open_command_without_inline_embed_is_rejected(tmp_path: Path) -> None:
    """只给 ``open`` 命令、不嵌图，必须失败并点名漏嵌的图片。"""
    evidence_dir_path = _build_evidence_layout(
        tmp_path, evidence_file_names=("rv-4-mcp-management.png",)
    )
    _write_evidence_report(
        evidence_dir_path,
        "# 证据报告\n\n## 人审导航\n\n"
        '> PNG 是本地文件、不入 Git，GitHub 上不显示。\n\n```bash\nopen "'
        f'{evidence_dir_path}/rv-4-mcp-management.png"\n```\n',
    )

    check_result = _run_evidence_check(tmp_path)

    assert check_result.returncode != 0, check_result.stdout + check_result.stderr
    assert "rv-4-mcp-management.png" in check_result.stdout


def test_inline_embedded_images_pass(tmp_path: Path) -> None:
    """就地嵌图后通过；相对路径前缀与 Markdown title 都视为同一张图。"""
    evidence_dir_path = _build_evidence_layout(
        tmp_path, evidence_file_names=("rv-4-mcp-management.png", "rv-5-skill-management.jpg")
    )
    _write_evidence_report(
        evidence_dir_path,
        "# 证据报告\n\n## 人审导航\n\n"
        "![MCP 管理页](rv-4-mcp-management.png)\n\n"
        '![Skill 管理页](./rv-5-skill-management.jpg "真实页面")\n',
    )

    check_result = _run_evidence_check(tmp_path)

    assert check_result.returncode == 0, check_result.stdout + check_result.stderr


def test_partially_embedded_images_name_only_the_missing_one(tmp_path: Path) -> None:
    """漏嵌一张也要失败，且只点名漏掉的那张，不把已嵌的混进来。"""
    evidence_dir_path = _build_evidence_layout(
        tmp_path, evidence_file_names=("rv-1-embedded.png", "rv-2-forgotten.png")
    )
    _write_evidence_report(
        evidence_dir_path,
        "# 证据报告\n\n![已嵌入](rv-1-embedded.png)\n",
    )

    check_result = _run_evidence_check(tmp_path)

    assert check_result.returncode != 0, check_result.stdout + check_result.stderr
    assert "rv-2-forgotten.png" in check_result.stdout
    assert "rv-1-embedded.png" not in check_result.stdout


def test_recordings_are_not_required_to_be_embedded(tmp_path: Path) -> None:
    """录屏无法在 Markdown 里内联渲染，不得因为「没嵌图」被判失败。"""
    evidence_dir_path = _build_evidence_layout(
        tmp_path, evidence_file_names=("rv-3-checkout-flow.webm",)
    )
    _write_evidence_report(evidence_dir_path, "# 证据报告\n\n录屏见证据目录。\n")

    check_result = _run_evidence_check(tmp_path)

    assert check_result.returncode == 0, check_result.stdout + check_result.stderr


def test_missing_report_or_images_does_not_fail(tmp_path: Path) -> None:
    """没有证据报告、以及没有任何图片时，本检查都不得误伤。"""
    _build_evidence_layout(tmp_path, evidence_file_names=("rv-1-only-image.png",))
    missing_report_result = _run_evidence_check(tmp_path)

    assert missing_report_result.returncode == 0, (
        missing_report_result.stdout + missing_report_result.stderr
    )

    empty_evidence_dir_path = _build_evidence_layout(tmp_path, evidence_file_names=())
    (empty_evidence_dir_path / "rv-1-only-image.png").unlink()
    _write_evidence_report(empty_evidence_dir_path, "# 证据报告\n\n无视觉产物。\n")
    no_image_result = _run_evidence_check(tmp_path)

    assert no_image_result.returncode == 0, no_image_result.stdout + no_image_result.stderr
