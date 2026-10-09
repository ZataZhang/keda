# 生命周期 Agent 矩阵

keda 有九个生命周期阶段，每个阶段调用一个 agent（codex / claude / kimi /
pi / codebuddy / qoder / opencode 或自定义注册的 agent）。**生命周期 Agent 矩阵**把"阶段 → agent"从
散落的配置段收敛成一张表，并支持仓库级与 PRD 级覆盖。

九个阶段**不在同一条流水线上**：它们分布在三个互相独立的入口，外加一个横跨全程的
内容生成阶段——见下文「各阶段在哪触发」一节。

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

## 各阶段在哪触发（消费点）

上一张表回答"这个键能配什么"，这一张回答"配了之后什么时候被读"。九个键分布在
三个互相独立的 CLI 入口，外加一个横跨全程的内容生成阶段。

console 的生命周期入口（统一设置页
[生命周期、模型与推理深度设置](#console) 的全局 / 仓库范围、
以及 PRD 覆盖抽屉）**按同一分组呈现**：组标题与每行触发时机来自只读视图下发，分组事实的
唯一代码定义是 `src/backend/core/shared/models/lifecycle_agent.py` 的
`LIFECYCLE_AGENT_ENTRY_GROUPS`（前端不持有第二份映射），与本表的三组一一对应：

> **两份手写副本，需人工同步。** 九阶段的触发入口分组在代码里只有一份定义
> （`src/backend/core/shared/models/lifecycle_agent.py` 的
> `LIFECYCLE_AGENT_ENTRY_GROUPS`），只读视图下发时按它分组。PRD 覆盖抽屉
> （`frontend-public/components/agent-runner/prd-agent-override-sheet.tsx`）直接消费
> 视图下发，不持有映射。需要人工核对的自包含副本有两处：交互原型
> `docs/prototypes/lifecycle-agent-matrix.html`（不能 import 后端常量，内联一份）与
> 对应的 e2e 用例 `tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts`
> 的 PRD 覆盖断言（独立 TS 包，只能自己声明要断言的组 id）。两者都不含
> `LIFECYCLE_AGENT_ENTRY*` / `LIFECYCLE_AGENT_KEYS` 字面量，因此
> `rg -n 'LIFECYCLE_AGENT_ENTRY|LIFECYCLE_AGENT_KEYS' src tests frontend-public`
> 这道"分组只有一份定义"的门禁扫不到它们。改动 core 常量（组顺序、组 id、组名、
> 组内含哪些键及组内顺序、组说明）时，必须同轮手工核对这两份副本。

| 触发入口分组 | 组内阶段 | 组说明 |
|---|---|---|
| **实现流水线**（`pipeline`） | 实现 / 修复 / 收尾 / 校验 / 审核 / 监督 | `kc run` / `daemon` 认领后，在同一 worktree 的同一次 claim 内依次触发 |
| **讨论与内容生成**（`discussion_content`） | 辩论 / 内容生成 | 辩论在 Phase 0 就该 Issue 展开（此时 PRD 尚不存在）；内容生成横切 `kc issue create`、`kc issue create --from-prompt`、Phase 1 与开 Draft PR 四处 |
| **独立入口**（`standalone`） | 决策 | `kc ask`，不在任何 Issue 流水线上（无 Issue / PRD 上下文） |

逐键消费点（"实现流水线"组的六个阶段都在同一次 claim 内）：

| 键 | 组 | 触发入口 | 代码消费点 |
|---|---|---|---|
| `implementation` | 实现流水线 | **Phase 2**：认领 `agent/ready` Issue | `run_agent_once.py::choose_agent` |
| `fix` | 实现流水线 | Phase 2，同一次认领内（verification 失败时） | `run_agent_execution_loop.py::run_agent_until_committed` |
| `closeout` | 实现流水线 | Phase 2，同一次认领内（交付门禁失败时） | `run_agent_execution_loop.py::_attempt_delivery_closeout` |
| `verifier` | 实现流水线 | Phase 2，同一次认领内 | `run_verifier_agent.py::_choose_verifier_agent` |
| `review` | 实现流水线 | Phase 2，同一次认领内（开 Draft PR 前） | `run_agent_once.py::resolve_reviewer_agent` |
| `supervisor` | 实现流水线 | Phase 2 发布路径**或** `kc review` / `kc review-daemon` | `run_agent_once.py::resolve_supervisor_agent` |
| `deliberate` | 讨论与内容生成 | **Phase 0**：每轮先扫 `agent/deliberate` Issue（此时 PRD 尚不存在） | `agent_runner_deliberation_issues.py::_process_single_deliberation_issue` |
| `content_generation` | 讨论与内容生成 | **横切四个 target**，见下文 | `generated_prd_content.py::generate_prd_content` / `generated_content.py::generate_issue_content` / `generated_issue_prompt_content.py::generate_issue_prompt_content` / `generate_pr_content` |
| `planner` | 独立入口 | `kc ask`，不在任何 Issue 流水线上 | `cli_parsed_commands/agent.py::run_ask_command` |

### 三个入口

**入口 A：`kc run` / `kc daemon` 的每轮三段式。** daemon 每轮依次执行
`process_deliberation_issues`（Phase 0）、`process_prd_rework_issues`（Phase 1）、
`run_once`（Phase 2）：

- **Phase 0** 用 `deliberate` 在 Issue 评论区跑异步讨论，此时 PRD 还不存在；
- 人手动把标签换成 `agent/rework-prd` 后，**Phase 1** 用 `content_generation` 的
  `prd_from_issue` target 把讨论落成 PRD；
- **Phase 2** 才是实现流水线：`implementation` / `verifier` / `fix` / `closeout` /
  `review` / `supervisor` 这六步共享同一个 worktree 与同一次 claim，因此 `executor`
  和多数 `auto` 语义都以"本次实现者"为锚。

完整流程见 [Agent Runner](agent-runner.md) 的「复杂需求：异步 Issue 评论讨论」一节，
状态机见同页「状态流转与两阶段审查」一节。

**入口 B：`kc review` / `kc review-daemon`。** 只驱动 `supervisor`（对已开 PR 的
Issue 单独跑一轮），因此 `supervisor` 是唯一跨 A / B 两个入口的键。

**入口 C：`kc ask`。** `planner` 的唯一消费点。它既没有 Issue 也没有 PRD 上下文，
这正是它不支持 PRD 级覆盖的原因（见下文「PRD 层」）。

> 结构性佐证：`attach_prd_lifecycle_overrides`（把 Issue 引用的 PRD 头部覆盖回填到
> `IssueSummary`）在 `src/backend` 里恰好只有三个调用点：
>
> - `_process_single_issue` —— 入口 A 的 Phase 2；
> - `_process_review_candidate` —— 入口 B；
> - `_process_single_deliberation_issue` —— 入口 A 的 Phase 0。
>
> 这三处就是全部"既有 Issue、又能读到 PRD 覆盖"的处理循环。入口 C 与
> `kc issue create` 不在其中，因为它们运行时还没有 Issue。

### 两个容易误判的键

- **`supervisor` 跨入口，同键不同义**：发布路径显式把本次实现者传给
  `resolve_supervisor_agent`，所以那里的 `auto` 沿用实现者；独立跑 `kc review` /
  `kc review-daemon` 时拿不到实现者，`auto` 回落成按 Issue 标签路由。
- **`content_generation` 是横切阶段，不是"流水线之外"**：四个 target 分别落在
  `kc issue create`（`issue_from_prd`）、`kc issue create --from-prompt`
  （`issue_from_prompt`，产物**不含 PRD 锚点**，因此提示词与回退模板都不能写出
  `- PRD path:`）、Phase 1（`prd_from_issue`）和 Phase 2 开
  Draft PR 时（`draft_pr`，生成 PR 标题与正文）。四者都以矩阵派生的
  `lifecycle_default_agent` 兜底，target 级显式 `agent` 更优先；
  `generated_content.enabled`（或该 target 自己的 `enabled`）为 `false` 时退回
  fallback 模板，不调用任何 agent。

> 判别窍门：能不能接 `auto` / `executor`，恰好是"该阶段有没有 Issue 或实现者上下文"
> 的代理指标。`planner` 与 `content_generation` 是仅有的两个都不接的键——它们没有
> "本次实现者"可沿用，只能写具体 agent 名。

## 声明与覆盖优先级

优先级从高到低：

1. **PRD 文件头部** `lifecycle_agents` 覆盖块（PRD 级，只影响该 PRD）；
2. **仓库 `.kedacode.toml`** 的 `[agent_runner.lifecycle_agents]`（仓库级）；
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
`deliberate`。**`planner` 不支持 PRD 覆盖**——它的唯一消费点是 `kc ask`
（交互式决策），既没有 Issue 也没有 PRD 上下文，所以 console 的「Agent 覆盖」
抽屉不提供该行、写回也会拒绝该键。`planner` 的**矩阵值**（`config.toml` /
`.kedacode.toml`）仍然有效。

## console 界面落点：三层各写各自文件

| 层 | 写入文件 | 入口 |
|---|---|---|
| 全局（机器级） | `config.toml` | **Settings → 「生命周期、模型与推理深度统一设置」→ 范围选「全局」** |
| 仓库级 | 该仓库 `.kedacode.toml` | **同一设置页 → 范围选「仓库」并选仓库**；或 **Backlog 仓库行齿轮**（直接带仓库进入该页） |
| PRD 级 | 该 PRD 文件头部 | **PRD 原文页工具栏「Agent 覆盖」** |

生命周期矩阵、模型预设与「执行器回退候选」合并到**一个** Settings 子页
`/app/settings/lifecycle/`（原先分散在 Settings「生命周期 Agent 设置」Tab 与 Backlog
仓库齿轮抽屉里的两张矩阵、以及独立的「回退顺序卡片」都已被它取代）。页面顶部用**范围
选择器**在「全局」与「仓库」间切换，仓库范围附一个仓库下拉；页首另有「生命周期矩阵 /
模型预设 / 执行器回退」三个区块锚点。Settings 主页面只保留**「Agent 标签设置」**
（编辑每个 agent 的 GitHub 路由标签名 / 颜色 / 描述，是 `auto` 解析 Issue 标签的依据）
与指向统一设置页的入口卡片。Backlog 的仓库齿轮不再自带编辑器，只负责进入同一页面并预选
该仓库。

矩阵区此刻是**九阶段有效配置表**：每行同时呈递绑定预设、最终生效 Agent、模型 ID、推理深度
及**逐字段来源**（预设 / 继承 / Agent 默认 / 未支持，或来自哪一层），来自聚合只读视图，前端
不硬编码映射。阶段本身通过**绑定命名预设**设定显式值；未绑定时如实展示 Agent 默认或
`fix` / `closeout` 的继承，不替用户猜外部 CLI 默认。矩阵行仍只有**真实取值**（已注册
agent / 该阶段的 `auto` / fix-closeout 的 `executor`），**当前生效值直接选中**，本层已显式
设置时给出恢复入口；**只有改动过的键会写进目标文件**，写入一律保留式（只动点名键，文件
其余内容与格式含注释不变）。

> **只有生命周期矩阵与预设是三层可写的。** PRD 覆盖抽屉与矩阵的写入按各自层落文件；
> **「Agent 标签设置」与「执行器回退候选」是机器级配置**——它们的 API 无论是否带
> `repo_id` 都只写全局 `config.toml`，`repo_id` 仅用于选取校验视角（认可该仓库级注册的
> agent）。因此统一页的「执行器回退」区注明它固定写入全局 `config.toml`，不随生命周期范围
> 切换而改变落点。

## 跨 agent 回退顺序（与矩阵是两件事）

矩阵只选**一个**主 agent；"主 agent 跑不起来（CLI 缺失 / 额度限流 / 进程级崩溃 /
超时）时换下一个"由 `[agent_runner.runner]` 的候选链与 `max_agent_switches`（默认 `2`，
即最多额外试 2 个候选步）驱动。候选链有两种写法：

- **有序数组表 `[[agent_runner.runner.agent_fallback_candidates]]`**（新格式，运行时事实源）：
  每项是 `agent` + 可选 `preset`，数组顺序就是候选顺序。**同一 agent 可用不同 preset 重复
  出现**（如 `claude` 先试 `sonnet-5.5 / max` 再试 `sonnet-5.5 / high`），去重单位是
  `(agent, preset)` 对——完全相同的组合被拒绝。切换预算 `max_agent_switches` **按候选步数
  计**，同一 agent 的另一预设也各消耗一步。每个候选**只有轮到它执行时**才应用自身绑定的
  preset（模型 / 推理档），未绑定时沿用该执行器的默认。
- **旧字符串列表 `agent_fallback_order`**（默认 `["claude", "kimi", "codex"]`）：候选数组
  为空时读取，逐 agent 折叠成**无 preset** 候选，行为与过去逐字节一致；数组非空时它仅作
  兼容顺序视图。

第一个尝试的 agent 仍由矩阵 / 标签路由 / 阶段预设决定；本机未安装的 agent 在运行时跳过。
统一设置页的「执行器回退候选」区给候选链一个可排序编辑入口（每行可选同 agent 的预设）。

**哪些阶段接入了这条链：**

| 阶段 | 本地候选链 | 候选口径 | 全部候选都跑不起来时 |
|---|---|---|---|
| implementation（整条执行流水线） | 是 | 首选 + 链，逐 Issue 换 builder | 落 `MaxRetriesExceededError`，判失败 |
| verifier | 是 | 恒 ≠ 本次 builder | 降级成 fail-safe red，交回 builder 的 recovery 循环 |
| review（pre-PR 审核者） | 是 | 恒 ≠ 本次 builder | 上抛给外层 builder 阶梯 |
| supervisor（post-PR 监督者） | 是 | 恒 ≠ 本次 builder（拿得到时） | 合成 `mark_failed` |
| fix / closeout | 跟随实现者 | 随 implementation 链 | 同 implementation |
| content_generation / planner / deliberate | 否 | — | 各自的既有兜底 |

**换人条件（载重语义）：** 只对"agent 跑不起来"的**基础设施失败**换人——CLI 缺失 /
额度限流 / 进程级崩溃 / 超时包裹的执行失败，以及 supervisor 的进程启动 I/O 失败。
**语义判定绝不换人**：reviewer 返回 `changes_requested`、supervisor 输出 `mark_failed`
都是真实决定，不触发换人。换到备用 agent 时**主 agent 的模型 / 推理档绑定整体丢弃**（各
CLI 模型命名空间不同，继续套用会报错或误设模型）；被选中的**回退候选若自带 `preset`，则
应用该候选自己的模型 / 推理档**（见上节候选数组表），未带 preset 的候选沿用执行器默认。

**候选枚举口径：** `src/backend/core/use_cases/agent_candidate_fallback.py` 统一构建候选链：
`effective_fallback_candidates` 解析"候选数组赢、旧名单兜底"的有序 `(agent, preset)` 序列；
verifier / review / supervisor 按 **agent 名**遍历（`build_agent_candidates`，同名不同 preset
折叠为一个名字，独立性排除也按 agent 名）；implementation 执行器阶梯按 **`(agent, preset)`
spec** 遍历（`build_fallback_candidate_specs`），preset 只在链条真正轮到该候选时应用。
各阶段自己的"逐个候选尝试"遍历仍留在各自调用点（失败分类 / 状态清理 / 耗尽语义各不相同，
强行统一更易出错）。

**为什么 review / supervisor 要接、其余不接：**

- review 与 supervisor 是**后期门禁 / 监督**：agent 偶发不可用不该把一条已通过实现的
  Issue 判死，也不该强迫整条流水线换 builder 重跑；换一个审核者还顺带强化独立性。
- content_generation 是纯文本生成，失败已有 agent → 模板 → 硬兜底三级，换 agent
  无语义增益（artifact 契约不变）。
- planner（`kc ask`）与 deliberate 是交互式 / 规划会话，价值在单一连续会话的上下文；
  中途换人会丢上下文、产出不连贯；deliberate 另有角色感知的 fallback profile 机制。

> 双层预算提示：review 本地候选链耗尽后仍会走外层 builder 阶梯，最坏出现
> `(max_agent_switches + 1)²` 次 reviewer 调用（仅在同时宕机时发生，仍有界）。

## 相关

- **阶段 → 预设 绑定（`[agent_runner.lifecycle_presets]`）**：在"选 agent"之上再绑定
  (模型, 推理档)，绑定后该阶段整体改用预设声明（遮蔽矩阵同键声明）。见
  [Agent 模型预设](model-presets.md)。
- Agent 注册表（`[agent_runner.agents.<name>]`）与调用形态：见
  [配置说明](configuration.md) 与 [Agent Runner](agent-runner.md)。
- Phase 0 异步讨论与 Phase 0 / 1 / 2 三段式：见 [Agent Runner](agent-runner.md)
  的「复杂需求：异步 Issue 评论讨论」一节。
- Issue 状态机与两阶段审查（`agent/ready` → `agent/running` → `agent/supervising`
  → `agent/review`）：见 [Agent Runner](agent-runner.md) 的「状态流转与两阶段审查」
  一节——那是**标签状态**的流转，与本页的**阶段 → agent**是两个正交维度。
- `[agent_runner.generated_content]` 的四个 target 与模板字段：见
  [Agent Runner](agent-runner.md) 的「Generated Content 配置」一节。
- 代码内权威定义：`src/backend/core/shared/models/lifecycle_agent.py`（含
  「触发入口」分组常量 `LIFECYCLE_AGENT_ENTRY_GROUPS`，console 三处矩阵的
  分组标题与行归属都由它下发）；
  解析函数：`src/backend/core/use_cases/lifecycle_agent_resolution.py`。
