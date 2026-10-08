"""为 Issue 正文的 ``PRD path:`` 锚点追加 GitHub blob 链接。

``- PRD path: `...` `` 是 runner 反向定位 PRD 的机器锚点（``extract_prd_path``），
解析正则要求反引号紧跟冒号，因此可点击的 GitHub 链接只能作为**后缀**追加在闭合
反引号之后，不能把反引号路径包进 Markdown 链接。

本模块从仓库 ``.git/config``（含 worktree 的 ``gitdir`` 指针）解析 GitHub
``owner/repo``，并把 ``（[在 GitHub 打开](blob-url)）`` 追加到锚点行尾；URL 使用
``/blob/HEAD/``，由 GitHub 解析到默认分支。全程只读本地文件、不执行子进程。

无 GitHub 远端、锚点行已带链接或路径不像仓库相对路径时一律原样返回，绝不阻断
Issue 创建。
"""

from __future__ import annotations

import re
from pathlib import Path

# 行首 PRD 锚点：前缀形态与 ``extract_prd_path`` 的解析正则一致（反引号紧跟冒号）。
# 第 1 组是锚点本体，第 2 组是反引号内路径，第 3 组是行内剩余文本（幂等判断用）。
_PRD_ANCHOR_LINE_RE = re.compile(r"(?m)^(\s*(?:[-*]\s+)?PRD path:\s*`([^`]+)`)(.*)$")

# 从 remote URL 提取 GitHub ``owner/repo``，覆盖 SSH/HTTPS/git 协议与 ``.git`` 后缀：
# git@github.com:owner/repo.git | ssh://git@github.com/owner/repo.git |
# https://github.com/owner/repo.git | git://github.com/owner/repo.git
_GITHUB_URL_SLUG_RE = re.compile(
    r"(?:git@github\.com:|ssh://git@github\.com/|https?://github\.com/|git://github\.com/)"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?\s*$"
)

# 追加到锚点行尾的链接文案（与手工修复 issue #247 时相同的格式）。
_LINK_SUFFIX_TEMPLATE = "（[在 GitHub 打开](https://github.com/{slug}/blob/HEAD/{path})）"


def parse_github_slug_from_url(url: str) -> str:
    """从单个 git remote URL 解析 GitHub ``owner/repo``。

    Args:
        url: remote URL 字符串（如 ``git@github.com:owner/repo.git``）。

    Returns:
        ``owner/repo``；URL 不指向 github.com 时返回空字符串。
    """
    match = _GITHUB_URL_SLUG_RE.search(url.strip())
    if not match:
        return ""
    return f"{match.group('owner')}/{match.group('repo')}"


def parse_github_slug_from_git_config(config_text: str) -> str:
    """从 ``.git/config`` 文本解析第一个 GitHub remote 的 ``owner/repo``。

    只把 ``[remote ...]`` 段内的 ``url = ...`` 行视为候选（``pushurl`` 与
    非 remote 段一律忽略）。

    Args:
        config_text: ``.git/config`` 的完整文本。

    Returns:
        ``owner/repo``；未找到 GitHub remote 时返回空字符串。
    """
    in_remote_section = False
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_remote_section = stripped.startswith("[remote")
            continue
        if in_remote_section and stripped.startswith("url"):
            slug = parse_github_slug_from_url(stripped.partition("=")[2])
            if slug:
                return slug
    return ""


def resolve_github_slug(repo_path: Path) -> str:
    """读取仓库 ``.git/config`` 并解析 GitHub ``owner/repo``。

    兼容 worktree（``.git`` 为指向真实 gitdir 的指针文件）。

    Args:
        repo_path: 仓库根目录（或 worktree 根目录）。

    Returns:
        ``owner/repo``；无法确定时返回空字符串。
    """
    git_path = repo_path / ".git"
    try:
        if git_path.is_file():
            pointer = git_path.read_text(encoding="utf-8")
            for line in pointer.splitlines():
                if line.strip().lower().startswith("gitdir:"):
                    gitdir = Path(line.partition(":")[2].strip())
                    if not gitdir:
                        continue
                    # worktree 的 gitdir 形如 <main>/.git/worktrees/<name>，
                    # 共享 config 位于主 gitdir（.git/config）。
                    if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                        gitdir = gitdir.parent.parent
                    return parse_github_slug_from_git_config(
                        (gitdir / "config").read_text(encoding="utf-8")
                    )
            return ""
        return parse_github_slug_from_git_config((git_path / "config").read_text(encoding="utf-8"))
    except OSError:
        return ""


def append_prd_github_link(body: str, *, repo_path: Path) -> str:
    """在 Issue 正文的 PRD 锚点行尾追加 GitHub blob 链接（幂等）。

    Args:
        body: 待处理的 Issue 正文 Markdown。
        repo_path: 仓库根目录，用于读取 ``.git/config``。

    Returns:
        处理后的正文；无法确定 GitHub 远端时原样返回。
    """
    if not body or "PRD path:" not in body:
        return body
    slug = resolve_github_slug(repo_path)
    if not slug:
        return body

    def _append(match: re.Match[str]) -> str:
        anchor, path, rest = match.group(1), match.group(2).strip(), match.group(3)
        # 行内已有 Markdown 链接时保持原样，保证幂等。
        if "](http" in rest:
            return match.group(0)
        # 与 extract_prd_path 同一校验：只链接看起来像仓库相对路径的锚点。
        if "/" not in path or any(char.isspace() for char in path):
            return match.group(0)
        return anchor + _LINK_SUFFIX_TEMPLATE.format(slug=slug, path=path)

    return _PRD_ANCHOR_LINE_RE.sub(_append, body)
