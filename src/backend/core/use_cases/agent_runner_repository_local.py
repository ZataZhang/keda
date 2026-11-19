"""本地仓库注册表能力的 core 编排入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 只依赖
``core/``，本模块把下层实现转发给上层。``core/`` 静态依赖 ``engines/`` 是
允许的（见 ``docs/ai-standards/architecture.md``），不再用 ``importlib``
动态加载：依赖因此对检查器、IDE 与类型检查都可见。本模块只做层次归位，
不新增编排逻辑；各名字语义以
:mod:`backend.engines.agent_runner.repository_local` 为准。
"""

from __future__ import annotations

from backend.engines.agent_runner.repository_local import (
    GITIGNORE_BLOCK_FOOTER,
    GITIGNORE_BLOCK_HEADER,
    GITIGNORE_SECTIONS,
    GitignoreSyncOptions,
    GitignoreSyncResult,
    RepositoryInitOptions,
    RepositoryInitResult,
    RepositoryLocalConfigExistsError,
    RepositoryNotInitializedError,
    detect_git_repository_root,
    discover_repositories,
    ensure_gitignore_entries,
    initialize_repository_local_config,
    load_agent_runner_local_settings,
    normalize_repository_id,
    require_repository_initialized,
)


__all__ = [
    "GITIGNORE_BLOCK_FOOTER",
    "GITIGNORE_BLOCK_HEADER",
    "GITIGNORE_SECTIONS",
    "GitignoreSyncOptions",
    "GitignoreSyncResult",
    "RepositoryNotInitializedError",
    "RepositoryInitOptions",
    "RepositoryLocalConfigExistsError",
    "RepositoryInitResult",
    "detect_git_repository_root",
    "discover_repositories",
    "ensure_gitignore_entries",
    "initialize_repository_local_config",
    "normalize_repository_id",
    "load_agent_runner_local_settings",
    "require_repository_initialized",
]
