"""运行看板「证据」胶囊按钮的后端解析：在仓库里找到可播放的视觉证据。

一个任务的人审证据有三种落点：执行 worktree 的 .iar/evidence/<stem>/、仓库根的
.iar/evidence/<stem>/（历史遗留），以及随代码提交的 tasks/evidence/<stem>/。本
模块只回答「有没有、在哪」，不读文件内容，也不决定怎么展示。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: 可播放的视频后缀，顺序即优先级。
VIDEO_SUFFIXES: tuple[str, ...] = (".mp4", ".webm", ".mov", ".gif")

#: 人审页候选文件名，顺序即优先级。
PAGE_FILENAMES: tuple[str, ...] = (
    "human-review-checklist.md",
    "human-review.md",
    "README.md",
    "README-evidence.md",
)


@dataclass(frozen=True)
class EvidenceMedia:
    """一个 Issue 的可呈递证据。

    Attributes:
        video_path: 可播放视频的绝对路径；没有时为 None。
        page_path: 本地人审页（Markdown）的绝对路径；没有时为 None。
        page_ref: 人审页文件名（本地或证据分支上的）；没有时为 None。
        source: 证据来源：本地目录或证据分支名；默认 local。
    """

    video_path: Path | None = None
    page_path: Path | None = None
    page_ref: str | None = None
    source: str = "local"

    @property
    def has_any(self) -> bool:
        """返回是否存在任一可呈递证据。"""
        return (
            self.video_path is not None or self.page_path is not None or self.page_ref is not None
        )


def evidence_stem(issue_number: int, prd_path: str | None) -> str:
    """返回证据目录名：有 PRD 时用 PRD 文件名，否则用 issue-<N>。

    Args:
        issue_number: Issue 编号。
        prd_path: Issue 关联的 PRD 相对路径；未关联时为 None。

    Returns:
        str: 证据目录名。
    """
    if prd_path:
        name = Path(prd_path).name
        return name[:-3] if name.endswith(".md") else name
    return f"issue-{issue_number}"


def _candidate_dirs(repo_path: Path, issue_number: int, stem: str) -> tuple[Path, ...]:
    """返回按优先级排列的证据目录候选。

    执行侧的证据目录既可能按 PRD 名（或 issue-<N>）命名，也可能只用 Issue 编号，
    两种都查，避免因为命名差异看不到刚采到的证据。
    """
    worktree = repo_path / ".kc-worktrees" / f"issue-{issue_number}"
    names = tuple(dict.fromkeys((stem, str(issue_number))))
    return tuple(
        directory
        for name in names
        for directory in (
            repo_path / ".iar" / "evidence" / name,
            worktree / ".iar" / "evidence" / name,
            repo_path / "tasks" / "evidence" / name,
        )
    )


def _first_video(directory: Path) -> Path | None:
    """返回目录内按后缀优先级选出的第一个视频文件。"""
    for suffix in VIDEO_SUFFIXES:
        for candidate in sorted(directory.glob(f"*{suffix}")):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def _first_page(directory: Path) -> Path | None:
    """返回目录内按文件名优先级选出的人审页。"""
    for filename in PAGE_FILENAMES:
        candidate = directory / filename
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def find_evidence_media(
    repo_path: Path,
    issue_number: int,
    prd_path: str | None = None,
) -> EvidenceMedia:
    """在仓库里查找该 Issue 的视频与人审页。

    逐个候选目录查找，视频与人审页可以分别来自不同目录（例如视频还在执行
    worktree 里、页面已随代码提交）。任一目录不可读都按「没有」处理。

    Args:
        repo_path: 仓库根目录。
        issue_number: Issue 编号。
        prd_path: Issue 关联的 PRD 相对路径；未关联时为 None。

    Returns:
        EvidenceMedia: 找到的路径；都没有时两个字段均为 None。
    """
    stem = evidence_stem(issue_number, prd_path)
    video_path: Path | None = None
    page_path: Path | None = None
    for directory in _candidate_dirs(repo_path, issue_number, stem):
        try:
            if not directory.is_dir():
                continue
            video_path = video_path or _first_video(directory)
            page_path = page_path or _first_page(directory)
        except OSError:
            continue
        if video_path is not None and page_path is not None:
            break
    return EvidenceMedia(
        video_path=video_path,
        page_path=page_path,
        page_ref=page_path.name if page_path is not None else None,
    )
