"""worktree CLI 管理器装配能力的 core 编排入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 只依赖
``core/``，本模块把下层实现转发给上层。``core/`` 静态依赖 ``engines/`` 是
允许的（见 ``docs/ai-standards/architecture.md``），不再用 ``importlib``
动态加载：依赖因此对检查器、IDE 与类型检查都可见。本模块只做层次归位，
不新增编排逻辑；各名字语义以
:mod:`backend.engines.agent_runner.worktree_cli` 为准。
"""

from __future__ import annotations

from backend.engines.agent_runner.worktree_cli import (
    build_worktree_manager,
)


__all__ = ["build_worktree_manager"]
