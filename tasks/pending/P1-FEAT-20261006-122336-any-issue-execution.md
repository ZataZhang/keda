# PRD: 任意 Issue 可执行（Issue-first 交付入口）

- GitHub Issue: https://github.com/ZataZhang/keda/issues/215

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文分两层：Part A 是给人审的行为与决策层，不含实现机制、文件路径与命令；Part B 是给执行器的实现层。两层都是投影，`§8` 与 `§9` 才是对应事实源。

## Feature Overview (功能一览)

以下条目是 §10 Functional Requirements 的投影，行为验收请看 §1 的行为样例表。

- **`iar run` 对任意 Issue 通用**（FR-1）：不管 Issue 是人手写的、agent 建的、带不带 PRD 链接、有没有 Machine Contract 结构，只要带就绪标记（守护进程路径）或被人显式点名（`--issue N` / PRD 路径）就能被领取并端到端执行出 Draft PR。
- **撤销「无 PRD 不得执行」的产品边界**（FR-2）：仓库路线图现在明文禁止把无 PRD 的 Issue 当作可执行任务；本次把这条从「禁止」改为「允许，由加就绪标记或显式点名的人决定」。
- **一句话创建 Issue**（FR-3）：`iar issue create` 多一种输入方式——直接给一段自然语言，agent 把它写成自足的 Issue；这是「任意 Issue」的一个来源，不是本次的主题。
- **创建时就地决定要不要立刻进队列**（FR-5）：`--ready` 立刻打上就绪标记，**任何正在轮询该标记的守护进程**（本机或别的机器上的 keda / 其他 agent）都可以领，**先到先得**；`--no-ready`（默认）则**不进队列 —— 谁的守护进程都不会领它**，直到有人后续手动加上该标记。**系统中没有「指派给某台机器」的机制**：由哪台机器执行，取决于哪台在为该仓库跑守护进程、以及谁先轮询到。
- **想「就我这台机器跑、别被抢」时走显式定向**（FR-6 / FR-21 / FR-23）：就绪标记只约束**守护进程的自主挑选** —— **不打标记、直接 `iar run --issue N`**，因无标记而不与任何守护进程竞争；同目标的排他由首次领取的真 CAS 保证。
- **本机守护进程在跑时也能手动跑 Issue**（FR-24）：`iar run` 与正在运行的本机守护进程**不再互斥** —— 现状是只要本机守护进程在跑，任何 `iar run` 都被拒绝（要求 `--takeover` 先停掉守护进程），这让上一条的「零竞争路径」实际走不通。本次把互斥收窄为**只挡队列轮询**（`--all-ready` / 无显式 target）：显式单目标与守护进程共存，各跑各的 Issue（工作树按 Issue 隔离）。对守护进程**当前正持有**的 Issue 显式 run 仍会被拒 —— 依据认领状态而非 mutex。
- **`--direct-pr`：agent 干完就出 PR**（FR-14 ~ FR-19）：在 **`iar run`** 上（不在 `iar issue create` 上 —— 前者管执行与发布，后者只管创建 Issue）新增一个 per-run 档位：执行 agent 结束后**只保留机械步骤**（runner 提交 → push → 建 Draft PR），中间不再有任何别的 agent 调用、也不再跑 runner 的验证命令。让 PR 上的 CI 成为门禁。
- **既有档位与既有 PRD 路径逐字不变**（FR-7 / FR-19）：`--fast-merge` 的行为与它的测试一条不动；继续用 PRD 文件建 Issue、继续跑 PRD-backed Issue 的行为与门禁一条都不变。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

使用者的项目定位是 **Issue 为真相源**：需求写在 Issue 里，任何一台装了 keda 的机器都可以来领。但路线图现在的产品边界与这个定位相反 —— 它把「没有 PRD 的 Issue」定义为**只能进澄清流程、不得被当作可执行任务领取**：

> 用户可以直接提交没有 PRD 的 Issue，但这类 Issue **只能进入需求澄清、合议和 PRD 审批流程**；在管理员确认前**不得被 runner 当作可执行代码任务领取**。（路线图 Product Boundary）

配套的 Target Workflow 更进一步：无 PRD 的 Issue「**只允许进入需求澄清和合议流程，不允许直接执行代码任务**」；M8 里程碑整节都在描述「无 PRD → 澄清 → 合议 → 生成 PRD → 才 ready」这条强制链。

**但代码里没有这条限制。** 实测核查：

- 全部 PRD 相关门禁都是「有 PRD 锚点才生效，没有就放行」——PRD 交付门、PR 正文契约、证据门禁、最终复核各自提前返回；
- 执行提示词对无 PRD 的 Issue 泛化为一句话提示；
- **独立 verifier 直接从 Issue body 取意图**（`issue.body.strip()`），oracle 为空时降级为「无结构化 oracle 项」；
- 证据目录兜底到按 Issue 号命名的目录；账本 `prd_path` 写空串；
- 全仓检索 `intake` / `needs-prd` / `requires_prd` **零命中** —— 没有任何代码在阻止无 PRD 的 Issue 被执行。

也就是说：**运行器本来就已经通用了，只是文档在禁止它。** 这是一个政策与实现脱节的状态：文档描述了强制的 PRD 前置链，代码里根本不存在这条链。

后果是使用者拿不到他想要的东西：「改个文案」这种事，按理说写一句 Issue 就该能跑，实际却要先补一份带 Machine Contract 的 PRD 才「合法」；而运行器明明不需要它。另外，最近一次真实验收（一个 360 行改动的小需求）端到端花了 47 分钟，其中约 22 次工具调用用于反复自证一份交互式 HTML 审查清单的按钮可用 —— 对这类小需求，整套 PRD 门禁的性价比是负的。

还有一处必须披露的既有事实：**「无 PRD 的 Issue 能跑」目前只有代码阅读与单元测试支撑，从未在真实生产 run 上验证过。** 账本里 29 条 `prd_path` 为空的 run 全部来自测试夹具（`rv1-fake` / `harness#1` / `rv206#1`），没有一条是真实运行；仓库当前 2 个 open Issue 也都带 PRD 锚点。所以本次的首要交付不是写代码，而是**把这条路径真正跑通并留下真实证据**。

**另一个被误判的点：`--fast-merge` 并不等于「agent 干完就出 PR」。** 实测读代码后确认，在「无 PRD 的 Issue + `--fast-merge`」下，执行 agent 结束与 Draft PR 创建之间**仍有 4 段**：

| 环节 | 代价 |
|---|---|
| Phase 2 runner 验证命令（`runner.verification_commands`） | 失败会**回到 agent 修复**（又一次 agent 调用） |
| Phase 3 PRD 交付门 | 无 PRD 锚点 → 零成本 |
| Phase 3.5 证据门禁 | 无验收段 → 零成本 |
| Phase 4 commit proxy + `runner.pre_commit_verification_command` | 失败也会**回到 agent 修复** |
| **pre-PR review** | **另起一个 reviewer agent**（`pre_pr_review.enabled` 默认 `True`，最多 2 轮修复，单轮超时 1800s），且**它自己能改代码并再次 push** |

也就是说：`--fast-merge` 旁路的是 Phase 4.5 的独立验证（重跑 RV + 换 agent 复核），但它**没有**动 pre-PR review，也**没有**动 runner 的两处验证命令 —— 而这两处都会把你打回 agent。因此「快速提交」的真实含义需要用一个新的档位来表达，而不是指望 `--fast-merge`。

现有文档完全没有覆盖这一段：`--fast-merge` 的 CLI help 与文档只说它跳过「rv re-exec + independent verifier」，从没提 pre-PR review 仍在链路上。

### Interpretation (解读回显)

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 手工建一个 Issue：标题一句话，正文是自由文本（**不带** `PRD path:` 锚点、不带验收清单、不带 Machine Contract 结构），给它打上就绪标记，然后真实执行一次 | 端到端跑通并产出 Draft PR；不因为「没有 PRD」在任何环节中止或报错；Issue 正文被当作需求来源 |
| 👀 人审 | 跑 `iar issue create --from-prompt "把 Stats 页 Token 表的总量列右对齐"` | 生成一个 GitHub Issue：标题概括这句话，正文是自足的需求描述，**不含** `PRD path:` / `Canonical PRD` 指针；`tasks/pending/` 下不新增任何文件 |
| 👀 人审 | 建 Issue 时**不加**就绪标记（默认），同时有本机与另一台机器的守护进程在跑 | **两边都不领它**；它一直停在未就绪状态，直到有人手动加上就绪标记 |
| 👀 人审 | 建 Issue 时**加上**就绪标记，只有本机守护进程在为该仓库跑 | 本机领走它，认领标记里记录的是本机 host 与 PID |
| 👀 人审 | 建 Issue 时**加上**就绪标记，但本机不为该仓库跑守护进程、另一台机器的守护进程在跑 | 另一台机器领走它，认领标记记录的是那台机器的 host 与 PID（**这是部署事实决定的，不是 Issue 上的某个「机器」字段**） |
| 🤖 自动验证 | 对一个无 PRD 锚点、正文无验收要求的 Issue，检查其执行时的证据要求 | 该 Issue 不要求证据文件（含前端改动也不要求截图）；显式要求验收的 Issue 则正常要求 |
| 👀 人审 + 自动验证 | 对一个无 PRD 的 Issue 跑 `iar run --issue N --direct-pr` | 执行 agent 结束后**只发生机械动作**：runner 提交 → push → 建 Draft PR。**不再出现第二个 agent**（无 pre-PR review），也不运行 runner 的验证命令；PR 正文带机器可读的档位标注 |
| 🤖 自动验证 | 用 `--direct-pr` 跑一个**故意让仓库测试失败**的改动 | 仍然产出 Draft PR（不因测试失败而中止）；PR 上的 CI 变红 —— 门禁从 runner 侧转移到 CI |
| 🤖 自动验证 | 对一个**带 PRD 锚点**的 Issue 跑 `--direct-pr` | 被拒绝（usage error），错误信息指向改用 `--fast-merge`；理由是 PRD-backed Issue 需要走 PRD 交付门并归档，不能被旁路 |
| 🤖 自动验证 | `--direct-pr` 与 `--fast-merge` 同时给出 | 被拒绝（usage error），而不是静默取更强者 |
| 🤖 自动验证 | 对一个**没有就绪标记**的 Issue 显式跑 `iar run --issue N` | **直接执行它** —— 显式定向是人的决定，不受就绪标记约束；另一台机器的守护进程不会参与，因为该 Issue 没有就绪标记（**这就是「我要自己跑」的路径，零竞争**） |
| 🤖 自动验证 | **本机守护进程正在为该仓库跑**，同时对**另一个** Issue 显式跑 `iar run --issue N` | **不再拒绝**（现状会 CONFLICT 并要求 `--takeover`）；本次定向 run 与守护进程**共存**，各跑各的 Issue、各自的工作树，互不干扰 |
| 🤖 自动验证 | 本机守护进程正在跑，显式 `iar run --issue N` 指向守护进程**当前正持有的那个 Issue** | 拒绝并给 CONFLICT，错误信息指出当前持有者（host/PID）；**不发生双跑**。判定依据是认领状态/CAS，**不再是 daemon mutex** |
| 🤖 自动验证 | 本机守护进程正在跑，显式 `iar run --all-ready`（或等价的无 target 队列轮询） | **仍拒绝**（CONFLICT）—— 只有队列轮询才会与守护进程重复领取同一批 Issue，这条互斥保留 |
| 🤖 自动验证 | 显式 `iar run --issue N` 打到一个**已被另一台机器活跃认领**（`agent/running` 且认领者存活）的 Issue | 拒绝并给非零退出码（CONFLICT），错误信息指出当前认领者（host/PID）；**不发生双跑** |
| 🤖 自动验证 | 两个进程**同时**领取同一张已就绪的 Issue | 只有一个真正执行；另一个检测到自己不是最早认领者并主动退出（CAS），**不发生双跑** |
| 🤖 自动验证 | 显式 `iar run --issue N` 打到一个 `agent/blocked` 且无解除标记的 Issue | 拒绝并提示用 `iar blocked-continue`（**不静默返回 0**） |
| 🤖 自动验证 | 守护进程轮询、或 `--all-ready` 在队列为空时 | 仍**静默继续**（退出码 0，仅 INFO 日志）—— 空队列是正常状态，不该被当成错误 |
| 🤖 自动验证 | 对一个**带 PRD 锚点但没有就绪标记**的 Issue 显式跑 `iar run <PRD_PATH>` | 同样直接执行（显式定向不要求就绪标记） |
| 🤖 自动验证 | 对比改动前后，用 PRD 文件建 Issue 并执行的完整流程 | 产物与门禁行为与改动前逐一相同 |
| 👀 人审 | 打开路线图 | Product Boundary 与 Target Workflow 不再禁止执行无 PRD 的 Issue；M8 从「强制前置链」改为「可选增强」 |

上表的活跃行为行会被 §7.6 逐条转成验收断言，**改一个单元格就是在改验收标准**。

**我默默定了这些**

- **主题是「运行器通用」，不是「加一个入口」**：`--from-prompt` 只是「任意 Issue」的一种来源；人手写的 Issue 同样必须能跑。因此本 PRD 的第一条行为样例是**手工建的** Issue，而不是命令生成的。
- **撤销边界，而不是增加开关**：不引入「允许无 PRD 执行」的配置项。理由：代码里从来没有这条限制，加开关等于为一个不存在的门禁增加状态空间；而且使用者要的是默认行为，不是又一个要记得打开的开关。
- **就绪标记只约束守护进程，不约束人的显式点名**：守护进程仍只挑选带 `agent/ready` 的 Issue（路线图「不会自主决定处理哪些 issue」这条与 PRD 无关的边界保持不变）；但 `iar run --issue N` / `iar run <PRD_PATH>` 是**人明确点名**，不受该标记约束 —— 不打标记就直接跑，而因为没标记，别的机器的守护进程不会来抢。**这就是「我要自己跑、不让人抢」的路径，零竞争。**
- **同理，daemon mutex 也只约束队列轮询，不约束人的显式点名**：现状是本机只要有守护进程在跑，**任何** `iar run` 都拒绝（CONFLICT，要求 `--takeover` 停掉守护进程），与是否显式定向无关（`runner.py:146-165`）。这使上一条的「零竞争路径」实际被堵死。本次把互斥收窄到**只对队列轮询生效**（`--all-ready` / 无 target 的轮询），显式单目标 run 与守护进程共存。**证据**：锁模块自己的 docstring 写明它的唯一目的是「refusing to double-claim the ready queue」；而显式定向只跑点名的那个 Issue、根本不轮询队列，该风险不存在。**这不是拿掉保护**：同仓多 Issue 并行本来就是 daemon 的一等能力（`concurrency > 1` 用线程池同轮并行处理多 Issue，`agent_runner_orchestration_runtime.py:581`），工作树又按 Issue 隔离（`.iar-worktrees/issue-{N}`，`worktree.py:80`）；同 Issue 的互斥由本次补的 CAS 承担（见下条）。**前置依赖**：该收窄必须与 CAS **同批或更晚**落地 —— 否则会暴露「首次领取非原子」这个既有双跑窗口。`--takeover` 语义不变，仍用于「我要停掉守护进程」（例如要抢它当前正持有的 Issue）。
- **互斥靠领取时的 CAS，不靠标签**：显式定向不再要求就绪标记之后，领取时的原子性成了唯一的防双跑机制。而当前「首次领取」是裸的 read-modify-write（`agent_runner_workflow.py:57-83`，`get_issue` → 算 labels → `edit_issue_labels`，**无回读校验**），两个并发领取者可以双双写成功、双双认为自己是赢家。本次把它补成真 CAS（以认领评论里已有的 `host`/`pid` marker 作仲裁见证），使「不双跑」成为真保证。这是本次唯一的 R3（并发正确性）变更。
- **仍不提供「指派给某台机器」的机制**：这不是本次要补的能力，也不该补（会与「任一台都能接手」的高可用相悖）。想自己跑就不打标记自己跑；想让任意一台接手就打标记，先到先得。`--agent claude` 选的是 agent 二进制，不是主机（`run_agent_once.py:236` 遍历 `agent_labels` 只用于决定跑哪个 agent）。
- **不强制 Issue 结构**：Issue 正文自由文本即可，不要求验收清单、不要求 oracle 块、不要求依赖声明。
- **生成的正文默认不含验收要求**：这不是偷懒，是**为了确定性**。证据门禁读的是 Issue body 的验收小节；若段落出现与否取决于 agent 当场判断，那「这次跑得轻还是重」就不可预测。默认不生成 → 门禁确定关闭；需要时用一个显式旗标要求生成 → 门禁确定开启。
- **不改 verifier 的意图来源**：它已经是从 Issue body 取意图（这正是「Issue 是真相源」该有的样子），本次不碰。
- **不新建第二条建 Issue 路径**：复用既有建 Issue / 打标签 / 依赖宣告链路。
- **`--direct-pr` 只对没有 PRD 锚点的 Issue 有效**：若 Issue 带 PRD 锚点，直接拒绝并提示改用 `--fast-merge`。理由：PRD-backed Issue 必须走 PRD 交付门（校验验收清单、归档 PRD），那是 PRD 卫生的一部分，不能被「快速出 PR」旁路掉；否则会留下未归档、未校验的 PRD。这个前置检查是 fail-closed 的：读不到 Issue 就报错要求去掉旗标。
- **`--direct-pr` 保留 commit proxy**：执行 agent 仍然不直接 `git add` / `git commit`，由 runner 在 host 侧完成受控提交。理由：这是既有的安全边界（agent 不能自己造提交），且它是机械动作、不引入 agent 调用；「只保留机械步骤」指的就是它加上 push 与建 PR。
- **`--direct-pr` 与 `--fast-merge` 互斥，同时给出即报错**：不复用「取更强者」的隐式规则。理由：两个旗标的存在感不同（一个跳独立验证、一个连 reviewer 与仓库验证一起跳），静默升级会掩盖调用者的真实意图。
- **内部用一个发布档位而不是再加一个布尔**：`--direct-pr` 跳过的范围**严格包含** `--fast-merge` 跳过的范围（嵌套关系）。既有 `fast_merge` 布尔已经在穿透链上留下约 9 个文件、约 30 处签名镜像，历史上还触发过重复检测告警；再加一个布尔会让镜像翻倍，并且能表达出「同时跳 A 和 B」这类矛盾状态。用一个档位既避免翻倍，也让嵌套关系在类型上不可违反而**不改变 `--fast-merge` 的任何外部行为**。
- **显式定向却无事可做时必须报错，守护进程空轮询必须静默**：这两者看着矛盾，其实是一条规则的两面 —— **只有「调用者明确点名了一个 target」时才把「没跑」当成错误**。理由是空队列对守护进程是正常状态（每轮都会遇到，报错会刷爆日志），而显式 `iar run --issue N` 却什么都不做、还返回成功，会让调用者以为跑过了（这正是本 PRD 初稿评审时被指出的困惑）。这条修的是**可诊断性**，不是准入规则本身。
- **`--direct-pr` 的降级是「门禁转移到 CI」而不是「没有门禁」**：PR 仍是 Draft，CI 会在它上面跑；不因为跳过 runner 验证就自动合并。这一点必须写进 PR 正文让 reviewer 看得见。

**我理解为不做**

- **不做「一句话直接改代码」**：中间必须有 Issue 这个可被任何机器领取、可被人审阅的落脚点。
- **不做自动判断「这句话够不够清楚」**，不引入澄清对话轮次。
- **不做自然语言 → PRD 文件**（与本次方向相反）。
- **不给 Issue 正文做结构化校验**（既然是自由文本）。
- **不引入「指派给某台机器」的机制**（不加 host label / 机器预留标记）；不改跨机归还协议；不改账本 schema。
- **不放宽守护进程侧的自主挑选**：本次只放开「人的显式点名」与「本机 daemon mutex 对显式单目标的拦截」；守护进程仍只领取带 `agent/ready` 的 Issue。
- **不改守护进程 / `--all-ready` 在空队列时的静默行为**（那是正常状态，报错会刷爆日志）。
- **不删 M8 里程碑**：澄清与 PRD 生成能力仍有价值，只是从「强制前置」改为「按需使用」。

**证伪式理解**：读作「让运行器接受任意 Issue（含人手写的、无 PRD 的），并撤销文档里那条禁止它的产品边界；顺带提供一个把一句话变成 Issue 的创建入口」。任何人读完这句话如果认为本次要给无 PRD 执行加配置开关、要强制 Issue 结构、要改 verifier 的意图来源、要删掉澄清/PRD 能力、或者要实现「一句话直接改代码」，那都是读错了。

### What The User Gets

想记一个需求时，写个 Issue 打上就绪标记就能开工；或者一句话直接建出 Issue。想现在就开工就加就绪标记（谁先轮询到谁领，不区分机器）；还没想好就什么都不加 —— 它不会进队列，也不会被任何机器的守护进程抢先跑掉。文档不再说「你必须先写 PRD」，代码不再有那条谁都没实现的强制链。

### Measurable Objectives

- 一个**手工建的、无 PRD 锚点的** Issue 能被真实执行并产出 Draft PR（本次的首要目标，且此前从未被真实验证过）。
- `--from-prompt` 能建出 Issue，且 `tasks/pending/` 下不新增任何文件，Issue body 不含 PRD 锚点。
- 无 PRD 的 Issue 仍需就绪标记才能被领取；不打标记的不被任何机器的守护进程领走。
- 就绪的 Issue 能被正在为该仓库跑守护进程的那台机器领走，认领标记如实记录其 host 与 PID；不存在「指定机器」的参数。
- 无 PRD 锚点且正文无验收要求的 Issue 不产生证据要求；显式要求的正常产生。
- 路线图的 Product Boundary、Target Workflow、M8 与相关验收项已与实现对齐，不再声称存在一条代码里不存在的强制链。
- 用 PRD 文件建 Issue 并执行的既有路径在改动前后逐项相同。

## 2. Human Review Map (介入与风险地图)

**本次有三项决策需要你拍板。前两项关乎「放弃多少保护换速度」，第三项关乎「准入规则怎么改、以及随之必须补的并发正确性」。**

---

**决策一：撤销「无 PRD 的 Issue 不得被当作可执行任务领取」这条产品边界。**

这条边界写在路线图的 Product Boundary 里，是产品层面的准入规则，不是技术约束 —— 改它等于改变产品的对外承诺。**它的两个直接后果：**

1. **「先对齐验收标准再开工」这道闸在无 PRD 的 Issue 上不再存在。** 这类 Issue 没有验收清单、默认也没有验收要求，因此证据门禁整体关闭（`FRONTEND_VISUAL_EVIDENCE_MISSING` 这类门禁全部不再触发，**连前端改动也不要求截图**）；若同时用快速合并档，重验与独立 verifier 也一并旁路。质量判断完全依赖人事后看那张 Draft PR。
2. **路线图里与之配套的「描述不清就反问用户」规则会失去强制力。** 原设计是「无 PRD → 必须先澄清 → 才可能执行」；改为「可直接执行」后，澄清变成可选：执行方会在需求含糊时自行补全，而不是停下来问你。

**为什么仍然建议撤销**：代码里从来没有实现这条强制链，所以现状是文档承诺了一个不存在的保护 —— 这种「文档说安全、实现没做」的状态比明确放开更危险，因为它会让人误以为有闸。同时使用者的项目定位是 Issue 为真相源，这条边界与该定位直接冲突。

---

**决策二：新增 `--direct-pr` 档位，跳过 runner 侧全部门禁与第二个 agent。**

用途是「执行 agent 干完就出 PR」。它跳过的不只是 `--fast-merge` 已跳过的那部分（重跑 RV + 独立复核），还包括：

- **pre-PR review** —— 一个默认开启、最多 2 轮修复、单轮超时 1800s 的 reviewer agent，而且它自己能改代码并再次 push；
- **runner 的验证命令**（`runner.verification_commands`）与 **pre-commit 验证命令** —— 这两处失败都会把你打回 agent 再改。

跳过后的流程只剩机械步骤：runner 受控提交 → push → 建 Draft PR。

**必须看清的后果：**

1. **runner 侧不再有任何质量检查。** 连仓库自己配置的测试命令都不跑，因此**可以推出一个连测试都不过的 PR**。这不是「门禁变松」，是「门禁消失」。
2. **唯一的门禁变成 PR 上的 CI。** 设计意图是「推上去让 CI 说话」（PR 仍是 Draft、不自动合并），但前提是**该仓库的 CI 真的会在 PR 上跑、并且真的会红**。若某仓库没有 CI 或 CI 不覆盖改动，这个档位产出的东西**没有任何自动化把关**。
3. **`--fast-merge` 不再是最快的档位**，两者语义差需要使用者自己记住：`--fast-merge` = 跳过独立验证（保留 reviewer 与仓库验证）；`--direct-pr` = 连 reviewer 与仓库验证一起跳。

**为什么仍然建议做**：使用者的场景是「写个 Issue 一句话就开工」的简单需求，`--fast-merge` 之后再挂一个 reviewer agent 与两处会打回 agent 的验证闸，与「快速」矛盾；而 PR 上的 CI 本来就是这个仓库真正的质量防线。它被刻意限定为**只对没有 PRD 锚点的 Issue 有效**（PRD-backed 必须走 PRD 交付门并归档，不能被旁路），所以不会污染 PRD 流程。

---

**决策三：`iar run` 的显式定向不再要求就绪标记（并因此必须补首次领取的原子性）。**

使用者的真实诉求是「我想让**我这台**机器跑，别被别的机器抢走」。当前做不到：就绪标记是**共享队列信号**，打完标记后另一台机器的守护进程最多 2 分钟（默认轮询间隔）就会先领走，而你无法阻止；而 `iar run --issue N` 自己也被要求必须带该标记，所以连「先占位再自己跑」的路径都没有。

改法：**让就绪标记只约束守护进程的自主挑选，不约束人的显式点名** —— 不打标记就直接 `iar run --issue N`，因为没标记，任何守护进程都不会来抢（零竞争）；想让任意一台接手时再打标记。守护进程侧准入不变。

**同一原则还要求收窄 daemon mutex**：现状是本机只要有守护进程在跑，**任何** `iar run` 都被拒绝（CONFLICT，要求 `--takeover` 先停掉守护进程），与是否显式定向无关（`runner.py:146-165`）。这使上面那条「零竞争路径」实际被堵死——你无法在守护进程活着时手动跑一个 Issue。本次把该互斥收窄为**只挡队列轮询**（`--all-ready` / 无显式 target），显式单目标与守护进程**共存**。依据：该锁的唯一声明用途是防止「double-claim the ready queue」（`daemon_single_instance.py:1-11`），而显式单目标不轮询队列；同仓多 Issue 并行本就是 daemon 的一等能力（线程池，`agent_runner_orchestration_runtime.py:581`），工作树又按 Issue 隔离（`.iar-worktrees/issue-{N}`）。**同目标的排他因此完全落在上面的 CAS 上，故两者必须同批或 CAS 在前。** `--takeover` 保留，仍用于「要停掉守护进程」（例如抢它当前正持有的 Issue）。

**这个改动带来一个必须一并修的并发缺陷**：显式定向不再要求标记后，**领取时的原子性成了唯一的防双跑机制**，而它现在不是原子的 —— `agent_runner_workflow.py:57-83` 是 `get_issue` → 算 labels → `edit_issue_labels`，**无回读校验**。两个并发领取者可以双双写成功、双双认为自己是赢家，**双跑同一 Issue**（两个 agent 同时改同一分支）。这是**既有**缺陷（今天被就绪标记部分掩盖），但本次会把它升格为唯一防线，所以必须补真 CAS。

**代价与残余风险**：CAS 若写错，可能反向导致 Issue **永久卡住**（无人能领）。这是本次唯一的 `R3`（并发正确性）变更，需要可执行负控（真实双进程并发 → 断言只有一个赢）与人工确认。**若你希望缩小本次范围**，可把「首次领取补 CAS」拆成独立 PRD 先行交付，本 PRD 只做准入规则变更。

---

**是否可接受，取决于你是否愿意在这类需求上完全靠事后人工判断与 CI。** 若对决策一不接受，替代路径是保留「无 PRD 可直接执行」但要求执行前必须在 Issue 里回一句验收标准（即把可选旗标改为默认开启）。若对决策二不接受，替代路径是用 `.iar.toml` 持久关掉 `pre_pr_review.enabled` 并清空 `verification_commands`（已有能力，但那是仓库级永久设置，不适合「偶尔赶时间」）。若对决策三不接受，可继续依赖部署约束（别的机器不为该仓库跑守护进程）规避竞争，但那就回到「没有 per-run 手段」的现状。

其余变更点都是**执行器 + 自动门禁**范围：`--from-prompt` 的入参互斥校验（`api`）、`--direct-pr` 的档位穿透与前置拒绝（`api`/`core`）、CLI 表面同步（守卫测试兜底）、以及路线图与实现对齐。**特别说明**：除 `--direct-pr` 的档位判定外，本次**没有**运行时的功能性代码改动 —— 逐条核查已确认全部门禁条件生效、无 PRD 的降级路径均已实现（§5 详列）。因此本次的执行侧工作主要是**证明既有路径真的能跑**（从未被真实验证），而不是实现它。

**本次明确不涉及**：没有数据库结构或迁移变更；没有安全或权限边界变更；没有并发行为变更；不新增运行时功能门禁。

## 3. Usage And Impact After Implementation

**记需求的人（主要受众，CLI）**：可以直接写 Issue（含自由文本）打上就绪标记就开工，也可以用一句话让 agent 代建。「必须先在 `tasks/pending/` 写一份 PRD」这个前置消失。

**另一台机器上的 keda / 其他 agent**：无需任何改动，也无需任何新知识 —— 建出的 Issue 与既有就绪 Issue 在 GitHub 上完全同构。**需求落到 Issue 后，任何为该仓库跑守护进程的机器都能领它，先到先得；换机器执行是部署层的事（哪台在跑、哪台能力匹配），不需要 Issue 上带任何「机器」信息。**

**想「就我这台机器跑、别被抢走」的人**：这是本次新疏通的路径 —— **不要打就绪标记**，直接 `iar run --issue N`（或给 PRD 路径）。因为没标记，别的机器的守护进程不会轮询到它，**零竞争**；而这需要 `iar run` 的显式定向不要求标记（本次改）与首次领取的原子性（本次补 CAS）共同成立。

**用 PRD 文件的既有流程**：逐字不变。`prd_paths` 入参、`--publish-prd`、`--force`、PRD 交付门、验收清单归档一律不动。

**既有 `iar issue create` 调用方**：`prd_paths` 从必填变为「与 `--from-prompt` 二者必居其一」。不传新旗标时行为逐字不变；**唯一扰动**是「两个入参都没给」时的报错文案会改为同时说明两种输入方式（退出码仍为 2）。这是本次唯一一处对既有表面的扰动，列为显式验收项。

**路线图读者**：Product Boundary 与 Target Workflow 不再声称存在一条代码里没有的强制链；M8 从「强制前置」改为「可选增强」。这是**对外承诺的收窄**，也是本次风险最高的一处。

向后兼容性：向后兼容 —— 新输入方式可选，既有 PRD 路径行为不变，**守护进程侧的就绪准入与 `--all-ready` 的 mutex 行为不变**；运行时的行为变化共三处，且都是「收窄约束、不新增约束」：① 显式定向不再要求就绪标记；② daemon mutex 不再拦显式单目标；③ 首次领取由裸 write 改为原子 CAS（此为正确性修复）。

## 4. Requirement Shape

- **actor**：写 Issue 的人（CLI / GitHub 网页）；为同一仓库跑守护进程的执行方（可能在不同机器上）；路线图读者
- **trigger**：一个带就绪标记的 Issue（无论是否带 PRD 锚点）进入队列；或人跑 `iar issue create --from-prompt "<一句需求>"`
- **expected behavior**：任意带就绪标记的 Issue 都能被领取并端到端执行出 Draft PR，Issue 正文被当作需求来源；`--from-prompt` 能从一句话建出自足的 Issue；文档不再禁止无 PRD 执行
- **scope boundary**：不加配置开关、不强制 Issue 结构、不改 verifier 意图来源、不删澄清/PRD 能力、不做「一句话直接改代码」、不提供机器定向机制；准入侧只改「显式定向不再要求就绪标记」与「daemon mutex 不再拦显式单目标」这两处（并补首次领取 CAS 以承接排他），守护进程侧的自主挑选与 `--all-ready` 的队列互斥均不变

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 相关模块

| 角色 | 位置 |
|---|---|
| **产品边界（本次要改的文档）** | `ROADMAP.md` 的 Product Boundary、Target Workflow、Partially/Not Completed、M1、M8、Near-Term Delivery Order、Acceptance Checklist、Open Questions 各处 |
| CLI 命令定义（typer） | `src/backend/api/cli_typer_issue.py:77-156`（`issue_create_command`） |
| 命令实现转发 | `src/backend/api/cli_parsed_commands/`（issue create 的转发与互斥校验） |
| 从 PRD 建 Issue | `src/backend/core/use_cases/create_issue_from_prd.py:959`（`create_issue_from_prd`） |
| 内容生成目标配置 | `src/backend/infrastructure/config/agent_runner_settings.py`（`issue_from_prd` / `draft_pr` / `prd_from_issue` 三处） |
| 内容生成调度 | `src/backend/core/use_cases/generated_content.py`（历史红线，**禁止改动**） |
| 目标解析器注册 | `src/backend/engines/agent_runner/factories/__init__.py:175`（`resolve_issue_from_prd_target`） |
| **就绪准入（本次要改：显式定向不再要求标签）** | `agent_runner_orchestration_runtime.py:679-683`（定向模式仍要求 `agent/ready`）、`:724-729`（running 通道）、`:762-767`（blocked 通道）、`:781-786`（候选为空时静默返回 0） |
| **首次领取（本次要补 CAS）** | `agent_runner_workflow.py:57-83`（`transition_issue_workflow_state`：`get_issue` → 算 labels → `edit_issue_labels`，**无回读校验**）；调用点 `agent_runner_issue_handlers.py:597-608`（先切 running、再发含 host/pid 的认领评论）；**带 CAS 的对照实现** `agent_runner_reclaim.py:250`（read-check-write-reread）；认领 marker 工具 `agent_runner_reclaim.py:38/59/65/79` |
| 跨机领取（本次不改） | `agent_runner_reclaim.py:38/59/173/250`（claim marker 格式、host/PID 归属、CAS 归还） |
| **无 PRD 的交付门** | `agent_runner_feedback.py:452-454`（`extract_prd_path` 为空即 return） |
| **无 PRD 的 PR 契约** | `agent_runner_pr_body_contract.py:145-147`（无锚点返回 `[]`） |
| **无 PRD 的证据门禁** | `agent_runner_validation.py:529-530`（`validation_required` 不满足即 return，连带 `:532` 的前端视觉证据也跳过） |
| **无 PRD 的最终复核** | `agent_runner_final_verification.py:76`（`ensure_validation_evidence_ready` 提前返回） |
| **verifier 的意图来源** | `run_verifier_agent.py:135/175`（`issue.body.strip()` 作为意图；oracle 空时降级为 `(no structured oracle items)`） |
| **无 PRD 的提示词** | `agent_runner_feedback.py:215-244`（`_build_prd_context_block:237-238` 无锚点返回泛化提示） |
| 无 PRD 的账本写入 | `agent_runner_orchestration_runtime.py:83-93`（`_resolve_lifecycle_prd_path` → `""`）、`console_store.py:557`（upsert 空值保护） |
| 无 PRD 的证据目录 | `resolve_issue_evidence_dir`（兜底 `tasks/evidence/issue-<N>`） |
| **pre-PR review（`--direct-pr` 要跳过）** | `agent_review.py:520` `run_pre_pr_review`（：通过 `review_config.enabled` 判定；`pre_pr_review.enabled` 默认 `True`，`max_attempts=2`，`timeout_seconds=1800`，reviewer 可改代码并经 `push_callback` 再 push） |
| **发布路径（`--direct-pr` 与 `--fast-merge` 都经过）** | `agent_runner_publication.py:341` `_review_verify_create_pr`（注释明示：pre-PR review 照常执行，快速通道只旁路 verification_request 携带的门禁与 PR 标注）、`:368` `ensure_final_verifier_verdict`、`:222/:240/:340-349` PR 正文标注 |
| **runner 验证命令（`--direct-pr` 要跳过）** | `agent_runner_settings.py:376` `verification_commands`、`:386` `pre_commit_verification_command`（默认 `None`）；执行点 `run_agent_execution_loop.py:702`（Phase 2）、Phase 4 commit 阶段 |
| **`--fast-merge` 现状（本次不改行为）** | `cli_parser.py:301`（argparse）与 `cli_typer_runner.py:137`（typer）定义；`run_agent_execution_loop.py:986`（Phase 4.5 唯一分支）；`cli_parsed_commands/runner.py:76`（与 `--all-ready` 冲突拒绝）、`:137`（stack 依赖拒绝）；`agent_runner_dependencies.py:337/354`（marker 生成/解析） |
| **`.iar.toml` 可持久化的相关键（现有替代路径）** | `repository_local.py:178/297-302`（`pre_pr_review.*` 可写，含 `enabled`） |
| CLI 表面守卫 | `tests/test_iar_operator_skill.py`（`_ALLOWED_FLAGS`） |
| 随包 skill | `src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md` |

### 依赖方向

四层方向 `api/ -> core/ -> engines/ -> infrastructure/` 不变。**本次改动落在 `api`（旗标与互斥校验）与 `engines`（新增一个内容生成目标的解析器），外加仓库根文档。`core` 的运行时行为零改动**（§7 详列证据）。

### 现有模式

`create_issue_from_prd`（`create_issue_from_prd.py:959`）已经把「读一个需求来源 → 生成 Issue 标题与正文 → 建 Issue → 可选打就绪标签 → 可选宣告依赖」这条链走通了一遍，包括 agent 生成失败时回退模板。本次新增的输入方式应当**复用这条链的建 Issue 与标签部分**，只替换「需求来源」与「正文生成目标」，而不是另写一条建 Issue 路径。

### 所有权与边界

- **`generated_content.py` 是历史红线，PRD 明令禁止改动。** 新增生成目标走配置字段 + 独立目标解析器（`factories/` 下的既有模式）。
- **就绪准入语义归 `agent_runner_orchestration_runtime.py` 所有。** 本次**只改显式侧**：显式定向不再要求 `agent/ready`；**守护进程侧的判定不变**（仍只领带标记的 Issue，路线图「不会自主决定处理哪些 issue」那条边界与 PRD 无关，不动）。
- **daemon mutex 语义归 `daemon_single_instance.py` + `cli_parsed_commands/runner.py` 所有。** 本次**收窄为只挡队列轮询**（显式单目标与守护进程共存，FR-24）；同目标的排他改由首次领取 CAS（FR-23）承担，两者必须同批或 CAS 在前。
- **跨机领取协议（归还路径）归 `agent_runner_reclaim.py` 所有，本次零改动。** 本次补的首次领取 CAS 落在 `agent_runner_workflow.py`，不重写归还路径。
- **verifier 的意图来源归 `run_verifier_agent.py` 所有，本次零改动** —— 它已经是从 Issue body 取意图。
- **路线图的产品边界归 `ROADMAP.md` 所有**，本次改动它需要与其他章节保持一致（不能只改一处留下自相矛盾的表述）。

### 前端影响

无。本次是 CLI 表面 + 仓库文档，控制台无需改动。

### 约束

- **本次应尽量不产生运行时功能改动。** 若实现过程中发现某处确实缺「无 PRD」分支，先停下来更新本 PRD 说明（那会改变本次的风险画像），再改。
- 既有债务已清零：`cli_parser.py` 等 4 个文件的行数超限已由 PR #212 拆分并合并（实测 34 / 745 / 972 / 962）。本次加旗标不再需要承担拆分工作，但**不要把任何文件重新推过 1000 非空行**。
- `hooks/max_file_lines.allowlist.txt` 保持为空名单。
- 新增/修改公共 Python API 需 Google Style docstring（`D100`–`D107` 强制）。
- 提交信息用英文 Conventional Commits。
- 新增 CLI 旗标必须同步随包 `iar-operator` skill 与 `docs/`，并通过 CLI 表面守卫测试。
- 路线图改动后需保证全文不自相矛盾（§7 Drift Guard 给出需一并检查的章节清单）。

### 相关 PRD

- `tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`（Issue #209，Draft PR #211）：**本次「小需求走完整 PRD 门禁性价比为负」这一观测的实测来源**（47 分钟 / 22 次浏览器自证工具调用）。**不重复其目标状态。**
- **PR #212**（已合并 `866ddabc`）：4 个超限源文件的拆分。本 PRD 的 `--from-prompt` 要改 CLI parser，依赖它已清零的行数余量；前置已满足。
- `tasks/hold/P1-FEAT-20261006-024434-iar-run-quick-stage-skips.md`：`iar run --quick` 轻量档，**已搁置**。它优化「出了 PRD 之后跑得慢」，方向被本 PRD 取代（本次是「根本不必出 PRD」）。其识别出的行数债已由 PR #212 承接。
- `ROADMAP.md` 的 M8「Issue-First PRD Gate」与 M1「Human-Gated Task Intake」：**本次要改的两节**。M8 的能力（澄清、合议、PRD 生成）不删除，只从「强制前置」改为「按需使用」。
- `tasks/archive/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance.md`：历史上另一次「取消强制阶段」的决策先例（re-grounding 从独立阶段收缩为提示词内置规则），本次撤销 PRD 前置有同类形态。

本次**不重复**任何 pending 工作：`tasks/pending/` 中没有与「任意 Issue 可执行」相关的 PRD。

## 6. Recommendation

### Recommended Approach

**先证明既有路径能跑，再撤销文档里那条禁止它的边界，最后补一个把一句话变成 Issue 的创建入口。**

三步按此顺序，理由是它们依赖同一份事实：

1. **证明（本次的主体）**：手工建一个无 PRD 锚点的 Issue，打就绪标记，真实执行一次，留下端到端证据。**这是首次真实验证这条路径**，也是本次最重要的交付 —— 若中途撞到代码阅读没看出的硬依赖，本次的范围会变化，必须先知道。
2. **撤销边界（文档）**：把路线图的 Product Boundary / Target Workflow / M1 / M8 / 验收项 / Not Completed 条目与实现对齐，改为「无 PRD 可直接执行，澄清与 PRD 为按需能力」。需一并检查所有相关章节，避免留下自相矛盾的表述。
3. **创建入口（代码）**：`iar issue create --from-prompt "<自然语言>"`，与既有的 `prd_paths` 位置参数互斥；agent 把这句话写成一个不含 PRD 锚点的 Issue 正文；建 Issue 与打标签复用既有链路。

具体机制：

- **入参**：`prd_paths` 由必填放宽为「与 `--from-prompt` 二者必居其一」。两者同时给、或都不给，都是 usage error（退出码 2）。`--publish-prd` 与 `--force` 在 `--from-prompt` 下显式传入即报 usage error（它们只在 PRD 路径下有语义，静默忽略会误导调用方）。`--depends-on` 两种方式都可用（Issue 级关系，与是否存在 PRD 无关）。
- **正文生成**：新增一个内容生成目标（沿用 `issue_from_prd` / `prd_from_issue` 的命名族，形如 `issue_from_prompt`），产物是自足的自然语言需求描述。**不生成** PRD 指针、**不生成** Machine Contract 结构、**默认不生成**验收要求。
- **就绪**：`--ready/--no-ready` 语义不变，默认未就绪。**注意它的准确含义是「要不要现在就进队列」**：不加标记则任何机器的守护进程都不会领；加了则先轮询到的守护进程领走。它**不是**机器定向参数。
- **门禁行为**：因为正文无 PRD 锚点、默认无验收段，运行时各门禁按既有「无 PRD」分支自然关闭。**本次不为这些门禁写任何新分支。**
- **可选中间档**：显式旗标（形如 `--require-validation`）要求 agent 在正文里生成一段验收要求，从而让证据门禁确定开启。默认关闭。
- **`--direct-pr`（本次新增的档位）**：per-run 旗标，让执行 agent 结束后只剩机械步骤。它是 `--fast-merge` 的**严格加强版**（跳过范围包含后者），因此：
  - **前置拒绝**：仅对**没有 PRD 锚点**的 Issue 有效（fail-closed：读不到 Issue 即报错）；对 PRD-backed Issue 报 usage error 并提示改用 `--fast-merge`。
  - **互斥**：与 `--fast-merge` 同时给出即 usage error，不隐式取更强者。
  - **跳过的门禁**：pre-PR review（第二个 agent）、`runner.verification_commands`、`pre_commit_verification_command`、Phase 4.5 的 `rv_reexec` 与 verifier、发布前的最终 RV/verifier 复检；若 Issue 正文仍带验收段，证据门禁也一并跳过。
  - **保留的步骤**：commit proxy（runner 受控提交，agent 不直接 commit）→ push → 建 Draft PR。
  - **可观测**：PR 正文注入机器可读档位标注 + 一行人读说明（与 `iar:fast-merge` marker 同族，但值不同，便于区分两个档位）。
  - **内部形态**：以一个「发布档位」字段表达（`normal` / `fast` / `direct`），而不是在既有 `fast_merge` 布尔之外再加一个布尔 —— 两者是嵌套关系，用一个字段既避免签名镜像翻倍（既有布尔已在约 9 个文件留下约 30 处镜像，历史上触发过重复检测告警），也让矛盾状态在类型上不可表达。**该重构不改变 `--fast-merge` 的任何外部行为**（其 CLI 表面、退出码、PR 标注逐字不变）。
- **显式定向不受就绪标记约束（准入规则变更）**：今天 `iar run --issue N` 打到没有 `agent/ready` 的 Issue 上会**静默返回 0**（`agent_runner_orchestration_runtime.py:781-786` 只留一条 INFO 日志）。改为：**显式定向 = 人明确点名，直接执行**，不要求就绪标记 —— 也因此不会与任何守护进程竞争（没标记就没人轮询到）。守护进程侧不变，仍只挑选带 `agent/ready` 的 Issue。
- **daemon mutex 收窄为「只挡队列轮询」（互斥语义变更）**：今天本机有活守护进程时，**任何** `iar run` 都拒绝（CONFLICT，提示 `--takeover`），与显式/轮询无关（`src/backend/api/cli_parsed_commands/runner.py:146-165` 的 `find_live_daemon_pid` 检查）。改为：该互斥**只在本次调用会轮询队列时生效**（`--all-ready` / 无显式 target）；**显式单目标**（`--issue N` / PRD 路径）不再触发它，与守护进程共存。依据：锁的唯一声明用途是防止「double-claim the ready queue」（`daemon_single_instance.py:1-11`），而显式单目标不轮询队列；同仓多 Issue 并行已是 daemon 既有能力（`agent_runner_orchestration_runtime.py:581` 线程池），工作树按 Issue 隔离（`worktree.py:80`）。**依赖**：必须与上一条 CAS 同批或更晚落地。`--takeover` 保留，仍用于显式停掉守护进程。
- **首次领取补真 CAS（R3，并发正确性）**：显式定向不再要求标记后，领取的原子性成了唯一防双跑机制，而它现在是裸的 read-modify-write。补法：沿用本仓 reclaim 路径已验证的 read-check-write-**reread** 模式，并以**认领评论里的 `host`/`pid` marker 作仲裁见证** —— 领取者先切 running 并发出带自己 marker 的认领评论，再回读确认自己的 marker 是最早一条；不是就主动退让（释放并退出）。守护进程与显式定向**共用同一条领取路径**，因此两种入口都受保护。
- **显式定向到「不可领取」状态时报明确错误**：已被他人活跃认领（`agent/running` 且持有者存活）→ CONFLICT 并指出持有者；`agent/blocked` 且无解除标记 → 拒绝并提示 `iar blocked-continue`；target 不存在 → NOT_FOUND。**不再静默返回 0。**
- **守护进程与 `--all-ready` 的空队列行为保持不变**（返回 0、仅 INFO 日志）—— 空队列对它们才是常态，报错会刷爆日志。

为什么这是最贴合现有架构的做法：

- **运行链路零改动**：无 PRD 的路径已完整实现（逐条位置见 §5 表），本次唯一缺的是**证明它**与**允许它**。
- **撤销而非加开关**：代码里没有这条限制，加配置项等于为一个不存在的门禁增加状态空间；且使用者要的是默认行为。
- **文档与实现对齐**：现状是文档承诺了一个不存在的保护，这比明确放开更危险 —— 会让人误以为有闸。
- **跨机零成本**：领取机制的原子性（标签 CAS）、host/PID 归属、死亡归还与进度保留都已实现；新入口只要产出带正确标签的 Issue，任何为该仓库跑守护进程的机器都能领。
- **默认值为了确定性**：证据门禁读 Issue body 的验收小节，因此「段落在不在」必须由旗标决定而非 agent 心情决定。

### Proposed Solution Summary (实现机制)

- **核心机制**：证明既有「无 PRD 可执行」路径真实可用 → 撤销文档中禁止它的产品边界 → 增加一个把自然语言变成 Issue 的创建入口。
- **谁提供数据**：需求来自人写的 Issue 正文，或来自人给的一句话（CLI 入参）。**由人显式提供**，不做推断。
- **接入点**：**`iar issue create`** 的命令行表面（`--from-prompt`）+ **`iar run`** 的命令行表面与档位穿透（`--direct-pr`）+ 显式侧的就绪准入与 daemon mutex 收窄（`api`/`core`）+ 首次领取 CAS（`core`）+ 一个新增的内容生成目标配置与解析器 + `ROADMAP.md`。**不新增 API 端点、不改账本 schema、不改跨机归还协议、不改 verifier。**
- **系统状态变化**：GitHub 上多一个 Issue；本地仓库文件系统零变化（不生成 PRD 文件）；`ROADMAP.md` 内容变化。
- **用户可见行为变化**：`iar issue create` 多一个可选旗标；路线图不再禁止无 PRD 执行；用 PRD 文件的既有行为不变。
- **刻意避免的复杂度**：不新增配置开关、不新增子命令、不新增建 Issue 的第二条路径、不改账本 schema、不引入机器定向机制、不改跨机归还协议、不做澄清对话、不往历史红线文件加逻辑。

### Alternatives Considered

**替代方案 B：保留产品边界，只加 `--from-prompt` 生成 PRD（让一句话也走完整 PRD 链）。** 不采用。这正好是本次要消除的迂回：使用者要的是 Issue-first，凭空多一个 PRD 文件既违背定位，也把重门禁重新引回来（正是 47 分钟那个观测的成因）。

**替代方案 C：新增配置开关 `allow_issue_without_prd`，默认关。** 不采用。代码里没有这条限制，加开关等于为一个不存在的门禁引入状态；且默认关意味着使用者拿到的还是「要记得打开」，与「默认就能用」的诉求相反。

**替代方案 D：新增 `iar issue draft "<一句话>"` 子命令。** 不采用。语义上与 `create` 是同一件事的两种输入方式，新开子命令要同步随包 skill 命令表、docs 页面、CLI 表面守卫白名单，同步面是加旗标的好几倍。

**替代方案 E：删掉 M8 里程碑与澄清能力。** 不采用。澄清与 PRD 生成对复杂需求仍有价值，本次只是把「强制前置」改为「按需使用」；删除是不可逆的信息损失。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

```
┌─ 第一步：证明既有路径（本次主体，零代码改动）
│  人为建一个 Issue：自由文本正文，无 PRD 锚点，无验收清单
│    └─ 打 agent/ready ─→ 真实 iar run --issue N
│         ├─ 领取     agent_runner_orchestration_runtime.py:679（只需 ready 标签）
│         ├─ 提示词   agent_runner_feedback.py:237-238（无锚点→泛化提示）
│         ├─ 执行     agent_runner_issue_handlers.py（对 prd_path 零引用）
│         ├─ 交付门   agent_runner_feedback.py:452-454（提前 return）
│         ├─ 证据门禁 agent_runner_validation.py:529-530（提前 return）
│         │             └─ 连带 :532 前端视觉证据也跳过 ← 需显式披露的后果
│         ├─ 最终复核 agent_runner_final_verification.py:76（提前 return）
│         ├─ PR 契约  agent_runner_pr_body_contract.py:145-147（返回 []）
│         ├─ verifier run_verifier_agent.py:175（从 issue.body 取意图）
│         └─ 账本/证据 console_store.py:557 · resolve_issue_evidence_dir → issue-<N>
│    └─ 产出：Draft PR + 真实证据（此前从未验证过）
│
├─ 第二步：撤销产品边界（文档，需全文一致）
│   ROADMAP.md
│     ├─ Product Boundary  ← 核心条款：从「禁止执行」改为「允许，由就绪标记决定」
│     ├─ 「描述不清必须反问」← 失去强制力，改为「可选」
│     ├─ Target Workflow 步骤 2-10
│     ├─ Partially Completed / Not Completed 条目
│     ├─ M1 Human-Gated Task Intake
│     ├─ M8 Issue-First PRD Gate ← 从「强制前置」改为「按需增强」
│     ├─ Near-Term Delivery Order
│     ├─ Acceptance Checklist 相关项
│     └─ Open Questions（intake label 相关）
│
└─ 第三步：创建入口（代码）
    iar issue create --from-prompt "…" [--ready]
      ├─ CLI 入参校验（新增互斥规则）
      │    ├─ prd_paths 与 --from-prompt 二者必居其一（都不给/都给 → usage error 退出码 2）
      │    ├─ --from-prompt 下显式 --publish-prd / --force → usage error
      │    └─ --depends-on 两种方式都可用
      ├─ 需求来源分派
      │    ├─ prd_paths   → 既有路径：create_issue_from_prd（逐字不变）
      │    └─ --from-prompt → 【新增】内容生成目标 issue_from_prompt
      │          └─ 产物：标题（概括）+ 正文（自足自然语言需求）
      │             ✗ 无 PRD 指针  ✗ 无 Machine Contract 结构
      │             ✗ 默认无验收段（除非显式 --require-validation）
      └─ 建 Issue + 标签（复用既有链路，不另写）
```

### Change Impact Tree

```
任意 Issue 可执行（Issue-first 交付入口）
├── 第一步：证明（零代码改动，产出证据）
│   └── 手工 Issue → agent/ready → 真实 iar run → Draft PR
│       └── 若撞到未预料的硬依赖：停下更新本 PRD（会改变风险画像）
├── 第二步：撤销产品边界（文档）
│   └── ROADMAP.md
│       ├── Product Boundary（核心条款，改为允许 + 由就绪标记决定）
│       ├── 「描述不清必须反问」→ 改为可选
│       ├── Partially Completed / Not Completed 条目
│       ├── Target Workflow 步骤
│       ├── M1 · M8（强制前置 → 按需增强）
│       ├── Near-Term Delivery Order · Acceptance Checklist · Open Questions
│       └── 【一致性检查】全文不得留下仍声称「必须先有 PRD」的表述
├── 第三步：创建入口（api / engines）│   ├── src/backend/api/cli_typer_issue.py
│   │   ├── :80-83 prd_paths 由必填放宽为可选
│   │   ├── 【新增】--from-prompt 旗标定义 + help
│   │   └── :77-156 转发层参数透传
│   ├── src/backend/api/cli_parsed_commands/（issue create 转发与互斥校验）
│   │   ├── 【新增】二者必居其一的 usage error（退出码 2）
│   │   └── 【新增】--from-prompt 下 --publish-prd / --force 的互斥拒绝
│   ├── src/backend/api/ 的 CLI parser 模块（PR #212 拆分后的 issue parser 部分）
│   │   └── 【新增】--from-prompt 的 argparse 定义（两套 CLI 入口共享）
│   ├── src/backend/infrastructure/config/agent_runner_settings.py
│   │   └── 【新增】issue_from_prompt 的 GeneratedContentTargetSettings 字段
│   ├── src/backend/engines/agent_runner/factories/__init__.py
│   │   └── 【新增】resolve_issue_from_prompt_target（照 resolve_issue_from_prd_target 模式）
│   ├── src/backend/engines/agent_runner/factory_config_builder.py / factory_config_merge.py
│   │   └── 【新增】目标配置的构造与合并转发
│   ├── src/backend/engines/agent_runner/repository_local.py
│   │   └── 【新增】.iar.toml 模板注释里的目标清单
│   └── ⛔ src/backend/core/use_cases/generated_content.py（历史红线，禁止改动）
├── 第四步（与 --from-prompt 并列）：--direct-pr 档位（api / core）
│   ├── CLI 表面（两套定义 + 前置拒绝）
│   │   ├── src/backend/api/cli_typer_runner.py / cli_parser 的 run parser 模块
│   │   │   └── 【新增】--direct-pr 旗标定义 + help
│   │   └── src/backend/api/cli_parsed_commands/runner.py
│   │       ├── 【新增】与 --fast-merge 互斥（同时给出 → usage error 退出码 2）
│   │       ├── 【新增】仅限无 PRD 锚点的 Issue（fail-closed；带锚点 → usage error 指向 --fast-merge）
│   │       └── 沿用既有「必须指定单个 target」约束（与 --fast-merge 同款）
│   ├── 发布档位字段（替代再加一个布尔）
│   │   └── AgentExecutionRequest 的 fast_merge 布尔 → 发布档位字段（normal / fast / direct）
│   │       逐层穿透照搬既有 fast_merge 的 ~9 文件镜像位置；--fast-merge 外部行为逐字不变
│   ├── 门禁旁路（core）
│   │   ├── run_agent_execution_loop.py:986 区段 —— direct 档额外跳过 Phase 2 与 Phase 4 的验证命令
│   │   ├── agent_runner_publication.py:341 —— direct 档跳过 pre-PR review（run_pre_pr_review）
│   │   └── agent_runner_final_verification.py:76 —— direct 档跳过最终 RV/verifier 复检
│   ├── 可观测
│   │   └── agent_runner_publish.py:340 区段 + agent_runner_dependencies.py marker 工具
│   │       【新增】direct 档的 PR 正文标注（与 iar:fast-merge 同族、值不同）
│   └── 不改动
│       ├── commit proxy（runner 受控提交仍是唯一提交路径）
│       └── --fast-merge 的 CLI 表面、退出码与 PR 标注
├── 第五步：准入规则变更 + 首次领取 CAS（core / api）
│   ├── src/backend/core/use_cases/agent_runner_orchestration_runtime.py
│   │   ├── :679-683 【改】显式定向时不再要求 agent/ready（直接进候选）；守护进程侧不变
│   │   ├── :724-729 / :762-767 【改】显式定向到 running / blocked 时给出明确拒绝原因
│   │   └── :781-786 【改】显式定向且不可领取 → 抛明确错误；守护进程 / --all-ready → 保持静默返回 0
│   ├── src/backend/core/use_cases/agent_runner_workflow.py:57-83
│   │   └── 【改】首次领取补真 CAS：read → check → write → reread，以认领评论 marker 作仲裁见证
│   ├── src/backend/core/use_cases/agent_runner_issue_handlers.py:597-608
│   │   └── 【改】领取序列改为「发认领评论 → 切 running → 回读确认自己的 marker 最早」；不是则释放并退出
│   ├── src/backend/api/cli_parsed_commands/runner.py
│   │   ├── 【新增】CliError：CONFLICT（被他人持有 / blocked 无解除标记）· NOT_FOUND（target 不存在）
│   │   └── :146-165 【改】daemon mutex 收窄：仅在本次会轮询队列（--all-ready / 无显式 target）时执行
│   │       find_live_daemon_pid 检查；显式单目标（--issue N / PRD 路径）不再触发，与守护进程共存
│   └── 复用而非新造：照抄 agent_runner_reclaim.py:250 已验证的 read-check-write-reread 模式与 marker 工具
├── 复用而非改动（零改动清单）
│   ├── agent_runner_reclaim.py                    跨机领取协议（不改）
│   ├── run_verifier_agent.py                      意图来源已是 Issue body（不改）
│   ├── agent_runner_feedback.py:452 等            无 PRD 的提前返回（既有行为，不新增分支）
│   └── create_issue_from_prd.py                   建 Issue / 标签 / 依赖宣告（复用）
│       （注意：`agent_runner_orchestration_runtime.py:679` 的就绪判定**本次要改**——显式定向不再要求
│        `agent/ready`，见第五步；守护进程侧判定不变，仅显式分支放开）
├── CLI 表面与随包 skill 同步义务（AGENTS.md 硬规则：改 CLI 表面 / 退出码 / 机器输出必须同步）
│   ├── src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md
│   │   ├── 【新增旗标】--from-prompt、--require-validation（issue create）；--direct-pr（run）
│   │   ├── 【改写行为描述】删除/收窄所有「iar run never runs while a daemon serves the same
│   │   │   repository」类**绝对**表述（现文第 25、103、106 行）→ 改为「只有队列轮询
│   │   │   （--all-ready / 无显式 target）与守护进程互斥；显式单目标共存，同目标冲突
│   │   │   由认领状态（CAS）拒绝」。**这是行为反转，不是加一句话。**
│   │   ├── 【改写】agent/ready 行（现文第 76 行）：说明它只约束守护进程的自主挑选，
│   │   │   人的显式定向不受约束（现状写成「Queued and eligible for the next pass」的绝对式）
│   │   ├── 【补充】exit code 5（conflict）说明（现文第 50 行）加入「显式 target 不可领取
│   │   │   （被他人持有 / blocked 无解除标记）」；并补 NOT_FOUND(3) 的显式 target 用法
│   │   ├── 【新增】在 --fast-merge 段落旁补 --direct-pr 的差异（后者连 reviewer 与仓库验证一起跳）
│   │   └── 【顺带去重】现文第 103/106 行、105/107 行是逐字重复段，改写时一并去重
│   ├── tests/test_iar_operator_skill.py（_ALLOWED_FLAGS 登记全部新旗标；**新增断言：SKILL.md 不得
│   │   残留旧的绝对表述**，如「never runs while a daemon serves」）
│   └── docs/（agent-runner.md 记录新入口 + 无 PRD 执行语义 + **daemon 共存语义**；过 mkdocs --strict）
└── 测试
    ├── tests/test_issue_create_from_prompt.py（新建：互斥校验 / 正文无锚点 / 工作区零 diff / --ready 语义）
    └── 既有 create_issue_from_prd 测试逐字不变作为回归基线
```

### Risk Classification Register

| 变更点 | 层级 | 风险档 | 决定性维度 | 介入方式 | oracle / 门禁 |
|---|---|---|---|---|---|
| **撤销「无 PRD 不得执行」产品边界** | 文档/产品 | **R2** | 产品对外承诺收窄：失去「先对齐验收标准再开工」这道闸，且「描述不清必须反问」失去强制力 | **人工确认**（§2 待拍板） | rv-3：路线图全文一致性检查（不残留旧禁令）+ §2 人审确认 |
| 证据门禁在无 PRD Issue 上整体关闭 | core | **R2** | **连前端改动也不要求截图**；质量锚消失，判断依赖人事后看 PR | **人工确认**（§2 同一项）+ 显式披露 | rv-4：显式验证「默认关闭 / 加旗标开启」两态，且在文档中披露前端截图的含义 |
| 既有路径从未真实验证 | core | **R2** | 若撞到代码阅读未发现的硬依赖，本次范围会变化；「能跑」目前只有单测与阅读支撑 | 执行器 + 真实端到端证据 | **rv-1（本次首要 oracle）**：手工无 PRD Issue 端到端跑出 Draft PR |
| **新增 `--direct-pr` 档位（跳过全部门禁与第二个 agent）** | api/core | **R2** | **runner 侧质量检查归零**：连仓库自己的测试命令都不跑，可推出连测试都不过的 PR；唯一门禁转移到 PR 上的 CI（若该仓库 CI 不覆盖改动则完全无把关） | **人工确认**（§2 决策二） | rv-7：`--direct-pr` 下确无第二个 agent 调用、确不跑验证命令、且 PR 仍被创建 |
| `--direct-pr` 的前置拒绝（仅无 PRD 锚点） | api | R1 | 若判定写错，会让 PRD-backed Issue 被旁路掉交付门与归档 | 执行器 + 自动门禁 | rv-7：PRD-backed Issue 下 `--direct-pr` 必须被拒且错误信息指向 `--fast-merge` |
| 发布档位字段重构（替代第二个布尔） | api/core | R1 | 触碰既有 `fast_merge` 的 ~9 文件穿透链；重构若改变其外部行为即回退 | 执行器 + 强门禁 | rv-5：`--fast-merge` 的 CLI 表面、退出码、PR 标注与行为逐项不变（含既有 test_agent_runner_fast_merge.py 全绿） |
| **首次领取补真 CAS（并发正确性）** | core | **R3** | 显式定向不再要求标记后，领取是**唯一**防双跑机制；写错会双跑同一 Issue（两个 agent 同时改同一分支），或反过来永久卡住 Issue（无人能领） | **人工确认 + 可执行负控** | rv-9：真实双进程并发领取同一 Issue，断言只有一个赢、另一个主动退出、且 Issue 未被卡死 |
| 显式定向不再要求就绪标记 | core/api | R2 | 准入规则变更：守护进程侧不变、显式侧放开；误写会让守护进程的自主挑选失控（把无标记 Issue 也领了） | 执行器 + 自动门禁 | rv-8：定向不打标记直接执行；**守护进程对无标记 Issue 仍不领** |
| 显式定向到不可领取状态 → 明确错误 | core/api | R1 | 改动退出码语义（0 → 5/3）；若判定写宽会把守护进程的空轮询变成报错刷屏 | 执行器 + 自动门禁 | rv-8：被他人持有 → CONFLICT；blocked 无解除标记 → 拒绝；守护进程与 `--all-ready` 空队列仍返回 0 且静默 |
| **daemon mutex 收窄为只挡队列轮询** | api | **R2** | 移除一层既有互斥保护：显式 run 与守护进程共存后，同目标的排他**完全**落在 CAS/认领状态上（故必须与 rv-9 同批或更晚落地）；若 mutex 收窄写错，可能让队列轮询与守护进程重复领取 | 执行器 + 自动门禁 + 真实双进程验证 | rv-10：显式单目标与守护进程共存；同目标冲突由认领状态判定；`--all-ready` 仍被 mutex 拦 |
| `prd_paths` 由必填改为二选一 | api | R1 | 既有调用方若依赖「不传参数必报 usage error」，文案会变（退出码不变） | 执行器 + 自动门禁 | rv-5：不传新旗标时 PRD 路径行为逐项不变；两者都不给退出码仍为 2 |
| 新增内容生成目标（agent 生成正文） | engines | R1 | agent 生成失败必须有回退，否则建 Issue 直接失败 | 执行器 + 自动门禁 | rv-2：生成失败回退模板仍建出 Issue |
| 复用建 Issue 链路而非另写 | core | R1 | 另写第二条路径会让标签/依赖/`--type` 语义漂移 | 执行器 + 自动门禁 | rv-2 同批核验 |
| 引入新旗标 / 行为反转的 CLI 表面漂移 | engines | R1 | 随包 skill 与守卫白名单未同步会导致 agent 侧知识漂移；**本次尤甚**：SKILL.md 现有「`iar run` never runs while a daemon serves」是**绝对表述**，行为反转后若只加旗标不改它，agent 会继续按旧行为决策（本会话的 memory 已记录该段是 skill 的决策依据） | 执行器 + 守卫测试 + 全文检索 | rv-5：守卫测试绿、`iar schema --json` 列出新旗标；**rv-11**：SKILL.md/docs 不残留旧的绝对表述 |
| 路线图内部自相矛盾 | 文档 | R1 | 只改一处会留下其他章节仍声称强制 PRD | 执行器 + 一致性检查 | rv-3：全文检索不得残留旧表述 |

`R3` 档有一处：**首次领取补真 CAS**（并发正确性 —— 领取是防双跑的唯一机制）。`R2` 档有六处：两处是同一项产品取舍（撤销边界及其质量后果），一处是**既有路径从未被真实验证**这个事实风险，一处是 `--direct-pr` 把 runner 侧质量检查归零（§2 决策二），一处是准入规则变更（显式定向不再要求就绪标记），一处是**daemon mutex 收窄**（移除一层既有互斥，排他全部落到 CAS 上，故与 CAS 同批约束）。无 schema/迁移、无安全边界、无破坏性数据操作。

### Executor Drift Guard

- **本文列出的文件是起点，不是全集。** 用下面这些检索确认没有漏掉隐藏引用：

```bash
# 无 PRD 的降级分支是否真的完整（这些是既有行为，本次不得新增分支）
rg -n 'extract_prd_path|validation_required' src/backend/core/use_cases

# 确认没有任何代码在阻止无 PRD 执行
rg -n 'needs-triage|needs-prd|intake|requires_prd' src/backend

# 就绪准入只改显式侧：守护进程分支须保留 labels.ready 判定，显式分支放开
rg -n 'labels.ready' src/backend/core/use_cases/agent_runner_orchestration_runtime.py

# daemon mutex 只收窄到队列轮询：--all-ready / 无 target 路径仍须走 find_live_daemon_pid
rg -n 'find_live_daemon_pid|daemon_lock_dir' src/backend/api/cli_parsed_commands/runner.py

# 跨机领取与 verifier 意图来源必须零改动
git diff --stat -- src/backend/core/use_cases/agent_runner_reclaim.py
git diff --stat -- src/backend/core/use_cases/run_verifier_agent.py

# --fast-merge 的外部行为必须零改动（重构为发布档位后仍须逐项一致）
rg -n 'fast_merge|fast-merge' src/backend tests
uv run pytest tests/test_agent_runner_fast_merge.py -q -o addopts=""

# --direct-pr 的跳过点必须成对出现（新增旗标要照 fast_merge 的穿透链逐处补齐）
rg -n 'pre_pr_review|run_pre_pr_review' src/backend
rg -n 'verification_commands|pre_commit_verification_command' src/backend/core/use_cases

# 内容生成目标的完整注册链（新增目标要照它逐处补齐）
rg -n 'issue_from_prd|prd_from_issue' src/backend

# 路线图一致性：不得残留「必须先有 PRD 才能执行」类表述
rg -n '不得被 runner|不允许直接执行|只能进入需求澄清' ROADMAP.md

# 行数红线（PR #212 已清零，不得重新推过 1000）
uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend

# CLI 表面守卫白名单（新旗标必须登记）
rg -n '_ALLOWED_FLAGS' tests/test_iar_operator_skill.py

# 随包 skill / docs 不得残留与本次行为反转相反的绝对表述（必须改写而非追加）
rg -n 'never runs while a daemon serves|refuses while it is alive|Queued and eligible for the next pass' src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md docs/guides/agent-runner.md
```

- **不要给无 PRD 执行加配置开关。** 代码里从来没有这条限制，加开关等于为一个不存在的门禁引入状态空间。
- **不要改 `--fast-merge` 的任何外部行为。** 把 `fast_merge` 布尔重构为发布档位只是为了承载 `--direct-pr` 的嵌套语义；`--fast-merge` 的 CLI 表面、退出码、PR 标注、阶段跳过范围必须逐项不变，既有 `tests/test_agent_runner_fast_merge.py` 必须全绿。
- **不要让 `--direct-pr` 作用于 PRD-backed Issue。** 那会旁路 PRD 交付门与归档。前置检查是 fail-closed 的。
- **不要动 commit proxy。** 「只保留机械步骤」指的是 commit proxy + push + 建 PR；agent 仍不得直接 `git add` / `git commit`。
- **就绪准入只改显式侧，不要改守护进程侧。** 守护进程仍只领取带 `agent/ready` 的 Issue（`agent_runner_orchestration_runtime.py:679` 的守护进程分支不动）——路线图「不会自主决定处理哪些 issue」那条边界与 PRD 无关；只放开「人的显式点名」（FR-6）。别把这条放宽成「守护进程也能领无标记 Issue」。
- **daemon mutex 只收窄到队列轮询，不要整个拿掉。** `--all-ready` / 无 target 的轮询仍必须被互斥拦住（它才会和守护进程重复领取 ready 队列，FR-24）；被拿掉后 rv-10 的第 ③ 条会失败。
- **随包 skill 的同步是「改行为表述」不是「加旗标」。** SKILL.md 现有绝对表述（「`iar run` never runs while a daemon serves the same repository」、`agent/ready` 的「Queued and eligible」）在本次行为反转后**必须改写**；只加新旗标而留着旧句子，agent 会按旧行为决策。判据见 rv-11（不残留相反表述）。
- **不要改 `agent_runner_reclaim.py` 与 `run_verifier_agent.py`。** 跨机领取协议不动；verifier 的意图来源已经是 Issue body（正是想要的形态）。
- **不要给运行时门禁写新分支。** 无 PRD 的降级行为全部已实现；若发现某处确实缺分支，先更新本 PRD 说明再改。
- **不要改 `src/backend/core/use_cases/generated_content.py`**（历史红线）。新目标走配置字段 + 独立解析器。
- **不要另写第二条建 Issue 路径。** 复用 `create_issue_from_prd` 的建 Issue / 标签 / 依赖宣告部分。
- **不要删 M8 或澄清能力。** 只把「强制前置」改为「按需使用」。
- **不要生成 PRD 文件。** 用 `--from-prompt` 时工作区必须零 diff。
- **路线图改动要全文一致。** 只改 Product Boundary 会在 Target Workflow / M8 / 验收项留下矛盾表述；按 §7 Change Impact Tree 列出的章节逐个检查。
- **失败排查**：若手工 Issue 跑不起来，先确认它真的带 `agent/ready`（`gh issue view <N> --json labels`）—— 这是唯一的准入条件，且是静默跳过而非报错；若跑起来但 agent 说找不到需求，检查 Issue 正文是否为空或过于含糊（正文就是唯一需求来源，没有 PRD 可以兜底）；若 `--ready` 后本机没领走，同样先查标签；若 agent 生成正文超时，确认回退模板路径可用。
- **提交信息用英文 Conventional Commits**（`docs/ai-standards/tooling.md`）。
- 本仓根使用单一 pnpm lockfile。

### Flow Diagram

见上方 Core Logic 的三步数据流图（证明既有路径 → 撤销产品边界 → 增加创建入口）。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 一个手工建的、无 PRD 锚点的 Issue 能被真实执行并端到端产出 Draft PR
  reviewer: human
  real_entry: "真实入口：手工建 GitHub Issue（自由文本正文，无 PRD path 锚点）→ gh issue edit --add-label agent/ready → 真实 iar run --issue N"
  expected: 全链路无中止；产出 Draft PR；PR 正文合理反映该 Issue 的需求；Issue 正文被当作需求来源（verifier 从中取意图）；账本记录该 run 的 prd_path 为空串；证据目录若产生则落在 issue-<N>
  mock_boundary: none —— 真实 GitHub Issue + 真实 CLI 进程 + 真实账本 + 真实浏览器（若涉及前端）
  tier: R2
  test_layer: 真实端到端执行
  required_for_acceptance: true
  presentation: "该 Issue 的 URL、其 Draft PR 的 URL、以及执行日志中「无 PRD」路径被走到的关键行截图"
  must_cross: 真实 GitHub + 真实 CLI + 真实账本
  forbidden_bypasses: 用 fixture Issue 替代真实 Issue；直接调 use_case 绕过 CLI；用有 PRD 锚点的 Issue 冒充
  fresh_state_probe: 采集前重新 gh issue view 确认该 Issue 仍无 PRD 锚点、且标签状态与实际一致
  final_tree_evidence: 执行时的 commit SHA 与 git tree 记录在证据报告
  negative_control: 把同一个 Issue 的 PRD 锚点人为加上（指向不存在的 PRD 文件），执行必须转为失败的 PRD 交付门 —— 证明「无锚点」是本次绿色结果的实际成因，而非该门禁被整体关掉
  expected_fail: 加锚点后 PRD 交付门报错，run 不产出 PR

- id: rv-2
  behavior: 一句话建出的 Issue 不含 PRD 指针、工作区零 diff、生成失败时可回退
  reviewer: verifier
  real_entry: "真实 CLI：iar issue create --from-prompt \"<需求>\"，随后读回真实 GitHub Issue 与 git status；另注入内容生成失败"
  expected: Issue body 无 `PRD path:` 与 `Canonical PRD` 锚点；git status 干净；生成失败时回退确定性模板仍能建出可用 Issue
  mock_boundary: 仅内容生成器的失败注入在测试夹具
  tier: R1
  test_layer: 真实 CLI + 远端 Issue 读回 + 工作区 diff 断言 + 失败路径测试
  required_for_acceptance: true
  negative_control: 临时让正文生成器写入一个 `PRD path:` 锚点，断言必须失败
  expected_fail: 正文含锚点时断言失败

- id: rv-3
  behavior: 路线图全文与实现对齐，不再声称存在「无 PRD 不得执行」的强制链
  reviewer: human
  real_entry: "真实文件：ROADMAP.md 全文一致性检查"
  expected: Product Boundary 允许无 PRD 执行（仍需就绪标记）；Target Workflow / M1 / M8 / 验收项 / Not Completed / Open Questions 与之一致，不存在仍声称「必须先有 PRD 才能执行」的表述；M8 保留为按需能力而非强制前置
  mock_boundary: none
  tier: R2
  test_layer: 全文检索 + 人读
  required_for_acceptance: true
  negative_control: 临时把 Product Boundary 改回旧禁令，一致性检查必须报出矛盾
  expected_fail: 存在相互矛盾的表述时检查失败

- id: rv-4
  behavior: 无验收要求的 Issue 确定性地不要求证据；显式要求时确定性要求证据（含前端截图）
  reviewer: verifier
  real_entry: "真实端到端两种调用 + 运行时门禁观察"
  expected: 默认路径下 validation_required(issue_body) 为 False，且不要求证据文件（含前端改动不要求截图）；加 --require-validation 后为 True 且要求证据（前端改动时要求截图）
  mock_boundary: none
  tier: R2
  test_layer: 真实运行时门禁判定对照（含前端改动的两态对照）
  required_for_acceptance: true
  negative_control: 临时让默认路径也生成验收段，默认态断言必须失败
  expected_fail: 默认态下 validation_required 为 True 时断言失败

- id: rv-5
  behavior: 既有 PRD 路径行为逐字不变；两者都不给仍是 usage error 退出码 2；新旗标已同步 skill 与守卫白名单
  reviewer: verifier
  real_entry: "真实 CLI：改动前后跑同一组 PRD 路径命令做差分；tests/test_iar_operator_skill.py；iar schema --json"
  expected: PRD 路径命令的产物与门禁行为逐项相同；两者都不给时退出码为 2（文案允许变化）；--from-prompt 下显式 --publish-prd/--force 报 usage error；守卫测试绿；schema 列出新旗标
  mock_boundary: none
  tier: R1
  test_layer: CLI 差分断言 + 守卫测试 + schema 契约测试
  required_for_acceptance: true
  negative_control: 从 _ALLOWED_FLAGS 移除新旗标后守卫测试必须失败
  expected_fail: skill 未同步时守卫测试失败

- id: rv-6
  behavior: 跨机归还协议（stale claim 回收）未被本次改动影响
  reviewer: verifier
  real_entry: "源码 diff 断言 + 既有 reclaim 测试"
  expected: agent_runner_reclaim.py 无行为性 diff hunk（本次只在别处补首次领取的 CAS，不重写归还路径）；既有 reclaim 相关测试全绿；死进程持有的 running claim 仍能被归还为 ready 并保留已提交进度
  mock_boundary: none
  tier: R1
  test_layer: diff 静态断言 + 既有 reclaim 测试
  required_for_acceptance: true
  negative_control: 临时破坏归还路径（改成不释放 label），既有 reclaim 测试必须失败
  expected_fail: 归还路径被破坏时测试失败

- id: rv-7
  behavior: --direct-pr 下执行 agent 结束后只剩机械步骤——无第二个 agent 调用、不跑 runner 验证命令、仍创建 Draft PR；且该档位被正确限制在无 PRD 锚点的 Issue
  reviewer: human
  real_entry: "真实入口：对一个无 PRD 锚点的 Issue 跑 iar run --issue N --direct-pr；对照同一 Issue 跑 --fast-merge"
  expected: --direct-pr 运行的日志与产物中不出现 pre-PR review 阶段、不出现 runner 验证命令执行、不出现 verifier/rv_reexec；Draft PR 被创建且正文带 direct 档位的机器可读标注；与 --fast-merge 对照可见后者仍执行 pre-PR review；对带 PRD 锚点的 Issue 跑 --direct-pr 被拒绝（usage error，提示改用 --fast-merge）；同时给出两个旗标被拒绝
  mock_boundary: none —— 真实 CLI 进程 + 真实 GitHub Issue + 真实 PR
  tier: R2
  test_layer: 真实端到端执行 + 阶段级对照
  required_for_acceptance: true
  presentation: "两条运行日志的阶段序列对照（--direct-pr vs --fast-merge），标明 --direct-pr 侧缺少 pre-PR review 与验证命令段落；以及两个被拒场景的 CLI 错误输出"
  must_cross: 真实 CLI + 真实 GitHub + 真实 PR 正文
  forbidden_bypasses: 用 fixture 或直接调 use_case 绕过 CLI；只看代码不看实际运行日志
  fresh_state_probe: 采集前重新确认该 Issue 无 PRD 锚点且带就绪标记
  final_tree_evidence: 两次运行的 commit SHA 与 git tree 记录在证据报告
  negative_control: 临时让 --fast-merge 也跳过 pre-PR review，则「两者可区分」的对照断言必须失败（证明对照不是恒真）
  expected_fail: 两档位行为不可区分时对照失败

- id: rv-8
  behavior: 显式定向不受就绪标记约束（不打标记直接执行）；守护进程侧不变（无标记不领）；定向到不可领取状态时报明确错误
  reviewer: verifier
  real_entry: "真实 CLI：① 对一个无任何 workflow 标签的真实 Issue 跑 iar run --issue N ② 同一 Issue 交给守护进程轮询一轮 ③ 定向到一个已被活跃认领的 Issue"
  expected: ① 直接执行（不要求就绪标记、不报错）② 守护进程**不领取**该 Issue（仅 INFO 日志、退出 0）③ 被活跃认领 → 非零退出（CONFLICT）并指出持有者 host/PID；blocked 且无解除标记 → 拒绝并提示 iar blocked-continue；--all-ready 空队列仍返回 0 且静默
  mock_boundary: none —— 真实 CLI 进程 + 真实 GitHub Issue
  tier: R2
  test_layer: 真实 CLI 与守护进程行为对照
  required_for_acceptance: true
  negative_control: 临时让守护进程也领取无标记的 Issue，则 ② 的断言必须失败
  expected_fail: 守护进程越过就绪标记自主挑选时断言失败

- id: rv-9
  behavior: 首次领取是真 CAS —— 两个并发领取者只有一个赢、另一个主动退出、Issue 不被卡死
  reviewer: human
  real_entry: "真实入口：对一个已就绪的真实 Issue 同时启动两个真实 iar run 进程（或用两个真实 host 身份），观察谁真正进入执行"
  expected: 只有一个进程进入 Phase 1 执行；另一个检测到自己不是最早认领者并主动退出（不进入执行）；Issue 最终为 agent/running 且认领标记指向赢家；赢家跑完后 Issue 正常流转（未卡在 running）
  mock_boundary: none —— 两个真实进程 + 真实 GitHub
  tier: R3
  test_layer: 真实并发双进程抢夺
  required_for_acceptance: true
  presentation: "两个进程的输出与 Issue 最终标签/认领评论的截图，标明哪个赢了、另一个以什么信息退出"
  must_cross: 真实 GitHub 标签 + 真实认领评论 + 两个真实进程
  forbidden_bypasses: 单进程顺序调用模拟并发；mock 掉 GitHub 客户端
  fresh_state_probe: 采集前确认该 Issue 处于可领取状态（未被他人持有）
  final_tree_evidence: 采集时的 commit SHA 与 git tree 记录在证据报告
  negative_control: 临时去掉 CAS 的回读校验（退回裸 write），并发断言必须失败（两个都进入执行，或都以为自己赢了）
  expected_fail: 无 CAS 时两个进程都进入执行，断言失败

- id: rv-10
  behavior: daemon mutex 只挡队列轮询；显式单目标 run 与正在跑的守护进程共存；同目标冲突由认领状态而非 mutex 判定
  reviewer: verifier
  real_entry: "真实入口：本机为一个仓库真实启动 iar daemon（守护进程持有单实例锁），随后 ① 对另一个 Issue 显式 iar run --issue M ② 对守护进程当前正持有的 Issue 显式 iar run --issue N ③ 显式 iar run --all-ready"
  expected: ① 不报「daemon already running」而直接执行（与守护进程并存，各用各的 issue-{M} 工作树）② 拒绝并给 CONFLICT，错误信息指出当前持有者 host/PID（依据认领状态，非 mutex）③ 仍被 mutex 拒绝（队列轮询才会重复领取 ready 队列）
  mock_boundary: none —— 真实 daemon 进程 + 真实 CLI 进程 + 真实 GitHub Issue + 真实文件系统锁
  tier: R1
  test_layer: 真实 daemon + 真实 CLI 行为对照 + 锁文件观察
  required_for_acceptance: true
  negative_control: 临时把 mutex 完全移除（连 --all-ready 也不挡），则 ③ 的断言必须失败（队列轮询与守护进程发生重复领取）
  expected_fail: 队列轮询不再被 mutex 拦截时断言失败

- id: rv-11
  behavior: 随包 iar-operator skill 与 docs 已随本次「行为反转」同步，不再残留与本次相反的绝对表述
  reviewer: verifier
  real_entry: "真实文件：src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md 与 docs/guides/agent-runner.md 全文检索 + tests/test_iar_operator_skill.py + iar schema --json"
  expected: SKILL.md 不再出现「iar run never runs while a daemon serves the same repository」这类绝对表述（改为只对队列轮询成立，显式单目标共存）；agent/ready 行说明它只约束守护进程、显式定向不受约束；exit code 5 说明含「显式 target 不可领取」；新旗标（--from-prompt / --direct-pr / --require-validation）出现在命令表与 _ALLOWED_FLAGS；docs/guides/agent-runner.md 同步 daemon 共存语义；iar schema --json 列出新旗标
  mock_boundary: none —— 真实 skill/docs 文件与真实 schema 输出
  tier: R1
  test_layer: 真实文件检索 + 守卫测试 + schema 契约
  required_for_acceptance: true
  negative_control: 临时把 SKILL.md 改回「iar run never runs while a daemon serves」旧绝对表述，检索断言必须失败
  expected_fail: skill 残留旧绝对表述时断言失败
```

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - PR #212（已合并 `866ddabc`）—— 行数红线清零；本 PRD 的 `--from-prompt` 要改 CLI parser，依赖它已清零的余量
- Gate type: none
- Sequence: via-main
- Notes: 上游已满足。**本 PRD 与既有 PRD 的显著差异：运行时不产生功能改动** —— 逐条核查确认所有 PRD 相关门禁都是条件生效、无 PRD 的降级路径均已实现（§5 表），全仓也搜不到任何阻止无 PRD 执行的代码。因此本次的执行侧工作分为「证明既有路径真实可跑」（零代码，产出端到端证据，此前从未验证过）、「撤销文档中禁止它的产品边界」、以及「增加一个把一句话变成 Issue 的创建入口」三部分。

## 9. Acceptance Checklist

### 9.1 人读呈递区 (Human Review Surface)

| 看什么 | 呈递物 | ~10 秒自检 |
|---|---|---|
| **手工建的、没有 PRD 的 Issue 真的能跑出 PR** | 真实 URL：Issue <https://github.com/ZataZhang/keda/issues/216> · Draft PR <https://github.com/ZataZhang/keda/pull/217><br>执行日志关键行（原文，非截图）：`.iar/evidence/rv-1-no-prd-issue-e2e-run.txt`，完整日志 `.iar/evidence/rv-1-run-216.log`<br>负向对照（人为加锚点后必须失败）：`.iar/evidence/rv-1-negative-control.txt` | 打开 Issue，确认它**没有** `PRD path:` 字样；打开 PR，确认它是 `isDraft=true`、`head=issue-216`、正文无旁路标注 |
| 一句话能建出 Issue，且正文是人话、无 PRD 指针 | 三条真实 Issue 正文：<https://github.com/ZataZhang/keda/issues/220> · <https://github.com/ZataZhang/keda/issues/222> · <https://github.com/ZataZhang/keda/issues/224>（最后一条走的是生成失败后的模板回退）<br>断言与差分：`.iar/evidence/rv-2-from-prompt.txt` | 看正文：应是一段说得清「要什么」的人话；**不应**出现 `Canonical PRD` 或 `PRD path:` |
| 路线图不再禁止无 PRD 执行，且全文无矛盾 | 路线图改动 diff：`open ".iar/evidence/rv-3-roadmap-diff.txt"`；一致性检查：`open ".iar/evidence/rv-3-roadmap-consistency.txt"` | 看 diff：Product Boundary 那条应从「禁止执行」变为「允许，由就绪标记决定」；全文检索不应再有「不得被 runner 当作可执行」（同一判据扫改动前版本命中 5 次，扫现版本命中 0 次） |
| 就绪标记是**守护进程**的唯一准入、且语义被如实呈现 | ① 未就绪 Issue 的队列侧不领：`.iar/evidence/rv-8-targeted-admission.txt`（INFO + 退出 0）② 真实 daemon 整轮未启动任何 agent，以及已就绪 Issue 被**实际在跑的那台**领走且认领标记含其 host/PID：`.iar/evidence/rv-10-daemon-mutex-scope.txt` 与 `.iar/evidence/rv-9-first-claim-cas.txt`（真实线程见 <https://github.com/ZataZhang/keda/issues/218>） | 看两份日志：未就绪那份没人动它；已就绪那份的认领标记 host 是实际跑 daemon 的机器。**注意：人显式定向不受该标记约束**，这不是矛盾。**跨机为单机等效，限制见 §12** |
| **`--direct-pr` 真的只剩机械步骤** | 阶段序列对照：`open ".iar/evidence/rv-7-direct-vs-fast-stages.txt"`（真实日志 `rv-7-run-218-direct.log` / `rv-7-run-220-fastmerge.log`）；两条真实 PR：<https://github.com/ZataZhang/keda/pull/221>（direct）· <https://github.com/ZataZhang/keda/pull/225>（fast）<br>被拒的三个真实调用：`open ".iar/evidence/rv-7-rejections.log"` | 打开对照：`--direct-pr` 那侧**不应**出现 pre-PR review、verifier、rv_reexec、验证命令这些段落；`--fast-merge` 那侧应仍有 pre-PR review（实测 direct 全档只有 1 个 agent 会话） |

> 验证层级说明：以上均为**真实入口** —— Issue 与 PR 都是 GitHub 上真实存在的，路线图 diff 来自真实文件，跨机领取是真实守护进程的认领行为。都不是模拟或声明。
>
> **呈递物形态**：本条无截图（本次改动没有图形界面变化，证据面是真实 URL + 真实运行日志 + 文件 diff）；§9.1 原稿点名的 `.png` 已按实替换为上述 URL 与日志文件。逐条证据的实名映射见 `.iar/evidence/evidence.json`；本地一页呈递：`open ".iar/evidence/human-review-bundle.html"`（在 gitignored 的 `.iar/` 下，属**本地呈递物**；跨机可看的持久呈递面是各项 GitHub 直链）。
>
> 以下 `reviewer: verifier` 组**刻意不在上方呈递**，只在失败时需要人工介入：rv-2（工作区零 diff + 生成失败回退）、rv-4（证据门禁两态对照）、rv-5（既有 PRD 路径差分 + 守卫测试）、rv-6（跨机归还协议零改动）、rv-8（显式定向准入与报错）、rv-10（daemon mutex 共存）、rv-11（随包 skill 与 docs 行为同步）。

### 9.2 Acceptance Evidence Package

**Human-Confirmed (来自 Part A 风险地图)**

- [ ] **§2 产品决策：接受撤销「无 PRD 的 Issue 不得被当作可执行任务领取」这条边界**，并知悉两个后果：(a) 这类 Issue 没有自动化质量锚 —— 证据门禁整体关闭，**连前端改动也不要求截图**；若同时用快速合并档，重验与独立 verifier 也旁路，质量判断依赖人事后看 Draft PR；(b) 路线图原「描述不清必须先反问用户」的规则失去强制力，执行方会在需求含糊时自行补全而非停下来问。已提供 `--require-validation` 旗标在需要时打开验证（默认关闭）。回答方式：「接受」或「我要改默认（把该旗标改为默认开启）」。
- [ ] **§2 决策二：接受新增 `--direct-pr` 档位**，并知悉其后果：(a) 该档位下 runner 侧不再有任何质量检查——**连仓库自己配置的测试命令都不跑**，因此可以推出一个连测试都不过的 PR；(b) 唯一门禁转移到 PR 上的 CI，**前提是该仓库的 CI 真的会在 PR 上跑并且真的会红**，若 CI 不覆盖改动则完全无把关；(c) `--fast-merge` 从此不是最快档位，两者语义差需自行记住。该档位被刻意限制为**只对没有 PRD 锚点的 Issue 有效**。回答方式：「接受」或「我不需要这个档位（改用 .iar.toml 持久关 pre_pr_review）」。
- [ ] **§2 决策二的边界确认：接受 `--direct-pr` **仅限无 PRD 锚点的 Issue**（PRD-backed 一律拒绝并提示改用 `--fast-merge`），以及它与 `--fast-merge` **互斥**（同时给出即报错）。回答方式：「接受」或指出你希望放宽/收紧的边界。
- [ ] **知悉本次含一处 R3（并发正确性）变更**：首次领取要从裸 read-modify-write 补成真 CAS。理由是「显式定向不再要求就绪标记」后，领取成为**唯一**防双跑机制；裸 write 下两个并发领取者可双双得逞（双跑同一 Issue）。这是既有缺陷的修复，但 CAS 若写错可能反向导致 Issue 永久卡住。回答方式：「知悉」或要求把首次领取的原子性单独拆成一个 PRD 先交付。
- [ ] **§2 决策三：接受「显式定向不再要求就绪标记」+「daemon mutex 收窄为只挡队列轮询」**，并知悉其后果：本机守护进程在跑时，显式 `iar run --issue M` 不再报错而直接执行（与守护进程共存，各用各的工作树）；**同目标的排他完全落在 CAS/认领状态上**（不由 mutex 保证），因此该收窄必须与首次领取 CAS 同批或更晚落地。`--takeover` 保留用于显式停守护进程。回答方式：「接受」或「先只做就绪准入、mutex 不动」。
- [ ] §1 行为样例表**全部行逐行确认**，尤其：第 1 行（**手工建的**无 PRD Issue 能跑）、就绪标记那三行（**未就绪时两个守护进程都不领**；已就绪由跑着的那台领走；换机器是部署事实而非 Issue 字段）、**daemon 共存那三行**（守护进程在跑时显式跑另一个 Issue 不报错；跑它正在持有的那个被拒；`--all-ready` 仍被拦）、以及 `--direct-pr` 那四行（只剩机械步骤；故意让测试失败仍出 PR 且 CI 变红；PRD-backed 被拒；两旗标互斥）。回答方式：对每一行回复「符合」或指出哪一行与预期不符。

**Architecture Acceptance**

- [x] 运行时**未新增任何功能分支**：无 PRD 的降级行为全部走既有实现（`agent_runner_feedback.py:452`、`agent_runner_validation.py:529`、`agent_runner_final_verification.py:76`、`agent_runner_pr_body_contract.py:145`）。
  ｜实测：`git diff main` 中四处降级点（feedback/validation/final_verification/pr_body_contract）只新增 `publish_stage` 派生判断，`git diff main -- …4 files | grep '^+.*if.*prd'` 命中 0；见 `rv-4-validation-toggle.txt`、`rv-7-direct-vs-fast-stages.txt`。
- [x] **就绪准入只改显式侧**：守护进程仍只领带 `agent/ready` 的 Issue（`agent_runner_orchestration_runtime.py:679` 的守护进程分支不变）；显式定向不再要求该标记。
  ｜实测：`agent_runner_orchestration_runtime.py` 的就绪过滤只在定向分支被替换（`config.labels.ready in …` → `has_non_ready_workflow_label(...)`），守护进程自主挑选分支无 diff；rv-8 负向侧确认无标记 Issue 队列不领。
- [x] `agent_runner_reclaim.py`（**归还**路径）与 `run_verifier_agent.py`（意图来源）均零改动；本次补的首次领取 CAS 落在 `agent_runner_workflow.py`，不重写归还路径。
  ｜实测：`git diff main -- agent_runner_reclaim.py run_verifier_agent.py generated_content.py` 输出为空；既有 reclaim 测试全绿（`rv-6-reclaim-intact.txt`）。
- [x] **准入规则只改了两处且互相关联**：① 显式定向不再要求 `agent/ready`；② daemon mutex 收窄为只挡队列轮询（`runner.py:146-165`）。守护进程侧的自主挑选（仍只领带标记的 Issue）未被改动。
  ｜实测：diff 面即 `runner.py` 的 `elif target_issue is None:` mutex 收窄分支 + 定向准入一处；见 `rv-10-daemon-mutex-scope.txt`（含移除锁判定的负控）与 `rv-8-targeted-admission.txt`。
- [x] **daemon mutex 收窄未削弱同目标排他**：显式 run 与守护进程对同一 Issue 的冲突仍会被拒绝（由认领状态/CAS 判定）；`--all-ready` 仍被 mutex 拦。
  ｜实测：daemon 持有 #222 时显式 run 同一 Issue → CONFLICT(5) 点名 PID；`--all-ready` → 5；对**另一个** Issue 显式 run 被接受（`rv-10-daemon-mutex-scope.txt`）。共存证据为 dry-run 级，已按窄口径披露。
- [x] `generated_content.py`（历史红线）未被改动。
  ｜实测：`git diff main -- …/generated_content.py` 为空。
- [x] 未另写第二条建 Issue 路径；`hooks/max_file_lines.allowlist.txt` 仍为空名单；无文件越过 1000 非空行。
  ｜实测：`hooks/max_file_lines.allowlist.txt` 去注释后 0 行；`check_max_file_lines.py` 对全部 41 个变更 .py 通过，唯一告警是既有 `tests/test_agent_runner_cli.py` 3771 非空行（main 已 3767，warn-only 非门禁）；新文件最大者 `create_issue_from_prd.py` 999 非空行（main 998，距上限 1 行，未越线）。
- [x] 依赖方向合法（`just lint` 架构检查通过）。
  ｜实测：`pre-commit run --files <41 个变更/新增 .py>` → 18 项 hooks 中 `Check architecture layer dependencies....Passed`，全部已执行项 Passed（`just lint` 对未暂存文件报 no files to check，故直接跑钩子本体）。

**Behavior Acceptance**

- [x] **rv-1 PASS（本次首要）**：手工建的、无 PRD 锚点的 Issue 端到端跑出 Draft PR；并已跑负向对照（人为加锚点后必须失败）。证据：`.iar/evidence/rv-1-no-prd-issue-e2e-run.txt`（负控 `rv-1-negative-control.txt`）
  ｜实测：#216（无锚点）→ Draft PR #217；负控 #219 加锚点后 PRD 交付门 1/5→2/5 且零交付物。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] rv-2 PASS：`--from-prompt` 建出的 Issue 无 PRD 锚点、工作区零 diff、生成失败可回退。证据：`rv-2-from-prompt.txt`
  ｜实测：#220/#222/#224 正文锚点计数 0、创建零工作区写入、模板回退成立。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] rv-3 PASS：路线图全文与实现对齐，无残留矛盾表述。证据：`rv-3-roadmap-diff.txt`
  ｜实测：现版本命中 0 / `git show main:ROADMAP.md` 命中 5。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] rv-4 PASS：无验收要求确定不要求证据（含前端不要求截图）；显式要求时确定要求证据（含截图）。证据：`rv-4-validation-toggle.txt`
  ｜实测：默认态 False 且不要求证据（含前端）；`--require-validation` 态抛 ValidationEvidenceError 且前端要图片。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] rv-5 PASS：既有 PRD 路径行为逐字不变；两者都不给退出码 2；新旗标已同步。证据：`rv-5-backward-compat.txt`
  ｜实测：`--fast-merge` 表面逐字相同、两者都不给仍 exit 2、守卫 11 项绿、schema 列三新旗标。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] rv-6 PASS：跨机归还协议未被破坏（`agent_runner_reclaim.py` 无行为性 diff；既有 reclaim 测试全绿）。证据：`rv-6-reclaim-intact.txt`
  ｜实测：`git diff main -- …/agent_runner_reclaim.py` 为空；破坏归还路径 3 failed。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] **rv-8 PASS**：显式定向不打就绪标记也直接执行；守护进程对无标记 Issue 仍不领取；定向到被他人活跃认领 / blocked 无解除标记 / 不存在时分别给出 CONFLICT / 拒绝并提示 / NOT_FOUND，不静默返回 0。证据：`rv-8-targeted-admission.txt`
  ｜实测：无标记显式定向准入、队列侧不领；blocked→5、不存在/已关闭→3、活跃认领→5 点名 PID。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] **rv-9 PASS（R3）**：真实双进程并发领取同一就绪 Issue 时只有一个进入执行、另一个主动退出、Issue 未被卡死；并已跑负控（退回裸 write 时断言必须失败）。证据：`.iar/evidence/rv-9-first-claim-cas.txt`
  ｜实测：#218 真实双进程一赢一撤（iar:claim-withdrawn）；#226 直调生产仲裁 WIN/LOSE；裸 write 负控两个都 WIN。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] **rv-10 PASS**：daemon mutex 只挡队列轮询 —— 本机守护进程在跑时，对**另一个** Issue 的显式 `iar run --issue M` 不报错而直接执行；对守护进程**当前持有**的 Issue 显式 run 仍被拒（认领状态判定）；`--all-ready` 仍被 mutex 拦。证据：`.iar/evidence/rv-10-daemon-mutex-scope.txt`
  ｜实测：daemon 存活时 --all-ready→5 点名 PID、显式 --issue 219 被接受、被持有的 #222→5 点名 PID；移除锁判定后 --all-ready 变 0。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] **rv-11 PASS**：随包 `iar-operator/SKILL.md` 与 `docs/guides/agent-runner.md` 已随行为反转同步 —— 不残留「iar run never runs while a daemon serves」类绝对表述；新旗标已进命令表与 `_ALLOWED_FLAGS`；`iar schema --json` 列出新旗标；并已跑负控（改回旧绝对表述则检索断言失败）。证据：`.iar/evidence/rv-11-skill-docs-sync.txt`
  ｜实测：旧绝对表述命中 0（旧文本会被同一判据拒）、三旗标在 skill/docs/schema 三处齐备。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。
- [x] **rv-7 PASS**：`--direct-pr` 下确无第二个 agent 调用、确不跑 runner 验证命令、仍创建 Draft PR；与 `--fast-merge` 的阶段序列可区分；PRD-backed Issue 被拒；两旗标互斥。证据：`.iar/evidence/rv-7-direct-vs-fast-stages.txt`
  ｜实测：direct=#218→PR #221，fast=#220→PR #225；三处 usage error 均为 exit 2。 负控与范围收窄见 `.iar/evidence/evidence.json` 对应块。

**Documentation Acceptance**

- [x] `ROADMAP.md` 的 Product Boundary / Target Workflow / M1 / M8 / Not Completed / Near-Term / Acceptance Checklist / Open Questions 已按 §7 Change Impact Tree 逐节检查并保持一致；M8 改为按需能力而非强制前置。
  ｜实测：逐节核对并改写；残留旧表述检索命中 0，`git show main:ROADMAP.md` 同一判据命中 5（`rv-3-roadmap-diff.txt`）。
- [x] `docs/guides/agent-runner.md` 记录「无 PRD 的 Issue 可执行」的语义与 `--from-prompt` 用法，并**准确描述就绪标记**（它是「要不要现在进队列」，不是机器定向；未就绪时任何守护进程都不会领）。
  ｜实测：第 11 行 `--from-prompt` 语义、1177 行「`agent/ready` 只约束守护进程的自主挑选」、1218-1230 行 `--direct-pr` 边界与互斥；均写明「未就绪时任何守护进程都不领」「不存在指派到某台机器的机制」。
- [x] **随包 `iar-operator/SKILL.md` 已随行为反转同步**：命令表新增旗标；**改写**（非追加）「`iar run` never runs while a daemon serves」类绝对表述为「只对队列轮询成立、显式单目标与守护进程共存」；`agent/ready` 行改为「只约束守护进程」；exit code 5 说明补入「显式 target 不可领取」。**判据是「不残留相反表述」，不是「加了新句子」。**
  ｜实测：第 16/23/24/25/50/76 行改写完毕——旧绝对表述 `never runs while a daemon serves the same repository` 命中 0，队列轮询与显式单目标共存表述在位，exit 5 说明含「显式 target 不可领取」（`rv-11-skill-docs-sync.txt`）。
- [x] `tests/test_iar_operator_skill.py` 的 `_ALLOWED_FLAGS` 登记全部新旗标，且含有「SKILL.md 不得残留旧绝对表述」的断言。
  ｜实测：`--from-prompt` / `--direct-pr` / `--require-validation` 均已登记；新增断言 `test_packaged_skill_drops_retired_absolute_claims()`（第 252 行）在改回旧表述时必须失败——已跑该负控。
- [x] `uv run mkdocs build --strict` 通过。
  ｜实测：`uv run mkdocs build --strict` EXIT=0。

**Validation Acceptance**

- [x] **rv-1 是本次的核心证据**：它是「任意 Issue 可执行」这条能力的首次真实验证（此前仅有单元测试与代码阅读支撑，账本中 29 条空 `prd_path` 记录全部来自测试夹具）。
- [x] 真实入口验证已执行：rv-1 / rv-4 走真实 CLI + 真实 GitHub；rv-6 走真实 CLI 观察。
- [x] 跨机行为已按**准确语义**验证：① 未就绪的 Issue 在多个守护进程同时运行时**没有任何一方领取它**；② 已就绪的 Issue 由实际运行守护进程的那台机器领走且认领标记 host 与之相符。若「另一台机器领走」只能在单机上用不同 host 身份模拟，必须如实披露限制。
  ｜实测：① 未就绪 Issue 在守护进程轮询下不被领取（rv-8 队列侧，两个轮询者都跳过）；② 认领标记的 host 由**实际执行领取的那台机器上的进程自己写入**：`rv-9-first-claim-cas.txt` 里赢家的 `iar:claim host="ZataZhangdeMacBook-Air.local" pid="2759"` 与真跑它的进程一致，败家的撤销评论点名同一 host/PID。**限制披露（如实）**：两个进程跑在**同一台机器**上，hostname 相同，因此「另一台机器领走」没有被真实双机演练过；本条证明的是 host 归属机制（谁领谁署名）与先到先得排他，跨机差异只是部署事实。该限制同样写进 `evidence.json` 的 rv-9 块与 §12 风险。
- [x] `CI=true just test all` 通过；`just lint` 通过。
  ｜实测：`3223 passed, 1 skipped in 174.88s`，EXIT=0（首轮因 `| tail` 吞掉退出码暴露 `check-test-flag` 未更新，改为落盘取 `$?` 后复跑至绿）；lint 侧以 `pre-commit run --files …` 全 Passed 呈现。
- [x] 负向对照已跑：rv-1 / rv-2 / rv-3 / rv-4 / rv-6 各自的负向对照均已实际执行并记录（不是只声明做过）。
- [x] 证据目录下 `human-review-checklist.md`（含 HTML 伴生页，若含截图）已生成。
  ｜实测：`tasks/evidence/P1-FEAT-20261006-122336-any-issue-execution/human-review-checklist.md` 已生成；无截图（本次为 CLI/runner 行为，无新增 UI 面），呈递改为本机一页 `open ".iar/evidence/human-review-bundle.html"` + 真实 GitHub 链接，已在 §9.1 与该文件内披露。

**Delivery Readiness**

- [~] 交付 PR 正文按 `prd-evidence-and-merge-acceptance` 契约写：唯一链接本 PRD、明确声明「合并即验收」的含义、投影 §2 的人审决策、投影 §9.1 的人读呈递、给出 verified head 与 git tree。
  ｜未达成部分：PR 正文/证据评论由 runner（执行侧之外的发布环节）创建，非本 worktree 产出；本次未推未开 PR（执行规则禁止）。契约要求已整理供 runner 使用：唯一链接本 PRD、声明「合并即验收」、投影 §2 决策与 §9.1 呈递、verified head 与 git tree（`git diff HEAD -- src tests ROADMAP.md docs config.toml | shasum -a 256` = `ec409c84…`，未跟踪摘要 `927e8e3f…`，见 verification-plan 冻结凭据节）。
- [~] PR 证据评论包含 §9.1 呈递内容、verifier 结论、必跑门禁汇总、证据链接与可复现命令。
  ｜同上：归属 runner。可供贴入的材料已在 `tasks/evidence/P1-FEAT-…/evidence-report.md`（命令、真实转录、负控、可复现命令）与 `verifier-report.md`（两轮结论）。
- [x] 原始证据（截图等非 .md 产物）不进入代码 diff；按需通过证据分支或 PR 评论发布。
  ｜实测：`.gitignore:90` 忽略 `.iar/`；`tasks/evidence/**` 仅 `!*.md` 放行——`git status` 中证据目录只出现 4 个 .md，原始日志/HTML 留在本地。
- [x] 完成消息原样携带 §9.1 的呈递内容（截图相对路径 + `open` 命令 + 打开 URL），而不是只说「证据已归档」。
  ｜完成消息将原样给出：本地一页呈递 `open ".iar/evidence/human-review-bundle.html"`、真实 URL（#216→PR #217、#218→PR #221、#220→PR #225、#222/#224、#219/#226）与证据目录路径，而非只说「证据已归档」。

## 10. Functional Requirements

- **FR-1**：任意带 `agent/ready` 标记的 Issue（含人手写的、无 PRD 锚点的、正文自由文本的）都能被领取并端到端执行出 Draft PR，Issue 正文被当作需求来源。
- **FR-2**：撤销「无 PRD 的 Issue 不得被当作可执行任务领取」这条产品边界；路线图全文与之保持一致，M8 保留为按需能力而非强制前置。
- **FR-3**：`iar issue create` 接受 `--from-prompt "<自然语言>"`，与既有的 PRD 文件位置参数互斥；二者必居其一。
- **FR-4**：用 `--from-prompt` 建出的 Issue body 不含 `PRD path:` / `Canonical PRD` 锚点，且不在本地产生任何 PRD 文件（工作区零 diff）。
- **FR-5**：`--ready` 立刻打上就绪标记，任何正在轮询该标记的守护进程（本机或别的机器上的 keda / 其他 agent）都可以领取，**先到先得**；`--no-ready`（默认）不进守护进程队列。**显式定向（`iar run --issue N` 或 PRD 路径）不受该标记约束**——人明确点名即直接执行，且因为没标记，别的机器的守护进程不会来抢。系统中**不存在「指派给某台机器」的机制**；`--agent` 选的是 agent 二进制（claude / codex / kimi 等），不是主机。
- **FR-6**：就绪标记**只约束守护进程的自主挑选**（守护进程仍只领取带该标记的 Issue，路线图「不会自主决定处理哪些 issue」这条边界不变）；显式定向不要求它。
- **FR-7**：用 PRD 文件建 Issue 并执行的既有路径在行为、产物、门禁上逐字不变；唯一允许的变化是「两者都不给」时的报错文案（退出码仍为 2）。
- **FR-8**：`--from-prompt` 下显式传 `--publish-prd` 或 `--force` 报 usage error（退出码 2），不静默忽略。
- **FR-9**：默认生成的正文不含验收要求 → 证据门禁确定关闭（含前端不要求截图）；存在显式旗标可要求生成 → 门禁确定开启。
- **FR-10**：`--type`、`--title` 覆盖、`--agent` 路由、`--depends-on` 在两种输入方式下行为一致。
- **FR-11**：**CLI 表面与随包 `iar-operator` skill 必须同步本次全部表面变化** —— 不只是新增旗标（`--from-prompt` / `--direct-pr` / `--require-validation`），还包括**行为语义的反转**（`iar run` 不再无条件被守护进程阻塞，只有队列轮询与 daemon 互斥；`agent/ready` 只约束守护进程的自主挑选）与**新增的显式-target 退出码语义**（不可领取 → `CONFLICT(5)` / `NOT_FOUND(3)`）。`docs/` 同步；通过 CLI 表面守卫测试；`iar schema --json` 列出新旗标。**验收判据不止「守卫测试绿」——SKILL.md 不得残留与本次相反的绝对表述**（见 rv-11；现文第 25/103/106 行需改写而非追加）。
- **FR-12**：agent 生成正文失败时回退到确定性模板，仍能建出可用的 Issue。
- **FR-13**：运行时不新增功能门禁分支；verifier 的意图来源保持为 Issue body。
- **FR-14**：`iar run` 接受 `--direct-pr`（per-run，仅配单个 target）；该档位下执行 agent 结束后只保留机械步骤——runner 受控提交（commit proxy）→ push → 建 Draft PR。
- **FR-15**：`--direct-pr` 跳过 pre-PR review（第二个 agent）、`runner.verification_commands`、`pre_commit_verification_command`、Phase 4.5 的 `rv_reexec` 与 verifier、以及发布前的最终 RV/verifier 复检；若 Issue 正文带验收段，证据门禁也一并跳过。
- **FR-16**：`--direct-pr` 仅对**没有 PRD 锚点**的 Issue 有效；带 PRD 锚点的 Issue 一律拒绝（usage error，退出码 2）并提示改用 `--fast-merge`；判定期 fail-closed（读不到 Issue 即报错）。
- **FR-17**：`--direct-pr` 与 `--fast-merge` 互斥——同时给出即 usage error（退出码 2），不隐式取更强者。
- **FR-18**：`--direct-pr` 发布的 PR 正文带机器可读档位标注与一行人读说明（与 `iar:fast-merge` 同族、值不同）；PR 仍是 Draft，不自动合并。
- **FR-19**：档位内部以一个「发布档位」字段表达（`normal` / `fast` / `direct`），而非在既有布尔之外再加布尔；`--fast-merge` 的 CLI 表面、退出码、PR 标注与跳过范围**逐字不变**。
- **FR-21**：**显式定向**到一个不可领取状态的 Issue 时，报明确错误并返回非零退出码（**不静默返回 0**）：已被他人活跃认领（`agent/running` 且持有者存活）→ `CONFLICT (5)` 并指出持有者；`agent/blocked` 且无解除标记 → 拒绝并提示 `iar blocked-continue`；target 不存在 → `NOT_FOUND (3)`。
- **FR-22**：守护进程轮询与 `--all-ready` 在无可领候选时**保持现状**（返回 0、仅 INFO 日志）—— 空队列是正常状态，不得因为 FR-21 而变成错误。
- **FR-23**：**首次领取必须是原子 CAS**——两个并发领取者中只有一个真正进入执行，另一个检测到自己不是最早认领者后主动退出（释放并退出，不进入执行）；仲裁见证用认领评论里已有的 `host`/`pid` marker；**守护进程与显式定向共用同一条领取路径**，因此两种入口都受保护。
- **FR-24**：**daemon mutex 只作用于队列轮询**——本机有活守护进程时，显式单目标（`iar run --issue N` / PRD 路径）**不再被拒绝**，与守护进程共存；`--all-ready` / 无显式 target 的轮询仍被 mutex 拦（它才会与守护进程重复领取 ready 队列）。`--takeover` 语义不变（仍用于显式停掉守护进程）。

## 11. Non-Goals

- 一句话直接改代码（中间必须有 Issue 这个可被任何机器领取、可被人审阅的落脚点）。
- 为「允许无 PRD 执行」增加配置开关（代码里从来没有这条限制）。
- 豁免无 PRD Issue 的就绪标记要求。
- 删除 M8 里程碑或其澄清、合议、PRD 生成能力（只从强制前置改为按需）。
- 给 Issue 正文做 Machine Contract 结构化校验。
- 自然语言 → PRD 文件（与本次方向相反）。
- 自动判断一句话是否足够清楚，或引入澄清对话轮次。
- 改动跨机领取协议、daemon、账本 schema、认领标记格式。
- 改动 verifier 的意图来源。
- 为超限文件添加 allowlist 豁免。
- 改动或删除 `--fast-merge` 的既有外部行为（档位重构只为承载嵌套语义，不改其表面）。
- 让 `--direct-pr` 作用于带 PRD 锚点的 Issue（会旁路 PRD 交付门与归档）。
- 用「取更强者」的隐式规则处理 `--direct-pr` 与 `--fast-merge` 并存（应显式报错）。
- 让 `--direct-pr` 自动合并 PR（产物仍是 Draft，CI 是门禁而不是自动放行）。
- **提供「指派给某台机器」的机制**（如机器预留标记 / host label）。想自己跑就不打标记自己跑；想让任意一台接手就打标记。机器定向会与「任一台都能接手」的高可用设计相悖（指定的那台挂了就卡住）。
- **改动守护进程侧的自主挑选准入**：守护进程仍只领取带 `agent/ready` 的 Issue，这条不变。
- **用标签本身做防双跑**（改为领取时的 CAS，见 FR-20）。

## 12. Risks And Follow-Ups

- **最大风险：撤销产品边界后的质量后果（已在 §2 请你拍板）。** 无验收要求的 Issue 证据门禁整体关闭，**连前端改动也不要求截图**；「描述不清必须先反问」的规则失去强制力。缓解：可选的验收要求旗标；不在本次触碰 `--fast-merge`，需要强验证时仍可走 PRD 路径。
- **`--direct-pr` 让 runner 侧质量检查归零（已在 §2 请你拍板）。** 连仓库自己的测试命令都不跑，可推出连测试都不过的 PR；唯一门禁是 PR 上的 CI，而**若某仓库的 CI 不覆盖该改动，就等于完全没有把关**。缓解：该档位只对无 PRD 锚点的 Issue 有效；PR 仍为 Draft 不自动合并；PR 正文带机器可读标注让 reviewer 看得见。**这条无法靠自动化缓解 —— 它是被显式选择的取舍。**
- **两档位语义差容易被记混。** `--fast-merge` 保留 reviewer 与仓库验证，`--direct-pr` 不保留。缓解：互斥 + 显式报错（不隐式取强）、PR 正文标注值不同、CLI help 写明差异。
- **发布档位字段重构会触碰既有 `fast_merge` 的穿透链。** 约 9 个文件、约 30 处签名镜像。缓解：rv-5 要求 `--fast-merge` 外部行为逐项不变 + 既有 `tests/test_agent_runner_fast_merge.py` 全绿；若重构风险高于收益，可回退为「再加一个布尔」并在 §13 记录。
- **既有路径从未被真实验证（rv-1 是本次主要目的）。** 「能跑」目前只有单元测试与代码阅读支撑；账本中 29 条空 `prd_path` 记录全部来自测试夹具。若 rv-1 撞到未预料的硬依赖，本次范围会变化 —— **此时应停下更新本 PRD，而不是绕过**。缓解：把 rv-1 排在实施第一步，并配负向对照（人为加锚点必须失败）证明绿色来自「无锚点」这条路径本身。
- **路线图改动容易只改一处留下矛盾。** Product Boundary 改了但 Target Workflow / M8 / 验收项没改，会产生新的「文档说不一致」。缓解：rv-3 做全文一致性检查并列出了需逐节检查的清单。
- **文档承诺收窄的对外影响。** 若有人已按路线图把「无 PRD Issue 只能进澄清」当作产品承诺依赖，本次改变需要公告。缓解：§3 显式标注这是对外承诺的收窄。
- **`--from-prompt` 的正文质量决定一切。** 正文是这类 Issue 的唯一真相源，若 agent 写得太泛，执行方会做偏。缓解：rv-1/rv-2 要求人读呈递正文；`--title` 可覆盖标题。
- **跨机行为需要用真实的第二台机器（或独立 host 身份）来验证。** 未就绪不被领取这条在单机即可验；但「另一台机器领走」需要真实的第二台机器或等效的独立 host 身份。若只能单机模拟，必须如实披露限制，不能声称是真实双机验证。
- **首次领取原来不是原子的（本次修，R3）。** 实测 `agent_runner_workflow.py:57-83` 是裸的 `get_issue` → 算 labels → `edit_issue_labels`，**无回读校验**；两个并发领取者可以双双写成功、双双认为自己是赢家 → 双跑同一 Issue（两个 agent 同时改同一分支）。这是**既有**缺陷（今天由就绪标记部分掩盖），但显式定向不再要求标记后它成了唯一防线，因此本次必须补真 CAS。风险在于：CAS 若写错，可能反过来**永久卡住** Issue（无人能领）。缓解：照抄本仓 reclaim 路径已验证的 read-check-write-reread 模式与 marker 工具；rv-9 用**真实双进程并发**做正反两向验证（含负控：退回裸 write 必须失败）。
- **daemon mutex 收窄会移除一层既有互斥保护（本次改，R2）。** 现状是本机守护进程在跑时任何 `iar run` 都拒绝；收窄后显式单目标与守护进程共存，**同目标的排他完全落在 CAS/认领状态上**。这意味着「本机同时有两个进程动同一 Issue」的防护从「结构上不可能」变成「靠 CAS 判定」—— 因此该收窄**必须与首次领取 CAS 同批或更晚落地**，否则会暴露既有的非原子领取窗口。缓解：rv-10 用真实 daemon + 真实 CLI 验证共存与同目标拒绝；rv-9 保证 CAS 正确。
- **跨机共享队列的竞争是设计使然，不是缺陷。** 打了就绪标记的 Issue 就是「谁先轮到谁跑」，默认轮询间隔 120 秒（各机的 daemon 各自轮询，无跨机协调）。这保证了任一台机器挂了其他机器能接手。**想避免被抢就不要打标记、自己显式跑**（本次新疏通的路径）—— 前提是 daemon mutex 已按本次收窄，否则显式跑会被本机守护进程挡住而被迫 `--takeover`。
- **同机 daemon 与显式 run 的共存会被误以为「重复领取」。** 二者看着都在跑同一个仓库，实际各跑各的 Issue、工作树按 Issue 隔离。缓解：§1 行为样例与 FR 已写明「mutex 只挡队列轮询」；rv-10 同时验「队列轮询仍被拦」以证明没有把 mutex 整个拿掉。
- **就绪标记的语义容易被误述成「机器定向」。** 本 PRD 初稿即犯过此错（写成「不加就绪 → 留给另一台机器认领」），评审时被指出。正确语义是：不加 → 守护进程不领；加了 → 先轮询到的守护进程领，**与机器无关**；而人显式定向本就不受标记约束。缓解：FR-5/FR-6 与 §1 行为样例已按准确语义重写，并在 §9.1 呈递区加了对照。

## 13. Decision Log

| ID | 决定 | 选择 | 否决 | 理由 |
|---|---|---|---|---|
| D-01 | 本 PRD 的主题 | 「运行器对任意 Issue 通用」，`--from-prompt` 只是其中一个来源 | 以「新增创建入口」为主题 | 使用者的需求主体是运行器通用性（人手写的 Issue 也必须能跑）；只做入口会把主要矛盾盖住 |
| D-02 | 产品边界的处置 | 撤销「无 PRD 不得执行」，不加配置开关 | 保留禁令 / 加 `allow_issue_without_prd` 开关 | 代码里从来没有这条限制，加开关等于为一个不存在的门禁引入状态；现状是「文档承诺了不存在的保护」，比明确放开更危险 |
| D-03 | 就绪标记 | 无 PRD 的 Issue 仍必须带 `agent/ready` | 豁免无 PRD Issue 的就绪要求 | 路线图「不会自主决定处理哪些 issue」这条边界与 PRD 无关，本次不动。**该标记的语义是「要不要现在就进队列」，不是「哪台机器执行」** —— 系统无机器定向机制（`--agent` 选的是 agent 二进制） |
| D-04 | 运行时改动 | 预期零功能改动（仅证明 + 文档 + 创建入口） | 顺手「补齐」某些门禁分支 | 逐条核查确认所有门禁条件生效、降级路径齐全；若发现确实缺分支，应先更新 PRD 再改（会改变风险画像） |
| D-05 | M8 的处置 | 保留，从「强制前置」改为「按需使用」 | 删除 M8 与澄清能力 | 澄清与 PRD 生成对复杂需求仍有价值；删除是不可逆的信息损失 |
| D-06 | verifier 的意图来源 | 保持 Issue body（零改动） | 改为需要 PRD | 它已经是从 Issue body 取意图，这正是「Issue 是真相源」该有的形态；改成依赖 PRD 会与本次方向相反 |
| D-07 | 验证强度默认值 | 默认不生成验收要求（门禁确定关闭），显式旗标开启 | 默认生成验收要求 | 使用者原话「简单任务不需要 review 和验证」；且门禁读 Issue body 的验收段，默认值必须确定而非取决于 agent 判断 |
| D-08 | 实施顺序 | 先证明既有路径（rv-1）→ 再改文档 → 最后加入口 | 先写代码 | 若 rv-1 撞到未预料的硬依赖，范围会变化；先证明可避免在错误前提下写代码，也避免重复本会话已犯的「在未核实的基础上写方案」的错 |
| D-09 | 行数债的处置 | 不另开 PRD，依赖已合并的 PR #212 | 另写拆分 PRD / 自行拆分 | 核对 `gh pr list` 发现 #212 已完成并合并（`866ddabc`，四个文件 34/745/972/962）；本会话起草过一份重复的还债 PRD，核实后已删除 |
| D-10 | 与 `--quick` 的关系 | `--quick` 保持搁置 | 顺带交付 `--quick` | 它优化「出了 PRD 之后跑得慢」，前提在新方向下不成立（本次是「根本不必出 PRD」）；其识别出的行数债已由 #212 承接 |
| D-11 | 是否需要「agent 干完就 PR」的档位 | 新增 `--direct-pr` | 只靠 `--fast-merge` / 只靠 `.iar.toml` 持久配置 | 实测 `--fast-merge` 之后仍挂着一个默认开启的 reviewer agent（pre-PR review，最多 2 轮、单轮 1800s，且它能改代码再 push）与两处会打回 agent 的验证命令，与「快速」矛盾；`.iar.toml` 是仓库级永久设置，不适合「偶尔赶时间」 |
| D-12 | `--direct-pr` 跳过范围 | 连 pre-PR review 与 runner 验证命令一起跳，只留 commit proxy + push + 建 PR | 只跳 reviewer、保留仓库验证命令 | 使用者选择「只留机械步骤，CI 当门禁」；保留验证命令仍会在失败时打回 agent，达不到「立马 PR」 |
| D-13 | `--direct-pr` 的适用范围 | 仅无 PRD 锚点的 Issue（fail-closed 拒绝 PRD-backed） | 任何 Issue 都可用 | PRD-backed 必须走 PRD 交付门并归档 PRD，那是 PRD 卫生的一部分，不能被「快速出 PR」旁路；限定后两个档位的域不重叠 |
| D-14 | 两旗标并存的处理 | 互斥，同时给出即 usage error | 隐式取更强者 | 两者存在感不同（一个跳独立验证、一个连 reviewer 与仓库验证一起跳），静默升级会掩盖调用者意图 |
| D-15 | 内部表达 | 一个「发布档位」字段（normal/fast/direct） | 在既有 `fast_merge` 布尔之外再加一个布尔 | 两档位是嵌套关系（direct ⊃ fast）；再加布尔会让既有 ~9 文件 ~30 处签名镜像翻倍、能表达矛盾状态，且历史上已触发过重复检测告警。用一个字段既避免翻倍又让嵌套在类型上不可违反，且**不改 `--fast-merge` 的外部行为** |
| D-16 | `--direct-pr` 是否自动合并 | 不自动合并，产物仍是 Draft，CI 当门禁 | 自动合并 | 「跳过门禁」与「自动放行」是两件事；PR 上的 CI 红了必须有人看得见，自动合并会把无把关的改动直接送进主干 |
| D-17 | 想「就我这台机器跑」怎么办 | **显式定向不再要求就绪标记**：不打标记、直接 `iar run --issue N`，因无标记而不与任何守护进程竞争 | 加机器预留标记 / 只靠部署约束规避 | 机器的共享队列本就是「先到先得」（默认 120s 轮询，无跨机协调），没有反制手段；而「显式定向 = 人已决定」与「守护进程自主挑选」是两回事，标签该约束后者而不是前者。机器预留标记会与高可用设计相悖（指定那台挂了就卡住） |
| D-18 | 首次领取的原子性 | **补真 CAS**（read-check-write-reread + 认领评论 marker 作仲裁见证） | 沿用裸 write，只把它写成「尽力而为」的已知限制 | 显式定向不再要求标记后，领取是**唯一**防双跑机制；裸 write 下两个并发领取者可双双得逞 → 双跑同一 Issue。这是既有缺陷，不该把它升格为唯一防线还留着 |
| D-19 | 是否提供机器定向 | 不提供 | host label / 机器预留标记 | 与「任一台都能接手」的可用性设计相悖；使用者的真实需求（别被抢）已由 D-17 更简单地满足 |
| D-20 | 本机守护进程在跑时能否手动跑 Issue | **能**：daemon mutex 收窄为只挡队列轮询，显式单目标与守护进程共存 | 维持现状（任何 `iar run` 都要求先停守护进程或 `--takeover`） | 锁的唯一声明用途是防止「double-claim the ready queue」，而显式单目标不轮询队列；同仓多 Issue 并行本就是 daemon 一等能力（线程池），工作树按 Issue 隔离。维持现状会让 D-17 的「零竞争路径」实际被 mutex 堵死。同目标排他改由 CAS 承担，故本项与 D-18 同批约束 |
| D-21 | 显式定向到不可领取状态的行为 | 报明确错误（CONFLICT / NOT_FOUND），不静默返回 0 | 保持现状静默返回 0 | 调用者明确点名了一个 Issue 却什么都没发生、还返回成功，会让人以为跑过了（本次评审中被实际指出）；守护进程/`--all-ready` 的空队列仍保持静默（那是常态） |

## 14. Change Log

### --direct-pr 一并跳过 post-PR supervisor
- Type: 实现细化（scope 内的门禁集合补齐，未改变产品承诺）
- Before: PRD 列举 `--direct-pr` 的跳过点为「pre-PR review、`runner.verification_commands`、`pre_commit_verification_command`、Phase 4.5 rv_reexec/verifier、最终 RV/verifier 复核、证据门禁」，未提及 post-PR supervisor（`post_pr_supervisor.enabled`）。
- After: 直发档同时跳过 post-PR supervisor，Draft PR 直接打 `agent/review`；判定收敛到 `agent_runner_publication.py::_should_run_post_pr_supervisor`。
- Reason: FR-14 的规范性句子是「执行 agent 结束后**只保留机械步骤**，中间不再有任何别的 agent 调用」。本机 `.iar.toml` 里 `post_pr_supervisor.enabled = true`，不跳过它会在 PR 建好之后再起一个 agent，与「只留机械步骤」矛盾；而跳过列点里漏它是列举不全，不是取舍。
- Impact: 直发档的 agent 调用数从「1（执行 agent）」保持不变；`--fast-merge` 与默认档行为逐项不变（该函数在 NORMAL/FAST 下仍返回 `config.post_pr_supervisor.enabled`）。门禁转移叙事不变：PR 上的 CI 是唯一点。
- Review: 待人工验收（见 §9.2 rv-7 证据；直发档与快速档的阶段序列对照在同一条证据里）。

### 决策日志 D-20 重复编号改为 D-21
- Type: doc
- Before: 表格末尾两行同用 `D-20`（daemon mutex 收窄与不可领取状态报错）。
- After: 后者改为 `D-21`。
- Reason: 编号重复会让「引用 D-20」产生歧义。
- Impact: 仅标识符，无语义变更。
- Review: 无需人工确认（排版修正）。

### --require-validation 归属 issue create，而非 run
- Type: doc（变更影响树与规范正文对齐，未改变产品承诺）
- Before: 第六步正文写「显式旗标（形如 `--require-validation`）要求 agent 在正文里生成一段验收要求」，属 `--from-prompt` 建 Issue 路径；但变更影响树 CLI 同步小节把 `--require-validation` 与 `--direct-pr` 一起标在 `（run）` 下。
- After: 该旗标实现在 `iar issue create` 上，与 `--from-prompt` 同时给出时生效；变更影响树的旗标行改为「--from-prompt、--require-validation（issue create）；--direct-pr（run）」。
- Reason: 验收段必须在**建 Issue 时**进入正文，因为运行时门禁读的是 Issue 正文（`validation_required(issue_body, config)`）；`iar run` 阶段再打开门禁只能靠改写已发布的 Issue 或新增运行期分支，两者都是本次明确排除的（「本次不为这些门禁写任何新分支」）。
- Impact: SKILL.md 与 `_ALLOWED_FLAGS` 的归属随之确定：`("issue","create")` 收 `--from-prompt`/`--require-validation`，`("run",)` 收 `--direct-pr`。rv-11 的表面清单不变（三个旗标都要出现），只是挂在哪个子命令下。
- Review: 待人工验收（旗标语义见 §9.2 rv-2/rv-4 证据）。

### PRD 路径下给出 --require-validation 报用法错误
- Type: 实现细化（补齐 PRD 未列举的一侧矛盾判定）
- Before: PRD 只规定「`--publish-prd` 与 `--force` 在 `--from-prompt` 下显式传入即报 usage error」，未规定反向矛盾（PRD 路径 + `--require-validation`）怎么处理。
- After: PRD 路径（位置参数）下显式给出 `--require-validation` 同样报 usage error（退出码 2），提示 PRD 自带验收清单。
- Reason: PRD-backed Issue 的验收清单由 PRD 的 Realistic Validation 小节物化，`--require-validation` 在那条路径上没有可作用的输入；静默忽略会让人以为额外加了一层要求。与 `--publish-prd`/`--force` 同一处理原则：矛盾旗标报错而非忽略。
- Impact: 只增加一条拒绝分支，既有 PRD 路径的正常调用逐字不变；`--require-validation` 的 store_true 默认 False，因此「没提」与「显式给出」可区分。
- Review: 无需人工确认（未削弱任何承诺；新增的拒绝面在 rv-2 证据里有真实 CLI 调用记录）。

### 默认态剥掉 agent 自写的验收小节
- Type: 实现细化（把「默认不生成验收要求」做成确定性判据）
- Before: PRD 规定默认「不生成验收要求」、`--require-validation` 时「让证据门禁确定开启」，未规定 agent 无视提示词自己在正文里写了 `## Realistic Validation` 时怎么办。
- After: 两种输入方式下，验收小节的存在与否由旗标决定，不由 agent 决定：默认路径先把正文里已有的该小节整段剥离（`create_issue_from_prompt.strip_validation_section`），`--require-validation` 时先取 agent 写的条目、再由代码统一物化小节（agent 没写条目时用需求原文兜一条）。
- Reason: 门禁开关是 `validation_required(issue_body, config)` 这个对正文的纯函数结果；若放任 agent 的输出决定，同一句 `--from-prompt` 可能这次入门禁、那次不入门禁，FR-9 的「确定开启/默认关闭」就只是概率性的。
- Impact: 默认路径产物可能比 agent 原始输出少一个小节（负控制见 rv-2：把剥离方向写反会让默认态测试失败）；PRD 路径不受影响，它的验收段来自 PRD 本身。
- Review: 待人工验收（见 §9.2 rv-2 expected 断言 `validation_required` 默认 False）。
