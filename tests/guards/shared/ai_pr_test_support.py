"""PR 交付入口测试的 GitHub CLI 传输替身与真实 Git 仓库。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 字段依据 gh 2.92.0 的 repo/pr view --json 帮助，以及本仓库 PR #33 的只读响应。
# 替身只替网络：分支、提交、远端推送和默认分支更新都走真实 Git。
_GH_TRANSPORT = r"""
import json
import os
import subprocess
import sys
from pathlib import Path

state_path = Path(os.environ["AI_PR_TEST_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
arguments = sys.argv[1:]
state.setdefault("calls", []).append(arguments)
remote = os.environ["AI_PR_TEST_REMOTE"]

def option(name):
    return arguments[arguments.index(name) + 1]

def git(*args):
    return subprocess.check_output(["git", "--git-dir", remote, *args], text=True).strip()

def body():
    return Path(option("--body-file")).read_text(encoding="utf-8")

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

if arguments[:2] == ["repo", "view"]:
    response = {
        "nameWithOwner": "example/project", "url": "https://github.com/example/project",
        "defaultBranchRef": {"name": state.get("default_branch", "main")},
        "viewerPermission": state.get("permission", "WRITE"),
        "mergeCommitAllowed": True, "squashMergeAllowed": True, "rebaseMergeAllowed": True,
    }
elif arguments[:2] == ["issue", "create"]:
    issues = state.setdefault("issues", {})
    number = max(map(int, issues), default=0) + 1
    issues[str(number)] = {"number": number, "state": "OPEN", "body": body()}
    response = f"https://github.com/example/project/issues/{number}"
elif arguments[:2] == ["issue", "view"]:
    response = state["issues"][arguments[2]]
elif arguments[:2] == ["issue", "reopen"]:
    state["issues"][arguments[2]]["state"] = "OPEN"
    response = ""
elif arguments[:2] == ["pr", "list"]:
    response = [
        {"number": int(number)} for number, pr in state.get("prs", {}).items()
        if pr["state"] == "OPEN" and pr["headRefName"] == option("--head")
    ]
elif arguments[:2] == ["pr", "create"]:
    pull_requests = state.setdefault("prs", {})
    number = max(map(int, pull_requests), default=1) + 1
    pull_requests[str(number)] = {
        "number": number, "url": f"https://github.com/example/project/pull/{number}",
        "body": body(),
        "state": "OPEN", "headRefName": option("--head"), "baseRefName": option("--base"),
        "isCrossRepository": False, "isDraft": "--draft" in arguments,
        "closingIssuesReferences": [{
            "number": 1, "url": "https://github.com/example/project/issues/1"
        }],
    }
    response = f"https://github.com/example/project/pull/{number}"
elif arguments[:2] == ["pr", "view"]:
    response = dict(state["prs"][arguments[2]])
    state["pr_views"] = state.get("pr_views", 0) + 1
    checks_sequence = state.get("checks_by_view")
    checks = (
        checks_sequence[min(state["pr_views"] - 1, len(checks_sequence) - 1)]
        if checks_sequence else state.get("checks", [{
            "__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"
        }])
    )
    response.update({
        "headRefOid": state.get("head_override") or git("rev-parse", response["headRefName"]),
        "mergeable": "MERGEABLE", "mergeStateStatus": state.get("merge_state", "CLEAN"),
        "reviewDecision": state.get("review", ""),
        "statusCheckRollup": checks,
    })
elif arguments[:2] == ["pr", "edit"]:
    state["prs"][arguments[2]]["body"] = body()
    response = ""
elif arguments[:2] == ["pr", "ready"]:
    state["prs"][arguments[2]]["isDraft"] = "--undo" in arguments
    response = ""
elif arguments[1] == "comment":
    state.setdefault("comments", []).append({"kind": arguments[0], "body": body()})
    response = ""
elif arguments[:2] == ["pr", "merge"]:
    if state.get("merge_error"):
        save()
        sys.exit(1)
    if not state.get("queued"):
        pr = state["prs"][arguments[2]]
        head_sha = git("rev-parse", pr["headRefName"])
        assert option("--match-head-commit") == head_sha
        assert "--admin" not in arguments and "--auto" not in arguments
        base_sha = git("rev-parse", pr["baseRefName"])
        merge_sha = subprocess.check_output(
            ["git", "--git-dir", remote, "commit-tree", head_sha + "^{tree}",
             "-p", base_sha, "-p", head_sha], input="Merge PR\n", text=True,
        ).strip()
        git("update-ref", "refs/heads/" + pr["baseRefName"], merge_sha)
        pr["mergeCommit"] = {"oid": merge_sha}
        pr["state"] = "MERGED"
        if state.get("dirty_after_merge"):
            Path("unrelated.txt").write_text("new change", encoding="utf-8")
    response = ""
else:
    raise AssertionError(arguments)
save()
print(response if isinstance(response, str) else json.dumps(response))
"""


@dataclass
class DeliveryHarness:
    """隔离的工作仓库、裸远端和可配置的 GitHub 传输状态。"""

    root: Path
    remote: Path
    state_path: Path
    environment: dict[str, str]

    def git(self, *arguments: str) -> str:
        """在隔离工作区执行真实 Git。

        Args:
            *arguments: Git 参数。

        Returns:
            标准输出。
        """
        return subprocess.check_output(
            ["git", *arguments], cwd=self.root, env=self.environment, text=True
        ).strip()

    def state(self) -> dict:
        """读取网络替身的持久状态。

        Returns:
            已发布的 Issue、PR、评论与调用记录。
        """
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def configure(self, **settings: object) -> None:
        """调整测试网络响应，不改生产代码。

        Args:
            **settings: 待更新的场景状态。
        """
        self.state_path.write_text(json.dumps({**self.state(), **settings}), encoding="utf-8")

    def deliver(self, context: dict, policy: str = "auto") -> subprocess.CompletedProcess:
        """通过真实 just ai pr 入口运行交付。

        Args:
            context: 当前调用方提供的上下文。
            policy: 合并策略。

        Returns:
            命令的退出码与输出。
        """
        context_path = self.root / ".git" / "context.json"
        context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            ["just", "ai", "pr", str(context_path), policy, "0"],
            cwd=self.root,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=30,
        )


def make_harness(parent: Path) -> DeliveryHarness:
    """构造不访问真实 GitHub、不读取用户 Git hooks 的测试环境。

    Args:
        parent: pytest 独立临时目录。

    Returns:
        可重复调用的真实 Git 工作区。
    """
    root = parent / "repo"
    root.mkdir()
    remote = parent / "remote.git"
    home = parent / "home"
    home.mkdir()
    binary = parent / "bin"
    binary.mkdir()
    state_path = parent / "github.json"
    state_path.write_text("{}", encoding="utf-8")
    environment = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "AI_PR_TEST_REMOTE": str(remote),
        "AI_PR_TEST_STATE": str(state_path),
    }
    transport = binary / "gh"
    transport.write_text(f"#!{sys.executable}\n" + _GH_TRANSPORT, encoding="utf-8")
    transport.chmod(0o755)
    scripts = root / "scripts" / "shared" / "just"
    scripts.mkdir(parents=True)
    for filename in ("ai_pr.py", "ai_pr_records.py"):
        shutil.copy(_PROJECT_ROOT / "scripts" / "shared" / "just" / filename, scripts)
    shared_justfile = (_PROJECT_ROOT / "justfile.shared").read_text(encoding="utf-8")
    (root / "justfile").write_text(
        "set allow-duplicate-recipes\n" + shared_justfile + "\ntest:\n    @true\n",
        encoding="utf-8",
    )
    (root / "feature.txt").write_text("before\n", encoding="utf-8")
    harness = DeliveryHarness(root, remote, state_path, environment)
    subprocess.run(
        ["git", "init", "--bare", str(remote)], env=environment, check=True, capture_output=True
    )
    harness.git("init", "-b", "main")
    harness.git("config", "core.hooksPath", str(home / "no-hooks"))
    harness.git("add", ".")
    harness.git("commit", "-qm", "initial")
    harness.git("remote", "add", "origin", "https://github.com/example/project.git")
    harness.git(
        "config", f"url.{remote.as_uri()}.insteadOf", "https://github.com/example/project.git"
    )
    harness.git("push", "-qu", "origin", "main")
    (root / "feature.txt").write_text("after\n", encoding="utf-8")
    return harness


def delivery_context(status: str = "passed") -> dict:
    """提供标注来源的最小交付上下文。

    Args:
        status: 真实验收状态。

    Returns:
        符合生产上下文契约的输入。
    """
    return {
        "schema_version": 1,
        "title": "fix(cli): handle the requested behavior",
        "request_source": "conversation",
        "user_messages": ["请修复这个问题", "需要走真实入口验证"],
        "task": "AI 整理：修复指定行为，并通过真实入口验证。",
        "summary": "Changed the requested behavior.",
        "commit_message": "fix(cli): handle behavior",
        "files": ["feature.txt"],
        "validation": {
            "status": status,
            "details": "Real entry observation recorded by the caller.",
        },
    }
