# 生命周期 Agent 矩阵 · 交互原型说明

本页归档 `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md` 的交互原型（v2.4，2026-09-18 重做）。原型用于在实施前确认三件事：三层配置各自的编辑落点长什么样、每层的"生效值与来源"怎么读、PRD 覆盖写回文件头部是什么形态。

## 打开方式

[打开交互原型](lifecycle-agent-matrix.html)（纯静态单文件，无构建步骤；也可从 [原型目录](hub.html) 进入）

## 三层落点（本原型要评审的核心结论）

| 层 | 配置文件 | 界面落点 | 原型里的画面 |
|---|---|---|---|
| 全局（机器级） | `config.toml`：agent 注册块标签、`[agent_runner.lifecycle_agents]`、`[agent_runner.runner]` | Settings 页新增 **「Agent 管理」粘性 Tab 区块** | 视图一 |
| 仓库级 | 该仓库 `.iar.toml` 的 `[agent_runner.lifecycle_agents]` | Roadmap「受管理仓库」每行右侧的 ⚙ | 视图二（抽屉） |
| PRD 级 | PRD 文件头部 `- lifecycle_agents:` bullet 块 | PRD 原文页工具栏「Agent 覆盖」 | 视图三（抽屉） |

仓库级配置**不放 Settings**：Settings 是机器级页面（会话信息 + 关于本机终端），仓库级配置跟着仓库走，入口挂在仓库列表上。

## Agent 管理：粘性 Tab 两页

Settings 页在真实的「设置」标题与用户副标题下方，覆盖一层新增的 **「Agent 管理」** 区块：标题 + 说明 + **粘性 Tab 栏**（覆盖层自身滚动，Tab 栏始终钉在区块顶部），下面依次是两个 Tab 面板，再往下是忠实复刻的「关于 iar 管理终端」卡片与「退出登录」按钮（滚动可见，说明页面会滚动、Tab 栏不动）。

| 顺序 | Tab | 内容 | 写入 |
|---|---|---|---|
| ① 默认 | **Agent 标签设置** | 四个 agent 各自的路由标签：GitHub 标签名 / 标签颜色 / 标签描述 | `config.toml` 里各自的 `[agent_runner.agents.<name>]` 段（`label` / `label_color` / `label_description`） |
| ② | **生命周期 Agent 设置** | 九行生命周期矩阵 + 「agent 回退顺序」卡片 | `[agent_runner.lifecycle_agents]` 段 / `[agent_runner.runner]` 段 |

- 「Agent 标签设置」说明 `auto` 的判定 = 运行时看 Issue 上挂着哪个 agent 标签，所以这张表就是 auto 的解析依据；并注明工作流标签（`agent/ready` 等，来自 `[agent_runner.labels]`）不在本表范围。
- 校验：标签名不能为空、也不能两个 agent 用同一个标签（会让 auto 无法判定）；非法时该行标红并阻断保存。演示按钮「两个 agent 用同一标签」把 kimi 的标签改成另一个 agent 正在用的值。
- 三个保存按钮各写 `config.toml` 的不同段，预览里都会说明"段内其余字段/键不变"。
- 改过的行/字段高亮；保存后基线更新，界面回到"与 config.toml 一致"。

## 原型形式：真实截图 + HTML 覆盖层（hybrid）

- **底图是真实 frontend-public 截图**，三张同尺寸（1440×1200 @2x）：Settings 页、Roadmap 依赖图、PRD 原文页。采集方式见各图的 `*.source.md` 旁车与 `tests/playwright-e2e/tests/workflows/prototype-screenshots.spec.ts`。不用图片生成模型截图——上一版 AI 生成的外壳图与真实产品的主题、导航、卡片结构都不一致。
- **覆盖层只绘制本 PRD 新增的区域**：Settings 页的「Agent 管理」区块（标题 + Tab + 两个面板）、仓库行齿轮、两个抽屉与 PRD 工具栏入口。其余像素全部来自真实产品。
- **画布固定 1440×1200 并按视口宽度等比缩放**，热点与覆盖层使用固定像素坐标（在真实页面上用 `getBoundingClientRect()` 实测），因此不会随视口漂移。
- **视图切换走真实侧栏导航**（截图上的 Settings / Roadmap 行），没有额外的原型标签栏，避免出现真实产品里不存在的页面结构。
- 新增区域统一带「本原型新增」标注；设计令牌（oklch 主题变量、shadcn Button/Card 类）取自 `frontend-public/app/globals.css` 与 `frontend-public/components/ui/*.tsx`。

## 下拉语义（三层共用）

- 下拉里**只有真实取值**：已注册 agent（codex / claude / kimi / pi）、该阶段的 `auto`、fix 与 closeout 的 `跟随实现（executor）`。没有"未设置""跟随全局"之类的伪选项。
- **`auto` 的文案按阶段如实描述**（各阶段语义本来就不同，见下表）；没有实现 `auto` 的阶段（决策 / 内容生成）不给这个选项。
- 下拉中**选中的就是当前生效值**，即优先级链上第一个被声明的值（本层声明 > 上一层声明 > 既有配置键 / 内置默认）；`executor` 不做二次解析，只在下方补一行"当前实现阶段：claude"。
- 右侧「当前值来源」列说明这个值来自哪一层（本机 `config.toml` / 本仓库 `.iar.toml` / 既有配置键 / 内置默认），并在本层已显式设置时给出「不写本键（跟随既有配置）」或「跟随全局（删除本键）」的恢复入口。
- **只有改动过的行会写进本层文件**：没动的行不在该层声明，继续沿回退链走；把下拉改回与继承值相同的取值，也会被视为"未改动"。保存预览逐行列明"写入 / 不写入并沿用谁"。
- 这样三层的关系是"越靠近 PRD 越优先"，而"继承"是**不动它**的默认状态，不需要在取值域里造一个假值。

| 阶段 | `auto` 的真实含义 | 代码落点 |
|---|---|---|
| 实现 | 按 Issue 上挂的 `agent/*` 标签路由 | `choose_agent`（`run_agent_once.py`） |
| 校验 | 从 `runner.agent_fallback_order` 里挑第一个 ≠ 实现者的 agent（独立性靠换模型） | `run_verifier_agent.py` 的 `_choose_verifier_agent` |
| 审核 | `allow_same_agent` 为真时沿用实现者；否则从注册表取第一个 ≠ 实现者的 | `run_agent_once.py` 的 `resolve_reviewer_agent` |
| 监督 | 发布路径沿用本次实现者；`iar review` 路径按标签路由 | `run_agent_once.py` 的 `resolve_supervisor_agent` |
| 辩论 | 按 `agent/deliberate` 标签路由 | 辩论队列 |
| 修复 / 收尾 / 决策 / 内容生成 | 无 `auto` 语义，不给该选项 | — |

## agent 回退顺序（生命周期 Agent 设置 Tab 里的第二张卡片）

矩阵只决定"这个阶段用谁"（单值）。**主 agent 失败或额度受限时换下一个**是另一套机制，入口在 Settings 页矩阵卡片下方：

- 编辑 `[agent_runner.runner]` 的 `agent_fallback_order`（默认 `["claude", "kimi", "codex"]`，本机 `config.toml` 没写、走代码默认）与 `max_agent_switches`（默认 `2`）。
- 交互：顺序可上下移动、可移除、可从剩余已注册 agent 里加到末尾；「最多切换」是数字输入，实时显示"最多尝试 N 个 agent"；保存展示 `[agent_runner.runner]` 段的写入预览（其余 runner 键不变）。
- 回退链只列"换谁"，**第一个尝试的 agent 由矩阵 / 标签路由决定**；本机未安装的 agent 会被运行时跳过（原型里对非注册表项标出"本机未安装，会被跳过"）。
- 卡片文案明确写了它的作用范围是 **Issue 执行阶段**的跨 agent 回退；校验阶段的 `auto` 会借用这条链来挑一个不同的 agent。

## 状态模型

```text
Settings · Agent 管理（默认视图，Tab 栏粘性）
→ 默认停在「Agent 标签设置」：四个 agent 的标签 / 颜色 / 描述
→ 改任一字段 → 该行高亮、保存点亮；把标签改名（如 claude → agent/cc）
→ 演示：两个 agent 用同一标签 → 冲突行标红 + 保存被阻断
→ 保存更改 → config.toml 各 agent 注册块的写入预览（段内其余字段不变）+ toast
→ 切到「生命周期 Agent 设置」→ 矩阵与回退顺序两张卡片
→ 滚动覆盖层到底 → Tab 栏保持钉住，「关于 iar 管理终端」卡片与「退出登录」按真实样式出现

Settings · 生命周期 Agent 矩阵（Tab ②）
→ 下拉显示九个生命周期当前生效的 agent，直接改成目标值 → 该行高亮为"已改"、来源列切成"已在本机 config.toml 显式设置"、保存点亮
→ 把某行改回与继承值相同的取值 → 视为未改动，恢复入口消失
→ 点「不写本键（跟随既有配置）」→ 该行回到继承（回落既有配置键）
→ 演示：写入未注册 agent → 该行变红 + fail-fast 说明 + 保存被阻断
→ 保存更改 → config.toml 写入预览（逐行列明写入 / 不写入）+ toast（各仓库 .iar.toml 不动）

Settings · agent 回退顺序（Tab ② 第二张卡片）
→ 上移 / 下移 / 移除某一项、从剩余 agent 里加到末尾 → 顺序变化、保存点亮
→ 改「最多切换」→ 摘要实时变成"最多尝试 N 个 agent"
→ 空列表 → 提示"回退链为空：主 agent 失败后不再换人"
→ 保存更改 → [agent_runner.runner] 段写入预览 + toast（其余 runner 键不变）

Roadmap · 受管理仓库列表（真实截图）
→ 点侧栏 Settings → 回全局矩阵
→ 点某行右侧 ⚙ → 打开该仓库的矩阵抽屉（默认选中该行仓库）
→ 抽屉里的下拉显示该仓库当前生效值（可能继承自全局层，来源列会标注）→ 改成目标值即在本仓库声明
→ 点「跟随全局（删除本键）」→ 该行回到继承
→ 演示：写入未注册 agent → 保存被阻断
→ 保存更改 → .iar.toml 写入预览 + toast；换一个仓库 ⚙ 打开，矩阵各自独立
→ 点 PRD 卡片（直开入口）→ PRD 原文画面

PRD 原文 · 覆盖抽屉
→ 点工具栏「Agent 覆盖」→ 抽屉打开，右侧实时预览将要写入的文件头部
→ 勾选生命周期 → 行内下拉解禁，默认值取当前继承值；继承值为 auto 的行要求显式选择 agent
→ 演示：文件里已有未注册 agent → 该行变红 + 写回被阻断
→ 写回 PRD 文件 → 头部 bullet 块变为"已写入"样式 + toast
→ 清除全部覆盖 / Esc / 点遮罩 → 回到继承
```

## 关键可点击对象与点击结果

- 侧栏 **Roadmap / Settings**：在三个画面之间切换（每个画面使用各自底图）。
- Roadmap 仓库行右侧 **⚙**：打开该仓库的仓库级矩阵抽屉；抽屉标题带仓库名，右上角 ✕ 与 Esc 可关闭。
- Roadmap 上**「生命周期 Agent 矩阵」PRD 卡片**：真实产品的直开入口，点击进入该 PRD 原文画面。
- Settings 页矩阵行 **agent 下拉**：已注册 agent（codex、claude、kimi、pi）、`auto（按 agent 标签路由）`，fix 与 closeout 另含 `跟随实现（executor）`；选中的是当前生效值，改成别的值即视为"本层显式声明"。「当前值来源」列说明它来自哪一层。
- 来源列的 **不写本键（跟随既有配置）** / **跟随全局（删除本键）**：只在本层已显式声明时出现，点它即恢复继承。
- 仓库抽屉矩阵行 **agent 下拉**：语义同上，作用范围是该仓库的 `.iar.toml`；来源列显示的值可能继承自全局层。
- **演示：写入未注册 agent**（Settings 与仓库抽屉各一个）：把审核行置为未注册的 `codebuddy`，展示"阶段开始前报错、不静默回落"的界面形态。真实下拉里不会出现未注册值，它模拟的是"文件被手工改坏"。
- Settings 页 **Tab 栏（Agent 标签设置 / 生命周期 Agent 设置）**：切换两个面板，Tab 栏在覆盖层滚动时保持钉在顶部。
- **Agent 标签设置**的 **标签名 / 颜色 / 描述输入**：改任一字段即视为"本行已改"；标签名空或与其它 agent 重复时该行标红并阻断保存。
- **演示：两个 agent 用同一标签**：把 kimi 的标签改成另一个 agent 正在用的值，展示冲突拦截。
- **保存更改**（Auto 标签、生命周期矩阵、agent 回退顺序三处各一个）：展示将写入 `config.toml` / `<仓库>/.iar.toml` 的内容，并逐行说明哪些行不写入、各自沿用谁。
- 回退顺序卡片 **↑ / ↓ / ✕ / ＋ 加到末尾 / 最多切换**：调整 `agent_fallback_order` 与 `max_agent_switches`；保存预览只动 `[agent_runner.runner]` 段。
- PRD 原文工具栏 **「Agent 覆盖」**：打开覆盖抽屉（右侧，带遮罩）。
- 覆盖抽屉 **勾选框 / 下拉 / 演示：文件里已有未注册 agent / 清除全部覆盖 / 写回 PRD 文件**：勾选后立即在右侧头部预览里新增 bullet 行。
- 底部「原型」Dock（评审工具层，非产品 UI）：**可点击区域开关**（默认开启）、**↺ 重置**（回到初始状态）、**返回 Hub**、**原型说明**。

## 演示数据说明

- 九个生命周期键名、中文名、取值来自 PRD §1 与 §10 的闭集；已注册 agent 取自 `config.toml` 的四个注册块（codex / claude / kimi / pi）。
- **Agent 标签设置**的四个标签取自真实 `config.toml`：`agent/codex`（#5319E7）、`agent/claude`（#BFDADC）、`agent/kimi`（#FF6B6B）、`agent/pi`（#7C3AED），描述也逐字来自各自的 `label_description`。
- 三个层级的初始值都是本机真实取值，来源列标注它来自哪一层：实现 `claude`（`runner.default_agent`）、校验 `auto`（`validation.verifier_agent` 缺省值）、审核 `auto`（`pre_pr_review.review_agent`）、监督 `auto`（`post_pr_supervisor.supervisor_agent`）、决策 `claude`（`interactive_decision.default_agent`）、内容生成 `claude`（`generated_content.default_agent`）、辩论 `auto`（`agent/deliberate` 标签路由）；fix / closeout 为内置默认 `executor`。
- 受管理仓库列表与真实 registry 一致（`repo_id` / `display_name`），行位坐标实测自真实页面。
- 保存与写回都是前端模拟，没有落到真实文件；PRD 原文与头部预览取自真实的 `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md`。

## 需要在实施前定的事

1. **矩阵是单值，回退顺序是另一张表。** 原型按"矩阵选主 agent + Settings 单独一节编辑 `runner.agent_fallback_order` / `max_agent_switches`"实现；如果之后想要"每个阶段一条独立顺序链"，配置格式要从单值改成数组，是破坏性变更。
2. **`auto` 的文案按阶段如实描述**（实现=标签路由 / 校验=挑一个 ≠ 实现者 / 审核=不同人优先 / 监督=沿用本次实现者 / 辩论=标签路由）。下拉里会出现五种不同说明的 `auto`，评审时要确认这个表达是否可接受，还是希望把各阶段语义统一。
3. **下拉用原生 `<select>` 演示。** 真实实现需要按 PRD §5 落 shadcn 的 `dropdown-menu`（仓库里暂无可直接复用的 select/table 组件）；回退顺序的拖拽排序在原型里用 ↑/↓ 按钮代替。
4. **「Agent 管理」粘性 Tab 是新增的页面组织**（原来 Settings 上只有"会话信息 + 关于 + 退出"）。两个 Tab 的面板高度会让页面滚动，原型里滚动的是覆盖层（底图是静态截图，无法真的滚页面），所以 Tab 栏钉在区块顶部而不是视口顶部。
5. **仓库行「选中」行为未在原型里还原。** 真实产品点仓库行会切换当前仓库并重绘 PRD 画布；原型底图是静态截图，因此只把齿轮作为新增入口，点行本身不产生可见变化。

## 已知限制与尚未验证的生产行为

- 仅浅色主题（真实截图即浅色；frontend-public 的深色令牌未演示）。
- `GET/PUT /api/v1/agent-runner/lifecycle-agents` 与 PRD 覆盖写回 API 均为前端模拟，未接真实端点。
- `config.toml` / `.iar.toml` 的保留式写入、PRD 头部保留式写回、并发写入的真实实现未验证。
- 解析优先级链的真实合并行为以 PRD §7 Realistic Validation Plan 的 oracle 为准。
- 真实截图是 2026-09-18 从本机 `just run` 起的栈上采集的静态快照；产品界面变化后需要按上述 spec 重采。

原型层级标注：**interactive prototype**（概念交互验证），底图属于真实产品截图，但覆盖层是概念实现，不构成 E2E 或功能验收证据。
