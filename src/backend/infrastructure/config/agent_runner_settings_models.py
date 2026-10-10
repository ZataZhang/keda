"""Agent Runner 基础设置模型。"""

import sys
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_session import require_loopback_preview_url
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    normalize_lifecycle_agent_value,
)


class AgentRunnerLifecycleAgentsSettings(BaseModel):
    """``[agent_runner.lifecycle_agents]`` 段：九个生命周期各选一个 agent。

    键是闭集（见 :data:`LIFECYCLE_AGENT_KEYS`），未知键在加载期报错；取值是
    ``auto``（按该阶段既有语义路由）、``executor``（跟随实现者，**仅**
    ``fix`` / ``closeout`` 合法）或已注册 agent 名（是否注册由解析期校验，
    配置层只校验形状）。未声明的键继续回落到既有散落配置键与内置默认。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    implementation: str | None = None
    fix: str | None = None
    closeout: str | None = None
    verifier: str | None = None
    review: str | None = None
    supervisor: str | None = None
    planner: str | None = None
    content_generation: str | None = None
    deliberate: str | None = None

    @field_validator("*")
    @classmethod
    def _reject_blank_values(cls, value: str | None) -> str | None:
        """拒绝空字符串；``None`` 表示未声明。"""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("lifecycle agent value must be a non-empty string")
        return normalized

    @model_validator(mode="after")
    def _validate_stage_specific_values(self) -> "AgentRunnerLifecycleAgentsSettings":
        """按阶段校验 ``auto`` / ``executor`` 的合法性。

        ``executor`` 只对 fix / closeout 合法（其余键会造成循环引用），
        ``auto`` 只对既有实现了 auto 语义的阶段合法。
        """
        for lifecycle_key in LIFECYCLE_AGENT_KEYS:
            raw_value = getattr(self, lifecycle_key)
            if raw_value is None:
                continue
            normalized = normalize_lifecycle_agent_value(raw_value)
            if normalized == LIFECYCLE_AGENT_EXECUTOR and lifecycle_key not in (
                LIFECYCLE_AGENT_EXECUTOR_KEYS
            ):
                raise ValueError(
                    f"lifecycle_agents.{lifecycle_key}: 'executor' is only valid for "
                    f"{', '.join(sorted(LIFECYCLE_AGENT_EXECUTOR_KEYS))}."
                )
            if (
                normalized == LIFECYCLE_AGENT_AUTO
                and lifecycle_key not in LIFECYCLE_AGENT_AUTO_KEYS
            ):
                raise ValueError(
                    f"lifecycle_agents.{lifecycle_key}: 'auto' is not a valid value for this "
                    "stage (the stage has no auto routing semantics)."
                )
        return self


class AgentRunnerLabelSettings(BaseModel):
    """GitHub labels used as runner queue state.

    ``codex`` / ``claude`` / ``kimi`` 是旧版 `[agent_runner.labels]` 的
    agent 路由键，作为**兼容覆盖来源**保留：``None`` 表示未覆盖，
    路由标签由 agent 注册表（``[agent_runner.agents.<name>].label``）
    提供（FR-12：既有仓库的这三个键继续生效，优先级与今天一致）。
    """

    ready: str = "agent/ready"
    running: str = "agent/running"
    supervising: str = "agent/supervising"
    review: str = "agent/review"
    failed: str = "agent/failed"
    blocked: str = "agent/blocked"
    waiting: str = "agent/waiting"
    validation_pending: str = "validation/pending"
    validation_passed: str = "validation/passed"
    verifier_passed: str = "validation/verifier-passed"
    codex: str | None = None
    claude: str | None = None
    kimi: str | None = None
    rework_prd: str = "agent/rework-prd"
    deliberate: str = "agent/deliberate"
    direct_pr: str = "direct-pr"
    prd_auto_merge: str = "prd-auto-merge"

    def legacy_agent_label_overrides(self) -> dict[str, str]:
        """返回旧版三个 agent 键中**显式设置**的标签覆盖（供注册表合并）。"""
        overrides: dict[str, str] = {}
        for agent_name, override_label in (
            ("codex", self.codex),
            ("claude", self.claude),
            ("kimi", self.kimi),
        ):
            if override_label is not None:
                overrides[agent_name] = override_label
        return overrides


class AgentRunnerAgentProfileSettings(BaseModel):
    """`[agent_runner.agents.<name>.profiles.<profile>]` 的稀疏覆盖。

    全部字段可选：未设置的字段回落到内置默认 spec（对既有 agent）
    或触发配置校验错误（对全新 agent 缺关键字段时）。
    """

    args: list[str] | None = None
    prompt_flag: str | None = None
    prompt_delivery: str | None = None
    output_protocol: str | None = None
    tail_args: list[str] | None = None
    resume_tail_args: list[str] | None = None
    expand: list[str] | None = None
    read_only: bool | None = None


class AgentRunnerAgentSettings(BaseModel):
    """`[agent_runner.agents.<name>]` 注册块。

    覆盖既有 agent 时全部字段可选（逐字段回落内置默认）；注册全新
    agent 时 ``bin`` 与 ``label`` 必填，四种用途 profile 至少声明一种。
    ``model_args`` / ``reasoning_effort_args`` 是模型参数 argv 模板
    （含 ``{model}`` / ``{effort}`` 占位符）；为空表示该 agent 未声明
    模型选择语法，命中模型绑定时 fail-fast。``resume_args`` 是通用续传模板；
    ``resume_tail_args`` 是 profile 级特殊续传尾部模板。
    """

    bin: str | None = None
    label: str | None = None
    label_color: str | None = None
    label_description: str | None = None
    auth_home: str | None = None
    auth_include: list[str] | None = None
    auth_exclude: list[str] | None = None
    project_skills_dir: str | None = None
    model_args: list[str] | None = None
    reasoning_effort_args: list[str] | None = None
    # 会话续传能力声明：``supports_resume`` 为真时，恢复轮次按 profile 注入
    # ``resume_args`` 或 ``resume_tail_args``（含 ``{session_id}`` 占位符）。
    supports_resume: bool | None = None
    resume_args: list[str] | None = None
    profiles: dict[str, AgentRunnerAgentProfileSettings] = Field(default_factory=dict)
    # 原生交互式 TUI 声明（`[agent_runner.agents.<name>.interactive]`）。与
    # ``profiles.*`` 分开而不是第五个用途：交互形态把 stdin/stdout/TTY 整个交给
    # provider，没有提示词投递与输出协议可言（见 core 的 INTERACTIVE_PROFILE_ID）。
    # 未声明即代表该 agent 不支持交互入口，裸 ``kc`` fail-fast，不猜、不降级。
    interactive: AgentRunnerAgentProfileSettings | None = None


class AgentRunnerPresetSettings(BaseModel):
    """`[agent_runner.presets.<name>]` 段：一个命名预设。

    ``agent`` 必填；``model`` / ``reasoning_effort`` 可选。预设名是否唯一由
    TOML 表键天然保证；预设引用的 agent 是否已注册由解析期校验，配置层只校验
    形状（非空字符串）。
    """

    model_config = ConfigDict(extra="forbid")

    agent: str
    model: str | None = None
    reasoning_effort: str | None = None

    @field_validator("agent", "model", "reasoning_effort")
    @classmethod
    def _reject_blank_values(cls, value: str | None) -> str | None:
        """拒绝空字符串；``None`` 表示未声明。"""
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("preset field must be a non-empty string")
        return normalized_value


class AgentRunnerLifecyclePresetsSettings(BaseModel):
    """``[agent_runner.lifecycle_presets]`` 段：九个生命周期各绑一个预设。

    键是闭集（与矩阵同键，见 :data:`LIFECYCLE_AGENT_KEYS`），未知键在加载期
    报错；取值是预设名（是否已定义由解析期校验，配置层只校验形状）。
    完全可选：不写本段时所有阶段都不注入模型参数，行为与今天逐字节一致。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    implementation: str | None = None
    fix: str | None = None
    closeout: str | None = None
    verifier: str | None = None
    review: str | None = None
    supervisor: str | None = None
    planner: str | None = None
    content_generation: str | None = None
    deliberate: str | None = None

    @field_validator("*")
    @classmethod
    def _reject_blank_values(cls, value: str | None) -> str | None:
        """拒绝空字符串；``None`` 表示未声明。"""
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("lifecycle preset binding must be a non-empty string")
        return normalized_value


class AgentRunnerFallbackCandidateSettings(BaseModel):
    """``[[agent_runner.runner.agent_fallback_candidates]]`` 的一条有序回退候选。

    候选 = ``(agent, 可选 preset)``：``agent`` 必填；``preset`` 可空，空表示
    该候选不注入模型参数（与历史 ``agent_fallback_order`` 逐 agent 等价的形态）。
    预设是否已定义、``preset.agent`` 是否与候选 agent 一致、同一 ``(agent,
    preset)`` 是否重复等**语义校验**由写入侧与解析期负责，配置层只校验形状。
    """

    model_config = ConfigDict(extra="forbid")

    agent: str
    preset: str | None = None

    @field_validator("agent", "preset")
    @classmethod
    def _reject_blank_values(cls, value: str | None) -> str | None:
        """拒绝空字符串；``preset=None`` 表示该候选不绑定预设。"""
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("fallback candidate field must be a non-empty string")
        return normalized_value


class AgentRunnerGitSettings(BaseModel):
    """Git publishing configuration."""

    remote: str = "origin"
    base_branch: str = "main"


#: worktree 命令默认值：命令名取自身份模块，``{…}`` 占位符留给运行期 format。
#: 与 domain ``backend.core.shared.models.agent_runner`` 的同名字段必须保持一致
#: （双类陷阱：运行时读的是 domain）。
_WORKTREE_CREATE_COMMAND_DEFAULT = (
    f"{product_identity.PRIMARY_COMMAND_NAME} worktree create "
    "--branch issue-{issue_number} --base-branch {base_branch}"
)
_WORKTREE_PATH_COMMAND_DEFAULT = (
    f"{product_identity.PRIMARY_COMMAND_NAME} worktree path " "--branch issue-{issue_number}"
)


class AgentRunnerWorktreeSettings(BaseModel):
    """Commands used to create and locate target worktrees.

    Defaults delegate to the built-in ``kc worktree`` subcommand so the
    create / path pair can never drift apart. Override only when the target
    repository genuinely needs a custom worktree layout.
    """

    create_command: str = _WORKTREE_CREATE_COMMAND_DEFAULT
    reuse_command: str = _WORKTREE_PATH_COMMAND_DEFAULT
    path_command: str = _WORKTREE_PATH_COMMAND_DEFAULT
    # 是否为每个 Issue worktree 建独立库,避免并行 worktree 互踩共享库的 alembic_version。
    # 默认开启;目标仓需含 scripts/shared/template/setup_copied_database.py,缺脚本或无关系型
    # DB 时 provision 会告警并回退到共享库(见 use_cases.worktree_database)。
    provision_database: bool = True


class AgentRunnerE2eArtifactSettings(BaseModel):
    """``verification_commands`` E2E 条目中单个产物声明（FR-5）。

    Attributes:
        path: 产物相对 worktree 根的路径（POSIX 分隔符）。
        mime: 期望的 ``file --mime-type`` 精确值，如 ``image/png``。
        min_size: 期望最小字节数；省略时只要求非空。
        key_claim: 给 verifier 的软层断言文本，不做机检。
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    mime: str = Field(min_length=1)
    min_size: int | None = Field(default=None, ge=0)
    key_claim: str = ""


class AgentRunnerE2eVerificationCommandSettings(BaseModel):
    """``verification_commands`` 的浏览器 E2E 结构化条目（FR-1 配置契约）。

    TOML 中与纯字符串条目混排在同一数组里（inline table 不能跨行，
    整条条目写在一行内）::

        verification_commands = [
          "git diff --check",
          { kind = "browser_e2e", script = "tests/e2e/smoke.sh", app_start = "just run frontend", ready_url = "http://127.0.0.1:5173", artifacts = [{ path = ".iar/evidence/x.png", mime = "image/png" }] },
        ]

    纯字符串条目的解析与执行行为逐字段不变；``extra="forbid"`` 保证配置
    里的未知/拼错字段在加载期就报出字段级诊断，而不是运行期裸失败。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["browser_e2e"]
    script: str = Field(min_length=1)
    app_start: str | None = None
    ready_url: str | None = None
    ready_timeout_seconds: int = Field(default=60, ge=1)
    startup_wait_seconds: int | None = Field(default=None, ge=0)
    probe: str | None = None
    timeout_seconds: int = Field(default=900, ge=1)
    inactivity_timeout_seconds: int | None = Field(default=None, ge=1)
    env_allow: list[str] = Field(default_factory=list)
    artifacts: list[AgentRunnerE2eArtifactSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_app_readiness_fields(self) -> "AgentRunnerE2eVerificationCommandSettings":
        """校验应用管理层字段间的依赖关系，给出定向诊断。

        Raises:
            ValueError: ``ready_url`` / ``startup_wait_seconds`` 在没有
                ``app_start`` 时无意义，视为配置错误。
        """
        if self.app_start is None:
            if self.ready_url is not None:
                raise ValueError(
                    "ready_url has no effect without app_start: the runner only "
                    "probes readiness of an app it started itself."
                )
            if self.startup_wait_seconds is not None:
                raise ValueError("startup_wait_seconds has no effect without app_start.")
        return self


class AgentRunnerRunnerSettings(BaseModel):
    """Local runner behavior."""

    max_issues: int = 1
    # Maximum Issues processed in parallel within one daemon pass. 1 keeps the
    # sequential path (zero regression); >1 enables thread-pool parallelism.
    max_concurrent_issues: int = 1
    default_agent: str = "auto"
    max_recovery_attempts: int = 5
    recovery_retry_delay_seconds: int = 30
    # Cross-agent fallback chain. The runner tries the primary agent first, then
    # falls back to the next locally available agent when recovery is exhausted
    # or the provider is capacity-limited. Commands that are not installed on
    # this machine are automatically skipped. Set to [] to disable switching.
    agent_fallback_order: list[str] = Field(default_factory=lambda: ["claude", "kimi", "codex"])
    # 有序回退候选（agent + 可选 preset）。非空时在运行时**整体接管**
    # agent_fallback_order（旧字段只作为无预设候选的兼容读取路径）；同一 agent
    # 可以以不同 preset 重复出现。空列表 = 沿用旧的纯 agent 名单语义。
    agent_fallback_candidates: list[AgentRunnerFallbackCandidateSettings] = Field(
        default_factory=list
    )
    # Maximum number of agent switches before the Issue is marked failed.
    # With order [a, b, c] and max_agent_switches=2, up to 3 agents are tried.
    max_agent_switches: int = 2
    # In-place retries for transient network/transport errors (Level 1).
    transient_retry_attempts: int = 2
    transient_retry_delay_seconds: int = 10
    # Extra same-session runs after process timeouts; counted per session.
    timeout_resume_attempts: int = Field(default=1, ge=0)
    timeout_seconds: int = 14400
    # Whether the lightweight Fix Agent runs when staged verification fails.
    # False escalates staged verification failures straight to full recovery.
    fix_agent_enabled: bool = True
    fix_timeout_seconds: int | None = None
    recovery_timeout_seconds: int | None = None
    # Whether the delivery Closeout Agent runs when a PRD-delivery / RV-evidence
    # gate fails with a closeout-eligible kind. False keeps the pre-closeout
    # behavior (the whole attempt restarts with the full implementation agent).
    closeout_agent_enabled: bool = True
    # Wall-clock budget for a text-only closeout (tick items / append a Change
    # Log entry / repair manifest fields). None falls back to
    # fix_timeout_seconds, then timeout_seconds.
    closeout_timeout_seconds: int | None = 600
    # Wall-clock budget for re-capturing visual evidence: that needs the app
    # actually started, so it must not share the short text budget.
    closeout_visual_timeout_seconds: int | None = 1800
    inactivity_timeout_seconds: int = 1200
    # 提交验证命令队列：纯字符串条目按既有语义以 `bash -lc` 执行、行为逐字段
    # 不变；结构化 dict 条目（kind = "browser_e2e"）声明浏览器 E2E 验证形态，
    # 两类条目按配置顺序在同一队列串行执行。
    verification_commands: list[str | AgentRunnerE2eVerificationCommandSettings] = Field(
        default_factory=lambda: [
            "git diff --check",
        ]
    )
    # Optional command to run after `git add -A` and before `git commit`.
    # When configured, the runner executes this command against the staged
    # tree and treats a non-zero exit as a VerificationFailedError, so the
    # Fix Agent receives the output and can repair pre-commit failures
    # without the runner needing to know the specific hooks involved.
    pre_commit_verification_command: str | None = Field(default=None)


class AgentRunnerMemorySettings(BaseModel):
    """Pydantic mirror of ``MemoryConfig``; ``enabled = false`` disables memory I/O and drafts."""

    enabled: bool = True
    base_dir: str = ".kedacode/memory"
    skill_drafts_dir: str = ".kedacode/skills/drafts"
    promoted_skills_dirs: list[str] = Field(default_factory=lambda: [".kedacode/skills"])
    top_k_skills: int = 3
    top_k_facts: int = 5
    auto_promote: bool = True
    auto_promote_threshold: int = 3
    auto_promote_min_success_rate: float = 1.0


class AgentRunnerSafetySettings(BaseModel):
    """Safety boundaries enforced before publishing."""

    auto_merge: bool = False
    forbidden_path_patterns: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "secrets/*",
            "docker-compose.prod.yml",
        ]
    )


class AgentRunnerAutopilotSettings(BaseModel):
    """Autopilot / merge queue configuration.

    全仓自动合并仍要求本段 ``enabled`` 与
    ``AgentRunnerSafetySettings.auto_merge`` 同时为真。若 ``auto_merge`` 关闭，
    合并队列只处理显式带 ``prd-auto-merge`` 标签的单个 PRD；本段 ``enabled``
    对两种路径都是必要条件。这保留了对早前误设 ``safety.auto_merge = true``
    的防呆保险。

    Attributes:
        enabled: 主开关；为 ``False`` 时合并队列整段 no-op，包括单 PRD 授权项。
        merge_method: 唯一接受的合并方式：``"squash"``；其它值在配置加载期
            由 :class:`AgentRunnerAutopilotSettings` 的 field_validator 拒
            绝。这是用户锁定的设计决策——单提交便于 ``git revert`` 回退。
        require_verifier_pass: 当该 Issue 需要 validation 时，是否要求
            ``validation/verifier-passed`` 标签先存在才进入合并流程；缺失
            则本轮跳过，留给 verifier / repair。
        auto_sign_off: 是否自动勾选 PR body 的 Realistic Validation sign-off
            清单；普通队列在 verifier 通过后勾选，显式 PRD 自动合并在队列仓库
            验证命令通过后勾选；勾选与 marker 评论均幂等。
        merge_check_timeout_seconds: 等 PR checks 全绿的最大秒数；超时则
            放弃本轮合并（不阻塞后续 PR）。
    """

    enabled: bool = False
    # 仅接受 "squash"；非法值在加载期直接报错，避免下游用错方法调用 gh。
    merge_method: Literal["squash"] = "squash"
    require_verifier_pass: bool = True
    auto_sign_off: bool = True
    merge_check_timeout_seconds: int = 1800


class AgentRunnerBacklogSettings(BaseModel):
    """Backlog 持续调度配置。

    ``auto_advance`` 只控制 daemon 是否发现并晋升符合条件的 Backlog PRD，
    与自动合并的两个配置开关相互独立。
    """

    auto_advance: bool = False


class AgentRunnerValidationSettings(BaseModel):
    """Realistic Validation evidence gate configuration."""

    enabled: bool = True
    # 与 core.shared.models.agent_runner.DEFAULT_VALIDATION_EVIDENCE_DIR 保持一致；
    # infrastructure 处于四层最底，不能反向 import core 的常量。
    evidence_dir: str = "tasks/evidence"
    branch_prefix: str = "iar-evidence/"
    evidence_format_check: bool = True
    parse_evidence_format_with_agent: bool = True
    language: str = "zh-CN"
    structured_evidence: bool = True
    require_negative_control: bool = True
    reexecute_commands: bool = True
    reexecute_timeout_seconds: int = 300
    reexecute_cache_enabled: bool = True
    verifier_enabled: bool = True
    verifier_agent: str = "auto"
    verifier_timeout_seconds: int = 1800
    verifier_inactivity_timeout_seconds: int = 1200
    artifact_health_enabled: bool = True
    frontend_visual_evidence_required: bool = True
    frontend_paths: list[str] = Field(default_factory=lambda: ["frontend-admin", "frontend-public"])


#: ``kc console`` 的默认端口；被占用时 CLI 会自动顺延，显式 ``--port`` 不顺延。
_CONSOLE_DEFAULT_PORT = 8313

#: 状态目录下的默认文件名（路径前缀在加载时归一化到当前生效状态目录）。
_CONSOLE_HISTORY_DB_NAME = "console.db"
_CONSOLE_PROCESS_REGISTRY_NAME = "processes.json"


def _default_runner_command() -> list[str]:
    """解析托管进程的默认启动命令（自有名字优先，见身份模块）。

    解析顺序：① 当前进程就是以 ``kc`` / ``kedacode`` / ``iar`` 之一启动的 → 用它
    （保证托管 daemon 与安装态同源）；② 按 ``kc`` → ``kedacode`` → ``iar`` 查
    PATH；③ 都找不到时回落到 ``uv run kc``（keda 源码树场景）。
    ``config.toml`` 里显式配置的 ``runner_command`` 始终优先。
    """
    return product_identity.resolve_own_command_argv(sys.argv[0] if sys.argv else "")


class AgentRunnerConsoleSettings(BaseModel):
    """统一管理终端（运行历史落库与托管进程）配置。

    **刻意没有 host 字段**：认证是空实现，监听地址即唯一访问控制，因此
    硬编码在 ``cli_typer_console.CONSOLE_HOST``，不给 CLI 参数也不给配置项
    （一行 ``host = "0.0.0.0"`` 就能把可写面板暴露给整个网段）。残留的
    ``host`` 键按 pydantic extra 忽略。远程访问走 SSH 端口转发。

    落库与注册表默认值随**当前生效的状态目录**派生（新旧双读），配置里写死的
    旧状态路径在加载时归一化到同一个目录，因此迁移前后、旧路径链接存在与否，
    读到的都是同一份历史。
    """

    history_db_path: str = Field(
        default_factory=lambda: str(product_identity.state_home() / _CONSOLE_HISTORY_DB_NAME)
    )
    process_registry_path: str = Field(
        default_factory=lambda: str(product_identity.state_home() / _CONSOLE_PROCESS_REGISTRY_NAME)
    )
    process_log_dir: str = "logs/agent-runner/processes"
    runner_command: list[str] = Field(default_factory=_default_runner_command)
    stop_timeout_seconds: int = 30
    port: int = _CONSOLE_DEFAULT_PORT
    # dashboard 后台自动同步的静态默认间隔（秒）。界面上改的是 DB 里的运行时
    # 覆盖值（monitor_settings 表）；只有在从来没有保存过设置时才回落到这里。
    monitor_sync_interval_seconds: int = 300

    @model_validator(mode="after")
    def _normalize_state_home_paths(self) -> "AgentRunnerConsoleSettings":
        """把写死旧/新状态目录的配置值归一化到当前生效状态目录。"""
        for path_field in ("history_db_path", "process_registry_path", "process_log_dir"):
            setattr(
                self,
                path_field,
                str(
                    product_identity.normalize_state_path(
                        getattr(self, path_field),
                        home_path=Path.home(),
                    )
                ),
            )
        return self


class AgentRunnerDaemonSettings(BaseModel):
    """Long-running daemon polling configuration."""

    review_interval_seconds: int = 120
    run_interval_seconds: int = 120
    max_deliberation_issues: int = 1
    # 托管资源与历史维护须显式开启；默认本地 daemon 不进入新清理路径。
    hosted_maintenance_enabled: bool = False
    # 低水位暂停新任务；恢复水位采用滞回，避免容量在阈值附近时反复启停。
    disk_low_watermark_bytes: int = 10 * 1024**3
    disk_resume_watermark_bytes: int = 15 * 1024**3
    # 崩溃对账主开关：daemon 每轮开头（Phase -1）扫描本机认领、但认领进程已死的
    # ``agent/running`` 僵尸 attempt，给出续传 / 重跑 / 判失败三出口留痕。关闭后
    # Phase -1 整轮空转（僵尸保持 agent/running 不被触碰），即回退到本特性前的现状。
    reconcile_stale_attempts: bool = True
    # claim 老化阈值(秒):claim marker 含 ``started_at`` 且距 now 超过此值时,
    # 即便 PID 仍存活也视为 stale（"daemon 自身持锁 claim 但已卡死"的死锁兜底）。
    # 默认 3 小时。仅在 ``reconcile_stale_attempts=True`` 时生效。
    reclaim_ttl_seconds: int = 10800

    @model_validator(mode="after")
    def _validate_disk_watermarks(self) -> "AgentRunnerDaemonSettings":
        """确保磁盘恢复水位高于暂停水位。"""
        if self.disk_low_watermark_bytes < 0:
            raise ValueError("daemon.disk_low_watermark_bytes must be non-negative")
        if self.disk_resume_watermark_bytes <= self.disk_low_watermark_bytes:
            raise ValueError(
                "daemon.disk_resume_watermark_bytes must be greater than "
                "daemon.disk_low_watermark_bytes"
            )
        return self


class AgentRunnerPromptSettings(BaseModel):
    """Agent prompt template settings supporting TOML string-list syntax."""

    default_phase: str = "execution"
    phases: dict[str, str | list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _join_list_templates(self) -> "AgentRunnerPromptSettings":
        """Convert list[str] phase values to joined strings."""
        for phase_name, phase_value in self.phases.items():
            if isinstance(phase_value, list):
                self.phases[phase_name] = "\n".join(phase_value)
        return self


class AgentRunnerPrePrReviewSettings(BaseModel):
    """Pre-PR AI review gate configuration.

    The review runs **after** the implementation commit has been pushed to the
    remote branch and **before** the Draft PR is created. Reviewer patches are
    themselves pushed so the remote branch always reflects the latest committed
    state when the PR is opened.
    """

    enabled: bool = True
    review_agent: str = "auto"
    allow_same_agent: bool = True
    # Who applies the fixes for this stage's findings: "self" (the reviewer
    # patches the worktree itself, the historical behavior), "executor" (hand
    # the findings back to the agent that implemented the Issue), or any
    # registered agent name. 与 runner.fix_agent_enabled 无关：后者是本地
    # 验证失败时的轻量修复 agent。
    repair_agent: str = "self"
    max_attempts: int = 2
    timeout_seconds: int = 1800
    # When the reviewer reports findings but fails to write a commit request,
    # the runner appends a reminder and re-invokes the reviewer up to this
    # many times within the same review cycle.
    commit_request_reminder_attempts: int = 1
    # Review rules template; supports either a single string or a list of
    # lines. When the field is omitted from TOML the embedded default in
    # ``agent_review.py`` is used so out-of-the-box behavior still calls the
    # ``code-reviewer`` skill.
    review_prompt_template: str | list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_review_prompt_template(self) -> "AgentRunnerPrePrReviewSettings":
        """Collapse empty / scalar values to a stable list representation."""
        if isinstance(self.review_prompt_template, str):
            normalized = [self.review_prompt_template] if self.review_prompt_template else []
        else:
            normalized = [str(item) for item in self.review_prompt_template]
        # Pydantic v2 disallows assigning to a field directly after validation;
        # use object.__setattr__ to keep the field frozen-friendly.
        object.__setattr__(self, "review_prompt_template", normalized)
        return self


class AgentRunnerPostPrSupervisorSettings(BaseModel):
    """Post-PR supervisor cycle configuration."""

    enabled: bool = True
    supervisor_agent: str = "auto"
    # 谁执行 supervisor 判定后的代码修复：``self``（supervisor 自己修，历史
    # 行为）/ ``executor``（交回本次实现者，拿不到本次实现者时按 Issue 标签
    # 回落并写日志）/ 任意已注册 agent 名。
    repair_agent: str = "self"
    # CI/CD 自动修复策略（独立开关，默认关闭）：只约束 Supervisor Agent 选择的
    # 自动 repair_pr_branch 动作，与 autopilot/safety/runner 开关语义独立。
    auto_repair_ci: bool = False
    max_repair_attempts: int = 2
    max_agent_crash_retries: int = 5
    crash_retry_initial_backoff_seconds: int = 30
    crash_retry_max_backoff_seconds: int = 600
    # 补丁 3：命中这些路径前缀的文件 diff 全量注入 supervisor prompt
    key_paths: list[str] = Field(default_factory=list)
    max_diff_chars: int = 6000
    # 补丁 4：跨 cycle finding 注入开关与 artifact 目录（worktree 相对路径）
    previous_findings_injection_enabled: bool = True
    findings_artifact_dir: str = ".iar/state"


class AgentRunnerDeliberationProfileSettings(BaseModel):
    """Participant profile for deliberation sessions."""

    agent: str = "claude"
    role: str = "participant"
    behavior_prompt: str = "Analyze requirements carefully."


class AgentRunnerInteractiveDecisionSettings(BaseModel):
    """Interactive decision (`kc ask`) configuration."""

    enabled: bool = True
    default_agent: str = "claude"
    default_output_dir: str = "logs/agent-runner/decisions"
    planner_timeout_seconds: int = 120
    max_context_chars: int = 24000
    allow_execute_yes: bool = True  # Allow --yes to skip confirmation.


class AgentRunnerReplSettings(BaseModel):
    """Interactive REPL (`kc` with no subcommand) configuration.

    The REPL entrypoint lets the user chat with a configured agent and
    grants the agent the ability to request execution of whitelisted KedaCode
    subcommands via ``<<IAR_EXEC>> ... <<END_IAR_EXEC>>`` markers. This
    settings block isolates the REPL's risk surface (default agent,
    command allow/confirm lists, audit directory) from the ``kc ask``
    decision planner.
    """

    enabled: bool = True
    default_agent: str = "claude"
    default_output_dir: str = "logs/agent-runner/repl"
    max_context_chars: int = 24000
    agent_timeout_seconds: int = 120
    # Commands that the executor may run without explicit confirmation.
    # Each entry is a *prefix* matched against the argv tail (everything
    # after ``kc``), so ``"labels sync --dry-run"`` auto-confirms only
    # that exact form. Anything not listed here is either matched against
    # ``confirm_commands`` (which prompts) or rejected outright.
    auto_confirm_commands: list[str] = Field(
        default_factory=lambda: [
            "labels sync --dry-run",
            "run --dry-run",
            "supervise --dry-run",
            "review --dry-run",
            "ask --plan-only",
        ]
    )
    # Commands whose execution prompts the user for confirmation. Matched
    # with the same prefix rules as ``auto_confirm_commands``.
    confirm_commands: list[str] = Field(
        default_factory=lambda: [
            "run",
            "daemon",
            "supervise",
            "supervise-daemon",
            "review",
            "review-daemon",
            "issue create",
            "recover",
            "blocked-continue",
            "worktree create",
            "worktree remove",
        ]
    )


class AgentSessionPreviewSettings(BaseModel):
    """``[agent_session.preview]``：受控项目预览的白名单声明。

    预览**只在用户于执行器对话里明确要求时**才启动，因此这里能声明的东西必须是
    精确 argv，而不是任意 shell 文本：``argv`` 直接作为子进程参数数组使用，KC 不
    经过 ``sh -c``，也就没有"把仓库里的字符串当脚本执行"这条路径。

    Attributes:
        argv: 已批准的 dev 命令 argv（如 ``["npm", "run", "dev"]``）。空列表视为
            未配置（走"仓库唯一候选 + 用户显式确认"路径）。
        ready_url: 期望的 ready 地址；主机必须属于回环闭集，且端口/路径原样用于
            校验进程自报的地址。配置非回环主机是**加载期错误**，绝不静默接受。
        ready_timeout_seconds: 等待进程报告地址的上限；必须为正数。
    """

    argv: list[str] = Field(default_factory=list)
    ready_url: str | None = None
    ready_timeout_seconds: int = Field(default=60, gt=0)

    @field_validator("argv")
    @classmethod
    def _validate_argv_entries(cls, value: list[str]) -> list[str]:
        """拒绝空 argv 元素：空字符串会被解释成参数而不是命令。"""
        for argv_entry in value:
            if not isinstance(argv_entry, str) or not argv_entry.strip():
                raise ValueError(
                    "agent_session.preview.argv must contain non-empty literal argv entries "
                    "(no shell text); each entry is passed to the child process verbatim."
                )
        return value

    @field_validator("ready_url")
    @classmethod
    def _validate_ready_url(cls, value: str | None) -> str | None:
        """校验声明的 ready URL 是回环 http 地址（非回环一律加载期报错）。"""
        if value is None:
            return None
        require_loopback_preview_url(value, source="agent_session.preview.ready_url")
        return value


class AgentSessionSettings(BaseModel):
    """顶层 ``[agent_session]``：裸 ``kc`` 原生执行器入口与按需项目预览。

    与 ``[agent_runner.repl]``（旧 Keda REPL）刻意分开：两者是不同风险面的两种
    交互形态——REPL 由 KC 解释 agent 输出并按白名单执行子命令，原生入口把对话
    与权限整个交给 provider，KC 只负责选择 executor、注入 operator skill 与
    bootstrap，然后转发 stdio / 信号 / 退出码。

    Attributes:
        default_agent: 裸 ``kc`` 使用的执行器注册名；``--agent`` 覆盖它。
            该 agent 必须声明 ``interactive`` profile，否则入口 fail-fast。
        bootstrap_enabled: 是否把 operator 使用说明作为 bootstrap 投递给 provider。
        skill_install_check_enabled: 启动前是否用现有 fail-closed 安装器校验
            packaged operator skill（冲突时报错，绝不覆盖用户改过的内容）。
        preview: ``[agent_session.preview]`` 子表。
    """

    model_config = ConfigDict(extra="forbid")

    default_agent: str = "claude"
    bootstrap_enabled: bool = True
    skill_install_check_enabled: bool = True
    preview: AgentSessionPreviewSettings = Field(default_factory=AgentSessionPreviewSettings)


class AgentRunnerStallSupervisorSettings(BaseModel):
    """``[agent_runner.stall_supervisor]``：活跃 attempt 的停滞监督（默认开启）。

    默认开启后，KC 会按周期调用模型；满足停滞结论与全部所有权校验后，可能终止一个
    正在写代码的子进程。需要关闭时显式设置 ``enabled = false``；关闭后监督器不调用
    模型，执行与恢复行为与本特性之前逐字节一致。

    两个阈值各自独立可覆盖：``check_interval_seconds`` 是巡检周期（决定模型调用
    频率下限），``stalled_after_seconds`` 是"无实质进展"的判定窗口（决定误中断
    门槛）。两者都必须为正数——0 或负数会让巡检空转或把任何任务判为停滞。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    check_interval_seconds: int = Field(default=1800, gt=0)
    stalled_after_seconds: int = Field(default=1800, gt=0)
    # 诊断用 agent；默认走生命周期矩阵的 supervisor 键（与 post-PR 监督同一派生），
    # 未声明时回落 runner.default_agent 的 auto 解析。
    agent: str = "auto"
    # 单次诊断调用自身的 wall-clock / 无输出上限，避免"为诊断停滞而制造的调用"
    # 自己变成新的停滞源。
    diagnosis_timeout_seconds: int = Field(default=600, gt=0)
    diagnosis_inactivity_timeout_seconds: int = Field(default=300, gt=0)


class AgentRunnerDeliberationSettings(BaseModel):
    """Multi-agent deliberation configuration."""

    default_rounds: int = 2
    default_synthesizer: str = "claude"
    default_output_dir: str = "logs/agent-runner/deliberations"
    continue_on_agent_error: bool = True
    agent_failure_timeout_seconds: int = 300
    stale_rounds_before_hint: int = 3
    profiles: dict[str, AgentRunnerDeliberationProfileSettings] = Field(
        default_factory=lambda: {
            "architect": AgentRunnerDeliberationProfileSettings(
                agent="claude",
                role="architect",
                behavior_prompt=(
                    "You are an experienced software architect. "
                    "Analyze the requirement from a system design perspective. "
                    "Focus on modularity, scalability, and maintainability."
                ),
            ),
            "skeptic": AgentRunnerDeliberationProfileSettings(
                agent="kimi",
                role="skeptic",
                behavior_prompt=(
                    "You are a skeptical reviewer. "
                    "Challenge assumptions, identify risks, and point out edge cases. "
                    "Ask hard questions that others might miss."
                ),
            ),
            "implementer": AgentRunnerDeliberationProfileSettings(
                agent="codex",
                role="implementer",
                behavior_prompt=(
                    "You are a pragmatic implementer. "
                    "Focus on feasibility, concrete steps, and implementation details. "
                    "Highlight what can be built and what resources are needed."
                ),
            ),
        }
    )


__all__ = (
    "AgentRunnerLifecycleAgentsSettings",
    "AgentRunnerLabelSettings",
    "AgentRunnerAgentProfileSettings",
    "AgentRunnerAgentSettings",
    "AgentRunnerPresetSettings",
    "AgentRunnerLifecyclePresetsSettings",
    "AgentRunnerFallbackCandidateSettings",
    "AgentRunnerGitSettings",
    "AgentRunnerWorktreeSettings",
    "AgentRunnerE2eArtifactSettings",
    "AgentRunnerE2eVerificationCommandSettings",
    "AgentRunnerRunnerSettings",
    "AgentRunnerMemorySettings",
    "AgentRunnerSafetySettings",
    "AgentRunnerAutopilotSettings",
    "AgentRunnerBacklogSettings",
    "AgentRunnerValidationSettings",
    "AgentRunnerConsoleSettings",
    "AgentRunnerDaemonSettings",
    "AgentRunnerPromptSettings",
    "AgentRunnerPrePrReviewSettings",
    "AgentRunnerPostPrSupervisorSettings",
    "AgentRunnerDeliberationProfileSettings",
    "AgentRunnerInteractiveDecisionSettings",
    "AgentRunnerReplSettings",
    "AgentSessionPreviewSettings",
    "AgentSessionSettings",
    "AgentRunnerStallSupervisorSettings",
    "AgentRunnerDeliberationSettings",
    "_default_runner_command",
)
