# 生命周期 Agent / 模型设置 · 交互原型说明

本页先前归档 `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md` 的交互原型，2026-10-09 加入独立生命周期设置页（v2.8）。Settings 中旧的 Agent-only 生命周期矩阵已替换为统一设置页入口；Backlog 仓库齿轮进入同一页面并预选仓库。新页面集中展示九阶段 Agent、命名预设、模型 ID、推理深度和来源。Settings 的独立回退顺序卡片现可为每个备用 Agent 选择同 Agent 的命名预设，并预览对应 TOML 写入；该项是新增设计提案，等待 PRD 人审确认。Backlog 为现行产品名称；仓库底图文件沿用历史名 `roadmap-real.png`，截图内的旧侧栏标签由原型覆盖层改为 Backlog。

## 打开方式

[打开交互原型](lifecycle-agent-matrix.html)（纯静态单文件，无构建步骤；也可从 [原型目录](hub.html) 进入）。[查看更新后的 Settings 主视图](assets/lifecycle-agent-matrix/preview-settings.png)；[查看三条回退链都绑定预设的保存预览](assets/lifecycle-agent-matrix/preview-fallback-settings.png)。可用 [`?screen=settings&demo=fallback-preset`](lifecycle-agent-matrix.html?screen=settings&demo=fallback-preset) 直达三条回退链各自绑定匹配预设的编辑状态。

## 三层落点（本原型要评审的核心结论）

| 层 | 配置文件 | 界面落点 | 原型里的画面 |
|---|---|---|---|
| 全局（机器级） | `config.toml`：agent 注册块标签、`[agent_runner.lifecycle_agents]`、`[agent_runner.runner]` | Settings 的 Agent 管理区保留标签与回退设置；生命周期卡片只提供统一设置页入口 | 视图一、统一设置页 |
| 仓库级 | 该仓库 `.kedacode.toml` 的 `[agent_runner.lifecycle_agents]` | 统一生命周期页的范围选择器；Backlog 仓库行 ⚙ 进入同一页并预选仓库 | 统一设置页 |
| PRD 级 | PRD 文件头部 `- lifecycle_agents:` bullet 块 | PRD 原文页工具栏「Agent 覆盖」 | 视图三（抽屉） |

仓库配置仍写入仓库自己的 `.kedacode.toml`。全局和仓库设置只在统一生命周期页编辑；Settings 移除旧矩阵，Backlog 仓库齿轮作为预选范围的快捷入口。

## 新增：生命周期 Agent / 模型统一设置页

从 Settings 的「生命周期 Agent、模型与推理深度」卡片点击「打开统一设置」进入；也可点 Backlog 仓库行齿轮直接进入并预选该仓库。原型可用 `?screen=lifecycle-settings` 直接打开该状态。

- 顶部范围切换为「全局 · config.toml」或「仓库 · .kedacode.toml」；仓库范围额外选择目标仓库，并显示该仓库继承的全局配置。
- 九阶段按三个真实触发入口分组；每行同时呈现阶段预设、生效 Agent / 模型、推理深度、来源和本层操作。未指定模型/推理深度时明确显示 Agent 默认值。
- 阶段预设下拉支持全局绑定/不绑定，以及仓库继承/覆盖；`fix` / `closeout` 未单独绑定时展示继承实现阶段预设的结果。
- 同一页的预设区允许新建预设并编辑 Agent、模型 ID、推理深度。仓库范围可以覆盖全局预设定义；编辑 inherited preset 时只形成仓库层覆盖。
- 预设卡片列出当前绑定的生命周期；编辑共享预设前能看到会受影响的阶段。若只想更改单个阶段，可以新建预设再单独绑定。
- 「恢复继承」清除该仓库阶段的 Agent 与预设绑定；全局阶段可清除此阶段显式配置。保存只展示当前作用域的 TOML 写入预览，不写本地文件。
- 页面是交互原型；预设值是演示数据。真实 config loading、保存、CLI 命令及 API 合约尚未在本原型实现。

## Settings：Agent 管理与统一设置入口

Settings 页在真实的「设置」标题与用户副标题下方显示「Agent 管理」区块。此处保留 Agent 标签编辑和 Agent 回退顺序；旧的 Agent-only 生命周期矩阵已移除，替换为一张醒目的「生命周期 Agent、模型与推理深度」入口卡片，点击进入唯一的生命周期编辑页。页面滚动时 Agent 管理区块标题保持可见。

- 「Agent 标签设置」说明 `auto` 的判定依据是 Issue 上的 agent 标签，并注明工作流标签（`agent/ready` 等，来自 `[agent_runner.labels]`）不在本表范围。
- 校验：标签名不能为空，也不能两个 agent 使用同一个标签；非法时该行标红并阻断保存。演示按钮「两个 agent 用同一标签」把 kimi 的标签改成另一个 agent 正在用的值。
- 生命周期入口卡片说明九阶段矩阵、预设、模型、推理深度和来源均在统一设置页维护；Settings 不再呈现第二张生命周期矩阵。
- Agent 标签和回退顺序保存到 `config.toml`；生命周期设置按当前范围保存到 `config.toml` 或仓库 `.kedacode.toml`。预览说明段内其余字段/键保持不变。
- 改过的行/字段高亮；保存后基线更新，界面回到“与 config.toml 一致”。

## 原型形式：真实截图 + HTML 覆盖层（hybrid）

- **底图是真实 frontend-public 截图**，三张同尺寸（1440×1200 @2x）：Settings 页、Backlog 依赖图、PRD 原文页。采集方式见各图的 `*.source.md` 旁车与 `tests/playwright-e2e/tests/workflows/prototype-screenshots.spec.ts`。Backlog 底图是 2026-09-18 的历史截图，侧栏仍显示旧名称；现行路由与文案统一使用 Backlog。其余底图像素保持真实页面截图，不用图片生成模型伪造产品界面。
- **覆盖层只绘制本原型调整的区域**：Settings 页的 Agent 标签区、统一生命周期页入口、回退顺序卡片，Backlog 仓库行齿轮和 PRD 工具栏入口。旧生命周期矩阵与仓库矩阵抽屉不再展示。其余像素全部来自真实产品。
- **画布固定 1440×1200 并按视口宽度等比缩放**，热点与覆盖层使用固定像素坐标（在真实页面上用 `getBoundingClientRect()` 实测），因此不会随视口漂移。
- **视图切换走真实侧栏导航**（截图上的 Settings / Backlog 行），没有额外的原型标签栏，避免出现真实产品里不存在的页面结构。
- 新增区域统一带「本原型新增」标注；设计令牌（oklch 主题变量、shadcn Button/Card 类）取自 `frontend-public/app/globals.css` 与 `frontend-public/components/ui/*.tsx`。

## 下拉语义（三层共用）

- 矩阵按**触发入口分组**呈现（2026-09-28 起）：九行分成「实现流水线」（实现/修复/收尾/校验/审核/监督）、「讨论与内容生成」（辩论/内容生成）、「独立入口」（决策）三组，组标题 + 一行组说明插在段首，每行生命周期名下再带一行触发时机；分组事实的唯一来源是后端 `LIFECYCLE_AGENT_ENTRY_GROUPS`，三层共用同一分组。分组只是呈现：下拉取值、来源列、写回载荷与分组前完全一致。PRD 覆盖抽屉沿用同一分组，但**不含决策行**（planner 没有 PRD 消费点，与真实 UI 一致不下发）。**副本同步**：本目录的自包含原型不能 import 后端常量，`lifecycle-agent-matrix.html` 里的 `ENTRY_GROUPS` 是 core 常量的副本，且 `docs/` 不在"单一份键→组映射"的 `rg` 门禁范围内——但漂移不会漏过去：这份副本连同 `tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts` 里的同名副本，由守卫测试 `tests/guards/test_lifecycle_entry_group_copies.py` 逐组比对组顺序 / 组 id / 组名 / 组内键序 / 组说明，改动 core 分组常量而忘了同步会直接 CI 转红。
- 下拉里**只有真实取值**：已注册 agent（codex / claude / kimi / pi / codebuddy / qoder / opencode）、该阶段的 `auto`、fix 与 closeout 的 `跟随实现（executor）`。没有"未设置""跟随全局"之类的伪选项。
- **`auto` 的文案按阶段如实描述**（各阶段语义本来就不同，见下表）；没有实现 `auto` 的阶段（决策 / 内容生成）不给这个选项。
- 下拉中**选中的就是当前生效值**，即优先级链上第一个被声明的值（本层声明 > 上一层声明 > 既有配置键 / 内置默认）；`executor` 不做二次解析，只在下方补一行"当前实现阶段：claude"。
- 右侧「当前值来源」列说明这个值来自哪一层（本机 `config.toml` / 本仓库 `.kedacode.toml` / 既有配置键 / 内置默认），并在本层已显式设置时给出「不写本键（跟随既有配置）」或「跟随全局（删除本键）」的恢复入口。
- **只有改动过的行会写进本层文件**：没动的行不在该层声明，继续沿回退链走；把下拉改回与继承值相同的取值，也会被视为"未改动"。保存预览逐行列明"写入 / 不写入并沿用谁"。
- 这样三层的关系是"越靠近 PRD 越优先"，而"继承"是**不动它**的默认状态，不需要在取值域里造一个假值。

| 阶段 | `auto` 的真实含义 | 代码落点 |
|---|---|---|
| 实现 | 按 Issue 上挂的 `agent/*` 标签路由 | `choose_agent`（`run_agent_once.py`） |
| 校验 | 从 `runner.agent_fallback_order` 里挑第一个 ≠ 实现者的 agent（独立性靠换模型） | `run_verifier_agent.py` 的 `_choose_verifier_agent` |
| 审核 | `allow_same_agent` 为真时沿用实现者；否则从注册表取第一个 ≠ 实现者的 | `run_agent_once.py` 的 `resolve_reviewer_agent` |
| 监督 | 发布路径沿用本次实现者；`kc review` 路径按标签路由 | `run_agent_once.py` 的 `resolve_supervisor_agent` |
| 辩论 | 按 `agent/deliberate` 标签路由 | 辩论队列 |
| 修复 / 收尾 / 决策 / 内容生成 | 无 `auto` 语义，不给该选项 | — |

## agent 回退顺序（Settings 中独立于生命周期矩阵的配置卡片）

生命周期设置页决定各阶段的主 Agent 与预设。**主 agent 失败或额度受限时换下一个**是另一套机制，入口在 Settings 的独立回退顺序卡片：

- 编辑 `[agent_runner.runner]` 的 `agent_fallback_order`（默认 `["claude", "kimi", "codex"]`，本机 `config.toml` 没写、走代码默认）与 `max_agent_switches`（默认 `2`）。
- 每个回退候选可选一个**属于同一 Agent**的命名模型预设；可选项按候选 Agent 过滤。绑定后，该候选作为回退 Agent 执行时使用预设模型与支持的推理深度；清除绑定则恢复 Agent 默认值。原型只演示全局 `config.toml` 范围。
- 交互：顺序可上下移动、可移除、可从剩余已注册 agent 里加到末尾；「最多切换」是数字输入，实时显示"最多尝试 N 个 agent"；保存预览包括 `[agent_runner.runner]` 与 `[agent_runner.runner.agent_fallback_presets]` 的差异，其余 runner 键不变。
- 回退链只列"换谁"，**第一个尝试的 agent 由矩阵 / 标签路由决定**；本机未安装的 agent 会被运行时跳过（原型里对非注册表项标出"本机未安装，会被跳过"）。
- 回退链在 Issue 执行切换中使用，也被校验、审核和监督的候选顺延复用；决定四建议在共享候选路径里应用映射，并由 PRD 人审确认最终范围。
- 新增的回退预设绑定行为仍是 PRD 待确认项：建议用 `agent_fallback_presets` 按 Agent 名称映射命名预设；绑定必须校验预设 Agent 与候选 Agent 一致。是否覆盖所有复用回退链的 runner 场景、以及 global/repository 的配置范围，以 PRD 人审决定为准。

![interactive prototype：Claude、Kimi 与 Codex 分别绑定自己的模型预设及 TOML 保存预览](assets/lifecycle-agent-matrix/preview-fallback-settings.png)

截图验证层级：**interactive prototype**；模型和推理档位是已公开模型的演示组合，不代表当前仓库的 Agent 参数模板或账号可用性。

## 状态模型

生命周期 Agent / 模型 / 推理深度只有一处编辑矩阵：专门设置页。Settings 卡片和 Backlog 仓库齿轮只是通往该页的入口；PRD 覆盖抽屉仍保留为局部上下文编辑。

```text
Settings · Agent 管理
→ Agent 标签设置区：编辑标签 / 颜色 / 描述
→ 改任一字段 → 该行高亮、保存点亮；重复标签使冲突行标红并阻断保存
→ 生命周期入口卡片 → 打开统一生命周期设置页（默认全局范围）
→ 回退顺序卡片 → 调整 agent_fallback_order / max_agent_switches / 可选备用 Agent 预设，并查看独立写入预览

生命周期设置页 · 全局范围
→ 九阶段矩阵显示阶段预设、Agent、模型 ID、推理深度和配置来源
→ 编辑阶段预设绑定或预设三元组 → 保存预览只列当前全局差异
→ 切换仓库范围并选仓库 → 未覆盖阶段继承全局值

Backlog · 受管理仓库列表（历史截图）
→ 点 Settings → 打开统一生命周期设置页
→ 点仓库行右侧 ⚙ → 打开同一设置页并预选该仓库
→ 修改仓库配置并保存预览；返回后可切换其他仓库再次进入
→ 点 PRD 卡片（直开入口）→ PRD 原文画面

PRD 原文 · 覆盖抽屉
→ 点工具栏「Agent 覆盖」→ 抽屉打开，右侧实时预览将要写入的文件头部
→ 勾选生命周期 → 行内下拉解禁，默认值取当前继承值；继承值为 auto 的行要求显式选择 agent
→ 演示：文件里已有未注册 agent → 该行变红 + 写回被阻断
→ 写回 PRD 文件 → 头部 bullet 块变为“已写入”样式 + toast
→ 清除全部覆盖 / Esc / 点遮罩 → 回到继承
```

## 关键可点击对象与点击结果

- 侧栏 **Backlog / Settings**：在三个画面之间切换（每个画面使用各自底图）。
- Settings「生命周期 Agent、模型与推理深度」入口卡片：打开唯一生命周期设置页；Backlog 仓库行右侧 **⚙**：打开同一页面并预选该仓库。
- 统一生命周期页的 **范围选择器和九阶段矩阵**：切换全局/仓库范围，查看 Agent、预设、模型、推理深度与来源；恢复操作清除当前层覆盖。
- **演示：两个 agent 用同一标签**：把 kimi 标签改成另一个 agent 正在用的值，展示冲突拦截。
- Agent 标签、生命周期配置和回退顺序的 **保存更改**：分别展示目标配置段的写入预览；生命周期页只预览当前作用域差异。
- 回退顺序卡片 **Agent 预设下拉 / ↑ / ↓ / ✕ / ＋ 加到末尾 / 最多切换**：每个候选只列同 Agent 的预设；选择“Agent 默认”清除绑定；保存预览可见候选顺序、切换预算和 `agent_fallback_presets` 映射。
- PRD 原文工具栏 **「Agent 覆盖」**：打开覆盖抽屉（右侧，带遮罩）。
- 覆盖抽屉 **勾选框 / 下拉 / 演示：文件里已有未注册 agent / 清除全部覆盖 / 写回 PRD 文件**：勾选后立即在右侧头部预览里新增 bullet 行。
- 底部「原型」Dock（评审工具层，非产品 UI）：**可点击区域开关**（默认开启）、**↺ 重置**（回到初始状态）、**返回 Hub**、**原型说明**。

## 演示数据说明

- 九个生命周期键名、中文名、取值来自 PRD §1 与 §10 的闭集；已注册 agent 取自 `config.toml` 的七个注册块（codex / claude / kimi / pi / codebuddy / qoder / opencode）。
- 回退预设下拉用 `fallback-claude`、`fallback-kimi`、`fallback-codex` 三个演示预设，分别匹配回退链里的 claude / kimi / codex；展示值为 `claude-sonnet-5-5 / max`、`kimi-k2.6 / high`、`gpt-5.4 / xhigh`。这些是帮助评审具体化模型与推理档位的样例，不代表当前仓库已配置对应的参数模板或账号可用性。实际已有的 codebuddy / qoder 示例预设仍保留。
- **Agent 标签设置**的七个标签取自真实 `config.toml`：`agent/codex`（#5319E7）、`agent/claude`（#BFDADC）、`agent/kimi`（#FF6B6B）、`agent/pi`（#7C3AED）、`agent/codebuddy`（#0052D9）、`agent/qoder`（#FF8C42）、`agent/opencode`（#0EA5E9），描述也逐字来自各自的 `label_description`。
- 三个层级的初始值都是本机真实取值，来源列标注它来自哪一层：实现 `claude`（`runner.default_agent`）、校验 `auto`（`validation.verifier_agent` 缺省值）、审核 `auto`（`pre_pr_review.review_agent`）、监督 `auto`（`post_pr_supervisor.supervisor_agent`）、决策 `claude`（`interactive_decision.default_agent`）、内容生成 `claude`（`generated_content.default_agent`）、辩论 `auto`（`agent/deliberate` 标签路由）；fix / closeout 为内置默认 `executor`。
- 受管理仓库列表与真实 registry 一致（`repo_id` / `display_name`），行位坐标实测自真实页面。
- 保存与写回都是前端模拟，没有落到真实文件；PRD 原文与头部预览取自真实的 `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md`。

## 需要在实施前定的事

1. **矩阵是单值，回退顺序是另一张表。** 原型按"矩阵选主 agent + Settings 单独一节编辑 `runner.agent_fallback_order` / `max_agent_switches`"实现；如果之后想要"每个阶段一条独立顺序链"，配置格式要从单值改成数组，是破坏性变更。
2. **`auto` 的文案按阶段如实描述**（实现=标签路由 / 校验=挑一个 ≠ 实现者 / 审核=不同人优先 / 监督=沿用本次实现者 / 辩论=标签路由）。下拉里会出现五种不同说明的 `auto`，评审时要确认这个表达是否可接受，还是希望把各阶段语义统一。
3. **下拉用原生 `<select>` 演示。** 真实实现需要按 PRD §5 落 shadcn 的 `dropdown-menu`（仓库里暂无可直接复用的 select/table 组件）；回退顺序的拖拽排序在原型里用 ↑/↓ 按钮代替。
4. **Settings 只保留一个生命周期入口**：旧 Agent-only 矩阵从主页面移除；阶段 Agent、模型和推理深度只在专门设置页编辑，避免两套矩阵产生不同结果。
5. **仓库行「选中」行为未在原型里还原。** 真实产品点仓库行会切换当前仓库并重绘 PRD 画布；原型底图是静态截图，因此只把齿轮作为新增入口，点行本身不产生可见变化。

## 已知限制与尚未验证的生产行为

- 仅浅色主题（真实截图即浅色；frontend-public 的深色令牌未演示）。
- `GET/PUT /api/v1/agent-runner/lifecycle-agents` 与 PRD 覆盖写回 API 均为前端模拟，未接真实端点。
- `config.toml` / `.kedacode.toml` 的保留式写入、PRD 头部保留式写回、并发写入的真实实现未验证。
- 解析优先级链的真实合并行为以 PRD §7 Realistic Validation Plan 的 oracle 为准。
- 真实截图是 2026-09-18 从本机 `just run` 起的栈上采集的静态快照；产品界面变化后需要按上述 spec 重采。

原型层级标注：**interactive prototype**（概念交互验证），底图属于真实产品截图，但覆盖层是概念实现，不构成 E2E 或功能验收证据。

## Prototype Change Log

| File Path | Change Type | Before | After | Why |
|---|---|---|---|---|
| `docs/prototypes/lifecycle-agent-matrix.html` | Modify | Settings 与 Backlog 各有一套 Agent-only 生命周期矩阵编辑界面；回退链只编辑顺序与预算 | Settings 移除旧矩阵并换成统一页入口；Backlog 仓库齿轮进入同一页面并预选仓库；回退每行增加同 Agent 预设下拉与 TOML 预览 | 让全局与仓库生命周期值共用一张有效矩阵，并把回退 Agent 的模型选择展示给人评审 |
| `docs/prototypes/lifecycle-agent-matrix.md` | Modify | 说明仍描述旧 Settings 矩阵与 Backlog 仓库抽屉，回退预设只写作后续想法 | 更新为统一设置页唯一矩阵入口，记录回退预设交互、演示数据和待确认边界，并内嵌保存预览截图 | 评审时可直接看到回退预设状态并核对 PRD 提案 |
| `docs/prototypes/assets/prototype-hub.js` | Modify | lifecycle prototype v2.7 登记统一 Settings 与 lifecycle 页面 | v2.8 登记回退候选预设绑定与更新后的 Settings 预览 | Prototype Hub 是该原型的唯一登记入口 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-settings.png` | Modify | 旧截图仍显示 Settings 双 Tab 与 Agent-only 生命周期矩阵 | 重截为 Agent 标签、统一生命周期入口和带回退预设选择的当前 Settings 概览 | 删除误导性的旧画面，并直观展示新增回退预设能力 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-settings.source.md` | Modify | 来源记录只展示回退顺序与切换预算 | 增加三种匹配 Agent 预设均已绑定的 capture URL 与演示数据说明 | 让截图可复现并解释模型/effort 样例的范围 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-fallback-settings.png` | Add | 无回退预设选择与保存预览截图 | 添加三条回退候选分别绑定匹配预设及模型/effort 后的保存预览 | 让评审者直接看到每个候选独立选择预设及 TOML 映射 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-fallback-settings.source.md` | Add | 无截图来源记录 | 记录 capture URL、视口、状态、演示数据和验证层级 | 保留回退预设保存预览的复现方式及生产边界 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-lifecycle-settings.png` | Add | 无设置页缩略图 | 添加统一生命周期设置页真实浏览器 capture | Hub 目录需要能识别新主状态 |
| `docs/prototypes/assets/lifecycle-agent-matrix/preview-lifecycle-settings.source.md` | Add | 无来源侧车 | 记录 capture URL、视口、日期、层级和失效条件 | 保留浏览器截图来源与可复现信息 |
| `docs/prototypes/assets/lifecycle-agent-matrix/roadmap-real.source.md` | Modify | 记录历史 `/app/roadmap/` 和 2026-09-18 截图 | 记录现行 `/app/backlog/` 路由、历史截图与原型内 Backlog 标签覆盖层 | 文档对齐现行产品名，同时披露静态底图与覆盖层边界 |
| `docs/prototypes/index.md` | Modify | 两份旧草图标题显示 Roadmap | 目录标题改为 Backlog，链接文件名保持兼容 | 让原型索引使用现行产品术语 |
| `mkdocs.yml` | Modify | 导航标题有历史/不一致命名 | 原型目录标题统一使用 Backlog 与新生命周期页面名称 | 文档站导航与产品命名一致 |
