#!/usr/bin/env python3
"""从当前 Agent 准备的上下文交付 Issue/PR，并按仓库规则收尾。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ai_pr_records import (
    DeliveryContext,
    DeliveryJournal,
    build_observations,
    render_iteration,
    render_overview,
    update_pr_body,
)

_REPO_FIELDS = (
    "nameWithOwner,url,defaultBranchRef,viewerPermission,"
    "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed"
)
_PR_FIELDS = (
    "number,url,body,state,baseRefName,headRefName,headRefOid,isCrossRepository,"
    "isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,closingIssuesReferences,mergeCommit"
)


class DeliveryError(RuntimeError):
    """交付停止，保留当前工作分支与现场。"""


def _check_outcome(pull_request: dict[str, Any]) -> str:
    checks = pull_request.get("statusCheckRollup")
    if not isinstance(checks, list):
        return "unknown"
    outcome = "passed"
    for check_status in checks:
        if check_status.get("__typename") == "CheckRun":
            if check_status.get("status") != "COMPLETED":
                outcome = "pending"
            elif check_status.get("conclusion") not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                return "failed"
        elif check_status.get("__typename") == "StatusContext":
            if check_status.get("state") == "PENDING":
                outcome = "pending"
            elif check_status.get("state") != "SUCCESS":
                return "failed"
        else:
            return "unknown"
    return outcome


def can_merge(pull_request: dict[str, Any], expected_head: str) -> bool:
    """合并前同时检查已验证提交、审查与当前检查状态。

    Args:
        pull_request: gh pr view 的实时 JSON。
        expected_head: 本次已验证并推送的提交。

    Returns:
        是否可以尝试不带管理员绕过的普通合并。
    """
    if (
        pull_request.get("state") != "OPEN"
        or pull_request.get("isDraft") is not False
        or pull_request.get("headRefOid") != expected_head
        or pull_request.get("mergeable") != "MERGEABLE"
        or pull_request.get("mergeStateStatus") != "CLEAN"
        or pull_request.get("reviewDecision") not in {"", "APPROVED"}
    ):
        return False
    return _check_outcome(pull_request) == "passed"


def _remote_identity(remote_url: str) -> tuple[str, str]:
    if "://" not in remote_url:
        remote_url = re.sub(r"^[^@]+@([^:]+):", r"https://\1/", remote_url)
    parsed_url = urlsplit(remote_url)
    return parsed_url.hostname or "", parsed_url.path.strip("/").removesuffix(".git")


class PullRequestDelivery:
    """执行确定性的 Git/GitHub 操作；语义整理由当前会话 Agent 负责。"""

    def __init__(self, arguments: argparse.Namespace) -> None:
        """准备交付输入，不执行仓库写操作。

        Args:
            arguments: 上下文路径、收尾策略和等待预算。
        """
        self.arguments = arguments
        self.context = DeliveryContext.read(Path(arguments.context))
        self.root = Path(self._run(["git", "rev-parse", "--show-toplevel"]))
        self.repository = json.loads(self._run(["gh", "repo", "view", "--json", _REPO_FIELDS]))
        self.repo_name = self.repository["nameWithOwner"]
        self.default_branch = self.repository["defaultBranchRef"]["name"]
        self.journal: DeliveryJournal | None = None

    def _run(self, command: list[str], *, stream: bool = False) -> str:
        completed = subprocess.run(
            command,
            cwd=getattr(self, "root", None),
            text=True,
            encoding="utf-8",
            capture_output=not stream,
            timeout=None if stream else 120,
            check=False,
        )
        if completed.returncode:
            raise DeliveryError(
                f"Command failed ({completed.returncode}): {' '.join(command[:3])}\n"
                f"{completed.stderr or ''}"
            )
        return (completed.stdout or "").removesuffix("\n")

    def _git(self, *arguments: str) -> str:
        return self._run(["git", *arguments])

    def _gh(self, *arguments: str) -> str:
        return self._run(["gh", *arguments, "--repo", self.repo_name])

    def _view_pr(self, pr_number: int) -> dict[str, Any]:
        return json.loads(self._gh("pr", "view", str(pr_number), "--json", _PR_FIELDS))

    def _publish_body(self, arguments: list[str], body: str) -> str:
        with tempfile.TemporaryDirectory(prefix="ai-pr-") as directory:
            body_path = Path(directory) / "body.md"
            body_path.write_text(body, encoding="utf-8")
            return self._gh(*arguments, "--body-file", str(body_path))

    def _changed_paths(self) -> set[str]:
        changed_paths: set[str] = set()
        for arguments in (
            ("diff", "--name-only", "--no-renames", "-z"),
            ("diff", "--cached", "--name-only", "--no-renames", "-z"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ):
            changed_paths.update(filter(None, self._git(*arguments).split("\0")))
        return changed_paths

    def _validate_file_scope(self) -> set[str]:
        changed_paths = self._changed_paths()
        unrelated_paths = changed_paths - set(self.context.payload["files"])
        if unrelated_paths:
            raise DeliveryError(f"Unselected changes remain; isolate them first: {unrelated_paths}")
        if self._git("diff", "--name-only", "--diff-filter=U"):
            raise DeliveryError("Resolve Git conflicts before creating a PR.")
        return changed_paths

    def _select_remote(self) -> str:
        matching_remotes = [
            remote_name
            for remote_name in self._git("remote").splitlines()
            if _remote_identity(self._git("config", "--get", f"remote.{remote_name}.url"))
            == _remote_identity(self.repository["url"])
        ]
        if len(matching_remotes) != 1:
            raise DeliveryError("Expected one Git remote matching the selected GitHub repository.")
        push_urls = self._git(
            "remote", "get-url", "--push", "--all", matching_remotes[0]
        ).splitlines()
        # 原始 pushurl 配置与有效推送地址数量分别核对，避免写入其他仓库。
        explicit_push_url = subprocess.run(
            ["git", "config", "--get-all", f"remote.{matching_remotes[0]}.pushurl"],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        if len(push_urls) != 1 or (
            explicit_push_url.returncode == 0
            and any(
                _remote_identity(push_url) != _remote_identity(self.repository["url"])
                for push_url in explicit_push_url.stdout.splitlines()
            )
        ):
            raise DeliveryError(
                "Push remote differs from the selected repository or has multiple URLs."
            )
        return matching_remotes[0]

    def _prepare_branch(self, remote_name: str) -> str:
        current_branch = self._git("branch", "--show-current")
        if not current_branch:
            raise DeliveryError("Detached HEAD is unsupported; select a work branch first.")
        self._git("fetch", remote_name, self.default_branch)
        base_ref = f"refs/remotes/{remote_name}/{self.default_branch}"
        if (
            not self._validate_file_scope()
            and self._git("rev-list", "--count", f"{base_ref}..HEAD") == "0"
        ):
            raise DeliveryError("No changes or branch commits to deliver.")
        if current_branch == self.default_branch:
            current_branch = f"ai/pr-{uuid.uuid4().hex[:12]}"
            self._git("switch", "-c", current_branch)
        common_dir = Path(self._git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        self.journal = DeliveryJournal(common_dir, current_branch)
        if self.journal.state.get("repository", self.repo_name) != self.repo_name:
            raise DeliveryError("This branch already belongs to another delivery repository.")
        self.journal.save(
            repository=self.repo_name,
            branch=current_branch,
            base_sha=self.journal.state.get("base_sha")
            or self._git("merge-base", "HEAD", base_ref),
        )
        return current_branch

    def _find_pr(self, branch_name: str) -> dict[str, Any] | None:
        candidates = json.loads(
            self._gh(
                "pr",
                "list",
                "--head",
                branch_name,
                "--state",
                "open",
                "--limit",
                "2",
                "--json",
                "number",
            )
        )
        if len(candidates) > 1:
            raise DeliveryError(
                "Multiple open PRs use this branch; select the intended PR manually."
            )
        if not candidates:
            return None
        pull_request = self._view_pr(candidates[0]["number"])
        if pull_request["baseRefName"] != self.default_branch or pull_request["isCrossRepository"]:
            raise DeliveryError(
                "Existing PR has a different base or fork head; leaving it unchanged."
            )
        return pull_request

    def _ensure_issue(self, pull_request: dict[str, Any] | None) -> int:
        assert self.journal is not None
        linked_issues = (pull_request or {}).get("closingIssuesReferences", [])
        issue_number = self.context.payload.get("issue") or self.journal.state.get("issue")
        if not issue_number and len(linked_issues) == 1:
            issue_repo_url = linked_issues[0].get("url", "").rsplit("/issues/", 1)[0]
            if _remote_identity(issue_repo_url) != _remote_identity(self.repository["url"]):
                raise DeliveryError(
                    "Linked issue is not in this repository; specify context.issue."
                )
            issue_number = linked_issues[0]["number"]
        if not issue_number and linked_issues:
            raise DeliveryError("Specify context.issue when a PR closes multiple issues.")
        if issue_number:
            issue = json.loads(
                self._gh("issue", "view", str(issue_number), "--json", "number,state")
            )
            if issue["state"] == "CLOSED":
                self._gh("issue", "reopen", str(issue_number))
        else:
            issue_url = self._publish_body(
                ["issue", "create", "--title", self.context.payload["title"]],
                self.context.issue_body(self.journal.state["base_sha"]),
            )
            issue_number = int(issue_url.rstrip("/").rsplit("/", 1)[1])
        self.journal.save(issue=issue_number)
        return issue_number

    def _commit(self) -> str:
        self._run(["just", "test"], stream=True)
        changed_paths = self._validate_file_scope()
        if changed_paths:
            self._git("--literal-pathspecs", "add", "--", *sorted(changed_paths))
            self._git("commit", "-m", self.context.payload["commit_message"])
        if self._changed_paths():
            raise DeliveryError(
                "Working tree changed during validation/commit; review it before pushing."
            )
        return self._git("rev-parse", "HEAD")

    def _observations(self, pull_request: dict[str, Any], head_sha: str) -> dict[str, Any]:
        ci_status = _check_outcome(pull_request)
        if pull_request.get("headRefOid") != head_sha:
            ci_status = "unknown"
        elif pull_request.get("statusCheckRollup") == []:
            ci_status = "not_observed"
        return build_observations(self.context, ci_status)

    def _publish_observation(self, pull_request: dict[str, Any], facts: dict[str, Any]) -> dict:
        assert self.journal is not None
        observations = self._observations(pull_request, facts["head_sha"])
        event = self.journal.append("delivery_observed", **facts, observations=observations)
        pr_number = pull_request["number"]
        self._publish_body(
            ["pr", "edit", str(pr_number)],
            update_pr_body(pull_request["body"], render_overview(event)),
        )
        comment_body = render_iteration(event)
        self._publish_body(["issue", "comment", str(facts["issue"])], comment_body)
        self._publish_body(["pr", "comment", str(pr_number)], comment_body)
        return observations

    def deliver(self) -> None:
        """创建或更新同一任务的交付，失败时留下恢复状态。

        Raises:
            DeliveryError: Git、GitHub 或交付前置条件失败。
        """
        self._validate_file_scope()
        remote_name = self._select_remote()
        branch_name = self._prepare_branch(remote_name)
        assert self.journal is not None
        pull_request = self._find_pr(branch_name)
        self.journal.append(
            "started", context=self.context.payload, head_sha=self._git("rev-parse", "HEAD")
        )
        if pull_request and not self.context.validation_passed and not pull_request["isDraft"]:
            self._gh("pr", "ready", str(pull_request["number"]), "--undo")
        issue_number = self._ensure_issue(pull_request)
        head_sha = self._commit()
        facts = {
            "repository": self.repo_name,
            "branch": branch_name,
            "issue": issue_number,
            "base_sha": self.journal.state["base_sha"],
            "previous_head_sha": self.journal.state.get("head_sha", self.journal.state["base_sha"]),
            "head_sha": head_sha,
            "context": self.context.payload,
        }
        self.journal.append("local_validated", **facts)
        self._git("push", "--set-upstream", remote_name, f"HEAD:refs/heads/{branch_name}")
        if not pull_request:
            create_arguments = [
                "pr",
                "create",
                "--base",
                self.default_branch,
                "--head",
                branch_name,
                "--title",
                self.context.payload["title"],
            ]
            if not self.context.validation_passed:
                create_arguments.append("--draft")
            initial_event = {**facts, "observations": build_observations(self.context, "unknown")}
            pr_url = self._publish_body(
                create_arguments, update_pr_body("", render_overview(initial_event))
            )
            pull_request = self._view_pr(int(pr_url.rstrip("/").rsplit("/", 1)[1]))
        pr_number = pull_request["number"]
        self.journal.save(pr=pr_number, head_sha=head_sha)
        observations = self._publish_observation(self._view_pr(pr_number), facts)
        self.journal.append("published", pr=pr_number, issue=issue_number, head_sha=head_sha)
        print(
            f"PR: {pull_request['url']}\nIssue: #{issue_number}\nBranch: {branch_name}", flush=True
        )
        if self.context.validation_passed and pull_request["isDraft"]:
            self._gh("pr", "ready", str(pr_number))
        self._finish(pr_number, head_sha, remote_name)
        latest_pr = self._view_pr(pr_number)
        if self._observations(latest_pr, head_sha) != observations:
            self._publish_observation(
                latest_pr,
                {
                    **facts,
                    "previous_head_sha": head_sha,
                    "iteration_summary": "Updated GitHub CI observations; no new code commit.",
                },
            )

    def _finish(self, pr_number: int, head_sha: str, remote_name: str) -> None:
        if not self.context.validation_passed or self.arguments.policy == "only":
            print("PR retained for review/real validation; staying on the work branch.")
            return
        if self.repository.get("viewerPermission") not in {"WRITE", "MAINTAIN", "ADMIN"}:
            print("Merge permission unavailable; staying on the work branch.")
            return
        deadline = time.monotonic() + self.arguments.wait_seconds
        while True:
            pull_request = self._view_pr(pr_number)
            assert self.journal is not None
            self.journal.append(
                "merge_observed",
                pr=pr_number,
                head_sha=pull_request.get("headRefOid"),
                state=pull_request.get("state"),
                review=pull_request.get("reviewDecision"),
                merge_state=pull_request.get("mergeStateStatus"),
                checks=pull_request.get("statusCheckRollup"),
            )
            if pull_request["headRefOid"] != head_sha:
                raise DeliveryError(
                    "PR head changed after validation; refusing to merge another commit."
                )
            if pull_request["state"] == "MERGED":
                break
            if can_merge(pull_request, head_sha):
                if self.arguments.policy == "confirm":
                    print(
                        f"Ready to merge PR #{pr_number} at {head_sha} into {self.default_branch}."
                    )
                    if not sys.stdin.isatty() or input("Merge now? [y/N] ").lower() != "y":
                        print("Merge not confirmed; staying on the work branch.")
                        return
                merge_method = next(
                    (
                        method
                        for method, setting in (
                            ("merge", "mergeCommitAllowed"),
                            ("squash", "squashMergeAllowed"),
                            ("rebase", "rebaseMergeAllowed"),
                        )
                        if self.repository.get(setting) is True
                    ),
                    None,
                )
                if not merge_method:
                    raise DeliveryError("No supported merge method is enabled.")
                self._gh(
                    "pr",
                    "merge",
                    str(pr_number),
                    f"--{merge_method}",
                    "--match-head-commit",
                    head_sha,
                )
                pull_request = self._view_pr(pr_number)
                if pull_request["state"] != "MERGED":
                    print("Merge queued or not completed; staying on the work branch.")
                    return
                break
            if pull_request.get("reviewDecision") in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
                print("Review is required; staying on the work branch.")
                return
            check_outcome = _check_outcome(pull_request)
            if check_outcome in {"failed", "unknown"}:
                print(f"Checks are {check_outcome}; staying on the work branch.")
                return
            if (
                pull_request["state"] != "OPEN"
                or pull_request.get("mergeStateStatus") == "DIRTY"
                or (
                    pull_request.get("mergeStateStatus") == "BLOCKED" and check_outcome != "pending"
                )
            ):
                print(
                    f"Merge blocked ({pull_request.get('mergeStateStatus')}); "
                    "staying on the work branch."
                )
                return
            if time.monotonic() >= deadline:
                print(
                    "Merge readiness is unconfirmed or checks are pending/failed; "
                    "staying on the work branch."
                )
                return
            print(f"Waiting for checks/mergeability of PR #{pr_number}...", flush=True)
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        assert self.journal is not None
        self.journal.append(
            "merged",
            pr=pr_number,
            head_sha=head_sha,
            merge_commit_sha=(pull_request.get("mergeCommit") or {}).get("oid"),
        )
        if self._changed_paths() or self._git("rev-parse", "HEAD") != head_sha:
            raise DeliveryError(
                "PR merged, but local changes appeared; staying on the work branch."
            )
        self._git("fetch", remote_name, self.default_branch)
        base_ref = f"refs/remotes/{remote_name}/{self.default_branch}"
        local_default = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{self.default_branch}"],
            cwd=self.root,
        )
        if local_default.returncode == 0:
            ancestry = subprocess.run(
                ["git", "merge-base", "--is-ancestor", self.default_branch, base_ref], cwd=self.root
            )
            if ancestry.returncode:
                raise DeliveryError(
                    "PR merged; local default branch diverged. No reset was performed."
                )
            self._git("switch", self.default_branch)
            self._git("merge", "--ff-only", base_ref)
        else:
            self._git("switch", "--track", "-c", self.default_branch, base_ref)
        self.journal.append("returned_to_default", branch=self.default_branch)
        print(f"PR merged; returned to updated {self.default_branch}.")


def main() -> int:
    """解析 CLI 并将失败状态传播给 just。

    Returns:
        0 表示发布完成或明确停留待审；1 表示交付失败。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", help="UTF-8 JSON prepared by the current conversation agent")
    parser.add_argument("policy", nargs="?", default="auto", choices=("auto", "only", "confirm"))
    parser.add_argument("wait_seconds", nargs="?", type=int, default=180)
    arguments = parser.parse_args()
    if arguments.wait_seconds < 0:
        parser.error("wait_seconds must be non-negative")
    delivery: PullRequestDelivery | None = None
    try:
        delivery = PullRequestDelivery(arguments)
        delivery.deliver()
    except (DeliveryError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        if delivery and delivery.journal:
            delivery.journal.append("stopped", reason=str(error))
        print(
            f"Delivery stopped: {error}\nCurrent branch and changes were preserved.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
