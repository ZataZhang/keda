"""``kc config migrate`` 的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.repository_local_migration`（仓库本地配置的
钉子清理与新旧名改名）和 :mod:`backend.engines.agent_runner.state_home_migration`
（本机状态目录搬迁）的能力，使 ``api/`` 层只依赖 core。实现机制（模块加载时经
``importlib`` 解析 engines 实现）见 :mod:`backend.core.use_cases.agent_runner_factory`
的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_repository_local_migration_module = importlib.import_module(
    "backend.engines.agent_runner.repository_local_migration"
)
_engines_state_home_migration_module = importlib.import_module(
    "backend.engines.agent_runner.state_home_migration"
)

ConfigMigrationError = _engines_repository_local_migration_module.ConfigMigrationError
ConfigMigrationResult = _engines_repository_local_migration_module.ConfigMigrationResult
ConfigRenameResult = _engines_repository_local_migration_module.ConfigRenameResult
PinDecision = _engines_repository_local_migration_module.PinDecision
migrate_repository_local_config = (
    _engines_repository_local_migration_module.migrate_repository_local_config
)
rename_repository_local_config_file = (
    _engines_repository_local_migration_module.rename_repository_local_config_file
)

StateHomeMigrationResult = _engines_state_home_migration_module.StateHomeMigrationResult
migrate_state_home = _engines_state_home_migration_module.migrate_state_home
#: 结论分类以函数透出：常量身份在 importlib 解析下会被复制，按值比较才是稳定的用法。
STATE_HOME_CONFLICT_OUTCOMES = _engines_state_home_migration_module.conflict_outcomes()
STATE_HOME_FAILURE_OUTCOMES = _engines_state_home_migration_module.failure_outcomes()

__all__ = [
    "ConfigMigrationError",
    "ConfigMigrationResult",
    "ConfigRenameResult",
    "PinDecision",
    "STATE_HOME_CONFLICT_OUTCOMES",
    "STATE_HOME_FAILURE_OUTCOMES",
    "StateHomeMigrationResult",
    "migrate_repository_local_config",
    "migrate_state_home",
    "rename_repository_local_config_file",
]
