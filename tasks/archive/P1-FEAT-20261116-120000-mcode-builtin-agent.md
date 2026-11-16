# PRD: 扩展内置 agent 覆盖——mcode（MiniMax Code）

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⏸️ **验收状态**：验证阻塞（2026-11-16）——实现、机器层 rv-1…rv-6 / rv-8 / rv-9 与独立代码复核均通过。真实 console 两层下拉可见 `mcode`，仓库层写回已验证；但当前 CUA 工具无法把屏幕截图导出到证据目录，rv-7 的截图呈递物未留存。此 PRD 按阻塞归档；补齐截图文件后再转入正常人工验收。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文档分两个高度：**Part A（§1–§4）** 给人看，用来确认"要不要做、做成什么样"，不含实现机制与命令；**Part B（§5–§13）** 给执行者看，包含机制、改动树与验证命令。

## Feature Overview (功能一览)

> 本块是第 10 节 Functional Requirements 的通俗投影，不是第二事实来源；行为验收以第 1 节的行为样例表为准。

- **`mcode` 进入内置注册表**（FR-1、FR-2、FR-3）：`mcode`（MiniMax Code CLI）与现有 `codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode` 并列，开箱即用、无需任何配置。它的调用形态是**纯数据注册块**——`src/` 下没有、也不会有 mcode 专有分支代码。
- **新增 `mcode-json-lines` 输出协议**（FR-4、FR-5）：mcode 的 `stream-json` 事件信封（`exec.started` / `turn.started` / `item.*` / `turn.completed` / `exec.completed`）是 mcode 自有 schema，与内置的 claude / codex / opencode / pi 四种协议**都不同形**，因此需要一个新协议实现并经 entry point 注册。协议负责渲染 agent 消息与工具调用、提取会话 id 与 token 用量。
- **补齐 mcode 的观测缺口**（FR-6）：keda 现有的会话 id 提取只认 `session_id` / `sessionID` / `thread_id` / `id` 四种键，token 用量只认 snake_case 的 `input_tokens` / `output_tokens`；mcode 报的是顶层 `sessionId` 与 `inputTokens` / `outputTokens` / `cacheReadTokens`。不改这两处解析器，mcode 的崩溃对账、续传与用量统计会全部失效。
- **权限档定为 yolo**（FR-7）：mcode 的 `--permission` 有 `smart`（默认）/ `full` / `off` 三档。无人值守跑 Issue 必须用 `full`，否则工具调用可能卡在确认上——它等价于 claude 的 `--dangerously-skip-permissions`。
- **出厂注册块跟上**（FR-4）：`config.toml` 里的可抄写注册块与代码内默认逐字段一致，防两处漂移。
- **文档、原型、配置注释与测试同步**（FR-8）：所有列举内置 agent 的地方补上 `mcode`；测试侧补命令行快照与写死清单。
- **明确不做的**（§11）：不改默认回退顺序、不改 `default_agent`、不给 mcode 声明 `judge` 用途（§2 决策一）、**不把 `--permission off` 当作只读保证**（§2 决策二）、不给 runner 容器镜像预装 mcode、不新增任何注册表字段。

---

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

keda 的 agent 注册表（`[agent_runner.agents.<name>]`）是**声明式**的：接一个新 agent 只要写一段纯数据配置，代码里没有 agent 专有分支逻辑。但内置默认只有 `codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode` 七个，而这台机器上实际装了、且正在用的另一个是 **`mcode`（MiniMax Code CLI，v0.7.7）**。

1. **`mcode` 不在内置默认里**：不写配置时 `kc agent list` 看不到它，console 的 agent 下拉里也选不到——于是每个想用它的人都要各自手写一遍注册块。
2. **mcode 的输出协议没有现成的**：它的 `stream-json` 是自有 schema，既不匹配 `claude-stream-json`（信封不同），也不匹配 `codex-json-lines`（item 字段是 `content` 不是 `text`、工具事件是 `item.toolCall` 不是 `item.command_execution`）。要么新写一个协议，要么退回 `plain`（代价是 live 面板看不到过程）。
3. **观测层不认 mcode 的字段名**：会话 id 是顶层 camelCase 的 `sessionId`、token 用量是 `inputTokens` / `outputTokens` / `cacheReadTokens`。keda 现有解析器两处都不认，直接后果是 mcode 的崩溃对账拿不到 session id（无法续传）、用量统计恒为空。
4. **多处测试与多处文档写死了"内置 agent 就这七个"**：agent 名清单、命令行快照、文档枚举都按七个写的，加 agent 必须同步，否则守卫与快照会红。

**注意本 PRD 几乎不含架构改动**：注册表的数据模型已经能表达 mcode 的全部调用差异（可执行文件、路由标签、认证目录、四种用途的参数与输出协议）。需要动逻辑的只有两处——新增一个输出协议实现、补两个字段名解析——都是"补齐既有机制对一个新 provider 的支持"，不引入新机制。

### Interpretation (解读回显)

下表每一行都会**原样**变成 §7.6 节的验收 oracle，所以改表里的一格就等于改验收标准——值得逐行读。

| 输入 / 操作 | 期望观察到的结果 |
|---|---|
| 不写任何配置，运行 `kc agent list` | 列表里出现 `mcode`，且原有七个的名字与顺序不变 |
| 不写任何配置，运行 `kc agent doctor mcode --all-profiles` | 打印各已声明用途的命令行，含 `--permission full`；进程内不出现 "executable not found" |
| 运行 `kc agent doctor --protocols` | 列出 `mcode-json-lines`，且原有五个协议 id 与顺序不变 |
| 在 console 生命周期矩阵里把某阶段设为 `mcode` | 两层下拉都能选到 `mcode`；选中保存后写回对应文件，其余内容不变 |
| 已装 mcode 的机器上把某阶段设为 `mcode` 并真实执行一次 | 子进程就是 `mcode exec ...`，live 面板能看到 agent 消息与工具调用，最终回复进 transcript，退出码沿用既有失败语义 |
| 用 mcode 真实跑一轮，事后查运行记录 | 该轮记录了 mcode 自报的 session id 与 token 用量（不再是空） |
| 请求 mcode 承担 `judge` 用途 | fail-fast 并指名该 agent 不提供此用途，不静默回落 |
| 不写任何配置，跑一次完整的 Issue 流程 | 各阶段用的 agent、子进程环境、回退链与今天**逐字节一致** |

**我默默定了这些**（未提问、直接选定的）：

- 注册名就用通用名小写 `mcode`，可执行文件名同为 `mcode`。
- 标签色沿用"每个 agent 一个 6 位 hex"的既有约定，新值不与其他 agent 重复。
- 新 agent 一律追加在注册表末尾，**不改动** `agent_fallback_order`、`default_agent`、deliberation 默认 profile 等任何默认值。
- `auth_home` 取 mcode 真实的配置根目录 `~/.minimax`；`project_skills_dir` 取 `.minimax/skills`（相对仓库根）。
- `mcode` 声明 `run` / `deliberate` / `generate` / `repl` 四个用途，**不声明 `judge`**（见决策一）。

**我理解为不做**：

- 不做"未安装的 agent 在界面上下沉/置灰"——那是独立的产品缺口，本 PRD 只描述现状。
- 不改任何既有默认值，不新增注册表字段，不新增 `kc` 子命令。
- 不把 mcode 加进 `agent_fallback_order`（回退链是既有默认链，改它会影响既有行为）。

### What The User Gets

维护 keda 的运维者打开 console 的 agent 下拉，能直接选到 `mcode` 并指到任意生命周期阶段；装了 MiniMax Code 的机器上就能直接跑，不需要手写注册块，也不需要为它单独写输出协议插件。

### Measurable Objectives

- `kc agent list` 的输出包含 `mcode`，且原有 7 个名字与顺序不变（可用一条命令断言）。
- `kc agent doctor mcode --all-profiles` 对已声明用途全部打印出命令行，退出码 0。
- 既有 7 个 agent 的命令行黄金快照**零 diff**。
- mcode 的真实执行事件流被正确渲染：agent 消息、工具调用、最终回复三类都可见。

## 2. Human Review Map (介入与风险地图)

**决策一：`mcode` 不提供 `judge` 用途，可以接受吗？** 仓库的 `judge` 用途（模型评测的评审调用）要求 agent 声明**禁用全部内置工具**的开关（如 claude 系 `--tools ""`、pi 的 `--no-tools`），评测页据此把没有该开关的 agent 标为"不可作为评审"，而不是假装隔离。mcode **没有**等价开关：`--permission off` 的字面含义是"关掉权限"，但实测表明它**不关闭工具**（见决策二），因此 mcode 无法提供"只能看 payload、不能动工作区"的评审形态。强行声明就是假声明。**请确认：** 接受"mcode 不能承担评审用途，宁缺勿假"，还是希望反过来放宽 judge 门禁（不建议）？**验收：** 矩阵里选 `mcode` 的 `judge` 被拒绝并指名；`run` / `repl` / `generate` 正常工作。

**决策二：`--permission off` 不能当只读保证，`mcode` 的 `generate` 用途按"约定只读"处理，可以接受吗？** 实测（本机 v0.7.7）：`mcode exec --permission off` **仍然会执行工具并写入文件**——让它写 `/tmp/x` 并读回，文件确实被创建并读到内容；让它删文件也照做。`off` 实际关掉的是**交互式权限询问**，不是工具能力本身（`--help` 也写明 `ask requires TUI/ACP`）。这意味着 mcode **无法**提供 `read_only_enforced` 那一档"即使提示词被忽略也写不进工作区"的保证。因此：`generate` 用途仍声明 `read_only = true`（供只读决策入口做 fail-fast 门禁），但 `read_only_enforced` 保持 `false`——与 claude 系 `generate` 的处理完全一致（它们的 argv 也带 `--dangerously-skip-permissions`，同样不是强制只读）。**请确认：** 接受"mcode 的只读是约定而非强制，且不因此拒绝它当 planner / `kc ask` 的 agent"？**验收：** `mcode` 的 `generate` 可被选中为 planner，调用方按既有语义处理（`read_only_enforced=False` 时依赖该保证的功能自动停用，不假装隔离）。

**决策三：`mcode` 的提示词投递方式选 `argv_tail` 还是 `stdin`？** mcode 的 `exec` 支持两种：位置参数（`argv_tail`）与 `--input -`（`stdin`）。实测两者都能正常收到提示词并作答。keda 既有 agent 里 claude / codex 走 `argv_tail`，pi / kimi 走 `stdin`——kimi 走 stdin 的理由是"长 prompt 撑爆 argv 上限"。mcode 的 Issue 提示词同样可能很长（PRD 全文 + 验收计划）。**本 PRD 选 `stdin` + `--input -`**：理由是 mcode 的位置参数在 `exec` 子命令下与提示词文本混排，长提示词有 `Argument list too long` 风险，而 `--input -` 已实测可用且天然规避长度上限；keda 的 `argv` 组装顺序是 `[bin] + args + resume_args + expanders + tail_args`，`--input -` 作为 `tail_args` 追加、提示词随后走 stdin，与 pi 的形态一致。**请确认：** 接受"`stdin` + `--input -`"这个选择（而不是与 claude 一致的 `argv_tail`）？**验收：** `kc agent doctor mcode --all-profiles` 打印的 argv 含 `--input -` 且 `prompt_delivery: stdin`；真实执行时提示词不进 argv。

> **实施期结论（已闭环）**：按"`stdin` + `--input -`"落地。实测 `echo "..." | mcode exec --input -` 正常收到提示词并返回 `PIPE-OK`，确认该通道可用。

**自动门禁，不需要逐项人工审阅**：出厂注册块与代码内默认的一致性守卫、mcode 各用途的命令行黄金快照与既有七条快照的零 diff、未声明用途的 fail-fast 测试、协议注册表一致性、`just lint`、守卫测试与 `just test all`。

**本次明确不涉及**：数据库表结构无变化；不改前端代码；不改子进程环境；不改任何既有默认值；不新增注册表字段。

## 3. Usage And Impact After Implementation

**运维者（console）**：入口 `just run` 打开 frontend-public → Roadmap → 受管理仓库列表某行齿轮（仓库层）或 Settings 页的全局矩阵（全局层）。生命周期矩阵的 agent 下拉里**新增一个选项**：`mcode`。选中的值照既有语义写回 `.kedacode.toml` 或 `config.toml`，保存与来源层标注行为**完全不变**——下拉的数据源本来就是接口返回的已注册 agent 列表，前端不需要任何改动。

**运维者（CLI）**：入口 `kc agent list` / `kc agent doctor mcode --all-profiles` / `kc agent doctor --protocols`。`mcode` 出现在列表里；doctor 打印各用途的完整命令行；对未声明的 `judge` 用途 doctor 报错并列出已声明的用途名。

**runner / 流水线**：各阶段的 agent 解析入口不变，子进程环境不变。被路由到 `mcode` 的阶段，其命令行来自新注册块；除此之外**一切照旧**。

**开发者 / 集成者**：`kc` CLI 不新增必用命令；注册表的数据结构与所有导出类型**零变化**。唯一的行为变化是补齐了两处解析（`sessionId` 与 camelCase token 用量）——这两个改动对既有 agent 是**增量**的（多认两个键名），不改变任何既有 agent 的行为。

## 4. Requirement Shape

- **Actor**：运维者（console 生命周期矩阵 + `kc` CLI）、runner 流水线（按阶段启动 agent 子进程）、开发者 / 集成者（新增 agent 注册块）。
- **Trigger**：配置加载时（注册表构建与三层合并）；`kc agent doctor` 自检时；console 读取矩阵视图时；agent 子进程输出中继时。
- **Expected behavior**：`mcode` 与既有七个并列可用；mcode 的事件流被正确渲染；mcode 自报的会话 id 与用量被正确观测；未声明用途 fail-fast 且不回落；不写任何配置时行为与今天完全一致。
- **Scope boundary**：仅覆盖"内置注册表里有哪些 agent"与"mcode 的输出如何被渲染与观测"。注册表的字段与能力、调用形态、子进程环境、生命周期矩阵语义、标签路由、回退链、状态机、前端代码、数据库结构全部不变。

---

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**现有相关模块**：

- `src/backend/core/shared/models/agent_spec.py`：`BUILTIN_AGENT_SPECS`（agent 注册表**唯一**代码内默认来源，现含 `codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode`）、`AgentProfileSpec`、`AgentSpec`、`AGENT_PROFILES` 闭集（`run` / `deliberate` / `generate` / `repl` / `judge`）、`PROMPT_DELIVERIES` 闭集、协议 id 常量。**本 PRD 只在此追加一个 spec，不动任何字段。**
- `src/backend/engines/agent_runner/output_protocols/`：协议实现目录，现含 `plain` / `claude_stream_json` / `codex_json_lines` / `opencode_json_lines` / `pi_json_lines`；`structured_json_lines.py` 是结构化 JSONL 的共用中继（`relay_structured_json_lines`）。**本 PRD 新增 `mcode_json_lines.py`。**
- `src/backend/core/shared/interfaces/agent_output_protocol.py`：协议接口与 id 常量再导出（id 实体定义在 `agent_spec.py`）。**本 PRD 在 `agent_spec.py` 加 id 常量并在此再导出。**
- `src/backend/infrastructure/agent_stream_usage.py`：`extract_session_id_event`（认 `session_id` / `sessionID` / `thread_id` / `id`）、`extract_usage_event` / `build_token_usage`（认 snake_case `input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`）。**本 PRD 补两处字段名识别。**
- `src/backend/core/use_cases/agent_invocation.py`：`build_agent_invocation`（argv 组装：`[bin] + args(占位符) + resume_args + expanders + tail_args`，再按 `prompt_delivery` 决定提示词落点）。**不改。**
- `src/backend/infrastructure/config/agent_runner_settings.py`：`AgentRunnerAgentSettings`（注册块模型）、`AgentRunnerAgentProfileSettings`（用途级稀疏覆盖）。**不改**——现有字段足够表达 mcode。
- `src/backend/engines/agent_runner/factory_config_builder.py` / `factory_config_merge.py`：注册表构建与三层合并。**不改。**
- `src/backend/engines/agent_runner/container_auth.py`：`build_agent_import_specs` 从注册表派生容器认证导入规格。**不改**，但注册表一变，`kc container auth import` 的产物会多出 `mcode` 目录（见 §12）。
- `pyproject.toml`：`[project.entry-points."kedacode.agent_output_protocols"]` 协议注册表。**本 PRD 新增一行。**
- `config.toml`：`[agent_runner.agents.<name>]` 出厂注册块（被守卫测试锁死与代码默认一致）。**本 PRD 新增一个块。**
- `tests/test_agent_spec_config.py`：`test_config_toml_agent_blocks_match_builtin_specs` 断言 `set(config.toml 的 agents 块) == set(BUILTIN_AGENT_SPECS)` 并逐字段比对。
- `tests/test_agent_invocation_golden.py`：命令行黄金快照 + 未注册 / 未声明用途的 fail-fast 测试。**本 PRD 补 mcode 快照。**
- `tests/test_output_protocol_registry.py`：硬编码已注册协议 id 清单。**本 PRD 补 `mcode-json-lines`。**
- `tests/test_container_auth.py`：`test_supported_agent_specs_follow_builtin_registry` 硬编码 agent 名列表。**本 PRD 补 `mcode`。**
- `tests/test_agent_config_consistency.py`：`test_planner_command_builders_for_supported_agents` 硬编码 agent 名元组。**本 PRD 补 `mcode`。
- `scripts/shared/template/sync_template.sh`：`SKILL_ADAPTER_NAMES` / `SKILL_ADAPTER_DIRS` —— 模板 skill 同步的适配器清单。**本 PRD 补 mcode。**
- `docs/guides/agent-runner.md`：内置 agent 枚举与注册块范例。**本 PRD 补 mcode。**
- `docs/prototypes/lifecycle-agent-matrix.{html,md}`：`REGISTERED_AGENTS` 硬编码名字列表。**本 PRD 补 mcode。**

**既有架构模式**：agent 的调用差异全部沉淀为**纯数据**（`AgentSpec` / `AgentProfileSpec`），`src/` 下没有 agent 专有分支；配置按"内置默认 → 全局 `config.toml` → 仓库级"三层逐字段合并；新 agent 的注册顺序即标签匹配优先级，追加在末尾就不会影响既有路由。输出协议经 entry point group 注册，内置与第三方插件走同一条路径。

**架构约束**：四层依赖方向 `api -> core -> engines -> infrastructure`；协议 id 常量必须定义在 `core/shared/models/agent_spec.py`（叶子模块，避免循环导入），`core/shared/interfaces/agent_output_protocol.py` 按需再导出；Python 文本 I/O 显式 `encoding="utf-8"`；单文件非空行 ≤ 1000。

**Frontend Impact**：**无前端代码改动**。理由：frontend-public 的 agent 下拉不含任何硬编码 agent 列表，选项由接口返回的 `agents: string[]` 经 `buildLifecycleOptions` 生成；新增注册项会自动出现在下拉里。但**用户可见面确实变了**，因此 §7.6 仍安排一条真实前端入口的验收。涉及的真实前端文件（供定位，非改动）：`frontend-public/components/agent-runner/lifecycle-agent-matrix.tsx`、`frontend-public/components/agent-runner/agent-fallback-order-editor.tsx`、`frontend-public/lib/api/lifecycleAgents.ts`。

**Existing PRD Relationship**：`tasks/pending/` 现有 PRD 与本 PRD 无重叠、无依赖。`tasks/archive/` 中的 `P1-FEAT-20260920-225333-builtin-agent-coverage-codebuddy-qoder-opencode`（扩展内置覆盖到 codebuddy / qoder / opencode）是本 PRD 的直接先例——它确立了"新内置 agent 用纯数据注册、只追加在末尾、不改默认值"的做法，本 PRD 沿用。**无重复工作，可独立交付。**

## 6. Recommendation

### Recommended Approach

把 `mcode` 作为**第八个内置 agent** 追加进 `BUILTIN_AGENT_SPECS`，同时新增一个 `mcode-json-lines` 输出协议（协议 id 常量放 `agent_spec.py`，实现放 `engines/agent_runner/output_protocols/`，经 `pyproject.toml` 的 entry point 注册），并在 `agent_stream_usage.py` 补两处字段名识别（`sessionId` 与 camelCase token 用量）。

调用形态（注册块，四个用途全部走 `--permission full` + `--input -` + `stream-json`）：

```toml
[agent_runner.agents.mcode]
bin = "mcode"
label = "agent/mcode"
label_color = "1F6FEB"
label_description = "Use MiniMax Code for local runner execution."
auth_home = "~/.minimax"
auth_include = ["auth", "config.yaml", "skills"]
auth_exclude = [".builtin-skills", ".internal-skills"]
project_skills_dir = ".minimax/skills"
supports_resume = true
resume_args = ["--session", "{session_id}"]

[agent_runner.agents.mcode.profiles.run]
args = ["exec", "--cwd", "{cwd}", "--permission", "full", "--output-format", "stream-json"]
tail_args = ["--input", "-"]
prompt_delivery = "stdin"
output_protocol = "mcode-json-lines"

[agent_runner.agents.mcode.profiles.deliberate]
args = ["exec", "--cwd", "{cwd}", "--permission", "full", "--output-format", "stream-json"]
tail_args = ["--input", "-"]
prompt_delivery = "stdin"
output_protocol = "mcode-json-lines"
read_only = true

[agent_runner.agents.mcode.profiles.generate]
args = ["exec", "--cwd", "{cwd}", "--permission", "full", "--output-format", "stream-json"]
tail_args = ["--input", "-"]
prompt_delivery = "stdin"
output_protocol = "mcode-json-lines"
read_only = true

[agent_runner.agents.mcode.profiles.repl]
args = ["exec", "--cwd", "{cwd}", "--permission", "full", "--output-format", "stream-json"]
tail_args = ["--input", "-"]
prompt_delivery = "stdin"
output_protocol = "mcode-json-lines"
```

**`--permission full` 的位置**：它在 `args` 里而不是 `tail_args` 里，与既有 agent 把危险旗标放 `args` 的做法一致（claude 的 `--dangerously-skip-permissions` 在 `args` 首项）。argv 组装顺序为 `[bin] + args + resume_args + expanders + tail_args`，因此实际命令行是 `mcode exec --cwd <wt> --permission full --output-format stream-json --session <sid> --input -`，提示词随后走 stdin。

### Proposed Solution Summary (实现机制)

**FR-1/FR-2/FR-3 — 内置注册表追加 mcode**：在 `BUILTIN_AGENT_SPECS` 末尾追加 `"mcode"` 键（**追加在末尾是硬要求**：注册顺序即标签匹配优先级，插入会改变既有 agent 的路由）。

**FR-4 — 新增 `mcode-json-lines` 协议**：

1. 在 `core/shared/models/agent_spec.py` 加 `MCODE_JSON_LINES_PROTOCOL_ID = "mcode-json-lines"`（叶子模块，避免循环导入），在 `core/shared/interfaces/agent_output_protocol.py` 再导出。
2. 新建 `src/backend/engines/agent_runner/output_protocols/mcode_json_lines.py`，复用 `relay_structured_json_lines`，实现 `_render_line`：
   - `item.completed` 且 `item.type == "agent_message"` → 渲染 `item["content"]`（mcode 用 `content`，codex 用 `text`——这是两者不能共用协议的原因之一）。
   - `item.completed` 且 `item.type == "tool_call"` → 渲染 `[agent tool] <name>: <detail>`，detail 从 `toolCall["input"]` 里取 `command` / `filePath` / `path`（与 opencode 协议同思路，但 mcode 的结构是 `toolCall.input`）。
   - `item.completed` 且 `item.type == "reasoning"` → 渲染为 `[agent thinking]` 前缀（mcode 有独立的 reasoning item，claude 系把思考混在 assistant 消息里）。
   - `turn.failed` → 渲染 `[agent error] <status>`。
   - 未知事件不输出（与既有协议一致）。
3. 在 `pyproject.toml` 的 `[project.entry-points."kedacode.agent_output_protocols"]` 注册 `mcode-json-lines`。

**FR-5 — 事件信封核对**（实施期必须逐条核对，mcode 的 schema 细节以实测为准）：

| mcode 事件 | 关键字段 | 用途 |
|---|---|---|
| `exec.started` / `session.started` | 顶层 `sessionId` | 会话 id |
| `turn.started` | — | 无 |
| `item.started` / `item.updated` | `item.contentDelta` | 增量文本（不渲染，避免重复） |
| `item.completed` | `item.type` = `agent_message` / `reasoning` / `tool_call` | 正文与工具调用 |
| `turn.completed` | `usage.{inputTokens,outputTokens,cacheReadTokens}` | token 用量 |
| `turn.failed` | `status` | 失败原因 |
| `exec.completed` | `result.status` / `result.output` | 最终结果 |

**FR-6 — 观测层补字段**：
- `extract_session_id_event`：在既有候选键后加 `"sessionId"`，并加一条 `event_payload.get("sessionId")` 取值。**注意**：mcode 的每条事件都带顶层 `sessionId`，取最后一次即最新会话，与既有语义一致。
- `extract_usage_event` / `_extract_stream_usage_event`：mcode 的 `turn.completed` 事件里 `usage` 是 camelCase（`inputTokens` / `outputTokens` / `cacheReadTokens`），与既有 codex 分支（snake_case + `cached_input_tokens` 语义）不同。**新增一条独立的 mcode 分支**：识别 `usage.inputTokens` / `usage.outputTokens` / `usage.cacheReadTokens`，映射到 `input_tokens` / `output_tokens` / `cache_read_input_tokens`。**不做字段级混合**——按"哪个 provider 的形状"整支处理，避免把 camelCase 与 snake_case 混着算。
  - 语义对齐说明（**已实测闭环**）：mcode 的 `inputTokens` **不含**缓存命中部分——实测 `inputTokens + cacheReadTokens + outputTokens == totalTokens`（如 15403 + 20224 + 429 == 36056），与 keda 的 Anthropic 口径一致，因此可 **1:1 直映射**，无需任何换算。最终映射：`input_tokens = inputTokens`、`output_tokens = outputTokens`、`cache_read_input_tokens = cacheReadTokens`。实现上按 usage 里的键名区分 provider（`cacheReadTokens` 在场即 mcode 形状），**不做字段级混合**——两套口径语义不同，混着算会重复计费。该结论由 `scripts/probe_mcode_protocol.py` 的同源对照锁定（原始上报与平台观测逐字段相等，见 §7.6 rv-3）。

**FR-7 — 权限档**：`--permission full` 写进四个用途的 `args`。**明确不写 `--permission off` 也不当只读保证用**（决策二）。`deliberate` / `generate` 的 `read_only = true` 但 `read_only_enforced` 保持 `false`。

**FR-8 — 同步面**：
- `config.toml` 出厂注册块（新增 `[agent_runner.agents.mcode]` 与四个 profile）。
- `docs/guides/agent-runner.md`：内置 agent 枚举 + mcode 注册块范例 + mcode 形态速查（含 `--permission` 三档的实测语义，特别是 `off` **不**禁工具这一坑）。
- `docs/prototypes/lifecycle-agent-matrix.{html,md}`：`REGISTERED_AGENTS` 补 `mcode`。
- `scripts/shared/template/sync_template.sh`：`SKILL_ADAPTER_NAMES` / `SKILL_ADAPTER_DIRS` 补 mcode。
- 测试：`tests/test_output_protocol_registry.py`、`tests/test_container_auth.py`、`tests/test_agent_config_consistency.py`、`tests/test_agent_invocation_golden.py`（补 mcode 黄金快照）。
- 打包 skill：`src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md` 及其 references 若列举内置 agent，需同步（依据 `docs/ai-standards/tooling.md` 的 CLI Surface And Packaged Skill Sync 小节）。

### Alternatives Considered

**A. 退回 `plain` 协议，不写新协议。** 优点：零代码改动，最快。缺点：live 面板只能看到 mcode 的原始 JSONL（`plain` 是逐行中继原文），对使用者是"一堆 JSON"，与 codex / claude 的体验差距明显；且 `plain` 路径下 `relay_structured_json_lines` 的用量采集不生效，token 用量恒为空。**否决**：mcode 已经有可用的事件流，写协议的成本低于"给用户看 JSON"。

**B. 把 mcode 的事件流塞进 `codex-json-lines`。** 两者信封接近（都是 `item.completed` + `item.type`），但字段名不同：codex 用 `item.text` / `item.command_execution`，mcode 用 `item.content` / `item.toolCall`。塞进去要么加 `or` 分支污染既有协议（每个 provider 都要改同一个渲染器），要么让协议同时认两套 schema（职责混乱）。**否决**：既有四个协议都是"一个 provider 一个实现"，跟随该惯例。

**C. 只写配置不内置（放用户自己的 `config.toml`）。** 优点：不动 keda 代码。缺点：每个用户都要手写一遍；且**新协议仍必须先在 keda 里存在**（配置只能引用已注册协议 id），所以"不写代码"这条路对 mcode 并不成立。**否决**：与用户明确选择的"做成内置默认 agent"一致。

**D. 用 mcode 的 `acp` 子命令（Agent Client Protocol）接入。** 优点：标准协议。缺点：ACP 是 stdio 服务端协议，keda 现有的调用层是"一条命令行跑完并中继输出"，接 ACP 需要新的进程交互模式，远超本 PRD 范围。**否决**：本期不做，记入 §12 后续项。

## 7. Implementation Guide

### Core Logic

**新增协议实现**（`src/backend/engines/agent_runner/output_protocols/mcode_json_lines.py`）：

```python
"""``mcode-json-lines`` 输出协议：渲染 MiniMax Code exec 的 JSONL 事件并提取会话 id。"""

from __future__ import annotations

import json

from backend.core.shared.interfaces.agent_output_protocol import (
    MCODE_JSON_LINES_PROTOCOL_ID,
    OutputRelayRequest,
)
from backend.core.shared.models.agent_runner import CommandResult
from backend.engines.agent_runner.output_protocols.structured_json_lines import (
    relay_structured_json_lines,
)
from backend.infrastructure.agent_model_config_snapshot import observe_relay_model_config


class McodeJsonLinesOutputProtocol:
    """渲染 mcode ``exec --output-format stream-json`` 事件流。"""

    @observe_relay_model_config
    def relay(self, request: OutputRelayRequest) -> CommandResult:
        """执行 mcode 并把 JSONL 事件转为可读输出。"""
        return relay_structured_json_lines(
            request,
            protocol_id=MCODE_JSON_LINES_PROTOCOL_ID,
            line_renderer=_render_line,
        )
```

`_render_line` 的事件映射见 FR-5 的表；**以实施期实测的字段名为准**，渲染函数对未知事件返回空串（与既有协议一致）。

**会话 id 与用量解析**（`src/backend/infrastructure/agent_stream_usage.py`）：

- `extract_session_id_event`：候选键加 `"sessionId"`；取值顺序建议 `session_id` → `sessionID` → `sessionId` → `thread_id` → `id`（保持既有键的优先级不变，mcode 的键是新增分支）。
- `_extract_stream_usage_event`：新增 mcode 分支，识别 `turn.completed` 且 `usage` 为 camelCase 形状时按 mcode 语义转换；**必须与 codex 的 snake_case 分支互斥**（codex 的 `turn.completed` 用 `input_tokens` / `cached_input_tokens`，mcode 用 `inputTokens` / `cacheReadTokens`，靠键名即可区分）。

### Change Impact Tree

```
src/backend/core/shared/models/agent_spec.py          # +1 协议 id 常量，+1 内置 spec（约 60 行）
src/backend/core/shared/interfaces/agent_output_protocol.py   # 再导出新协议 id
src/backend/engines/agent_runner/output_protocols/mcode_json_lines.py   # 新文件（约 70 行）
src/backend/infrastructure/agent_stream_usage.py       # +sessionId 识别，+mcode usage 分支（约 30 行）
pyproject.toml                                          # +1 entry point
config.toml                                              # +mcode 出厂注册块（约 30 行）
scripts/shared/template/sync_template.sh                # +mcode 适配器
docs/guides/agent-runner.md                             # +mcode 枚举 / 范例 / 速查
docs/prototypes/lifecycle-agent-matrix.{html,md}        # +mcode 名字
src/backend/engines/agent_runner/templates/skills/kedacode-operator/**  # 若列举内置 agent 则同步
tests/test_output_protocol_registry.py                 # +mcode-json-lines
tests/test_container_auth.py                           # +mcode
tests/test_agent_config_consistency.py                 # +mcode
tests/test_agent_invocation_golden.py                  # +mcode 黄金快照
tests/test_agent_spec_config.py                        # 无需改（自动跟随）
tests/test_stream_usage_parsing.py（或同类）           # +sessionId / mcode usage 单测
```

### Risk Classification Register

| 风险 | 等级 | 缓解 |
|---|---|---|
| mcode 的事件 schema 随版本漂移 | 中 | 渲染器对未知事件宽容跳过；rv-3 用真实 CLI 锁定当前版本行为；文档记录锁定版本号 |
| camelCase token 用量与 keda 聚合口径不一致 | 中 | rv-3 实测核对 `TokenUsage` 语义后定最终映射；测试锁死 |
| `--permission off` 的语义误导后续维护者 | 低 | 决策二写进 PRD 与文档速查；注册块只用 `full` |
| mcode 不在 `agent_fallback_order` 里 | 低 | 刻意为之，不改既有回退链；文档说明 |
| mcode CLI 未安装的机器 | 低 | doctor 报 "executable not found"，与既有 agent 行为一致 |

### Executor Drift Guard

- 注册表守卫：`test_config_toml_agent_blocks_match_builtin_specs` 断言 `config.toml` 的 agents 块与 `BUILTIN_AGENT_SPECS` 逐字段一致——**新增 spec 必须同步 `config.toml`，否则守卫红**。
- 协议守卫：`test_output_protocol_registry.py` 的协议 id 清单必须含 `mcode-json-lines`。
- 快照守卫：既有 7 个 agent 的黄金快照**零 diff** 是硬门槛。
- 未注册/未声明用途守卫：`mcode` 不声明 `judge`，被要求时必须 fail-fast 指名。

### Flow or Architecture Diagram

```text
kc daemon 认领 Issue
   └─ 生命周期矩阵把 implementation 阶段解析为 mcode
        └─ resolve_lifecycle_agent → 预设/矩阵 → agent = "mcode"
             └─ build_agent_invocation
                  argv = [mcode, exec, --cwd, <wt>, --permission, full,
                          --output-format, stream-json, --input, -]
                  prompt_delivery = stdin
                  output_protocol = "mcode-json-lines"
                  └─ McodeJsonLinesOutputProtocol.relay
                       └─ relay_structured_json_lines → run_filtered_claude_stream
                            ├─ line_renderer: item.completed → 可读文本（消息/思考/工具）
                            ├─ StreamUsageCollector: sessionId（新增）+ turn.completed usage（新增分支）
                            └─ CommandResult（退出码 / transcript / token 用量 / session id）
```

### ER Diagram

不适用（无数据库表结构变化）。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: config.toml 的出厂注册块与代码内默认逐字段一致，新增 mcode 后集合与字段都不漂移
  reviewer: verifier
  real_entry: "uv run pytest -o addopts='' tests/test_agent_spec_config.py -q"
  expected: "set(config.toml 的 agents 块) == set(BUILTIN_AGENT_SPECS)（含 mcode）且每个 agent / profile 的字段逐一相等；test_default_app_config_matches_builtin_registry 通过"
  mock_boundary: "不 mock：真实读取仓库根 config.toml 文本并真实导入内置注册表"
  tier: R1
  test_layer: unit
  required_for_acceptance: true
- id: rv-2
  behavior: mcode 的各用途命令行与设计一致，且既有七个 agent 的命令行逐字节不变
  reviewer: verifier
  real_entry: "uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q"
  expected: "mcode 的四条 argv 均为 `mcode exec --cwd <wt> --permission full --output-format stream-json --input -`（含 resume 时多 `--session <sid>`），prompt_delivery 为 stdin、output_protocol 为 mcode-json-lines；codex / claude / kimi / pi / codebuddy / qoder / opencode 的快照无任何 diff"
  mock_boundary: "不 mock：真实 AppConfig 与真实 build_agent_invocation"
  tier: R1
  test_layer: unit
  required_for_acceptance: true
- id: rv-3
  behavior: mcode 的真实事件流被正确渲染，且会话 id 与 token 用量被正确观测
  reviewer: verifier
  real_entry: "uv run kc agent doctor mcode --all-profiles && uv run python scripts/probe_mcode_protocol.py"
  expected: "probe 脚本跑真实 `mcode exec --permission full --output-format stream-json --input -`，把**同一份**原始事件流同时交给平台观测器与渲染器（同源对照）：观测 session_id 与原始 sessionId 逐字相等；token_usage 的 input_tokens / output_tokens / cache_read_input_tokens 与原始 camelCase usage 的 inputTokens / outputTokens / cacheReadTokens 逐字段相等；平台 total_tokens 与 mcode 上报的 totalTokens 相等；渲染文本含 agent 最终回复正文"
  mock_boundary: "不 mock：真实 mcode CLI 进程、真实事件流；唯一可 mock 的是提示词内容"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20261116-120000-mcode-builtin-agent/rv-3-mcode-protocol.txt（探针完整输出，含原始事件流首尾各 3 行、观测值、逐字段对照表与端到端中继结果），约 30 秒自检：看「session id 一致：True」与三项箭头后的数字是否左右相等、渲染文本里有没有 agent 说的那句话"
- id: rv-4
  behavior: 真实 CLI 入口能看到 mcode，并对已声明用途打印完整命令行
  reviewer: human
  real_entry: "uv run kc agent list && uv run kc agent doctor --protocols && uv run python -c \"from backend.engines.agent_runner.factories import build_app_config; from backend.core.use_cases.agent_invocation import build_agent_invocation; from pathlib import Path; c=build_app_config(); [print(p, build_agent_invocation('mcode', p, 'golden-prompt', Path('/tmp/worktree'), c).argv) for p in ('run','deliberate','generate','repl')]\""
  expected: "list 含 mcode 且原七个名字与顺序不变（mcode 在末尾）；--protocols 输出含 mcode-json-lines 且原五个协议 id 与顺序不变；四条 argv 均为 `mcode exec --cwd <wt> --permission full --output-format stream-json --input -`"
  mock_boundary: "不 mock：真实 CLI 进程、真实配置加载、真实 PATH 探测；argv 由 `build_agent_invocation` 打印，与真实执行同一条路径"
  tier: R1
  test_layer: smoke
  required_for_acceptance: true
  note: "**为什么不用 `kc agent doctor mcode --all-profiles`**：doctor 遍历全部五种用途、遇缺即报，mcode 刻意不声明 `judge`，因此该命令会 fail-fast 而打不出 argv。`opencode` 同样如此（`not_found: agent 'opencode' has no 'generate' profile`）——这是 doctor 的既有设计，不是本次引入的问题。故改用 `build_agent_invocation` 逐用途打印真实 argv。"
  presentation: "tasks/evidence/P1-FEAT-20261116-120000-mcode-builtin-agent/rv-4-agent-doctor.txt（`kc agent list`、`kc agent doctor --protocols` 与四条真实 argv 的终端输出捕获），约 10 秒自检：看 list 里有没有 mcode、--protocols 里有没有 mcode-json-lines、四条 argv 里有没有 --permission full 与 --input -"
- id: rv-5
  behavior: 未声明 judge 用途的 mcode 被要求评审时 fail-fast 并指名，不回落
  reviewer: verifier
  real_entry: "uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q -k mcode"
  expected: "对 mcode 请求 judge 用途抛 UnknownProfileError，消息包含 agent 名与已声明用途列表；不产生任何「改用 run 用途」的回退"
  mock_boundary: "不 mock：真实内置注册表"
  tier: R1
  test_layer: unit
  required_for_acceptance: true
- id: rv-6
  behavior: 容器认证导入为 mcode 派生出正确的目标目录名
  reviewer: verifier
  real_entry: "uv run python -c 'from backend.engines.agent_runner.container_auth import SUPPORTED_AGENT_SPECS; print([(s.agent_name, s.target_subdir) for s in SUPPORTED_AGENT_SPECS])' && uv run pytest -o addopts='' tests/test_container_auth.py -q"
  expected: "输出含 ('mcode','minimax')（目标子目录由 auth_home 去掉引导点与 ~ 得到），且顺序为原七个之后追加；原有七个 agent 的派生名与顺序不变"
  mock_boundary: "不 mock 派生逻辑；不要求真实 docker build（本 PRD 不改容器镜像与 compose）"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
- id: rv-7
  behavior: console 的 agent 下拉出现 mcode，且写回被接受
  reviewer: human
  real_entry: "just run 打开 frontend-public -> Roadmap 仓库行齿轮（仓库层）与 Settings 全局矩阵（全局层）；对某行选择 mcode 并保存"
  expected: "两层下拉都含 mcode；选择并保存后返回 200，对应文件写入该值，其余内容不变"
  mock_boundary: "不 mock 前后端：真实起服、真实写盘"
  tier: R1
  test_layer: manual
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20261116-120000-mcode-builtin-agent/rv-7-console-agents-global.png 与 rv-7-console-agents-repo.png（两层下拉展开的真实截图，各含 mcode 选项），配 rv-7-console-agents.txt（候选值与写回落盘文本）。约 10 秒自检：打开两张图找 mcode 选项；再看 txt 末尾写回后是否只有 mcode 一行新增"
- id: rv-8
  behavior: 文档、配置注释、原型与打包 skill 里的内置 agent 枚举补全
  reviewer: verifier
  real_entry: "rg -n 'codex.*claude.*kimi.*pi' docs/ config.toml scripts/shared/template/sync_template.sh && rg -n 'REGISTERED_AGENTS' docs/prototypes/lifecycle-agent-matrix.html && rg -n 'codebuddy|opencode' src/backend/engines/agent_runner/templates/skills/kedacode-operator/"
  expected: "docs 与 config.toml 里每一处列举内置 agent 的位置都含 mcode；原型页的 REGISTERED_AGENTS 含 mcode；sync_template 的适配器清单含 mcode；打包 skill 若列举内置 agent 则含 mcode"
  mock_boundary: "不 mock：对仓库真实文件做文本断言"
  tier: R0
  test_layer: unit
  required_for_acceptance: true
- id: rv-9
  behavior: 全量测试与 lint 在最终树上通过
  reviewer: verifier
  real_entry: "just test all"
  expected: "全量用例通过（最终树实测 5331 passed, 1 skipped），lint --full 通过"
  mock_boundary: "不 mock：真实测试套件与真实 pre-commit 钩子"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  note: "**不要加 `CI=true`**：`just test` 内部会调 `just lint --full`，而 lint 的 `check-test-flag` 钩子要求 test 标记；`CI=true` 会让 just 走另一条分支从而在 lint 阶段自锁（实测 `CI=true just test` 报 `error: recipe 'lint' failed with exit code 1`）。`just test` 内部已用 `SKIP=check-test-flag` 正确编排二者，直接跑即可。**注意**：`just test` 走本地档只跑子集（本次 939 passed / 1306 deselected），要覆盖全量请用 `just test all`（`-n auto` 并行）。另：改了 `.py` 文件（含新增）会让 test 树指纹变化、标记失效，需重跑 `just test all` 才能让 `just lint` 通过——这是仓库的既有流程守卫。"
```

失败排查顺序：先看 `bin` 名是否写对（mcode 的 bin 就是 `mcode`）、`exec` 子命令与 `--input -` 的位置是否在拼装顺序里正确；再看 `config.toml` 出厂块与内置 spec 是否逐字段一致（守卫会指名漂移字段）；然后查新 spec 是否被追加在末尾（插入会改变标签匹配优先级）；接着查事件渲染的字段名是否与 mcode 当前版本一致（`content` vs `text`、`toolCall` vs `command_execution`）；最后查文档与原型里的枚举是否漏点。

### Low-Fidelity Prototype

- `No interactive prototype file changes in this PRD.` 本次的用户可见变化是"下拉里多一个选项"，由接口数据驱动、无新布局与新交互，静态图与 `rv-7` 的真实截图足以表达；不新建原型文件，也不修改 `docs/prototypes/lifecycle-agent-matrix.html` 的交互结构（仅按 FR-8 同步其中的 agent 名字常量）。

### Interactive Prototype Change Log

- `No interactive prototype file changes in this PRD.`

### External Validation

| Topic | Source | Checked On | Relevant Finding | Impact On Recommendation |
|---|---|---|---|---|
| MiniMax Code CLI 形态 | `mcode --help` / `mcode exec --help`（本机 v0.7.7，`/Users/zata/.local/state/fnm_multishells/.../bin/mcode`） | 2026-11-16 | 非交互入口 `mcode exec [options] [prompt]`；支持 `--cwd` / `--output-format text\|json\|stream-json` / `--model` / `--effort` / `--permission smart\|full\|off` / `--input -` / `--session <id>` / `--continue` / `--max-steps`；配置在 `~/.minimax` | 注册名与 bin 同为 `mcode`；`run` 走 `exec --output-format stream-json`；提示词走 `--input -` + stdin |
| mcode 事件 schema | 实测 `mcode exec --permission full --output-format stream-json`（本机 v0.7.7） | 2026-11-16 | 自有信封：`exec.started` / `session.started` / `turn.started` / `item.started` / `item.updated` / `item.completed` / `turn.completed` / `turn.failed` / `exec.completed`；item 类型为 `agent_message`（字段 `content`）/ `reasoning` / `tool_call`（字段 `toolCall`，内含 `id`/`name`/`status`/`input`/`output`） | 必须新写协议，不能复用 codex / claude 协议（§6 备选 B 否决的直接依据） |
| mcode 会话 id 与用量字段名 | 同上实测 | 2026-11-16 | 会话 id 在**顶层** `sessionId`（camelCase，每条事件都带）；用量在 `turn.completed.usage` 的 `inputTokens` / `outputTokens` / `cacheReadTokens` / `totalTokens`（camelCase） | keda 现有解析器两处都不认 → FR-6 必须补 |
| mcode 续传能力 | 实测 `--session <sid>` 续传同一 session | 2026-11-16 | `--session` 有效且**真的延续上下文**（首轮约定暗号、次轮问出答案） | `supports_resume = true`，`resume_args = ["--session", "{session_id}"]` |
| mcode 权限档真实语义 | 实测 `--permission off` 执行写文件与删除文件 | 2026-11-16 | **`off` 并不关闭工具**：仍成功写入 `/tmp/mcode_off_probe.txt` 并读回；仍成功删除文件。`--help` 写明 `ask requires TUI/ACP`——`off` 关的是交互式询问，不是工具能力 | 决策二：不能拿 `off` 当只读保证；mcode 无 judge 用途（决策一） |
| mcode 在 smart 档的工具可用性 | 实测 `--permission smart`（默认档）执行 `ls` | 2026-11-16 | 非交互模式下工具正常执行，未卡在确认上 | 说明无人值守场景用 `full` 更稳（不依赖 smart 策略的自动放行行为），且与既有 agent 的 yolo 语义对齐 |
| mcode 认证与 skills 目录 | `ls ~/.minimax`（本机） | 2026-11-16 | 配置根 `~/.minimax`；`skills/`（用户级，纯用户资产）、`.builtin-skills/`、`.internal-skills/`（内置，不应进容器）；`auth/prod/cn`（认证产物）；`config.yaml`（含 provider 与默认模型 `minimax/MiniMax-M3.1-Flash`）。运行时状态（`sessions` / `cache` / `logs` / `background-tasks` / `sqlite.db` 等）都在配置根下但**不需显式排除**——容器认证导入是**白名单**语义，只复制 `auth_include` 列出的条目 | `auth_home = "~/.minimax"`；`auth_include` = `auth` / `config.yaml` / `skills`；`auth_exclude` 只保留 `.builtin-skills` / `.internal-skills` 作为白名单内的兜底 |
| mcode 模型与推理档 | 实测 `--model minimax/MiniMax-M3.1-Flash --effort high` | 2026-11-16 | 两个旗标都接受；`--model` 形如 `<provider>/<model>`（provider 见 `mcode provider list`：`minimax_oauth` / `minimax_api`） | 可声明 `model_args` / `reasoning_effort_args`（**实施期核对旗标名后再定**：本轮未核实 `--effort` 是否即 keda 语义里的 reasoning effort 档位，暂不声明，宁缺勿假） |

## 8. Delivery Dependencies

### Delivery Dependencies

- Group: builtin-agent-coverage
- Depends on tasks/issues:
  - none
- Gate type: none
- Notes: 与 `tasks/pending/` 中既有 PRD 无重叠、无顺序依赖。本 PRD 无内部前置，FR 之间除 FR-4→FR-6（协议先于观测解析生效）外无先后依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

- [x] 本 PRD 的三个决策（mcode 不提供 judge 用途、mcode 的只读是约定非强制、提示词走 stdin）已获人确认
- [x] `rv-3` 的真实事件流证据已呈递：agent 最终回复正文、session id 与 token 用量三项都在（同源对照逐字段相等，见 `rv-3-mcode-protocol.txt`）
- [ ] `rv-4` 的 doctor 输出与 `rv-7` 的 console 截图已呈递（rv-4 已呈递；真实页面已操作并确认，但截图未能导出到证据目录）

### 9.2 Acceptance Evidence Package

- [x] `rv-1`…`rv-6`、`rv-8` 的机器层真实入口均已执行并留证（证据目录 `tasks/evidence/P1-FEAT-20261116-120000-mcode-builtin-agent/`）；rv-7 已做真实 UI 操作，但截图文件缺失
- [x] `rv-3` 的证据写明 camelCase 用量到 `TokenUsage` 的最终映射关系（1:1 直映射，`inputTokens + cacheReadTokens + outputTokens == totalTokens`）
- [x] `just test all` 与 `just lint --full` 在最终树上通过（5331 passed, 1 skipped；lint 通过）
- [ ] `rv-7`（console 两层下拉截图文件与写回落盘）已完整留证——页面观察和仓库层写回已验证，截图文件未能导出

### 9.3 执行结果概览

| rv | 状态 | 备注 |
|---|---|---|
| rv-1 | ✅ | 出厂块与内置 spec 逐字段一致 |
| rv-2 | ✅ | 五条新形态正确；既有七条快照零 diff |
| rv-3 | ✅ | 真实 mcode CLI 端到端：渲染 / session id / 用量三项全对 |
| rv-4 | ✅ | 真实 CLI 入口（argv 由 `build_agent_invocation` 打印，与真实执行同路径） |
| rv-5 | ✅ | 未声明 `judge` 时 fail-fast 并指名 |
| rv-6 | ✅ | 容器认证派生目标目录 `minimax`，顺序在原七个之后 |
| rv-7 | ⬜ | **真实 UI 操作与仓库层写回已验证**；截图在 CUA 展示中观察到，但工具不支持导出到证据目录 |
| rv-8 | ✅ | 文档 / 原型 / sync_template 适配器清单枚举补全 |
| rv-9 | ✅ | 全量测试 5331 passed / 1 skipped；`just lint --reuse` 与 `just lint --full` 通过 |

> **归档前置**：独立 verifier 已对实现与已确认需求复核 `PASS`。rv-7 截图因当前工具能力无法留存，使用仓库允许的阻塞归档路径；此归档只代表代码落地，不代表验收完成。

## 10. Functional Requirements

- **FR-1**：`mcode` 必须作为内置默认 agent 出现在 `BUILTIN_AGENT_SPECS` 中，**追加在注册表末尾**，不改变任何既有 agent 的注册顺序。
- **FR-2**：`mcode` 必须声明 `run` / `deliberate` / `generate` / `repl` 四个用途，**不声明 `judge`**；被要求 `judge` 时必须 fail-fast 并指名该 agent 已声明的用途，不静默回落。
- **FR-3**：`mcode` 的注册块必须声明 `bin = "mcode"`、`label = "agent/mcode"`、`auth_home = "~/.minimax"`、`supports_resume = true` 与 `resume_args = ["--session", "{session_id}"]`；`deliberate` / `generate` 的 `read_only = true`，`read_only_enforced` 保持 `false`。
- **FR-4**：必须新增 `mcode-json-lines` 输出协议（id 常量定义在 `core/shared/models/agent_spec.py`，实现放 `engines/agent_runner/output_protocols/`，经 `pyproject.toml` 的 entry point 注册），渲染 agent 消息、思考与工具调用三类事件。
- **FR-5**：`mcode` 的四个用途必须使用 `--permission full`（yolo）与 `--output-format stream-json`；提示词必须经 `--input -` 走 stdin（`prompt_delivery = "stdin"`），不得进 argv。
- **FR-6**：`extract_session_id_event` 必须识别顶层 `sessionId`；token 用量解析必须支持 mcode 的 camelCase 形状（`inputTokens` / `outputTokens` / `cacheReadTokens`），且不得与 codex 的 snake_case 分支混算。
- **FR-7**：`config.toml` 必须包含与 `BUILTIN_AGENT_SPECS` 逐字段一致的 mcode 出厂注册块。
- **FR-8**：文档（`docs/guides/agent-runner.md`）、原型（`docs/prototypes/lifecycle-agent-matrix.{html,md}`）、模板 skill 同步清单（`scripts/shared/template/sync_template.sh`）、打包 skill（`kedacode-operator`）与测试中的内置 agent 枚举必须同步补全 mcode。
- **FR-9**：不得改动任何既有默认值（`default_agent`、`agent_fallback_order`、deliberation 默认 profile、任何既有 agent 的 spec 字段）。

## 11. Non-Goals

- 不改 `default_agent` 与 `agent_fallback_order`（mcode 刻意不进回退链）。
- 不改任何既有 agent 的 spec 字段与 argv。
- 不给注册表新增任何字段（不引入"按用途注入环境变量"这类能力——本 PRD 没有消费者）。
- 不新增 `kc` 子命令、旗标或退出码。
- 不给 mcode 声明 `judge` 用途（决策一）。
- 不把 `--permission off` 当只读保证、不把它写进注册块（决策二）。
- 不改前端代码（agent 下拉由接口数据驱动）。
- 不改数据库表结构。
- 不给 runner 容器镜像预装 mcode、不改容器 compose（与 codebuddy/qoder/opencode 那轮一致：镜像预装属非声明式改动，且本机无法验证镜像构建）。
- 不接 mcode 的 `acp` 子命令（§6 备选 D，留作后续项）。
- 不纳入 `dsh`（与上一轮 PRD 相同的排除理由：developer preview、无命令行权限开关）。

## 12. Risks And Follow-Ups

- **容器认证导入的副作用**：注册表一变，`kc container auth import` 的产物会多出 `minimax` 目录（`auth_home` 去掉 `~` 与引导点得到）。这是既有派生逻辑的自然结果，不改代码；本 PRD 不预装 mcode CLI 到镜像，因此该目录暂不会被消费。
- **mcode 版本漂移**：事件 schema 属于 CLI 实现细节，升级可能改字段名。渲染器对未知事件宽容跳过，但"跳过"意味着 live 面板可能变空。文档需记录本轮锁定的版本号（v0.7.7）与复核方法（跑 rv-3 的 probe 脚本）。
- **`--permission off` 的命名陷阱**：实测它不关闭工具。后续若有人想用它做只读，会得到假保证。已写入决策二与文档速查。
- **后续项（不在本 PRD）**：接 mcode 的 `acp` 协议作为独立进程交互模式；给注册表加"按用途注入环境变量"能力（届时与 dsh 一起交付）；把 mcode 纳入 `agent_fallback_order` 作为显式用户决策。

## 13. Decision Log

### Final Reconciliation

- Interpretation: 需求按已确认的三项决策实现：mcode 不提供 judge、只读是约定、提示词通过 stdin 投递；`mcode` 仅追加到内置注册表，不改既有默认 agent 与回退顺序。
- Public behavior and contracts: `kc agent list` / doctor、新增 mcode JSONL renderer、会话与 token 观测、console 两层可选及仓库层写回均与本 PRD 契约一致；`read_only_enforced` 对 mcode 保持 false。
- Related PRD status: 本次没有关联 PRD 或前置依赖。
- Requirements and risks: FR-1…FR-9 已落地并通过全量测试与 lint；mcode v0.7.7 schema 漂移风险已记录，未声明 judge 与强制只读能力。
- Validation blocker: 当前 CUA 工具无法把真实 console 截图导出到仓库，rv-7 要求的两张截图文件缺失。
- Validation follow-up: 用可保存图片的真实浏览器入口补齐全局/仓库下拉截图及写回文本，复核 mcode 在两层均可见且配置只写入预期内容，再更新验收横幅。
- 三个决策均已确认（§2 决策一/二/三）。
- 实施完成：内置注册表 + 新协议 + 观测层 + 出厂块 + 文档 / 原型 / skill 同步 + 测试，全部落地。
- rv-1…rv-6 / rv-8 / rv-9 已通过并留证，独立 verifier 通过；rv-7 的界面操作和仓库写回已验证，但截图文件不能留存。
- 因 rv-7 的证据阻塞，PRD 使用 `⏸️ 验证阻塞` 路径；归档仅表示代码落地，不等于验证或人工验收完成。

## 14. Change Log

### 初稿生成

- 按用户选择（内置默认 agent / `--permission full` / 全局可用）起草。
- 全部 CLI 契约均在本机 mcode v0.7.7 上实测确认，写入 §7.6 External Validation。

### 实施：内置注册表 + 新协议 + 观测层

- 内置注册表追加 `mcode` spec（`agent_spec.py`），协议 id 常量 `MCODE_JSON_LINES_PROTOCOL_ID` 同处定义。
- 新增 `mcode-json-lines` 协议实现（`output_protocols/mcode_json_lines.py`），经 entry point 注册。
- 观测层补两处字段识别：`sessionId`（顶层 camelCase）与 mcode 的 camelCase token 用量。
- 补齐出厂配置块、文档、原型、模板 skill 适配器清单与四处测试写死清单。
- **doctor 的沙箱告警保留**：mcode 的 `--permission full` 是 yolo 执行档，不提供沙箱或只读保证，因此 writable profile 继续显示「writable but declares no sandbox/approval flag」告警。它不应被当作沙箱标记。

### camelCase 用量映射已实测闭环
- 初稿把它列为「唯一有实现不确定性的点」。实施期用 `scripts/probe_mcode_protocol.py` 做**同源对照**（同一份原始事件流同时喂给观测器与渲染器），结论：mcode 的 `inputTokens` **不含**缓存命中（`inputTokens + cacheReadTokens + outputTokens == totalTokens`），与 keda 的 Anthropic 口径一致，**1:1 直映射**、无需换算。
- 实测对照：原始 `inputTokens=15403 / outputTokens=429 / cacheReadTokens=20224 / totalTokens=36056` ↔ 平台观测 `input_tokens=15403 / output_tokens=429 / cache_read_input_tokens=20224 / total_tokens=36056`，逐字段相等；session id 逐字相等。
- 实现上按 usage 里的键名区分 provider（`cacheReadTokens` 在场即 mcode 形状），不做字段级混合——codex 的 `turn.completed` 用 snake_case、mcode 用 camelCase，两套口径混算会重复计费。已加单测锁定两段相加而非互相污染。

### 最终复核与阻塞归档

- 全量测试：`just test all` → 5331 passed, 1 skipped；`just lint --reuse`、`just lint --full` 与 `uv run mkdocs build` 通过。
- 独立代码复核 PASS；复核关注的 `read_only` / `read_only_enforced` 差异符合已确认的决策二，mcode 不被作为强制只读的停滞诊断器。
- 真实 console 两层下拉观察及仓库层写回已完成，但工具无法把截图导出至证据目录。故本 PRD 以 `⏸️ 验证阻塞` 归档，保留 rv-7 空框；不表示人工验收完成。

### `auth_exclude` 收敛为白名单内的兜底

- 初稿按"运行时状态目录"直觉给 `auth_exclude` 列了 `background-tasks` / `cache` / `logs` 等。实施期读 `container_auth.py` 才发现：**导入是白名单语义**——只复制 `auth_include` 列出的条目，`auth_exclude` 只是"白名单内也排除"的兜底。因此初稿列的那些运行时目录**根本不会被复制**，属于无效声明。
- 收敛为 `.builtin-skills` / `.internal-skills` 两项（内置 skills 随 CLI 分发、不是用户资产），并在代码与文档里注明白名单语义，避免后续维护者重复加无效排除项。
