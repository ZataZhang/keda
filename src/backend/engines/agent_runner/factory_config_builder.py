"""Agent Runner config builders.

Module-level functions that convert pydantic-settings models from
:mod:`backend.infrastructure.config.settings` into the frozen dataclasses
defined in :mod:`backend.core.shared.models.agent_runner`. Split out of
:mod:`backend.engines.agent_runner.factory` so the factories sub-package
can stay focused on object construction.
"""

from __future__ import annotations

from backend.core.shared.models.agent_deliberation import (
    DeliberationAgentProfile,
    DeliberationConfig,
)
from backend.core.shared.models.agent_runner import (
    AppConfig,
    AutopilotConfig,
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    GitConfig,
    LabelConfig,
    MemoryConfig,
    PostPrSupervisorConfig,
    PrePrReviewConfig,
    PromptConfig,
    RunnerConfig,
    SafetyConfig,
    ValidationConfig,
    WorktreeConfig,
)
from backend.core.shared.models.agent_spec import (
    AGENT_PROFILES,
    BUILTIN_AGENT_SPECS,
    PROMPT_DELIVERIES,
    AgentProfileSpec,
    AgentSpec,
)
from backend.infrastructure.config.settings import (
    AgentRunnerAgentProfileSettings,
    AgentRunnerAgentSettings,
    AgentRunnerDeliberationSettings,
    AgentRunnerGeneratedContentSettings,
    AgentRunnerGeneratedContentTargetSettings,
    AgentRunnerMemorySettings,
    AgentRunnerReplSettings,
    AgentRunnerSettings,
)
from backend.core.shared.models.agent_decision import (
    InteractiveDecisionConfig,
    ReplConfig,
)


def _build_generated_content_target_config(
    target_settings: AgentRunnerGeneratedContentTargetSettings,
) -> GeneratedContentTargetConfig:
    """Convert pydantic target settings to frozen core config."""
    return GeneratedContentTargetConfig(
        enabled=target_settings.enabled,
        mode=target_settings.mode,
        output=target_settings.output,
        title_template=target_settings.title_template,
        body_template=target_settings.body_template,
        agent=target_settings.agent,
        timeout_seconds=target_settings.timeout_seconds,
        prompt=target_settings.prompt,
        include_commit_log=target_settings.include_commit_log,
        include_diff_stat=target_settings.include_diff_stat,
    )


def _build_generated_content_config(
    gc_settings: AgentRunnerGeneratedContentSettings,
) -> GeneratedContentConfig:
    """Convert pydantic generated-content settings to frozen core config."""
    return GeneratedContentConfig(
        enabled=gc_settings.enabled,
        fallback=gc_settings.fallback,
        max_input_chars=gc_settings.max_input_chars,
        default_agent=gc_settings.default_agent,
        issue_from_prd=_build_generated_content_target_config(gc_settings.issue_from_prd),
        draft_pr=_build_generated_content_target_config(gc_settings.draft_pr),
        prd_from_issue=_build_generated_content_target_config(gc_settings.prd_from_issue),
    )


def _build_deliberation_config(
    deliberation_settings: AgentRunnerDeliberationSettings,
) -> DeliberationConfig:
    """Convert pydantic deliberation settings to frozen core config."""
    profiles = tuple(
        DeliberationAgentProfile(
            profile_id=profile_id,
            agent=profile.agent,
            role=profile.role,
            behavior_prompt=profile.behavior_prompt,
        )
        for profile_id, profile in deliberation_settings.profiles.items()
    )
    return DeliberationConfig(
        default_rounds=deliberation_settings.default_rounds,
        default_synthesizer=deliberation_settings.default_synthesizer,
        default_output_dir=deliberation_settings.default_output_dir,
        continue_on_agent_error=deliberation_settings.continue_on_agent_error,
        agent_failure_timeout_seconds=deliberation_settings.agent_failure_timeout_seconds,
        stale_rounds_before_hint=deliberation_settings.stale_rounds_before_hint,
        profiles=profiles,
    )


def _build_memory_config(
    memory_settings: AgentRunnerMemorySettings,
) -> MemoryConfig:
    """Convert pydantic memory settings to frozen core config."""
    return MemoryConfig(
        enabled=memory_settings.enabled,
        base_dir=memory_settings.base_dir,
        skill_drafts_dir=memory_settings.skill_drafts_dir,
        promoted_skills_dirs=tuple(memory_settings.promoted_skills_dirs),
        top_k_skills=memory_settings.top_k_skills,
        top_k_facts=memory_settings.top_k_facts,
        auto_promote=memory_settings.auto_promote,
        auto_promote_threshold=memory_settings.auto_promote_threshold,
        auto_promote_min_success_rate=(memory_settings.auto_promote_min_success_rate),
    )


def _build_repl_config(repl_settings: AgentRunnerReplSettings) -> ReplConfig:
    """Convert pydantic REPL settings to frozen core config."""
    return ReplConfig(
        enabled=repl_settings.enabled,
        default_agent=repl_settings.default_agent,
        default_output_dir=repl_settings.default_output_dir,
        max_context_chars=repl_settings.max_context_chars,
        agent_timeout_seconds=repl_settings.agent_timeout_seconds,
        auto_confirm_commands=tuple(repl_settings.auto_confirm_commands),
        confirm_commands=tuple(repl_settings.confirm_commands),
    )


def _merge_profile_settings(
    base_profile: AgentProfileSpec | None,
    profile_settings: AgentRunnerAgentProfileSettings | None,
    *,
    agent_name: str,
    profile_name: str,
) -> AgentProfileSpec:
    """把单个用途的稀疏配置覆盖合并到基础 profile spec 上。

    Args:
        base_profile: 内置（或全局层）基础 spec；全新用途时为 ``None``。
        profile_settings: 配置声明；``None`` 时直接返回基础 spec。
        agent_name: 报错上下文用的 agent 名。
        profile_name: 报错上下文用的用途名。

    Returns:
        合并后的 :class:`AgentProfileSpec`。

    Raises:
        ValueError: 用途名不在闭集、``prompt_delivery`` 非法、或全新用途
            缺 ``prompt_delivery`` 等关键字段。
    """
    if profile_name not in AGENT_PROFILES:
        raise ValueError(
            f"agents.{agent_name}.profiles.{profile_name}: unknown profile "
            f"'{profile_name}'. Valid profiles: {', '.join(AGENT_PROFILES)}."
        )
    if profile_settings is None:
        if base_profile is None:
            raise ValueError(
                f"agents.{agent_name}.profiles.{profile_name} is declared empty and "
                f"has no built-in default; declare at least prompt_delivery."
            )
        return base_profile
    declared = profile_settings.model_fields_set
    prompt_delivery = profile_settings.prompt_delivery
    if prompt_delivery is None:
        if base_profile is None:
            raise ValueError(
                f"agents.{agent_name}.profiles.{profile_name}: prompt_delivery is "
                f"required for a new profile. Valid values: {', '.join(PROMPT_DELIVERIES)}."
            )
        prompt_delivery = base_profile.prompt_delivery
    elif prompt_delivery not in PROMPT_DELIVERIES:
        raise ValueError(
            f"agents.{agent_name}.profiles.{profile_name}: invalid prompt_delivery "
            f"'{prompt_delivery}'. Valid values: {', '.join(PROMPT_DELIVERIES)}."
        )
    if prompt_delivery == "flag" and profile_settings.prompt_flag is None:
        base_prompt_flag = base_profile.prompt_flag if base_profile is not None else None
    else:
        base_prompt_flag = None
    prompt_flag = profile_settings.prompt_flag if "prompt_flag" in declared else base_prompt_flag
    if prompt_delivery == "flag" and not prompt_flag:
        raise ValueError(
            f"agents.{agent_name}.profiles.{profile_name}: prompt_delivery='flag' "
            f"requires prompt_flag."
        )
    return AgentProfileSpec(
        args=tuple(
            profile_settings.args
            if profile_settings.args is not None
            else (base_profile.args if base_profile is not None else ())
        ),
        prompt_flag=prompt_flag,
        prompt_delivery=prompt_delivery,
        output_protocol=(
            profile_settings.output_protocol
            if profile_settings.output_protocol is not None
            else (base_profile.output_protocol if base_profile is not None else "plain")
        ),
        tail_args=tuple(
            profile_settings.tail_args
            if profile_settings.tail_args is not None
            else (base_profile.tail_args if base_profile is not None else ())
        ),
        expand=tuple(
            profile_settings.expand
            if profile_settings.expand is not None
            else (base_profile.expand if base_profile is not None else ())
        ),
        read_only=(
            profile_settings.read_only
            if profile_settings.read_only is not None
            else (base_profile.read_only if base_profile is not None else False)
        ),
    )


def _merge_agent_settings(
    agent_name: str,
    agent_settings: AgentRunnerAgentSettings,
    base_spec: AgentSpec | None,
) -> AgentSpec:
    """把一个 ``[agent_runner.agents.<name>]`` 声明合并到基础 spec 上。

    覆盖既有 agent 时未声明字段逐字段回落内置默认；注册全新 agent 时
    ``bin`` / ``label`` 必填。
    """
    if base_spec is None and (agent_settings.bin is None or agent_settings.label is None):
        missing_fields = ", ".join(
            field_name
            for field_name, value in (
                ("bin", agent_settings.bin),
                ("label", agent_settings.label),
            )
            if value is None
        )
        raise ValueError(f"agents.{agent_name}: new agent registration requires {missing_fields}.")
    label_color = agent_settings.label_color
    if label_color is not None and (
        len(label_color) != 6 or any(c not in "0123456789abcdefABCDEF" for c in label_color)
    ):
        raise ValueError(
            f"agents.{agent_name}: label_color must be 6 hex digits (without '#'); "
            f"got {label_color!r}."
        )
    profiles: dict[str, AgentProfileSpec] = {}
    for profile_name in AGENT_PROFILES:
        profile_settings = agent_settings.profiles.get(profile_name)
        # 全新 agent 只要求"四种用途至少声明一种"：未声明的用途直接跳过，
        # 不触发"声明为空且无内置默认"的报错（那是显式写空段 `[...profiles.x]` 的情况）。
        if base_spec is None and profile_settings is None:
            continue
        merged_profile = _merge_profile_settings(
            base_spec.profiles.get(profile_name) if base_spec is not None else None,
            profile_settings,
            agent_name=agent_name,
            profile_name=profile_name,
        )
        if merged_profile is not None:
            profiles[profile_name] = merged_profile
    for profile_name in agent_settings.profiles:
        if profile_name not in AGENT_PROFILES:
            _merge_profile_settings(
                None,
                agent_settings.profiles[profile_name],
                agent_name=agent_name,
                profile_name=profile_name,
            )
    return AgentSpec(
        bin=agent_settings.bin
        if agent_settings.bin is not None
        else (base_spec.bin if base_spec else agent_settings.bin),  # type: ignore[union-attr]
        label=agent_settings.label
        if agent_settings.label is not None
        else (base_spec.label if base_spec else agent_settings.label),  # type: ignore[union-attr]
        label_color=label_color
        if label_color is not None
        else (base_spec.label_color if base_spec else "5319E7"),
        label_description=(
            agent_settings.label_description
            if agent_settings.label_description is not None
            else (base_spec.label_description if base_spec else "")
        ),
        auth_home=(
            agent_settings.auth_home
            if agent_settings.auth_home is not None
            else (base_spec.auth_home if base_spec else None)
        ),
        auth_include=tuple(
            agent_settings.auth_include
            if agent_settings.auth_include is not None
            else (base_spec.auth_include if base_spec else ())
        ),
        auth_exclude=tuple(
            agent_settings.auth_exclude
            if agent_settings.auth_exclude is not None
            else (base_spec.auth_exclude if base_spec else ())
        ),
        project_skills_dir=(
            agent_settings.project_skills_dir
            if agent_settings.project_skills_dir is not None
            else (base_spec.project_skills_dir if base_spec else None)
        ),
        profiles=profiles,
    )


def build_agent_registry_from_settings(
    agent_settings: dict[str, AgentRunnerAgentSettings],
    *,
    base_registry: dict[str, AgentSpec] | None = None,
) -> dict[str, AgentSpec]:
    """从配置的 ``agents`` 声明构建完整 agent 注册表。

    内置默认（``BUILTIN_AGENT_SPECS`` 或调用方给定的基础注册表）在前，
    配置声明逐字段覆盖在后；覆盖既有 agent 保持其原注册顺序，新 agent
    追加在末尾（``choose_agent`` 的标签匹配按先到先得）。
    """
    registry = dict(base_registry if base_registry is not None else BUILTIN_AGENT_SPECS)
    for agent_name, agent_settings in agent_settings.items():
        registry[agent_name] = _merge_agent_settings(
            agent_name, agent_settings, registry.get(agent_name)
        )
    return registry


def build_label_config_from_settings(
    label_settings,
    agent_registry: dict[str, AgentSpec],
) -> LabelConfig:
    """从 agent 注册表派生 ``LabelConfig``，并应用旧版 labels 键的兼容覆盖。

    ``agent_labels`` 由注册表按注册顺序派生（agent 名 -> 路由标签）；
    ``[agent_runner.labels]`` 的 ``codex`` / ``claude`` / ``kimi`` 旧键
    仍作为对应 agent 标签的覆盖来源（FR-12）。
    """
    agent_labels = {name: spec.label for name, spec in agent_registry.items()}
    agent_labels.update(label_settings.legacy_agent_label_overrides())
    return LabelConfig(
        ready=label_settings.ready,
        running=label_settings.running,
        supervising=label_settings.supervising,
        review=label_settings.review,
        failed=label_settings.failed,
        blocked=label_settings.blocked,
        waiting=label_settings.waiting,
        validation_pending=label_settings.validation_pending,
        validation_passed=label_settings.validation_passed,
        verifier_passed=label_settings.verifier_passed,
        group_prefix=label_settings.group_prefix,
        rework_prd=label_settings.rework_prd,
        deliberate=label_settings.deliberate,
        agent_labels=agent_labels,
    )


def build_app_config_from_settings(
    agent_runner_settings: AgentRunnerSettings,
) -> AppConfig:
    """Convert pydantic-settings ``AgentRunnerSettings`` to frozen ``AppConfig``."""
    label_settings = agent_runner_settings.labels
    git_settings = agent_runner_settings.git
    worktree_settings = agent_runner_settings.worktree
    runner_settings = agent_runner_settings.runner
    memory_settings = agent_runner_settings.memory
    safety_settings = agent_runner_settings.safety
    autopilot_settings = agent_runner_settings.autopilot
    validation_settings = agent_runner_settings.validation
    prompt_settings = agent_runner_settings.prompts

    pre_pr = agent_runner_settings.pre_pr_review
    post_supervisor = agent_runner_settings.post_pr_supervisor
    generated_content = _build_generated_content_config(agent_runner_settings.generated_content)
    interactive_decision = agent_runner_settings.interactive_decision
    repl = _build_repl_config(agent_runner_settings.repl)
    deliberation = _build_deliberation_config(agent_runner_settings.deliberation)
    agent_registry = build_agent_registry_from_settings(agent_runner_settings.agents)
    labels = build_label_config_from_settings(label_settings, agent_registry)

    return AppConfig(
        agents=agent_registry,
        labels=labels,
        git=GitConfig(
            remote=git_settings.remote,
            base_branch=git_settings.base_branch,
        ),
        worktree=WorktreeConfig(
            create_command=worktree_settings.create_command,
            reuse_command=worktree_settings.reuse_command,
            path_command=worktree_settings.path_command,
            provision_database=worktree_settings.provision_database,
            base_branch=git_settings.base_branch,
        ),
        runner=RunnerConfig(
            max_issues=runner_settings.max_issues,
            max_concurrent_issues=runner_settings.max_concurrent_issues,
            default_agent=runner_settings.default_agent,
            max_recovery_attempts=runner_settings.max_recovery_attempts,
            recovery_retry_delay_seconds=runner_settings.recovery_retry_delay_seconds,
            agent_fallback_order=tuple(runner_settings.agent_fallback_order),
            max_agent_switches=runner_settings.max_agent_switches,
            transient_retry_attempts=runner_settings.transient_retry_attempts,
            transient_retry_delay_seconds=runner_settings.transient_retry_delay_seconds,
            timeout_seconds=runner_settings.timeout_seconds,
            fix_agent_enabled=runner_settings.fix_agent_enabled,
            fix_timeout_seconds=runner_settings.fix_timeout_seconds,
            recovery_timeout_seconds=runner_settings.recovery_timeout_seconds,
            closeout_agent_enabled=runner_settings.closeout_agent_enabled,
            closeout_timeout_seconds=runner_settings.closeout_timeout_seconds,
            closeout_visual_timeout_seconds=runner_settings.closeout_visual_timeout_seconds,
            inactivity_timeout_seconds=runner_settings.inactivity_timeout_seconds,
            verification_commands=tuple(runner_settings.verification_commands),
            pre_commit_verification_command=runner_settings.pre_commit_verification_command,
        ),
        memory=_build_memory_config(memory_settings),
        safety=SafetyConfig(
            auto_merge=safety_settings.auto_merge,
            forbidden_path_patterns=tuple(safety_settings.forbidden_path_patterns),
        ),
        autopilot=AutopilotConfig(
            enabled=autopilot_settings.enabled,
            merge_method=autopilot_settings.merge_method,
            require_verifier_pass=autopilot_settings.require_verifier_pass,
            auto_sign_off=autopilot_settings.auto_sign_off,
            merge_check_timeout_seconds=autopilot_settings.merge_check_timeout_seconds,
        ),
        validation=ValidationConfig(
            enabled=validation_settings.enabled,
            evidence_dir=validation_settings.evidence_dir,
            branch_prefix=validation_settings.branch_prefix,
            evidence_format_check=validation_settings.evidence_format_check,
            parse_evidence_format_with_agent=validation_settings.parse_evidence_format_with_agent,
            language=validation_settings.language,
            structured_evidence=validation_settings.structured_evidence,
            require_negative_control=validation_settings.require_negative_control,
            reexecute_commands=validation_settings.reexecute_commands,
            reexecute_timeout_seconds=validation_settings.reexecute_timeout_seconds,
            reexecute_cache_enabled=validation_settings.reexecute_cache_enabled,
            verifier_enabled=validation_settings.verifier_enabled,
            verifier_agent=validation_settings.verifier_agent,
            verifier_timeout_seconds=validation_settings.verifier_timeout_seconds,
            verifier_inactivity_timeout_seconds=(
                validation_settings.verifier_inactivity_timeout_seconds
            ),
            artifact_health_enabled=validation_settings.artifact_health_enabled,
            frontend_visual_evidence_required=validation_settings.frontend_visual_evidence_required,
            frontend_paths=tuple(validation_settings.frontend_paths),
        ),
        prompts=PromptConfig(
            default_phase=prompt_settings.default_phase,
            phases=dict(prompt_settings.phases),
        ),
        pre_pr_review=PrePrReviewConfig(
            enabled=pre_pr.enabled,
            review_agent=pre_pr.review_agent,
            allow_same_agent=pre_pr.allow_same_agent,
            max_attempts=pre_pr.max_attempts,
            timeout_seconds=pre_pr.timeout_seconds,
            commit_request_reminder_attempts=pre_pr.commit_request_reminder_attempts,
            review_prompt_template=tuple(pre_pr.review_prompt_template),
        ),
        post_pr_supervisor=PostPrSupervisorConfig(
            enabled=post_supervisor.enabled,
            supervisor_agent=post_supervisor.supervisor_agent,
            max_repair_attempts=post_supervisor.max_repair_attempts,
            max_agent_crash_retries=post_supervisor.max_agent_crash_retries,
            crash_retry_initial_backoff_seconds=(
                post_supervisor.crash_retry_initial_backoff_seconds
            ),
            crash_retry_max_backoff_seconds=(post_supervisor.crash_retry_max_backoff_seconds),
            key_paths=tuple(post_supervisor.key_paths),
            max_diff_chars=post_supervisor.max_diff_chars,
            previous_findings_injection_enabled=(
                post_supervisor.previous_findings_injection_enabled
            ),
            findings_artifact_dir=post_supervisor.findings_artifact_dir,
        ),
        generated_content=generated_content,
        interactive_decision=InteractiveDecisionConfig(
            enabled=interactive_decision.enabled,
            default_agent=interactive_decision.default_agent,
            default_output_dir=interactive_decision.default_output_dir,
            planner_timeout_seconds=interactive_decision.planner_timeout_seconds,
            max_context_chars=interactive_decision.max_context_chars,
            allow_execute_yes=interactive_decision.allow_execute_yes,
        ),
        repl=repl,
        deliberation=deliberation,
    )


__all__ = [
    "build_app_config_from_settings",
]
