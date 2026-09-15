"""agent runner 配置装配 / 工厂能力的 core 编排入口（薄 facade）。

``api/`` 层历史上直接 ``import backend.engines.agent_runner.factory`` 拿到
``create_*`` 装配函数、settings 加载器与共享 ``logger``。按四层依赖方向
（``api → core → engines → infrastructure``），api 只允许依赖 core，
因此本模块作为 core 侧的统一入口转发该 engines 能力。

实现机制遵循 :mod:`backend.core.agent.memory._composition` 先例：core
不得静态 ``import backend.engines.*``（``hooks/shared/check_architecture.py``
的 AST 检查与 ``tests/test_agent_runner_container.py`` 的守门测试共同
约束），故在模块加载时经 ``importlib.import_module`` 解析 engines 实现
并绑定为模块级名字。调用方 ``from ... import X`` 的绑定时机与被 patch
语义和迁移前完全一致——engines 模块仍在 api 模块 import 时被加载，
``patch("backend.api.<module>.X")`` 依旧生效。

本模块只做层次归位，不新增编排逻辑；各名字语义以
:mod:`backend.engines.agent_runner.factory` 为准。
"""

from __future__ import annotations

import importlib

_engines_factory_module = importlib.import_module("backend.engines.agent_runner.factory")

logger = _engines_factory_module.logger
build_app_config = _engines_factory_module.build_app_config
build_app_config_from_settings = _engines_factory_module.build_app_config_from_settings
create_console_store = _engines_factory_module.create_console_store
create_content_generator = _engines_factory_module.create_content_generator
create_event_sink = _engines_factory_module.create_event_sink
create_github_client = _engines_factory_module.create_github_client
create_loop_clock = _engines_factory_module.create_loop_clock
create_loop_state_store = _engines_factory_module.create_loop_state_store
create_planner_runner = _engines_factory_module.create_planner_runner
create_process_runner = _engines_factory_module.create_process_runner
create_process_supervisor = _engines_factory_module.create_process_supervisor
create_registry_editor = _engines_factory_module.create_registry_editor
create_repl_command_executor = _engines_factory_module.create_repl_command_executor
create_roadmap_store = _engines_factory_module.create_roadmap_store
create_transcript_runner = _engines_factory_module.create_transcript_runner
find_repository_match_for_path = _engines_factory_module.find_repository_match_for_path
get_agent_runner_settings = _engines_factory_module.get_agent_runner_settings
get_agent_runner_status_data = _engines_factory_module.get_agent_runner_status_data
load_fresh_agent_runner_settings = _engines_factory_module.load_fresh_agent_runner_settings
resolve_console_spawn_cwd = _engines_factory_module.resolve_console_spawn_cwd
resolve_issue_from_prd_target = _engines_factory_module.resolve_issue_from_prd_target
resolve_project_root_path = _engines_factory_module.resolve_project_root_path
resolve_registry_config_toml_path = _engines_factory_module.resolve_registry_config_toml_path
resolve_repository_targets = _engines_factory_module.resolve_repository_targets
resolve_repository_targets_with_diagnostics = (
    _engines_factory_module.resolve_repository_targets_with_diagnostics
)
write_deliberation_outputs = _engines_factory_module.write_deliberation_outputs

__all__ = [
    "build_app_config",
    "build_app_config_from_settings",
    "create_console_store",
    "create_content_generator",
    "create_event_sink",
    "create_github_client",
    "create_loop_clock",
    "create_loop_state_store",
    "create_planner_runner",
    "create_process_runner",
    "create_process_supervisor",
    "create_registry_editor",
    "create_repl_command_executor",
    "create_roadmap_store",
    "create_transcript_runner",
    "find_repository_match_for_path",
    "get_agent_runner_settings",
    "get_agent_runner_status_data",
    "load_fresh_agent_runner_settings",
    "logger",
    "resolve_console_spawn_cwd",
    "resolve_issue_from_prd_target",
    "resolve_project_root_path",
    "resolve_registry_config_toml_path",
    "resolve_repository_targets",
    "resolve_repository_targets_with_diagnostics",
    "write_deliberation_outputs",
]
