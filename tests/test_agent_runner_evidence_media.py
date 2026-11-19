"""证据视频解析的单元测试。

看板的「证据」按钮只在真找到可播放文件时出现，因此解析必须同时覆盖三种落点、
两种目录命名，以及「只有页面没有视频」这种情况。
"""

from __future__ import annotations

from pathlib import Path

from backend.core.use_cases.agent_runner_evidence_media import (
    EvidenceMedia,
    evidence_stem,
    find_evidence_media,
)


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_evidence_stem_prefers_prd_name() -> None:
    """有 PRD 时用 PRD 文件名做证据目录名。"""
    assert (
        evidence_stem(358, "tasks/pending/P1-BUG-20260101-000000-x.md")
        == "P1-BUG-20260101-000000-x"
    )
    assert evidence_stem(358, None) == "issue-358"


def test_finds_video_in_worktree_with_issue_number_dir(tmp_path: Path) -> None:
    """执行 worktree 里按 Issue 编号命名的目录也能找到视频。"""
    _touch(tmp_path / ".kc-worktrees" / "issue-55" / ".iar" / "evidence" / "55" / "demo.mp4")
    media = find_evidence_media(tmp_path, 55)
    assert media.video_path is not None
    assert media.video_path.name == "demo.mp4"


def test_prefers_mp4_over_gif(tmp_path: Path) -> None:
    """同一目录有多个可播放文件时按后缀优先级取。"""
    directory = tmp_path / "tasks" / "evidence" / "issue-7"
    _touch(directory / "walkthrough.gif")
    _touch(directory / "demo.mp4")
    media = find_evidence_media(tmp_path, 7)
    assert media.video_path is not None
    assert media.video_path.suffix == ".mp4"


def test_skips_empty_files(tmp_path: Path) -> None:
    """空文件不算证据，避免按钮点开是黑屏。"""
    _touch(tmp_path / ".iar" / "evidence" / "issue-9" / "demo.mp4", b"")
    assert find_evidence_media(tmp_path, 9).video_path is None


def test_page_without_video(tmp_path: Path) -> None:
    """只有人审页时仍然返回页面、不给视频，按钮因此不出现。"""
    _touch(tmp_path / "tasks" / "evidence" / "issue-3" / "human-review.md", b"# page")
    media = find_evidence_media(tmp_path, 3)
    assert media.video_path is None
    assert media.page_path is not None
    assert media.has_any is True


def test_missing_everything_returns_empty(tmp_path: Path) -> None:
    """目录不存在时返回空结果而不是抛错。"""
    assert find_evidence_media(tmp_path / "nope", 1) == EvidenceMedia()


def test_prefers_canonical_checklist_page(tmp_path: Path) -> None:
    """人审页优先取 PRD 规范里的 human-review-checklist.md。"""
    directory = tmp_path / "tasks" / "evidence" / "issue-11"
    _touch(directory / "README.md", b"# readme")
    _touch(directory / "human-review-checklist.md", b"# checklist")
    media = find_evidence_media(tmp_path, 11)
    assert media.page_path is not None
    assert media.page_path.name == "human-review-checklist.md"
