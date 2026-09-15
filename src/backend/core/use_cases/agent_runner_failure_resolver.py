"""agent 失败恢复编排能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.failure_resolver` 的
:class:`AgentFailureResolver`，使 ``api/`` 层只依赖 core。实现机制
（模块加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_failure_resolver_module = importlib.import_module(
    "backend.engines.agent_runner.failure_resolver"
)

AgentFailureResolver = _engines_failure_resolver_module.AgentFailureResolver

__all__ = ["AgentFailureResolver"]
