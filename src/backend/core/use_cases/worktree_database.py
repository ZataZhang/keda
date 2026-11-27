"""为 KedaCode worktree 准备隔离的关系型数据库。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeDatabaseProvisionRequest:
    """描述一次 Issue worktree 数据库准备请求。

    Attributes:
        repository_path: 主仓库路径，参与本机数据库隔离。
        worktree_path: 目标工作树路径。
        issue_number: 当前任务编号。
        migration_command: 仓库显式选择的迁移命令。
        workspace_key: 新任务的完整命名空间；None 保留旧数据库标识。
    """

    repository_path: Path
    worktree_path: Path
    issue_number: int
    migration_command: tuple[str, ...] | None = None
    workspace_key: str | None = None


def provision_worktree_database(
    request: WorktreeDatabaseProvisionRequest,
    process_runner: IProcessRunner,
) -> None:
    """为 worktree 创建独立数据库并执行仓库选定的迁移命令。

    数据库连接从 worktree 的 ``.env.local`` 中读取。实际的 URL 解析与
    PostgreSQL/MySQL 建库由模板脚本承担;这里仅编排 daemon 生命周期,避免
    core 层依赖具体数据库驱动。

    默认迁移是尽力而为。显式配置命令的仓库要求独立库和迁移都成功。

    Args:
        request: 仓库、worktree 与 Issue 标识。
        process_runner: 执行建库和迁移命令的端口。

    Raises:
        RuntimeError: 显式配置迁移命令时，建库或迁移失败。
    """
    database_script_path = (
        request.repository_path / "scripts" / "shared" / "template" / "setup_copied_database.py"
    )
    if not database_script_path.is_file():
        if request.migration_command is not None:
            raise RuntimeError("Worktree database setup script is missing.")
        _logger.warning(
            "worktree 数据库隔离已启用,但未找到建库脚本 %s;回退到共享数据库。"
            "如需隔离,请从模板仓同步 scripts/shared/template/setup_copied_database.py。",
            database_script_path,
        )
        return

    repository_identity = str(request.repository_path.resolve())
    # 新任务增加完整命名空间；旧任务不改原标识，避免重试时切走已有数据库。
    database_identity = (
        f"{repository_identity}\n{request.workspace_key}"
        if request.workspace_key
        else repository_identity
    )
    repository_digest = sha256(database_identity.encode("utf-8")).hexdigest()[:8]
    database_identifier = (
        f"{request.repository_path.name}_kc_issue_{request.issue_number}_{repository_digest}"
    )
    setup_result = process_runner.run(
        [
            "uv",
            "run",
            "python",
            str(database_script_path),
            database_identifier,
            str(request.worktree_path),
            "--strict",
        ],
        cwd=request.worktree_path,
        check=False,
    )
    if setup_result.return_code != 0:
        if request.migration_command is not None:
            raise RuntimeError(
                f"Worktree database setup failed (exit code {setup_result.return_code})."
            )
        _logger.warning(
            "worktree 建库脚本失败(return_code=%s);回退到共享数据库。stdout=%r, stderr=%r",
            setup_result.return_code,
            setup_result.stdout,
            setup_result.stderr,
        )
        return

    if request.migration_command is None:
        if not (request.worktree_path / "alembic.ini").is_file():
            return
        migration_argv = ["uv", "run", "alembic", "upgrade", "head"]
    else:
        migration_argv = [
            argument.replace("{database_identifier}", database_identifier)
            for argument in request.migration_command
        ]

    migration_result = process_runner.run(
        migration_argv,
        cwd=request.worktree_path,
        check=False,
    )
    if migration_result.return_code != 0:
        if request.migration_command is not None:
            raise RuntimeError(
                f"Worktree database migration failed for {database_identifier} "
                f"(exit code {migration_result.return_code})."
            )
        _logger.warning(
            "worktree 数据库迁移失败(return_code=%s);worktree 继续,"
            "agent 运行时会再次执行迁移。stdout=%r, stderr=%r",
            migration_result.return_code,
            migration_result.stdout,
            migration_result.stderr,
        )
        return
    _logger.info("Worktree database migration completed for %s.", database_identifier)
