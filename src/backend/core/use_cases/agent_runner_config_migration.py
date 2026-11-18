"""``kc config migrate`` 的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.repository_local_migration`（仓库本地配置的
钉子清理与新旧名改名）、:mod:`backend.engines.agent_runner.state_home_migration`
（本机状态目录搬迁）和 :mod:`backend.engines.agent_runner.worktree_directory_migration`
的能力，使 ``api/`` 层只依赖 core。``core/`` 静态依赖 ``engines/`` 是允许的
（见 ``docs/ai-standards/architecture.md``），本模块不再用 ``importlib`` 动态加载。
"""

from __future__ import annotations

from backend.engines.agent_runner.repository_local_migration import (
    ConfigMigrationError,
    ConfigMigrationResult,
    ConfigRenameResult,
    PinDecision,
    migrate_repository_local_config,
    rename_repository_local_config_file,
)
from backend.engines.agent_runner.state_home_migration import (
    StateHomeMigrationResult,
    conflict_outcomes,
    failure_outcomes,
    migrate_state_home,
)
from backend.engines.agent_runner.worktree_directory_migration import (
    WorktreeDirectoryMigrationResult,
    migrate_worktree_directory,
)

#: 结论分类以函数透出：调用方按值比较，不依赖枚举身份。
STATE_HOME_CONFLICT_OUTCOMES = conflict_outcomes()
STATE_HOME_FAILURE_OUTCOMES = failure_outcomes()

__all__ = [
    "ConfigMigrationError",
    "ConfigMigrationResult",
    "ConfigRenameResult",
    "PinDecision",
    "STATE_HOME_CONFLICT_OUTCOMES",
    "STATE_HOME_FAILURE_OUTCOMES",
    "StateHomeMigrationResult",
    "WorktreeDirectoryMigrationResult",
    "migrate_repository_local_config",
    "migrate_state_home",
    "migrate_worktree_directory",
    "rename_repository_local_config_file",
]
