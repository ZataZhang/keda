"""worktree CLI 管理器装配能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.worktree_cli` 的
:func:`build_worktree_manager`，使 ``api/`` 层只依赖 core。实现机制
（模块加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_worktree_cli_module = importlib.import_module("backend.engines.agent_runner.worktree_cli")

build_worktree_manager = _engines_worktree_cli_module.build_worktree_manager

__all__ = ["build_worktree_manager"]
