# 人工验收清单 · Console 网页版补齐 CLI 操作能力

- PRD：`tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`，横幅 🧍 待人工验收（归档归 runner）。
- Issue：#247 ｜ 分支 `issue-247`（worktree `/Users/zata/code/keda/.iar-worktrees/issue-247`）。
- 状态：**执行侧已交付 + 第 3 轮代码审查修复已落地（2026-10-09）**——九个 FR 全部落地，8 个 rv 脚本在最终代码树全部重跑（负例 RED、正例 GREEN），`CI=true just test all` 与 `just lint` 结论见 PRD §14「收尾修复」与「第 3 轮审查修复」条目；closeout 阶段已补采真实入口 UI 截图（见下方「呈递过目」）。**等你对下面 4 项表态**：3 个 Part A 决策 + §9.1 呈递过目。这 4 项就是 PRD §9 `Human-Confirmed` 组的 4 个空框。
- ⚠️ **表态前请先读「决定一」「决定二」两节新增的补充/修正呈递**：本轮审查发现这两项当初是在信息不完整的情况下呈递的（决定一漏说了「网页可写出状态机/签收标签」，决定二承诺了一个按构造不可成立的直出 PR 开关）。
- 证据报告：`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/P1-FEAT-20261008-165241-console-cli-parity-operations.evidence-report.md"`（开头「人审导航」节已内嵌 7 张真实入口截图，可直接过目）。
- 打开本清单：`just prd review tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`（本清单为 Markdown 版；无头执行环境无法在真实浏览器自检交互 HTML，故不附交互版，内容以本文件为准）。

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时一句"四项都同意"就够。**在你回复之前，这 4 个空框保持 `- [ ]`，执行工具不会代勾。**

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 标签编辑仅限仓库已同步的标准标签集，网页不创建新标签——**该集合按构造包含被自动化消费的工作流状态标签与 validation 签收标签**（见下「决定一」补充呈递） | §2 决定一 → §9 Human-Confirmed 第 1 项 | 开放任意标签写入等于把 GitHub 工作流纪律交给网页随手操作，一个拼错的标签就让 Issue 脱离监控口径；反过来收得太紧，日常需要的标签进出还得回终端。**补充代价**：白名单里的 `agent/running` / `agent/supervising` / `agent/failed` / `agent/waiting` 是在途与依赖判定依据（误打 `agent/running` 会让 daemon 跳过该 Issue 且入队永久 409），`validation/passed` / `validation/verifier-passed` 是验证门禁读取的签收信号，`agent/rework-prd` / `agent/deliberate` 写出即触发重写 PRD 与多 agent 合议——网页一次点击就能改变状态机或替人签收 |
| 2 | 快合上网页高级选项（默认折叠、默认关闭）；**直出 PR 已退回终端**——它对本入口按构造不可成立（见下「决定二」修正呈递） | §2 决定二 → §9 Human-Confirmed 第 2 项 | 放显眼处会养成随手勾选的习惯、降低交付安全阈值；完全不放则偶尔要走捷径时只能回 CLI。缺省路径已有逐字节一致性契约测试兜底。**修正代价**：原方案承诺的「网页勾选直出 PR 即走直发通道」在 CLI 规则下不可能达成（PRD-backed Issue 被硬性拒绝），现已改为发起端 400 拒绝 + 不暴露该开关；若你要网页也能出直发 PR，需要的是「对无 PRD 锚点的 Issue 发起启动」这个新入口，超出本 PRD 范围 |
| 3 | 「加入就绪」绝不触发 runner 启动，领取与否归 autopilot | §2 决定三 → §9 Human-Confirmed 第 3 项 | 若偷偷"顺手启动一次"，攒队列意图被静默破坏，且与 CLI 就绪语义分叉——这是本 PRD 最高优先的反例验收项 |
| 4 | §9.1 三项呈递物已亲手看过：dashboard 操作态、加入就绪、标签增删（真实浏览器手测 + 独立 `gh issue view` 交叉核对，截图存 `tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/`） | §9.1 → §9 Human-Confirmed 第 4 项 | 前三项决策的实况依据就是这三条真实入口手测；没看就确认，"验收"只剩机器背书。执行侧披露：closeout 已补采真实入口渲染截图（见证据报告「人审导航」节），会改动共享状态的动作手测（「跑一轮」进程页、线上 GitHub fresh read）仍待人工 |

**术语先解释一句**

- *标准标签集*：`kc labels sync` 同步到本仓库的那组标签（`agent/*` 队列与状态标签、`validation/*` 签收标签、`direct-pr` 档位标签、`type/*` 与 `status/*` 描述标签），是网页可编辑范围的白名单来源。**注意**：这是同步集合的全量，不只是「队列资格」标签——其中一半以上会被 daemon、依赖判定、验证门禁直接消费，网页写出即改变执行状态机或签收结论（详见「决定一」补充呈递）。
- *快合 / 直出 PR*：跳过合并前独立验证阶段 / 不经草稿 PR 复核直接开 PR 的两个 CLI 旗标。本 PRD 原计划把两者都搬进网页高级选项；审查后**只有快合留在网页**——直出 PR 对「PRD 建的 Issue」这条唯一入口被 CLI 硬性拒绝（PRD-backed Issue 必须过 PRD 交付门），勾上只会得到一次立刻用法错误退出的托管进程，因此该开关已撤下并改为发起端 400 拒绝。
- *autopilot*：runner 自动领取就绪 Issue 并执行的开关；「加入就绪」只发放队列资格，不碰这个开关。
- *fresh read*：操作后用独立查询（如 `gh issue view --json labels`）重读真实状态，而不是读本地缓存或前端乐观态。
- *负控 / 负例*：先证明"如果实现错了，这条检查会红"，再看它变绿——只有绿、无法证明会红的证据视为无效。
- *`[~]`*：PRD §9 里"等 runner 门禁/人工触点"的标记，不是"已完成"。

---

## 决定一 · 标签编辑的开放范围

**你在拍板的事**：网页 Issue 详情面板的标签增删，是否限定在仓库已同步的标准标签集内、网页不提供创建新标签。

> **⚠️ 补充呈递（2026-10-09 第 3 轮代码审查后新增，请先读这段再表态）**：上面那段 PRD 原话把范围描述成「`agent/*` 队列标签、优先级标签等工作流标签」，**说小了**。实际开放的是 `kc labels sync` 同步集合的**全量**，其中包含被自动化直接消费的状态与签收标签：
>
> | 标签 | 谁在读 | 误写的后果 |
> |---|---|---|
> | `agent/running` | claim / reclaim 与「加入就绪」冲突判定 | daemon 跳过该 Issue，且对它点「加入就绪」永久返回 409 |
> | `agent/supervising` / `agent/failed` / `agent/waiting` | PR 复核、失败标记、依赖判定 | 改变在途/等待判定，队列视图与依赖解锁随之出错 |
> | `agent/rework-prd` / `agent/deliberate` | PRD 重写、多 agent 合议触发 | 写出即触发一轮额外自动化工作 |
> | `direct-pr` | 发布档位选择 | 把该 Issue 推进直发档（跳过复核通道） |
> | `validation/pending` / `validation/passed` / `validation/verifier-passed` | 验证门禁与合并队列 | **等于在网页上替人 / verifier 签收验收证据** |
>
> 前端下拉对这些标签**不分级、点击即写入**，仅在候选菜单顶部加了一行提示。执行侧没有擅自收窄集合（收窄等于改需求），只做了两处披露：本段 + `docs/guides/agent-runner.md`「标签编辑的例外」。**请选 1 或 2**：1=按现方案执行并接受「网页可写出状态机/签收标签」；2=把集合收窄为「队列资格 + 描述类」，状态与签收标签仍留终端（需另开改动）。

**PRD 原话（§2 决定一）**：

> 建议只允许增删**仓库已同步的标准标签集**（`agent/*` 队列标签、优先级标签等工作流标签），网页不提供创建全新标签的能力。……代价是遇到确实需要新标签时仍要去终端执行一次 `kc labels sync`（低频，可接受）。
>
> **请确认：** 标签编辑是否按「仅限仓库已同步的标准标签集，不提供创建新标签」执行？

**证据（自动化层，`rv-4-issue-labels.txt`）**：负例——PUT 拼错的 `agent/redy` 被拒，文案指明不在已同步标准集并引导 `kc labels sync`，断言 GitHub 零变化（`edits=0`）；正例——pytest 7 例读写/增删/fresh-read/矛盾/拒绝留痕全绿。复跑：`bash tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/scripts/rv-4-issue-labels.sh`。**待你手测**：真实浏览器增删一个集合内标签 + `gh issue view` 交叉核对（§9.1 rv-4 截图）。

**同批修复（2026-10-09 审查带出，与本决定直接相关的两处）**：① 上一段所述「下拉不分级」目前只有提示文案，没有阻断——若你选 2（收窄集合），需要另开改动；② Issue 写端点过去只审计成功路径，与本文档「所有写操作（含被拒绝的）都会写入审计日志」的矛盾已修好：集合外标签、非法 Issue 编号、被拒的建 Issue 请求都落 `result="rejected"`（detail 含本次想写的标签/类型与原因），见 `test_put_issue_labels_out_of_set_rejected_422_and_audited`。本轮 5 项修复的完整清单见文末「第 3 轮代码审查修复清单」。

## 决定二 · 发布通道开关是否暴露到网页

**你在拍板的事**：快合与直出 PR 是否进网页高级选项（折叠、默认关闭），还是只暴露 agent/模型预设、通道开关留给 CLI。

> **⚠️ 修正呈递（2026-10-09 第 3 轮代码审查后新增）**：原方案里的**直出 PR 在本入口按构造不可成立**。backlog「开始此 PRD」启动的是 `kc run --issue <N>`，而 N 就是这份 PRD 建的 Issue，正文一定带 PRD 锚点；CLI 对「PRD-backed Issue + `--direct-pr`」是硬性 USAGE 拒绝（`PRD-backed Issue must pass the PRD delivery gate and archive its PRD`）。修好的实现里勾上它的后果是：前端 toast「已开始」、进程页留一条立刻以用法错误退出的托管进程、永远出不了 PR——§1 行为样例与下面的验收当时按「能走通」写，属呈递错误。
>
> **已按您的保守档收敛**：高级选项只留 **快合 + agent + 模型预设**；直出 PR 撤下开关，请求体字段保留但在改动 Issue 之前就返回 400 并给出 CLI 同款原因（不静默降级、GitHub 与进程零残留）。快合对 PRD-backed Issue 是 CLI 允许的组合，行为不变。若你希望网页也能出直发 PR，需要的是「对无 PRD 锚点的 Issue 发起启动」这个新入口，超出本 PRD 范围。

**PRD 原话（§2 决定二）**：

> 建议把快合……和直出 PR……这两个「跳过流程闸门」的开关暴露到「开始此 PRD」的高级选项里，但**默认折叠且默认关闭**。……若您倾向于更保守，可以只暴露 agent/模型预设选择，把这两个通道开关留给 CLI。
>
> **请确认：** 快合是否上网页（高级选项内、默认关闭）？直出 PR 已按 CLI 目标域规则退回终端。

**证据（自动化层，`rv-5-start-contract.txt`）**：缺省请求（不展开高级选项）与改动前 argv **逐字节一致**（契约测试，不放宽）；带选项请求中 `--fast-merge` 确实进入 argv（identity 负例按预期失败）；**新增**——`direct_pr=True` 的正例折算断言（`("--direct-pr",)`，补齐首轮只记互斥负例的证据缺口）、`direct_pr` 配 PRD 锚点目标的**负控**（断言「也能启动」必须 RED；实测被拒且 `github calls = [] / spawns = []`）、HTTP 边界 400 断言、队列级 --all-ready 拒跳过闸门旗标。pytest 28 例全绿。复跑：`bash …/scripts/rv-5-start-contract.sh`。附加披露：为实现预设候选与后端同源，超出 §7 改动树新增了只读端点 `launch-options`（§14 第二条 Change Log，随本决定一并呈阅）。

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

**背景**：closeout（2026-10-08）用无头 Chrome 驱动真实 `uv run kc console` 入口补采 7 张渲染层截图，全部内嵌于证据报告「人审导航」节（rv-1 两张、rv-3 backlog 入口、rv-4 标签编辑、rv-7 建Issue 对话框、rv-8 登录页与 404）；捕获脚本只读交互、对共享 GitHub 状态零写入。**第 3 轮审查修复改了标签候选下拉与启动高级选项抽屉，7 张截图已于 2026-10-09 在重建后的最终代码树全部重采**——`rv-4-issue-labels.png` 现在能看到下拉顶部的知情提示。**仍留你手测的部分**：点「跑一轮」后进程页出现新托管进程记录、真实入队/标签写回后用 `gh issue view --json labels` 交叉核对。另请注意 rv-3 的如实披露：「加入就绪」按钮仅渲染于 `not_started` 态 PRD，当前受管仓库无该态 PRD，故截图未见该按钮；其写路径由自动化层证据（`rv-3-enqueue-ready.txt` + 冲突负控）覆盖。手测入口：`uv run kc console` 启动后浏览器操作 dashboard/backlog；补截到上表路径后即可勾第 4 项。

---

## 第 3 轮代码审查修复清单（2026-10-09，不需要你逐项表态，但影响上面两项决策的读法）

| # | 审查发现 | 修了什么 | 证据 |
|---|---|---|---|
| 1 | 高级选项里的「直出 PR」在唯一入口上必然被 CLI 拒绝（PRD-backed 目标），勾上只会留下一条立刻用法错误退出的托管进程 | `start_prd` 在改动 GitHub 之前按 CLI 同款理由拒绝（400 + 审计），前端撤下该开关并就地说明；`--all-ready` 与跳过闸门旗标的同类互斥也补进 argv 构建端 | `rv-5-start-contract.txt`：新增 `direct_pr` 正例折算 + PRD 锚点负控（`github calls = [] / spawns = []`）+ HTTP 400 用例，绿步 28 例 |
| 2 | 标签白名单含被自动化消费的状态机与签收标签，决定一呈递时没说全 | 补进 PRD 决定一、本清单、`docs/guides/agent-runner.md`「标签编辑的例外」与 core 用例 docstring；网页下拉顶部加知情提示。**未擅自收窄集合** | 见「决定一」补充呈递；`rv-4-issue-labels.png`（重采后可见提示行） |
| 3 | Issue 写端点只审计成功路径，与文档「含被拒绝的」矛盾 | 被拒尝试也落 `result="rejected"`（含本次想写的标签/类型与原因） | `test_put_issue_labels_out_of_set_rejected_422_and_audited`、`test_put_issue_labels_rejects_non_positive_issue_number`、`rv-4` 绿步 7 例 |
| 4 | 网页建 Issue 的 `issue_type` 无服务端校验，可把任意字符串当标签名交给 gh | 约束在该仓库已同步的 `type/*` 生效名内，越界在调用用例之前 400（GitHub 零调用） | `rv-7-from-prompt.txt` 新增 `issue_type` 负控 + 37 例绿步 |
| 5 | `is_default()` 与 `cli_flags()` 对 `agent="auto"` 判定不一致 | 两者改用同一份取值判定 | `test_runner_launch_options_is_default_agrees_with_cli_flags`（参数化 10 组） |

全量门禁与逐条实现细节见 PRD §14「第 3 轮 pre-PR 代码审查修复」条目。

## 第 4 轮代码审查修复清单（2026-10-09，同样不需要你逐项表态）

| # | 审查发现 | 修了什么 | 证据 |
|---|---|---|---|
| 1 | Issue 详情端点不带仓库限定，撞号时面板会把**另一个仓库**的监控详情绑到点击的仓库上（标签与动作却发往点击的仓库） | `GET /api/v1/agent-runner/issues/{n}` 新增可选 `repo_id` 作用域（给出时只查该仓库、未知仓库 404 并点名；不带参数保留旧的跨仓库行为，CLI 与其它消费方零变化），dashboard 始终带上点击的仓库 | `test_api_issue_detail_scopes_lookup_to_repo_id`（scoped 只查该仓库 / unscoped 保留旧行为 / 未知仓库零查询）+ `rv-1-issue-detail-repo-scope.txt`（真实 HTTP 两条负控：未知仓库 404 点名、`#245` 改问 `freshai` 不再拿到 keda 的快照）+ `rv-1-issue-detail-repo-scope-ui.txt`（真实浏览器发出 `issues/245?repo_id=keda`） |
| 2 | 新增的端点表里三行路径在路由器里不存在，操作员照文档调会 404 | 三行改为真实路径（`…/console/repositories/{repo_id}/actions`、`…/console/repositories/{repo_id}/issues/{issue_number}/actions`、`/api/v1/agent-runner/backlog/prds/{encoded_path}/enqueue-ready`），并把「复用 backlog start 端点」那行也写成显式路径；全表 9 行逐行对过路由声明 | `docs/guides/agent-runner.md`「网页操作入口与对应端点」表；`uv run mkdocs build --strict` 通过 |
| 3 | 证据脚本绿步不 fail-loud；截图脚本硬等一个只渲染于 `not_started` 的按钮 | `rv-3`/`rv-6` 补 `exit 1` 门禁（`rv-4`/`rv-7` 此前已有，审查把四份都点了名）；`capture_ui_screenshots.mjs` 的硬等待已在第 3 轮改为容忍缺席，本轮再加浏览器侧详情请求记录；`evidence.json` 的绿步用例数与 `[green-exit=0]` 断言按实况校正（原记 `6/12/34` 与实跑 `7/28/37` 不符，会让复跑门禁误判） | 10 个 rv 脚本在第 4 轮最终代码树全部 exit 0；`rv-*.txt`、`rv-capture-ui.txt` 与新增 `rv-1-issue-detail-repo-scope*.txt` 重采 |
| 4 | `system-design.md` 仍列 public 域的 `/auth/register` 与「开放自助注册」，而本 PR 的 FR-9 正是删掉那个必然失败的注册页 | 该行改为「login/logout/me，无 register」，并在表下写明上表是目标态、实际只有 `local_auth.py` 的 no-op local operator 会话，同时指向 console 的信任边界小节 | `docs/architecture/system-design.md`「认证与会话域」；`uv run mkdocs build --strict` 通过 |

全量门禁与逐条实现细节见 PRD §14「第 4 轮 pre-PR 代码审查修复」条目。
