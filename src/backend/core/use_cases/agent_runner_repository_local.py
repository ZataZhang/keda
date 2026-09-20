"""本地仓库注册表能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.repository_local` 的仓库发现、
初始化与 gitignore 同步能力，使 ``api/`` 层只依赖 core。实现机制（模块
加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_repository_local_module = importlib.import_module(
    "backend.engines.agent_runner.repository_local"
)

GITIGNORE_BLOCK_FOOTER = _engines_repository_local_module.GITIGNORE_BLOCK_FOOTER
GITIGNORE_BLOCK_HEADER = _engines_repository_local_module.GITIGNORE_BLOCK_HEADER
IAR_GITIGNORE_SECTIONS = _engines_repository_local_module.IAR_GITIGNORE_SECTIONS
GitignoreSyncOptions = _engines_repository_local_module.GitignoreSyncOptions
GitignoreSyncResult = _engines_repository_local_module.GitignoreSyncResult
IARRepositoryNotInitializedError = _engines_repository_local_module.IARRepositoryNotInitializedError
RepositoryInitOptions = _engines_repository_local_module.RepositoryInitOptions
RepositoryInitResult = _engines_repository_local_module.RepositoryInitResult
detect_git_repository_root = _engines_repository_local_module.detect_git_repository_root
discover_iar_repositories = _engines_repository_local_module.discover_iar_repositories
ensure_gitignore_entries = _engines_repository_local_module.ensure_gitignore_entries
initialize_repository_local_config = (
    _engines_repository_local_module.initialize_repository_local_config
)
normalize_repository_id = _engines_repository_local_module.normalize_repository_id
require_iar_repository_initialized = (
    _engines_repository_local_module.require_iar_repository_initialized
)

__all__ = [
    "GITIGNORE_BLOCK_FOOTER",
    "GITIGNORE_BLOCK_HEADER",
    "IAR_GITIGNORE_SECTIONS",
    "GitignoreSyncOptions",
    "GitignoreSyncResult",
    "IARRepositoryNotInitializedError",
    "RepositoryInitOptions",
    "RepositoryInitResult",
    "detect_git_repository_root",
    "discover_iar_repositories",
    "ensure_gitignore_entries",
    "initialize_repository_local_config",
    "normalize_repository_id",
    "require_iar_repository_initialized",
]
