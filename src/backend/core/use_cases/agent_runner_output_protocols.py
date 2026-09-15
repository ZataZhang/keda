"""输出协议注册表能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.output_protocols` 的
:func:`get_output_protocol_registry`，使 ``api/`` 层只依赖 core。
实现机制（模块加载时经 ``importlib`` 解析 engines 实现）见
:mod:`backend.core.use_cases.agent_runner_factory` 的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_output_protocols_module = importlib.import_module(
    "backend.engines.agent_runner.output_protocols"
)

get_output_protocol_registry = _engines_output_protocols_module.get_output_protocol_registry

__all__ = ["get_output_protocol_registry"]
