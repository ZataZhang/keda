"""Runner 管理的 Git ref 写入恢复。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import IssueSummary
from backend.core.shared.models.runner_recovery_operations import (
    build_operation_intent,
    execute_journaled_write,
)


@dataclass(frozen=True)
class GitRefMutationRequest:
    """一项 runner 管理的远端 Git ref 更新或删除。"""

    issue: IssueSummary
    worktree_path: Path
    process_runner: IProcessRunner
    github_client: IGitHubClient
    remote_name: str
    target_ref: str
    desired_sha: str | None
    expected_remote_sha: str | None = None
    force_with_lease: bool = False


def mutate_git_ref_with_recovery(request: GitRefMutationRequest) -> str:
    """为 Issue 关联的 ref 写入记账，并用远端事实确认结果。

    普通推送保持 Git 的 fast-forward 约束。分支删除使用显式 lease，避免在查询后
    删除其他参与者新推送的分支。未知结果只读对账；若远端仍未显示目标状态则挂起，
    不自动重复发送。
    """
    if not request.target_ref.startswith("refs/heads/"):
        raise ValueError("Runner Git recovery only supports refs/heads targets.")
    if request.desired_sha is None and not request.expected_remote_sha:
        raise ValueError("Remote branch deletion requires the SHA observed before deletion.")
    if request.force_with_lease and request.desired_sha is None:
        raise ValueError("Use the leased deletion path instead of force-pushing an empty ref.")

    repository_id = request.worktree_path.resolve().as_posix()
    owner_repo_resolver = getattr(request.github_client, "_get_owner_repo", None)
    if callable(owner_repo_resolver):
        repository_id = owner_repo_resolver()

    operation_kind = "git_push" if request.desired_sha is not None else "git_ref_delete"
    payload_text = request.desired_sha if request.desired_sha is not None else "deleted"
    intent = build_operation_intent(
        repository_id=repository_id,
        issue_number=request.issue.number,
        operation_kind=operation_kind,
        target=f"{request.remote_name}:{request.target_ref}",
        payload_text=payload_text,
    )

    def read_remote_sha() -> str | None:
        result = request.process_runner.run(
            [
                "git",
                "ls-remote",
                "--heads",
                request.remote_name,
                request.target_ref,
            ],
            cwd=request.worktree_path,
            check=False,
            timeout=30,
        )
        if result.return_code != 0:
            diagnostic = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Cannot reconcile remote ref {request.target_ref}: {diagnostic}")
        output_lines = result.stdout.splitlines()
        if not output_lines:
            return None
        remote_sha, remote_ref = output_lines[0].split(maxsplit=1)
        if remote_ref != request.target_ref:
            raise RuntimeError("Remote returned an unexpected ref during Git reconciliation.")
        return remote_sha

    def reconcile() -> str | None:
        remote_sha = read_remote_sha()
        if request.desired_sha is None:
            return f"deleted:{request.target_ref}" if remote_sha is None else None
        if remote_sha == request.desired_sha:
            return f"{request.target_ref}@{remote_sha}"
        return None

    def send() -> str | None:
        if request.desired_sha is None:
            command = [
                "git",
                "push",
                f"--force-with-lease={request.target_ref}:{request.expected_remote_sha}",
                request.remote_name,
                f":{request.target_ref}",
            ]
        else:
            command = ["git", "push"]
            if request.force_with_lease:
                expected_remote_sha = request.expected_remote_sha
                if expected_remote_sha is None:
                    expected_remote_sha = read_remote_sha() or ""
                command.append(f"--force-with-lease={request.target_ref}:{expected_remote_sha}")
            command.extend(
                [
                    "-u",
                    request.remote_name,
                    f"{request.desired_sha}:{request.target_ref}",
                ]
            )
        result = request.process_runner.run(
            command,
            cwd=request.worktree_path,
            check=False,
            timeout=120,
        )
        if result.return_code != 0:
            diagnostic = result.stderr.strip() or result.stdout.strip() or "git push failed."
            raise RuntimeError(diagnostic)
        if request.desired_sha is None:
            return f"deleted:{request.target_ref}"
        return f"{request.target_ref}@{request.desired_sha}"

    reference = execute_journaled_write(
        recovery_store=getattr(request.github_client, "recovery_store", None),
        intent=intent,
        send=send,
        reconcile=reconcile,
    )
    if reference is None:
        raise RuntimeError(f"Git operation {intent.operation_key} completed without a reference.")
    return reference
