"""GitHub 非幂等写操作的严格账本与远端对账辅助。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from backend.core.shared.interfaces.runner_recovery import IRunnerRecoveryStore
from backend.core.shared.models.runner_recovery_operations import (
    build_operation_intent,
    execute_journaled_write,
    operation_marker,
)
from backend.infrastructure.process_runner import CommandResult


class _JournaledCommentClient(Protocol):
    """评论账本包装器需要的最小 GitHub 客户端契约。"""

    repo_path: Path
    recovery_store: IRunnerRecoveryStore | None

    def _write_body_file(self, temp_dir: str, filename: str, body: str) -> Path: ...

    def _run_with_retry(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        attempts: int = ...,
    ) -> CommandResult: ...


_MAX_WRITE_ATTEMPTS = 5
_WRITE_RETRY_BUDGET_SECONDS = 60


def send_github_write_command(
    client: _JournaledCommentClient,
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    accept_failure: Callable[[str], bool] | None = None,
) -> str | None:
    """执行一次账本写命令，并保留足够诊断供恢复策略分类。

    Args:
        client: GitHub CLI 客户端。
        command: 单次 `gh` 写命令。
        cwd: 命令工作目录；省略时使用客户端仓库根目录。
        accept_failure: 可选的已满足目标状态判定，例如“已合并”。

    Returns:
        标准输出中的远端引用；没有输出时为 ``None``。

    Raises:
        RuntimeError: 已启用严格账本且命令失败时，包含可分类诊断。
        subprocess.CalledProcessError: 未启用账本时命令失败。
    """
    recovery_store = getattr(client, "recovery_store", None)
    result = client._run_with_retry(
        command,
        cwd=cwd if cwd is not None else client.repo_path,
        check=False if recovery_store is not None or accept_failure is not None else True,
        **({"attempts": 1} if recovery_store is not None else {}),
    )
    if result.return_code != 0:
        diagnostic_text = result.stderr.strip() or result.stdout.strip()
        if accept_failure is not None and accept_failure(diagnostic_text):
            return result.stdout.strip() or diagnostic_text or None
        if recovery_store is not None:
            raise RuntimeError(
                diagnostic_text or "GitHub write command failed without a diagnostic."
            )
        raise subprocess.CalledProcessError(
            result.return_code,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result.stdout.strip() or None


def post_comment_with_recovery(
    *,
    client: _JournaledCommentClient,
    repository_id: str,
    issue_number: int,
    operation_kind: str,
    target: str,
    body: str,
    command_for_body_file: Callable[[Path], Sequence[str]],
    comment_entries_for_marker: Callable[[str], list[tuple[int, str]]],
) -> None:
    """为 Issue 或 PR 评论共用稳定身份、发送前账本和未知结果对账。

    Args:
        client: GitHub CLI 客户端。
        repository_id: 规范化的仓库身份。
        issue_number: Issue 或 PR 编号。
        operation_kind: 显式写操作类别。
        target: 远端评论集合。
        body: 已脱敏的评论正文。
        command_for_body_file: 按评论正文临时文件构造一次 gh 命令。
        comment_entries_for_marker: 返回全量可信评论分页中的 ID 与正文。
    """
    intent = build_operation_intent(
        repository_id=repository_id,
        issue_number=issue_number,
        operation_kind=operation_kind,
        target=target,
        payload_text=body,
    )
    recovery_store = getattr(client, "recovery_store", None)
    marker = operation_marker(intent.operation_key) if recovery_store else ""
    marked_body = body if not marker or marker in body else f"{body}\n\n{marker}"

    def send_comment() -> str | None:
        with tempfile.TemporaryDirectory(prefix="kc-recovery-comment-") as temp_dir:
            comment_path = client._write_body_file(temp_dir, "comment.md", marked_body)
            command_output = send_github_write_command(
                client,
                command_for_body_file(comment_path),
            )
            output_text = (command_output or "").strip()
            if not output_text:
                return None
            try:
                remote_payload = json.loads(output_text)
            except json.JSONDecodeError:
                return output_text
            if isinstance(remote_payload, dict):
                remote_id = remote_payload.get("id")
                if remote_id is not None:
                    return str(remote_id)
            return output_text

    def reconcile_comment() -> str | None:
        if not marker:
            return None
        return find_comment_reference(
            comment_entries=comment_entries_for_marker(marker),
            marker=marker,
        )

    def confirm_comment_absence() -> str | None:
        # 评论列表在服务端传播延迟期间可能短暂看不到新对象；再次读取后才考虑补发。
        for delay_seconds in (1, 2, 4):
            time.sleep(delay_seconds)
            remote_reference = reconcile_comment()
            if remote_reference:
                return remote_reference
        return None

    execute_journaled_write(
        recovery_store=recovery_store,
        intent=intent,
        send=send_comment,
        reconcile=reconcile_comment,
        # 评论列表即使完整分页并重复读取，也不能证明 GitHub 尚未传播迟到的创建。
        # 结果未知且未查到标记时继续保持 unknown；只有传输层明确证明未发送才补发。
        safe_to_retry_after_confirmed_absence=False,
        reconcile_after_absence=(confirm_comment_absence if recovery_store is not None else None),
    )


def find_comment_reference(
    *,
    comment_entries: list[tuple[int, str]],
    marker: str,
) -> str | None:
    """从完整分页的评论记录中提取操作标记对应的评论 ID。"""
    for comment_id, comment_body in comment_entries:
        if marker in comment_body and comment_id > 0:
            return str(comment_id)
    return None
