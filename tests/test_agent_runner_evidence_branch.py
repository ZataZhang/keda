"""证据分支读取的单元测试。

人审页常常只在远端 orphan 分支上，看板要能按分支回退；这些用例覆盖分支清单解析、
缓存、页面发现顺序（清单声明优先于文件名）与文件名防护，全部用假 git 输出，
不依赖网络。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core.use_cases import agent_runner_evidence_branch as branch_module


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """每个用例前清空模块级缓存，避免用例之间互相污染。"""
    branch_module._BRANCH_CACHE.clear()
    branch_module._BRANCH_REFRESH_ATTEMPTS.clear()
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


def test_list_evidence_branches_treats_git_timeout_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """远端查询超时按无可用证据处理，不把异常抛给看板接口。"""
    monkeypatch.setattr(branch_module, "_github_remote", _fake_remote)

    def timed_out_git(command: list[str], **_options: object) -> None:
        raise subprocess.TimeoutExpired(command, timeout=branch_module.GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(branch_module, "run", timed_out_git)

    assert branch_module.list_evidence_branches(Path("/tmp/repo")) == {}


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


def test_list_evidence_branches_can_refresh_cached_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看板可在刷新冷却期后发现刚推送的 verifier 前证据分支。"""
    monkeypatch.setattr(branch_module, "_github_remote", _fake_remote)
    now_monotonic = [0.0]
    monkeypatch.setattr(branch_module.time, "monotonic", lambda: now_monotonic[0])
    calls: list[tuple[str, ...]] = []

    def fake_git(repo_path: Path, *args: str) -> str:
        calls.append(args)
        return "abc\trefs/heads/iar-evidence/issue-9\n" if len(calls) > 1 else ""

    monkeypatch.setattr(branch_module, "_git", fake_git)
    repo_path = Path("/tmp/repo-a")

    assert branch_module.list_evidence_branches(repo_path) == {}
    now_monotonic[0] = branch_module.BRANCH_REFRESH_COOLDOWN_SECONDS
    assert branch_module.list_evidence_branches(repo_path, refresh=True) == {
        9: "iar-evidence/issue-9"
    }
    assert branch_module.list_evidence_branches(repo_path, refresh=True) == {
        9: "iar-evidence/issue-9"
    }
    assert len(calls) == 2


def test_refresh_throttles_remote_failures_and_keeps_last_known_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刷新失败时保留最近成功结果，并在冷却期内复用该结果。"""
    monkeypatch.setattr(branch_module, "_github_remote", _fake_remote)
    now_monotonic = [0.0]
    monkeypatch.setattr(branch_module.time, "monotonic", lambda: now_monotonic[0])
    calls: list[tuple[str, ...]] = []

    def fake_git(repo_path: Path, *args: str) -> str | None:
        calls.append(args)
        if len(calls) == 1:
            return "abc\trefs/heads/iar-evidence/issue-9\n"
        return None

    monkeypatch.setattr(branch_module, "_git", fake_git)
    repo_path = Path("/tmp/repo-a")
    expected_branches = {9: "iar-evidence/issue-9"}

    assert branch_module.list_evidence_branches(repo_path) == expected_branches
    now_monotonic[0] += branch_module.BRANCH_REFRESH_COOLDOWN_SECONDS
    assert branch_module.list_evidence_branches(repo_path, refresh=True) == expected_branches
    now_monotonic[0] += 1
    assert branch_module.list_evidence_branches(repo_path, refresh=True) == expected_branches
    assert len(calls) == 2


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


def test_branch_with_artifacts_and_no_review_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """证据分支只有日志和截图时，仍提供可打开的证据索引。"""
    monkeypatch.setattr(
        branch_module, "list_evidence_branches", lambda repo: {140: "iar-evidence/issue-140"}
    )
    monkeypatch.setattr(branch_module, "_fetch_branch", lambda repo, branch: "a123")
    monkeypatch.setattr(
        branch_module, "_git", lambda repo, *args: "capture.png\nvalidation.txt\nunsafe.html\n"
    )

    page = branch_module.read_branch_page(Path("/tmp/repo"), 140)

    assert page is not None
    assert page.is_evidence_overview
    assert page.revision == "a123"
    assert "(capture.png)" in page.markdown
    assert "(validation.txt)" in page.markdown
    assert "unsafe.html" not in page.markdown
