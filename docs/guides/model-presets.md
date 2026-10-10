# Agent 模型预设（Model Presets in the Lifecycle）

> 配套 PRD：`tasks/archive/P1-FEAT-20260930-130445-agent-model-preset-switching.md`（交付后归档路径）。

一个**命名预设 = 一组（agent + 模型 + 推理档）**。运维者在配置里定义 `plan` / `work` 之类预设；
keda 拉起 agent CLI 时按 agent 级声明式模板注入对应的模型与推理深度参数，不用再手工改各 CLI 的
全局配置。预设再通过 **阶段 → 预设绑定层** 接进九个生命周期阶段——校验用强模型慢推理、实现用快
模型高推理，整条流水线自动生效，不依赖人在每条命令上传参。

完全可选：预设、绑定、命令行旗标三者都可以不设置；任何一层都没设时，九个阶段的行为与过去
**逐字节一致**（argv 黄金快照零 diff）。

## 1. 定义预设

在 `config.toml`（全局）或仓库 `.kedacode.toml`（仓库级，同键覆盖全局）写：

```toml
[agent_runner.presets.plan]
agent = "codebuddy"
model = "glm-5.3-flash"
reasoning_effort = "max"

[agent_runner.presets.work]
agent = "codebuddy"
model = "deepseek-v4.1-flash"
reasoning_effort = "high"
```

- `agent` 必填，须已注册（`[agent_runner.agents.<name>]` 或内置）。
- `model` / `reasoning_effort` 可选；两者都缺省时预设只改 agent 路由，不改 argv。
- 未知预设名在解析期 fail-fast（带已定义清单），绝不静默回落——"切了模型"不能成为假象。

枚举全部预设：`kc agent presets`。

## 2. Agent 级模型参数模板

模型参数怎么注入由**每个 agent 自己声明**（`[agent_runner.agents.<name>]`）：

```toml
[agent_runner.agents.codebuddy]
model_args = ["--model", "{model}"]
reasoning_effort_args = ["--settings", "{\"reasoningEffort\":\"{effort}\"}"]
```

- 七个内置 agent 都声明了 `model_args`：`claude` / `codebuddy` / `qoder` 用 `--model`，
  `codex` / `kimi` / `opencode` 用 `-m/--model`，`pi` 用 `--model`（后四个以本机
  `--help` 确认，未做端到端实跑）。
- `reasoning_effort_args` 只有 `codebuddy`（`--settings reasoningEffort`）、
  `qoder`（`--reasoning-effort`）与 `pi`（`--thinking`）声明；其余 agent 保持为空。
- **为空表示"未核实语法"**：该 agent 命中带模型/推理档的绑定时直接报错
  （`ModelNotSupportedError`，指名 agent 与缺失模板），绝不静默忽略。
- 注入位置：profile `args` 之后、展开器与 `tail_args` 之前；占位符闭集扩为
  `{cwd}` / `{worktree}` / `{prompt}` / `{model}` / `{effort}`。
- 预览注入结果：`kc agent doctor <agent> --preset <名> [--json]`（doctor 是 what-if 工具，
  不做"执行 agent == 预设 agent"的丢弃判定，模板缺失时如实报错）。

## 3. 阶段 → 预设 绑定

```toml
[agent_runner.lifecycle_presets]
verifier = "plan"        # 校验用强模型慢推理
implementation = "work"  # 实现用快模型高推理
```

九键闭集与生命周期 Agent 矩阵同键（implementation / fix / closeout / verifier / review /
supervisor / planner / content_generation / deliberate）。

**绑定后该阶段整体由预设决定**：agent、模型、推理档都取预设声明——遮蔽矩阵同键的 agent
声明。生效 agent 也随之变化（`resolve_lifecycle_agent` 返回预设声明的 agent）。

优先级（高到低）：

1. 命令行显式 `--agent`（绑定让位，模型绑定由换人丢弃规则处理）；
2. 命令行 `--preset` / `--model` / `--reasoning-effort`（一次性锚定 + 同名字段覆盖）；
3. PRD 头部 `lifecycle_presets` 块（除 planner 外八键，随 Issue 流动）；
4. 仓库 `.kedacode.toml` > 全局 `config.toml`（同键仓库层赢）。

未绑定预设的阶段：继续走生命周期矩阵 / 既有散落键 / 内置默认，行为零变化。

### executor 阶段的继承

`fix` / `closeout` 未自绑预设时跟随实现者，并**继承实现阶段的模型绑定**（同一 agent、同一
模型命名空间，不算换人）。它们自绑预设时用自己的绑定。

## 4. 换人丢弃规则

不同 CLI 的模型命名空间互不相通（codebuddy 的 `--settings` 对 claude 无意义），因此：

- 执行 agent 与预设声明 agent **不一致**（跨 agent 回退、显式 `--agent` 换人）时，模型绑定
  整体丢弃并写 WARN 日志；绝不把 A CLI 的模型参数塞给 B CLI。
- 同 agent 的原地瞬态重试不换人，绑定保持有效。
- 绑定生效的 attempt 在账本（`attempt_records`，schema v6）写入实际生效的 `preset` / `model`；
  未绑定或被丢弃为 NULL。

## 5. 命令行一次性旗标

| 命令 | 锚定阶段 | 旗标 |
|---|---|---|
| `kc run` / `kc daemon` | implementation | `--preset <名> [--model <id>] [--reasoning-effort <档>]` |
| `kc review` / `kc review-daemon` | supervisor | 同上 |
| `kc ask` | planner | 同上 |
| `kc issue create` | content_generation | 同上 |
| `kc agent doctor` | what-if 预览 | `--preset` / `--model` / `--reasoning-effort` / `--lifecycle <键>` |

- `--preset` 把该阶段锚定到指定预设并**覆盖绑定同名字段**；`--model` / `--reasoning-effort`
  是预设同名字段的一次性覆盖（必须与 `--preset` 同用）。
- 仓库层 `.kedacode.toml` 的显式绑定仍会赢过 CLI 合成的全局层绑定；PRD 块（随 Issue 流动）最高。
- PRD 头部块写法（与 `lifecycle_agents` 块同型）：

```markdown
- lifecycle_presets:
  - verifier: plan
  - review: work
```

## 6. 排查与观测

- `kc agent presets`：列出全部预设。
- `kc agent doctor <agent> --preset <名>`：预览将被执行的完整 argv。
- `kc agent doctor --lifecycle <键>`：按阶段视角打印"解析出的 agent + 绑定的模型参数"；
  fix / closeout 在无实现者上下文时如实返回 `follows_implementation`。
- attempt 账本 `attempt_records` 新增 `preset` / `model` 可空列（schema v6，自动迁移）。

## 7. 统一设置页与持久化 CLI（生命周期 Agent / 模型 / 推理深度）

上述预设与绑定过去只能在多份 TOML 与逐阶段 doctor 之间手工拼；现在有一个跨全局 / 仓库
范围的**统一设置页**和一组**持久化 CLI**，读写同一份 `presets` / `lifecycle_presets` /
回退候选事实源（不新建第二套配置、不入库）。

### 7.1 Settings 统一设置页

路由 `/app/settings/lifecycle/`（入口：Settings「Agent 管理」区下方卡片，或 Backlog 仓库行
齿轮带仓库直接进入并预选）。一页承载三块，页首有锚点导航：

- **生命周期矩阵**：九阶段各自的最终生效 Agent / 模型 / 推理深度 / 绑定预设与**逐字段来源**
  （预设 / 继承 / Agent 默认 / 未支持，或来自哪一层）。缺省与不支持如实标示，不猜外部 CLI
  默认。阶段通过**绑定命名预设**设定显式值；`fix` / `closeout` 未绑定时继承实现阶段。
- **模型预设**：双列卡片 upsert `(agent, model, reasoning_effort)` 三元组，编辑前展示该共享
  预设当前绑定的所有阶段；删除预设连带解绑。
- **执行器回退候选**：有序候选链，每个候选可选绑定**同 agent** 的预设（见 §7.3）；页面这一区
  **固定写入全局 `config.toml`**，不随生命周期范围切换改变落点。但它显示的是所选范围内的
  **有效**链——若该仓库自己声明了候选数组（§7.2 的 `--scope repository` 写法），仓库数组对该
  仓库整体接管机器级链，此时在仓库范围保存只会改到机器级文件、对该仓库不生效；要动这个仓库
  的链，用 CLI 往该仓库写，或删掉仓库里那一段让它回到继承。

范围选择器在「全局」（`config.toml`）与「仓库」（该仓库 `.kedacode.toml`）间切换；页面展示
的是所选**基线**，PRD 头部覆盖与单次 `--preset/--model/--reasoning-effort` 仍可在其上进一步
覆写（页面会提示，不把基线冒充成某个 PRD 的最终值）。保存只提交改动过的键，写前完整校验、
写后从磁盘重载返回新视图。

### 7.2 持久化 CLI

与页面调用同一 core 用例 / TOML editor。**读取可自动推断 effective 范围；所有写入必须显式
`--scope`，仓库写入必须显式给 `--repo-id`（或 `--repo`）**，避免脚本在 cwd 不明时写错文件：

```text
kc agent lifecycle list [--scope effective|global|repository] [--repo-id <id>] [--output json]
kc agent lifecycle set   <stage> --preset <name> --scope global|repository [--repo-id <id>]
kc agent lifecycle unset <stage>                --scope global|repository [--repo-id <id>]
kc agent preset set      <name> --agent <agent> [--model <id>] [--reasoning-effort <档>]
                          --scope global|repository [--repo-id <id>]
kc agent fallback list   [--scope effective|global|repository] [--repo-id <id>] [--output json]
kc agent fallback candidate add    --agent <a> [--preset <p>] [--position <n>] --scope … [--repo-id <id>]
kc agent fallback candidate preset set   <position> --preset <p> --scope … [--repo-id <id>]
kc agent fallback candidate preset unset <position>            --scope … [--repo-id <id>]
kc agent fallback candidate remove <position> --scope … [--repo-id <id>]
kc agent fallback candidate move   <position> --to <n>  --scope … [--repo-id <id>]
```

- `lifecycle list` / `fallback list` 输出固定九行 / 有序候选，`--output json`（或 `--json`）字段
  名稳定、顺序确定，含 scope / repo id 与来源；写命令成功后重新打印生效视图（写后 fresh 读）。
- `preset set` 是 **upsert**：省略 `--model` / `--reasoning-effort` 表示该预设不显式设置该字段。
  要设三元组先 `preset set` 再 `lifecycle set` 绑定；`lifecycle unset` 只删当前层该阶段的绑定
  （仓库层清除后回落全局，全局清除后回落既有直接 Agent / legacy / 内置默认）。
- 未知 stage、未注册 agent、未知预设、预设 agent 与候选执行器不一致、给缺 `reasoning_effort_args`
  模板的 agent 设非空 effort——都在**写文件或调用 agent 前**返回可诊断的非零退出，目标文件字节不变。
- `--scope global` 与 `--repo-id` / `--repo` **互斥**：全局层写的是机器级 `config.toml`，仓库选择器
  在这里没有任何作用，同时给出按用法错误退出（2），不静默落到机器级配置。预设名写入前去掉首尾
  空白，落盘键与可绑定名字始终是同一个。
- 候选命令的 `--scope repository` 写的是**该仓库配置文件**里的
  `[[agent_runner.runner.agent_fallback_candidates]]`（不是机器级文件）。数组表按**整体接管**
  合并：写入前会把该仓库当前的**生效**候选链（机器级数组，或旧 `agent_fallback_order` 的折叠
  结果）连同新候选一起物化进仓库文件，并写下该仓库的 `max_agent_switches`；此后该仓库不再跟随
  机器级候选链的改动。所以只想调整机器级链请用 `--scope global`；要让某仓库回到继承状态，删掉
  仓库文件里那一段候选表。页面「执行器回退」区不写仓库文件（见 §7.1）。
- 既有 `kc agent presets` 与 `kc agent doctor` 保持兼容；单次运行旗标不调用这些持久化命令。

### 7.3 执行器回退候选预设（FR-9 / FR-10）

回退候选链有两种写法，运行时以有序数组表为事实源：

```toml
[[agent_runner.runner.agent_fallback_candidates]]
agent  = "claude"
preset = "claude-max"     # 可选；该候选被轮到时用这个预设的模型 / 推理档

[[agent_runner.runner.agent_fallback_candidates]]
agent  = "claude"          # 同一 agent 可用不同预设再来一步
preset = "claude-high"

[agent_runner.runner]
max_agent_switches = 3     # 预算按候选步数计，同 agent 另一预设也各占一步
```

- 数组非空时整体接管候选链；为空时读旧 `agent_fallback_order` 字符串列表，折叠成**无预设**
  候选，行为逐字节不变。
- 去重单位是 `(agent, preset)`：完全相同的组合被拒绝，同一 agent 的另一预设是独立候选。
- 候选 preset 的 agent 必须与候选 agent 一致；绑定的预设若带模型 / 推理值，须有对应参数模板
  （复用生命周期预设校验）。
- 预设**只在链条轮到该候选时**应用；一次性 `--agent` 换人不消费此映射；主 agent 的模型绑定在
  换人时丢弃（各 CLI 命名空间不同），与"候选自带 preset"是两条独立路径。
- 数组表**也能按仓库声明**（该仓库 `.kedacode.toml`）：仓库数组非空时对该仓库整体接管机器级
  链，不与机器级逐条合并；为空时继续继承。写入方式见 §7.2 的 `--scope repository`。
