"""PRD 发布采用调用边界验证结构化证据协议。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from backend.core.use_cases.create_issue_from_prd import IssueFromPrdRequest, create_issue_from_prd
from tests.conftest import FakeGitHubClient

_MARKER = re.compile(
    r"""<!--\s*iar:structured-evidence\s+version=1\s+language=(?P<quote>["']?)(?P<language>[-\w]+)(?P=quote)\s*-->"""
)


def _publish(tmp_path: Path, validation_text: str, **overrides: object) -> str:
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    relative_prd = Path("tasks/pending/structured.md")
    prd_file = tmp_path / relative_prd
    prd_file.parent.mkdir(parents=True)
    prd_file.write_text(f"# PRD: Structured\n\n{validation_text}\n", encoding="utf-8")
    client = FakeGitHubClient()
    issue_options = {
        "repo_path": tmp_path,
        "prd_path": relative_prd,
        "issue_type": "feature",
        **overrides,
    }
    create_issue_from_prd(request=IssueFromPrdRequest(**issue_options), github_client=client)
    return next(record["body"] for record in client.calls if record["method"] == "create_issue")


def test_validation_emits_versioned_marker(tmp_path: Path) -> None:
    validation = "### Realistic Validation\n\n- [ ] Install and open CLI"
    body = _publish(tmp_path, validation)
    markers = list(_MARKER.finditer(body))
    assert len(markers) == 1, body
    assert markers[0].group("language") == "zh-CN"
    assert "Install and open CLI" in body


def test_validation_marker_uses_requested_language(tmp_path: Path) -> None:
    body = _publish(
        tmp_path, "### Realistic Validation\n\n- [ ] Run tests", validation_language="en-US"
    )
    marker = _MARKER.search(body)
    assert marker is not None and marker["language"] == "en-US"


def test_checklist_free_issue_keeps_legacy_body(tmp_path: Path) -> None:
    body = _publish(tmp_path, "### Summary\n\nPreserve this content.")
    assert "Canonical PRD" in body and "structured.md" in body
    assert "iar:structured-evidence" not in body


def test_explicit_opt_out_preserves_validation_but_skips_marker(tmp_path: Path) -> None:
    body = _publish(
        tmp_path, "### Realistic Validation\n\n- [ ] Run tests", structured_evidence=False
    )
    assert "Run tests" in body
    assert "iar:structured-evidence" not in body
