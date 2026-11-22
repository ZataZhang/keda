"""将评测生命周期里的 Issue 与 Draft PR 保存在进程内，无外部 GitHub 写入。"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import (
    IssueSummary,
    LabelConfig,
    PullRequestContext,
    PullRequestSummary,
)
from backend.core.shared.models.agent_spec import AgentSpec

ISSUE_NUMBER = 42
ISSUE_BRANCH = f"issue-{ISSUE_NUMBER}"
_LOCAL_ISSUE_URL = f"https://github.com/example/repo/issues/{ISSUE_NUMBER}"
_LOCAL_PR_URL = "https://github.com/example/repo/pull/1"


class LocalEvaluationGitHub(IGitHubClient):
    """单格独占的生命周期适配器；真正的 Git 发布走本地 bare remote。"""

    def __init__(self, *, title: str, prompt: str, ready_label: str, base_sha: str) -> None:
        """创建一条本地 ready Issue 和冻结的远端基线引用。"""
        self._issue = IssueSummary(ISSUE_NUMBER, title, _LOCAL_ISSUE_URL, prompt, (ready_label,))
        self._base_sha = base_sha
        self._comments: dict[int, str] = {}
        self._next_comment_id = 1
        self._pr_comments: list[str] = []
        self.draft: dict[str, str] | None = None

    def sync_labels(
        self, labels: LabelConfig, agent_registry: dict[str, AgentSpec] | None = None
    ) -> None:
        """评测标签无需在外部注册。"""

    def list_ready_issues(self, ready_label: str, limit: int) -> list[IssueSummary]:
        """只返回符合就绪标签的本地候选。"""
        return self.list_issues_by_label(ready_label, limit, state="open")

    def list_rework_prd_issues(self, rework_prd_label: str, limit: int) -> list[IssueSummary]:
        """返回符合返工标签的本地候选。"""
        return self.list_issues_by_label(rework_prd_label, limit, state="open")

    def list_issues_by_label(
        self, label: str | None, limit: int, state: str = "all"
    ) -> list[IssueSummary]:
        """按标签与状态查询本格的唯一 Issue。"""
        matches = (label is None or label in self._issue.labels) and state.lower() in (
            "all",
            self._issue.state.lower(),
        )
        return [self._issue] if limit > 0 and matches else []

    def get_issue(self, issue_number: int) -> IssueSummary:
        """读取当前 Issue；其他编号没有本地记录。"""
        if issue_number != self._issue.number:
            raise LookupError(f"评测 Issue #{issue_number} 不存在")
        return self._issue

    def edit_issue_labels(
        self, issue_number: int, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        """在本地应用标签增删。"""
        issue = self.get_issue(issue_number)
        new_labels = set(issue.labels).union(add).difference(remove)
        self._issue = replace(issue, labels=tuple(sorted(new_labels)))

    def comment_issue(self, issue_number: int, body: str) -> None:
        """增加一条本地 Issue 事件。"""
        self.get_issue(issue_number)
        self._comments[self._next_comment_id] = body
        self._next_comment_id += 1

    def edit_issue_body(self, issue_number: int, body: str) -> None:
        """更新已有的本地 Issue 正文。"""
        self._issue = replace(self.get_issue(issue_number), body=body)

    def presence_creation_lock(self) -> AbstractContextManager[None]:
        """单格单进程，无需跨进程 presence 锁。"""
        return nullcontext()

    def issue_supervision_lock(self, issue_number: int) -> AbstractContextManager[bool]:
        """本地夹具由当前进程独占。"""
        self.get_issue(issue_number)
        return nullcontext(True)

    def close_issue(self, issue_number: int) -> None:
        """关闭本地 Issue。"""
        self._issue = replace(self.get_issue(issue_number), state="CLOSED")

    def create_issue(self, *, title: str, body: str, labels: Sequence[str]) -> str:
        """禁止评测流程创建额外 Issue。"""
        raise RuntimeError("本地评测不创建额外 Issue")

    def create_draft_pr(self, *, title: str, body: str, base_branch: str, cwd: Path) -> str:
        """保存 Draft PR 内容以判断本地交付完成。"""
        self.draft = {
            "url": _LOCAL_PR_URL,
            "title": title,
            "body": body,
            "base_branch": base_branch,
        }
        return _LOCAL_PR_URL

    def list_review_candidate_issues(self, labels: Sequence[str], limit: int) -> list[IssueSummary]:
        """本地评测不运行发布后的监督队列。"""
        return []

    def get_pull_request_context(
        self, branch: str, *, require_success: bool = False
    ) -> PullRequestContext | None:
        """运行前没有既有 PR 上下文。"""
        return None

    def list_pull_requests_for_issue(
        self, repo: str, issue_number: int
    ) -> list[PullRequestSummary]:
        """评测不链接其他仓库的 PR。"""
        return []

    def list_issue_comments(self, issue_number: int, *, require_success: bool = False) -> list[str]:
        """按发布顺序读取本地 Issue 评论。"""
        self.get_issue(issue_number)
        return list(self._comments.values())

    def list_issue_comment_entries(
        self,
        issue_number: int,
        *,
        trusted_only: bool = False,
        body_contains: str | None = None,
        require_success: bool = False,
    ) -> list[tuple[int, str]]:
        """本地评论由本格 runner 创建，可按正文过滤。"""
        self.get_issue(issue_number)
        return [
            (comment_id, body)
            for comment_id, body in self._comments.items()
            if body_contains is None or body_contains in body
        ]

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        """更新已有本地评论。"""
        if comment_id not in self._comments:
            raise LookupError(f"评测评论 {comment_id} 不存在")
        self._comments[comment_id] = body

    def delete_issue_comment(self, comment_id: int) -> None:
        """删除本地评论，支持重复清理。"""
        self._comments.pop(comment_id, None)

    def comment_pr(self, pr_number: int, body: str) -> None:
        """添加本地 PR 评论。"""
        self._pr_comments.append(body)

    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        """更新本地 Draft PR 正文。"""
        if self.draft is None:
            raise LookupError("本格尚未创建本地 Draft PR")
        self.draft["body"] = body

    def merge_pull_request(self, pr_number: int, *, method: str = "squash") -> None:
        """禁止评测执行合并。"""
        raise RuntimeError("本地评测不执行 PR 合并")

    def list_pr_comments(self, pr_number: int) -> list[str]:
        """读取本地 PR 评论。"""
        return list(self._pr_comments)

    def find_open_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        """仅本格的分支可命中已经创建的草稿。"""
        return _LOCAL_PR_URL if branch == ISSUE_BRANCH and self.draft is not None else None

    def find_merged_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        """评测没有已合并的 PR。"""
        return None

    def get_remote_base_sha(self, remote: str, base_branch: str) -> str:
        """返回本地隔离远端的冻结基线。"""
        if (remote, base_branch) != ("origin", "main"):
            raise LookupError(f"未知评测基线 {remote}/{base_branch}")
        return self._base_sha
