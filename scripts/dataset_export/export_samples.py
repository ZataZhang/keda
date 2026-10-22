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
PATROL_TITLE_PATTERN = re.compile("巡检|巡查")
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
    """巡检类 Issue 标为 ``patrol``，其余视为 ``development``。"""
    return "patrol" if title and PATROL_TITLE_PATTERN.search(title) else "development"


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


def classify_input(*, gh_issue: dict[str, Any] | None, runs: list[dict[str, Any]]) -> str:
    """判定任务输入的可信度：原文 > 仅标题 > 缺失。"""
    if gh_issue and gh_issue.get("body"):
        return "verbatim_issue_body"
    if any(run.get("issue_title") for run in runs):
        return "title_only"
    return "missing"


def is_eval_ready(*, labels: dict[str, Any], input_provenance: str) -> bool:
    """能否作为评测题：PR 已合并、有提交、有输入、真实开发任务、规模适中，且已核实无 merge commit。

    ``merge_commit_count`` 为 ``None``（提交对象不在本地）表示未核实，不算通过。
    """
    return bool(
        labels["resolved"]
        and labels["resolved_basis"] == RESOLVED_BASIS_PR_MERGED
        and labels["has_commits"]
        and input_provenance != "missing"
        and labels["task_kind"] == "development"
        and labels["size_band"] in {name for name, _, _ in SIZE_BANDS}
        and labels["merge_commit_count"] == 0
    )


def build_sample(
    group: IssueGroup, *, repo_path: Path | None, options: ExportOptions
) -> dict[str, Any]:
    """把一个 Issue 的全部事实拼成一条样本。"""
    gh_facts = (
        collect_gh_facts(repo_path=repo_path, issue_number=group.issue_number)
        if options.use_gh and repo_path
        else {"issue": None, "pull_requests": None}
    )
    pull_request = pick_pull_request(gh_facts["pull_requests"])
    if pull_request and repo_path:
        git_facts = collect_pull_request_commits(repo_path=repo_path, pull_request=pull_request)
    elif repo_path:
        git_facts = collect_branch_commits(repo_path=repo_path, issue_number=group.issue_number)
    else:
        git_facts = None
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
    input_provenance = classify_input(gh_issue=gh_issue, runs=group.runs)
    issue_title = (gh_issue or {}).get("title") or next(
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
        "repo_id": group.repo_id,
        "alias_repo_ids": group.alias_repo_ids,
        "issue_number": group.issue_number,
        "input": {
            "provenance": input_provenance,
            "title": issue_title,
            "body": (gh_issue or {}).get("body"),
            "url": (gh_issue or {}).get("url")
            or next((r["issue_url"] for r in group.runs if r.get("issue_url")), None),
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
        counts["resolved_by_merged_pr"] += (
            labels["resolved"] and labels["resolved_basis"] == RESOLVED_BASIS_PR_MERGED
        )
        counts["patrol_issue"] += labels["task_kind"] == "patrol"
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


def export_samples(options: ExportOptions) -> list[dict[str, Any]]:
    """执行一次完整导出并返回样本列表。"""
    connection = open_readonly(options.db_path)
    try:
        groups = merge_groups_by_repo_path(load_issue_groups(connection, options=options))
    finally:
        connection.close()
    return [
        build_sample(group, repo_path=latest_repo_path(group), options=options) for group in groups
    ]


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
    namespace = parser.parse_args(argv)
    return ExportOptions(
        db_path=namespace.db,
        output_path=namespace.output,
        repo_ids=tuple(namespace.repo),
        exclude_repo_patterns=tuple(namespace.exclude_repo or DEFAULT_EXCLUDE_REPO_PATTERNS),
        use_gh=namespace.with_gh,
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：写出 JSONL，并把统计摘要打印到 stderr。"""
    options = parse_args(argv)
    if not options.db_path.is_file():
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
