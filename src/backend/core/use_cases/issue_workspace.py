"""以完整 Issue URL 隔离任务分支、工作树与续跑归属。"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.models.repository_paths import resolve_git_pointer_directory

_WORKSPACE_RECORD = Path(".agent-runner/issue-workspace.json")
_ISSUE_PATH = re.compile(r"/([\w.-]+)/([\w.-]+)/issues/([1-9]\d*)/?", re.ASCII)


@dataclass(frozen=True)
class IssueWorkspaceIdentity:
    """规范化的远端任务身份，与本机 registry ID 和 remote 别名无关。

    Attributes:
        repository_url: 规范化的远端仓库 URL。
        issue_number: 仓库内任务编号。
        namespace: 适用于分支和目录的仓库命名空间。
    """

    repository_url: str
    issue_number: int
    namespace: str

    @property
    def issue_url(self) -> str:
        """返回用于归属核对的完整 Issue URL。"""
        return f"{self.repository_url}/issues/{self.issue_number}"

    @property
    def branch(self) -> str:
        """返回适用于 Git 和工作树目录的稳定分支名。"""
        return f"kc/{self.namespace}/issue-{self.issue_number}"


def issue_workspace_identity(issue: IssueSummary) -> IssueWorkspaceIdentity | None:
    """解析完整远端身份；非 URL 的离线任务保留旧命名兼容。

    Args:
        issue: 带有远端 URL 的任务。

    Returns:
        可解析的仓库身份；离线占位 URL 返回 None。

    Raises:
        ValueError: URL 中的 Issue 编号与任务编号不一致，或路径不安全。
    """
    parsed_url = urlsplit(issue.url)
    path_match = _ISSUE_PATH.fullmatch(parsed_url.path)
    if not parsed_url.hostname or path_match is None:
        return None
    owner, repository, number_text = path_match.groups()
    if int(number_text) != issue.number or owner in {".", ".."} or repository in {".", ".."}:
        raise ValueError(f"Issue URL does not match task #{issue.number}: {issue.url}")
    host = parsed_url.netloc.lower()
    if (
        parsed_url.scheme not in {"https", "http"}
        or re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host) is None
    ):
        raise ValueError("Issue workspace URL must use a valid HTTP repository host.")
    if parsed_url.username or parsed_url.password or parsed_url.port is not None:
        raise ValueError("Issue workspace URL must not contain credentials or a port.")
    owner, repository = owner.lower(), repository.lower()
    # 点号编码避免 .lock / 隐藏路径；设备名编码首字符以兼容 Windows。
    segments = [segment.replace(".", "%2e") for segment in (owner, repository)]
    reserved_names = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{n}" for n in range(1, 10)),
        *(f"lpt{n}" for n in range(1, 10)),
    }
    safe_segments = [
        f"%{ord(segment[0]):02x}{segment[1:]}" if segment in reserved_names else segment
        for segment in segments
    ]
    namespace = "/".join(safe_segments)
    if host != "github.com":
        namespace = f"{host.replace('.', '%2e')}/{namespace}"
    return IssueWorkspaceIdentity(f"https://{host}/{owner}/{repository}", issue.number, namespace)


def issue_branch_name(issue: IssueSummary, *, issue_number: int | None = None) -> str:
    """返回任务或同仓上游任务的新分支名。

    Args:
        issue: 提供仓库身份的任务。
        issue_number: 同仓上游编号；省略时使用当前任务。

    Returns:
        按仓库隔离的分支名，离线任务使用兼容名称。
    """
    target_number = issue.number if issue_number is None else issue_number
    identity = issue_workspace_identity(issue)
    return (
        replace(identity, issue_number=target_number).branch
        if identity is not None
        else f"issue-{target_number}"
    )


def issue_branch_candidates(issue: IssueSummary) -> tuple[str, ...]:
    """返回当前任务的新旧远端分支候选，查询仍受 GitHub 仓库作用域约束。

    Args:
        issue: 提供完整远端身份的任务。

    Returns:
        去重且优先使用新命名的候选分支。
    """
    return tuple(dict.fromkeys((issue_branch_name(issue), f"issue-{issue.number}")))


def related_issue(issue: IssueSummary, issue_number: int) -> IssueSummary:
    """构造同仓上游任务身份，用于解析其已绑定的分支。

    Args:
        issue: 提供仓库身份的当前任务。
        issue_number: 同仓上游任务编号。

    Returns:
        更新了 URL、编号且清空正文的任务摘要。
    """
    identity = issue_workspace_identity(issue)
    return replace(
        issue,
        number=issue_number,
        url=f"{identity.repository_url}/issues/{issue_number}" if identity else "",
        body="",
    )


def local_issue_worktree_paths(repo_path: Path, issue_number: int) -> tuple[Path, ...]:
    """按仓库显式配置定位证据工作树，避免同号任务跨仓库误匹配。

    Args:
        repo_path: 主仓库路径。
        issue_number: 当前仓库的任务编号。

    Returns:
        已确认的工作树候选；归属冲突或配置错误时为空。
    """
    config_path = repo_path / ".kedacode.toml"
    if not config_path.is_file():
        return (repo_path / ".kc-worktrees" / f"issue-{issue_number}",)
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        repository_slug = config.get("agent_runner", {}).get("repository", {}).get("github_repo")
        if not repository_slug:
            return (repo_path / ".kc-worktrees" / f"issue-{issue_number}",)
        issue = IssueSummary(
            issue_number, "", f"https://github.com/{repository_slug}/issues/{issue_number}", "", ()
        )
        branch = resolve_issue_branch(repo_path, issue)
        worktree_path = repo_path / ".kc-worktrees" / branch
        validate_issue_workspace(worktree_path, issue)
        return (worktree_path,)
    except (OSError, ValueError, AttributeError):
        return ()


def _binding_path(repo_path: Path, identity: IssueWorkspaceIdentity) -> Path:
    git_path = repo_path / ".git"
    if git_path.is_file():
        git_directory = resolve_git_pointer_directory(git_path, prefix="gitdir:")
        if git_directory is None:
            raise ValueError(f"Invalid Git directory pointer: {git_path}")
        git_path = git_directory
        common_path = git_path / "commondir"
        if common_path.is_file():
            git_path = resolve_git_pointer_directory(common_path) or git_path
    identity_digest = hashlib.sha256(identity.issue_url.encode("utf-8")).hexdigest()
    return git_path / "kedacode/issue-workspaces" / f"{identity_digest}.json"


def _read_record(record_path: Path) -> dict[str, str] | None:
    if not record_path.exists():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read Issue workspace ownership: {record_path}") from exc
    if not isinstance(record, dict) or not all(isinstance(value, str) for value in record.values()):
        raise ValueError(f"Invalid Issue workspace ownership: {record_path}")
    return record


def validate_issue_workspace(
    worktree_path: Path, issue: IssueSummary, *, allow_unbound: bool = False
) -> None:
    """拒绝消费明确属于其他任务的工作树。

    Args:
        worktree_path: 即将读取、修复或发布的工作树。
        issue: 当前任务。
        allow_unbound: 仅新建成功时允许尚未写入归属记录。

    Returns:
        None。

    Raises:
        ValueError: 归属记录损坏，或记录的完整任务身份不匹配。
    """
    identity = issue_workspace_identity(issue)
    record = _read_record(worktree_path / _WORKSPACE_RECORD)
    if (
        identity is not None
        and record is None
        and not allow_unbound
        and not is_legacy_issue_workspace(worktree_path, issue)
    ):
        raise ValueError(
            f"Worktree {worktree_path} has no confirmed ownership for {identity.issue_url}."
        )
    if (
        record is not None
        and identity is not None
        and record.get("issue_url") != identity.issue_url
    ):
        raise ValueError(
            f"Worktree {worktree_path} belongs to {record.get('issue_url')}, not {identity.issue_url}."
        )


def resolve_issue_branch(
    repo_path: Path, issue: IssueSummary, *, worktree_path: Path | None = None
) -> str:
    """解析已绑定的续跑分支；旧工作树只有 PRD 明确指向当前任务时才复用。

    Args:
        repo_path: 主仓库或其工作树。
        issue: 当前任务。
        worktree_path: 自定义命令已定位的工作树；先核对其归属记录。

    Returns:
        已确认的旧分支，或按仓库隔离的新分支。

    Raises:
        ValueError: 已有归属记录不一致或损坏。
    """
    identity = issue_workspace_identity(issue)
    if identity is None:
        return issue_branch_name(issue)
    if worktree_path is not None:
        workspace_record = _read_record(worktree_path / _WORKSPACE_RECORD)
        if workspace_record is not None and workspace_record.get("issue_url") == identity.issue_url:
            workspace_branch = workspace_record.get("branch")
            if workspace_branch not in issue_branch_candidates(issue):
                raise ValueError("Workspace branch does not match its complete Issue identity.")
            return workspace_branch
        if is_legacy_issue_workspace(worktree_path, issue):
            return f"issue-{issue.number}"
    binding = _read_record(_binding_path(repo_path, identity))
    if binding is not None:
        if binding.get("issue_url") != identity.issue_url or binding.get("branch") not in {
            identity.branch,
            f"issue-{issue.number}",
        }:
            raise ValueError("Issue workspace binding does not match its remote identity.")
        return binding["branch"]
    legacy_branch = f"issue-{issue.number}"
    legacy_path = repo_path / ".kc-worktrees" / legacy_branch
    record = _read_record(legacy_path / _WORKSPACE_RECORD)
    if record is not None:
        if record.get("issue_url") == identity.issue_url:
            return legacy_branch
        return identity.branch
    return legacy_branch if is_legacy_issue_workspace(legacy_path, issue) else identity.branch


def is_legacy_issue_workspace(worktree_path: Path, issue: IssueSummary) -> bool:
    """仅当旧工作树的归属记录或目标 PRD 明确指向完整任务 URL 时允许接管。

    Args:
        worktree_path: 待核对的工作树。
        issue: 当前任务及其 PRD 锚点。

    Returns:
        是否已有指向完整远端任务身份的证据。
    """
    identity = issue_workspace_identity(issue)
    if identity is None:
        return False
    record = _read_record(worktree_path / _WORKSPACE_RECORD)
    if record is not None:
        return record.get("issue_url") == identity.issue_url
    # 只读取本 Issue 声明的 PRD，不在旧目录里搜索同号或任意历史链接。
    from backend.core.use_cases.agent_runner_feedback import extract_prd_path

    prd_relative_path = extract_prd_path(issue.body)
    if prd_relative_path is not None:
        relative_path = Path(prd_relative_path)
        if not relative_path.is_absolute() and ".." not in relative_path.parts:
            for prd_path in (
                worktree_path / relative_path,
                worktree_path / "tasks/archive" / relative_path.name,
            ):
                if prd_path.is_file() and re.search(
                    rf"^\s*- GitHub Issue:\s*{re.escape(identity.issue_url)}\s*$",
                    prd_path.read_text(encoding="utf-8"),
                    re.MULTILINE | re.IGNORECASE,
                ):
                    return True
    return False


def bind_issue_workspace(
    repo_path: Path, issue: IssueSummary, worktree_path: Path, *, branch: str
) -> None:
    """持久化工作树归属和续跑索引，不改写其他任务的历史。

    Args:
        repo_path: 主仓库路径。
        issue: 当前任务。
        worktree_path: 已确认的工作树路径。
        branch: 已确认的任务分支。

    Returns:
        None。

    Raises:
        ValueError: 已有工作树归属与当前任务不符。
    """
    identity = issue_workspace_identity(issue)
    if identity is None:
        return
    validate_issue_workspace(worktree_path, issue, allow_unbound=True)
    record_text = (
        json.dumps({"issue_url": identity.issue_url, "branch": branch}, ensure_ascii=False) + "\n"
    )
    for record_path in (worktree_path / _WORKSPACE_RECORD, _binding_path(repo_path, identity)):
        record_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = record_path.with_suffix(".tmp")
        temporary_path.write_text(record_text, encoding="utf-8")
        temporary_path.replace(record_path)
