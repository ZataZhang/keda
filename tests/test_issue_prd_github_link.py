"""issue_prd_github_link 的单元与接线测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.core.use_cases.create_issue_from_prd import (
    IssueFromPrdRequest,
    create_issue_from_prd,
)
from backend.core.use_cases.issue_prd_github_link import (
    append_prd_github_link,
    parse_github_slug_from_git_config,
    parse_github_slug_from_url,
    resolve_github_slug,
)
from tests.conftest import FakeGitHubClient

_ANCHOR = "- PRD path: `tasks/pending/example.md`"


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command for test setup."""
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8")


def _init_repo(path: Path) -> None:
    """Initialize a git repository."""
    _run(["git", "init", "-b", "main"], path)
    _run(["git", "config", "user.name", "Test"], path)
    _run(["git", "config", "user.email", "test@example.com"], path)
    (path / "README.md").write_text("test\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# URL / config 解析
# ---------------------------------------------------------------------------


def test_parse_slug_from_ssh_url() -> None:
    assert parse_github_slug_from_url("git@github.com:ZataZhang/keda.git") == "ZataZhang/keda"


def test_parse_slug_from_https_url() -> None:
    assert parse_github_slug_from_url("https://github.com/owner/repo-name.git") == "owner/repo-name"


def test_parse_slug_from_ssh_protocol_url() -> None:
    assert parse_github_slug_from_url("ssh://git@github.com/owner/repo") == "owner/repo"


def test_parse_slug_from_non_github_url_returns_empty() -> None:
    assert parse_github_slug_from_url("/tmp/bare-remote.git") == ""
    assert parse_github_slug_from_url("git@gitlab.com:owner/repo.git") == ""


def test_parse_slug_from_git_config_picks_first_github_remote() -> None:
    config_text = (
        "[core]\n\trepositoryformatversion = 0\n"
        '[remote "upstream"]\n\turl = /tmp/local-bare.git\n'
        '[remote "origin"]\n\turl = git@github.com:owner/repo.git\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
    )
    assert parse_github_slug_from_git_config(config_text) == "owner/repo"


def test_parse_slug_from_git_config_ignores_non_remote_sections() -> None:
    config_text = '[remote "origin"]\n\tpushurl = git@github.com:owner/push.git\n'
    assert parse_github_slug_from_git_config(config_text) == ""


def test_resolve_slug_from_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    _run(["git", "remote", "add", "origin", "git@github.com:owner/repo.git"], repo)
    assert resolve_github_slug(repo) == "owner/repo"


def test_resolve_slug_from_worktree_pointer(tmp_path: Path) -> None:
    """Worktree 的 .git 是指针文件，应透过 gitdir 找到真实 config。"""
    main_repo = tmp_path / "main"
    main_repo.mkdir(parents=True)
    _init_repo(main_repo)
    _run(["git", "remote", "add", "origin", "git@github.com:owner/repo.git"], main_repo)

    worktree = tmp_path / "wt"
    _run(["git", "worktree", "add", "-b", "wt-branch", str(worktree)], main_repo)

    assert (worktree / ".git").is_file()
    assert resolve_github_slug(worktree) == "owner/repo"


def test_resolve_slug_missing_repo_returns_empty(tmp_path: Path) -> None:
    assert resolve_github_slug(tmp_path / "not-a-repo") == ""


# ---------------------------------------------------------------------------
# append_prd_github_link
# ---------------------------------------------------------------------------


def _repo_with_github_remote(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    _run(["git", "remote", "add", "origin", "git@github.com:owner/repo.git"], repo)
    return repo


def test_append_link_adds_suffix_after_anchor(tmp_path: Path) -> None:
    repo = _repo_with_github_remote(tmp_path)
    body = f"# Title\n\n{_ANCHOR}\n\n## Section\n"

    linked = append_prd_github_link(body, repo_path=repo)

    expected_suffix = (
        "（[在 GitHub 打开](https://github.com/owner/repo/blob/HEAD/" "tasks/pending/example.md)）"
    )
    assert f"{_ANCHOR}{expected_suffix}" in linked
    # 锚点本体保持原样，extract_prd_path 仍能解析。
    assert "- PRD path: `tasks/pending/example.md`" in linked


def test_append_link_is_idempotent(tmp_path: Path) -> None:
    repo = _repo_with_github_remote(tmp_path)
    body = f"{_ANCHOR}（[在 GitHub 打开](https://github.com/owner/repo/blob/HEAD/x)）\n"

    linked = append_prd_github_link(body, repo_path=repo)

    assert linked == body


def test_append_link_without_github_remote_returns_body_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    body = f"{_ANCHOR}\n"

    assert append_prd_github_link(body, repo_path=repo) == body


def test_append_link_ignores_path_without_directory_separator(tmp_path: Path) -> None:
    repo = _repo_with_github_remote(tmp_path)
    body = "- PRD path: `README.md`\n"

    assert append_prd_github_link(body, repo_path=repo) == body


def test_append_link_ignores_body_without_anchor(tmp_path: Path) -> None:
    repo = _repo_with_github_remote(tmp_path)
    body = "## Requirement\n\nNo PRD here.\n"

    assert append_prd_github_link(body, repo_path=repo) == body


def test_append_link_supports_anchor_without_list_dash(tmp_path: Path) -> None:
    repo = _repo_with_github_remote(tmp_path)
    body = "PRD path: `tasks/pending/example.md`\n"

    linked = append_prd_github_link(body, repo_path=repo)

    assert "https://github.com/owner/repo/blob/HEAD/tasks/pending/example.md" in linked


# ---------------------------------------------------------------------------
# create_issue_from_prd 接线
# ---------------------------------------------------------------------------


def _request(repo_path: Path, prd_path: Path) -> IssueFromPrdRequest:
    return IssueFromPrdRequest(repo_path=repo_path, prd_path=prd_path, issue_type="feature")


def test_create_issue_from_prd_body_contains_github_link(tmp_path: Path) -> None:
    """端到端：带 GitHub remote 的仓库建 Issue，正文锚点自动带链接。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _run(["git", "remote", "add", "origin", "git@github.com:owner/repo.git"], repo)
    fake_client = FakeGitHubClient()

    prd = repo / "tasks" / "20260516-120000-prd-example.md"
    prd.parent.mkdir()
    prd.write_text(
        "# PRD: Example\n\n## Acceptance Checklist\n\n- [x] One\n- [ ] Two\n",
        encoding="utf-8",
    )

    create_issue_from_prd(
        request=_request(repo, Path("tasks/20260516-120000-prd-example.md")),
        github_client=fake_client,
    )

    create_calls = [c for c in fake_client.calls if c["method"] == "create_issue"]
    body = create_calls[0]["body"]
    assert "- PRD path: `tasks/20260516-120000-prd-example.md`" in body
    assert "https://github.com/owner/repo/blob/HEAD/tasks/20260516-120000-prd-example.md" in body


def test_update_issue_body_with_prd_path_appends_link(tmp_path: Path) -> None:
    """rework 流程重写锚点行时也应补上 GitHub 链接。"""
    from backend.core.use_cases.create_prd_from_issue import _update_issue_body_with_prd_path

    repo = _repo_with_github_remote(tmp_path)

    updated = _update_issue_body_with_prd_path("old body", "tasks/pending/new.md", repo_path=repo)

    assert "- PRD path: `tasks/pending/new.md`（[在 GitHub 打开]" in updated

    # 不传 repo_path 时保持纯函数行为（既有单测契约）。
    pure = _update_issue_body_with_prd_path("old body", "tasks/pending/new.md")
    assert pure == "- PRD path: `tasks/pending/new.md`\n\nold body"
