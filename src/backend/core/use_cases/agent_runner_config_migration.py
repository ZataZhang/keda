"""仓库级 ``.iar.toml`` 迁移能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.repository_local_migration` 的迁移能力，使
``api/`` 层只依赖 core。实现机制（模块加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_repository_local_migration_module = importlib.import_module(
    "backend.engines.agent_runner.repository_local_migration"
)

ConfigMigrationError = _engines_repository_local_migration_module.ConfigMigrationError
ConfigMigrationResult = _engines_repository_local_migration_module.ConfigMigrationResult
PinDecision = _engines_repository_local_migration_module.PinDecision
migrate_repository_local_config = (
    _engines_repository_local_migration_module.migrate_repository_local_config
)

__all__ = [
    "ConfigMigrationError",
    "ConfigMigrationResult",
    "PinDecision",
    "migrate_repository_local_config",
]
