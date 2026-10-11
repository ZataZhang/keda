# 人工验收清单 · Console 交付进度信号（验收证据「计划 vs 产出」与影响树触达）

- PRD：`tasks/archive/P1-FEAT-20261010-093054-console-prd-progress-signals.md`（执行侧交付完成，runner 已归档），横幅 🧍 待人工验收（人工确认后由人翻 ✅）。
- Issue：#274 ｜ 分支 `issue-274`。
- 状态：**执行侧已交付**——后端 progress/evidence 端点与三个解析模块 + 前端三读数与证据分组全部落地，三项真实入口 oracle 全绿（rv-1 零 mock 浏览器 5 passed + 负控红证、rv-2 进程内 55 passed + 安全负控 15 passed + 跨进程 curl 29 条断言、rv-3 真实 git CLI 对照 9 passed），最终树 `just test all` 3859 passed / 1 skipped 全绿。**只剩你对下面 3 项表态**：2 个 Part A 决策 + §9.1 呈递过目。这 3 项就是 PRD §9 `Human-Confirmed` 组的 3 个空框。
- 打开本清单：`just prd review tasks/archive/P1-FEAT-20261010-093054-console-prd-progress-signals.md`（本清单为 Markdown 版；本次为无头执行环境的自动收尾轮，无法在真实浏览器自检交互 HTML，故不附交互版，内容以本文件为准）。
- 证据目录：`tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/`（截图为本地文件、按仓库 `.gitignore` 白名单不入库；`open` 命令在仓库根目录执行）。

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时一句「三项都同意」就够。**在你回复之前，这 3 个空框保持 `- [ ]`，执行工具不会代勾。**

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 运行中 PRD 的读数与证据改读执行工作区：唯一匹配时分支优先、主仓库兜底；主仓库归档副本是终态；发现失败或多重匹配显示不可用；工作区文件在页面带「未合并」标注 | §2 决定一 → §9 Human-Confirmed 第 1 项 | 这会把服务端可读边界扩大到未合并的工作区目录：口径收得太松，越界/逃逸读取面扩大；收得太紧或读错来源，运行中的真实进度看不见、归档终态又被遗留工作区冒充 |
| 2 | 三个读数（清单 / 影响树 / 证据）都是弱信号：文件归属 ≠ 验证通过、影响树触碰 ≠ 改对；确认无工作区/无基准时显示 `-`，读取失败单独显示「暂不可用」，不编造分母、不伪造零 | §2 决定二 → §9 Human-Confirmed 第 2 项 | 弱信号被读成「完成度/通过率」会直接误导验收判断：把「有文件」当成「验证通过」、把「无法测量」当成「零进度」 |
| 3 | §9.1 呈递区各项已亲眼看过：四态真实截图与三张 design-intent 目标图配对 + 10 秒自检结果 | §9.1 → §9 Human-Confirmed 第 3 项 | 前两项决策的实况依据就是这些真实入口截图；没看就确认，「验收」只剩机器背书 |

**术语先解释一句**

- *弱信号*：只帮助定位、不构成结论的读数；界面用 `~` 前缀、`?n` 与提示文案三层披露，不用进度条或完成色。
- *未合并*：文件来自执行工作区（分支），还没进主仓库；合并后来源自动回落主仓库。
- *影响树触达 `~触达/可判定`*：PRD §7.2 影响树计划要碰的文件中，分支实际碰了多少；无法判定是否可比较的节点以 `?n` 披露、不进分母。
- *rv-id*：PRD §7.6 验证计划的条目编号（rv-1…rv-3），证据文件按它命名。
- *design intent / real console verification*：前者是目标态设计图（意图），后者是真实 `kc console` 进程上跑出的页面截图（验证），两者配对呈递、层级不同不混用。
- *`[~]`*：PRD §9 里「等 runner 门禁/人工触点」的标记，不是「已完成」。

---

## 决定一 · 运行中读取口径：分支优先、主仓库兜底、归档终态

**你在拍板的事**：正在执行的 PRD，详情页读数与证据是否改读它的执行工作区（分支），以及未合并内容如何标注。

**PRD 原话（§2 决定一）**：

> **请确认：** 接受「唯一匹配时分支优先、主仓库兜底；主仓库归档副本为终态；发现失败或多重匹配时显示不可用」的读取口径，且工作区文件在页面上以「未合并」明确标注。

**证据（真实入口）**：

- 运行中态（`rv-1-running-detail.png`，下方内嵌）：证据分组里 rv-1、rv-3 的文件全部带「未合并」徽标，读数来自工作区。
- 归档终态（`rv-1-archived-detail.png`，下方内嵌）：主仓库已有归档副本时，即使遗留工作区仍在，页面也只读主仓库、不显示「未合并」。
- 端点层（rv-2 跨进程 curl，29 条断言全通过，转写见 `rv-2-live-console.txt`）：同名文件取工作区版本并标 `source=worktree`，主仓库独有文件标 `source=main`；worktree 查询失败/多重匹配时来源读数标不可用，不以主仓库空结果伪装。
- 安全边界（`rv-2-security-negative-control.txt`）：路径逃逸、隐藏文件、符号链接、超限、畸形 token 五类一律 4xx 且不泄露仓外内容，15 passed。
- 负控（`rv-1-negative-control-red.txt`）：在未实现本改动的 console 构建上跑同一 fixture，页面无读数、spec rc=1——证明该验证能判别缺失。

**复跑**：`evidence.json` item 1/2 的 command（`scripts/rv1_real_entry.sh`、`scripts/rv2_live_console.sh`），或在 worktree 内 `uv run pytest tests/test_backlog_prd_evidence.py`。

## 决定二 · 三读数口径与弱信号披露

**你在拍板的事**：头部「清单 / 影响树 / 证据」三个读数的取数口径，以及「不代表通过、不代表改对」如何向人披露。

**PRD 原话（§2 决定二）**：

> **请确认：** 接受上述三个读数的口径与弱信号披露方式（文件归属 ≠ 通过；影响树触碰 ≠ 改对；确认无工作区/无基准时用 `-`，读取不可用单独披露，不编造分母）。

**证据（真实入口）**：

- 运行中态截图读数与夹具逐字段一致：`清单 3/12`、`影响树 ~7/12 ?2`、`证据 2/3`、`计划 3 项 · 已产出 2 项（共 4 个文件）`；端点原文（`rv-1-running-progress-endpoint.json`）：`"checklist":{"checked":3,"total":12}`、`"impact":{"touched":7,"judgeable":12,"unresolvable":2}`、`"produced_planned_count":2,"file_count":4,"attribution_source":"manifest"`。
- 弱信号文案在真实页面上断言过：strip 内三个 chip 的 `title` 提示都含「弱信号」（spec:185-188，`rv-1-real-entry-green.txt` 用例 2）。
- 降级态不伪造数字（下方内嵌）：无计划旧 PRD 计划/产出显示 `-` 但文件数如实显示 2（`rv-1-degraded-no-plan.png`）；无 merge-base 时影响树显示 `-` 并说明无法比较，不显示 `~0/12`（`rv-1-no-base-detail.png`）。
- fresh-state 探针：在真实磁盘把工作区 PRD 第 3 项取消勾选后重进页面，读数 3/12 → 2/12 → 恢复后 3/12（spec:201-225），证明读数来自真实磁盘而非缓存。

**复跑**：`evidence.json` item 1 的 command（`scripts/rv1_real_entry.sh --negative`）。

## §9.1 呈递区 · 四态真实截图过目

**你在拍板的事**：以下四态页面是否如 §9.1 所述呈递、读数是否符合「10 秒自检」的预期值。

**呈递物（真实 `kc console` 进程 + 真实 git worktree fixture，零 mock）**：

| 状态 | 10 秒自检：精确看哪里、应看到什么 |
|---|---|
| 运行中 | 三读数 chip 精确为 `3/12`、`~7/12 ?2`、`2/3`；汇总行「计划 3 项 · 已产出 2 项（共 4 个文件）」；rv-1、rv-3 分组文件带「未合并」 |
| 已归档 | 即使存在遗留工作区，来源为主仓库、无「未合并」徽标、影响树读数 `-` |
| 无计划旧 PRD | 计划/产出为 `-`，文件汇总「共 2 个文件」，报告状态仅证据报告已产出 |
| 无基准 | 影响树为 `-` 并说明无法比较，不出现 `~0/12` |

**运行中**（real console verification，本地文件）：

![rv-1 运行中详情](rv-1-running-detail.png)

本地预览命令：`open "tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/rv-1-running-detail.png"`

**已归档**（real console verification，本地文件）：

![rv-1 已归档详情](rv-1-archived-detail.png)

本地预览命令：`open "tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/rv-1-archived-detail.png"`

**无计划降级**（real console verification，本地文件）：

![rv-1 无计划旧 PRD](rv-1-degraded-no-plan.png)

本地预览命令：`open "tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/rv-1-degraded-no-plan.png"`

**无基准**（real console verification，本地文件；该态没有独立目标图，复用降级目标图并在原型页记录原因）：

![rv-1 无基准详情](rv-1-no-base-detail.png)

本地预览命令：`open "tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/rv-1-no-base-detail.png"`

**配对目标图**（design intent，入库文件）：

![运行中目标图](../../../docs/prototypes/assets/roadmap-prd-controls-evidence-autopilot-progress-running.png)
![已归档目标图](../../../docs/prototypes/assets/roadmap-prd-controls-evidence-autopilot-progress-archived.png)
![降级目标图](../../../docs/prototypes/assets/roadmap-prd-controls-evidence-autopilot-progress-degraded.png)

本地预览命令：`open "docs/prototypes/assets/roadmap-prd-controls-evidence-autopilot-progress-running.png"`

---

*本清单是验收评审的呈递面，不是第二事实源：确认结果落在 PRD §9 与横幅上，本文件留作评审痕迹。*
