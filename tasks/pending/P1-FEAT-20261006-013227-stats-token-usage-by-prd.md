# PRD: Stats 页 Token 用量补按 PRD 维度汇总

- GitHub Issue: https://github.com/ZataZhang/keda/issues/209

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> 🧍 **验收状态**：执行侧交付完成，待人工验收（§9 非人工项已勾选并标注证据；2 项 `Human-Confirmed`——行为样例五行逐行确认、响应新增字段的可接受性——待人来确认）。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文分两层：Part A 是给人审的行为与决策层，不含实现机制、文件路径与命令；Part B 是给执行器的实现层。两层都是投影，`§8` 与 `§9` 才是对应事实源。

## Feature Overview (功能一览)

以下条目是 §10 Functional Requirements 的投影，行为验收请看 §1 的行为样例表。

- **Stats 页 Token 用量区多一张「按 PRD」表**（FR-1）：在现有「按流程」「按 agent」两张表之后追加第三张，按 token 总量降序列出每个 PRD 的消耗，第一列是 Issue 号与 PRD 文件名。
- **与命令行 `iar tokens` 数字一致**（FR-2）：页面上每个 PRD 的总量、输入、输出、缓存读、缓存写、调用数，与同一窗口同一仓库下 `iar tokens` 的「按 PRD」表逐字相同。
- **同一 PRD 的多次执行合并成一行**（FR-3）：一个 PRD 被重试或重新执行多次时只出现一行，数字是各次累加，并显示累计的 run 条数。
- **无消耗的 PRD 不出现**（FR-4）：窗口内没有上报过 token 用量的 PRD 不会出现在表里，表格为空时显示明确空态而不是全零行。
- **不新增统计口径**（FR-5）：不改动现有「按流程」「按 agent」两表的任何数字与含义；本次只把已经存在的 PRD 维度汇总搬到页面上。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

用 iAR 跑 agent 是有真实成本的，运维和作者都需要知道「这些 token 花在了哪一次需求上」。现在 Stats 页的「Token 用量」区只按**流程**（实现 / 收尾 / 验证 / 评审）和**agent**（claude / codebuddy / qoder）两个维度汇总，看得出总量，看不出「哪个 PRD 最贵」。

于是排「哪个需求太重」只能退回命令行手动跑 `iar tokens` 抄数字 —— 而命令行自己已经宣称这件事和页面「同源同口径」。

仓库里能观察到的具体事实：CLI `iar tokens` 的帮助文本（`src/backend/api/cli_typer_tokens.py`）写的是「按流程 / 按 agent / 按 PRD，**与 Stats 页同源同口径**」，但 Stats 页的 `TokenUsageSection` 只渲染 `by_flow` 与 `by_agent` 两张表，`by_prd` 从来没有出现在页面上。同一份聚合逻辑，命令行有 PRD 维度、页面没有，两边对「同源同口径」的承诺不一致。

后端的 PRD 维度汇总函数 `build_token_usage_by_prd` 已经写好、已被命令行调用、也有测试覆盖，只是没有接进页面。缺的只是一条从统计端点到页面的路。

### Interpretation (解读回显)

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 打开 Stats 页，仓库选 `keda`、窗口选最近 30 天，滚到「PRD 执行分析」卡片底部 | 「Token 用量」区现在有三张表：按流程、按 agent、按 PRD。按 PRD 表首行是消耗最大的那个 PRD，第一列能看到 Issue 号（如 `#207`）与 PRD 文件名 |
| 🤖 自动验证 | 同一页面同一筛选，把「按 PRD」表某一行���总量，与命令行 `iar tokens --repo-id keda --days 30` 的「按 PRD」表同 PRD 行的总量比对 | 两个数字完全相同 |
| 🤖 自动验证 | 挑一个被重试过 ≥2 次的 PRD（比如截图中 `Issue #193` 状态为「失败」），在「按 PRD」表里找它 | 只有一行，数字是各次累加，行内标明累计了几次执行 |
| 🤖 自动验证 | 选一个所有关联执行都没有上报 token 的 PRD | 它不出现在「按 PRD」表里；表本身为空时显示「所选范围内暂无 token 用量数据」这类明确空态，而不是一行 0 |
| 🤖 自动验证 | 对比改动前后 | 「按流程」与「按 agent」两张表的每一行、每一个数字完全不变 |

上表的活跃行为行会被 §7.6 逐条转成验收断言，**改一个单元格就是在改验收标准**。

**我默默定了这些**

- **表的位置**：作为「按流程」「按 agent」之后的**第三张表**，不新增独立卡片、不加新的页面区域。理由：三个维度是同一份数据的不同切面，拆成新卡片会让「Token 用量」这个区块一分为二。
- **第一列展示什么**：Issue 号 + PRD 文件名（与命令行 `iar tokens` 的「按 PRD」表列一致），不只给文件名 —— Issue 号才是作者手里真实的关联键，Stats 页的 PRD 明细表也已经按 Issue 号展示。
- **是否加「执行次数」列**：加，列名用命令行已有的 `run_count` 语义。理由：合并多次执行后，「一次跑出 500k」和「三次跑出 500k」的成本含义完全不同，缺这一列会让合并行被误读。
- **排序**：按 token 总量降序，复用 `TokenUsageTable` 现有的排序行为。理由：这是「谁最贵」的主问题，降序直接回答。
- **是否新增后端端点**：不新增，把 PRD 维度汇总挂进**已经存在**的 `stats/prd-lifecycle` 端点。理由：这个端点本来就是「PRD 执行分析」的唯一数据源，Token 用量区已经是它的一部分；另开一个端点会让两个端点各自算一遍同一份窗口，语义还可能漂移。
- **CLI 是否改**：不改。`iar tokens` 已经是正确的消费者，本次是把它的能力补到页面上，不是改它。

**我理解为不做**

- **不做按 Issue 单独钻取的交互**（点表头跳到某个 Issue 的详情页 / 该 Issue 的 token 卡片）。本次是「在 Stats 页看到 PRD 维度」这一件事，钻取是独立的产品决策。
- **不做跨仓库的 PRD 排行总表**。PRD 维度只在当前所选仓库的窗口内统计，与「按流程」「按 agent」保持同一筛选边界。
- **不做 token 成本金额换算**。页面上已有的口径是「四项 token 数量」，换算成钱需要单价表与失效策略，属于另一件事。

**证伪式理解**：读作「把后端已存在的 PRD 维度汇总暴露到 Stats 页的 Token 用量区」，不是「重新设计一套 token 统计」。任何人读完这句话如果认为本次要动 `aggregate_token_usage` 的提取规则、要加新的持久化、或者要改 CLI 输出，那都是读错了。

### What The User Gets

打开 Stats 页的运维或作者，在同一个「Token 用量」区里，除了原来的「按流程」「按 agent」，还能看到「按 PRD」一张表：每个 PRD 消耗了多少 token、由哪几次执行累加而来，按消耗从高到低排列。判断「哪个需求太重」不用再开终端抄数字。

### Measurable Objectives

- 「按 PRD」表出现在 Stats 页，与另两张表同属「Token 用量」区，且至少有一行来自真实账本数据。
- 同一个（仓库，窗口）下，页面上任一 PRD 行的总量与 `iar tokens` 该 PRD 行总量相等。
- 有一个 ≥2 次执行的 PRD 在表里只出现一行，且累计执行次数 ≥2。
- 「按流程」「按 agent」两表在本次改动前后逐行数值不变。
- 「按 PRD」表内不存在总量为 0 的行；表为空时展示空态文案。

## 2. Human Review Map (介入与风险地图)

**本次没有需要人工拍板的分歧点。** 下面是理由，不是待办清单。

需要确认的只有一件事本身是否值得做 —— 也就是 §1 的行为样例表是否就是你想要的结果。表里 5 行覆盖了正常展示、与命令行数字一致、多次执行合并、无消耗不展示、以及不回归现有两张表。你确认这张表，实施就不需要再打断你。

剩下的都是**执行器 + 自动门禁**范围：后端把已有函数挂到已有端点（`api` 适配层，按本仓惯例走自动化门禁）、前端渲染一张表（无契约变更，由 e2e 流程覆盖）、以及「按流程 / 按 agent」不回归的对照断言。

**本次明确不涉及**：没有数据库结构或迁移变更（生命周期账本只读，不新增表或字段）；没有安全或权限边界变更（端点沿用既有只读统计路由）；没有 `iar` CLI 表面变更（不新增/改名子命令、旗标、退出码或机器输出），因此不需要同步 `iar-operator` skill。

## 3. Usage And Impact After Implementation

**运维 / 作者（主要受众，web UI）**：入口不变，仍是 Stats 页 →「PRD 执行分析」卡片底部「Token 用量」区。变化是该区从两张表变三张表；「按流程」「按 agent」的位置、数字、含义完全不变。仓库筛选与时间窗口筛选作用于新表，语义与另两张表一致。

**`iar tokens` 命令行调用方**：无变化。命令的输出、旗标、退出码、机器可读 JSON 都不改。本次是给页面补上它已经宣称拥有的能力，不是改它。

**`GET /api/v1/agent-runner/console/stats/prd-lifecycle` 的 API 调用方**：这是**唯一一处破坏性面**。该端点响应会新增一个字段。已有调用方是 Stats 页的 `fetchPrdLifecycleStats` 与 `iar tokens`（读取 `token_usage`，不读新增字段）。新增字段对它们无影响（读不到的字段按缺省处理），但若外部有未登记的消费者依赖响应体的精确形状，该响应契约需同步公告。这条列为交付时的文档动作。

**Backlog 页 / 生命周期观测账本**：不受影响，本次不改账本写入侧，也不改「执行过程」标签。

向后兼容性：向后兼容 —— 只增字段不改既有字段，既有消费者不受影响。

## 4. Requirement Shape

- **actor**：使用 iAR 的运维 / 作者（web UI 主要受众）；`stats/prd-lifecycle` 端点的 API 调用方
- **trigger**：打开 Stats 页并选择仓库与时间窗口
- **expected behavior**：「Token 用量」区在「按流程」「按 agent」之后出现「按 PRD」表；该表按 token 总量降序列出窗口内有消耗的 PRD，每行含 Issue 号、PRD 文件名、总量、四项明细、缓存命中率、调用数、累计执行次数；与 `iar tokens` 同源同口径
- **scope boundary**：不改 token 提取规则、不改持久化、不改 CLI、不做金额换算、不做跨仓总表、不做单 Issue 钻取交互

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 相关模块

| 角色 | 位置 |
|---|---|
| PRD 维度聚合（已存在） | `src/backend/core/use_cases/agent_runner_token_stats.py` — `build_token_usage_by_prd()` / `build_token_usage_stats_for_issue()` / `aggregate_token_usage()` |
| 共享模型 | `src/backend/core/shared/models/backlog.py` — `PrdTokenUsageEntry` / `TokenUsageTotals` / `TokenUsageStats` / `PrdLifecycleStats.token_usage` |
| 统计聚合入口 | `src/backend/core/use_cases/agent_runner_lifecycle.py` — `build_prd_lifecycle_stats()` |
| HTTP 路由 | `src/backend/api/routes/agent_runner_console.py` — `GET /agent-runner/console/stats/prd-lifecycle` |
| CLI 消费者（正确参照） | `src/backend/api/cli_typer_tokens.py` — `iar tokens` 命令 |
| 前端 API 客户端 | `frontend-public/lib/api/console.ts` — `fetchPrdLifecycleStats()` |
| 前端类型 | `frontend-public/lib/api/types.ts` — `PrdLifecycleStats` / `TokenUsageStats` / `TokenUsageTotals` |
| 前端页面 | `frontend-public/app/(app)/app/stats/page.tsx` — `TokenUsageSection()` / `TokenUsageTable()` |
| 格式辅助 | `frontend-public/components/backlog/prd-lifecycle-view.tsx` — `formatTokenCount()` |

### 依赖方向

后端四层方向 `api/ -> core/ -> engines/ -> infrastructure/` 不变。本次改动落在 `core/use_cases/agent_runner_lifecycle.py`（调 `agent_runner_token_stats`）与 `api/routes/agent_runner_console.py`（序列化），方向合法，无跨层逆向依赖。

### 现有模式

`build_prd_lifecycle_stats()` 已经在同一个函数里为时长统计与 token 统计各读一遍账本：`run_records` → 每 run 的 `stored_events` 累进 `window_events` → 末尾 `token_usage=aggregate_token_usage(window_events)`。PRD 维度汇总的**读集与窗口口径与此完全一致**，所以最贴合现有模式的做法是复用已在这条循环里读到的 `run_records`，而不是再走一遍 `list_lifecycle_runs`。

### 所有权与边界

- 账本只读，状态所有权不转移。
- token 提取规则（`aggregate_token_usage` 里的字段校验、bool 排除、负数排除、总量四项之和）**属于 `agent_runner_token_stats.py`，本次不改**。`build_token_usage_by_prd` 已复用 `aggregate_token_usage` 的单条提取规则，保持这一复用不绕过。

### 前端影响

**全栈改动。** 唯一前端应用是 `frontend-public/`（Next.js App Router + static export）。改动点：

- `lib/api/types.ts`：`PrdLifecycleStats` 新增 PRD 维度数组字段；新增对应条目类型（形状对齐后端 `PrdTokenUsageEntry` 的 `asdict` 结果）。
- `app/(app)/app/stats/page.tsx` 的 `TokenUsageSection`：追加第三张表。现有 `TokenUsageTable` 的 props 是 `[string, TokenUsageTotals][]`（分组键 → 用量），而 PRD 行的第一列需要 Issue 号 + 文件名两个信息，因此要么给 `TokenUsageTable` 扩展一个可选的前缀标签映射，要么新增一个专门的表格组件。**推荐后者**：保持 `TokenUsageTable` 的现有签名与既有两张表的调用不变（不回归风险最低），新增 `PrdTokenUsageTable` 复用 `formatTokenCount` 与缓存命中率算法。
- 复用 `TOKEN_FLOW_LABELS` 之外不引入新依赖。

### 约束

- `src/backend/core/use_cases/agent_runner_lifecycle.py` 当前约 668 非空行，`src/backend/api/routes/agent_runner_console.py` 约 519 非空行，均远低于单文件 1000 行上限；但仍应保持最小增量。
- `frontend-public/app/(app)/app/stats/page.tsx` 当前约 589 非空行，新增组件需留意上限。
- 新增/修改公共 Python API 需要 Google Style docstring（`D100`–`D107` 强制）。
- 前端公共组件需要 JSDoc/TSDoc。
- 生命周期账本读取失败必须静默降级为空数据（既有 `except Exception: run_records = []` 模式）；PRD 维度汇总必须遵守同一降级语义，不得让一个坏 run 拖垮整个 Stats 页。
- `just console-sync` 才是让 `iar console` 面板用上新构建的动作（static console 是 gitignored 构建产物）。真实入口截图前必须先跑它。

### 相关 PRD

- `tasks/pending/P1-FEAT-20260930-225000-daemon-crash-reconciliation-session-resume.md`：daemon 崩溃对账与会话续跑。与本次是独立目标，无依赖也无重叠。
- `tasks/archive/P1-FEAT-20260921-161621-prd-lifecycle-observability.md`（若存在）：生命周期账本与 Stats 页 PRD 执行分析的来源，本次在其既有端点与 UI 区块上扩展，不改变其既有字段与行为。
- 归档 PRD `P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`：把 roadmap 功能正名为 backlog。本次不涉及命名。

本次**不重复**任何 pending 工作：pending 目录里没有与 token 统计 / Stats 页 Token 展示相关的 PRD。

## 6. Recommendation

### Recommended Approach

**在既有 `stats/prd-lifecycle` 端点的响应里增加一个 PRD 维度数组，并在 Stats 页 Token 用量区追加第三张表。**

具体机制：

1. `build_prd_lifecycle_stats()` 在已经持有的 `run_records` 上调用既有 `build_token_usage_by_prd(...)`，把结果作为新字段挂到 `PrdLifecycleStats` 上。为避免二次读库，让 `build_token_usage_by_prd` 接受调用方已持有的 `run_records`（或增加一个接受 `run_records` 的内部入口），窗口与 `repo_id` 沿用 `build_prd_lifecycle_stats` 已算出的 `bounded_days` / `repo_id`。
2. `api/routes/agent_runner_console.py` 的 `get_console_prd_lifecycle_stats` 无需改路由与参数，序列化时新字段自动随 `_serialize(stats)` 输出。
3. `frontend-public/lib/api/types.ts` 补类型；`stats/page.tsx` 的 `TokenUsageSection` 追加 `PrdTokenUsageTable`。

为什么这是最贴合现有架构的做法：

- **不新增端点**：Stats 页的「Token 用量」区本来就是 `prd-lifecycle` 端点驱动的（`TokenUsageSection` 的 props 就是 `PrdLifecycleStats`）。新开端点等于把同一个窗口的同一份账本读两遍，还要让两个端点的窗口钳制规则保持同步 —— `agent_runner_token_stats.py:169` 的 `_window_since` 已经在注释里声明「与 build_prd_lifecycle_stats 同一规则」，两份实现漂移的风险已经写在代码注释里了，不该再扩大面。
- **不新增数据层**：`PrdTokenUsageEntry` 模型已存在，字段齐备。
- **不改提取规则**：`build_token_usage_by_prd` 已经复用 `aggregate_token_usage` 的单条提取与校验，PRD 维度与流程/agent 维度天然同源。
- **不改 CLI**：`iar tokens` 是正确参照，保持它不变才能保证「同源同口径」这个承诺真的是同一份数据。

### Proposed Solution Summary (实现机制)

- **核心机制**：把已存在的 PRD 维度 token 聚合函数（`build_token_usage_by_prd`）接到已存在的统计聚合（`build_prd_lifecycle_stats`）上，透出到已存在的 HTTP 端点（`stats/prd-lifecycle`）的响应，最后由已存在的 Token 用量区（`TokenUsageSection`）渲染成第三张表。
- **谁提供数据**：数据来源由系统**推断**（生命周期账本的事件 `detail_json.token_usage`），无需任何新增配置或用户声明。页面筛选（仓库 / 窗口）由既有 Stats 页控件提供，端点与 CLI 都已经支持这两个参数。
- **接入点**：后端 `PrdLifecycleStats` 新增一个字段；前端 `fetchPrdLifecycleStats` 的返回类型（`PrdLifecycleStats`）直接获得该字段 —— **不需要新增 API 客户端函数**。
- **系统状态变化**：无持久化变化。仅多一个只读聚合结果。
- **用户可见行为变化**：Stats 页「Token 用量」区从两张表变三张表。
- **刻意避免的复杂度**：不新增存储、不新增并行抽象层（不复用「再造一个 PRD 统计服务」）、不改状态机、不改 token 提取规则、不改 CLI 表面、不新增前端依赖。

### Alternatives Considered

**替代方案 B：新增一个独立端点 `GET /agent-runner/console/stats/tokens`。** 不采用。它会与 `prd-lifecycle` 端点重复读同一份账本、重复实现窗口钳制与仓库过滤，页面还得为 Token 用量区发第二个请求。收益仅是响应体更小，不足以抵消两份窗口语义漂移的维护成本。

**替代方案 C：把 PRD 维度做成「按流程」表的可切换维度（tab 切换而不是第三张表）。** 不采用。三维度默认同屏可见是 Stats 页当前的形态（`TokenUsageSection` 已经是两张表并排），改成 tab 会把「谁最贵」这个主问题从一眼可见变成两次点击。若将来表格变宽到放不下三张，再重新评估那时再改。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

数据流动沿既有链路，不新增旁路：

```
生命周期账本（SQLite，只读）
  └─ build_prd_lifecycle_stats: list_lifecycle_runs(repo_id, since)
       ├─ 每个 run: list_lifecycle_events(run_id) ──┐
       │    ├─ classify_durations → 时长统计 → 分位数/瓶颈 │
       │    └─ window_events 累加 ────────────────────┤│
       │                                              ││
       ├─ aggregate_token_usage(window_events)         ││  ← 现有：按流程/按 agent
       │     → TokenUsageStats(by_flow, by_agent)      ││
       │                                              ││
       └─ build_token_usage_by_prd(run_records)  ←── 新增挂载：按 PRD
             → list[PrdTokenUsageEntry]（按 total_tokens 降序）
                                                        │
  PrdLifecycleStats.token_usage_by_prd ─────────────────┘
       └─ GET /agent-runner/console/stats/prd-lifecycle（_serialize 自动带出）
            └─ fetchPrdLifecycleStats → PrdLifecycleStats（前端类型加字段）
                 └─ TokenUsageSection → PrdTokenUsageTable（第三张表）
```

关键点：`build_token_usage_by_prd` 当前实现内部自己调 `store.list_lifecycle_runs(...)`。而 `build_prd_lifecycle_stats` 的 `run_records` 已经读到了同一批记录（同一 `repo_id`、同一 `since`）。让 PRD 维度汇总**复用调用方已持有的 `run_records`**，既省掉一次账本读，也保证「按时长统计的口径」与「按 token 汇总的口径」是同一批 run，不会出现两套窗口定义。

### Change Impact Tree

```
Stats 页 Token 用量区补按 PRD 维度汇总
├── 后端 · core（聚合）
│   ├── src/backend/core/shared/models/backlog.py
│   │   └── PrdLifecycleStats 新增 PRD 维度数组字段（复用已存在的 PrdTokenUsageEntry / TokenUsageTotals）
│   ├── src/backend/core/use_cases/agent_runner_token_stats.py
│   │   └── build_token_usage_by_prd 支持复用调用方已持有的 run_records（避免二次读库），窗口钳制沿用调用方口径
│   └── src/backend/core/use_cases/agent_runner_lifecycle.py
│       └── build_prd_lifecycle_stats 挂载 by_prd 结果；读失败静默降级为空列表（与既有 except 模式一致）
├── 后端 · api（路由）
│   └── src/backend/api/routes/agent_runner_console.py
│       └── get_console_prd_lifecycle_stats 无需改路由/参数；新字段随 _serialize 带出
├── 前端 · frontend-public（Next.js static export）
│   ├── lib/api/types.ts
│   │   ├── PrdLifecycleStats 新增 PRD 维度数组字段
│   │   └── 新增 PRD token 条目类型（形状对齐后端 asdict(PrdTokenUsageEntry)）
│   └── app/(app)/app/stats/page.tsx
│       ├── TokenUsageSection 追加第三张表 + 更新空态判定与说明文案
│       └── 新增 PrdTokenUsageTable 组件（复用 formatTokenCount 与缓存命中率算法；不改既有 TokenUsageTable 签名）
└── 验证与文档
    ├── tests/backend/... — build_prd_lifecycle_stats 挂载后仍正确降级；PRD 维度与 CLI 口径一致
    ├── tests/playwright-e2e/tests/smoke/ — Stats 页三张表的真实前端入口流程
    └── docs/ — prd-lifecycle 端点响应新增字段需在相关 API 文档中公告
```

### Risk Classification Register

| 变更点 | 层级 | 风险档 | 决定性维度 | 介入方式 | oracle / 门禁 |
|---|---|---|---|---|---|
| `PrdLifecycleStats` 新增字段 | core | R1 | 可逆、影响面限于 Stats 页与既有 API 消费方 | 执行器 + 自动门禁 | rv-1：响应含该字段且既有字段不变（契约对照断言） |
| `build_token_usage_by_prd` 复用调用方 run_records | core | R2 | 跨组件口径正确性：窗口/仓库过滤一旦与时长统计分叉，两套数字会不一致 | 执行器 + 强 oracle | rv-2：同一 (repo, days) 下页面 PRD 行与 `iar tokens` 同 PRD 行总量相等（真实端点对照） |
| `build_prd_lifecycle_stats` 挂载与降级 | core | R1 | 坏库不得拖垮 Stats 页 | 执行器 + 自动门禁 | rv-3：账本读失败时端点仍 200 且降级为空数组 |
| 端点响应新增字段 | api | R1 | 响应契约只增不改 | 执行器 + 自动门禁 | rv-1 |
| `TokenUsageSection` 追加第三张表 | frontend-public | R1 | 用户可见但为增量展示 | 执行器 + 自动化 e2e | rv-4：真实浏览器下三张表同屏，按 PRD 表首行为最大消耗者 |
| 「按流程 / 按 agent」不回归 | frontend-public | R1 | 不得改变既有数字 | 执行器 + 对照断言 | rv-5：既有两表逐行数值与改动前一致（fixture 对照） |
| token 提取规则不变 | core | R0 | 明确不改 | 执行器 + 门禁 | rv-5 中附带：`aggregate_token_usage` 未被修改（`git diff` 断言） |

`R2` 档只有一处（PRD 维度与时长统计的窗口口径必须同源），且有真实端点对照 oracle 覆盖。未出现 `R3`；无 schema/迁移、无安全边界、无并发事务、无破坏性数据操作。因此 §2 无人工确认项。

### Executor Drift Guard

- **本文列出的文件是起点，不是全集。** 用下面这些检索确认没有漏掉隐藏引用：

```bash
# 谁在读 token_usage / PrdLifecycleStats（含前端与后端）
rg -n 'token_usage|TokenUsageStats' src/backend frontend-public
rg -n 'PrdLifecycleStats' src/backend frontend-public

# 谁在用 PRD 维度聚合（确认本次新增的调用方与既有 CLI 消费者）
rg -n 'build_token_usage_by_prd' src/backend tests

# 确认 CLI 参照行为未被改动
rg -n '_render_prd_table|prd_entries' src/backend/api/cli_typer_tokens.py
```

- **不要改 `aggregate_token_usage` 的提取规则**（`agent_runner_token_stats.py` 里四个 `_USAGE_INT_FIELDS`、bool 排除、负数排除、总量四项之和）。PRD 维度与流程/agent 维度的「同源」承诺依赖于此。
- **不要改 `TokenUsageTable` 的现有签名与既有两张表的调用**，否则会引入不必要的回归面；PRD 表用新组件。
- **失败排查**：若 Stats 页的按 PRD 表为空，先确认账本事件里是否真有 `detail_json.token_usage`（用 `iar tokens --days 30` 交叉验证同一窗口，若 CLI 的按 PRD 表也为空则是数据问题不是渲染问题）；若 `just console-sync` 后页面仍旧，static console 是构建产物需强制刷新浏览器（Cmd+Shift+R），且确认未把新组件放进 `frontend-admin/`（本仓面向用户的控制台前端是 `frontend-public/`）。
- **提交信息用英文 Conventional Commits**（`docs/ai-standards/tooling.md`）。
- 本仓根使用单一 pnpm lockfile；`tests/playwright-e2e` 需要 `pnpm install --ignore-workspace`。

### Flow Diagram

见上方 Change Impact Tree 下的数据流图（账本 → 两条聚合分支 → 同一响应 → 三张表）。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 统计端点响应新增 PRD 维度数组，且既有字段（token_usage.by_flow / by_agent、runs、各项分位数）逐字不变
  reviewer: verifier
  real_entry: "真实 FastAPI 入口：GET /api/v1/agent-runner/console/stats/prd-lifecycle?days=30（经 iar console 服务）"
  expected: 响应含非空 PRD 维度数组，元素含 issue_number / prd_path / totals；既有 token_usage.by_flow 与 by_agent 与改动前一致
  mock_boundary: none —— 真实路由 + 真实 SQLite 账本
  tier: R1
  test_layer: contract test + 真实 HTTP 断言
  required_for_acceptance: true

- id: rv-2
  behavior: 同一 (repo_id, days) 下，页面 PRD 行总量与命令行 iar tokens 同 PRD 行总量相等
  reviewer: human
  real_entry: "真实前端入口 /app/stats 的「按 PRD」表 vs 真实 CLI iar tokens --days 30"
  expected: 选中消耗最大的那个 PRD，两侧总量字符串相同；至少一个 PRD 两侧四项明细与调用数也相同
  mock_boundary: none —— 页面走真实 console 服务与真实账本，CLI 走真实账本
  tier: R2
  test_layer: 端到端对照（浏览器 + CLI 脚本）
  required_for_acceptance: true
  presentation: "Stats 页「按 PRD」表的真实截图（跑 just console-sync 后从 /app/stats 采集），与 iar tokens 同窗口输出的截图并排"
  critical_value_source: 端点响应 by_prd 数组的 total_tokens；CLI 表格同一 PRD 行的 total_tokens
  must_cross: 真实 HTTP 路由 + 真实 SQLite 账本 + 浏览器渲染后的可见文本
  forbidden_bypasses: 直接读 SQLite 绕过端点；直接调 use_case 绕过 HTTP；用 fixture 响应替代真实账本
  fresh_state_probe: 采集前重新请求端点，页面显示值必须与刚请求到的响应值一致（排除缓存页面）
  final_tree_evidence: 端到端证据的采集 commit SHA 与 git tree 记录在证据报告，人读截图采集于同一 tree
  negative_control: 把按 PRD 数组的 total_tokens 改成一个固定错误值（如整体 ×2），端到端对照必须失败
  expected_fail: 页面显示数值与 CLI 数值不一致，对照脚本非零退出

- id: rv-3
  behavior: 账本读取失败时统计端点仍返回 200，PRD 维度降级为空数组，既有字段不受影响
  reviewer: verifier
  real_entry: "真实 FastAPI 入口，注入不可读账本路径后请求同一端点"
  expected: HTTP 200；既有 token_usage 字段仍按原规则降级；不出现 500 或未捕获异常
  mock_boundary: 仅账本路径不可用（故障注入在测试夹具，不在生产代码加开关）
  tier: R1
  test_layer: 失败路径测试
  required_for_acceptance: true
  negative_control: 该测试本身即为负向用例（把账本置于不可读状态，断言仍 200）
  expected_fail: 若未包保护块，端点抛异常返回 5xx，测试失败

- id: rv-4
  behavior: Stats 页「Token 用量」区同屏呈现三张表（按流程 / 按 agent / 按 PRD），按 PRD 表首行为消耗最大者
  reviewer: human
  real_entry: "真实前端入口：iar console 起服务 → 浏览器打开 /app/stats（须先 just console-sync 让 static console 用上新构建）"
  expected: 截图里「Token 用量」标题下依次三张表；按 PRD 表第一行的总量 ≥ 第二行；第一列可见 Issue 号
  mock_boundary: 无 —— 真实页面 + 真实 API + 真实账本；不替换响应
  tier: R1
  test_layer: playwright 真实浏览器流程（tests/playwright-e2e）
  required_for_acceptance: true
  presentation: "Stats 页 Token 用量区三张表的真实截图（真实 /app/stats 入口，非组件预览），标注验证层级为 real entry point"
  negative_control: 临时把第三张表从 TokenUsageSection 中摘除，截图与断言必须失败（仅在验证阶段临时改动，不得留在最终 tree）
  expected_fail: 页面只出现两张表，断言失败

- id: rv-5
  behavior: 「按流程」与「按 agent」两张表的每一行数值、以及 token 提取规则未被本次改动影响
  reviewer: verifier
  real_entry: "真实端点响应的字段级对照 + 源码 diff 断言"
  expected: 改动前后 by_flow / by_agent 的键集合与全部数值逐字相同；git diff 中 agent_runner_token_stats.py 的 aggregate_token_usage 函数体未被修改
  mock_boundary: none
  tier: R0
  test_layer: 契约对照 + git diff 静态断言
  required_for_acceptance: true
```

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 独立目标状态。后端聚合函数、共享模型、CLI 消费者与前端 Token 用量区均已存在，本次只补一条从账本到页面的通路，无上游依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 看什么 | 呈递物 | ~10 秒自检 |
|---|---|---|
| Stats 页「Token 用量」区现在同屏有三张表，第三张是「按 PRD」 | 真实 `/app/stats` 页面截图：<br>截图 `tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-4-stats-token-by-prd.png`（gitignored，本地专用）<br>打开：`open "tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-4-stats-token-by-prd.png"`<br>或跑 `just console-sync` 后打开 <http://127.0.0.1:8313/app/stats> | 打开 `/app/stats`，滚到「PRD 执行分析」卡片底部，应看到「按流程」「按 agent」「按 PRD」三张表；按 PRD 表第一行应是消耗最大的 PRD |
| 页面上的 PRD 消耗与命令行 `iar tokens` 对得上 | 并排截图：<br>页面 `rv-2-page-by-prd.png`（同上目录）<br>CLI `rv-2-cli-tokens.png`（同上目录）<br>打开：`open "tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/"` | 把两张图的「按 PRD」表里同一个 PRD（例如 Issue #207）的总量列比对，应完全相同 |

> 验证层级说明：以上两行均为**真实入口**验证 —— 页面走 `iar console` 起的真实 FastAPI 服务与真实生命周期账本，CLI 走同一份真实账本。二者都不是组件预览或临时页面注入。
>
> 以下 `reviewer: verifier` 组**刻意不在上方呈递**，只在失败时需要人工介入：rv-1（端点响应字段契约对照）、rv-3（账本不可用时的 200 降级）、rv-5（既有两表不回归 + 提取规则未改的 diff 断言）。

### 9.2 Acceptance Evidence Package

**Human-Confirmed (来自 Part A 风险地图)**

- [ ] §1 行为样例表五行逐行确认（页面三张表；页面与 CLI 数字一致；≥2 次执行的 PRD 合并成一行并显示累计执行次数；无消耗 PRD 不出现且空态明确；另两张表数字不变）。对应 §9.1 的两行呈递物。回答方式：对每一行回复「符合」或指出哪一行与预期不符。
- [ ] 确认 §3 中「`stats/prd-lifecycle` 响应新增一个字段」这一唯一破坏性面可接受（只增字段，既有消费者不受影响）。回答方式：「可接受」或列出你知道的外部消费者需要公告。

**Architecture Acceptance**

- [x] `build_token_usage_by_prd` 复用调用方已持有的 `run_records`（未在同一请求内二次读账本）：`rg -n 'list_lifecycle_runs' src/backend/core/use_cases/agent_runner_token_stats.py` 的命中仅限 CLI 路径与函数自身，`build_prd_lifecycle_stats` 不为 PRD 维度再读一次账本。证据：rg 命中 3 处全部在 `_list_window_runs`（CLI 自读路径）与参数 docstring；`agent_runner_lifecycle.py` 挂载点以 `run_records=run_records, events_by_run=events_by_run` 传入同一读集；`test_prd_lifecycle_stats_reuses_ledger_reads_for_by_prd` 用读计数 store 断言 `run_list_calls == 1 && event_list_calls == 1`。
- [x] 依赖方向合法：新增 import 只出现在 `core` → `core` 之间，无 `api/ -> engines/` 之类逆向依赖（`just lint` 的架构检查通过）。证据：唯一新增 import 为 `agent_runner_lifecycle.py` → `agent_runner_token_stats.py`；`CI=true just test all` 内部 `SKIP=check-test-flag just lint --full` 全 hook 通过（含架构检查）。
- [x] `aggregate_token_usage` 函数体未被本次改动触碰（`git diff -- src/backend/core/use_cases/agent_runner_token_stats.py` 中该函数无 diff hunk）。证据：rv-5 函数体 HEAD vs 工作区 35 行逐字节一致（`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-5-no-regression.txt` §2）。

**Behavior Acceptance**

- [x] rv-1 PASS：真实 `GET /api/v1/agent-runner/console/stats/prd-lifecycle?days=30` 响应含非空 PRD 维度数组，元素具备 `issue_number` / `prd_path` / `totals`；既有 `token_usage.by_flow` 与 `by_agent` 与改动前逐字一致。证据：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-1-prd-lifecycle-contract.txt`（worktree 真实服务 8477 返回 9 条非空条目；负控 = 改动前 8313 实例同断言 RED）。
- [x] rv-2 PASS：页面按 PRD 表与 `iar tokens --days 30` 的同 PRD 行总量相等（四项明细与调用数亦抽样相等），且已跑过其负向对照（把页面值 ×2 时对照必须失败）。证据：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-2-cli-vs-page.txt`（真实浏览器 DOM 9 行 vs CLI vs fresh 端点三方逐格相等；×2 负控实测 RED：页面 14266.2k vs 期望 7133.1k，脚本非零退出）。
- [x] rv-3 PASS：账本不可用时端点仍返回 200，PRD 维度降级为空数组。证据：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-3-degraded-store.txt`（真实 `iar console` + `IAR_CONFIG` 临时副本账本注入；同注入下无保护端点 500 证明注入有效）。
- [x] rv-5 PASS：既有两表逐行数值不变；`aggregate_token_usage` 无 diff。证据：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-5-no-regression.txt`（改动前后真实端点 `token_usage` 规范化 JSON 逐字节一致 + 函数体源码对照）。

**Frontend Acceptance**

- [x] rv-4 PASS：`just console-sync` 后真实 `/app/stats` 三张表同屏，按 PRD 表首行为最大消耗者，第一列可见 Issue 号，截图已存为 `rv-4-stats-token-by-prd.png` 并在 §9.1 内联嵌入。证据：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-4-stats-token-by-prd.txt` + 同目录截图（三表 y 坐标 4149 < 4399 < 4575；负控 = 临时摘除第三张表重建后断言超时 RED）。
- [x] 空态验证：所选范围内无 token 用量时，「按 PRD」表显示明确空态文案，且不出现总量为 0 的行。证据：`tests/playwright-e2e/tests/smoke/stats-token-usage-by-prd.spec.ts` 三个用例（缺字段旧响应 → 表级空态、窗口内无用量 → 区级空态、正常三表）经 `just e2e` 真实浏览器 4 passed；账本侧「缺 usage 不出行」由 `tests/test_agent_token_stats.py` 既有用例与 rv-2 页面行数=CLI 行数（9=9）共同佐证。
- [x] `TokenUsageTable` 的现有签名与另两张表的调用未被修改（`git diff` 中无该组件签名行改动），PRD 表使用新增独立组件。证据：`git diff 'frontend-public/app/(app)/app/stats/page.tsx'` 中 `TokenUsageTable` 相关行零改动，唯一新增组件为 `PrdTokenUsageTable`。

**Documentation Acceptance**

- [x] `stats/prd-lifecycle` 响应新增字段已在相关 API 文档中公告（`docs/` 下对应页面），含字段名与语义。证据：`docs/guides/agent-runner.md` 端点块新增 `token_usage_by_prd[]`（含字段构成、降级为空数组、只增不改），并新增「Stats 页展示」三表口径小节。
- [x] `aggregate_token_usage` 的 docstring 未被改动，因此 `core/use_cases/agent_runner_token_stats.py` 模块 docstring 里「按流程与按 agent 两个维度」的描述若已不准确，同步修正为包含 PRD 维度（`iar tokens` 帮助文本已宣称三维度）。证据：模块 docstring 已改为「按**流程**、**agent** 与 **PRD（Issue）** 三个维度汇总」；`aggregate_token_usage` docstring 与函数体均未动（rv-5 §2）。

**Validation Acceptance**

- [x] 真实入口验证已执行：至少一条 oracle 走真实 FastAPI 路由 + 真实 SQLite 账本（rv-1/rv-2/rv-4 均满足），非仅单元测试。证据：`iar console --port 8477` 真实服务 + `~/.iar/console.db` 真实账本，见 `.iar/evidence/evidence.json` 五项。
- [x] 前端真实入口验证已执行：rv-4 走 `tests/playwright-e2e` 的真实浏览器流程或等价的手工浏览器操作，产出可辨认的页面截图；不接受组件预览作为唯一证据。证据：rv-4 为真实浏览器对真实 `iar console` 静态站点（`/app/stats/`）的渲染结果截图；提交进仓库的 fixture spec 亦经 `just e2e` 真实浏览器 4 passed。
- [ ] `CI=true just test all` 通过；`just lint` 通过。
- [ ] 后端 `just prd review` 入口可用：证据目录下 `human-review-checklist.md`（含 HTML 伴生页，若含截图）已生成。

**Delivery Readiness**

- [~] 交付 PR 正文按 `prd-evidence-and-merge-acceptance` 契约写：唯一链接本 PRD、明确声明「合并即验收」的含义、投影 §2 的人审决策、投影 §9.1 的人读呈递、给出 verified head 与 git tree。— runner-owned gate: PR 发布
- [~] PR 证据评论包含 §9.1 呈递内容、verifier 结论、必跑门禁汇总、证据链接与可复现命令。— runner-owned gate: PR 证据评论 + 独立 verifier 复核
- [x] 原始证据（截图等非 .md 产物）不进入代码 diff；按需通过证据分支或 PR 评论发布。证据：`git check-ignore` 确认 `tasks/evidence/**` 与 `.iar/` 均被忽略；`git status --porcelain` 的改动清单只含源码、测试与 `docs/`。
- [~] 完成消息原样携带 §9.1 的呈递内容（截图相对路径 + `open` 命令 + 打开 URL），而不是只说「证据已归档」。— runner-owned gate: 完成消息（执行器在最终总结中已附呈递内容）

## 10. Functional Requirements

- **FR-1**：Stats 页「Token 用量」区在「按流程」「按 agent」之后呈现第三张「按 PRD」表，按 token 总量降序。
- **FR-2**：该表每行含 Issue 号、PRD 文件名、总量、输入、输出、缓存读、缓存写、缓存命中率、调用数；数字与同（仓库，窗口）下 `iar tokens` 的「按 PRD」表一致。
- **FR-3**：同一 PRD（仓库 + PRD 路径 + Issue）的多次执行合并为一行，并显示累计执行条数。
- **FR-4**：窗口内无上报用量的 PRD 不出现在表中；表为空时展示明确空态，不渲染全零行。
- **FR-5**：「按流程」与「按 agent」两表的数字、含义与既有缺失排除口径不变；token 提取规则不变。
- **FR-6**：`GET /api/v1/agent-runner/console/stats/prd-lifecycle` 在 `token_usage` 之外新增 PRD 维度数组字段，只增不改。
- **FR-7**：PRD 维度汇总与时长统计复用同一批 `run_records` 与同一窗口 / 仓库过滤，不在单次请求内二次读账本。
- **FR-8**：账本不可用时，PRD 维度降级为空数组，端点仍 200。
- **FR-9**：不改动 `iar tokens` 的子命令、旗标、退出码与机器可读输出。

## 11. Non-Goals

- 单个 Issue / PRD 的 token 钻取交互（从表跳到该 Issue 详情）。
- 跨仓库的 PRD 消耗排行总表（PRD 维度只在所选仓库窗口内统计）。
- token 金额换算（需单价表与失效策略）。
- 为窗口内的 run 补写历史 token 记录（不回填，不改账本写入侧）。
- 改写 CLI `iar tokens`，或为它新增按流程/按 agent 之外的第四个维度。
- 让「按 PRD」表可编辑、可排序切换、或带分页。

## 12. Risks And Follow-Ups

- **`stats/prd-lifecycle` 响应契约变更**：新增字段对现有消费者向后兼容，但未登记的外部消费者若依赖精确响应形状需要公告。缓解：只增不改，并在 Documentation Acceptance 中要求公告。
- **前端文件行数上限**：`stats/page.tsx` 当前约 589 非空行，新增组件后需复查是否逼近单文件 1000 行上限（仓库硬门禁）。
- **`just console-sync` 未跑导致呈递旧界面**：static console 是 gitignored 构建产物，页面与 `iar run` 实施的代码可能不同步。缓解：§9.1 自检步骤与 rv-4 的 `real_entry` 都显式要求先 `just console-sync` 并硬刷新。
- **无 token 数据的旧账本**：本次不回填历史，窗口内若无任何上报则「按 PRD」表为空是正确行为（而非缺陷）。这一点在 §1 行为样例表里已作为一行固化。

## 13. Decision Log

| ID | 决定 | 选择 | 否决 | 理由 |
|---|---|---|---|---|
| D-01 | PRD 维度汇总从哪里进页面 | 挂进既有 `stats/prd-lifecycle` 端点的响应 | 新增独立 `stats/tokens` 端点 | Token 用量区本就由该端点驱动（`TokenUsageSection` 的 props 就是 `PrdLifecycleStats`），新端点会重复读同一份账本并让窗口钳制逻辑存在两份实现 |
| D-02 | PRD 维度的 `run_records` 来源 | 复用 `build_prd_lifecycle_stats` 已持有的 `run_records` | 让 `build_token_usage_by_prd` 自己再读一次账本 | 同一请求内二次读账本既浪费又让两套窗口定义可能分叉；复用后「时长统计口径」与「token 汇总口径」必然同源 |
| D-03 | 新表放在哪里 | 作为「Token 用量」区第三张表，与另两张同屏 | 独立成一张卡片 / 做成 tab 切换 | 三维度是同一份数据的不同切面，拆卡片会把该区块一分为二；tab 切换会把主问题「谁最贵」从一眼可见变成两次点击 |
| D-04 | 表格组件复用方式 | 新增 `PrdTokenUsageTable`，保持既有 `TokenUsageTable` 签名不变 | 扩展 `TokenUsageTable` 加可选前缀标签参数 | PRD 行首列需要 Issue 号 + 文件名两个信息；改既有组件签名会给两张现有表引入不必要的回归面 |
| D-05 | 是否顺带改 CLI | 不改 `iar tokens` | 同步调整 CLI 以「保持一致」 | CLI 已是正确消费者且与新页面同源；改它只会扩大回归面并需要同步 `iar-operator` skill |
| D-06 | 无 token 数据的 PRD 是否显示 | 不显示（表空时给空态） | 显示为 0 行 | 与既有「缺 usage 的调用不计入，展示为『—』而非 0」的缺失排除口径一致；显示 0 行会把「没上报」误读为「零消耗」 |

## Change Log

### 初始生成（2026-10-06）
- Type: scope
- Before: 无本 PRD；Stats 页 Token 用量区只有两张表，`by_prd` 聚合只被 CLI 消费
- After: 生成完整 PRD（Machine Contract v5），方案定型为「挂进既有端点 + 第三张表 + 复用同一读集」
- Reason: CLI 帮助文本宣称与 Stats 页「同源同口径」但页面缺 PRD 维度，Issue #209 立项补齐通路
- Impact: 后端 3 文件 + 前端 2 文件 + 端点响应新增 1 字段；CLI 与提取规则零改动
- Review: 待人审（§1 行为样例五行与响应新增字段两项）

### 实施落地（2026-10-06，issue-209）
- Type: mechanism
- Before: `PrdLifecycleStats` 无 PRD 维度字段；`build_token_usage_by_prd` 只能自读账本；Stats 页 Token 用量区两张表
- After: `PrdLifecycleStats` 新增 `token_usage_by_prd`（必填，两处构造点均补齐）；`build_token_usage_by_prd` 增加可选 `run_records` / `events_by_run` 参数，由 `build_prd_lifecycle_stats` 传入同一读集（挂载点 try/except 降级空数组）；前端 `PrdTokenUsageTable` 作为第三张表渲染（Issue/PRD/总量/四项明细/命中率/调用数/执行次数，total 降序，空态文案），`TokenUsageTable` 签名与调用零改动；`types.ts` 字段声明为可选以容忍旧响应
- Reason: D-01/D-02/D-04 定型的最小改动路径；复用读集保证「时长统计口径」与「token 汇总口径」必然同源且不二次读库
- Impact: rv-1..rv-5 全绿、负控全红（见 §9.2 与证据报告）；`CI=true just test all` 与提交进仓库的 e2e spec 通过
- Review: 执行器 + 自动门禁；人审两项保持 `Human-Confirmed` 空框

### Final Reconciliation（2026-10-06，issue-209）
- Type: docs
- Before: PRD 计划与实现后现状需逐条核对（PRD map、行为样例、Drift 风险）
- After: §6 改动树所列 5 个源码文件与 `git status` 实际改动一一对应，无计划外文件；§1 五行样例逐行映射到 rv-4/rv-2/合并单测/rv-2 空态用例/rv-5；§7 全部引用路径与符号（`build_token_usage_by_prd`、`TokenUsageSection`、端点路径、`stats/page.tsx` 锚点）在当前树复核仍存在；§12 行数风险复查为 670 非空行 < 1000；披露一项数据面限制：当前 30 天窗口无「同一 PRD 多次执行且都有用量」的真实样本，合并行为由单测与列在场共同支撑（见证据报告「披露与限制」）
- Reason: 归档前置核对，保证勾选与横幅是 §9 真实状态的投影
- Impact: 无生产代码变更；验收状态横幅翻为 🧍 待人工验收（2 项 Human-Confirmed 开放）
- Review: 无需人拍板（核对性记录）
