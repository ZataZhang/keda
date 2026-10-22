"""从 console.db、git 与证据目录聚合出以 Issue 为主键的数据集样本。

只读：SQLite 以 ``mode=ro`` 打开，git 只调用 ``rev-list`` / ``merge-base`` 等
查询命令，``gh`` 仅在显式传入 ``--with-gh`` 时调用且只执行 ``view`` / ``list``。

每个样本是一行 JSON，字段含义见 ``docs/guides/dataset-export.md``。历史数据
拿不到 Issue 原文时 ``input_provenance`` 标为 ``title_only`` 或 ``missing``，
不会伪装成原文。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.use_cases import agent_runner_issue_snapshot as issue_snapshot
from scripts.dataset_export import agent_sessions

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".kedacode" / "console.db"
# 测试仓库与演示仓库的运行记录不是真实任务，默认剔除。
DEFAULT_EXCLUDE_REPO_PATTERNS = ("rv*", "process-runner-test", "harness")
DEFAULT_BASE_BRANCHES = ("origin/main", "main", "origin/master", "master")
COMPLETED_OUTCOME = "completed"
SUCCESS_FAILURE_TYPE = "success"
MERGED_PR_STATE = "MERGED"
RESOLVED_BASIS_PR_MERGED = "pr_merged"
RESOLVED_BASIS_RUN_COMPLETED = "run_completed"
PR_JSON_FIELDS = (
    "number,state,mergeCommit,headRefName,headRefOid,additions,deletions,changedFiles,commits"
)
# 自动巡检生成的 Issue 不是真实开发任务，不适合作为评测题。
# 非 keda 来源：除了判定"已合并"的字段外，还要 PR 描述、关联 Issue、作者与合并时间。
PR_DISCOVERY_FIELDS = (
    f"{PR_JSON_FIELDS},title,body,baseRefName,mergedAt,closingIssuesReferences,author"
)
# 一次列出很多 PR 时带 commits/author 会超过 GitHub GraphQL 的节点上限，所以先列轻量字段筛选，
# 再逐个取详情。
PR_LIST_FIELDS = "number,headRefName,author"
DEFAULT_PR_LIMIT = 100
ORIGIN_KEDA_RUN = "keda_run"
ORIGIN_GITHUB_PR = "github_pr"
ORIGIN_AGENT_SESSION = "agent_session"
GITHUB_REMOTE_PATTERN = re.compile(r"github\.com(?::\d+)?[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$")
GITHUB_URL_REPO_PATTERN = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/(?:issues|pull)/\d+")
# 输入来源太弱、不能当评测题的来源：缺失，或只有 PR 描述（它描述的是"改了什么"，等于泄露答案）。
# 会话首句是对话式的（"你看一下这个"），不是任务规格，同样只能留给后续整理。
WEAK_INPUT_PROVENANCES = frozenset({"missing", "pr_description", "session_first_prompt"})
ISSUE_BRANCH_PATTERN = re.compile(r"^issue-(\d+)$")
PATROL_TITLE_PATTERN = re.compile("巡检|巡查")
# Conventional Commits 的 docs / chore 类型：归档 PRD、同步模板这类维护动作，不是开发任务。
MAINTENANCE_TITLE_PATTERN = re.compile(r"^(docs|chore)(\([^)]*\))?!?:")
# (档位, 文件数上限, 增删行数上限)；超出最后一档记为 large。
SIZE_BANDS = (("small", 5, 300), ("medium", 30, 3000))
LARGE_SIZE_BAND = "large"
GIT_TIMEOUT_SECONDS = 30
GH_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ExportOptions:
    """一次导出的输入选项。"""

    db_path: Path
    output_path: Path | None
    repo_ids: tuple[str, ...] = ()
    exclude_repo_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_REPO_PATTERNS
    use_gh: bool = False
    home_path: Path | None = None
    github_pr_repo_paths: tuple[Path, ...] = ()
    pr_limit: int = DEFAULT_PR_LIMIT
    agent_session_repo_paths: tuple[Path, ...] = ()
    sessions_root: Path | None = None


@dataclass
class IssueGroup:
    """同一 (repo_id, issue_number) 下的全部数据库记录。"""

    repo_id: str
    issue_number: int
    runs: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    invocations: list[dict[str, Any]] = field(default_factory=list)
    alias_repo_ids: list[str] = field(default_factory=list)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """以只读模式打开 SQLite，库不存在时直接报错而不是新建。"""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def is_excluded_repo(*, repo_id: str, patterns: Iterable[str]) -> bool:
    """判断 repo_id 是否命中任一排除通配符。"""
    return any(fnmatch.fnmatch(repo_id, pattern) for pattern in patterns)


def load_issue_groups(
    connection: sqlite3.Connection, *, options: ExportOptions
) -> list[IssueGroup]:
    """读取 run / attempt / invocation 三类记录并按 Issue 聚合。"""
    groups: dict[tuple[str, int], IssueGroup] = {}

    def group_for(repo_id: str, issue_number: int) -> IssueGroup | None:
        if options.repo_ids and repo_id not in options.repo_ids:
            return None
        if is_excluded_repo(repo_id=repo_id, patterns=options.exclude_repo_patterns):
            return None
        key = (repo_id, issue_number)
        if key not in groups:
            groups[key] = IssueGroup(repo_id=repo_id, issue_number=issue_number)
        return groups[key]

    for row in connection.execute("SELECT * FROM run_records ORDER BY started_at, id"):
        target = group_for(row["repo_id"], row["issue_number"])
        if target is not None:
            target.runs.append(dict(row))
    for row in connection.execute("SELECT * FROM attempt_records ORDER BY started_at, id"):
        target = group_for(row["repo_id"], row["issue_number"])
        if target is not None:
            target.attempts.append(dict(row))
    for row in connection.execute(
        "SELECT repo_id, issue_number, phase, role, agent, detail_json "
        "FROM agent_invocation_events WHERE event_type = 'invocation_started' "
        "AND issue_number IS NOT NULL ORDER BY occurred_at, id"
    ):
        target = group_for(row["repo_id"], row["issue_number"])
        if target is not None:
            detail = json.loads(row["detail_json"] or "{}")
            target.invocations.append(
                {
                    "phase": row["phase"],
                    "role": row["role"],
                    "agent": row["agent"],
                    "requested_model": detail.get("requested_model"),
                }
            )
    return sorted(groups.values(), key=lambda g: (g.repo_id, g.issue_number))


def run_command(*, args: list[str], cwd: Path, timeout: int) -> str | None:
    """执行只读外部命令，失败或超时返回 ``None``。"""
    try:
        completed = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def run_git(*, repo_path: Path, args: list[str]) -> str | None:
    """在仓库内执行只读 git 子命令。"""
    return run_command(args=["git", *args], cwd=repo_path, timeout=GIT_TIMEOUT_SECONDS)


def find_issue_branch(*, repo_path: Path, issue_number: int) -> str | None:
    """找到名为 ``issue-N``（允许带远程或用户前缀）的分支，优先本地分支。"""
    refs = run_git(
        repo_path=repo_path,
        args=["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
    )
    pattern = re.compile(rf"(^|/)issue-{issue_number}$")
    for ref_name in (refs or "").splitlines():
        if pattern.search(ref_name):
            return ref_name
    return None


def find_base_branch(*, repo_path: Path) -> str | None:
    """返回第一个存在的默认分支引用。"""
    for candidate in DEFAULT_BASE_BRANCHES:
        if run_git(repo_path=repo_path, args=["rev-parse", "--verify", "--quiet", candidate]):
            return candidate
    return None


def collect_branch_commits(*, repo_path: Path, issue_number: int) -> dict[str, Any] | None:
    """用 Issue 分支相对默认分支的 merge-base 还原 base_sha 与有序提交。"""
    branch_name = find_issue_branch(repo_path=repo_path, issue_number=issue_number)
    base_branch = find_base_branch(repo_path=repo_path)
    if branch_name is None or base_branch is None:
        return None
    base_sha = run_git(repo_path=repo_path, args=["merge-base", base_branch, branch_name])
    final_sha = run_git(repo_path=repo_path, args=["rev-parse", branch_name])
    if not base_sha or not final_sha:
        return None
    listing = run_git(
        repo_path=repo_path,
        args=["log", "--reverse", "--format=%H%x09%s", f"{base_sha}..{final_sha}"],
    )
    commits = [
        {"sha": sha, "subject": subject}
        for sha, _, subject in (line.partition("\t") for line in (listing or "").splitlines())
    ]
    return {
        "source": "issue_branch",
        "branch": branch_name,
        "base_sha": base_sha,
        "final_sha": final_sha,
        "commits": commits,
    }


def pick_pull_request(pull_requests: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """多个 PR 同属一个 Issue 分支时，优先取已合并的最新一个，否则取编号最大的。"""
    if not pull_requests:
        return None
    merged = [pr for pr in pull_requests if pr["state"] == MERGED_PR_STATE]
    return max(merged or pull_requests, key=lambda pr: pr["number"])


def collect_pull_request_commits(
    *, repo_path: Path, pull_request: dict[str, Any]
) -> dict[str, Any]:
    """以 PR 为准还原提交链：本地分支可能落后于 PR 后续推送的提交。"""
    commits = [
        {"sha": commit["oid"], "subject": commit["messageHeadline"]}
        for commit in pull_request["commits"]
    ]
    first_sha = commits[0]["sha"] if commits else None
    base_sha = (
        run_git(repo_path=repo_path, args=["rev-parse", "--verify", "--quiet", f"{first_sha}^"])
        if first_sha
        else None
    )
    return {
        "source": "pull_request",
        "branch": pull_request["headRefName"],
        "base_sha": base_sha,
        "final_sha": pull_request["headRefOid"],
        "commits": commits,
    }


def count_merge_commits(*, repo_path: Path, code: dict[str, Any] | None) -> int | None:
    """统计 base..final 之间的 merge commit 数；对象不在本地时返回 ``None``。"""
    if not code or not code.get("base_sha") or not code.get("final_sha"):
        return None
    listing = run_git(
        repo_path=repo_path,
        args=["rev-list", "--merges", f"{code['base_sha']}..{code['final_sha']}"],
    )
    return None if listing is None else len(listing.split())


def measure_local_diff(*, repo_path: Path, code: dict[str, Any] | None) -> dict[str, int] | None:
    """用 ``git diff --numstat`` 统计改动规模；二进制文件只计文件数。"""
    if not code or not code.get("base_sha") or not code.get("final_sha"):
        return None
    numstat = run_git(
        repo_path=repo_path, args=["diff", "--numstat", code["base_sha"], code["final_sha"]]
    )
    if numstat is None:
        return None
    rows = [line.split("\t") for line in numstat.splitlines()]
    return {
        "files": len(rows),
        "additions": sum(int(row[0]) for row in rows if row[0].isdigit()),
        "deletions": sum(int(row[1]) for row in rows if row[1].isdigit()),
    }


def classify_size_band(size: dict[str, int] | None) -> str | None:
    """按文件数与增删行数把改动规模分档，规模未知返回 ``None``。"""
    if size is None:
        return None
    changed_lines = size["additions"] + size["deletions"]
    for band_name, max_files, max_lines in SIZE_BANDS:
        if size["files"] <= max_files and changed_lines <= max_lines:
            return band_name
    return LARGE_SIZE_BAND


def classify_task_kind(title: str | None) -> str:
    """巡检类标为 ``patrol``，docs/chore 类提交标题标为 ``maintenance``，其余为 ``development``。"""
    if title and PATROL_TITLE_PATTERN.search(title):
        return "patrol"
    if title and MAINTENANCE_TITLE_PATTERN.match(title):
        return "maintenance"
    return "development"


def fetch_gh_json(*, repo_path: Path, args: list[str]) -> Any | None:
    """调用 ``gh`` 并解析 JSON，失败返回 ``None``。"""
    stdout = run_command(args=["gh", *args], cwd=repo_path, timeout=GH_TIMEOUT_SECONDS)
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def collect_gh_facts(*, repo_path: Path, issue_number: int) -> dict[str, Any]:
    """通过 ``gh`` 取 Issue 原文与关联 PR；任一项取不到则对应键为 ``None``。"""
    issue = fetch_gh_json(
        repo_path=repo_path,
        args=["issue", "view", str(issue_number), "--json", "title,body,url,createdAt"],
    )
    pull_requests = fetch_gh_json(
        repo_path=repo_path,
        args=[
            "pr",
            "list",
            "--state",
            "all",
            "--head",
            f"issue-{issue_number}",
            "--json",
            PR_JSON_FIELDS,
        ],
    )
    return {"issue": issue, "pull_requests": pull_requests}


def summarize_evidence(*, repo_path: Path, issue_number: int) -> dict[str, Any]:
    """读取 ``tasks/evidence/issue-N/evidence.json`` 的条目数与负控制覆盖。"""
    evidence_path = repo_path / "tasks" / "evidence" / f"issue-{issue_number}" / "evidence.json"
    if not evidence_path.is_file():
        return {"present": False, "item_count": 0, "items_with_negative_control": 0}
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"present": False, "item_count": 0, "items_with_negative_control": 0}
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return {
        "present": True,
        "item_count": len(items),
        "items_with_negative_control": sum(1 for item in items if item.get("negative_control")),
    }


def has_failed_then_succeeded(attempts: list[dict[str, Any]]) -> bool:
    """是否存在"先失败、后成功"的 attempt 序列，可构成偏好对。"""
    seen_failure = False
    for attempt in attempts:
        if attempt["failure_type"] != SUCCESS_FAILURE_TYPE:
            seen_failure = True
        elif seen_failure:
            return True
    return False


def load_issue_snapshots(group: IssueGroup, *, options: ExportOptions) -> list[dict[str, Any]]:
    """读回某 Issue 的全部认领时快照（含别名 repo_id），按认领时间升序。"""
    records: list[dict[str, Any]] = []
    for repo_id in (group.repo_id, *group.alias_repo_ids):
        snapshot_path = issue_snapshot.snapshot_file_path(
            repo_id=repo_id, issue_number=group.issue_number, home_path=options.home_path
        )
        records.extend(issue_snapshot.read_issue_snapshots(snapshot_path))
    return sorted(records, key=lambda record: record.get("captured_at", ""))


def classify_input(
    *,
    gh_issue: dict[str, Any] | None,
    runs: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> str:
    """判定任务输入的可信度：认领时快照 > 事后抓取的原文 > 仅标题 > 缺失。"""
    if snapshots:
        return "snapshot_at_claim"
    if gh_issue and gh_issue.get("body"):
        return "verbatim_issue_body"
    if any(run.get("issue_title") for run in runs):
        return "title_only"
    return "missing"


def is_eval_ready(*, labels: dict[str, Any], input_provenance: str) -> bool:
    """能否作为评测题：PR 已合并、有提交、输入可信（非缺失、非仅 PR 描述）、真实开发任务、规模适中，且已核实无 merge commit。

    ``merge_commit_count`` 为 ``None``（提交对象不在本地）表示未核实，不算通过。
    """
    return bool(
        labels["resolved"]
        and labels["resolved_basis"] == RESOLVED_BASIS_PR_MERGED
        and labels["has_commits"]
        and input_provenance not in WEAK_INPUT_PROVENANCES
        and labels["task_kind"] == "development"
        and labels["size_band"] in {name for name, _, _ in SIZE_BANDS}
        and labels["merge_commit_count"] == 0
    )


def is_edited_since_claim(
    snapshots: list[dict[str, Any]], *, gh_issue: dict[str, Any] | None
) -> bool | None:
    """认领后正文是否被改过；没有快照时无从判断，返回 ``None``。

    最新已知正文优先取 GitHub 现状（``--with-gh``），否则取最后一条快照。
    """
    if not snapshots:
        return None
    latest_known_sha256 = (
        hashlib.sha256(str(gh_issue["body"]).encode("utf-8")).hexdigest()
        if gh_issue and gh_issue.get("body") is not None
        else snapshots[-1]["body_sha256"]
    )
    return latest_known_sha256 != snapshots[0]["body_sha256"]


def build_sample(
    group: IssueGroup, *, repo_path: Path | None, options: ExportOptions
) -> dict[str, Any]:
    """把一个 Issue 的全部事实拼成一条样本。"""
    gh_facts = (
        collect_gh_facts(repo_path=repo_path, issue_number=group.issue_number)
        if options.use_gh and repo_path
        else {"issue": None, "pull_requests": None}
    )
    snapshots = load_issue_snapshots(group, options=options)
    earliest_snapshot = snapshots[0] if snapshots else None
    pull_request = pick_pull_request(gh_facts["pull_requests"])
    if pull_request and repo_path:
        git_facts = collect_pull_request_commits(repo_path=repo_path, pull_request=pull_request)
    elif repo_path:
        git_facts = collect_branch_commits(repo_path=repo_path, issue_number=group.issue_number)
    else:
        git_facts = None
    if git_facts is not None:
        # 认领时记录的分叉点优先于事后推断：它是 Agent 实际开始工作的起点。
        has_snapshot_base = bool(earliest_snapshot and earliest_snapshot.get("base_sha"))
        git_facts = {
            **git_facts,
            "base_sha": earliest_snapshot["base_sha"]
            if has_snapshot_base
            else git_facts["base_sha"],
            "base_sha_source": "snapshot" if has_snapshot_base else "inferred",
        }
    size = (
        {
            "files": pull_request["changedFiles"],
            "additions": pull_request["additions"],
            "deletions": pull_request["deletions"],
        }
        if pull_request
        else (measure_local_diff(repo_path=repo_path, code=git_facts) if repo_path else None)
    )
    merge_commit_count = (
        count_merge_commits(repo_path=repo_path, code=git_facts) if repo_path else None
    )
    evidence = (
        summarize_evidence(repo_path=repo_path, issue_number=group.issue_number)
        if repo_path
        else {"present": False, "item_count": 0, "items_with_negative_control": 0}
    )
    gh_issue = gh_facts["issue"]
    if pull_request:
        resolved = pull_request["state"] == MERGED_PR_STATE
        resolved_basis = RESOLVED_BASIS_PR_MERGED
    else:
        resolved = any(run["outcome"] == COMPLETED_OUTCOME for run in group.runs)
        resolved_basis = RESOLVED_BASIS_RUN_COMPLETED
    input_provenance = classify_input(gh_issue=gh_issue, runs=group.runs, snapshots=snapshots)
    input_source = earliest_snapshot or gh_issue or {}
    issue_title = input_source.get("title") or next(
        (run["issue_title"] for run in group.runs if run.get("issue_title")), None
    )
    labels = {
        "resolved": resolved,
        "resolved_basis": resolved_basis,
        "task_kind": classify_task_kind(issue_title),
        "size_band": classify_size_band(size),
        "merge_commit_count": merge_commit_count,
        "has_commits": bool(git_facts and git_facts["commits"]),
        "has_evidence": evidence["present"],
        "has_preference_pair": has_failed_then_succeeded(group.attempts),
    }
    labels["eval_ready"] = is_eval_ready(labels=labels, input_provenance=input_provenance)
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": f"{group.repo_id}#issue-{group.issue_number}",
        "origin": ORIGIN_KEDA_RUN,
        "repo_id": group.repo_id,
        "alias_repo_ids": group.alias_repo_ids,
        "issue_number": group.issue_number,
        "input": {
            "provenance": input_provenance,
            "title": issue_title,
            "body": input_source.get("body"),
            "url": input_source.get("url")
            or next((r["issue_url"] for r in group.runs if r.get("issue_url")), None),
            "edited_since_claim": is_edited_since_claim(snapshots, gh_issue=gh_issue),
        },
        "code": git_facts,
        "pull_requests": gh_facts["pull_requests"],
        "runs": [
            {
                key: run[key]
                for key in (
                    "trigger",
                    "agent",
                    "outcome",
                    "error_summary",
                    "started_at",
                    "duration_seconds",
                )
            }
            for run in group.runs
        ],
        "attempts": [
            {
                key: attempt.get(key)
                for key in (
                    "attempt_number",
                    "agent",
                    "failure_type",
                    "recovered",
                    "failure_phase",
                    "preset",
                    "model",
                    "duration_seconds",
                )
            }
            for attempt in group.attempts
        ],
        "invocations": group.invocations,
        "evidence": evidence,
        "size": size,
        "labels": labels,
    }


def latest_repo_path(group: IssueGroup) -> Path | None:
    """取最近一次运行记录的仓库路径，路径已不存在则视为不可用。"""
    for run in reversed(group.runs):
        candidate = Path(run["repo_path"])
        if candidate.is_dir():
            return candidate
    return None


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, int]:
    """统计各质量条件的样本数，供导出后快速判断可用量。"""
    counts: dict[str, int] = defaultdict(int)
    for sample in samples:
        labels = sample["labels"]
        counts["total"] += 1
        counts["resolved"] += labels["resolved"]
        counts["has_commits"] += labels["has_commits"]
        counts["has_evidence"] += labels["has_evidence"]
        counts["has_preference_pair"] += labels["has_preference_pair"]
        counts["input_verbatim"] += sample["input"]["provenance"] == "verbatim_issue_body"
        counts["input_title_only"] += sample["input"]["provenance"] == "title_only"
        counts["input_snapshot_at_claim"] += sample["input"]["provenance"] == "snapshot_at_claim"
        counts["edited_since_claim"] += bool(sample["input"]["edited_since_claim"])
        counts["resolved_by_merged_pr"] += (
            labels["resolved"] and labels["resolved_basis"] == RESOLVED_BASIS_PR_MERGED
        )
        counts["origin_github_pr"] += sample["origin"] == ORIGIN_GITHUB_PR
        counts["origin_agent_session"] += sample["origin"] == ORIGIN_AGENT_SESSION
        counts["input_pr_description"] += sample["input"]["provenance"] == "pr_description"
        counts["patrol_issue"] += labels["task_kind"] == "patrol"
        counts["maintenance_task"] += labels["task_kind"] == "maintenance"
        counts["unverified_history"] += labels["merge_commit_count"] is None
        counts["eval_ready"] += labels["eval_ready"]
    return dict(counts)


def merge_groups_by_repo_path(groups: list[IssueGroup]) -> list[IssueGroup]:
    """合并指向同一仓库路径、同一 Issue 编号的分组，避免同一任务被导出多次。

    同一仓库可能以多个 ``repo_id`` 注册；保留运行记录最多的那个 ``repo_id`` 作为样本主键，
    其余 ``repo_id`` 记在 ``alias_repo_ids`` 中。仓库路径不可用的分组不参与合并。
    """
    merged: dict[tuple[str, int], list[IssueGroup]] = defaultdict(list)
    standalone: list[IssueGroup] = []
    for group in groups:
        repo_path = latest_repo_path(group)
        if repo_path is None:
            standalone.append(group)
        else:
            merged[(str(repo_path.resolve()), group.issue_number)].append(group)
    result = list(standalone)
    for members in merged.values():
        primary = max(members, key=lambda member: len(member.runs))
        for other in members:
            if other is primary:
                continue
            primary.runs.extend(other.runs)
            primary.attempts.extend(other.attempts)
            primary.invocations.extend(other.invocations)
            primary.alias_repo_ids.append(other.repo_id)
        primary.runs.sort(key=lambda run: run["started_at"])
        primary.attempts.sort(key=lambda attempt: attempt["started_at"])
        result.append(primary)
    return sorted(result, key=lambda group: (group.repo_id, group.issue_number))


def discover_merged_pull_requests(*, repo_path: Path, limit: int) -> list[dict[str, Any]] | None:
    """列出仓库最近已合并的 PR（只含筛选用的轻量字段）；``gh`` 不可用时返回 ``None``。"""
    pull_requests = fetch_gh_json(
        repo_path=repo_path,
        args=[
            "pr", "list", "--state", "merged", "--limit", str(limit),
            "--json", PR_LIST_FIELDS,
        ],
    )  # fmt: skip
    return pull_requests if isinstance(pull_requests, list) else None


def fetch_pull_request_detail(*, repo_path: Path, number: int) -> dict[str, Any] | None:
    """取单个 PR 的完整字段（提交链、规模、关联 Issue 等）；失败返回 ``None``。"""
    detail = fetch_gh_json(
        repo_path=repo_path,
        args=["pr", "view", str(number), "--json", PR_DISCOVERY_FIELDS],
    )
    return detail if isinstance(detail, dict) else None


def is_covered_by_keda(pull_request: dict[str, Any], *, covered_issue_numbers: set[int]) -> bool:
    """PR 是否已作为 keda 运行样本导出（head 分支为 ``issue-N`` 且该 Issue 已有 keda 样本）。"""
    matched = ISSUE_BRANCH_PATTERN.match(pull_request["headRefName"])
    return bool(matched and int(matched.group(1)) in covered_issue_numbers)


def resolve_pr_input(
    pull_request: dict[str, Any], *, repo_path: Path
) -> tuple[str, dict[str, Any], int | None]:
    """取非 keda 样本的任务输入，返回 ``(provenance, 输入来源, 关联 Issue 编号)``。

    有关联 Issue 且有正文时用 Issue 正文（事后抓取的现状，与 keda 路径的
    ``verbatim_issue_body`` 同义）；否则退到 PR 标题与描述并标 ``pr_description``——
    那是对"改了什么"的描述，不是任务本身，也不能当评测题。
    """
    for reference in pull_request.get("closingIssuesReferences") or []:
        issue = fetch_gh_json(
            repo_path=repo_path,
            args=["issue", "view", str(reference["number"]), "--json", "title,body,url"],
        )
        if issue and issue.get("body"):
            return "verbatim_issue_body", issue, int(reference["number"])
    return "pr_description", pull_request, None


def build_pull_request_sample(
    pull_request: dict[str, Any], *, repo_path: Path, repo_slug: str
) -> dict[str, Any]:
    """把一个与 keda 无关的已合并 PR 还原成样本；没有尝试与轨迹，只有可核实的事实。"""
    provenance, input_source, issue_number = resolve_pr_input(pull_request, repo_path=repo_path)
    code = collect_pull_request_commits(repo_path=repo_path, pull_request=pull_request)
    size = {
        "files": pull_request["changedFiles"],
        "additions": pull_request["additions"],
        "deletions": pull_request["deletions"],
    }
    labels = {
        "resolved": True,
        "resolved_basis": RESOLVED_BASIS_PR_MERGED,
        "task_kind": classify_task_kind(input_source.get("title")),
        "size_band": classify_size_band(size),
        "merge_commit_count": count_merge_commits(repo_path=repo_path, code=code),
        "has_commits": bool(code["commits"]),
        "has_evidence": False,
        "has_preference_pair": False,
    }
    labels["eval_ready"] = is_eval_ready(labels=labels, input_provenance=provenance)
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": f"{repo_slug}#pr-{pull_request['number']}",
        "origin": ORIGIN_GITHUB_PR,
        "repo_id": repo_slug,
        "alias_repo_ids": [],
        "issue_number": issue_number,
        "input": {
            "provenance": provenance,
            "title": input_source.get("title"),
            "body": input_source.get("body"),
            "url": input_source.get("url"),
            "edited_since_claim": None,
        },
        "code": {**code, "base_sha_source": "inferred"},
        "pull_requests": [
            {key: pull_request.get(key) for key in ("number", "state", "mergeCommit", "mergedAt")}
        ],
        "runs": [],
        "attempts": [],
        "invocations": [],
        "evidence": {"present": False, "item_count": 0, "items_with_negative_control": 0},
        "size": size,
        "labels": labels,
    }


def export_github_pr_samples(
    options: ExportOptions, *, covered_issues_by_path: dict[Path, set[int]]
) -> list[dict[str, Any]]:
    """为 ``--github-pr-repo`` 指定的本地仓库导出非 keda 的已合并 PR 样本。

    已作为 keda 样本导出的 PR、机器人账号的 PR 都会跳过；``gh`` 不可用时该仓库整体跳过。
    """
    samples: list[dict[str, Any]] = []
    for repo_path in options.github_pr_repo_paths:
        repo_info = fetch_gh_json(
            repo_path=repo_path, args=["repo", "view", "--json", "nameWithOwner"]
        )
        pull_requests = discover_merged_pull_requests(repo_path=repo_path, limit=options.pr_limit)
        if not repo_info or pull_requests is None:
            print(f"跳过 {repo_path}: gh 不可用或无法识别 GitHub 仓库", file=sys.stderr)
            continue
        covered_issue_numbers = covered_issues_by_path.get(repo_path.resolve(), set())
        for pull_request in pull_requests:
            if (pull_request.get("author") or {}).get("is_bot"):
                continue
            if is_covered_by_keda(pull_request, covered_issue_numbers=covered_issue_numbers):
                continue
            detail = fetch_pull_request_detail(repo_path=repo_path, number=pull_request["number"])
            if detail is None:
                print(f"跳过 PR #{pull_request['number']}: 取不到详情", file=sys.stderr)
                continue
            samples.append(
                build_pull_request_sample(
                    detail, repo_path=repo_path, repo_slug=repo_info["nameWithOwner"]
                )
            )
    return samples


def resolve_repo_slug(repo_path: Path) -> str:
    """从 git 远程地址解析 ``owner/repo``（不联网）；解析不出时退回 ``local:<目录名>``。"""
    remote_name = (run_git(repo_path=repo_path, args=["remote"]) or "").splitlines()
    remote_url = (
        run_git(repo_path=repo_path, args=["remote", "get-url", remote_name[0]])
        if remote_name
        else None
    )
    matched = GITHUB_REMOTE_PATTERN.search(remote_url or "")
    return matched.group(1) if matched else f"local:{repo_path.name}"


def resolve_session_commits(
    parsed: agent_sessions.ParsedSession, *, repo_path: Path
) -> dict[str, Any] | None:
    """把会话里记录的提交哈希对照本地 git：能解析的按会话内顺序组成提交链，全都解析不出返回 ``None``。

    重写历史（rebase、amend）后的旧哈希会解析失败，这类提交直接丢弃而不是猜。
    """
    resolved_shas = [
        resolved
        for sha in parsed.commit_shas
        if (
            resolved := run_git(
                repo_path=repo_path, args=["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"]
            )
        )
    ]
    if not resolved_shas:
        return None
    return {
        "source": "session_transcript",
        "branch": parsed.git_branch,
        "base_sha": run_git(
            repo_path=repo_path, args=["rev-parse", "--verify", "--quiet", f"{resolved_shas[0]}^"]
        ),
        "final_sha": resolved_shas[-1],
        "commits": [
            {
                "sha": sha,
                "subject": run_git(repo_path=repo_path, args=["log", "-1", "--format=%s", sha])
                or "",
            }
            for sha in resolved_shas
        ],
        "base_sha_source": "inferred",
    }


def build_session_sample(
    parsed: agent_sessions.ParsedSession, *, repo_path: Path, repo_slug: str
) -> dict[str, Any] | None:
    """把一个真人会话还原成样本；会话没有可对照的提交时不构成样本，返回 ``None``。"""
    code = resolve_session_commits(parsed, repo_path=repo_path)
    if code is None:
        return None
    size = measure_local_diff(repo_path=repo_path, code=code)
    default_branch = find_base_branch(repo_path=repo_path)
    on_default_branch = bool(
        default_branch
        and run_git(
            repo_path=repo_path,
            args=["merge-base", "--is-ancestor", code["final_sha"], default_branch],
        )
        is not None
    )
    labels = {
        "resolved": on_default_branch,
        "resolved_basis": "on_default_branch",
        "task_kind": classify_task_kind(None),
        "size_band": classify_size_band(size),
        "merge_commit_count": count_merge_commits(repo_path=repo_path, code=code),
        "has_commits": True,
        "has_evidence": False,
        "has_preference_pair": False,
    }
    labels["eval_ready"] = is_eval_ready(labels=labels, input_provenance="session_first_prompt")
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": f"{repo_slug}#session-{parsed.session_id[:8]}",
        "origin": ORIGIN_AGENT_SESSION,
        "repo_id": repo_slug,
        "alias_repo_ids": [],
        "issue_number": None,
        "input": {
            "provenance": "session_first_prompt",
            "title": None,
            "url": None,
            "edited_since_claim": None,
            **agent_sessions.redact_first_prompt(parsed.first_prompt),
        },
        "code": code,
        "pull_requests": [],
        "runs": [],
        "attempts": [],
        "invocations": [],
        "evidence": {"present": False, "item_count": 0, "items_with_negative_control": 0},
        "size": size,
        "session": {
            "id": parsed.session_id,
            "entrypoint": parsed.entrypoint,
            "started_at": parsed.started_at,
            "ended_at": parsed.ended_at,
            "human_turns": parsed.human_turns,
            "models": parsed.models,
            "git_branch": parsed.git_branch,
            "linked_prs": parsed.pr_links,
        },
        "labels": labels,
    }


def export_agent_session_samples(options: ExportOptions) -> list[dict[str, Any]]:
    """为 ``--agent-sessions-repo`` 指定的仓库导出真人 Claude Code 会话样本。"""
    samples: list[dict[str, Any]] = []
    for repo_path in options.agent_session_repo_paths:
        project_dir = agent_sessions.claude_project_dir(
            repo_path=repo_path, projects_root=options.sessions_root
        )
        repo_slug = resolve_repo_slug(repo_path)
        session_files = sorted(project_dir.glob("*.jsonl"))
        human_sessions = [
            parse_result
            for path in session_files
            if (parse_result := agent_sessions.parse_session_file(path))
        ]
        built = [
            sample
            for parsed in human_sessions
            if (sample := build_session_sample(parsed, repo_path=repo_path, repo_slug=repo_slug))
        ]
        print(
            f"{repo_path}: 会话文件 {len(session_files)}，真人会话 {len(human_sessions)}，"
            f"有可对照提交 {len(built)}",
            file=sys.stderr,
        )
        samples.extend(built)
    return samples


def _sample_repo_slug(sample: dict[str, Any]) -> str | None:
    """样本所属 GitHub 仓库的 ``owner/repo``；keda 运行样本从 Issue 链接里取，取不到为 ``None``。"""
    if sample["origin"] != ORIGIN_KEDA_RUN:
        return sample["repo_id"]
    matched = GITHUB_URL_REPO_PATTERN.search(sample["input"].get("url") or "")
    return matched.group(1) if matched else None


def link_sessions_to_pull_requests(samples: list[dict[str, Any]]) -> None:
    """用会话里的 ``pr-link`` 把同一份工作的不同视角互相链接。

    会话是原始首句，PR 样本和 keda 运行样本是可核实的事实；链上之后可以把"当时说了什么"和
    "最后做成了什么"放在一起看。keda 运行样本只有带 ``--with-gh`` 时才有 PR 列表可供匹配。
    """
    targets: dict[tuple[str, int], dict[str, Any]] = {}
    for sample in samples:
        slug = _sample_repo_slug(sample)
        if sample["origin"] == ORIGIN_AGENT_SESSION or slug is None:
            continue
        for pull_request in sample["pull_requests"] or []:
            targets[(slug, pull_request["number"])] = sample
    for sample in samples:
        if sample["origin"] != ORIGIN_AGENT_SESSION:
            continue
        linked_sample_ids: list[str] = []
        for link in sample["session"]["linked_prs"]:
            target = targets.get((sample["repo_id"], link["number"]))
            if target is not None and target["sample_id"] not in linked_sample_ids:
                target.setdefault("linked_session_ids", []).append(sample["session"]["id"])
                linked_sample_ids.append(target["sample_id"])
        sample["session"]["linked_sample_ids"] = linked_sample_ids


def export_samples(options: ExportOptions) -> list[dict[str, Any]]:
    """执行一次完整导出并返回样本列表。"""
    groups: list[IssueGroup] = []
    if options.db_path.is_file():
        connection = open_readonly(options.db_path)
        try:
            groups = merge_groups_by_repo_path(load_issue_groups(connection, options=options))
        finally:
            connection.close()
    samples = [
        build_sample(group, repo_path=latest_repo_path(group), options=options) for group in groups
    ]
    covered_issues_by_path: dict[Path, set[int]] = defaultdict(set)
    for group in groups:
        group_repo_path = latest_repo_path(group)
        if group_repo_path is not None:
            covered_issues_by_path[group_repo_path.resolve()].add(group.issue_number)
    samples += export_github_pr_samples(options, covered_issues_by_path=covered_issues_by_path)
    samples += export_agent_session_samples(options)
    link_sessions_to_pull_requests(samples)
    return samples


def parse_args(argv: list[str] | None) -> ExportOptions:
    """解析命令行为 :class:`ExportOptions`。"""
    parser = argparse.ArgumentParser(description="导出 KedaCode 数据集样本（只读）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="console.db 路径")
    parser.add_argument("--output", type=Path, help="JSONL 输出路径，缺省写 stdout")
    parser.add_argument("--repo", action="append", default=[], help="只导出该 repo_id，可重复")
    parser.add_argument(
        "--exclude-repo",
        action="append",
        default=None,
        help="排除的 repo_id 通配符，可重复；缺省排除测试/演示仓库",
    )
    parser.add_argument("--with-gh", action="store_true", help="调用 gh 补 Issue 原文与 PR")
    parser.add_argument(
        "--github-pr-repo",
        action="append",
        type=Path,
        default=[],
        help="额外导出该本地仓库中与 keda 无关的已合并 PR（需要 gh），可重复",
    )
    parser.add_argument(
        "--pr-limit", type=int, default=DEFAULT_PR_LIMIT, help="每个仓库最多取多少个已合并 PR"
    )
    parser.add_argument(
        "--agent-sessions-repo",
        action="append",
        type=Path,
        default=[],
        help="额外导出该本地仓库的真人 Claude Code 会话（首句会脱敏；需显式开启），可重复",
    )
    parser.add_argument(
        "--sessions-root", type=Path, help="Claude Code 会话根目录，缺省 ~/.claude/projects"
    )
    namespace = parser.parse_args(argv)
    return ExportOptions(
        db_path=namespace.db,
        output_path=namespace.output,
        repo_ids=tuple(namespace.repo),
        exclude_repo_patterns=tuple(namespace.exclude_repo or DEFAULT_EXCLUDE_REPO_PATTERNS),
        use_gh=namespace.with_gh,
        github_pr_repo_paths=tuple(namespace.github_pr_repo),
        pr_limit=namespace.pr_limit,
        agent_session_repo_paths=tuple(namespace.agent_sessions_repo),
        sessions_root=namespace.sessions_root,
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：写出 JSONL，并把统计摘要打印到 stderr。"""
    options = parse_args(argv)
    has_extra_sources = options.github_pr_repo_paths or options.agent_session_repo_paths
    if not options.db_path.is_file() and not has_extra_sources:
        print(f"数据库不存在: {options.db_path}", file=sys.stderr)
        return 2
    samples = export_samples(options)
    lines = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples)
    if options.output_path:
        options.output_path.write_text(lines, encoding="utf-8")
    else:
        sys.stdout.write(lines)
    summary = summarize_samples(samples)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"[{stamp}] 导出摘要: {json.dumps(summary, ensure_ascii=False)}", file=sys.stderr)
    return 0
