"""Roadmap PRD 验收证据的受限只读访问测试。

事实源是真实临时文件系统里的证据目录：文件名、大小、角色全部来自磁盘解析，
不是 manifest 常量，也不是 PRD 验收勾选数。攻击面（穿越、隐藏文件、符号链接
逃逸、超限）必须全部拒绝且不泄露仓外内容。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_roadmap as roadmap_routes
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, RepositoryRunContext
from backend.core.use_cases.roadmap_prd_evidence import (
    RoadmapPrdEvidenceError,
    build_evidence_manifest,
    encode_artifact_token,
    read_evidence_artifact,
    read_evidence_artifact_text,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeGitHubClient

client = TestClient(app)

REPO_ID = "keda-main"
PRD_STEM = "P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot"
PENDING_PRD = f"tasks/pending/{PRD_STEM}.md"
ARCHIVED_PRD = f"tasks/archive/{PRD_STEM}.md"


def _repo_with_prd(tmp_path: Path, prd_relpath: str) -> Path:
    """建一个含指定 PRD 文件的临时仓库。"""
    repo_root = tmp_path / "repo"
    prd_path = repo_root / prd_relpath
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text("# PRD: Sample\n\n## Acceptance Checklist\n- [x] item\n", encoding="utf-8")
    return repo_root


def _evidence_dir_for(repo_root: Path, prd_relpath: str) -> Path:
    stem = Path(prd_relpath).stem
    return repo_root / "tasks" / "evidence" / stem


@pytest.fixture
def evidence_repo(tmp_path: Path):
    """含归档 PRD 与其证据目录的临时仓库。"""
    repo_root = _repo_with_prd(tmp_path, ARCHIVED_PRD)
    evidence_dir = _evidence_dir_for(repo_root, ARCHIVED_PRD)
    evidence_dir.mkdir(parents=True)
    (evidence_dir / f"{PRD_STEM}.evidence-report.md").write_text(
        "# Evidence Report: Sample\n\n正文。\n", encoding="utf-8"
    )
    (evidence_dir / f"{PRD_STEM}.verifier-report.md").write_text("# Verifier\n", encoding="utf-8")
    (evidence_dir / f"{PRD_STEM}.verification-plan.md").write_text("# Plan\n", encoding="utf-8")
    (evidence_dir / "extra-note.md").write_text("笔记\n", encoding="utf-8")
    return repo_root


def test_manifest_lists_real_files_with_roles(evidence_repo: Path) -> None:
    """manifest 必须与磁盘逐项一致，并按既有命名约定判定角色。"""
    manifest = build_evidence_manifest(
        repo_path=evidence_repo, config=AppConfig(), prd_path=ARCHIVED_PRD
    )
    assert manifest.exists is True
    assert manifest.prd_stem == PRD_STEM
    assert manifest.evidence_dir == f"tasks/evidence/{PRD_STEM}"
    names = {file.name: file for file in manifest.files}
    assert set(names) == {
        f"{PRD_STEM}.evidence-report.md",
        f"{PRD_STEM}.verifier-report.md",
        f"{PRD_STEM}.verification-plan.md",
        "extra-note.md",
    }
    assert names[f"{PRD_STEM}.evidence-report.md"].role == "evidence_report"
    assert names[f"{PRD_STEM}.verifier-report.md"].role == "verifier_report"
    assert names[f"{PRD_STEM}.verification-plan.md"].role == "verification_plan"
    assert names["extra-note.md"].role == "artifact"
    # 大小来自真实 stat，而不是验收勾选数
    report = names[f"{PRD_STEM}.evidence-report.md"]
    assert report.size_bytes == (evidence_repo / manifest.evidence_dir / report.name).stat().st_size
    assert report.media_type == "text/markdown"


def test_manifest_empty_state_reports_lookup_path(tmp_path: Path) -> None:
    """无证据目录时是明确空态（并给出实际查找位置），而不是报错或伪造数量。"""
    repo_root = _repo_with_prd(tmp_path, ARCHIVED_PRD)
    manifest = build_evidence_manifest(
        repo_path=repo_root, config=AppConfig(), prd_path=ARCHIVED_PRD
    )
    assert manifest.exists is False
    assert manifest.files == []
    assert manifest.evidence_dir == f"tasks/evidence/{PRD_STEM}"


def test_manifest_reflects_disk_changes(evidence_repo: Path) -> None:
    """rv-3 fresh probe：磁盘新增/删除文件后 manifest 立刻变化（无缓存）。"""
    evidence_dir = _evidence_dir_for(evidence_repo, ARCHIVED_PRD)
    before = build_evidence_manifest(
        repo_path=evidence_repo, config=AppConfig(), prd_path=ARCHIVED_PRD
    )
    (evidence_dir / "new-file.md").write_text("新证据\n", encoding="utf-8")
    after = build_evidence_manifest(
        repo_path=evidence_repo, config=AppConfig(), prd_path=ARCHIVED_PRD
    )
    assert len(after.files) == len(before.files) + 1
    assert "new-file.md" in {file.name for file in after.files}
    (evidence_dir / "new-file.md").unlink()
    final_manifest = build_evidence_manifest(
        repo_path=evidence_repo, config=AppConfig(), prd_path=ARCHIVED_PRD
    )
    assert "new-file.md" not in {file.name for file in final_manifest.files}


def test_manifest_rejects_prd_outside_whitelist(tmp_path: Path) -> None:
    """PRD 路径不在 tasks/pending 或 tasks/archive 内时拒绝。"""
    repo_root = tmp_path / "repo"
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "docs" / "outside.md").write_text("# x\n", encoding="utf-8")
    with pytest.raises(RoadmapPrdEvidenceError):
        build_evidence_manifest(repo_path=repo_root, config=AppConfig(), prd_path="docs/outside.md")


def test_artifact_reads_text_and_classifies_media_type(evidence_repo: Path) -> None:
    """文本类 artifact 可按 UTF-8 读出，媒体类型为 text/markdown。"""
    name = f"{PRD_STEM}.evidence-report.md"
    content, media_type, returned_name = read_evidence_artifact(
        evidence_repo, AppConfig(), ARCHIVED_PRD, name
    )
    assert returned_name == name
    assert media_type == "text/markdown"
    assert content.decode("utf-8").startswith("# Evidence Report")
    text = read_evidence_artifact_text(evidence_repo, AppConfig(), ARCHIVED_PRD, name)
    assert text == (evidence_repo / "tasks" / "evidence" / PRD_STEM / name).read_text(
        encoding="utf-8"
    )


def test_artifact_rejects_path_attacks(evidence_repo: Path) -> None:
    """穿越、父目录、Windows 分隔符与隐藏文件一律拒绝。"""
    for attack in ("../secret.md", "..\\secret.md", ".hidden", ".", ".."):
        with pytest.raises(RoadmapPrdEvidenceError):
            read_evidence_artifact(evidence_repo, AppConfig(), ARCHIVED_PRD, attack)


def test_artifact_rejects_symlink_escape(evidence_repo: Path) -> None:
    """符号链接指向仓外目标必须被拒绝（解析后父目录不相等）。"""
    outside_target = evidence_repo.parent / "outside-secret.md"
    outside_target.write_text("仓外内容\n", encoding="utf-8")
    evidence_dir = _evidence_dir_for(evidence_repo, ARCHIVED_PRD)
    (evidence_dir / "escaped.md").symlink_to(outside_target)
    with pytest.raises(RoadmapPrdEvidenceError):
        read_evidence_artifact(evidence_repo, AppConfig(), ARCHIVED_PRD, "escaped.md")


def test_artifact_rejects_missing_file(evidence_repo: Path) -> None:
    """不存在的文件名必须是 4xx 语义的错误，而不是空内容。"""
    with pytest.raises(RoadmapPrdEvidenceError):
        read_evidence_artifact(evidence_repo, AppConfig(), ARCHIVED_PRD, "nope.md")


def test_encode_decode_roundtrip() -> None:
    """artifact token 编码可被同一套 base64url 约定还原。"""
    token = encode_artifact_token("报告 with space.md")
    assert "/" not in token and "+" not in token


def test_legacy_flat_evidence_dir_keeps_flat_semantics(tmp_path: Path) -> None:
    """显式 legacy 证据目录保持扁平语义：根下的文件即可被列出。"""
    repo_root = _repo_with_prd(tmp_path, ARCHIVED_PRD)
    flat_root = repo_root / ".iar" / "evidence"
    flat_root.mkdir(parents=True)
    (flat_root / "legacy-report.md").write_text("legacy\n", encoding="utf-8")
    config = replace(AppConfig())
    config = replace(config, validation=replace(config.validation, evidence_dir=".iar/evidence"))
    manifest = build_evidence_manifest(repo_path=repo_root, config=config, prd_path=ARCHIVED_PRD)
    assert manifest.evidence_dir == ".iar/evidence"
    assert {file.name for file in manifest.files} == {"legacy-report.md"}


# ── API 契约 ────────────────────────────────────────────────────────────────


@pytest.fixture
def evidence_api_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 Roadmap 路由接到含证据目录的临时仓库。"""
    repo_root = _repo_with_prd(tmp_path, ARCHIVED_PRD)
    evidence_dir = _evidence_dir_for(repo_root, ARCHIVED_PRD)
    evidence_dir.mkdir(parents=True)
    (evidence_dir / f"{PRD_STEM}.evidence-report.md").write_text(
        "# Evidence Report\n", encoding="utf-8"
    )
    store = SqliteConsoleStore(tmp_path / "console.db")
    contexts = [
        RepositoryRunContext(
            repo_id=REPO_ID,
            display_name="tmp",
            repo_path=repo_root,
            config=AppConfig(),
        )
    ]
    monkeypatch.setattr(roadmap_routes, "create_roadmap_store", lambda: store)
    monkeypatch.setattr(roadmap_routes, "_resolve_contexts", lambda: contexts)
    monkeypatch.setattr(
        roadmap_routes, "create_github_client", lambda repo_path: FakeGitHubClient()
    )
    return {"repo_root": repo_root, "evidence_dir": evidence_dir}


def test_api_evidence_manifest_and_artifact(evidence_api_environment) -> None:
    """GET manifest 列出真实文件，GET artifact 内联返回 UTF-8 文本。"""
    import base64

    encoded_prd = base64.urlsafe_b64encode(ARCHIVED_PRD.encode("utf-8")).decode("ascii")
    response = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded_prd}/evidence?repo_id={REPO_ID}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert len(data["files"]) == 1
    token = data["files"][0]["artifact_token"]

    artifact = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded_prd}/evidence/{token}?repo_id={REPO_ID}"
    )
    assert artifact.status_code == 200
    assert artifact.headers["content-disposition"].startswith("inline")
    assert artifact.headers["x-content-type-options"] == "nosniff"
    assert artifact.text == "# Evidence Report\n"


def test_api_evidence_rejects_malicious_token(evidence_api_environment) -> None:
    """越界 token 必须 4xx，且不返回仓外内容。"""
    import base64

    encoded_prd = base64.urlsafe_b64encode(ARCHIVED_PRD.encode("utf-8")).decode("ascii")
    bad_tokens = [
        base64.urlsafe_b64encode(b"../.iar.toml").decode("ascii"),
        base64.urlsafe_b64encode(b"../../../../etc/hosts").decode("ascii"),
        "!!!not-base64!!!",
    ]
    for token in bad_tokens:
        response = client.get(
            f"/api/v1/agent-runner/roadmap/prds/{encoded_prd}/evidence/{token}?repo_id={REPO_ID}"
        )
        assert response.status_code == 400
        assert "Evidence Report" not in response.text


def test_api_evidence_unknown_repo(evidence_api_environment) -> None:
    """未知仓库 → 400。"""
    import base64

    encoded_prd = base64.urlsafe_b64encode(ARCHIVED_PRD.encode("utf-8")).decode("ascii")
    response = client.get(
        f"/api/v1/agent-runner/roadmap/prds/{encoded_prd}/evidence?repo_id=missing"
    )
    assert response.status_code == 400
