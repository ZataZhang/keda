# 配置说明

本项目通过 `src/backend/infrastructure/config/settings.py` 中的 `AppSettings` 统一管理配置，并组合多个子配置模型，实现工程化的配置分层。

配置模型按职责分三个模块：`settings.py`（基础服务设置与 `AppSettings` / `AgentRunnerSettings` 聚合）、`agent_runner_settings.py`（全部 `AgentRunner*Settings` 与仓库级 `.kedacode.toml` 覆盖加载）、`settings_sources.py`（配置文件发现与 TOML 设置源）。`settings.py` 对另外两个模块的名字做再导出，既有的 `from backend.infrastructure.config.settings import ...` 路径保持不变。

## 配置来源优先级

总优先级从高到低：

1. 环境变量（含 `.env` / `.env.local`）
2. `config.toml`
3. 代码默认值

## 关键配置模块

- **应用层配置**：应用名、日志级别、日志目录。
- **数据库配置**：后端类型、主机、端口、库名、驱动。
- **模型配置**：默认聊天模型提供商、模型名和温度。
- **基础设施配置**：MinIO、Qdrant、Embedding、Chunking、Timeout 等。

## 常用实践

- 推荐在 `.env` 中存放密钥与敏感信息。
- 推荐在 `config.toml` 中维护非敏感默认项。
- 所有业务代码统一从 `config` 实例读取，不直接散落调用 `os.getenv`。

## Worktree 相关环境变量

`just worktree`（底层实现位于 `scripts/shared/worktree/create.sh`）支持以下环境变量来控制新 worktree 的依赖准备行为：

- `KODA_WORKTREE_BASE_BRANCH`
  - 新 worktree 默认使用的 base branch 名称，默认值为 `main`。
  - 命令行参数 `--base <branch>` 会覆盖这个环境变量。
- `KEDA_WORKTREE_SYNC_BASE`
  - 默认 `true`。创建 worktree 前自动 fetch 远程 base branch 并使用最新远程提交作为起点。
  - 设为 `false` 时关闭远程同步，保持旧行为（直接从本地 base branch 创建）。
  - 远程不存在时会自动回退到本地 base branch；远程存在但 fetch 失败时命令会非零退出，避免静默使用过期基线。
- `KEDA_WORKTREE_BASE_REMOTE`
  - 覆盖默认 remote 名称。未设置时优先读取 `branch.<base>.remote`，不存在时回退到 `origin`。
- `WORKTREE_FRONTEND_STRATEGY`
  - `install-per-worktree`：默认值。扫描 worktree 根目录和子目录中的前端项目，并在各自目录执行锁文件驱动的依赖安装。
  - `symlink-from-main`：不重新安装依赖，而是尝试把新 worktree 中的前端项目 `node_modules` 链接到源仓库对应目录。
- `WORKTREE_SKIP_FRONTEND_INSTALL`
  - 仅在 `WORKTREE_FRONTEND_STRATEGY=install-per-worktree` 时生效。
  - 设为 `true` 后，跳过前端依赖安装步骤。

创建完成后，`just worktree` 会根据仓库名、分支名与短哈希生成唯一的 PostgreSQL
数据库名，改写新 worktree `.env.local` 中的 `DATABASE_URL`，再创建数据库并执行
Alembic 迁移（如仓库存在 `alembic.ini`）。该流程使用严格模式：`.env.local` 未配置
PostgreSQL URL、数据库无法创建或迁移失败时，worktree 创建命令会失败，避免多个
worktree 意外共用同一数据库。开发服务与 E2E 测试共用该 worktree 专用数据库。

`kc daemon` 也可通过 `[agent_runner.worktree]` 的 `provision_database = true` 在领取
Issue 后执行同一流程。数据库 URL 支持 PostgreSQL（`postgresql...`）和 MySQL
（`mysql...`）；两者均要求数据库账号有创建数据库的权限。SQLite 不支持 worktree
隔离，严格模式下会阻止 daemon 开始执行，避免多个 Issue 共享同一个数据库文件。

对包含多个前端子项目的仓库，默认策略会覆盖类似 `demo-frontend/`、`admin-frontend/` 这类嵌套目录，而不是只处理仓库根目录。

## 模板同步配置

`just sync-template` 会读取 `config.toml` 中的 `[template_sync]` 表，用来决定默认模式下哪些项目路径不参与模板同步。

- `project_skip_paths`：默认跳过的项目路径，例如 `src/backend/`、`frontend/`、`infra/`。
- `project_include_paths`：即使命中 `project_skip_paths` 也仍然显示的路径。

运行 `just sync-template --all` 时会忽略这些项目路径过滤规则，临时查看所有模板差异。

也可以用环境变量临时覆盖：

- `SYNC_TEMPLATE_PROJECT_SKIP_PATHS`：逗号或空格分隔的跳过路径列表。
- `SYNC_TEMPLATE_PROJECT_INCLUDE_PATHS`：逗号或空格分隔的保留显示路径列表。

## Agent Runner 仓库配置

`config.toml` 的 `[agent_runner.repositories.<repo_id>]` 段支持配置多个目标仓库：
- `path`：本地已 clone 的仓库绝对路径（必填）。
- `enabled`：是否启用该仓库，默认为 `true`。
- `display_name`：前端展示名称，默认为 `repo_id`。

registry 用的 `config.toml` 是**全局共享**的一份，解析顺序为：`KEDACODE_CONFIG` 优先，未设置时由旧名
`IAR_CONFIG` 兜底（仅在旧名被显式设置时提醒一次） <!-- legacy-alias -->
→ `<本机状态目录>/config.toml` → 源码根 `config.toml`。本机状态目录取 `~/.kedacode`，
只有旧目录的机器继续用旧目录 `~/.iar` <!-- legacy-alias -->。源码根模板存在时，首次调用会把默认配置 seed 进状态目录；
全局安装的 `kc`（`uv tool install kedacode`，没有源码根模板可拷）则在状态目录里创建一份只含
`[agent_runner]` 空表的最小配置作为 registry 载体，已有文件不会被覆盖。

每个仓库可独立覆盖 `labels`、`git`、`worktree`、`runner`、`safety` 子配置：

```toml
[agent_runner.repositories.keda]
path = "/Users/zata/code/keda"
enabled = true
display_name = "Keda"

[agent_runner.repositories.keda.git]
remote = "origin"
base_branch = "main"

[agent_runner.repositories.backend_service]
path = "/Users/zata/code/backend-service"
enabled = true

[agent_runner.repositories.backend_service.runner]
verification_commands = [
  "git diff --check",
  "uv run pytest",
]
```

未覆盖的字段自动继承全局 `[agent_runner]` 默认值。环境变量仍可对全局段生效，但暂不支持通过环境变量覆盖单个仓库的字段。

## Agent Runner Agent 注册表配置

`config.toml` 的 `[agent_runner.agents.<name>]` 段以声明式方式注册 agent。一个 agent 的全部调用差异（可执行文件、认证/skills 路径、各用途 argv、提示词投递、输出协议）都是纯数据，由统一的命令构造器组装；`src/` 下没有 agent 专有的分支代码。全部字段与取值域见 [Agent Runner 使用指南](agent-runner.md#接入一个新-agent声明式注册表)。

### 合并语义

agent 注册表按**三层**合并，后一层逐字段覆盖前一层：

1. **内置默认**：代码内 `BUILTIN_AGENT_SPECS`（`codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode`），是注册表唯一的代码内默认来源；
2. **全局覆盖**：`config.toml` 顶层 `[agent_runner.agents.*]`；
3. **仓库级覆盖**：`[agent_runner.repositories.<repo_id>.agents.*]`。

覆盖既有 agent 时保持其注册顺序（注册顺序即 `choose_agent` 标签匹配的优先级），仓库级新声明的 agent 追加在注册表末尾。旧版 `[agent_runner.labels]` 的 agent 路由键（`codex` / `claude` / `kimi`）继续作为兼容覆盖来源生效；新 agent 的路由标签写在注册块的 `label` 字段，无需再进 labels 段。

### 边界：配置不做 shell 展开

- `bin` / `args` / `tail_args` / `prompt_flag` 的每个元素都是**字面量**：`$(whoami)`、`a|b`、`*.py` 会原样进入子进程 argv，绝不经 shell 解释。
- 运行期参数只通过**命名展开器**注入，且展开器是**闭集**（当前仅 `git_writable_roots`）。引用未注册展开器在构造命令时报错并列出全部已命名展开器，不静默忽略。
- `output_protocol` 必须指向已注册的输出协议（entry point group `kedacode.agent_output_protocols`），加载失败直接报错，不静默回落 `plain`。

配置完成后用 `uv run kc agent doctor <name> --all-profiles` 自检，确认各用途 argv 与预期逐字节一致。

### 模型参数模板与命名预设

- 注册块新增 `model_args` / `reasoning_effort_args`：声明该 agent 的模型与推理档 flag 语法
  （`{model}` / `{effort}` 占位符）。为空表示"未核实语法"，命中带模型/推理档的绑定时 fail-fast。
- `config.toml` 的 `[agent_runner.presets.<name>]` 段定义命名预设（agent 必填，model /
  reasoning_effort 可选）；`[agent_runner.lifecycle_presets]` 段把九个生命周期阶段各绑到一个
  预设，绑定后该阶段整体由预设决定（agent + 模型 + 推理档），遮蔽矩阵同键声明。
- 完整语义、优先级与命令行一次性旗标（`--preset` / `--model` / `--reasoning-effort`）见
  [Agent 模型预设](model-presets.md)。

## Agent Runner Deliberation 配置

`config.toml` 的 `[agent_runner.deliberation]` 段配置多 Agent 合议：

```toml
[agent_runner.deliberation]
default_rounds = 2
default_synthesizer = "claude"
default_output_dir = "logs/agent-runner/deliberations"

[agent_runner.deliberation.profiles.architect]
agent = "claude"
role = "architect"
behavior_prompt = "You are an experienced software architect..."

[agent_runner.deliberation.profiles.skeptic]
agent = "kimi"
role = "skeptic"
behavior_prompt = "You are a skeptical reviewer..."

[agent_runner.deliberation.profiles.implementer]
agent = "codex"
role = "implementer"
behavior_prompt = "You are a pragmatic implementer..."
```

- `default_rounds`：默认讨论轮数（不含综合轮）。
- `default_synthesizer`：默认综合 agent 名称。
- `default_output_dir`：默认输出根目录。
- `profiles.<profile_id>`：自定义参与者 profile，至少包含 `agent`、`role`、`behavior_prompt`。

## Agent Runner Interactive Decision 配置

`config.toml` 的 `[agent_runner.interactive_decision]` 段配置 `kc ask` 行为：

```toml
[agent_runner.interactive_decision]
enabled = true
default_agent = "claude"
default_output_dir = "logs/agent-runner/decisions"
planner_timeout_seconds = 120
max_context_chars = 24000
allow_execute_yes = true
```

- `enabled`：是否启用 `kc ask`。
- `default_agent`：默认 planner agent（支持 `claude`、`codex`、`kimi`）。
- `default_output_dir`：决策审计文件默认输出目录。
- `planner_timeout_seconds`：planner agent 超时时间（秒）。
- `max_context_chars`：传入 planner 的上下文最大字符数。
- `allow_execute_yes`：是否允许 `--yes` 非交互确认。

## Agent Runner REPL 配置

`config.toml` 的 `[agent_runner.repl]` 段配置 `kc repl` 的交互式
REPL 入口。整段与 `[agent_runner.interactive_decision]` 隔离，二者可
独立调整默认 agent、超时、白名单策略。

> 入口变更（Issue #256）：TTY 下的**裸 `kc`** 不再进入本段的 REPL，而是进入原生执行器
> 入口 `kc session`（见下文「Agent Session 原生入口与预览配置」）；本段入口固定为显式的
> `kc repl`。两者是不同风险面：REPL 由 KC 解释 agent 输出并按白名单执行子命令，原生入口
> 把对话与权限整个交给 provider。

```toml
[agent_runner.repl]
enabled = true
default_agent = "claude"
default_output_dir = "logs/agent-runner/repl"
max_context_chars = 24000
agent_timeout_seconds = 120
auto_confirm_commands = [
  "labels sync --dry-run",
  "run --dry-run",
  "review --dry-run",
  "ask --plan-only",
]
confirm_commands = [
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
```

- `enabled`：是否启用 REPL；设为 `false` 时 `kc repl` 直接报 `REPL is disabled in configuration.` 并以 1 退出。
- `default_agent`：默认 REPL agent（支持 `claude`、`codex`、`kimi`）。
  `auto` 不被接受为 REPL 默认 agent（`kc run` 才用 auto）。
- `default_output_dir`：REPL 会话审计目录前缀；每次会话创建
  `<default_output_dir>/<session-id>/`，包含 `session.json`、
  `transcript.md`、`commands.json`。
- `max_context_chars`：首条 system prompt 的最大字符数（超出部分被
  截断并保留头尾）。
- `agent_timeout_seconds`：每轮调用 agent 子进程的超时；超时或非零
  退出码会作为 `[IAR_EXEC_RESULT] exit_code=...` 块追加到对话历史。
- `auto_confirm_commands`：前缀匹配列表，匹配的命令直接执行。
- `confirm_commands`：前缀匹配列表，匹配的命令执行前先询问用户
  `Execute? [y/N]`。

`auto_confirm_commands` 与 `confirm_commands` 都按「剥离 `kc` 后剩余
的命令 tail」做前缀匹配，例如 `"run --dry-run"` 只对
`kc run --dry-run` 自动放行；`"run"` 则对 `kc run ...` 的所有调用
询问确认。

`.kedacode.toml` 可在 `[agent_runner.repl]` 段覆盖上述任意字段，实现仓库级
REPL 策略：默认全局 agent 是 `claude`，某个仓库可改为 `kimi` 或
`codex`；默认 dry-run 白名单之外的命令可通过
`confirm_commands` 在仓库层收紧或放宽。

## Agent Session 原生入口与预览配置

`config.toml` 的顶层 `[agent_session]` 段配置 TTY 下裸 `kc` 的原生执行器入口，以及
`[agent_session.preview]` 子表所声明的按需项目预览。行为全景见
[Agent Runner 指南](agent-runner.md) 的「原生执行器入口与按需项目预览」。

```toml
[agent_session]
default_agent = "claude"
bootstrap_enabled = true
skill_install_check_enabled = true

[agent_session.preview]
argv = ["npm", "run", "dev"]
ready_url = "http://127.0.0.1:3000"
ready_timeout_seconds = 60
```

- `default_agent`：裸 `kc` / `kc session` 使用的执行器注册名，`--agent` 可覆盖。该 agent
  **必须声明 `interactive` profile**，否则入口 fail-fast 报错，不会静默回退到别的 provider。
- `bootstrap_enabled`：是否把 operator 使用说明作为 bootstrap 投递给 provider。投递通道由
  profile 的 `prompt_delivery` 决定（claude 走 `--append-system-prompt`；没有可核实通道的
  provider 就不投递，也不假装投递）。设为 `false` 时空提示词不会占用 argv 位置参数。
- `skill_install_check_enabled`：启动前是否用既有 fail-closed 安装器核对随包 operator skill；
  冲突时报错，**绝不覆盖**用户改过的内容。
- `preview.argv`：已批准的 dev 命令 **argv 数组**（不是 shell 文本）。空列表视为未配置，此时走
  "仓库唯一候选 + 用户显式确认"路径；元素为空字符串是加载期错误。
- `preview.ready_url`：期望的 ready 地址。主机必须属于回环闭集（`localhost` / `127.0.0.1` / `::1`），
  非回环主机是**加载期错误**——绑到可路由网卡这条路径在任何进程启动之前就被排除。
- `preview.ready_timeout_seconds`：等待进程自报地址的上限，必须为正数。

仓库层 `.kedacode.toml` 可覆盖以上任意字段（含 `preview.argv`），因此不同仓库能各自声明自己的
dev 命令而不改动机器级配置。预览进程**只在用户于执行器对话里明确要求时**由 `kc preview start`
启动，KC 不主动起、也不猜用户想不想看。

## Agent Runner 停滞监督配置

`config.toml` 的 `[agent_runner.stall_supervisor]` 段控制"活跃 attempt 是否被周期性问一次
是否停滞"。**默认关闭**是刻意的：开启意味着 KC 会按周期调用模型，并在满足全部所有权校验后
终止一个正在写代码的子进程。关闭时一次模型都不调用，执行与恢复行为与本特性之前逐字节一致。

```toml
[agent_runner.stall_supervisor]
enabled = false
check_interval_seconds = 1800
stalled_after_seconds = 1800
agent = "auto"
diagnosis_timeout_seconds = 600
diagnosis_inactivity_timeout_seconds = 300
```

- `enabled`：总开关。
- `check_interval_seconds`：巡检周期；必须为正数，`0` 或负值在配置加载期就报错（不会起观察线程再崩）。
- `stalled_after_seconds`：现场指纹连续冻结多久算疑似停滞；同样必须为正。
- `agent`：诊断使用哪个 agent 的**只读 `generate`** profile。默认 `auto` 沿用生命周期矩阵的
  `supervisor` 键（与 post-PR 监督同一派生），未声明时回落到 `runner.default_agent` 的 auto 解析。
  解析结果没有只读 `generate` 形态时，监督器自我禁用并记一条审计，而不是拿带写权限的 profile 去诊断。
- `diagnosis_timeout_seconds` / `diagnosis_inactivity_timeout_seconds`：单次诊断调用的总上限与静默上限。

判定与处置的完整链路（含"结论必须落进闭集、`stalled` 缺摘要或证据一律降级 `uncertain`"）见
[Agent Runner 指南](agent-runner.md) 的「停滞监督（Stall Supervisor）」。

## 预览部署配置

`config.toml` 的 `[preview]` 段控制 PR 预览部署的非敏感结构。敏感值（服务器地址、SSH 密钥、镜像仓库密码、数据库密码）必须通过 GitHub Secrets 注入，不得写入仓库文件。

```toml
[preview]
enabled = false
base_domain = "preview.example.com"
project_slug = "keda"
app_dir_root = "/opt/preview"
registry_host = "ghcr.io"
registry_namespace = "ZataZhang"
traefik_network = "traefik"
url_scheme = "https"
subdomain_template = "pr-{pr_number}.{base_domain}"
compose_template = "{project_slug}-pr-{pr_number}"
```

字段说明：

- `enabled`：是否启用预览部署工作流。设为 `true` 且配置 Secrets 后 PR 才会触发部署。
- `base_domain`：预览入口的基础域名，通配证书应覆盖 `*.<base_domain>`。
- `project_slug`：项目短标识，用于镜像名与 Compose project 名。
- `app_dir_root`：预览服务器上存放各 PR 栈的父目录。
- `registry_host` / `registry_namespace`：镜像仓库主机与命名空间。
- `traefik_network`：服务器上已存在的外部 Traefik 网络名。
- `url_scheme`：`https` 或 `http`，决定 sticky 评论中的 URL 协议。
- `subdomain_template`：PR 子域名模板，可用变量 `{pr_number}`、`{base_domain}`。
- `compose_template`：Compose project 名模板，可用变量 `{project_slug}`、`{pr_number}`。

环境变量覆盖：所有字段均可通过 `PREVIEW_` 前缀的环境变量覆盖，例如 `PREVIEW_ENABLED=true`。

## 日志相关配置

日志位于 `logs/` 目录，按日期命名，格式为 `app-YYYY-MM-DD.log`：

```bash
# 查看今天的日志
cat logs/app-$(date +%Y-%m-%d).log

# 实时查看日志
tail -f logs/app-$(date +%Y-%m-%d).log
```

### 配置项

| 配置（`[app]` 段） | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `log_level` | `LOG_LEVEL` | `INFO` | 日志级别。写成小写（如 `info`）会按大小写归一化生效，并留下一条"不是 logging 预定义名，已按 INFO 解析"的提示；写成完全无法解析的值（错拼、空值）时不中断启动，降级为 `INFO` 并留下一条"无效日志级别，已降级为 INFO"的警告。 |
| `log_retention_days` | `LOG_RETENTION_DAYS` | `14` | 日日志保留天数。非正数或无法解析（如 `abc`）时回退到默认 14：既不会因为一次写错就清空历史日志，也不会让进程起不来。 |
| `log_dir` | `LOG_DIR` | `<项目根>/logs` | 仅用于保证日志目录存在。**注意**：日文件的实际落点由 `log_file` 决定，单独设置 `LOG_DIR` 不会把日文件挪走。 |
| `log_file` | `LOG_FILE` | `<项目根>/logs/app.log` | 日文件写在它的父目录里，命名为 `app-YYYY-MM-DD.log`。该目录由日志模块单点提供，`kc logs` 的回退提示也取它，因此不会因为自定义路径而指向另一个文件。 |

### 日志特性

- **按日期划分**：每天生成一个独立的日志文件，如 `app-2026-05-24.log`
- **长驻进程跨天切换**：跑过午夜的进程（`kc loop-daemon`、`kc registry start` 的 runner / review-daemon）在下一次写日志时自动切到当天的 `app-YYYY-MM-DD.log`，不会继续写进昨天的文件。若新文件因为权限、只读挂载等原因开不出来，日志会继续写进原来的文件并提示一次，等目录恢复后自动补上切换——日志故障不会让业务调用失败
- **自动清理**：启动时以及每次跨天切换时删除超过 `log_retention_days`（默认 14）天的旧日志文件
- **时间戳格式**：日志条目使用 `YYYY-MM-DD HH:MM:SS` 格式
- **终端同步**：`kc` 命令的终端输出带有 `HH:MM:SS` 时间戳前缀
- **与第三方 handler 共存**：进程里已有其他日志 handler（uvicorn、测试框架）时，keda 仍会挂上自己的 stdout 与文件 handler，因此同一条记录可能同时出现在对方 handler 与 keda 日志里，这不是重复日志

### 日志内容

日志文件记录以下内容：

- CLI 启动和配置加载事件
- Agent 工具调用摘要（如 `[agent tool] Read`）
- Agent 返回结果摘要（如 `[agent result]`）
- Agent 错误信息（如 `[agent error]`）
- Agent 输出文本（按消息边界汇总记录）
- 子进程输出（Codex/Kimi 等非 Claude agent 的输出）

## 数据库 URL 解析

`AppSettings.resolved_database_url` 支持：

- 直接使用 `DATABASE_URL`。
- 在未提供完整 URL 时，通过组件拼接生成连接字符串。
