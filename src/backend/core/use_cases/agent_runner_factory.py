"""agent runner 配置装配 / 工厂能力的 core 编排入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 只依赖
``core/``，本模块把下层实现转发给上层。``core/`` 静态依赖 ``engines/`` 是
允许的（见 ``docs/ai-standards/architecture.md``），不再用 ``importlib``
动态加载：依赖因此对检查器、IDE 与类型检查都可见。本模块只做层次归位，
不新增编排逻辑；各名字语义以
:mod:`backend.engines.agent_runner.factory` 为准。
"""

from __future__ import annotations

from backend.engines.agent_runner.factory import (
    build_app_config,
    build_app_config_from_settings,
    collect_repository_own_presets,
    create_backlog_store,
    create_console_store,
    create_content_generator,
    create_event_sink,
    create_foreground_session_launcher,
    create_github_api_budget,
    create_github_client,
    create_lifecycle_settings_editor,
    create_loop_clock,
    create_loop_state_store,
    create_machine_identity_store,
    create_planner_runner,
    create_preview_process_manager,
    create_process_runner,
    create_process_supervisor,
    create_registry_editor,
    create_repl_command_executor,
    create_repository_autopilot_settings_editor,
    create_transcript_runner,
    daily_log_path,
    ensure_standard_executable_path,
    find_repository_match_for_path,
    get_agent_runner_settings,
    get_agent_runner_status_data,
    load_fresh_agent_runner_settings,
    load_local_override_lifecycle_preset_stages,
    logger,
    prepare_native_session_plan,
    resolve_console_spawn_cwd,
    resolve_issue_from_prd_target,
    resolve_project_root_path,
    resolve_registry_config_toml_path,
    resolve_repository_targets,
    resolve_repository_targets_with_diagnostics,
    write_deliberation_outputs,
)


__all__ = [
    "build_app_config",
    "build_app_config_from_settings",
    "collect_repository_own_presets",
    "create_console_store",
    "create_content_generator",
    "create_event_sink",
    "create_foreground_session_launcher",
    "create_github_client",
    "create_machine_identity_store",
    "create_lifecycle_settings_editor",
    "create_github_api_budget",
    "create_loop_clock",
    "create_loop_state_store",
    "create_planner_runner",
    "create_preview_process_manager",
    "create_process_runner",
    "create_process_supervisor",
    "create_registry_editor",
    "create_repository_autopilot_settings_editor",
    "create_repl_command_executor",
    "create_backlog_store",
    "create_transcript_runner",
    "daily_log_path",
    "ensure_standard_executable_path",
    "find_repository_match_for_path",
    "get_agent_runner_settings",
    "get_agent_runner_status_data",
    "load_fresh_agent_runner_settings",
    "load_local_override_lifecycle_preset_stages",
    "logger",
    "prepare_native_session_plan",
    "resolve_console_spawn_cwd",
    "resolve_issue_from_prd_target",
    "resolve_project_root_path",
    "resolve_registry_config_toml_path",
    "resolve_repository_targets",
    "resolve_repository_targets_with_diagnostics",
    "write_deliberation_outputs",
]
