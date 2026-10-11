"""PRD 交付进度聚合（backlog_prd_progress）单测 + 端点契约。

覆盖弱信号读数口径：验证计划解析、清单勾选、产出归属（结构化清单 / 文件名前缀 / 混合 /
无）、报告三件套、工作区优先级（分支优先、归档终态）、以及「读取失败 ≠ 空结果」的降级。
端点契约用真实 FastAPI 路由 + 顶替的仓库上下文（既有 backlog 测试同款模式）。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.api.routes.agent_runner_backlog as backlog_routes
import backend.core.use_cases.backlog_prd_progress as backlog_prd_progress_module
from backend.api.app import app
from backend.core.shared.models.agent_runner import AppConfig, CommandResult, RepositoryRunContext
from backend.core.use_cases.backlog_prd_evidence import build_evidence_manifest
from backend.core.use_cases.backlog_prd_progress import (
    EvidenceReports,
    build_prd_progress,
    parse_validation_plan,
)

client = TestClient(app)

REPO_ID = "keda-main"
PRD_STEM = "P1-FEAT-20261010-093054-console-prd-progress-signals"
PENDING_PRD = f"tasks/pending/{PRD_STEM}.md"
ARCHIVED_PRD = f"tasks/archive/{PRD_STEM}.md"
WORKTREE_BRANCH = "console-prd-progress-signals"

PLAN_HEADING = "## 7.6 Realistic Validation Plan"
PLAN_BLOCK = (
    f"{PLAN_HEADING}\n\n"
    "```yaml\n"
    "- id: rv-1\n"
    '  behavior: "运行中详情显示交付读数"\n'
    "  reviewer: human\n"
    "- id: rv-2\n"
    '  behavior: "读取边界正确"\n'
    "  reviewer: verifier\n"
    "- id: rv-3\n"
    '  summary: "影响树与 CLI 一致"\n'
    "  reviewer: verifier\n"
    "```\n"
)
CHECKLIST_BLOCK = (
    "## Acceptance Checklist\n"
    "- [x] a\n- [x] b\n- [x] c\n"
    "- [ ] d\n- [ ] e\n- [ ] f\n- [ ] g\n- [ ] h\n- [ ] i\n- [ ] j\n- [ ] k\n- [ ] l\n"
)
IMPACT_BLOCK = (
    "## 7.2 Change Impact Tree\n\n"
    "```text\nrepo-root\n├── touched.py [修改]\n└── missed.py [新增]\n```\n"
)


class _Runner:
    """按 porcelain / git 片段顶替只读命令端口。"""

    def __init__(
        self, *, porcelain: str | None = None, git: dict | None = None, fail: bool = False
    ):
        self.porcelain = porcelain
        self.git = git or {}
        self.fail = fail

    def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001, D102
        tokens = tuple(list(command)[3:])
        if tokens == ("worktree", "list", "--porcelain"):
            if self.fail:
                return CommandResult(
                    command=tuple(command), return_code=128, stdout="", stderr="boom"
                )
            return CommandResult(
                command=tuple(command), return_code=0, stdout=self.porcelain or "", stderr=""
            )
        if tokens in self.git:
            stdout, code = self.git[tokens]
            return CommandResult(command=tuple(command), return_code=code, stdout=stdout, stderr="")
        return CommandResult(command=tuple(command), return_code=0, stdout="", stderr="")


def _porcelain_for(path: Path, branch: str = WORKTREE_BRANCH) -> str:
    return (
        f"worktree {path}\nHEAD abc\nbranch refs/heads/{branch}\n\n"
        f"worktree {path.parent / 'main'}\nHEAD def\nbranch refs/heads/main\n\n"
    )


def _evidence_json(number_map: dict[str, int]) -> str:
    items = []
    for file_name, item_number in number_map.items():
        items.append(
            {
                "item_number": item_number,
                "item_name": f"条目 {item_number}",
                "command": "pytest",
                "evidence_files": [file_name],
                "output_summary": "ok",
                "explanation": "why",
                "risks": "none",
            }
        )
    return json.dumps({"version": 1, "language": "zh-CN", "items": items}, ensure_ascii=False)


def _make_repo(
    tmp_path: Path,
    *,
    prd_relpath: str = PENDING_PRD,
    body: str = f"# PRD: Demo\n\n{PLAN_BLOCK}\n{CHECKLIST_BLOCK}\n",
    evidence_files: Sequence[str] = (),
    evidence_json: dict[str, int] | None = None,
) -> Path:
    repo_root = tmp_path / "repo"
    prd_path = repo_root / prd_relpath
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(body, encoding="utf-8")
    evidence_dir = repo_root / "tasks" / "evidence" / PRD_STEM
    if evidence_files or evidence_json is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for file_name in evidence_files:
            (evidence_dir / file_name).write_text(f"# {file_name}\n", encoding="utf-8")
        if evidence_json is not None:
            (evidence_dir / "evidence.json").write_text(
                _evidence_json(evidence_json), encoding="utf-8"
            )
    return repo_root


def _make_worktree(
    tmp_path: Path,
    *,
    prd_body: str | None = None,
    evidence_files: Sequence[str] = (),
    evidence_json: dict | None = None,
) -> Path:
    wt = tmp_path / ".iar-worktrees" / WORKTREE_BRANCH
    prd_copy = wt / PENDING_PRD
    prd_copy.parent.mkdir(parents=True, exist_ok=True)
    prd_copy.write_text(prd_body or "# PRD: WT\n", encoding="utf-8")
    evidence_dir = wt / "tasks" / "evidence" / PRD_STEM
    if evidence_files or evidence_json is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for file_name in evidence_files:
            (evidence_dir / file_name).write_text(f"WT {file_name}\n", encoding="utf-8")
        if evidence_json is not None:
            (evidence_dir / "evidence.json").write_text(
                _evidence_json(evidence_json), encoding="utf-8"
            )
    return wt


# ── 计划解析单元 ────────────────────────────────────────────────────────────


def test_parse_validation_plan_extracts_items() -> None:
    """解析顶层条目：编号、摘要（behavior 优先）、人验/机验。"""
    plan = parse_validation_plan(PLAN_BLOCK)
    assert [item.id for item in plan] == ["rv-1", "rv-2", "rv-3"]
    assert plan[0].summary == "运行中详情显示交付读数"
    assert plan[0].reviewer == "human"
    assert plan[2].summary == "影响树与 CLI 一致"
    assert plan[2].reviewer == "verifier"


def test_parse_validation_plan_absent_returns_empty() -> None:
    """无 §7.6 → 空列表（前端显示 -，不是计划 0 项的伪造分母）。"""
    assert parse_validation_plan("# PRD\n\nno plan here\n") == []


def test_parse_validation_plan_reviewer_other_is_null() -> None:
    """reviewer 非 human/verifier 时记 None。"""
    plan = parse_validation_plan(
        "## Realistic Validation Plan\n\n```yaml\n- id: rv-1\n  behavior: x\n  reviewer: robot\n```\n"
    )
    assert plan[0].reviewer is None


# ── build_prd_progress：来源与优先级 ─────────────────────────────────────────


def test_pending_no_worktree_uses_main(tmp_path: Path) -> None:
    """pending 且检索成功无命中 → 主仓库兜底，impact 报 no_worktree。"""
    repo_root = _make_repo(tmp_path, body=f"# PRD: Demo\n\n{PLAN_BLOCK}\n{CHECKLIST_BLOCK}\n")
    no_match = "worktree /repo/main\nHEAD def\nbranch refs/heads/main\n\n"
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=no_match),
    )
    assert progress.prd_source == "main"
    assert progress.worktree_status == "none"
    assert progress.checklist.status == "available"
    assert (progress.checklist.checked, progress.checklist.total) == (3, 12)
    assert [item.id for item in progress.evidence.planned] == ["rv-1", "rv-2", "rv-3"]
    assert progress.impact_status == "no_worktree"


def test_pending_matched_reads_worktree_branch_first(tmp_path: Path) -> None:
    """唯一命中 → 工作区优先，manifest 文件带 source=worktree。"""
    repo_root = _make_repo(tmp_path)
    _make_worktree(
        tmp_path,
        prd_body=f"# PRD: WT\n\n{PLAN_BLOCK}\n{CHECKLIST_BLOCK}\n",
        evidence_files=["rv-1-run.png"],
    )
    wt = tmp_path / ".iar-worktrees" / WORKTREE_BRANCH
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=_porcelain_for(wt)),
    )
    assert progress.prd_source == "worktree"
    assert progress.worktree_status == "matched"
    assert progress.worktree is not None and progress.worktree.branch == WORKTREE_BRANCH
    sources = {f.name: f.source for f in _manifest_files(repo_root, tmp_path, wt)}
    assert sources["rv-1-run.png"] == "worktree"


def test_archived_terminal_ignores_residual_worktree(tmp_path: Path) -> None:
    """主仓库已归档 → 终态事实来源，即便残留工作区也读主仓库、impact 为 terminal_archive。"""
    repo_root = _make_repo(
        tmp_path, prd_relpath=ARCHIVED_PRD, body=f"# PRD: Demo\n\n{PLAN_BLOCK}\n{CHECKLIST_BLOCK}\n"
    )
    wt = _make_worktree(tmp_path, prd_body="# PRD: WT residual\n", evidence_files=["rv-1-run.png"])
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=ARCHIVED_PRD,
        process_runner=_Runner(porcelain=_porcelain_for(wt)),
    )
    assert progress.prd_source == "main"
    assert progress.worktree_status == "not_applicable"
    assert progress.impact_status == "terminal_archive"


def test_ambiguous_worktree_marks_unavailable(tmp_path: Path) -> None:
    """多重命中 → 不以主仓库冒充：来源 null、清单/证据不可用、planned 为 None。"""
    repo_root = _make_repo(tmp_path)
    ambiguous = (
        f"worktree {tmp_path / 'wt1'}\nHEAD a\nbranch refs/heads/{WORKTREE_BRANCH}\n\n"
        f"worktree {tmp_path / 'wt2'}\nHEAD b\nbranch refs/heads/feat/{WORKTREE_BRANCH}\n\n"
    )
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=ambiguous),
    )
    assert progress.prd_source is None
    assert progress.worktree_status == "ambiguous"
    assert progress.checklist.status == "unavailable"
    assert progress.evidence.status == "unavailable"
    assert progress.evidence.planned is None
    assert progress.evidence.file_count is None


def test_worktree_query_failure_marks_unavailable(tmp_path: Path) -> None:
    """worktree 检索失败 → unavailable，绝不退化成 none 并回落主仓库。"""
    repo_root = _make_repo(tmp_path)
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(fail=True),
    )
    assert progress.worktree_status == "unavailable"
    assert progress.prd_source is None
    assert progress.evidence.status == "unavailable"


# ── 产出归属 ────────────────────────────────────────────────────────────────


def test_attribution_manifest_by_item_number(tmp_path: Path) -> None:
    """有效清单按 item_number 归属（即便 item_name 是描述文本），attribution_source=manifest。"""
    repo_root = _make_repo(
        tmp_path,
        evidence_files=["rv-1-run.png", "rv-3-run.png"],
        evidence_json={"rv-1-run.png": 1, "rv-3-run.png": 3},
    )
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    ev = progress.evidence
    assert ev.status == "complete"
    assert ev.attribution_source == "manifest"
    assert ev.produced_ids == ["rv-1", "rv-3"]
    assert ev.produced_planned_count == 2
    # evidence.json 自身也是一个可展示文件，故共 3 个文件
    assert ev.file_count == 3


def test_attribution_filename_fallback_when_manifest_missing(tmp_path: Path) -> None:
    """无结构化清单 → 文件名前缀回退，attribution_source=filename。"""
    repo_root = _make_repo(tmp_path, evidence_files=["rv-2-run.png"])
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    assert progress.evidence.attribution_source == "filename"
    assert progress.evidence.produced_ids == ["rv-2"]


def test_attribution_filename_fallback_when_manifest_damaged(tmp_path: Path) -> None:
    """损坏的结构化清单（JSON 解析失败）→ 同样回退文件名前缀，不使端点整体失败。"""
    repo_root = _make_repo(tmp_path, evidence_files=["rv-2-run.png"])
    evidence_dir = repo_root / "tasks" / "evidence" / PRD_STEM
    (evidence_dir / "evidence.json").write_text("{ not valid json", encoding="utf-8")

    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    assert progress.evidence.status == "complete"
    assert progress.evidence.attribution_source == "filename"
    assert progress.evidence.produced_ids == ["rv-2"]
    assert progress.evidence.produced_planned_count == 1


def test_unreadable_manifest_falls_back_to_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """结构化清单读取失败（无权限等）→ 回退文件名前缀，不让端点整体失败。"""
    repo_root = _make_repo(
        tmp_path, evidence_files=["rv-2-run.png"], evidence_json={"rv-2-run.png": 2}
    )

    def _raise_permission_denied(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise PermissionError("denied")

    monkeypatch.setattr(
        backlog_prd_progress_module, "load_evidence_manifest", _raise_permission_denied
    )
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    assert progress.evidence.status == "complete"
    assert progress.evidence.attribution_source == "filename"
    assert progress.evidence.produced_ids == ["rv-2"]


def test_attribution_mixed_when_manifest_and_prefix_both_used(tmp_path: Path) -> None:
    """同一合并文件集里既用到清单映射又用到前缀回退 → attribution_source=mixed。"""
    repo_root = _make_repo(
        tmp_path,
        evidence_files=["shot.png", "rv-2-run.png"],
        evidence_json={"shot.png": 1},
    )
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    evidence = progress.evidence
    assert evidence.status == "complete"
    assert evidence.attribution_source == "mixed"
    assert evidence.produced_ids == ["rv-1", "rv-2"]
    assert evidence.produced_planned_count == 2
    assert evidence.file_count == 3  # 含 evidence.json 自身


def test_attribution_unattributed_does_not_count_planned(tmp_path: Path) -> None:
    """计划外编号不增加 produced_planned_count；无归属文件归其他、attribution=none。"""
    repo_root = _make_repo(tmp_path, evidence_files=["notes.txt", "rv-9-run.png"])
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    assert progress.evidence.attribution_source == "filename"
    assert progress.evidence.produced_ids == ["rv-9"]
    assert progress.evidence.produced_planned_count == 0


def test_reports_flags_by_role(tmp_path: Path) -> None:
    """三件套按文件名角色识别「是否已产出」。"""
    repo_root = _make_repo(
        tmp_path,
        evidence_files=[
            f"{PRD_STEM}.verification-plan.md",
            f"{PRD_STEM}.evidence-report.md",
        ],
    )
    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    reports = progress.evidence.reports
    assert reports.verification_plan is True
    assert reports.evidence_report is True
    assert reports.verifier_report is False


def _block_dir_enumeration(monkeypatch: pytest.MonkeyPatch, blocked_dir_name: str) -> None:
    """让指定名字的目录在 ``iterdir`` 时抛 PermissionError（模拟无权限枚举）。"""
    original_iterdir = Path.iterdir

    def _iterdir_with_denied(self: Path):  # noqa: ANN202
        if self.name == blocked_dir_name:
            raise PermissionError(f"permission denied: {self}")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir_with_denied)


def test_unreadable_evidence_dir_marks_unavailable_not_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据目录存在但枚举失败 → unavailable 且计数为 null，不伪装成「确认无文件」。"""
    repo_root = _make_repo(tmp_path, evidence_files=["rv-1-run.png"])
    _block_dir_enumeration(monkeypatch, PRD_STEM)

    progress = build_prd_progress(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=""),
    )
    evidence = progress.evidence
    assert evidence.status == "unavailable"
    assert evidence.file_count is None
    assert evidence.produced_planned_count is None
    assert evidence.reports == EvidenceReports(None, None, None)

    manifest = build_evidence_manifest(
        repo_path=repo_root, config=AppConfig(), prd_path=PENDING_PRD
    )
    assert manifest.status == "unavailable"
    assert manifest.files == []


def _manifest_files(repo_root: Path, tmp_path: Path, wt: Path):
    from backend.core.use_cases.backlog_prd_evidence import build_evidence_manifest
    from backend.core.use_cases.backlog_prd_progress import resolve_evidence_lookup

    candidates, _status = resolve_evidence_lookup(
        repo_path=repo_root,
        config=AppConfig(),
        prd_path=PENDING_PRD,
        process_runner=_Runner(porcelain=_porcelain_for(wt)),
    )
    return build_evidence_manifest(
        repo_path=repo_root, config=AppConfig(), prd_path=PENDING_PRD, candidates=candidates
    ).files


# ── 端点契约 ────────────────────────────────────────────────────────────────


@pytest.fixture
def progress_api_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = _make_repo(tmp_path, body=f"# PRD: Demo\n\n{PLAN_BLOCK}\n{CHECKLIST_BLOCK}\n")
    contexts = [
        RepositoryRunContext(
            repo_id=REPO_ID, display_name="tmp", repo_path=repo_root, config=AppConfig()
        )
    ]
    monkeypatch.setattr(backlog_routes, "create_backlog_store", lambda: _NullStore())
    monkeypatch.setattr(backlog_routes, "_resolve_contexts", lambda: contexts)
    monkeypatch.setattr(backlog_routes, "create_process_runner", lambda: _Runner(porcelain=""))
    return repo_root


class _NullStore:
    def append_audit(self, entry) -> None:  # noqa: ANN001
        return None


def _encoded(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii")


def test_endpoint_progress_ok(progress_api_environment: Path) -> None:
    """GET /progress 返回聚合读数，字段与 core 一致。"""
    response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded(PENDING_PRD)}/progress?repo_id={REPO_ID}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["prd_source"] == "main"
    assert data["checklist"]["checked"] == 3
    assert data["checklist"]["total"] == 12
    assert [item["id"] for item in data["evidence"]["planned"]] == ["rv-1", "rv-2", "rv-3"]
    assert data["evidence"]["status"] == "complete"


def test_endpoint_progress_unknown_repo(progress_api_environment: Path) -> None:
    response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded(PENDING_PRD)}/progress?repo_id=missing"
    )
    assert response.status_code == 400


def test_endpoint_progress_missing_prd(progress_api_environment: Path) -> None:
    """不存在的 PRD 路径 → 400，而不是 500。"""
    response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded('tasks/pending/nope.md')}/progress?repo_id={REPO_ID}"
    )
    assert response.status_code == 400


def test_endpoints_survive_unreadable_evidence_dir(
    progress_api_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据目录枚举失败时两端点都 200 并以 status=unavailable 披露，而不是 500。"""
    evidence_dir = progress_api_environment / "tasks" / "evidence" / PRD_STEM
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "rv-1-run.png").write_text("x", encoding="utf-8")
    _block_dir_enumeration(monkeypatch, PRD_STEM)

    progress_response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded(PENDING_PRD)}/progress?repo_id={REPO_ID}"
    )
    assert progress_response.status_code == 200
    progress_data = progress_response.json()
    assert progress_data["evidence"]["status"] == "unavailable"
    assert progress_data["evidence"]["file_count"] is None
    assert progress_data["evidence"]["produced_planned_count"] is None

    manifest_response = client.get(
        f"/api/v1/agent-runner/backlog/prds/{_encoded(PENDING_PRD)}/evidence?repo_id={REPO_ID}"
    )
    assert manifest_response.status_code == 200
    assert manifest_response.json()["status"] == "unavailable"
