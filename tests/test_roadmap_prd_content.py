"""Tests for the read-only PRD content endpoint.

覆盖合法原文往返，以及目录穿越、绝对路径、非 ``.md`` 后缀、符号链接逃逸四类
负向请求；负向用例在归属校验被删除时必须变红（见 PRD rv-1 的 negative_control）。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_roadmap as roadmap_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext

client = TestClient(app)

_ENDPOINT_TEMPLATE = "/api/v1/agent-runner/roadmap/prds/{encoded_path}/content?repo_id=keda-main"

_PENDING_PRD_TEXT = (
    "# PRD: 控制台内直接阅读 PRD 原文\n"
    "\n"
    "## 1. Introduction\n"
    "\n"
    "| 输入 | 期望 |\n"
    "|---|---|\n"
    "| 点开 pending PRD | 看到完整 Markdown 原文 |\n"
    "\n"
    "## Acceptance Checklist\n"
    "- [x] 已完成项\n"
    "- [ ] 未完成项\n"
)
_ARCHIVED_PRD_TEXT = "# PRD: Archived Feature\n\n## Acceptance Checklist\n- [x] done\n"
_OUTSIDE_SECRET_TEXT = "SECRET-OUTSIDE-WHITELIST\n"


def _encode_prd_path(prd_path: str) -> str:
    """按既有 base64url 约定编码 PRD 相对路径。"""
    return base64.urlsafe_b64encode(prd_path.encode("utf-8")).decode("ascii")


def _write_prd(repo_dir: Path, relative_path: str, text: str) -> Path:
    """在仓库内写入一个 PRD 文件并返回其绝对路径。"""
    prd_file_path = repo_dir / relative_path
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(text, encoding="utf-8")
    return prd_file_path


@pytest.fixture
def prd_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """准备一个带真实 ``tasks/pending`` 与 ``tasks/archive`` 的仓库根目录。"""
    repo_dir = tmp_path / "repo"
    _write_prd(repo_dir, "tasks/pending/P1-FEAT-20260101-pending.md", _PENDING_PRD_TEXT)
    _write_prd(repo_dir, "tasks/archive/P1-FEAT-20260101-archived.md", _ARCHIVED_PRD_TEXT)
    _write_prd(repo_dir, "tasks/pending/P1-FEAT-中文 名称.md", "# PRD: 中文文件名\n")
    # 白名单之外的诱饵文件，用于验证越界请求拿不到内容。
    _write_prd(repo_dir, "docs/outside.md", _OUTSIDE_SECRET_TEXT)
    (repo_dir / "tasks" / "pending" / "not-a-prd.py").write_text("print('x')\n", encoding="utf-8")

    contexts = [
        RepositoryRunContext(
            repo_id="keda-main",
            display_name="Keda Main",
            repo_path=repo_dir,
            config=AppConfig(),
        )
    ]
    monkeypatch.setattr(roadmap_routes, "_resolve_contexts", lambda: contexts)
    return repo_dir


def _get_content(prd_path: str) -> object:
    """对给定 PRD 相对路径发起真实 HTTP 请求。"""
    return client.get(_ENDPOINT_TEMPLATE.format(encoded_path=_encode_prd_path(prd_path)))


@pytest.mark.parametrize(
    ("relative_path", "expected_text"),
    [
        ("tasks/pending/P1-FEAT-20260101-pending.md", _PENDING_PRD_TEXT),
        ("tasks/archive/P1-FEAT-20260101-archived.md", _ARCHIVED_PRD_TEXT),
        ("tasks/pending/P1-FEAT-中文 名称.md", "# PRD: 中文文件名\n"),
    ],
)
def test_content_matches_disk_bytes(
    prd_repo: Path,
    relative_path: str,
    expected_text: str,
) -> None:
    """合法路径返回与磁盘文件逐字节一致的 UTF-8 原文。"""
    response = _get_content(relative_path)

    assert response.status_code == 200
    assert response.content == (prd_repo / relative_path).read_bytes()
    assert response.content.decode("utf-8") == expected_text


def test_content_reflects_later_disk_change(prd_repo: Path) -> None:
    """同一端点重复请求会读到最新磁盘内容，证明没有隐式缓存。"""
    relative_path = "tasks/pending/P1-FEAT-20260101-pending.md"
    assert _get_content(relative_path).content.decode("utf-8") == _PENDING_PRD_TEXT

    updated_text = _PENDING_PRD_TEXT + "\n<!-- 追加一行 -->\n"
    (prd_repo / relative_path).write_text(updated_text, encoding="utf-8")

    assert _get_content(relative_path).content.decode("utf-8") == updated_text


@pytest.mark.parametrize(
    "relative_path",
    [
        "tasks/pending/../../docs/outside.md",
        "tasks/pending/../archive/../../docs/outside.md",
        "/etc/hosts.md",
        "tasks/pending/not-a-prd.py",
        "tasks/pending/P1-FEAT-20260101-pending.md.bak",
        "docs/outside.md",
    ],
)
def test_rejects_illegal_paths(prd_repo: Path, relative_path: str) -> None:
    """目录穿越、绝对路径、非 ``.md``、白名单外路径一律 4xx 且不泄漏内容。"""
    response = _get_content(relative_path)

    assert 400 <= response.status_code < 500
    assert _OUTSIDE_SECRET_TEXT not in response.content.decode("utf-8", errors="replace")


def test_rejects_symlink_escape(prd_repo: Path, tmp_path: Path) -> None:
    """指向白名单之外的符号链接必须被拒绝，不能读出目标文件。"""
    outside_target_path = tmp_path / "outside-target.md"
    outside_target_path.write_text(_OUTSIDE_SECRET_TEXT, encoding="utf-8")
    escape_link_path = prd_repo / "tasks" / "pending" / "escape.md"
    escape_link_path.symlink_to(outside_target_path)

    response = _get_content("tasks/pending/escape.md")

    assert 400 <= response.status_code < 500
    assert _OUTSIDE_SECRET_TEXT not in response.content.decode("utf-8", errors="replace")


def test_rejects_symlink_to_outside_directory(prd_repo: Path, tmp_path: Path) -> None:
    """白名单目录整体被替换为指向外部的符号链接时同样拒绝。"""
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "leak.md").write_text(_OUTSIDE_SECRET_TEXT, encoding="utf-8")
    linked_pending_dir = prd_repo / "tasks" / "pending"
    for existing_entry in linked_pending_dir.iterdir():
        existing_entry.unlink()
    linked_pending_dir.rmdir()
    linked_pending_dir.symlink_to(outside_directory)

    response = _get_content("tasks/pending/leak.md")

    assert 400 <= response.status_code < 500
    assert _OUTSIDE_SECRET_TEXT not in response.content.decode("utf-8", errors="replace")


def test_missing_prd_returns_4xx_not_500(prd_repo: Path) -> None:
    """已删除的 PRD 返回 4xx 与明确错误信息，而不是 500。"""
    relative_path = "tasks/pending/P1-FEAT-20260101-pending.md"
    (prd_repo / relative_path).unlink()

    response = _get_content(relative_path)

    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]


def test_invalid_base64_path_returns_4xx(prd_repo: Path) -> None:
    """非法 base64 编码路径按既有 start 端点的语义返回 4xx。"""
    response = client.get(
        "/api/v1/agent-runner/roadmap/prds/not-valid-base64!!!/content?repo_id=keda-main"
    )

    assert response.status_code == 400


def test_unknown_repo_returns_4xx(prd_repo: Path) -> None:
    """未知仓库返回 4xx，而不是 500。"""
    encoded_path = _encode_prd_path("tasks/pending/P1-FEAT-20260101-pending.md")
    response = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded_path}/content?repo_id=unknown"
    )

    assert response.status_code == 400
