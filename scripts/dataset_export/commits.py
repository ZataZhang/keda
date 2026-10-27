"""把默认分支上没有 PR 的普通提交还原成样本。

这是质量最低的来源：任务输入只能从提交信息重建（``commit_message``），既不是任务本身，也
没有尝试与轨迹。所以它只记录事实——提交链与改动规模——并永远不计入 ``eval_ready``；
输入留给后期补全（见 :mod:`curation`）。

哪些提交算"普通提交"：沿默认分支的**第一父链**，排除合并提交、标题带 ``(#N)`` 的 squash-merge
PR 提交、机器人账号的提交，以及已经被其他来源（keda 运行、PR、会话）覆盖的提交。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from scripts.dataset_export.export_samples import (
    RESOLVED_BASIS_ON_DEFAULT_BRANCH,
    SCHEMA_VERSION,
    ExportOptions,
    classify_size_band,
    classify_task_kind,
    count_merge_commits,
    find_base_branch,
    is_eval_ready,
    measure_local_diff,
    resolve_repo_slug,
    run_git,
)
from scripts.dataset_export.redaction import redact_text

ORIGIN_GIT_COMMIT = "git_commit"
PROVENANCE_COMMIT_MESSAGE = "commit_message"
# squash-merge 的 PR 提交标题以 ``(#123)`` 结尾；它们由 PR 来源负责。
PR_SQUASH_SUBJECT_PATTERN = re.compile(r"\(#\d+\)\s*$")
BOT_AUTHOR_PATTERN = re.compile(r"\[bot\]$|^dependabot|^github-actions", re.IGNORECASE)
# 多取一些再过滤，免得前面全是 PR 提交时凑不够。
SCAN_MULTIPLIER = 5
_FIELD_SEPARATOR = "\x1f"
_RECORD_SEPARATOR = "\x1e"
_LOG_FORMAT = _FIELD_SEPARATOR.join(["%H", "%P", "%an", "%s", "%b"]) + _RECORD_SEPARATOR


def list_plain_commits(
    *, repo_path: Path, default_ref: str, limit: int, excluded_shas: set[str]
) -> list[dict[str, str]]:
    """沿默认分支第一父链列出最近的普通提交（新的在前），最多 ``limit`` 个。"""
    log_output = run_git(
        repo_path=repo_path,
        args=[
            "log",
            default_ref,
            "--first-parent",
            f"--max-count={limit * SCAN_MULTIPLIER}",
            f"--format={_LOG_FORMAT}",
        ],
    )
    commits: list[dict[str, str]] = []
    for record in (log_output or "").split(_RECORD_SEPARATOR):
        fields = record.strip("\n").split(_FIELD_SEPARATOR)
        if len(fields) != 5:
            continue
        sha, parents, author, subject, body = fields
        if (
            len(parents.split()) != 1
            or subject.startswith("Merge ")
            or PR_SQUASH_SUBJECT_PATTERN.search(subject)
            or BOT_AUTHOR_PATTERN.search(author)
            or sha in excluded_shas
        ):
            continue
        commits.append({"sha": sha, "parent": parents.strip(), "subject": subject, "body": body})
        if len(commits) >= limit:
            break
    return commits


def build_commit_sample(
    commit: dict[str, str], *, repo_path: Path, repo_slug: str, default_ref: str
) -> dict[str, Any]:
    """把一个普通提交还原成样本；沿默认分支第一父链得到的提交必然已在默认分支上。"""
    code = {
        "source": "git_commit",
        "branch": default_ref,
        "base_sha": commit["parent"],
        "final_sha": commit["sha"],
        "commits": [{"sha": commit["sha"], "subject": commit["subject"]}],
        "base_sha_source": "inferred",
    }
    size = measure_local_diff(repo_path=repo_path, code=code)
    redacted_body = redact_text(commit["body"])
    labels = {
        "resolved": True,
        "resolved_basis": RESOLVED_BASIS_ON_DEFAULT_BRANCH,
        "task_kind": classify_task_kind(commit["subject"]),
        "size_band": classify_size_band(size),
        "merge_commit_count": count_merge_commits(repo_path=repo_path, code=code),
        "has_commits": True,
        "has_evidence": False,
        "has_preference_pair": False,
    }
    labels["eval_ready"] = is_eval_ready(labels=labels, input_provenance=PROVENANCE_COMMIT_MESSAGE)
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": f"{repo_slug}#commit-{commit['sha'][:10]}",
        "origin": ORIGIN_GIT_COMMIT,
        "repo_id": repo_slug,
        "alias_repo_ids": [],
        "issue_number": None,
        "input": {
            "provenance": PROVENANCE_COMMIT_MESSAGE,
            "title": commit["subject"],
            "body": redacted_body.text,
            "url": None,
            "edited_since_claim": None,
            "redaction_count": redacted_body.redaction_count,
            "redaction_reviewed": False,
        },
        "code": code,
        "pull_requests": [],
        "runs": [],
        "attempts": [],
        "invocations": [],
        "evidence": {"present": False, "item_count": 0, "items_with_negative_control": 0},
        "size": size,
        "labels": labels,
    }


def export_commit_samples(
    options: ExportOptions, *, excluded_shas: set[str]
) -> list[dict[str, Any]]:
    """导出 ``--commits-repo`` 指定仓库的普通提交样本。"""
    samples: list[dict[str, Any]] = []
    for repo_path in options.commit_repo_paths:
        default_ref = find_base_branch(repo_path=repo_path)
        if default_ref is None:
            print(f"跳过 {repo_path}: 找不到默认分支", file=sys.stderr)
            options.skipped_sources.append(f"git_commit:{repo_path}")
            continue
        repo_slug = resolve_repo_slug(repo_path)
        commits = list_plain_commits(
            repo_path=repo_path,
            default_ref=default_ref,
            limit=options.commit_limit,
            excluded_shas=excluded_shas,
        )
        built = [
            build_commit_sample(
                commit, repo_path=repo_path, repo_slug=repo_slug, default_ref=default_ref
            )
            for commit in commits
        ]
        print(f"{repo_path} [commits]: 普通提交 {len(built)}", file=sys.stderr)
        samples.extend(built)
    return samples
