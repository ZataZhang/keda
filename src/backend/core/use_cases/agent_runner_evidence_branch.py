"""从证据分支读取人审页与截图。

人审页的原始产物通常进不了主干：截图与视频被 gitignore，只发布在
iar-evidence/issue-<N> 这类无历史分支上。看板只读本地目录时看不到它们，本模块
提供按分支读取的兜底：一次 ls-remote 拿到全部证据分支并缓存，按需 shallow
fetch 后再从 git 对象里取文件。任何失败都按「没有」处理，不让看板报错。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError, run

from backend.core.use_cases.agent_runner_evidence_media import (
    PAGE_FILENAMES,
    EvidenceMedia,
)

#: 证据分支清单的缓存秒数：同一次看板浏览里不该重复打远端。
BRANCH_LIST_TTL_SECONDS = 600

#: 单条 git 命令的超时秒数；离线或凭据缺失时快速失败。
GIT_TIMEOUT_SECONDS = 8

_BRANCH_CACHE: dict[str, tuple[float, dict[int, str]]] = {}
_REMOTE_CACHE: dict[str, str] = {}


@dataclass(frozen=True)
class BranchPage:
    """证据分支上的人审页与同目录文件名。

    Attributes:
        branch: 分支名，例如 iar-evidence/issue-49。
        page_name: 人审页文件名。
        markdown: 页面正文。
        siblings: 同目录下的其余文件名，用于解析内联图片。
    """

    branch: str
    page_name: str
    markdown: str
    siblings: tuple[str, ...]


def _git(repo_path: Path, *args: str) -> str | None:
    """执行一条只读 git 命令；失败或超时返回 None。"""
    try:
        completed = run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (CalledProcessError, OSError):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _github_remote(repo_path: Path) -> str | None:
    """取仓库里指向 github.com 的远端名；结果进程内缓存。"""
    key = str(repo_path)
    if key in _REMOTE_CACHE:
        return _REMOTE_CACHE[key] or None
    output = _git(repo_path, "remote", "-v")
    if output is None:
        _REMOTE_CACHE[key] = ""
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "github.com" in parts[1]:
            _REMOTE_CACHE[key] = parts[0]
            return parts[0]
    _REMOTE_CACHE[key] = ""
    return None


def list_evidence_branches(repo_path: Path) -> dict[int, str]:
    """返回该仓库的证据分支映射：Issue 编号 → 分支名。

    只认名字里带 evidence 且以 issue-<N> 结尾的分支；结果缓存一分钟级的
    十分之一小时，看板连续刷新时不会重复打远端。

    Args:
        repo_path: 仓库根目录。

    Returns:
        dict[int, str]: Issue 编号到分支名的映射；读不到时为空字典。
    """
    key = str(repo_path)
    cached = _BRANCH_CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < BRANCH_LIST_TTL_SECONDS:
        return cached[1]
    remote = _github_remote(repo_path)
    if remote is None:
        return {}
    output = _git(repo_path, "ls-remote", "--heads", remote)
    branches: dict[int, str] = {}
    if output is not None:
        for line in output.splitlines():
            _, _, ref = line.partition("\t")
            if not ref.startswith("refs/heads/") or "evidence" not in ref:
                continue
            name = ref[len("refs/heads/") :]
            suffix = name.rsplit("issue-", 1)
            if len(suffix) != 2 or not suffix[1].isdigit():
                continue
            branches[int(suffix[1])] = name
    _BRANCH_CACHE[key] = (time.monotonic(), branches)
    return branches


def _fetch_branch(repo_path: Path, branch: str) -> str | None:
    """浅拉取一个证据分支，返回可直接引用的 ref 名。"""
    remote = _github_remote(repo_path)
    if remote is None:
        return None
    if _git(repo_path, "fetch", "--depth=1", remote, branch) is None:
        return None
    return "FETCH_HEAD"


def _declared_page(siblings: list[str], manifest_text: str | None) -> str | None:
    """按证据清单的声明取人审页文件名；没有声明时返回 None。"""
    if not manifest_text:
        return None
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError:
        return None
    declared = manifest.get("review_page") if isinstance(manifest, dict) else None
    if isinstance(declared, str) and declared in siblings:
        return declared
    return None


def read_branch_page(repo_path: Path, issue_number: int) -> BranchPage | None:
    """读取该 Issue 证据分支上的人审页。

    Args:
        repo_path: 仓库根目录。
        issue_number: Issue 编号。

    Returns:
        BranchPage | None: 找到页面时返回页面与同目录文件；否则 None。
    """
    branch = list_evidence_branches(repo_path).get(issue_number)
    if branch is None:
        return None
    ref = _fetch_branch(repo_path, branch)
    if ref is None:
        return None
    listing = _git(repo_path, "ls-tree", "--name-only", ref)
    if listing is None:
        return None
    siblings = [name for name in listing.splitlines() if name]
    manifest_text = (
        _git(repo_path, "show", f"{ref}:evidence.json") if "evidence.json" in siblings else None
    )
    page_name = _declared_page(siblings, manifest_text)
    if page_name is None:
        page_name = next((name for name in PAGE_FILENAMES if name in siblings), None)
    if page_name is None:
        return None
    markdown = _git(repo_path, "show", f"{ref}:{page_name}")
    if markdown is None:
        return None
    return BranchPage(
        branch=branch,
        page_name=page_name,
        markdown=markdown,
        siblings=tuple(siblings),
    )


def read_branch_file(repo_path: Path, issue_number: int, name: str) -> bytes | None:
    """读取证据分支上与人审页同目录的文件。

    Args:
        repo_path: 仓库根目录。
        issue_number: Issue 编号。
        name: 文件名，必须是纯文件名。

    Returns:
        bytes | None: 文件内容；不在该分支或读取失败时为 None。
    """
    if Path(name).name != name:
        return None
    page = read_branch_page(repo_path, issue_number)
    if page is None or name not in page.siblings:
        return None
    try:
        completed = run(
            ["git", "-C", str(repo_path), "show", f"FETCH_HEAD:{name}"],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (CalledProcessError, OSError):
        return None
    return completed.stdout


def find_branch_media(repo_path: Path, issue_number: int) -> EvidenceMedia | None:
    """把证据分支上的页面投影成统一的 EvidenceMedia。

    Args:
        repo_path: 仓库根目录。
        issue_number: Issue 编号。

    Returns:
        EvidenceMedia | None: 带 source="branch" 的证据；没有页面时为 None。
    """
    page = read_branch_page(repo_path, issue_number)
    if page is None:
        return None
    return EvidenceMedia(page_ref=page.page_name, source=page.branch)


__all__ = [
    "BranchPage",
    "find_branch_media",
    "list_evidence_branches",
    "read_branch_file",
    "read_branch_page",
]
