# PRD: 生命周期 Agent 矩阵按触发入口分组展示

> ✅ **交付前置**：无硬依赖；§8 是唯一依赖事实源。
>
> ⬜ **验收状态**：未开工；§9 是唯一验收事实源。

> 本文分为 Part A 人审层与 Part B 执行层。标题下的横幅和功能一览只投影正文，不另定义行为。

## Feature Overview (功能一览)

- **FR-1–FR-2：** 九个生命周期键的"触发入口分组"收敛成后端一份常量，闭集、取值域、回落与写回语义一律不变。
- **FR-3–FR-5：** 全局 Settings、Roadmap 仓库行齿轮、PRD 覆盖抽屉三处矩阵按同一分组呈现；组头 + 每行触发时机可见，九行仍齐全可编辑。
- **FR-6–FR-7：** 只读视图新增展示字段且 `lifecycles` 顺序、既有字段与既有 testid 全部保持兼容。
- **FR-8–FR-9：** 文档与交互原型与真实界面同构，操作者不再把 `planner` / `content_generation` / `deliberate` 误读成与 `fix` 同一条流水线。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

操作者打开 Settings →「Agent 管理」→「生命周期 Agent 设置」（或仓库行齿轮、PRD 覆盖抽屉）时，看到的是**一张没有分组标题的九行平铺表**：`实现 / 修复 / 收尾 / 校验 / 审核 / 监督 / 决策 / 内容生成 / 辩论`。这张表会让人得出一个错误结论——这九个阶段在同一条流水线上依次发生。

但事实不是：`实现 / 修复 / 收尾 / 校验 / 审核 / 监督` 六行共享同一次 `iar run` 认领（同一个 worktree、同一次 claim），触发点相邻、`auto` 语义都以"本次实现者"为锚；`决策`（`planner`）压根不在任何 Issue 流水线上，它唯一的消费点是 `iar ask`，既没有 Issue 也没有 PRD 上下文；`辩论`（`deliberate`）在 Phase 0 就该 Issue 在评论区展开讨论，那时 PRD 还不存在；`内容生成`（`content_generation`）横切三个 target（PRD→Issue、Issue→PRD、Draft PR 文案），既不属于 Phase 2 也不属于单一入口。

仓库文档自己已经承认了这个事实：`docs/guides/lifecycle-agent-matrix.md` 明写"九个阶段**不在同一条流水线上**"，并配了一张"各阶段在哪触发（消费点）"表。**文档分了组，界面没有**——于是唯一的可视化入口在说一件文档否认的事。操作者据此会把 `决策` 当成发布链路上的一个环节去调，或者反过来以为改了 `辩论` 就会影响实现路径。误读的代价不是崩溃，而是把 agent 配到不该配的阶段上，且事后看不出错在哪。

### Interpretation (解读回显)

下表每行的输入和结果对应 §7.6 的验收 oracle；纠正其中一格，应同步纠正对应 oracle。

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 打开 Settings →「Agent 管理」→「生命周期 Agent 设置」 | 九行分成三组、每组有组标题与一行触发时机；`实现/修复/收尾/校验/审核/监督` 在"实现流水线"组，`辩论/内容生成` 在另一组，`决策` 单独一组 |
| 🤖 自动验证 | 打开 Roadmap 受管理仓库行的齿轮抽屉、以及 PRD 原文页的「Agent 覆盖」抽屉 | 同样分组、同样组标题；分组不是某处硬编码的第二份映射 |
| 🤖 自动验证 | 读取 console 只读视图接口 | 每行都能说出自己的组与触发时机；`lifecycles` 数组仍是原来的九键顺序，既有字段与既有 testid 未改名 |
| 🤖 自动验证 | 改动矩阵下拉并保存（任一层） | 写回载荷与生效值来源列与本次改动前完全一致；分组只是呈现，不进入写回 |
| 🤖 自动验证 | 执行 `rg -n 'LIFECYCLE_AGENT_ENTRIES|LIFECYCLE_AGENT_KEYS' src tests frontend-public` | 分组事实只有一份定义（core 常量），前端与文档引用它而不是各写一份 |

**我默默定了这些：** 分组只做"哪些行属于同一触发入口"这一件事；组是固定三组、由后端常量声明，不做用户自定义分组或拖拽排序；`lifecycles` 数组顺序与键序保持 `LIFECYCLE_AGENT_KEYS` 不变（前端按 `entry` 分组、组内沿用数组顺序）；既有 `data-testid="lifecycle-matrix-row-<key>"` 等选择器一律不改名。

**我理解为不做：** 不改九键闭集、取值域（`auto` / `executor` 的合法键）、两层（global/repository）与 PRD 层的回落顺序、保留式写回、`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 的行筛选；不改 `iar run` 实际挑选 agent 的任何运行时行为；不拆成两张配置文件；不新增页面、路由或接口；不给组加开关或权限。

这份需求是让矩阵的**呈现**与它早已存在的**语义**对齐：六个阶段在同一次认领里，另外三个不在。分组本身是一个关于"哪些阶段共享同一次 claim"的断言，写错了界面就在说谎，所以分组归属值得人确认一次。

### What The User Gets

- 在本机控制台任一矩阵入口，一眼看出哪些行属于同一次任务执行、哪些行是独立入口。
- 每行旁边能看到一行触发时机（例如 `决策` 旁边是"`iar ask`，不在 Issue 流水线上"），不必再去翻文档。
- 分组不改变任何可操作项：九行照旧可改、可恢复、可保存，来源层与生效值照旧。

### Measurable Objectives

- 三处矩阵入口都出现三个组标题，九行的组归属与 §7 的常量定义一一对应，且没有任何一行落空或重复。
- 只读视图每一行都返回非空 `entry` / `entry_label` / `trigger`，且 `lifecycles` 的键序与 `LIFECYCLE_AGENT_KEYS` 完全一致。
- 既有 e2e 断言（按 `<key>` 定位的下拉、来源列、恢复动作、PRD 覆盖行筛选）全部继续通过，无一需要放宽。
- 仓库内不再存在第二份"键 → 组"映射：`rg` 只在 core 常量处命中分组定义。

## 2. Human Review Map (介入与风险地图)

### 固定评审区与横切触发清单

- ① 核心业务逻辑 / 编排（`core/`）
- ② 数据库结构 / schema / migration（即使在 `infrastructure/` 下）
- ③ 安全 / 认证 / 信任边界
- ④ 对外 API 契约 / 破坏性变更
- ⑤ 金钱 / 计费 / 配额
- ⑥ 不可逆或破坏性数据操作
- ⑦ 并发 / 事务 / 幂等

### 命中的人审项

- **①（核心层）**：分组常量与只读视图行字段都落在 `core/`。命中原因不是"改了核心逻辑"，而是**分组是一个语义断言**——它声称"这六个阶段共享同一次 claim、那三个不共享"。这个断言错了，界面就会系统性误导配置决策，而机器门禁只能验"字段存在且与常量一致"，验不了"常量说得对"。因此分组归属本身请人确认一次。

### 未命中

其余 ②–⑦ 全部未命中，交由执行器 + 自动门禁兜底：本次不新增表/迁移、不碰认证与信任边界、不做破坏性数据操作、不引入并发或事务语义、无计费面。最坏情况逐条记一句——②无 schema 改动，最坏为无；③只读视图沿既有本机控制台权限边界，最坏是把已可见的 agent 名再显示一次；④是**纯新增字段**，最坏情况是旧客户端忽略新字段（若有人把既有字段改名才会破坏，本 PRD 明确禁止），故不列为人审项；⑤⑥⑦均无。

### 分类表

| 改动点 | 架构层 | 风险 | 介入方式（人工确认=高证据负担 / 执行器+门禁=兜底） | 证据 / Oracle |
|---|---|---|---|---|
| 九个键 → 触发入口分组的归属与组名 | core（`shared/models/lifecycle_agent.py`） | R2 | **人工确认**：分组是语义断言，机器只能验自洽 | rv-1 |
| 只读视图行新增 `entry` / `entry_label` / `trigger`，数组顺序与既有字段不变 | core（`core/use_cases/lifecycle_agents_console.py`） | R1 | 执行器 + 门禁：`uv run pytest tests/test_lifecycle_agents_console_api.py` | rv-3 |
| 三处矩阵入口按 `entry` 分组渲染 | frontend（`frontend-public/`） | R1 | 执行器 + 门禁：`just e2e lifecycle-agent-matrix` | rv-2 |
| 文档与交互原型与真实界面同构 | docs / prototype | R1 | 执行器 + 门禁：`uv run mkdocs build --strict` + `prototype-screenshots.spec.ts` | 门禁名 + §9 仓库检索断言 |

### 如何证明它生效（真实入口，白话）

起本机控制台，从 Settings 进"生命周期 Agent 设置"，肉眼核对三组标题与九行归属，并对照 Roadmap 齿轮抽屉与 PRD「Agent 覆盖」抽屉是同一分组；再用只读视图接口核对每行的组与触发时机，以及九键顺序没变。详细 oracle 见 §7.6。

### 数据库结构评审

本次无数据库结构变化。

## 3. Usage And Impact After Implementation

### 控制台操作者

打开 Settings →「Agent 管理」→「生命周期 Agent 设置」，矩阵不再是一条平铺长表，而是三块：**实现流水线**（`实现/修复/收尾/校验/审核/监督`，标注"`iar run` / `daemon` 认领后，共享同一次 claim"）、**讨论与内容生成**（`辩论/内容生成`，标注 Phase 0 讨论与 Phase 1/横切的文案生成）、**独立入口**（`决策`，标注"`iar ask`，不在 Issue 流水线上"）。每行的下拉、来源层徽标、"本层设置"标记与恢复动作位置不变；只是多了一层分组标题与一行触发时机。保存行为与生成写回载荷完全不变。

只读视图接口（供面板与本地集成方）示例：

```bash
uv run iar console --port 8391 --no-browser   # 另开一个终端
curl -s "http://127.0.0.1:8391/api/v1/agent-runner/lifecycle-agents?scope=global" \
  | python3 -m json.tool | head -40
```

返回的 `lifecycles` 仍是九行，每行新增 `entry`（组 id）、`entry_label`（组中文名）、`trigger`（触发时机一句话）；其余字段原样保留，键序不变。

### 仓库负责人 / PRD 维护者

Roadmap 受管理仓库行的齿轮抽屉（写 `.iar.toml`）与 PRD 原文页工具栏「Agent 覆盖」抽屉（写 PRD 文件头部）沿用同一分组与同一行筛选规则：前者九行全给，后者仍不给 `决策` 行（它没有 PRD 上下文）。写回目标文件与载荷结构不变。

### 集成和运维人员

零配置改动、零迁移。`[agent_runner.lifecycle_agents]` 的键名、取值与解析路径都不变；新字段只出现在只读视图响应里，属纯新增。既有按 `<key>` 定位的 UI 选择器与 e2e 断言继续有效，外部脚本若只读旧字段也不受影响。

### Impact On Existing Behavior

- 九键闭集、`auto` / `executor` 的合法键集合、两层与 PRD 层回落顺序、保留式写回、来源层标注语义全部不变。
- `iar run` / `daemon` / `review` / `ask` 实际挑选 agent 的行为完全不变——本 PRD 不进入任何执行路径。
- 只读视图接口的既有字段名、字段含义与 `lifecycles` 顺序保持兼容，只增不改。
- 文档与交互原型同步到同一分组，不再与真实界面对不上。

## 4. Requirement Shape

- **Actor:** 本机控制台操作者、仓库负责人、PRD 维护者，以及读取只读视图的本地集成方。
- **Trigger:** 操作者在 Settings、Roadmap 仓库行齿轮或 PRD 覆盖抽屉打开生命周期 Agent 矩阵。
- **Expected behavior:** 矩阵按"触发入口"分成三组呈现，每行带一行触发时机；分组由后端单一常量声明，经只读视图下发，前端只负责渲染。
- **Scope boundary:** 不改变任何 agent 的实际选择与执行；不改变键集合、取值域、回落顺序与写回语义；不新增页面、接口或配置项。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### Existing Path

- 闭集与全部 per-key 事实（合法取值、`auto` 语义文案、PRD 覆盖白名单）已在 `src/backend/core/shared/models/lifecycle_agent.py` 定义一次，`LIFECYCLE_AGENT_KEYS` 的顺序即 UI 展示顺序。
- 只读视图在 `src/backend/core/use_cases/lifecycle_agents_console.py:build_lifecycle_agents_view`，按 `LIFECYCLE_AGENT_KEYS` 逐键构造行字典；HTTP 映射在 `src/backend/api/routes/agent_runner_lifecycle_agents.py`，路由层只做参数与 4xx。
- 前端渲染有三处，共用同一个只读视图：`frontend-public/components/agent-runner/lifecycle-agent-matrix.tsx`（Settings 全局层与 Roadmap 仓库级抽屉都渲染它）、`frontend-public/components/agent-runner/prd-agent-override-sheet.tsx`（自己 `map(view.lifecycles)`）。托管它的两处宿主 `frontend-public/app/(app)/app/settings/page.tsx` 与 `frontend-public/components/agent-runner/repository-agent-matrix-sheet.tsx` 只传 `scope` / `repoId`，无需改动。
- 类型契约在 `frontend-public/lib/api/types.ts` 的 `LifecycleAgentEntry`；请求封装在 `frontend-public/lib/api/lifecycleAgents.ts`（本次不需要改，字段是响应侧新增）。

### Reuse Candidates And Architecture Constraints

- 复用既有闭集模块作为**唯一**分组事实源：新增组描述常量与既有 `LIFECYCLE_AGENT_KEYS` 同处一个文件、同一风格（`LIFECYCLE_AGENT_AUTO_KEYS` / `LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 已是先例：per-key 事实放模型层，消费方读它）。
- 复用既有只读视图与既有组件树：分组只是给已有行加两个属性 + 在渲染时插入组标题，不新增组件、不新增接口、不新增状态。
- 保持四层依赖方向（`api → core → engines → infrastructure`）：core 定义事实并提供只读视图，api 只做 HTTP 映射，infrastructure 不参与；前端不引入第二份"键 → 组"映射。

### Frontend Impact

Full-stack。前端 app 是 `frontend-public/`（Next.js 16 / React 19 静态导出控制台），开发入口 `just run frontend-public`（整栈用 `just run all`），构建 `cd frontend-public && pnpm build`，真实 UI 验证走 `just e2e`（用例目录 `tests/playwright-e2e/tests/workflows/`）。`frontend-admin/` 是独立模板后台，不承载 iAR Console 页面。本机 wheel 中的静态导出需经既有 `just console-sync` 或打包流程同步。

### Existing PRD Relationship And Redundancy Risks

- 已归档 `P1-FEAT-20260918-110027-lifecycle-agent-matrix` 定义了九键闭集、三层落点与视图契约；本 PRD 只在其上补展示分组，不重做配置或写回。
- Pending `P1-FEAT-20260928-183700-issue-live-output-cli-console` 改的是 Issue 输出查看，与本 PRD 无交付顺序依赖，但两者都会动 `frontend-public/components/` 与 console API：合并时按"各自动到的文件"顺序 rebase 即可，无共享写入原语。
- 冗余风险：最可能的漂移是**在前端组件里再写一份键 → 组映射**（尤其 `prd-agent-override-sheet.tsx` 是第二个渲染点）。本 PRD 要求分组事实只在 core 常量处定义一次，并由 §9 的 `rg` 断言守住。

## 6. Recommendation

### Recommended Approach

在 `lifecycle_agent.py` 增加一份**分组描述常量**（组 id、组中文名、该组包含的键，按展示顺序排列），在导入期断言"九键恰好各属一组、不重不漏"；`build_lifecycle_agents_view` 每行补 `entry` / `entry_label` / `trigger` 三个展示字段，`lifecycles` 数组顺序与既有字段一律不动。前端两处渲染点按 `entry` 分组插入组标题与触发时机文案，行内选择、来源列、恢复动作与写回逻辑保持原样。

这样改的理由是：分组是"阶阶段之间的拓扑事实"，与键集合、取值域、PRD 覆盖白名单属于同一类知识，应该和它们放在同一个文件、由同一批消费方读取。前端只做渲染，未来新增第四个入口时不会出现第三份映射。

### Proposed Solution Summary (实现机制)

1. **声明方**：`src/backend/core/shared/models/lifecycle_agent.py` 新增组常量（例如 `LIFECYCLE_AGENT_ENTRY_GROUPS`：有序元组，每项含 `entry` id、中文 `label`、一行 `summary`、`keys`）。消费者是 core 的只读视图与前端；系统**只消费显式声明**，不做任何推断。导入期做一次 `assert`/`raise`，把"某键未分组 / 重复分组 / 组里出现未知键"变成加载期错误——与既有闭集"写错键在配置加载期直接报错"的风格一致。
2. **下发**：`build_lifecycle_agents_view` 在构造每行时补 `entry`、`entry_label`，并从 per-key 触发文案表取 `trigger`；行字段是**纯新增**，`lifecycles` 仍按 `LIFECYCLE_AGENT_KEYS` 顺序生成，`key` / `source` / `declared_*` / `effective_agent` / `follows_executor` 等既有字段一字不改。
3. **渲染**：`lifecycle-agent-matrix.tsx` 遍历 `view.lifecycles` 时按 `entry` 切块，在块首插组标题与组 `summary`，行内增一行 `trigger` 文案；新增 `data-testid="lifecycle-matrix-entry-<entry>"` 与 `lifecycle-matrix-trigger-<key>`，既有 per-key testid 全部保留。`prd-agent-override-sheet.tsx` 用同一份 `entry` 分组渲染它既有那批行。写回载荷构造、脏行判定、恢复动作零改动。
4. **类型同步**：`types.ts` 的 `LifecycleAgentEntry` 补三个字段；`lifecycleAgents.ts` 无需改动。
5. **一致性**：`docs/guides/lifecycle-agent-matrix.md` 把"各阶段在哪触发"表与三组归属对齐并交叉引用；`docs/prototypes/lifecycle-agent-matrix.html` 的三处矩阵覆盖层补组标题（仅插入组标题行与时机文案，不重排像素）。
6. **刻意避免的复杂度**：不新增页面、路由、接口、配置项或数据库表；不引入用户自定义分组、拖拽排序或分组级开关；不改变任何执行路径的 agent 选择（本 PRD 不进 `choose_agent` / 各阶段解析函数）；不改写回格式，因此无需迁移或兼容层。

### Alternatives Considered

| 方案 | 结论 |
|---|---|
| 分组映射只写在前端组件里（不改后端、不改 API） | 拒绝；第二个渲染点 `prd-agent-override-sheet.tsx` 会让映射出现两份，且与既有的"per-key 事实放模型层"（`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 先例）背离。 |
| 拆成两张配置文件（流水线六键一张、非流水线三键一张） | 拒绝；闭集被 8 个后端文件与 2 个前端文件消费，拆表要复制两层回落、来源层标注、写回校验与 PRD 覆盖抽屉，回归面翻倍且换不到新能力。 |
| 不分组，只在表里加一列"触发入口/时机" | 保留为更轻的退路；一行文字不足以表达"这六行共享同一次 claim"，且三行独立入口仍与流水线同列，误读只是变轻而非消除。 |

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### 7.1 Core Logic

- 分组常量的**唯一事实源**是 `lifecycle_agent.py`；组顺序即展示顺序，组内顺序即 `LIFECYCLE_AGENT_KEYS` 中的相对顺序。导入期校验：组内键并集 == `frozenset(LIFECYCLE_AGENT_KEYS)`、无重复、无未知键；不满足即抛错（加载期失败，不静默）。
- 只读视图保持"逐 `LIFECYCLE_AGENT_KEYS` 追加行"的既有循环，只在行字典里补字段；**不要**改成"先分组再展开"，否则键序契约（既有测试与文档都依赖）会漂移。
- 前端分组只影响渲染顺序与标题插入，不参与写回载荷计算：`buildPayload` / 脏行判定 / `pendingDeletes` / 恢复动作沿用现状，分组前后的保存请求体必须逐字节可比。
- 触发时机文案写在 core（后端），前端不拼文案；组标题同理。文案里不要出现端口、路径等易漂移的具体值。

### 7.2 Change Impact Tree

```text
.
├── src/backend/core/shared/models/lifecycle_agent.py [修改]
│   【总结】新增三组"触发入口"分组常量（组 id / 中文名 / 一行说明 / 组内键）与导入期完整性校验，闭集与取值域不动。
├── src/backend/core/use_cases/lifecycle_agents_console.py [修改]
│   【总结】只读视图每行补 entry / entry_label / trigger 三个展示字段，数组顺序与既有字段不变。
├── frontend-public/lib/api/types.ts [修改]
│   【总结】LifecycleAgentEntry 同步三个新增字段的类型与注释。
├── frontend-public/components/agent-runner/lifecycle-agent-matrix.tsx [修改]
│   【总结】按 entry 切块渲染组标题与触发时机，新增组/时机 testid，行内交互与写回逻辑不变。
├── frontend-public/components/agent-runner/prd-agent-override-sheet.tsx [修改]
│   【总结】PRD 覆盖抽屉按同一 entry 分组，行筛选规则与写回载荷不变。
├── docs/guides/lifecycle-agent-matrix.md [修改]
│   【总结】把"各阶段在哪触发"表与三组归属对齐，说明 UI 与分组常量同源。
├── docs/prototypes/lifecycle-agent-matrix.html [修改]
│   【总结】三处矩阵覆盖层插入组标题与时机文案，与真实界面同构。
├── docs/prototypes/lifecycle-agent-matrix.md [修改]
│   【总结】原型说明补一句分组呈现与对照入口。
├── tests/test_lifecycle_agents_console_api.py [修改]
│   【总结】断言每行都有非空 entry/entry_label/trigger、九键恰好各属一组且 lifecycles 顺序不变。
└── tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts [修改]
    【总结】断言三处入口的组标题可见、九行归属正确、既有 per-key 断言仍通过。
```

文件清单以语义锚点为准；若实施时发现 `repository-agent-matrix-sheet.tsx` 或 `settings/page.tsx` 也需要改动，先更新本树。

### 7.3 Risk Classification Register

| Change point | Tier | Decisive reason | Intervention | Oracle/gate |
|---|---|---|---|---|
| 分组归属与组名（哪些键属于同一触发入口） | R2 | 这是语义断言，机器只能验"自洽"，验不了"说得对" | Human confirmation | rv-1 |
| 只读视图新增字段与键序兼容 | R1 | 纯新增字段，旧消费方忽略即无影响；错在键序才会破契约 | Executor + pytest gate | rv-3 |
| 三处入口的分组渲染 | R1 | 本地可逆的展示变更，断言可按组标题判别 | Executor + e2e gate | rv-2 |
| 文档、原型与界面同构 | R1 | 静态可判别；不同构会留下互相矛盾的唯一可视化入口 | Executor + build/screenshot gate | `uv run mkdocs build --strict`、`prototype-screenshots.spec.ts` |

### 7.4 Executor Drift Guard

- 复核闭集与既有 per-key 常量的位置：`rg -n 'LIFECYCLE_AGENT_KEYS|LIFECYCLE_AGENT_AUTO_KEYS|LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS' src tests frontend-public`；分组常量必须与它们同文件，不要另建模块或第二张表。
- 复核所有消费 `view.lifecycles` 的前端点：`rg -n 'lifecycles' frontend-public`；每个点都要按 `entry` 分组，不允许漏掉 `prd-agent-override-sheet.tsx`。
- 复核只读视图的构造循环仍以 `LIFECYCLE_AGENT_KEYS` 为准：`rg -n 'for lifecycle_key in LIFECYCLE_AGENT_KEYS' src/backend/core/use_cases`。
- 复核既有 e2e 选择器未被改名：`rg -n 'lifecycle-matrix-row-|lifecycle-matrix-select-|lifecycle-matrix-source-' tests/playwright-e2e`，改动前后命中集合应只增不减。
- 若 `docs/prototypes/lifecycle-agent-matrix.html` 的底图或覆盖层坐标在本轮已被别的 PRD 改动，以真实界面为准重建覆盖层，不要照抄旧像素。

### 7.5 Flow / Architecture Diagram

```mermaid
flowchart LR
  A["lifecycle_agent.py<br/>分组常量（唯一事实源）"] --> B["lifecycle_agents_console.py<br/>只读视图行补 entry/label/trigger"]
  B --> C["HTTP: GET /agent-runner/lifecycle-agents"]
  C --> D["lifecycle-agent-matrix.tsx<br/>Settings / 仓库齿轮共用"]
  C --> E["prd-agent-override-sheet.tsx<br/>PRD 覆盖抽屉"]
  A --> F["docs/guides + docs/prototypes<br/>与界面同构"]
```

No data model changes in this PRD.

### 7.6 Realistic Validation Plan

```yaml
- id: rv-1
  behavior: "全局 Settings 的生命周期矩阵按三个触发入口分组呈现，九行归属正确且仍可逐个编辑"
  reviewer: human
  real_entry: "just run all；浏览器打开本机控制台 → Settings → 「Agent 管理」→「生命周期 Agent 设置」"
  expected: "三个组标题可见且顺序为 实现流水线 / 讨论与内容生成 / 独立入口；实现流水线组含 实现·修复·收尾·校验·审核·监督 六行，讨论与内容生成组含 辩论·内容生成 两行，独立入口组只含 决策 一行；每行附近可见触发时机文案；九行的下拉、来源列与恢复动作照旧可用"
  mock_boundary: "可用 route mock 提供 auth/repositories/roadmap 等无关数据；真实 console 静态产物、真实只读视图响应与真实页面渲染必须保留"
  tier: R2
  test_layer: e2e
  required_for_acceptance: true
  presentation: "真实页面截图/录屏绝对路径 + open 命令；静态截图在 PRD 与 evidence-report 内用相对路径内嵌并标注本地可见；PR 证据评论提供可访问版本"
  critical_value_source: "组标题与行归属来自浏览器实际渲染；行数据来自真实 /api/v1/agent-runner/lifecycle-agents 响应"
  must_cross: "真实 console 静态产物 -> 浏览器 Settings 页面 -> 只读视图 API -> core 分组常量 -> DOM 分组标题与行"
  forbidden_bypasses: "不得用组件预览、手工注入页面 state、伪造视图响应或直接读常量断言代替真实页面"
  fresh_state_probe: "新浏览器 context 重新进入同一页面并切走再切回 Tab，分组与归属保持一致"
  final_tree_evidence: "保存最终 Git tree、页面截图/录屏、真实 API 响应摘要；组件/视图/常量变更后重跑"
  negative_control: "临时把 planner 的所属组改为 pipeline（或让某键不落在任何组），重跑同一 e2e"
  expected_fail: "「独立入口」组为空、决策行出现在实现流水线组，或分组断言找不到组标题"
- id: rv-2
  behavior: "Roadmap 仓库齿轮抽屉与 PRD「Agent 覆盖」抽屉呈现同一分组，且分组不来自前端第二份映射"
  reviewer: verifier
  real_entry: "just e2e lifecycle-agent-matrix"
  expected: "仓库级抽屉与 PRD 覆盖抽屉都出现同一组标题集合；PRD 覆盖抽屉仍不提供 决策 行；既有 per-key 断言（下拉取值、来源列、恢复动作）全部继续通过；rg 检索显示键→组映射只在 core 常量处定义"
  mock_boundary: "可用 route mock 提供仓库列表与 PRD 内容；真实前端产物、真实视图响应与真实组件渲染必须保留"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  presentation: "Playwright 用例名与通过输出、两份抽屉截图；PR 证据评论提供可访问版本"
  critical_value_source: "组标题来自页面 DOM；是否只有一份映射来自 rg 命中集合"
  must_cross: "真实前端产物 -> 两个抽屉组件 -> 同一只读视图响应 -> 组标题 DOM"
  forbidden_bypasses: "不得只断言全局矩阵、不得在组件里再写一份映射后仍声称同源"
  fresh_state_probe: "新浏览器 context 打开 PRD 覆盖抽屉，确认分组与全局视图一致且 决策 行仍缺席"
  final_tree_evidence: "保存最终 Git tree、e2e 输出、抽屉截图与 rg 命中清单；组件变更后重跑"
  negative_control: "临时让 prd-agent-override-sheet.tsx 退回平铺渲染，重跑同一 e2e"
  expected_fail: "覆盖抽屉缺少组标题或 决策 行意外出现"
- id: rv-3
  behavior: "只读视图每行都带组与触发时机，九键恰好各属一组，且 lifecycles 顺序与既有字段保持兼容"
  reviewer: verifier
  real_entry: "IAR_CONFIG=<隔离 config.toml> uv run iar console --port 8391 --no-browser；另开终端 curl -s 'http://127.0.0.1:8391/api/v1/agent-runner/lifecycle-agents?scope=global'"
  expected: "lifecycles 长度仍为 9、键序等于 LIFECYCLE_AGENT_KEYS；每行 entry / entry_label / trigger 均非空；按 entry 聚合后恰好三组、并集等于九键；既有字段（auto_allowed / declared_* / effective_agent / follows_executor / source）名与含义未变"
  mock_boundary: "可用隔离 HOME 与临时 IAR_CONFIG；真实 CLI、真实 HTTP 路由、真实只读视图与真实常量必须保留"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  presentation: "原始 URL、JSON 片段与 pytest 输出；PR 证据评论提供可访问版本"
  critical_value_source: "键序与 entry 取值来自真实 HTTP 响应；九键清单来自 core 闭集常量"
  must_cross: "curl -> HTTP 路由 -> core 只读视图 -> 分组常量 -> 响应 JSON"
  forbidden_bypasses: "不得直接调用 build_lifecycle_agents_view 或读常量断言代替真实 HTTP 响应"
  fresh_state_probe: "新 HTTP client 再请求一次 scope=repository 视角，确认同一分组与键序"
  final_tree_evidence: "保存最终 Git tree、原始 URL、JSON 片段与 pytest 输出；视图/常量变更后重跑"
  negative_control: "临时让某键不出现在任何组的 keys 里（触发导入期校验）或让组顺序与常量不符"
  expected_fail: "导入期抛出分组校验错误，或响应中某行 entry 为空、组并集不等于九键"
```

失败排查：rv-1 先查前端是否真的按 `entry` 切块（而不是只加了一列文案）；rv-2 先查 `prd-agent-override-sheet.tsx` 是否漏改、以及是否存在第二份映射；rv-3 先查只读视图是否仍以 `LIFECYCLE_AGENT_KEYS` 顺序构造行、以及新增字段是否落进响应。需要真实 GitHub/Agent 凭据的现场可补充，但上述本地确定性真实入口验证是无凭据的验收底线。

### 7.7 Low-Fidelity Prototype

目标布局（桌面与窄屏同一结构，窄屏仅列宽收窄，组标题不折行）：

```mermaid
flowchart TB
  A["Settings · Agent 管理 · 生命周期 Agent 设置"] --> B["组：实现流水线 — iar run / daemon 认领后，共享同一次 claim"]
  B --> B1["实现 | 修复 | 收尾 | 校验 | 审核 | 监督（每行：下拉 + 来源 + 恢复）"]
  A --> C["组：讨论与内容生成 — Phase 0 讨论、Phase 1 与横切文案生成"]
  C --> C1["辩论 | 内容生成"]
  A --> D["组：独立入口 — iar ask，不在 Issue 流水线上"]
  D --> D1["决策"]
```

#### Interactive Prototype Change Log

| File Path | Change Type | Before | After | Why |
|---|---|---|---|---|
| `docs/prototypes/lifecycle-agent-matrix.html` | Modify | 三处矩阵覆盖层为九行平铺 | 插入三处组标题行与每行触发时机文案 | 原型是文档引用的唯一可视化权威，分组后必须与真实界面同构 |
| `docs/prototypes/lifecycle-agent-matrix.md` | Modify | 未说明分组 | 补一句分组呈现与对照入口 | 让原型说明与界面结构一致 |

### External Validation

No external validation required; repository evidence was sufficient.

## 8. Delivery Dependencies

### Delivery Dependencies

- Group: lifecycle-agent-matrix-ui
- Depends on groups:
  - none
- Depends on tasks/issues:
  - none
- Gate type: none
- Notes: 闭集与视图契约由已归档 `P1-FEAT-20260918-110027-lifecycle-agent-matrix` 定义，本 PRD 只补展示分组；与 pending `issue-live-output-cli-console` 无交付顺序依赖，仅共享 `frontend-public/components/` 与 console API 的合并面。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 人审结果 | 呈递材料 | 十秒自查 |
|---|---|---|
| 三处入口按同一分组呈现，九行归属正确（rv-1、rv-2） | 最终交付填写 Settings 页面与两份抽屉的截图/录屏绝对路径及 `open "<绝对路径>"`；静态截图在 PRD 与 evidence-report 内用相对路径 Markdown 内嵌并标注本地可见，PR 评论提供可访问版本。 | 组标题是否有三个、`决策` 是否独立成组、`辩论/内容生成` 是否与流水线六行分开。 |
| 只读视图字段与键序兼容（rv-3） | 最终交付填写原始 URL、JSON 片段与 pytest 输出的绝对路径及 `open` 命令。 | `lifecycles` 是否仍是九行原顺序、每行 `entry/entry_label/trigger` 是否非空。 |

当前尚未实施，呈递路径留空；不得把原型图或 mock 页面当作真实截图。

### 9.2 Acceptance Evidence Package

#### Human-Confirmed

- [ ] 依 rv-1 的真实页面材料，确认三组标题与九行归属符合 §2 的分组断言（六个共享同一次 claim 的行在一组、`辩论/内容生成` 在一组、`决策` 独立一组），且每行触发时机可读。
- [ ] 打开 §9.1 的两份实际呈递材料，确认真实界面与接口响应可读、可核对。

#### Behavior And Frontend Acceptance

- [ ] rv-2 证明 Roadmap 仓库齿轮抽屉与 PRD 覆盖抽屉呈现同一分组，且 PRD 覆盖抽屉仍不提供 `决策` 行。
- [ ] 既有 e2e 断言全部继续通过，无一处被放宽：`just e2e lifecycle-agent-matrix` 中按 `lifecycle-matrix-row-*` / `lifecycle-matrix-select-*` / `lifecycle-matrix-source-*` 定位的用例命中集合只增不减。
- [ ] 分组不进入写回：改动任一行后生成的保存请求体，与改动前同一操作的载荷逐字段一致（组标题与时机文案不出现在载荷里）。
- [ ] `frontend-public` 的 `pnpm build` 与 Console 静态导出通过，`just console-sync` 后本机 wheel 的静态产物包含分组标题。

#### Contract And Compatibility Acceptance

- [ ] rv-3 证明 `lifecycles` 长度仍为 9、键序等于 `LIFECYCLE_AGENT_KEYS`，既有字段名与含义未变，新增三个字段对旧消费方为纯新增。
- [ ] `rg -n 'LIFECYCLE_AGENT_KEYS|LIFECYCLE_AGENT_ENTRY' src tests frontend-public` 显示键→组映射只在 `src/backend/core/shared/models/lifecycle_agent.py` 定义，前端无第二份映射。
- [ ] 分组完整性在加载期可判别：人为让某键不属于任何组时，导入 `lifecycle_agent` 即报错（附 §9.2 的复现命令与输出）。

#### Architecture And Documentation Acceptance

- [ ] 分组常量与只读视图分别落在 core 的 `shared/models` 与 `use_cases`，`api` 只做 HTTP 映射，未新增 `api → engines` 或 `core → infrastructure` 依赖；`just lint --full` 通过。
- [ ] `docs/guides/lifecycle-agent-matrix.md` 的"各阶段在哪触发"表与三组归属一致，`docs/prototypes/lifecycle-agent-matrix.html` / `.md` 与真实界面同构；`uv run mkdocs build --strict` 通过。

#### Validation Acceptance

- [ ] §7.6 的 rv-1、rv-2、rv-3 在最终 Git tree 上执行，证据含原值来源、必经边界、禁止旁路、fresh probe 与负控。
- [ ] rv-1 与 rv-2 的负控均转红（planner 错组 / 覆盖抽屉退回平铺），rv-3 的负控触发导入期校验错误；任何现场结果与证据冲突时重开相应 oracle。
- [ ] 独立 verifier 对 rv-2、rv-3 的最终树证据给出结论 — runner-owned gate: independent verifier (Phase 3.6)。

#### Delivery Readiness

- [ ] 推荐的"后端单一分组常量 + 视图下发 + 三处渲染"完整落地；无遗留的平铺渲染点、无临时兼容层、无待办分组。
- [ ] PR 或完成消息原样呈递 §9.1 的实际材料；PR 证据评论包含验证树、必要门禁与可访问审阅入口。
- [ ] 完成 §13 Final Reconciliation；仅余 Human-Confirmed 时横幅改为 `🧍 待人工验收`，全部确认后才改 `✅ 可归档` 并归档 — runner-owned gate: PRD 归档动作。

## 10. Functional Requirements

- **FR-1:** 九个生命周期键的"触发入口分组"由 `src/backend/core/shared/models/lifecycle_agent.py` 中的单一有序常量声明；组含组 id、组中文名、一行组说明与组内键列表，且导入期校验组内键并集恰好等于 `LIFECYCLE_AGENT_KEYS`、无重复、无未知键。
- **FR-2:** 分组为固定三组——`实现流水线`（实现/修复/收尾/校验/审核/监督）、`讨论与内容生成`（辩论/内容生成）、`独立入口`（决策）；不提供用户自定义分组或排序。
- **FR-3:** 只读视图 `GET /api/v1/agent-runner/lifecycle-agents` 的每一行新增 `entry`、`entry_label`、`trigger`；`lifecycles` 的顺序、既有字段名与含义、scope 行为全部不变。
- **FR-4:** 全局 Settings 矩阵、Roadmap 仓库级齿轮抽屉、PRD 覆盖抽屉三处按同一 `entry` 分组渲染组标题与每行触发时机；PRD 覆盖抽屉的行筛选规则不变（仍不提供 `决策` 行）。
- **FR-5:** 分组只影响呈现：下拉取值集合、来源层标注、"本层设置"标记、恢复动作、脏行判定与写回载荷结构与本次改动前完全一致。
- **FR-6:** 既有选择器 `lifecycle-matrix-row-<key>` / `lifecycle-matrix-select-<key>` / `lifecycle-matrix-source-<key>` / `lifecycle-matrix-restore-<key>` 一律保留；新增组与时机选择器。
- **FR-7:** 前端不得存在第二份"键 → 组"映射；分组文案（组名、组说明、每行时机）一律来自后端响应，前端不拼接业务文案。
- **FR-8:** `docs/guides/lifecycle-agent-matrix.md` 的触发时机表与三组归属一致；`docs/prototypes/lifecycle-agent-matrix.html` 与 `.md` 同步展示分组，且原型仍标注为设计意图而非运行证据。
- **FR-9:** 本 PRD 不进入任何执行路径：`choose_agent`、`resolve_*_agent`、`lifecycle_agent_resolution` 的解析语义与 `iar run` / `daemon` / `review` / `ask` 的实际 agent 选择零变化。

## 11. Non-Goals

- 不改变九键闭集、`auto` / `executor` 的合法键集合、两层与 PRD 层回落顺序、保留式写回与 PRD 覆盖白名单。
- 不拆分为两张配置文件、不新增第二张矩阵视图或平行 Settings 页面。
- 不新增页面、路由、后端接口、配置项或数据库表；不做迁移。
- 不提供用户自定义分组、拖拽排序、分组级开关或权限控制。
- 不改变任何阶段实际挑选的 agent，也不改变 Issue 流水线拓扑本身。
- 不在本 PRD 内重做 `docs/prototypes/lifecycle-agent-matrix.html` 的底图与像素坐标。

## 12. Risks And Follow-Ups

- **分组语义漂移：** 分组是"哪些阶段共享同一次 claim"的断言，若未来新增阶段或拆分 claim，组常量必须同轮更新；否则界面会重新开始说谎。此风险由"分组常量与闭集同文件 + 导入期完整性校验"约束到最小，但不自动消除语义判断。
- **第二份映射：** `prd-agent-override-sheet.tsx` 是第二个渲染点，最容易被顺手写一份本地映射；由 §9.2 的 `rg` 断言与 rv-2 的负控守住。
- **原型与静态导出的时滞：** 原型 HTML 与 `frontend-public/out/` 都是产物，改造后需重新同步（`just console-sync`）才能在本机 wheel 中看到分组；文档与 PR 证据必须标注所依据的构建批次。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 分组事实的归属层 | 后端 core 单一常量 + 只读视图下发 | 前端组件内的本地映射 | 第二个渲染点会复制映射；per-key 事实放模型层已有先例（`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS`）。 |
| D-02 | 分组粒度 | 固定三组（流水线 / 讨论与内容生成 / 独立入口） | 每行一列"触发时机"不做分组 | 一行文字表达不了"六行共享同一次 claim"，独立入口仍与流水线同列。 |
| D-03 | 键序与既有字段 | 完全保持 `LIFECYCLE_AGENT_KEYS` 顺序与既有字段名 | 按组重排 `lifecycles` 数组 | 键序是既有文档与测试的契约；重排会波及 e2e 断言与旧消费方，纯增量才无破坏。 |
| D-04 | 配置模型 | 九键仍在一张表内 | 拆成流水线表与非流水线表 | 闭集被 8 个后端文件与 2 个前端文件消费，拆表要复制回落、来源标注与写回校验，回归面翻倍。 |
| D-05 | 文案来源 | 组名、组说明、每行时机均由后端下发 | 前端硬编码中文文案 | 前端硬编码会与后端常量分叉，且新增入口时要改两处。 |

### Final Reconciliation

- Interpretation: pending — 实施后按真实页面与接口结果复核。
- Public behavior and contracts: pending — 核对只读视图新增字段的兼容性与键序。
- Related PRD status: pending — 核对归档的 lifecycle-agent-matrix 与 pending 的 issue-live-output 现状。
- Requirements and risks: pending — 核对三处入口一致性、写回不变性与原型同步。
- Reconciled differences: none yet.
