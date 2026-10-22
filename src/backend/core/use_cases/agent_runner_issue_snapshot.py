"""认领 Issue 时留存原始任务输入快照。

数据集样本最关键的一项是"任务当时到底是怎么说的"，而 Issue 正文可以被随时编辑。
Runner 在认领一个 ready Issue、工作目录就绪后、Agent 启动前，把 Agent 即将读到的
任务输入原样追加一条到本机状态目录：

- **只追加、不改写**：同一正文（SHA-256 相同）不重复写；正文被改过才追加新行，旧行
  逐字节保持不变。
- **只记事实，不记评判**：字段只有原文与代码起点，没有"可用于评测""难度"之类标签；
  这些判断属于后续由人工与更强 Agent 完成的整理层。
- **可演进**：每行带整数 ``schema_version``，之后只允许新增可选字段；读取方遇到未知
  版本的行跳过并告警。
- **观察性**：写入失败只记 warning，绝不阻断认领、实现或交付。

快照放在 ``state_home()`` 下（不在任何被处理的项目内），所有项目的快照集中一处，
由数据集导出脚本汇总；它是采集缓存，长期保存靠导出的样本。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker

_logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = 1
"""当前写入的快照格式版本；只允许新增可选字段。"""

_SNAPSHOT_DIR_RELPATH = Path("dataset") / "issue-snapshots"
"""快照目录相对本机状态目录的位置。"""

_UNSAFE_DIRNAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class IssueInputSnapshot:
    """认领时刻的任务输入与代码起点（一行 JSON）。"""

    schema_version: int
    captured_at: str
    repo_id: str
    issue_number: int
    title: str
    body: str
    body_sha256: str
    labels: tuple[str, ...]
    state: str
    url: str
    base_sha: str | None
    head_sha: str | None
    base_ref: str | None


@dataclass(frozen=True)
class IssueSnapshotRequest:
    """一次快照写入所需的上下文。"""

    issue: IssueSummary
    repo_id: str
    worktree_path: Path
    config: AppConfig
    process_runner: IProcessRunner


def snapshot_file_path(*, repo_id: str, issue_number: int, home_path: Path | None = None) -> Path:
    """返回某 Issue 的快照文件路径，不创建目录。

    ``repo_id`` 先清洗为安全目录名，避免路径分隔符逃出快照区。

    Args:
        repo_id: 仓库标识。
        issue_number: Issue 编号。
        home_path: 家目录；省略时取当前用户家目录（便于测试注入临时目录）。
    """
    safe_repo_dirname = _UNSAFE_DIRNAME_PATTERN.sub("_", repo_id) or "_"
    return (
        product_identity.state_home(home_path)
        / _SNAPSHOT_DIR_RELPATH
        / safe_repo_dirname
        / f"issue-{issue_number}.snapshots.jsonl"
    )


def _sha256_hex(text: str) -> str:
    """返回文本 UTF-8 编码的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_git_line(request: IssueSnapshotRequest, args: list[str]) -> str | None:
    """在工作目录执行只读 git 命令，失败或无输出返回 ``None``。"""
    result = request.process_runner.run(["git", *args], cwd=request.worktree_path, check=False)
    output = result.stdout.strip() if result.return_code == 0 else ""
    return output or None


def _candidate_base_refs(request: IssueSnapshotRequest) -> list[str]:
    """按优先级列出可作为"代码起点基线"的引用。

    ``stack`` 排序时基线是上游 Issue 的分支；否则是配置的基线分支。远程引用优先，
    因为工作目录是从刷新后的远程引用分叉出来的。
    """
    remote = request.config.git.remote
    declaration = parse_dependency_marker(request.issue.body)
    if declaration is not None and declaration.sequence == "stack" and declaration.issue_numbers:
        branch_name = f"issue-{declaration.issue_numbers[0]}"
    else:
        branch_name = request.config.worktree.base_branch or request.config.git.base_branch
    return [f"{remote}/{branch_name}", branch_name]


def _resolve_base(request: IssueSnapshotRequest) -> tuple[str | None, str | None]:
    """返回 ``(base_sha, base_ref)``：工作目录 HEAD 与基线的分叉点。

    复用旧工作目录时 HEAD 可能含上次留下的提交，因此起点取分叉点而不是 HEAD；
    所有候选引用都解析不出时返回 ``(None, None)``。
    """
    for base_ref in _candidate_base_refs(request):
        if _run_git_line(request, ["rev-parse", "--verify", "--quiet", base_ref]) is None:
            continue
        merge_base = _run_git_line(request, ["merge-base", "HEAD", base_ref])
        if merge_base is not None:
            return merge_base, base_ref
    return None, None


def build_issue_snapshot(request: IssueSnapshotRequest) -> IssueInputSnapshot:
    """用认领时已在内存里的 Issue 与工作目录 git 状态构造快照。"""
    base_sha, base_ref = _resolve_base(request)
    issue = request.issue
    return IssueInputSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        repo_id=request.repo_id,
        issue_number=issue.number,
        title=issue.title,
        body=issue.body,
        body_sha256=_sha256_hex(issue.body),
        labels=tuple(issue.labels),
        state=issue.state,
        url=issue.url,
        base_sha=base_sha,
        head_sha=_run_git_line(request, ["rev-parse", "HEAD"]),
        base_ref=base_ref,
    )


def _last_body_sha256(snapshot_path: Path) -> str | None:
    """读取文件最后一个非空行的 ``body_sha256``；文件缺失或该行损坏返回 ``None``。"""
    if not snapshot_path.is_file():
        return None
    lines = snapshot_path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        return record.get("body_sha256") if isinstance(record, dict) else None
    return None


def append_snapshot_if_changed(*, snapshot_path: Path, snapshot: IssueInputSnapshot) -> bool:
    """正文哈希与最后一行不同（或文件不存在）时追加一行，返回是否写入。

    已有行永不改写、不删除；最后一行损坏时按"不同"处理并继续追加。
    """
    if _last_body_sha256(snapshot_path) == snapshot.body_sha256:
        return False
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    record_line = json.dumps(
        {**asdict(snapshot), "labels": list(snapshot.labels)}, ensure_ascii=False
    )
    with snapshot_path.open("a", encoding="utf-8") as snapshot_file:
        snapshot_file.write(record_line + "\n")
    return True


def read_issue_snapshots(snapshot_path: Path) -> list[dict[str, Any]]:
    """按文件顺序读回快照；损坏行与未知（更高）版本的行跳过并告警。"""
    if not snapshot_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        snapshot_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            _logger.warning("skip corrupt snapshot line %s:%d", snapshot_path, line_number)
            continue
        version = record.get("schema_version") if isinstance(record, dict) else None
        if not isinstance(version, int) or version > SNAPSHOT_SCHEMA_VERSION:
            _logger.warning(
                "skip snapshot line with unknown schema_version %r in %s:%d",
                version,
                snapshot_path,
                line_number,
            )
            continue
        records.append(record)
    return records


def record_issue_input_snapshot(
    request: IssueSnapshotRequest, *, home_path: Path | None = None
) -> bool:
    """认领路径上的入口：构造并追加快照，任何异常只记 warning。

    Returns:
        本次是否写入了新行；失败或正文未变化为 ``False``。
    """
    try:
        snapshot = build_issue_snapshot(request)
        snapshot_path = snapshot_file_path(
            repo_id=request.repo_id, issue_number=request.issue.number, home_path=home_path
        )
        return append_snapshot_if_changed(snapshot_path=snapshot_path, snapshot=snapshot)
    except Exception:
        _logger.warning(
            "failed to record issue input snapshot for %s#%s",
            request.repo_id,
            request.issue.number,
            exc_info=True,
        )
        return False
