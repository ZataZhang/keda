"""仓库级与本地 Agent Runner 配置覆盖模型及加载器。"""

import logging
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.core.shared.models import product_identity
from backend.infrastructure.config.agent_runner_settings_models import (
    AgentRunnerAgentSettings,
    AgentRunnerAutopilotSettings,
    AgentRunnerBacklogSettings,
    AgentRunnerDaemonSettings,
    AgentRunnerDeliberationSettings,
    AgentRunnerGitSettings,
    AgentRunnerInteractiveDecisionSettings,
    AgentRunnerLabelSettings,
    AgentRunnerLifecycleAgentsSettings,
    AgentRunnerLifecyclePresetsSettings,
    AgentRunnerMemorySettings,
    AgentRunnerPostPrSupervisorSettings,
    AgentRunnerPrePrReviewSettings,
    AgentRunnerPresetSettings,
    AgentRunnerPromptSettings,
    AgentRunnerReplSettings,
    AgentRunnerRunnerSettings,
    AgentRunnerSafetySettings,
    AgentRunnerStallSupervisorSettings,
    AgentRunnerValidationSettings,
    AgentRunnerWorktreeSettings,
    AgentSessionSettings,
)
from backend.infrastructure.config.agent_runner_generated_content_settings import (
    AgentRunnerGeneratedContentSettings,
    GENERATED_CONTENT_TARGET_NAMES,
)

logger = logging.getLogger("backend.infrastructure.config.agent_runner_settings")


class AgentRunnerRepositoryMetadataSettings(BaseModel):
    """Repository identity stored in the repository-local KedaCode config."""

    id: str | None = None
    enabled: bool = True
    display_name: str | None = None
    # Optional ``owner/name`` string passed to ``gh pr list --repo`` so the
    # PR column on ``kc issue list`` is populated. Omitting it is allowed
    # — the PR column then stays empty with a one-shot stderr warning.
    github_repo: str | None = None

    @field_validator("github_repo")
    @classmethod
    def _validate_github_repo_format(cls, value: str | None) -> str | None:
        """Reject malformed ``github_repo`` values at config load time.

        Format: ``owner/name`` with non-empty owner / name and no leading
        or trailing slash. ``None`` and empty string are accepted (the
        field is optional).
        """
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "Invalid github_repo: must be a non-empty 'owner/name' string or null."
            )
        if "/" not in value or value.startswith("/") or value.endswith("/"):
            raise ValueError(f"Invalid github_repo {value!r}; expected 'owner/name' format.")
        owner_part, _, name_part = value.partition("/")
        if not owner_part or not name_part or "/" in name_part:
            raise ValueError(f"Invalid github_repo {value!r}; expected 'owner/name' format.")
        return value


class _AgentRunnerRepositoryOverrideSettings(BaseModel):
    """Optional Agent Runner overrides shared by registry and local config."""

    labels: AgentRunnerLabelSettings | None = None
    git: AgentRunnerGitSettings | None = None
    worktree: AgentRunnerWorktreeSettings | None = None
    runner: AgentRunnerRunnerSettings | None = None
    memory: AgentRunnerMemorySettings | None = None
    safety: AgentRunnerSafetySettings | None = None
    autopilot: AgentRunnerAutopilotSettings | None = None
    backlog: AgentRunnerBacklogSettings | None = None
    validation: AgentRunnerValidationSettings | None = None
    prompts: AgentRunnerPromptSettings | None = None
    pre_pr_review: AgentRunnerPrePrReviewSettings | None = None
    post_pr_supervisor: AgentRunnerPostPrSupervisorSettings | None = None
    daemon: AgentRunnerDaemonSettings | None = None
    generated_content: AgentRunnerGeneratedContentSettings | None = None
    interactive_decision: AgentRunnerInteractiveDecisionSettings | None = None
    deliberation: AgentRunnerDeliberationSettings | None = None
    repl: AgentRunnerReplSettings | None = None
    # 仓库级 ``[agent_runner.stall_supervisor]`` 覆盖（在全局段之内，故随本模型）。
    # ``[agent_session]`` 不在此列：它在仓库配置文件里同样是**顶层**段，由
    # :class:`AgentRunnerRepositorySettings.session` 承载，见
    # :func:`load_agent_runner_local_settings`。
    stall_supervisor: AgentRunnerStallSupervisorSettings | None = None
    lifecycle_agents: AgentRunnerLifecycleAgentsSettings | None = None
    presets: dict[str, AgentRunnerPresetSettings] = Field(default_factory=dict)
    lifecycle_presets: AgentRunnerLifecyclePresetsSettings | None = None
    agents: dict[str, AgentRunnerAgentSettings] = Field(default_factory=dict)


class AgentRunnerRepositorySettings(_AgentRunnerRepositoryOverrideSettings):
    """Per-repository Agent Runner configuration overrides."""

    path: str
    id: str | None = None
    enabled: bool = True
    display_name: str | None = None
    # 仓库配置文件里的**顶层** ``[agent_session]`` 段（与全局 config.toml 同名同形）。
    # 刻意不放进共享的 override 模型：那样 ``[agent_runner.session]`` 会被静默接受，
    # 同一个能力出现两种写法。
    session: AgentSessionSettings | None = None
    # Optional ``owner/name`` string passed to ``gh pr list --repo``.
    # Mirrors the same field on ``AgentRunnerRepositoryMetadataSettings``;
    # the local-config loader propagates the value at merge time. See
    # the field validator on the metadata class for the format contract.
    github_repo: str | None = None

    @field_validator("github_repo")
    @classmethod
    def _validate_github_repo_format(cls, value: str | None) -> str | None:
        """Mirror the metadata-level validation for registry entries."""
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "Invalid github_repo: must be a non-empty 'owner/name' string or null."
            )
        if "/" not in value or value.startswith("/") or value.endswith("/"):
            raise ValueError(f"Invalid github_repo {value!r}; expected 'owner/name' format.")
        owner_part, _, name_part = value.partition("/")
        if not owner_part or not name_part or "/" in name_part:
            raise ValueError(f"Invalid github_repo {value!r}; expected 'owner/name' format.")
        return value


class AgentRunnerLocalSettings(_AgentRunnerRepositoryOverrideSettings):
    """Repository-local Agent Runner settings loaded from ``.kedacode.toml``."""

    repository: AgentRunnerRepositoryMetadataSettings = Field(
        default_factory=AgentRunnerRepositoryMetadataSettings
    )


# 已经提示过的 (配置路径, target 名)：daemon 每轮轮询都会重新加载仓库本地配置，
# 同一份配置在一个进程里只提示一次，避免刷屏。
_WARNED_TEMPLATE_PINS: set[tuple[str, tuple[str, ...]]] = set()


def _warn_deprecated_template_mode_pins(
    agent_runner_section: dict[str, Any], local_config_path: Path
) -> None:
    """对显式钉成 ``mode = "template"`` 的 generated_content target 记弃用警告。

    ``template`` 模式已废弃：agent 是默认值，模板渲染只保留为失败兜底。旧版
    ``kc init`` 把当时的默认值 ``template`` 落盘成了显式配置，这类钉值会一直盖过
    新的默认值，所以在加载时指出来，并提示用 ``kc config migrate`` 清理。
    有意使用模板渲染的仓库（自定义了模板）配置仍被接受，只是同样会收到弃用提示。
    """
    generated_content_section = agent_runner_section.get("generated_content")
    if not isinstance(generated_content_section, dict):
        return
    pinned_target_names = tuple(
        target_name
        for target_name in GENERATED_CONTENT_TARGET_NAMES
        if isinstance(generated_content_section.get(target_name), dict)
        and generated_content_section[target_name].get("mode") == "template"
    )
    warning_key = (str(local_config_path), pinned_target_names)
    if not pinned_target_names or warning_key in _WARNED_TEMPLATE_PINS:
        return
    _WARNED_TEMPLATE_PINS.add(warning_key)
    logger.warning(
        '%s pins generated_content.<target>.mode = "template" for: %s. '
        "template mode is deprecated: agent is the default and template rendering stays "
        "only as the failure fallback. If the pin is a leftover of an older `kc init`, "
        "run `kc config migrate` in this repository to drop it.",
        local_config_path,
        ", ".join(pinned_target_names),
    )


def load_agent_runner_local_settings(
    repo_root_path: Path,
) -> AgentRunnerRepositorySettings | None:
    """Load repository-local KedaCode settings from the repository config file.

    只从当前命名的 ``.kedacode.toml`` 读取仓库配置。

    Args:
        repo_root_path: Target Git repository root path.

    Returns:
        Repository settings if the local config exists; otherwise ``None``.

    Raises:
        ValueError: If the local config exists but is invalid.
    """
    resolved_repo_path = repo_root_path.resolve()
    local_config_path = product_identity.effective_repository_config_path(resolved_repo_path)
    if not local_config_path.is_file():
        return None

    try:
        with open(local_config_path, "rb") as local_config_file:
            local_toml_data: dict[str, Any] = tomllib.load(local_config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid KedaCode local config at {local_config_path}: {exc}") from exc

    agent_runner_section = local_toml_data.get("agent_runner")
    if not isinstance(agent_runner_section, dict):
        raise ValueError(
            f"Invalid KedaCode local config at {local_config_path}: "
            "missing [agent_runner] section."
        )

    try:
        local_settings = AgentRunnerLocalSettings(**agent_runner_section)
    except ValidationError as exc:
        raise ValueError(f"Invalid KedaCode local config at {local_config_path}: {exc}") from exc

    _warn_deprecated_template_mode_pins(agent_runner_section, local_config_path)

    repository_metadata = local_settings.repository
    session_section = local_toml_data.get("agent_session")
    if session_section is not None and not isinstance(session_section, dict):
        raise ValueError(
            f"Invalid KedaCode local config at {local_config_path}: "
            "[agent_session] must be a table."
        )
    try:
        local_session_settings = (
            AgentSessionSettings(**session_section) if session_section is not None else None
        )
    except ValidationError as exc:
        raise ValueError(
            f"Invalid KedaCode local config at {local_config_path}: "
            f"invalid [agent_session] section: {exc}"
        ) from exc
    return AgentRunnerRepositorySettings(
        path=str(resolved_repo_path),
        id=repository_metadata.id,
        enabled=repository_metadata.enabled,
        display_name=repository_metadata.display_name,
        github_repo=repository_metadata.github_repo,
        labels=local_settings.labels,
        agents=local_settings.agents,
        git=local_settings.git,
        worktree=local_settings.worktree,
        runner=local_settings.runner,
        safety=local_settings.safety,
        autopilot=local_settings.autopilot,
        backlog=local_settings.backlog,
        validation=local_settings.validation,
        prompts=local_settings.prompts,
        pre_pr_review=local_settings.pre_pr_review,
        post_pr_supervisor=local_settings.post_pr_supervisor,
        daemon=local_settings.daemon,
        generated_content=local_settings.generated_content,
        interactive_decision=local_settings.interactive_decision,
        deliberation=local_settings.deliberation,
        repl=local_settings.repl,
        session=local_session_settings,
        stall_supervisor=local_settings.stall_supervisor,
        lifecycle_agents=local_settings.lifecycle_agents,
        presets=local_settings.presets,
        lifecycle_presets=local_settings.lifecycle_presets,
    )


__all__ = (
    "AgentRunnerRepositoryMetadataSettings",
    "AgentRunnerRepositorySettings",
    "AgentRunnerLocalSettings",
    "load_agent_runner_local_settings",
)
