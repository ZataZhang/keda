"""Agent Runner 的全部设置模型。

本模块承接原先落在 ``settings.py`` 的 ``AgentRunner*Settings`` 家族与仓库级
``.iar.toml`` 覆盖加载（:func:`load_agent_runner_local_settings`）。这些模型不依赖
基础服务设置（数据库 / 模型 / 存储 / 超时），只依赖
:mod:`backend.infrastructure.config.settings_sources` 的配置发现能力，因此可以
独立成层；``settings.py`` 在 ``AgentRunnerSettings`` / ``AppSettings`` 中聚合它们。
"""

import logging
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_AUTO,
    LIFECYCLE_AGENT_AUTO_KEYS,
    LIFECYCLE_AGENT_EXECUTOR,
    LIFECYCLE_AGENT_EXECUTOR_KEYS,
    LIFECYCLE_AGENT_KEYS,
    normalize_lifecycle_agent_value,
)
from backend.infrastructure.config.settings_sources import (
    IAR_REPOSITORY_CONFIG_FILENAME,
)

logger = logging.getLogger(__name__)


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
    expand: list[str] | None = None
    read_only: bool | None = None


class AgentRunnerAgentSettings(BaseModel):
    """`[agent_runner.agents.<name>]` 注册块。

    覆盖既有 agent 时全部字段可选（逐字段回落内置默认）；注册全新
    agent 时 ``bin`` 与 ``label`` 必填，四种用途 profile 至少声明一种。
    ``model_args`` / ``reasoning_effort_args`` 是模型参数 argv 模板
    （含 ``{model}`` / ``{effort}`` 占位符）；为空表示该 agent 未声明
    模型选择语法，命中模型绑定时 fail-fast。
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
    # 会话续传能力声明：``supports_resume`` 为真时，恢复轮次把 ``resume_args``
    # 模板（含 ``{session_id}`` 占位符）注入 argv；未声明的 agent 自然走全新会话。
    supports_resume: bool | None = None
    resume_args: list[str] | None = None
    profiles: dict[str, AgentRunnerAgentProfileSettings] = Field(default_factory=dict)


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


class AgentRunnerGitSettings(BaseModel):
    """Git publishing configuration."""

    remote: str = "origin"
    base_branch: str = "main"


class AgentRunnerWorktreeSettings(BaseModel):
    """Commands used to create and locate target worktrees.

    Defaults delegate to the built-in ``iar worktree`` subcommand so the
    create / path pair can never drift apart. Override only when the target
    repository genuinely needs a custom worktree layout.
    """

    create_command: str = (
        "iar worktree create --branch issue-{issue_number} --base-branch {base_branch}"
    )
    reuse_command: str = "iar worktree path --branch issue-{issue_number}"
    path_command: str = "iar worktree path --branch issue-{issue_number}"
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
    # Maximum number of agent switches before the Issue is marked failed.
    # With order [a, b, c] and max_agent_switches=2, up to 3 agents are tried.
    max_agent_switches: int = 2
    # In-place retries for transient network/transport errors (Level 1).
    transient_retry_attempts: int = 2
    transient_retry_delay_seconds: int = 10
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
    base_dir: str = ".iar/memory"
    skill_drafts_dir: str = ".iar/skills/drafts"
    promoted_skills_dirs: list[str] = Field(default_factory=lambda: [".iar/skills"])
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

    与 ``AgentRunnerSafetySettings.auto_merge`` 组成"双开关"——只有两者同时
    为真时合并队列阶段才会被激活。这是历史上 ``safety.auto_merge`` 死开关被
    真正消费时的防呆保险：早前误设为 ``true`` 的旧环境若未同时打开本段
    ``enabled``，仍不会开始自动合并。

    Attributes:
        enabled: 主开关；为 ``False`` 时合并队列整段 no-op（严格档行为）。
        merge_method: 唯一接受的合并方式：``"squash"``；其它值在配置加载期
            由 :class:`AgentRunnerAutopilotSettings` 的 field_validator 拒
            绝。这是用户锁定的设计决策——单提交便于 ``git revert`` 回退。
        require_verifier_pass: 当该 Issue 需要 validation 时，是否要求
            ``validation/verifier-passed`` 标签先存在才进入合并流程；缺失
            则本轮跳过，留给 verifier / repair。
        auto_sign_off: 是否在 verifier 绿灯后自动勾选 PR body 的 Realistic
            Validation sign-off 清单；勾选与 marker 评论均幂等。
        merge_check_timeout_seconds: 等 PR checks 全绿的最大秒数；超时则
            放弃本轮合并（不阻塞后续 PR）。
    """

    enabled: bool = False
    # 仅接受 "squash"；非法值在加载期直接报错，避免下游用错方法调用 gh。
    merge_method: Literal["squash"] = "squash"
    require_verifier_pass: bool = True
    auto_sign_off: bool = True
    merge_check_timeout_seconds: int = 1800


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


#: ``iar console`` 的默认端口；被占用时 CLI 会自动顺延，显式 ``--port`` 不顺延。
_CONSOLE_DEFAULT_PORT = 8313

#: 托管进程启动命令的兜底值：运行时解析失败时回退为 uv 项目内运行（旧行为）。
_FALLBACK_RUNNER_COMMAND = ["uv", "run", "iar"]


def _default_runner_command() -> list[str]:
    """解析当前 ``iar`` 可执行文件，作为托管进程的默认启动命令。

    解析顺序：① ``sys.argv[0]``（``iar`` 入口直接运行时即当前可执行文件，
    保证托管 daemon 与安装态同源；按 ``Path.stem`` 比较，否则 Windows 的
    ``iar.exe`` 会漏判）；② ``shutil.which("iar")``（uvicorn 等入口启动后端
    时 argv[0] 不是 iar）；③ 兜底 ``["uv", "run", "iar"]``（keda 源码树场景）。
    ``config.toml`` 里显式配置的 ``runner_command`` 始终优先。
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).stem == "iar":
        return [argv0]
    resolved_iar = shutil.which("iar")
    if resolved_iar:
        return [resolved_iar]
    return list(_FALLBACK_RUNNER_COMMAND)


class AgentRunnerConsoleSettings(BaseModel):
    """统一管理终端（运行历史落库与托管进程）配置。

    **刻意没有 host 字段**：认证是空实现，监听地址即唯一访问控制，因此
    硬编码在 ``cli_typer_console.CONSOLE_HOST``，不给 CLI 参数也不给配置项
    （一行 ``host = "0.0.0.0"`` 就能把可写面板暴露给整个网段）。残留的
    ``host`` 键按 pydantic extra 忽略。远程访问走 SSH 端口转发。
    """

    history_db_path: str = "~/.iar/console.db"
    process_registry_path: str = "~/.iar/processes.json"
    process_log_dir: str = "logs/agent-runner/processes"
    runner_command: list[str] = Field(default_factory=_default_runner_command)
    stop_timeout_seconds: int = 30
    port: int = _CONSOLE_DEFAULT_PORT
    # dashboard 后台自动同步的静态默认间隔（秒）。界面上改的是 DB 里的运行时
    # 覆盖值（monitor_settings 表）；只有在从来没有保存过设置时才回落到这里。
    monitor_sync_interval_seconds: int = 300


class AgentRunnerDaemonSettings(BaseModel):
    """Long-running daemon polling configuration."""

    review_interval_seconds: int = 120
    run_interval_seconds: int = 120
    max_deliberation_issues: int = 1
    # 崩溃对账主开关：daemon 每轮开头（Phase -1）扫描本机认领、但认领进程已死的
    # ``agent/running`` 僵尸 attempt，给出续传 / 重跑 / 判失败三出口留痕。关闭后
    # Phase -1 整轮空转（僵尸保持 agent/running 不被触碰），即回退到本特性前的现状。
    reconcile_stale_attempts: bool = True
    # claim 老化阈值(秒):claim marker 含 ``started_at`` 且距 now 超过此值时,
    # 即便 PID 仍存活也视为 stale（"daemon 自身持锁 claim 但已卡死"的死锁兜底）。
    # 默认 3 小时。仅在 ``reconcile_stale_attempts=True`` 时生效。
    reclaim_ttl_seconds: int = 10800


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
    """Interactive decision (`iar ask`) configuration."""

    enabled: bool = True
    default_agent: str = "claude"
    default_output_dir: str = "logs/agent-runner/decisions"
    planner_timeout_seconds: int = 120
    max_context_chars: int = 24000
    allow_execute_yes: bool = True  # Allow --yes to skip confirmation.


class AgentRunnerReplSettings(BaseModel):
    """Interactive REPL (`iar` with no subcommand) configuration.

    The REPL entrypoint lets the user chat with a configured agent and
    grants the agent the ability to request execution of whitelisted IAR
    subcommands via ``<<IAR_EXEC>> ... <<END_IAR_EXEC>>`` markers. This
    settings block isolates the REPL's risk surface (default agent,
    command allow/confirm lists, audit directory) from the ``iar ask``
    decision planner.
    """

    enabled: bool = True
    default_agent: str = "claude"
    default_output_dir: str = "logs/agent-runner/repl"
    max_context_chars: int = 24000
    agent_timeout_seconds: int = 120
    # Commands that the executor may run without explicit confirmation.
    # Each entry is a *prefix* matched against the argv tail (everything
    # after ``iar``), so ``"labels sync --dry-run"`` auto-confirms only
    # that exact form. Anything not listed here is either matched against
    # ``confirm_commands`` (which prompts) or rejected outright.
    auto_confirm_commands: list[str] = Field(
        default_factory=lambda: [
            "labels sync --dry-run",
            "run --dry-run",
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
            "review",
            "review-daemon",
            "issue create",
            "recover",
            "blocked-continue",
            "worktree create",
            "worktree remove",
        ]
    )


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


class AgentRunnerGeneratedContentTargetSettings(BaseModel):
    """生成内容目标配置，支持 TOML 字符串列表语法。

    用于 ``issue_from_prd``、``issue_from_prompt``、``draft_pr``、
    ``prd_from_issue`` 四类生成目标，
    分别控制 Issue、无 PRD Issue、PR、PRD 的标题/正文生成方式。
    """

    enabled: bool = True
    # 仅接受 template / agent；非法值（如手误 "agnet"）在配置加载期直接报错，
    # 而不是静默退回 fallback。默认 agent；agent 缺 prompt、超时、不可用或输出
    # 不合格时按 ``fallback`` 退回 template 渲染，所以未配置的机器同样能产出内容。
    # 显式配成 template 已废弃（仍被接受，加载 ``.iar.toml`` 时会告警）：模板渲染的
    # 长期用途是上面这条失败兜底。
    mode: Literal["template", "agent"] = "agent"
    output: str = "json"
    title_template: str | list[str] = ""
    body_template: str | list[str] = ""
    agent: str = "auto"
    timeout_seconds: int = 120
    prompt: str | list[str] = ""
    include_commit_log: bool = True
    include_diff_stat: bool = True

    @model_validator(mode="after")
    def _join_list_templates(self) -> "AgentRunnerGeneratedContentTargetSettings":
        """将 list[str] 类型的模板字段合并为单个字符串。

        TOML 中多行模板通常以字符串列表书写，便于版本控制审阅；
        加载后需要拼接成完整模板字符串供 ``str.format()`` 渲染。
        """
        for field_name in ("title_template", "body_template", "prompt"):
            value = getattr(self, field_name)
            if isinstance(value, list):
                setattr(self, field_name, "\n".join(value))
        return self


class AgentRunnerGeneratedContentSettings(BaseModel):
    """GitHub Issue 与 PR 的生成内容全局配置。

    聚合各类生成目标（PRD→Issue、一句话→Issue、PR、PRD）的共享参数，如是否启用、
    失败回退策略、最大输入长度以及默认 agent。
    """

    enabled: bool = True
    fallback: str = "template"
    max_input_chars: int = 20000
    default_agent: str = "auto"
    # 各 target 默认都是 agent 模式；``output`` 必须与各自提示词的回复格式一致：
    # issue_from_prd 的提示词回 JSON（沿用类默认），draft_pr / prd_from_issue 回 Markdown。
    # 与 core 的 ``GeneratedContentConfig`` 默认值保持一致（有测试守卫）。
    issue_from_prd: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=AgentRunnerGeneratedContentTargetSettings
    )
    # 一句话需求 → Issue 正文（``iar issue create --from-prompt``）。产物不含 PRD 锚点，
    # 因此提示词与回退模板都不能写出 ``- PRD path:``。
    issue_from_prompt: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=AgentRunnerGeneratedContentTargetSettings
    )
    draft_pr: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=lambda: AgentRunnerGeneratedContentTargetSettings(
            enabled=False, output="markdown"
        )
    )
    prd_from_issue: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=lambda: AgentRunnerGeneratedContentTargetSettings(output="markdown")
    )


# ``generated_content`` 下的生成 target 名（TOML 子表名，同时是 settings / core 配置的
# 字段名），直接取自模型字段，不另写一份清单。
GENERATED_CONTENT_TARGET_NAMES: tuple[str, ...] = tuple(
    field_name
    for field_name, field_info in AgentRunnerGeneratedContentSettings.model_fields.items()
    if field_info.annotation is AgentRunnerGeneratedContentTargetSettings
)


class AgentRunnerRepositoryMetadataSettings(BaseModel):
    """Repository identity stored in repository-local IAR config."""

    id: str | None = None
    enabled: bool = True
    display_name: str | None = None
    # Optional ``owner/name`` string passed to ``gh pr list --repo`` so the
    # PR column on ``iar issue list`` is populated. Omitting it is allowed
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
    validation: AgentRunnerValidationSettings | None = None
    prompts: AgentRunnerPromptSettings | None = None
    pre_pr_review: AgentRunnerPrePrReviewSettings | None = None
    post_pr_supervisor: AgentRunnerPostPrSupervisorSettings | None = None
    daemon: AgentRunnerDaemonSettings | None = None
    generated_content: AgentRunnerGeneratedContentSettings | None = None
    interactive_decision: AgentRunnerInteractiveDecisionSettings | None = None
    deliberation: AgentRunnerDeliberationSettings | None = None
    repl: AgentRunnerReplSettings | None = None
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
    """Repository-local Agent Runner settings loaded from ``.iar.toml``."""

    repository: AgentRunnerRepositoryMetadataSettings = Field(
        default_factory=AgentRunnerRepositoryMetadataSettings
    )


# 已经提示过的 (配置路径, target 名)：daemon 每轮轮询都会重新加载 ``.iar.toml``，
# 同一份配置在一个进程里只提示一次，避免刷屏。
_WARNED_TEMPLATE_PINS: set[tuple[str, tuple[str, ...]]] = set()


def _warn_deprecated_template_mode_pins(
    agent_runner_section: dict[str, Any], local_config_path: Path
) -> None:
    """对显式钉成 ``mode = "template"`` 的 generated_content target 记弃用警告。

    ``template`` 模式已废弃：agent 是默认值，模板渲染只保留为失败兜底。旧版
    ``iar init`` 把当时的默认值 ``template`` 落盘成了显式配置，这类钉值会一直盖过
    新的默认值，所以在加载时指出来，并提示用 ``iar config migrate`` 清理。
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
        "only as the failure fallback. If the pin is a leftover of an older `iar init`, "
        "run `iar config migrate` in this repository to drop it.",
        local_config_path,
        ", ".join(pinned_target_names),
    )


def load_agent_runner_local_settings(
    repo_root_path: Path,
) -> AgentRunnerRepositorySettings | None:
    """Load repository-local IAR settings from ``.iar.toml``.

    Args:
        repo_root_path: Target Git repository root path.

    Returns:
        Repository settings if the local config exists; otherwise ``None``.

    Raises:
        ValueError: If the local config exists but is invalid.
    """
    resolved_repo_path = repo_root_path.resolve()
    local_config_path = resolved_repo_path / IAR_REPOSITORY_CONFIG_FILENAME
    if not local_config_path.is_file():
        return None

    try:
        with open(local_config_path, "rb") as local_config_file:
            local_toml_data: dict[str, Any] = tomllib.load(local_config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid IAR local config at {local_config_path}: {exc}") from exc

    agent_runner_section = local_toml_data.get("agent_runner")
    if not isinstance(agent_runner_section, dict):
        raise ValueError(
            f"Invalid IAR local config at {local_config_path}: missing [agent_runner] section."
        )

    try:
        local_settings = AgentRunnerLocalSettings(**agent_runner_section)
    except ValidationError as exc:
        raise ValueError(f"Invalid IAR local config at {local_config_path}: {exc}") from exc

    _warn_deprecated_template_mode_pins(agent_runner_section, local_config_path)

    repository_metadata = local_settings.repository
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
        validation=local_settings.validation,
        prompts=local_settings.prompts,
        pre_pr_review=local_settings.pre_pr_review,
        post_pr_supervisor=local_settings.post_pr_supervisor,
        daemon=local_settings.daemon,
        generated_content=local_settings.generated_content,
        interactive_decision=local_settings.interactive_decision,
        deliberation=local_settings.deliberation,
        repl=local_settings.repl,
        lifecycle_agents=local_settings.lifecycle_agents,
        presets=local_settings.presets,
        lifecycle_presets=local_settings.lifecycle_presets,
    )
