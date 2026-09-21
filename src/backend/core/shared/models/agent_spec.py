"""agent 调用的声明式领域模型。

一个 agent 的全部调用差异（可执行文件、认证/skills 路径、各用途的
argv 片段、提示词投递方式、输出协议）都沉淀为纯数据
：class:`AgentSpec`，由 ``[agent_runner.agents.<name>]`` 配置段或
内置默认 ：data:`BUILTIN_AGENT_SPECS` 提供。

本模块是 agent 注册表的**唯一**代码内默认来源；配置层
（pydantic settings）、工厂映射与各构造点都从这里派生，
禁止再写死 agent 名。

`argv` 组装语义见 :mod:`backend.core.use_cases.agent_invocation`：
``[bin] + args(占位符替换) + expanders + tail_args``，再按
``prompt_delivery`` 决定提示词落在 argv 尾部、指定 flag 后还是 stdin。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 输出协议 id（叶子常量：interfaces 与 models 都从这里取，避免循环导入）
# ---------------------------------------------------------------------------

PLAIN_PROTOCOL_ID = "plain"
"""通用文本协议 id：逐行中继 stdout，不做任何结构化渲染。"""

CLAUDE_STREAM_JSON_PROTOCOL_ID = "claude-stream-json"
"""Claude ``stream-json`` 事件流渲染协议 id。"""

PI_JSON_LINES_PROTOCOL_ID = "pi-json-lines"
"""pi JSON Lines 事件流渲染协议 id。"""

# ---------------------------------------------------------------------------
# 提示词投递方式与用途 profile 的闭集
# ---------------------------------------------------------------------------

PROMPT_DELIVERY_ARGV_TAIL = "argv_tail"
"""提示词作为一个整体追加到 argv 尾部。"""

PROMPT_DELIVERY_FLAG = "flag"
"""提示词跟在 ``prompt_flag`` 指定的参数之后（如 kimi 的 ``--prompt``）。"""

PROMPT_DELIVERY_STDIN = "stdin"
"""提示词不进 argv，由执行层写入子进程 stdin。"""

PROMPT_DELIVERIES: tuple[str, ...] = (
    PROMPT_DELIVERY_ARGV_TAIL,
    PROMPT_DELIVERY_FLAG,
    PROMPT_DELIVERY_STDIN,
)

AGENT_PROFILE_RUN = "run"
"""主执行用途（Issue 的实现/修复，``iar run`` 路径）。"""

AGENT_PROFILE_DELIBERATE = "deliberate"
"""多方辩论用途（``iar deliberate`` / transcript runner 路径）。"""

AGENT_PROFILE_GENERATE = "generate"
"""内容生成用途（Issue 正文 / PR 描述 / 决策 planner 等只读调用）。"""

AGENT_PROFILE_REPL = "repl"
"""REPL 用途（``iar repl``，允许在用户确认模型内写文件）。"""

AGENT_PROFILES: tuple[str, ...] = (
    AGENT_PROFILE_RUN,
    AGENT_PROFILE_DELIBERATE,
    AGENT_PROFILE_GENERATE,
    AGENT_PROFILE_REPL,
)


@dataclass(frozen=True)
class AgentProfileSpec:
    """单个 agent 单个用途的调用形态。

    Attributes:
        args: 基础 argv 片段（``bin`` 之后、展开器与 tail 之前）。
            支持 ``{cwd}`` / ``{worktree}`` / ``{prompt}`` 占位符；
            元素是字面量，不做 shell 展开。
        prompt_flag: ``prompt_delivery="flag"`` 时承载提示词的参数名
            （如 kimi 的 ``--prompt``）；其余投递方式必须为 ``None``。
        prompt_delivery: 提示词投递方式，取值见 ``PROMPT_DELIVERIES``。
        output_protocol: 输出协议 id，经
            ``iar.agent_output_protocols`` entry point 注册表解析。
        tail_args: 追加在展开器之后、提示词之前的 argv 片段
            （如 codex 的 ``exec`` 子命令）。
        expand: 展开器声明，格式 ``"<expander_name>:<flag>"``，语义是
            "对展开器返回的每个值各追加一次 ``<flag> <value>``
            （如 codex 的 ``git_writable_roots:--add-dir``）。
        read_only: 该用途是否为可验证的只读调用。只读决策入口
            （planner / ``iar ask``）以此字段做 fail-fast 门禁。
    """

    args: tuple[str, ...] = ()
    prompt_flag: str | None = None
    prompt_delivery: str = PROMPT_DELIVERY_ARGV_TAIL
    output_protocol: str = PLAIN_PROTOCOL_ID
    tail_args: tuple[str, ...] = ()
    expand: tuple[str, ...] = ()
    read_only: bool = False


@dataclass(frozen=True)
class AgentSpec:
    """单个 agent 的声明式注册信息。

    Attributes:
        bin: 可执行文件名（按 ``PATH`` 解析，不内建嗅探）。
        label: agent 路由标签（如 ``"agent/claude"``）。
        label_color: GitHub 标签颜色（6 位 hex，不含 ``#``）。
        label_description: GitHub 标签描述。
        auth_home: 本机认证/配置根目录（支持 ``~``），容器认证导入与
            用户级 skills 目录派生都以此为源。
        auth_include: 容器认证导入的顶层白名单条目。
        auth_exclude: 容器认证导入显式排除的运行时状态子路径。
        project_skills_dir: 项目级 skills 目录（相对仓库根，如 pi 的
            ``".pi/skills"``）。
        profiles: 用途名 -> 调用形态。四种用途键见 ``AGENT_PROFILES``。
    """

    bin: str
    label: str
    label_color: str
    label_description: str
    auth_home: str | None = None
    auth_include: tuple[str, ...] = ()
    auth_exclude: tuple[str, ...] = ()
    project_skills_dir: str | None = None
    profiles: dict[str, AgentProfileSpec] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内置默认（唯一来源；config.toml / .iar.toml 的 [agent_runner.agents.*] 覆盖它）
# ---------------------------------------------------------------------------

# 注意 dict 顺序即注册顺序：choose_agent 按标签匹配时先到先得，
# 这里保持与旧 LabelConfig.agent_labels 一致的 codex -> claude -> kimi 顺序；
# 后续新增的 agent（pi、codebuddy、qoder、opencode）一律追加在末尾，绝不插入
# 既有条目之间——插入会改变既有 agent 的标签匹配优先级。
BUILTIN_AGENT_SPECS: dict[str, AgentSpec] = {
    "codex": AgentSpec(
        bin="codex",
        label="agent/codex",
        label_color="5319E7",
        label_description="Use Codex for local runner execution.",
        auth_home="~/.codex",
        auth_include=("auth.json", "skills"),
        auth_exclude=(
            "sessions",
            "cache",
            ".tmp",
            ".codex-global-state.json",
            "log",
            "logs",
            "history.jsonl",
        ),
        project_skills_dir=".codex/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=(
                    "--cd",
                    "{cwd}",
                    "--sandbox",
                    "workspace-write",
                    "--config",
                    "sandbox_workspace_write.network_access=true",
                    "--ask-for-approval",
                    "never",
                ),
                expand=("git_writable_roots:--add-dir",),
                tail_args=("exec",),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=(
                    "--cd",
                    "{cwd}",
                    "--sandbox",
                    "read-only",
                    "--ask-for-approval",
                    "never",
                ),
                tail_args=("exec",),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=(
                    "--cd",
                    "{cwd}",
                    "--sandbox",
                    "read-only",
                    "--ask-for-approval",
                    "never",
                ),
                tail_args=("exec",),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("--cd", "{cwd}"),
                tail_args=("exec",),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    "claude": AgentSpec(
        bin="claude",
        label="agent/claude",
        label_color="BFDADC",
        label_description="Use Claude Code for local runner execution.",
        auth_home="~/.claude",
        auth_include=("settings.json", "skills"),
        auth_exclude=(
            "history.jsonl",
            "file-history",
            "paste-cache",
            "plans",
            "plugins",
            "cache",
            "ide",
            "backups",
            "telemetry",
            "todos",
        ),
        project_skills_dir=".claude/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=(
                    "--dangerously-skip-permissions",
                    "--verbose",
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--include-partial-messages",
                ),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=(
                    "--dangerously-skip-permissions",
                    "--verbose",
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--include-partial-messages",
                ),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    "kimi": AgentSpec(
        bin="kimi",
        label="agent/kimi",
        label_color="FF6B6B",
        label_description="Use Kimi for local runner execution.",
        auth_home="~/.kimi-code",
        auth_include=("config.toml", "credentials", "oauth", "device_id", "skills"),
        auth_exclude=(
            "sessions",
            "logs",
            "cache",
            "session_index.jsonl",
            "tmp",
        ),
        project_skills_dir=".kimi-code/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=(),
                prompt_flag="--prompt",
                prompt_delivery=PROMPT_DELIVERY_FLAG,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=("--input-format", "text"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=(),
                prompt_flag="--prompt",
                prompt_delivery=PROMPT_DELIVERY_FLAG,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=(),
                prompt_flag="--prompt",
                prompt_delivery=PROMPT_DELIVERY_FLAG,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    "pi": AgentSpec(
        bin="pi",
        label="agent/pi",
        label_color="7C3AED",
        label_description="Use pi for local runner execution.",
        auth_home="~/.pi/agent",
        auth_include=("auth.json", "settings.json", "models.json", "skills"),
        auth_exclude=("sessions", "pi-crash.log", "models-store.json"),
        project_skills_dir=".pi/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("--approve", "--mode", "json"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PI_JSON_LINES_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=("--approve", "--no-tools", "--print"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=("--no-tools", "--print"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("--approve", "--print"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    # CodeBuddy Code（@tencent-ai/codebuddy-code）与 Claude Code 同构：
    # -p/--print、--output-format stream-json、--include-partial-messages、
    # -y/--dangerously-skip-permissions 全部存在，因此四用途照抄 claude 形态，
    # 输出复用内置 claude-stream-json 协议。配置与凭据在 ~/.codebuddy/。
    "codebuddy": AgentSpec(
        bin="codebuddy",
        label="agent/codebuddy",
        label_color="0052D9",
        label_description="Use CodeBuddy Code for local runner execution.",
        auth_home="~/.codebuddy",
        auth_include=("settings.json", "skills"),
        auth_exclude=(
            "history.jsonl",
            "file-history",
            "sessions",
            "logs",
            "plans",
            "plugins",
            "cache",
            "blobs",
            "traces",
            "shell-snapshots",
            "tasks",
            "teams",
            "projects",
            "local_storage",
            "jobs",
            "diagnostics",
            "skills-marketplace",
        ),
        project_skills_dir=".codebuddy/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=(
                    "--dangerously-skip-permissions",
                    "--verbose",
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--include-partial-messages",
                ),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=(
                    "--dangerously-skip-permissions",
                    "--verbose",
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--include-partial-messages",
                ),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    # Qoder CLI CN（@qodercn-ai/qoderclicn）。两点与 claude 不同，均以本机
    # --help 与包内 schema 实测为准：
    # 1. 可执行文件名是 ``qodercn``（``qoder`` 只是 shell 别名）；
    # 2. 没有 ``--verbose`` / ``--include-partial-messages``，运行用途改用
    #    ``-o stream-json``：其包内 schema 就是 claude 的信封形状
    #    （type=stream_event / assistant / result），故复用 claude-stream-json。
    # deliberate 走 stdin + plain 而非 claude 的 argv_tail + 流式：流式协议会把
    # ``-p`` 从 argv 剥离再经 stdin 投递，而 qoder 在缺 ``-p`` 时的行为未经验证；
    # 保留 ``-p`` 并用 stdin 投递，既避免依赖未验证行为，也避免长 transcript
    # 撑爆 argv。配置与凭据在 ~/.qoder-cn/。
    "qoder": AgentSpec(
        bin="qodercn",
        label="agent/qoder",
        label_color="FF8C42",
        label_description="Use Qoder for local runner execution.",
        auth_home="~/.qoder-cn",
        auth_include=("settings.json", "skills"),
        auth_exclude=(
            "logs",
            "file-history",
            "projects",
            "tasks",
            "shell-snapshots",
            "cache",
            "plugins",
        ),
        project_skills_dir=".qoder-cn/skills",
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p", "-o", "stream-json"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=CLAUDE_STREAM_JSON_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_STDIN,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_GENERATE: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=True,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("--dangerously-skip-permissions", "-p"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
    # OpenCode。非交互入口是 ``run`` 子命令（消息是位置参数），输出走
    # ``--format default``（逐行文本）即 plain；``--format json`` 是另一套事件
    # 形状，本项目没有对应协议，故不启用。**不声明 generate**：它没有任何
    # 沙箱 / 只读开关，声明 read_only 会是无法验证的假声明，只读决策入口
    # （planner / iar ask）需要该字段做门禁，故宁可缺用途也不做假声明。
    # 配置在 ~/.config/opencode/（XDG 路径，容器认证导入会派生为 config/opencode）。
    "opencode": AgentSpec(
        bin="opencode",
        label="agent/opencode",
        label_color="0EA5E9",
        label_description="Use OpenCode for local runner execution.",
        auth_home="~/.config/opencode",
        auth_include=("opencode.json", "skills"),
        auth_exclude=(
            "node_modules",
            "package.json",
            "package-lock.json",
            "tui.jsonc",
            "plugins",
        ),
        profiles={
            AGENT_PROFILE_RUN: AgentProfileSpec(
                args=("run", "--dangerously-skip-permissions"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_DELIBERATE: AgentProfileSpec(
                args=("run", "--dangerously-skip-permissions"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
            AGENT_PROFILE_REPL: AgentProfileSpec(
                args=("run", "--dangerously-skip-permissions"),
                prompt_delivery=PROMPT_DELIVERY_ARGV_TAIL,
                output_protocol=PLAIN_PROTOCOL_ID,
                read_only=False,
            ),
        },
    ),
}


def builtin_agent_names() -> list[str]:
    """按注册顺序返回内置 agent 名列表。"""
    return list(BUILTIN_AGENT_SPECS)


__all__ = [
    "AGENT_PROFILES",
    "AGENT_PROFILE_DELIBERATE",
    "AGENT_PROFILE_GENERATE",
    "AGENT_PROFILE_REPL",
    "AGENT_PROFILE_RUN",
    "BUILTIN_AGENT_SPECS",
    "CLAUDE_STREAM_JSON_PROTOCOL_ID",
    "PI_JSON_LINES_PROTOCOL_ID",
    "PLAIN_PROTOCOL_ID",
    "AgentProfileSpec",
    "AgentSpec",
    "PROMPT_DELIVERIES",
    "PROMPT_DELIVERY_ARGV_TAIL",
    "PROMPT_DELIVERY_FLAG",
    "PROMPT_DELIVERY_STDIN",
    "builtin_agent_names",
]
