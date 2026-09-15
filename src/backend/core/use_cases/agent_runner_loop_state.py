"""loop 状态持久化能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.persistence.loop_state_json`
的 :class:`JsonLoopStateStore`，使 ``api/`` 层只依赖 core。实现机制
（模块加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_loop_state_json_module = importlib.import_module(
    "backend.engines.agent_runner.persistence.loop_state_json"
)

JsonLoopStateStore = _engines_loop_state_json_module.JsonLoopStateStore

__all__ = ["JsonLoopStateStore"]
