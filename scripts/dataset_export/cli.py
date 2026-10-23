"""``python -m scripts.dataset_export`` 的命令行：解析参数、写出 JSONL、打印统计摘要。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.dataset_export.archive import ArchivePrivacyError, archive_samples
from scripts.dataset_export.curation import build_curation_worklist
from scripts.dataset_export.export_samples import (
    DEFAULT_COMMIT_LIMIT,
    DEFAULT_DB_PATH,
    DEFAULT_EXCLUDE_REPO_PATTERNS,
    DEFAULT_PR_LIMIT,
    ExportOptions,
    export_samples,
    resolve_repo_slug,
    summarize_samples,
)

EXIT_PARTIAL = 3
EXIT_PRIVACY = 4


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
    parser.add_argument(
        "--codex-sessions-repo",
        action="append",
        type=Path,
        default=[],
        help="额外导出该本地仓库的真人 codex 会话（按会话 cwd 归属；首句会脱敏；需显式开启），可重复",
    )
    parser.add_argument(
        "--codex-sessions-root", type=Path, help="codex 会话根目录，缺省 ~/.codex/sessions"
    )
    parser.add_argument(
        "--commits-repo",
        action="append",
        type=Path,
        default=[],
        help="额外导出该本地仓库默认分支上没有 PR、也没有被其他来源覆盖的普通提交，可重复",
    )
    parser.add_argument(
        "--commit-limit",
        type=int,
        default=DEFAULT_COMMIT_LIMIT,
        help="每个仓库最多取多少个普通提交",
    )
    parser.add_argument(
        "--curation-dir",
        type=Path,
        help="后期补全目录：叠加其中 *.jsonl 的补全记录（任务输入、排除、评测方式），不改原始记录",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        help="把样本归档到该目录（每个样本一个文件、原地更新、只增不删，供提交进数据集仓库）；"
        "给了它又没给 --output 时，不再把样本打到 stdout",
    )
    parser.add_argument(
        "--curation-worklist",
        type=Path,
        help="另写一份待补全清单（输入偏弱的开发类样本），交给人或更强的 Agent 补全",
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
        codex_session_repo_paths=tuple(namespace.codex_sessions_repo),
        codex_sessions_root=namespace.codex_sessions_root,
        commit_repo_paths=tuple(namespace.commits_repo),
        commit_limit=namespace.commit_limit,
        curation_dir=namespace.curation_dir,
        worklist_path=namespace.curation_worklist,
        archive_dir=namespace.archive_dir,
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：写出 JSONL，并把统计摘要打印到 stderr。"""
    options = parse_args(argv)
    has_extra_sources = (
        options.github_pr_repo_paths
        or options.agent_session_repo_paths
        or options.codex_session_repo_paths
        or options.commit_repo_paths
    )
    if not options.db_path.is_file() and not has_extra_sources:
        print(f"数据库不存在: {options.db_path}", file=sys.stderr)
        return 2
    samples = export_samples(options)
    lines = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples)
    if options.output_path:
        options.output_path.write_text(lines, encoding="utf-8")
    elif options.archive_dir is None:
        sys.stdout.write(lines)
    if options.archive_dir is not None:
        try:
            report = archive_samples(samples, options.archive_dir)
        except ArchivePrivacyError as error:
            print(f"归档失败: {error}", file=sys.stderr)
            return EXIT_PRIVACY
        print(
            f"归档: 新增 {report.added}，更新 {report.updated}，未变 {report.unchanged}；"
            f"归档目录共 {sum(report.total_by_origin.values())} 个（{report.total_by_origin}）",
            file=sys.stderr,
        )
    if options.worklist_path:
        repo_paths = {
            resolve_repo_slug(path): path
            for path in (
                *options.commit_repo_paths,
                *options.github_pr_repo_paths,
                *options.agent_session_repo_paths,
                *options.codex_session_repo_paths,
            )
        }
        worklist = build_curation_worklist(samples, repo_paths=repo_paths, use_gh=options.use_gh)
        options.worklist_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in worklist),
            encoding="utf-8",
        )
        print(f"待补全清单: {len(worklist)} 条 -> {options.worklist_path}", file=sys.stderr)
    summary = summarize_samples(samples)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"[{stamp}] 导出摘要: {json.dumps(summary, ensure_ascii=False)}", file=sys.stderr)
    if options.skipped_sources:
        # 样本和归档已经写好（数据本身有效），但有被要求的来源缺失，不能报成功。
        print(f"部分来源被跳过: {', '.join(options.skipped_sources)}", file=sys.stderr)
        return EXIT_PARTIAL
    return 0
