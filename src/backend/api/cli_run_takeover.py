"""``iar run --takeover`` 编排的 api 侧入口（engines 实现的薄转发）。

四层依赖方向要求 api 只直接依赖 core，而接管编排要同时触碰 core 用例
（reclaim）与 infrastructure 监管器，因此实现在
:mod:`backend.engines.agent_runner.run_takeover`（engines 允许依赖 core 与
infrastructure），本模块按 :mod:`backend.core.use_cases.agent_runner_factory`
的既有先例经 ``importlib`` 在模块加载时解析并绑定名字，保持
``patch("backend.api.cli_run_takeover.X")`` 的 patch 语义不变。
"""

from __future__ import annotations

import importlib

_module = importlib.import_module("backend.engines.agent_runner.run_takeover")

DEFAULT_STOP_TIMEOUT_SECONDS = _module.DEFAULT_STOP_TIMEOUT_SECONDS
DaemonTakeoverResult = _module.DaemonTakeoverResult
collect_descendant_group_ids = _module.collect_descendant_group_ids
take_over_daemon = _module.take_over_daemon

__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "DaemonTakeoverResult",
    "collect_descendant_group_ids",
    "take_over_daemon",
]
