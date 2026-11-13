"""证据分支读取的单元测试。

人审页常常只在远端 orphan 分支上，看板要能按分支回退；这些用例覆盖分支清单解析、
缓存、页面发现顺序（清单声明优先于文件名）与文件名防护，全部用假 git 输出，
不依赖网络。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.use_cases import agent_runner_evidence_branch as branch_module


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """每个用例前清空模块级缓存，避免用例之间互相污染。"""
    branch_module._BRANCH_CACHE.clear()
    branch_module._REMOTE_CACHE.clear()


def _fake_remote(repo_path: Path) -> str:
    """假远端解析：固定返回 dw-github。"""
    return "dw-github"


def test_list_evidence_branches_maps_issue_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """只认名字里带 evidence 且以 issue-<N> 结尾的分支。"""
    monkeypatch.setattr(branch_module, "_github_remote", _fake_remote)
    listing = (
        "abc\trefs/heads/iar-evidence/issue-49\n"
        "def\trefs/heads/iar-evidence/issue-55\n"
        "ghi\trefs/heads/feature/issue-7\n"
        "jkl\trefs/heads/evidence/manual-review\n"
    )
    monkeypatch.setattr(branch_module, "_git", lambda repo, *args: listing)
    branches = branch_module.list_evidence_branches(Path("/tmp/repo"))
    assert branches == {49: "iar-evidence/issue-49", 55: "iar-evidence/issue-55"}


def test_list_evidence_branches_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一仓库的第二次调用直接命中缓存，不再打远端。"""
    monkeypatch.setattr(branch_module, "_github_remote", _fake_remote)
    calls: list[tuple[str, ...]] = []

    def fake_git(repo: Path, *args: str) -> str:
        calls.append(args)
        return "abc\trefs/heads/iar-evidence/issue-9\n"

    monkeypatch.setattr(branch_module, "_git", fake_git)
    branch_module.list_evidence_branches(Path("/tmp/repo-a"))
    branch_module.list_evidence_branches(Path("/tmp/repo-a"))
    assert len(calls) == 1


def test_no_github_remote_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 github 远端时不尝试网络，直接返回空。"""
    monkeypatch.setattr(branch_module, "_github_remote", lambda repo: None)
    assert branch_module.list_evidence_branches(Path("/tmp/repo-b")) == {}


def test_declared_page_wins_over_filename() -> None:
    """证据清单声明的页面优先于约定文件名。"""
    siblings = ["human-review-checklist.md", "review.md"]
    declared = branch_module._declared_page(siblings, '{"review_page": "review.md"}')
    assert declared == "review.md"


def test_declared_page_ignores_missing_or_broken_manifest() -> None:
    """清单缺失、坏 JSON 或声明不存在的文件时都回退到约定文件名。"""
    siblings = ["human-review-checklist.md"]
    assert branch_module._declared_page(siblings, None) is None
    assert branch_module._declared_page(siblings, "not json") is None
    assert branch_module._declared_page(siblings, '{"review_page": "nope.md"}') is None


def test_read_branch_page_falls_back_to_conventional_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有清单声明时按约定文件名取页面。"""
    monkeypatch.setattr(
        branch_module, "list_evidence_branches", lambda repo: {55: "iar-evidence/issue-55"}
    )
    monkeypatch.setattr(branch_module, "_fetch_branch", lambda repo, branch: "FETCH_HEAD")

    def fake_git(repo: Path, *args: str) -> str:
        if args[0] == "ls-tree":
            return "before.png\nhuman-review-checklist.md\n"
        if args[0] == "show":
            return "# page body"
        raise AssertionError(args)

    monkeypatch.setattr(branch_module, "_git", fake_git)
    page = branch_module.read_branch_page(Path("/tmp/repo"), 55)
    assert page is not None
    assert page.page_name == "human-review-checklist.md"
    assert page.branch == "iar-evidence/issue-55"
    assert "before.png" in page.siblings


def test_read_branch_file_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """图片名必须是纯文件名，不允许带路径。"""
    assert branch_module.read_branch_file(Path("/tmp/repo"), 55, "../secret.png") is None


def test_find_branch_media_marks_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退命中时把来源标成证据分支名，供前端展示。"""
    monkeypatch.setattr(
        branch_module,
        "read_branch_page",
        lambda repo, issue: branch_module.BranchPage(
            branch="iar-evidence/issue-49",
            page_name="human-review-checklist.md",
            markdown="# page",
            siblings=("human-review-checklist.md",),
        ),
    )
    media = branch_module.find_branch_media(Path("/tmp/repo"), 49)
    assert media is not None
    assert media.source == "iar-evidence/issue-49"
    assert media.page_ref == "human-review-checklist.md"
