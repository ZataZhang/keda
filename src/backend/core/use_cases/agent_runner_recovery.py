"""Agent Runner 阶段结果的成果前提核对。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner
from backend.core.shared.interfaces.runner_recovery import IRunnerRecoveryStore
from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.models.runner_recovery import RecoveryStageRecord
from backend.core.shared.models.runner_recovery_operations import (
    build_operation_intent,
    execute_journaled_write,
    operation_marker,
)

__all__ = [
    "ValidationStageContext",
    "build_operation_intent",
    "execute_journaled_write",
    "find_reusable_validation_stage",
    "operation_marker",
    "persist_validation_stage_success",
    "resolve_validation_stage_context",
]


@dataclass(frozen=True)
class ValidationStageContext:
    """可复用验证阶段的稳定身份和成果前提。"""

    run_key: str
    stage_key: str
    artifact_tree: str
    base_sha: str
    validation_digest: str


def resolve_validation_stage_context(
    *,
    repository_id: str,
    issue_number: int,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> ValidationStageContext | None:
    """读取当前 Git tree、base 与验证契约摘要。

    Args:
        repository_id: 稳定仓库登记标识。
        issue_number: 当前 Issue 编号。
        worktree_path: 真实 runner worktree。
        config: 合并后的仓库配置。
        process_runner: Git 命令执行器。

    Returns:
        可核验的阶段前提；Git 信息不完整时为 ``None``。
    """
    tree_result = process_runner.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=worktree_path,
        check=False,
    )
    base_result = process_runner.run(
        ["git", "rev-parse", f"{config.git.remote}/{config.git.base_branch}"],
        cwd=worktree_path,
        check=False,
    )
    artifact_tree = tree_result.stdout.strip()
    base_sha = base_result.stdout.strip()
    if (
        tree_result.return_code != 0
        or base_result.return_code != 0
        or not artifact_tree
        or not base_sha
    ):
        return None

    validation_contract_json = json.dumps(
        {
            "commands": [repr(command) for command in config.runner.verification_commands],
            "validation": repr(config.validation),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    validation_digest = hashlib.sha256(validation_contract_json.encode("utf-8")).hexdigest()
    run_key = f"repo:{repository_id}:issue:{issue_number}"
    return ValidationStageContext(
        run_key=run_key,
        stage_key=f"{run_key}:validation",
        artifact_tree=artifact_tree,
        base_sha=base_sha,
        validation_digest=validation_digest,
    )


def find_reusable_validation_stage(
    *,
    recovery_store: IRunnerRecoveryStore | None,
    stage_context: ValidationStageContext | None,
) -> RecoveryStageRecord | None:
    """只在代码成果、base 與验证契约全部一致时返回已通过阶段。

    Args:
        recovery_store: 严格恢复账本。
        stage_context: 当前运行时计算的前提。

    Returns:
        匹配当前成果的已通过阶段；否则为 ``None``。
    """
    if recovery_store is None or stage_context is None:
        return None
    saved_stage = recovery_store.get_stage(stage_context.stage_key)
    if saved_stage is None or saved_stage.status != "passed":
        return None
    if (
        saved_stage.artifact_tree != stage_context.artifact_tree
        or saved_stage.base_sha != stage_context.base_sha
        or saved_stage.validation_digest != stage_context.validation_digest
    ):
        return None
    return saved_stage


def persist_validation_stage_success(
    *,
    recovery_store: IRunnerRecoveryStore | None,
    stage_context: ValidationStageContext | None,
) -> None:
    """严格保存已通过验证的成果前提。

    Args:
        recovery_store: 严格恢复账本。
        stage_context: 测试成功后重新读取的成果前提。
    """
    if recovery_store is None or stage_context is None:
        return
    recovery_store.save_stage(
        RecoveryStageRecord(
            stage_key=stage_context.stage_key,
            run_key=stage_context.run_key,
            stage="validation",
            artifact_tree=stage_context.artifact_tree,
            base_sha=stage_context.base_sha,
            validation_digest=stage_context.validation_digest,
            status="passed",
            completed_at=datetime.now(timezone.utc),
        )
    )
