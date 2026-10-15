"""Realistic Validation 证据与 PR 改动之间的稳定身份。"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.models.agent_runner import CommandResult

_logger = logging.getLogger(__name__)
_FULL_SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
_GIT_FETCH_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PrChangeFingerprintRequest:
    """计算 PR 改动指纹所需的仓库与身份信息。

    Attributes:
        repo_path: 可访问 PR head 和 base Git 对象的仓库工作树。
        remote: 缺少提交对象时用于 fetch 的 Git remote。
        base_sha: GitHub 当前 PR base 的提交 SHA。
        head_sha: GitHub 当前 PR head 的提交 SHA。
        process_runner: 执行 Git 命令的进程端口。
    """

    repo_path: Path
    remote: str
    base_sha: str
    head_sha: str
    process_runner: IProcessRunner


@dataclass(frozen=True)
class EvidenceMarker:
    """PR 评论中解析出的 ``iar:validation-evidence`` 隐藏标记。

    Attributes:
        head_sha: 发布证据时的 PR head。
        branch: 存放证据文件的 orphan 分支。
        count: 证据文件数量。
    """

    head_sha: str
    branch: str
    count: int


@dataclass(frozen=True)
class EvidenceMarkerV2(EvidenceMarker):
    """包含 PR 改动指纹的 v2 证据标记。"""

    base_sha: str
    patch_id: str


def format_validation_evidence_marker(marker: EvidenceMarker | EvidenceMarkerV2) -> str:
    """根据是否具备稳定指纹，渲染兼容的 v1 或 v2 评论标记。

    Args:
        marker: 需要编码到隐藏注释中的证据身份。

    Returns:
        ``iar:validation-evidence`` 格式的单行 HTML 注释。
    """
    if isinstance(marker, EvidenceMarkerV2):
        return (
            f"<!-- iar:validation-evidence version=2 head={marker.head_sha} "
            f"base={marker.base_sha} patch={marker.patch_id} branch={marker.branch} "
            f"count={marker.count} -->"
        )
    return (
        f"<!-- iar:validation-evidence version=1 head={marker.head_sha} "
        f"branch={marker.branch} count={marker.count} -->"
    )


def compute_pr_change_fingerprint(request: PrChangeFingerprintRequest) -> str | None:
    """计算排除 evidence 发布记录后的 PR 改动 patch-id。

    先求当前 base 与 PR head 的 merge-base，因此 main 前进但 PR 尚未 rebase
    时不会把 main 新增的提交误算成 PR 改动。``git patch-id --stable`` 也不把
    父提交身份纳入指纹；PR 自身的改动保持一致时，rebase 后证据仍有效。
    `tasks/evidence/` 下的文件是发布记录，不属于 PR 功能改动；PRD 本身仍纳入
    指纹，因此修改验收范围会让旧签收失效。

    Args:
        request: 当前 PR 的仓库路径、base/head 和进程端口。

    Returns:
        稳定 patch-id；提交不可用、无法解析共同祖先或 Git 命令失败时返回 ``None``。
    """
    if not _FULL_SHA_PATTERN.fullmatch(request.base_sha) or not _FULL_SHA_PATTERN.fullmatch(
        request.head_sha
    ):
        return None

    if not _ensure_commit_available(request, request.base_sha):
        return None
    if not _ensure_commit_available(request, request.head_sha):
        return None

    merge_base_result = _run_git_command(
        request, ["git", "merge-base", request.base_sha, request.head_sha]
    )
    if merge_base_result is None:
        return None
    merge_base_sha = merge_base_result.stdout.strip()
    if merge_base_result.return_code != 0 or not _FULL_SHA_PATTERN.fullmatch(merge_base_sha):
        return None

    diff_result = _run_git_command(
        request,
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-color",
            merge_base_sha,
            request.head_sha,
            "--",
            ".",
            ":(exclude)tasks/evidence/**",
        ],
    )
    if diff_result is None:
        return None
    if diff_result.return_code != 0:
        return None
    if not diff_result.stdout:
        return hashlib.sha1(b"empty-pr-change").hexdigest()

    patch_result = _run_git_command(
        request,
        ["git", "patch-id", "--stable"],
        input_text=diff_result.stdout,
    )
    if patch_result is None:
        return None
    if patch_result.return_code != 0:
        return None
    patch_id = patch_result.stdout.split(maxsplit=1)[0] if patch_result.stdout else ""
    return patch_id if _FULL_SHA_PATTERN.fullmatch(patch_id) else None


def _ensure_commit_available(request: PrChangeFingerprintRequest, commit_sha: str) -> bool:
    """从配置 remote 获取 GitHub 已知、但当前仓库尚未缓存的提交。"""
    object_result = _run_git_command(request, ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"])
    if object_result is not None and object_result.return_code == 0:
        return True
    fetch_result = _run_git_command(
        request,
        ["git", "fetch", "--no-tags", request.remote, commit_sha],
        timeout=_GIT_FETCH_TIMEOUT_SECONDS,
    )
    if fetch_result is None or fetch_result.return_code != 0:
        return False
    object_result = _run_git_command(request, ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"])
    return object_result is not None and object_result.return_code == 0


def _run_git_command(
    request: PrChangeFingerprintRequest,
    command: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: int | None = None,
) -> CommandResult | None:
    """执行指纹所需的 Git 命令，异常时返回 None 以便调用方 fail closed。"""
    try:
        return request.process_runner.run(
            command,
            cwd=request.repo_path,
            check=False,
            input_text=input_text,
            timeout=timeout,
        )
    except Exception as command_exc:  # noqa: BLE001 - 指纹无法计算时不得沿用旧结论。
        command_name = " ".join(command[:2])
        _logger.warning(
            "Could not compute PR evidence identity; '%s' failed: %s",
            command_name,
            command_exc,
        )
        return None
