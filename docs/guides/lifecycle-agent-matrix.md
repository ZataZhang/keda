# 生命周期 Agent 矩阵

keda 的流水线由九个生命周期阶段组成，每个阶段调用一个 agent（codex / claude / kimi /
pi / codebuddy / qoder / opencode 或自定义注册的 agent）。**生命周期 Agent 矩阵**把"阶段 → agent"从
散落的配置段收敛成一张表，并支持仓库级与 PRD 级覆盖。

> 交互原型：`docs/prototypes/lifecycle-agent-matrix.html`（真实截图 + 覆盖层）。

## 键名与取值（权威定义）

矩阵键名是**闭集**，写错键在配置加载期直接报错，不会静默忽略：

| 键 | 中文名 | 是否接受 `auto` | 是否接受 `executor` | 未声明时回落到的既有配置键 |
|---|---|---|---|---|
| `implementation` | 实现 | ✅ 按 Issue 上的 `agent/*` 标签路由 | ❌ | `runner.default_agent` |
| `fix` | 修复 | ❌ | ✅（默认） | 跟随实现阶段 agent |
| `closeout` | 收尾 | ❌ | ✅（默认） | 跟随实现阶段 agent |
| `verifier` | 校验 | ✅ 从回退顺序里挑第一个 ≠ 实现者 | ❌ | `validation.verifier_agent` |
| `review` | 审核 | ✅ 优先 ≠ 实现者；`allow_same_agent` 时沿用实现者 | ❌ | `pre_pr_review.review_agent` |
| `supervisor` | 监督 | ✅ 发布路径沿用本次实现者 | ❌ | `post_pr_supervisor.supervisor_agent` |
| `planner` | 决策 | ❌ | ❌ | `interactive_decision.default_agent` |
| `content_generation` | 内容生成 | ❌ | ❌ | `generated_content.default_agent` |
| `deliberate` | 辩论 | ✅ 按 Issue 上的 `agent/deliberate` 标签路由 | ❌ | `deliberation.default_synthesizer` |

取值域：

- **已注册 agent 名**（`codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` /
  `opencode`，或你在
  `[agent_runner.agents.<name>]` 里注册的 agent）——未注册的名字在**该阶段开始前**
  fail-fast 报错并指名，不会静默回落到别的 agent；
- **`auto`**——按该阶段**既有**语义路由（含义逐阶段不同，见上表），不做统一。
  既有配置键配成**具体 agent** 时 `auto` 一律先用它（`auto` 是"沿用既有语义"，
  不是"跳过既有键"），所以 console 呈递的生效值与 runner 实际使用的 agent 一致；
- **`executor`**——跟随实现阶段选中的 agent，**仅** `fix` / `closeout` 合法
  （其余键写 `executor` 会形成循环引用，遇到即报配置错误）。

`repl`（交互式会话）不算流水线生命周期，不在矩阵内。

## 声明与覆盖优先级

优先级从高到低：

1. **PRD 文件头部** `lifecycle_agents` 覆盖块（PRD 级，只影响该 PRD）；
2. **仓库 `.iar.toml`** 的 `[agent_runner.lifecycle_agents]`（仓库级）；
3. **全局 `config.toml`** 的 `[agent_runner.lifecycle_agents]`（机器级）；
4. **既有散落配置键**（上表最后一列）；
5. **内置默认**。

同键时仓库层赢过全局层；仓库层未声明的键回落全局层。**既有配置键不迁移、
不删除**——`legacy` 层继续生效，所以不写矩阵时所有阶段行为与今天完全一致。

### 全局 / 仓库层：`[agent_runner.lifecycle_agents]`

```toml
[agent_runner.lifecycle_agents]
fix = "kimi"
verifier = "codex"
closeout = "executor"   # 跟随实现者（默认）
```

`config.toml` 里给出的是**全注释模板**，取消注释即生效。

### PRD 层：文件头部 `lifecycle_agents` 块

PRD markdown 标题下的 bullet 区（与 §8 依赖声明同型）：

```markdown
- lifecycle_agents:
  - implementation: claude
  - review: codex
```

只影响该 PRD 的执行；未声明的阶段继续走仓库层与全局层。未知键名与非法取值在
解析时报错并带上 PRD 路径。

块只在**头部 bullet 区**（H1 标题之后、第一个非 bullet 行之前）被识别：正文里
引用这段语法的段落不会被误当成覆盖块，写回也只改头部那一块。

PRD 覆盖的生效范围是**有 PRD / Issue 上下文的八个阶段**：`implementation`、
`fix`、`closeout`、`verifier`、`review`、`supervisor`、`content_generation`、
`deliberate`。**`planner` 不支持 PRD 覆盖**——它的唯一消费点是 `iar ask`
（交互式决策），既没有 Issue 也没有 PRD 上下文，所以 console 的「Agent 覆盖」
抽屉不提供该行、写回也会拒绝该键。`planner` 的**矩阵值**（`config.toml` /
`.iar.toml`）仍然有效。

## console 界面落点：三层各写各自文件

| 层 | 写入文件 | 入口 |
|---|---|---|
| 全局（机器级） | `config.toml` | **Settings → 「Agent 管理」→ Tab ②「生命周期 Agent 设置」** |
| 仓库级 | 该仓库 `.iar.toml` | **Roadmap → 受管理仓库列表每行右侧的齿轮** |
| PRD 级 | 该 PRD 文件头部 | **PRD 原文页工具栏「Agent 覆盖」** |

Settings 的「Agent 管理」区块用粘性 Tab 分两页：**Tab ①「Agent 标签设置」**
（默认页）编辑每个 agent 的 GitHub 路由标签名 / 颜色 / 描述——这是 `auto`
解析 Issue 标签的依据；**Tab ②「生命周期 Agent 设置」**放九行矩阵与
**agent 回退顺序**。

矩阵行的交互口径（三层一致）：下拉里**只有真实取值**（已注册 agent / 该阶段的
`auto` / fix-closeout 的 `executor`），**当前生效值直接选中**；「当前值来源」列
说明它来自哪一层，本层已显式设置时给出恢复入口（全局层「不写本键（跟随既有
配置）」/ 仓库层「跟随全局（删除本键）」）；**只有改动过的行会写进本层文件**。
写入一律保留式——只动点名的那几个键，文件其余内容与格式（含注释）不变。

> 只有生命周期矩阵是三层可写的。**「Agent 标签设置」与「agent 回退顺序」是机器级
> 配置**（PRD FR-10 / FR-12：两者都在 Settings 页），对应 API 无论是否带 `repo_id`
> 都只写全局 `config.toml`；`repo_id` 仅用于选取校验视角（认可该仓库级注册的 agent）。

## 跨 agent 回退顺序（与矩阵是两件事）

矩阵只选**一个**主 agent；"主 agent 崩溃或额度受限时换下一个"由
`[agent_runner.runner]` 的 `agent_fallback_order`（默认
`["claude", "kimi", "codex"]`）与 `max_agent_switches`（默认 `2`，即最多试 3 个
agent）驱动，**全阶段共用一条链**，不在矩阵里按阶段各排一条。第一个尝试的
agent 由矩阵 / 标签路由决定；本机未安装的 agent 在运行时跳过。Settings 的
「生命周期 Agent 设置」页给这一对键一个可排序编辑入口。

## 相关

- Agent 注册表（`[agent_runner.agents.<name>]`）与调用形态：见
  [配置说明](configuration.md) 与 [Agent Runner](agent-runner.md)。
- 代码内权威定义：`src/backend/core/shared/models/lifecycle_agent.py`；
  解析函数：`src/backend/core/use_cases/lifecycle_agent_resolution.py`。
