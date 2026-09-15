"""仓库接管（takeover）能力的 core 编排入口（薄 facade）。

转发 :mod:`backend.engines.agent_runner.takeover` 与
:mod:`backend.engines.agent_runner.takeover_interactive` 的接管流程
能力，使 ``api/`` 层只依赖 core。实现机制（模块加载时经 ``importlib``
解析 engines 实现）见 :mod:`backend.core.use_cases.agent_runner_factory`
的模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_takeover_module = importlib.import_module("backend.engines.agent_runner.takeover")
_engines_takeover_interactive_module = importlib.import_module(
    "backend.engines.agent_runner.takeover_interactive"
)

build_takeover_options = _engines_takeover_module.build_takeover_options
execute_takeover = _engines_takeover_module.execute_takeover
filter_unregistered_candidates = _engines_takeover_module.filter_unregistered_candidates
list_github_repositories = _engines_takeover_module.list_github_repositories
parse_selected_repositories = _engines_takeover_module.parse_selected_repositories
upsert_repository = _engines_takeover_module.upsert_repository
select_repositories_interactive = (
    _engines_takeover_interactive_module.select_repositories_interactive
)

__all__ = [
    "build_takeover_options",
    "execute_takeover",
    "filter_unregistered_candidates",
    "list_github_repositories",
    "parse_selected_repositories",
    "select_repositories_interactive",
    "upsert_repository",
]
