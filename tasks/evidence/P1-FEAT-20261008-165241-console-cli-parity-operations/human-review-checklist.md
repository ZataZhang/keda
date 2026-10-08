# 人工验收清单 · Console 网页版补齐 CLI 操作能力

- PRD：`tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`，横幅 🧍 待人工验收（归档归 runner）。
- Issue：#247 ｜ 分支 `issue-247`（worktree `/Users/zata/code/keda/.iar-worktrees/issue-247`）。
- 状态：**执行侧已交付**——九个 FR 全部落地，8 个 rv 脚本在最终代码树全部重跑（负例 RED、正例 GREEN），`CI=true just test all` 与 `just lint` 结论见 PRD §14「收尾修复」条目；closeout 阶段已补采真实入口 UI 截图（见下方「呈递过目」）。**等你对下面 4 项表态**：3 个 Part A 决策 + §9.1 呈递过目。这 4 项就是 PRD §9 `Human-Confirmed` 组的 4 个空框。
- 证据报告：`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/P1-FEAT-20261008-165241-console-cli-parity-operations.evidence-report.md"`（开头「人审导航」节已内嵌 7 张真实入口截图，可直接过目）。
- 打开本清单：`just prd review tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`（本清单为 Markdown 版；无头执行环境无法在真实浏览器自检交互 HTML，故不附交互版，内容以本文件为准）。

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时一句"四项都同意"就够。**在你回复之前，这 4 个空框保持 `- [ ]`，执行工具不会代勾。**

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 标签编辑仅限仓库已同步的标准标签集，网页不创建新标签 | §2 决定一 → §9 Human-Confirmed 第 1 项 | 开放任意标签写入等于把 GitHub 工作流纪律交给网页随手操作，一个拼错的标签就让 Issue 脱离监控口径；反过来收得太紧，日常需要的标签进出还得回终端 |
| 2 | 快合/直出 PR 两个跳过闸门的开关上网页高级选项，默认折叠默认关闭 | §2 决定二 → §9 Human-Confirmed 第 2 项 | 放显眼处会养成随手勾选的习惯、降低交付安全阈值；完全不放则偶尔要走捷径时只能回 CLI。缺省路径已有逐字节一致性契约测试兜底 |
| 3 | 「加入就绪」绝不触发 runner 启动，领取与否归 autopilot | §2 决定三 → §9 Human-Confirmed 第 3 项 | 若偷偷"顺手启动一次"，攒队列意图被静默破坏，且与 CLI 就绪语义分叉——这是本 PRD 最高优先的反例验收项 |
| 4 | §9.1 三项呈递物已亲手看过：dashboard 操作态、加入就绪、标签增删（真实浏览器手测 + 独立 `gh issue view` 交叉核对，截图存 `tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/`） | §9.1 → §9 Human-Confirmed 第 4 项 | 前三项决策的实况依据就是这三条真实入口手测；没看就确认，"验收"只剩机器背书。执行侧披露：closeout 已补采真实入口渲染截图（见证据报告「人审导航」节），会改动共享状态的动作手测（「跑一轮」进程页、线上 GitHub fresh read）仍待人工 |

**术语先解释一句**

- *标准标签集*：`kc labels sync` 同步到本仓库的那组标签（`agent/*` 队列标签、优先级标签等），是网页可编辑范围的白名单来源。
- *快合 / 直出 PR*：跳过合并前独立验证阶段 / 不经草稿 PR 复核直接开 PR 的两个 CLI 旗标，本 PRD 把它们搬进网页高级选项。
- *autopilot*：runner 自动领取就绪 Issue 并执行的开关；「加入就绪」只发放队列资格，不碰这个开关。
- *fresh read*：操作后用独立查询（如 `gh issue view --json labels`）重读真实状态，而不是读本地缓存或前端乐观态。
- *负控 / 负例*：先证明"如果实现错了，这条检查会红"，再看它变绿——只有绿、无法证明会红的证据视为无效。
- *`[~]`*：PRD §9 里"等 runner 门禁/人工触点"的标记，不是"已完成"。

---

## 决定一 · 标签编辑的开放范围

**你在拍板的事**：网页 Issue 详情面板的标签增删，是否限定在仓库已同步的标准标签集内、网页不提供创建新标签。

**PRD 原话（§2 决定一）**：

> 建议只允许增删**仓库已同步的标准标签集**（`agent/*` 队列标签、优先级标签等工作流标签），网页不提供创建全新标签的能力。……代价是遇到确实需要新标签时仍要去终端执行一次 `kc labels sync`（低频，可接受）。
>
> **请确认：** 标签编辑是否按「仅限仓库已同步的标准标签集，不提供创建新标签」执行？

**证据（自动化层，`rv-4-issue-labels.txt`）**：负例——PUT 拼错的 `agent/redy` 被拒，文案指明不在已同步标准集并引导 `kc labels sync`，断言 GitHub 零变化（`edits=0`）；正例——pytest 6 例读写/增删/fresh-read 全绿。复跑：`bash tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/scripts/rv-4-issue-labels.sh`。**待你手测**：真实浏览器增删一个集合内标签 + `gh issue view` 交叉核对（§9.1 rv-4 截图）。

## 决定二 · 发布通道开关是否暴露到网页

**你在拍板的事**：快合与直出 PR 是否进网页高级选项（折叠、默认关闭），还是只暴露 agent/模型预设、通道开关留给 CLI。

**PRD 原话（§2 决定二）**：

> 建议把快合……和直出 PR……这两个「跳过流程闸门」的开关暴露到「开始此 PRD」的高级选项里，但**默认折叠且默认关闭**。……若您倾向于更保守，可以只暴露 agent/模型预设选择，把这两个通道开关留给 CLI。
>
> **请确认：** 快合与直出 PR 是否上网页（高级选项内、默认关闭）？还是仅暴露 agent/模型预设？

**证据（自动化层，`rv-5-start-contract.txt`）**：缺省请求（不展开高级选项）与改动前 argv **逐字节一致**（契约测试，不放宽）；带选项请求中 `--fast-merge` 确实进入 argv（identity 负例按预期失败）。复跑：`bash …/scripts/rv-5-start-contract.sh`。附加披露：为实现预设候选与后端同源，超出 §7 改动树新增了只读端点 `launch-options`（§14 第二条 Change Log，随本决定一并呈阅）。

## 决定三 · 「加入就绪」的启动语义

**你在拍板的事**：「加入就绪」是否严格 = 建 Issue（若无）+ 打就绪标签，**绝不启动** runner。

**PRD 原话（§2 决定三）**：

> 建议「加入就绪」严格定义为「建 Issue（若无）+ 打就绪标签，**不启动** runner」，是否被自动领取完全交给仓库现有的 autopilot 开关决定——这与 CLI 侧的就绪语义一致……。
>
> **请确认：** 「加入就绪」是否按上述语义执行（绝不触发 runner 启动）？

**证据（自动化层，`rv-3-enqueue-ready.txt`）**：core 编排 `enqueue_prd_ready` 无 supervisor 形参（结构性不启动）；pytest 4 例——建 Issue+打标签不启动、幂等、对 `agent/running` 的 Issue #21 返回冲突且不改写状态（负例 RED 确认有牙齿）。复跑：`bash …/scripts/rv-3-enqueue-ready.sh`。**待你手测**：对 pending PRD 点「加入就绪」后进程页无新 runner 记录 + `gh issue view --json labels`（§9.1 rv-3 截图）。

## 呈递过目 · §9.1 三项真实入口手测

**你在确认的事**：rv-1（dashboard 一键执行 + 进程页 + 「全部」视图）、rv-3、rv-4 三条已由你在真实浏览器走完并截图，与 §9.1 描述一致。

**PRD 原话（§9.1 呈递表）**：三行分别要求 `rv-1-dashboard-actions.png`、`rv-3-enqueue-ready.png`、`rv-4-issue-labels.png`，10 秒自检为"进程页出现新记录且状态非幽灵 / 进程页无新 runner 记录 / 页面徽章与 gh 查询一致"。

**背景**：closeout（2026-10-08）已用无头 Chrome 驱动真实 `uv run kc console` 入口补采 7 张渲染层截图，全部内嵌于证据报告「人审导航」节（rv-1 两张、rv-3 backlog 入口、rv-4 标签编辑、rv-7 建Issue 对话框、rv-8 登录页与 404）；捕获脚本只读交互、对共享 GitHub 状态零写入。**仍留你手测的部分**：点「跑一轮」后进程页出现新托管进程记录、真实入队/标签写回后用 `gh issue view --json labels` 交叉核对。另请注意 rv-3 的如实披露：「加入就绪」按钮仅渲染于 `not_started` 态 PRD，当前受管仓库无该态 PRD，故截图未见该按钮；其写路径由自动化层证据（`rv-3-enqueue-ready.txt` + 冲突负控）覆盖。手测入口：`uv run kc console` 启动后浏览器操作 dashboard/backlog；补截到上表路径后即可勾第 4 项。
