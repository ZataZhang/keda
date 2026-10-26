# PRD: 跨机器可见性与认领租约（只依赖 GitHub 的多机协作）

- GitHub Issue: https://github.com/ZataZhang/keda/issues/314

> ⛔ **交付前置**：排在 `P1-REFACTOR-20261020-021715-purge-residual-iar-naming` 合并进 main 之后开工（hard 门禁）。两份 PRD 改的是同一批认领标记的格式化与解析代码；本 PRD 单独先做也能构建，但会和命名清理反复改同一组函数并在合并时冲突，所以由 runner 的依赖门禁强制先后。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。
> 🧍 **验收状态**：待人工验收。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本 PRD 分两层：Part A（§1–4）供人确认目标行为与两项关键决策；Part B（§5–13）供执行者按现有架构实现并验证。

## Feature Overview (功能一览)

以下是 §10 Functional Requirements 的通俗摘要，不替代其验收定义；行为验收以 §1 的行为样例表为准。

- **每台机器有稳定身份**（FR-1）：首次运行自动生成机器编号并保存在本机，主机名只用来显示，两台同名电脑不再被混为一台。
- **每个仓库一块"机器状态"公告板**（FR-2、FR-3）：kc 自动在仓库里维护一条状态 Issue，每台机器原地更新自己那条评论，写明在线、版本和手上的 Issue；不装 kc 的人在 GitHub 网页上也能看。
- **一条命令看全部机器**（FR-4）：`kc peers` 列出在这个仓库上活动的机器、各自在做什么，以及持有者没有上报状态的认领（通常是旧版本）。
- **认领带租约并自动续期**（FR-5）：认领 Issue 后持有进程在后台定期续期，续期信息附在认领评论里，旧版 kc 的认领解析不受影响。
- **远端宕机的 Issue 不再永久卡住**（FR-6）：远端认领的租约过期后，任何一台机器都可以按现有对账流程把它放回队列重跑；没有租约的旧认领仍然不碰。
- **断网的持有者主动让位**（FR-7）：持有者续不了期时先停掉自己的 agent、不再推送，推送和开 PR 前还会确认认领仍归自己，防止两台机器同时交付。
- **本机判定按机器编号**（FR-8）：带租约的认领用机器编号判断"是不是本机"，不再依赖主机名。
- **可关闭、可调、文档同步**（FR-9、FR-10）：状态上报可按仓库关闭，租约时长可配置；用户文档与随包 operator skill 同步说明新命令和新行为。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

用户在多台电脑上运行 KedaCode：有的电脑只建 Issue、看 PR，有的常驻 daemon 执行任务。现在只要装好 kc、登录 GitHub 就能协作，但各机器之间互相看不见，而且某台机器一旦宕机，它手上的任务会永久卡住。

仓库里能观察到的现状：

- 跨机器没有任何状态汇总。调用账本、运行历史和日志都只存在本机（`docs/guides/agent-runner.md` 明写"A 机起的调用不会出现在 B 机的清单里"）。要知道另一台机器在做什么，只能逐条翻 Issue 评论里的认领记录。
- 判断远端持有者是否还活着时，系统一律当作还活着：崩溃对账遇到"认领来自别的机器"直接跳过，定向 `kc run --issue N` 返回冲突，并且"系统没有强制接管远端认领的入口"。远端机器宕机后，它的 Issue 会停在 `agent/running`，要等那台机器重启 daemon 才能回收；如果它不再开机，就只能人手改标签。
- "是不是本机"只按主机名判断。两台主机名相同的电脑会把对方的认领当成本机认领，并用本机 PID 去探测对方的进程，可能误判为已死而重复执行。
- 现有认领标记由严格的正则解析，属性顺序固定。往现有标记里加字段，会让还没升级的 kc 认不出新认领，进而在这条 Issue 上重复执行。所以租约信息要放在单独的标记里，不碰认领标记本身。认领标记的命名迁移（去掉残留的 `iar` 前缀）由另一份待办 PRD 负责，本 PRD 不做。

### Interpretation (解读回显)

我把需求读作：**不增加任何服务器或网络配置**，只靠每台机器已经登录的 GitHub，让同一仓库上的各台机器互相看到在线状态和在做的任务，同时让远端宕机的任务在安全前提下可以被别的机器接手。

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | A、B 两台电脑都对同一仓库运行 daemon，B 正在执行 #42；在 A 上运行 `kc peers` | 输出两行机器：A 和 B 的主机名、kc 版本、在线状态、最后上报时间；B 一行写明正在做 #42、所用 agent、开始时间。仓库里的"机器状态" Issue 网页上有 A、B 各一条评论，内容一致。 |
| 🤖 自动验证 | B 认领 #42 后直接断电，A 的 daemon 继续运行 | 租约到期前（默认 20 分钟）A 不碰 #42；到期后 A 的下一轮对账在 #42 留下一条写明"远端租约过期"的对账评论，并把它放回 `agent/ready`，随后由某台机器重新认领、从远端分支已有的状态重跑。 |
| 🤖 自动验证 | B 正在执行 #42 时断网 11 分钟，进程一直活着 | 断网满 10 分钟（租约时长的一半）时，B 主动终止自己这次 agent 运行，不推送、不开 PR、不改标签；网络恢复后 B 发现最近一条认领已不是自己，同样不推送。 |
| 🤖 自动验证 | C 运行旧版 kc 认领 #43（认领评论没有租约信息）后宕机 | A 永远不接手 #43，行为与今天相同；`kc peers` 把 #43 列为"持有者未上报状态（可能是旧版本）"，并显示认领评论里的主机名。 |
| 🤖 自动验证 | 两台主机名都叫 `macbook` 的电脑分别认领不同 Issue，其中一台的 kc 进程退出 | 另一台不会把对方的认领当成本机认领，也不会因为本机查不到对方 PID 就回收它。 |
| 🤖 自动验证 | 一个从未用过本功能的仓库里，两台机器在同一分钟首次启动 daemon | 最终只有一条带状态标签的"机器状态" Issue；多建出来的那条被移除标签，并留言指向保留的那条。 |

上表的行为行就是验收依据（会原样转成 §7.6 的验收 oracle）；改一个格子就等于改对应的验收标准。

**我默默定了这些**

- 状态 Issue 的标签名固定为 `kedacode/presence`，不做成可配置项；它不带 `agent/ready`，所以不会被当成任务认领。
- 每个持续运行的 kc 进程（daemon、`kc run`、supervise-daemon）各占一条状态评论；同一台机器上已退出进程留下的评论会被本机新进程复用，避免评论无限增长。
- 状态评论超过 5 分钟没有更新就显示为"离线"；离线评论不自动删除。
- 新命令叫 `kc peers`，支持表格和 `--json` 两种输出；不改现有 `kc daemon status`（它继续只看本机进程）。
- 租约时长由持有者写进认领评论，接手方按持有者声明的时长判定，不用自己的配置，避免两台机器配置不同导致误判。
- 续期间隔为租约时长的 1/10（默认 120 秒），自我隔离阈值为租约时长的 1/2（默认 10 分钟）。
- 接手方用"评论在 GitHub 服务器上的最后更新时间"与本机 UTC 时间比较；两台机器的时钟偏差在租约一半以内（默认 10 分钟）时判定依然安全，超出这个偏差不在本期保护范围内。
- 远端接手一律是"放回队列从头跑"：远端未推送的本地进度（WIP checkpoint 只存在本地分支）和会话都拿不到，因此不尝试续传；恢复次数预算沿用现有设置。
- 有租约且仍处于"等待返工"状态的远端认领过期后，保留 `agent/running`，交给现有返工通道重新认领，与本机崩溃对账的规则一致。
- 续期属于必须执行的调用：daemon 因 GitHub API 预算暂停时，续期照常进行，但仍计入用量；真被限流导致的失败按"续期失败"处理。

**我理解为不做**

- 不做 `kc hub`、Tailscale 或任何需要额外服务器、网络配置的方案（用户已明确留到以后）。
- 不做跨仓库的全局机器名单：一台机器只能看到"在我关心的这些仓库上活动的机器"。
- 不做 agent 之间的消息传递，也不做 Console 网页上的机器页面（`kc peers` 和 GitHub 网页即本期的查看入口；Console 机器页面留给 Tailscale PRD）。
- 不提供"强制接管一个租约仍有效的远端认领"的入口。

**可证伪的解读**：本期读作"在现有 GitHub 共享状态上加两样东西：每仓一条状态 Issue、认领评论附带可续期的租约"，而不是"引入新的协调服务"。所有新判定只在认领评论带有新租约标记时生效；没有租约标记的认领（旧版本或本功能关闭前的历史认领）保持今天"远端一律当作还活着"的行为。不读作"把接手做成续传"，远端接手一律从远端分支的已推送状态重跑。

### What The User Gets

- **在任意一台装了 kc 的电脑上**：运行 `kc peers`，就能看到这个仓库上有哪几台机器、哪些在线、各自在跑什么 Issue。
- **不装 kc 的人**：打开仓库里的"机器状态" Issue，网页上就能看到同样的信息。
- **daemon 机器的操作者**：一台执行机宕机、断网或被收走以后，它手上的任务会在约 20 分钟后自动回到队列，由还活着的机器接着做，不用再人工改标签。
- **所有人**：同名电脑和断网恢复不会造成同一条 Issue 被两台机器同时交付。

### Measurable Objectives

- 两台机器（两个独立的 KedaCode 状态目录）对同一仓库运行时，任一台上的 `kc peers --json` 都包含这两台机器，在做的 Issue 编号也正确。
- 带租约的远端认领：服务器端最后更新时间距今不到声明时长时，对账 0 次接手；超过声明时长后的第一次对账恰好把 Issue 放回 `agent/ready`，并留下 1 条对账评论。
- 无租约标记的远端认领：任意时长后对账都是 0 次接手。
- 续期连续失败达到租约时长一半时，持有者的 agent 子进程被终止；从失租时起 0 次 `git push`、0 次开 PR、0 次标签写入（不计初始认领的标签变更）。
- 推送前回读发现最近一条认领不是自己时，0 次 `git push`。
- 两台机器同时首次启动后，带 `kedacode/presence` 标签的 open Issue 恰好 1 条。

## 2. Human Review Map (介入与风险地图)

### 决策一：允许接手"租约已过期"的远端认领

这一项改变了一个现有的安全承诺。今天跨机器的认领一律当作还活着，代价是远端宕机后任务永久卡住；本期改为远端认领如果带租约，并且超过持有者声明的时长（默认 20 分钟）没有续期，就视为失效，可以由任何机器放回队列。为了不出现两台机器同时干活，持有者这边配了两道刹车：一是续期连续失败满一半时长（默认 10 分钟）就停掉自己的 agent，不推送、不开 PR；二是每次推送和开 PR 之前，回读确认最近一条认领仍是自己。

主要风险有两个。一是两台机器时钟偏差超过 10 分钟时，接手可能早于持有者自停，造成短时间双跑；第二道刹车（推送前确认认领归属）可以挡住其中一方的交付，但挡不住双方都消耗 agent 调用。二是接手方拿不到远端未推送的进度，只能从远端分支已推送的状态重跑，上一台机器没推送的工作会重做一遍。没有租约标记的旧认领完全保持现状，混用新旧版本时不会更不安全。

**请确认：** 是否接受"远端认领租约过期（默认 20 分钟）即可由其他机器放回队列重跑，持有者 10 分钟续不上期就自停"这一规则，以及默认时长？

**验收：** 在本机模拟两台机器：一台认领后被强杀，另一台在到期前不接手、到期后接手并留下对账评论；另一组模拟断网，持有者在一半时长时自停，并且全程没有推送。

### 决策二：每个仓库默认创建一条公开的"机器状态" Issue

状态 Issue 是本期唯一的共享查看面，所有能读这个仓库的人都看得到：每台机器的主机名、kc 版本、最后在线时间、手上的 Issue 编号与所用 agent。今天的认领评论已经公开了主机名和 PID，新增暴露的主要是 kc 版本、在线时间，以及空闲机器的存在。公开仓库也适用同样的规则。我建议默认开启，仓库可以在配置里关闭状态上报；关闭后租约续期与远端接手照常工作，只是看不到状态汇总。

**请确认：** 是否接受状态 Issue 默认开启（包括公开仓库），并且只提供按仓库关闭的开关，不提供"隐藏主机名"之类的细分选项？

**验收：** 新仓库首次运行后出现一条状态 Issue，网页上能看到本机的一条评论；把开关关掉后再运行，状态 Issue 不再被创建或更新，租约续期仍照常写入认领评论。

### 自动门禁，不需要逐项人工审阅

机器编号的生成与保存、`kc peers` 的输出格式、状态评论的复用与离线判定、重复状态 Issue 的收敛、按机器编号判定"是否本机"、续期计入 API 用量、配置项读取、文档与 operator skill 同步：这些由执行者实现，并由针对性的单元测试、隔离的端到端场景和既有门禁覆盖。

**本次明确不涉及**：没有数据库结构变化（只新增一个本机机器编号文件）；不改变现有标签状态机和认领仲裁规则；不改认领标记本身的格式与前缀（前缀迁移归命名清理 PRD）；不改前端。本 PRD 新增的标记一律使用 `kc:` 前缀，不新增任何 `iar` 命名。

## 3. Usage And Impact After Implementation

**执行端操作者（跑 daemon 的电脑）**
照常运行 `kc daemon`，不需要新配置。升级后 daemon 自动生成机器编号，在仓库状态 Issue 里维护自己的评论，认领 Issue 后在后台续期。日志里会多出续期失败和自我隔离的告警行。本机进程崩溃后的对账规则不变；新增的是：别的机器宕机后，它手上的 Issue 会在租约到期后被本机的对账放回队列。

**管理端（只建 Issue、看 PR 的电脑）**
运行 `kc peers` 就能看到这个仓库上有哪些机器、各自在做什么：

```bash
kc peers            # 当前仓库
kc peers --json     # 给脚本或 Agent 用
```

不用 kc 时，在 GitHub 上打开带 `kedacode/presence` 标签的 Issue 也能看。

**定向运行者（`kc run --issue N`）**
远端认领租约仍有效时，行为和今天一样：返回冲突并点名持有者。不同的是，提示里会写明租约剩余时间。租约已过期时，先执行与 daemon 相同的接手对账，再继续正常的认领流程。

**仓库的其他读者（未必用 kc 的协作者、公开仓库的访客）**
会多看到一条"机器状态" Issue；它不带任何任务标签，不会出现在任务队列里。

**通过 operator skill 操作 kc 的 Agent**
skill 新增"看别的机器"路由，指向 `kc peers --json`。

**对现有行为的影响**
- 没有租约标记的认领（旧版本或历史认领）：所有判定与今天完全一致。
- 旧版 kc 与新版混跑：旧版读不到新标记，按今天的方式工作；新版对旧版的认领一律不接手。
- 新增配置项都有默认值，未配置时状态上报开启、租约时长 20 分钟。关闭状态上报只影响状态 Issue，不影响租约。

## 4. Requirement Shape

- **Actor**：执行端操作者、管理端使用者、定向运行者、仓库其他读者、通过 operator skill 调用 kc 的 Agent、配置仓库的开发者。
- **Trigger**：kc 长驻进程启动与运行期间（状态上报、租约续期）；daemon 每轮对账与定向运行的准入（远端租约判定）；推送与开 PR 之前（认领归属确认）；用户运行 `kc peers`。
- **Expected behavior**：各机器通过状态 Issue 互相可见；带租约的远端认领过期后可被放回队列；断网的持有者先自停，不交付；"是否本机"按机器编号判定。
- **Scope boundary**：只使用 GitHub Issue 与评论；不新增服务、数据库表或网络配置；不改认领标记本身的格式与前缀、不改标签状态机；不改前端。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**当前相关模块**

- 认领仲裁：`src/backend/core/use_cases/agent_runner_claim_arbitration.py`（`arbitrate_first_claim`、`format_claim_comment`、`concurrent_bids`；跨机投标"按有效计入"）。
- 认领标记解析与回收：`src/backend/core/use_cases/agent_runner_reclaim.py`（`_CLAIM_MARKER_PATTERN` 严格按 `host → pid → started_at → agent` 顺序匹配，结尾要求 `\s*-->`；`classify_claim_staleness`）。
- 崩溃对账：`src/backend/core/use_cases/agent_runner_reconcile.py`（`_reconcile_one_issue` 中 `if claim.host != pass_context.host: return None` 是远端跳过点）与 `agent_runner_stale_attempt.py`（`decide_stale_attempt_disposition` 三出口）。
- 定向准入：`src/backend/core/use_cases/run_target_admission.py`（`require_explicit_target_claimable`、`_claim_is_active`）。
- daemon 主循环：`src/backend/core/use_cases/run_agent_daemon.py`（`_run_daemon_loop`：Phase -1 对账 → Phase 0/1 → Phase 2 `run_once`；`run_once` 在执行 Issue 期间阻塞，因此续期不能挂在 tick 上）。
- 认领入口：`src/backend/core/use_cases/agent_runner_issue_handlers.py`（`_process_ready_issue`、`_process_running_rework`、`_process_running_publish_recovery`、blocked 恢复路径）与 `recover_publish.py`，全部调用 `arbitrate_first_claim`。
- 推送：`src/backend/core/use_cases/agent_runner_publish.py` 的 `push_changes`；开 PR 在同一发布路径上。
- 本机进程终止：`src/backend/infrastructure/attempt_process_registry.py`（停滞监督使用的、带身份复核的在册进程终止能力）。
- GitHub 访问：`src/backend/core/shared/interfaces/agent_runner.py` 的 `IGitHubClient`（已有 `comment_issue`、`edit_issue_comment`、`list_issue_comment_entries(trusted_only=...)`、`create_issue`、`list_issues_by_label`、`edit_issue_labels`）；实现在 `src/backend/infrastructure/github_issue_ops.py` 与 `github_client.py`。评论列表读的是 REST `issues/{n}/comments`，响应里本来就有 `updated_at`，但当前被丢弃。唯一的测试替身是 `tests/conftest.py` 的 `FakeGitHubClient`。
- API 预算：`src/backend/core/shared/interfaces/github_api_budget.py`（`IGitHubApiBudget.record_call`）。
- 状态目录：`src/backend/core/shared/models/product_identity.py` 的 `state_home()`（`~/.kedacode`，容器内由 `KEDACODE_HOME` 指定）。
- 版本：`src/backend/api/version_info.py` 的 `resolve_keda_version`。
- CLI：Typer 命令分布在 `src/backend/api/cli_typer_*.py`；`kc daemon status` 在 `cli_typer_runner.py`。
- operator skill：`src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md` 及其 `references/`。

**要遵循的架构模式**

- 四层依赖方向 `api → core → engines → infrastructure`：判定逻辑（租约是否过期、状态评论渲染与解析、重复状态 Issue 的收敛选择）放 `core/use_cases`，是纯函数并可注入时钟；GitHub I/O 走 `IGitHubClient`；本机文件（机器编号）走 infrastructure，经接口注入。
- 沿用"证据只读现成事实"的对账风格：租约信息写在已有的认领评论里，不新增数据库表或心跳文件；只新增本机机器编号文件，它是身份，不是第二状态源。
- 沿用 `FakeGitHubClient` 与注入 `now` / `pid_alive` / `host` 的测试风格（见 `tests/test_agent_runner_reclaim.py`、`tests/test_agent_runner_reconcile.py`、`tests/test_agent_runner_claim_arbitration.py`）。

**前端影响**：`No frontend impact`。本期的查看入口是 CLI 与 GitHub 网页；Console 机器页面属于 Tailscale PRD 的 `/app/machines/` 范围。

**约束**

- 本 PRD 不改认领标记本身的文本格式：往里加属性会让未升级的 kc 把新认领当成"无认领"，在准入和仲裁中重复执行同一 Issue。依赖的命名清理已进入本 worktree 基线（commit `f4886655`），实际认领标记使用 `kc:claim`，旧 `iar:claim` 名称不再识别；本 PRD 的 `kc:lease` 不需要迁移。
- 本 PRD 新增的所有标记、常量、函数和文件名都不得带 `iar`；命名清理 PRD 的回潮守卫测试（若已合入）必须保持通过。
- 新增 runner 配置字段需要同步 domain dataclass、pydantic settings 和 factory 映射三处（仓库已有的配置双类结构），否则会被相同的默认值掩盖、看起来生效其实没生效。
- 改动 `kc` CLI 表面必须同步 `kedacode-operator` skill 与 `docs/`（`docs/ai-standards/tooling.md` 的 CLI Surface And Packaged Skill Sync）。
- 机器可读输出中的时间统一用带偏移的 UTC，人读输出按本机时区展示（`docs/ai-standards/time-display.md`）。

**相关 PRD**

- `tasks/archive/P1-REFACTOR-20261020-021715-purge-residual-iar-naming.md`：hard 顺序依赖（via-main），已由基线中的 commit `f4886655` 满足。该 PRD 将认领标记迁移为 `kc:claim` 并停止识别旧 `iar:` 名称；本 PRD 只在独立标记中新增租约，不重写认领标记格式。
- `tasks/pending/P1-FEAT-20261018-034216-kc-tailscale-machine-onboarding.md`：独立。它规划经 Tailscale 与 SSH 管理机器，并新建 `kc machine` 命令组与 Console `/app/machines/` 页面。本 PRD 不占用 `kc machine` 命名；本 PRD 的机器编号文件今后可以被它复用，但两边互不依赖。
- `tasks/pending/P1-FEAT-20261009-161453-kc-hosted-runner-deployment.md`：独立。托管 runner 运行在容器里，状态目录由 `KEDACODE_HOME` 指定，本 PRD 的机器编号随之落在容器的状态目录中。
- `tasks/archive/P1-FEAT-20260930-225000-daemon-crash-reconciliation-session-resume.md`：前序工作，定义了"不引入心跳表或租约库"和三出口对账。本 PRD 在它之上新增"远端租约过期"这一证据来源，租约信息依然写在 Issue 评论里，没有引入第二个状态源。
- 除上述与命名清理 PRD 的 hard 顺序关系外，没有重复或阻塞关系。

## 6. Recommendation

### Recommended Approach

在现有"GitHub Issue 即共享状态"的模型上做最小扩展：

1. **机器编号**：首次需要时在状态目录写入 `machine.json`（含 UUID）。
2. **认领租约**：认领评论在 `kc:claim` 之后追加一个独立的 `kc:lease` 标记。持有进程启动一个后台续期线程，定期编辑这条评论刷新续期时间。接手方读取评论的服务器端 `updated_at` 判定是否过期。
3. **远端接手**：对账在"认领来自别的机器"的分支里，对带租约且已过期的认领走新的"远端接手"出口：放回队列，预算耗尽则判失败。
4. **自我隔离**：续期线程连续失败达到一半时长时，终止本 attempt 的 agent 子进程，并给本次领取打上"已失去认领"标志；推送和开 PR 前回读确认认领归属。
5. **状态 Issue**：同一个后台线程在每个仓库维护状态评论；`kc peers` 读取并汇总。

**为什么最贴合现有架构**：所有共享事实仍然只在 GitHub 上，判定仍然只读现成事实；认领入口、对账出口、标签状态机、进程终止都复用现有实现。

**拒绝的冗余抽象**：不新建租约表、心跳文件或本机状态数据库；不新建"集群协调"模块层；不把续期挂到 daemon tick 上（`run_once` 执行期间阻塞，tick 间隔还会随空闲退避拉长，无法保证续期频率）。

### Proposed Solution Summary (实现机制)

- **核心机制**：GitHub 评论充当带服务器时间戳的租约。持有者编辑评论即续期，接手方读服务器端 `updated_at` 判定过期；持有者用本机单调时钟统计"距上次续期成功的时间"来决定是否自停，因此持有者侧不依赖墙钟。
- **谁提供什么**：租约时长由持有者的配置决定，并写进标记；接手方只读取标记里的时长，不做推断。机器编号由 kc 首次运行时生成，用户不需要提供。
- **接入点**：认领成功后的处理边界（`arbitrate_first_claim` 返回之后、各 handler 的 `finally` 之前）启动和停止续期；对账 `_reconcile_one_issue` 的远端分支；准入 `_claim_is_active`；发布路径 `push_changes` 与开 PR 之前；daemon 与 `kc run` 进程的生命周期（启动和停止状态上报）。
- **状态与可见变化**：认领评论多一行"最后续期"文字和一个隐藏标记；仓库多一条状态 Issue；新增 `kc peers`；对账新增 `remote_lease_expired` 原因。
- **刻意避免的复杂度**：新服务、新表、标签状态机变更、`kc:claim` 格式变更、续传式远端接手。

### Alternatives Considered

- **把新字段加进 `kc:claim` 标记**：实现更少，但旧版解析器的正则要求属性顺序固定、结尾紧跟 `-->`，加字段会让旧版认不出新认领，进而在准入时放行、在仲裁时把对方投标排除，造成双跑。因此拒绝。
- **`kc hub` + Tailscale 的中心服务**：实时性更好，但违背"只装 kc 和登录 GitHub"的最简配置目标，用户已明确暂缓。
- **用 git ref 做心跳（推送隐藏 ref）**：不占 REST 配额，但网页上看不到，`kc peers` 还要额外 fetch，且没有服务器端时间戳。因此拒绝。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### 7.1 Core Logic

**机器编号**
`machine_identity()` 读取 `state_home()/machine.json`；文件不存在时用原子写（临时文件 + rename）生成 `{"machine_id": "<uuid4>", "created_at": "<UTC>"}`。文件损坏时重新生成并告警。主机名每次实时读 `socket.gethostname()`，只用于显示。

**租约标记**
认领评论正文变成：

```text
  ## Agent Runner Claimed（评论标题，原样）
- Host / PID / Agent / Started at（原样）
- Lease: renews every 120s, expires 1200s after the last renewal（人读）
- Last renewed: `<UTC ISO>`（人读，每次续期改写，保证正文变化从而刷新 updated_at）

<!-- kc:claim host=... pid=... started_at=... agent=... -->   ← 原样，不得改动
<!-- kc:lease machine="<machine_id>" ttl="1200" renewed_at="<UTC ISO>" v="1" -->
```

解析 `kc:lease` 用新的独立正则，属性顺序宽松。`kc:claim` 的正则与格式化函数不改。

**续期线程（每个 kc 进程一个 `CoordinationHeartbeat`）**
- 进程内维护"本进程持有的认领"集合：`(repo, issue_number, comment_id, ttl)`。认领入口成功后登记，handler 结束（`finally`）时注销。
- 每 `ttl/10` 秒对每个持有的认领执行一次 `edit_issue_comment`（只改续期时间文字与 `renewed_at`）。成功后记录单调时钟时间。
- 某个认领"距上次续期成功"达到 `ttl/2`：将其标记为已失去认领，通过 attempt 进程登记簿终止该 Issue 当前在册的 agent 子进程（复用停滞监督的身份复核与终止回调），写一条 WARNING 日志。不改标签、不写评论（此时很可能本来就写不了）。
- 同一线程每 60 秒刷新本进程的状态评论（见下文"状态 Issue"）。
- 续期调用计入 `IGitHubApiBudget.record_call`，但不受预算暂停跳过；遇到限流异常按续期失败计。

**推送与开 PR 前的认领确认（第二道刹车）**
在 `push_changes` 与开 PR 之前调用 `assert_claim_still_owned(issue, own_comment_id)`：用 `trusted_only=True` 读评论，取最近一条可解析的 `kc:claim`。如果它不是本进程那条评论（按 comment id 比较），或本进程已被续期线程标记为失去认领，就抛出 `ClaimLeaseLost`。调用方按"放弃本次交付"处理，不改标签、不写失败评论（标签归新的持有者）。只对带租约的认领做这一检查；本进程的认领一定带租约。

**远端租约判定（纯函数）**
`classify_remote_lease(claim_comment_updated_at, lease, now) -> "live" | "expired" | "no_lease"`：
- 认领评论里没有 `kc:lease`，返回 `no_lease`，调用方保持现状（远端一律有效）。
- `now - updated_at >= lease.ttl`，返回 `expired`；否则返回 `live`，同时给出剩余秒数供提示使用。

**"是否本机"判定**
`is_local_claim(claim, lease, this_machine_id, this_host)`：有 `kc:lease` 时比较 `machine_id`；没有时退回比较主机名（旧行为）。对账、准入、仲裁（`concurrent_bids` 中"本机已死 PID 剔除"的判断）三处统一改用这个函数。

**对账新出口**
`_reconcile_one_issue` 的远端分支：读取带 `updated_at` 的评论记录（新接口），用 `trusted_only=True` 只信任有写权限作者的评论；对 `expired` 的远端认领构造证据，worktree 与会话一律视为不可用（不在本机），交给 `decide_stale_attempt_disposition` 新增的远端规则：
- 有未消费的返工标记：保留 `agent/running`（与本机规则一致）；
- 恢复预算未耗尽：放回 `agent/ready`（重新入队，不续传）；
- 预算耗尽：判 `agent/failed`。
对账评论的原因写作 `remote_lease_expired`，附上持有者的主机名、机器编号、最后续期时间和声明的时长。去重 hash 额外折入持有者的 `machine_id`。

**定向准入**
`_claim_is_active` 对远端带租约的认领：`live` 视为有效，冲突提示里附上剩余时间；`expired` 视为无效。`kc run --issue N` 在认领前对该 Issue 调用同一个对账函数（单 Issue 版），让它先走"远端接手"出口，再进入正常认领。

**状态 Issue**
- 查找：`list_issues_by_label("kedacode/presence")`，取 open 状态里编号最小的一条为正本。如果有多条，对其余各条移除标签（`edit_issue_labels`）并留言指向正本；这个操作幂等。
- 创建：找不到时用 `create_issue` 新建（标题"KedaCode 机器状态"，正文说明用途与"请勿手动编辑"），创建后立刻重新查找并收敛，处理同时创建的竞争。
- 评论槽位：评论里带 `<!-- kc:presence machine="<id>" slot="<pid>@<process_start>" v="1" -->` 加一段 JSON 隐藏块，以及人读表格（主机名、kc 版本、进程角色、在线状态、最后上报时间、手上的 Issue：编号、阶段、agent、开始时间）。进程启动时，在本机文件锁保护下，复用一条"同一 `machine_id`、记录的 PID 在本机已不存在或标记为已退出"的评论；没有可复用的就新建。正常退出时把评论改为"已退出"。
- 离线：读取方按评论服务器端 `updated_at` 判断，超过 5 分钟没有更新即为离线。
- 开关：`presence_enabled = false` 时，不查找、不创建、不写状态评论。

**`kc peers`**
读取正本状态 Issue 的评论，按 `machine_id` 归组，每台机器取最新的一条。再列出 `agent/running` 的 Issue，取各自最近的认领：持有者的 `machine_id`（或旧认领的主机名）不在状态里的，列为"持有者未上报状态"。输出表格（时间按本机时区显示）或 `--json`（时间为 UTC 带偏移）。找不到状态 Issue 时提示"该仓库还没有机器上报状态"，退出码为 0。

### 7.2 Change Impact Tree

本树是起点，不保证穷尽；请配合 §7.4 Executor Drift Guard 的搜索命令。

```text
.
├── Infrastructure
│   ├── src/backend/infrastructure/machine_identity_store.py
│   │   [新增]
│   │   【总结】在状态目录原子读写 machine.json，提供稳定机器编号。
│   │   ├── 首次生成 uuid4 并原子写入，损坏时重建并告警
│   │   └── 实现 core 侧的机器身份读取接口
│   ├── src/backend/infrastructure/github_issue_ops.py
│   │   [修改]
│   │   【总结】评论读取新增带 updated_at 与作者信息的记录形态，复用现有分页与可信作者校验。
│   │   └── 新增 list_issue_comment_records（与 list_issue_comment_entries 共用请求与 trusted_only 逻辑，不复制粘贴）
│   └── src/backend/infrastructure/github_client.py
│       [修改]
│       【总结】把新的评论记录读取暴露为 IGitHubClient 方法。
│
├── Domain / Core
│   ├── src/backend/core/shared/interfaces/agent_runner.py
│   │   [修改]
│   │   【总结】IGitHubClient 增加 list_issue_comment_records；新增机器身份读取接口。
│   ├── src/backend/core/shared/models/agent_runner.py
│   │   [修改]
│   │   【总结】新增 IssueCommentRecord（id、body、updated_at、author）值对象。
│   ├── src/backend/core/use_cases/agent_runner_claim_lease.py
│   │   [新增]
│   │   【总结】kc:lease 标记渲染与解析、远端租约判定、is_local_claim、assert_claim_still_owned、ClaimLeaseLost。
│   ├── src/backend/core/use_cases/coordination_heartbeat.py
│   │   [新增]
│   │   【总结】进程级后台线程：按 ttl/10 续期持有的认领、超过 ttl/2 自我隔离、每 60 秒刷新状态评论。
│   ├── src/backend/core/use_cases/machine_presence.py
│   │   [新增]
│   │   【总结】状态 Issue 查找/创建/收敛、评论槽位复用、状态评论渲染与解析、peers 汇总。
│   ├── src/backend/core/use_cases/agent_runner_claim_arbitration.py
│   │   [修改]
│   │   【总结】认领评论追加 kc:lease 与续期文字；concurrent_bids 的本机判定改用 is_local_claim。
│   │   ├── format_claim_comment 接收 machine_id 与 ttl
│   │   └── kc:claim marker 文本保持逐字节不变
│   ├── src/backend/core/use_cases/agent_runner_reclaim.py
│   │   [修改]
│   │   【总结】旧 reclaim 路径的本机判定改用 is_local_claim；_CLAIM_MARKER_PATTERN 不改。
│   ├── src/backend/core/use_cases/agent_runner_reconcile.py
│   │   [修改]
│   │   【总结】远端分支新增 remote_lease_expired 出口，读带 updated_at 的可信评论记录，hash 折入 machine_id。
│   ├── src/backend/core/use_cases/agent_runner_stale_attempt.py
│   │   [修改]
│   │   【总结】判定与评论渲染支持远端出口（不续传、预算耗尽判失败、返工保持 running）。
│   ├── src/backend/core/use_cases/run_target_admission.py
│   │   [修改]
│   │   【总结】_claim_is_active 支持租约判定；冲突提示附剩余时间；过期时先走单 Issue 对账。
│   ├── src/backend/core/use_cases/agent_runner_issue_handlers.py
│   │   [修改]
│   │   【总结】各认领入口成功后向 CoordinationHeartbeat 登记、finally 注销；收到 ClaimLeaseLost 时放弃交付且不写标签。
│   ├── src/backend/core/use_cases/recover_publish.py
│   │   [修改]
│   │   【总结】发布恢复的认领同样登记续期并在推送前确认归属。
│   ├── src/backend/core/use_cases/agent_runner_publish.py
│   │   [修改]
│   │   【总结】push_changes 与开 PR 前调用 assert_claim_still_owned。
│   ├── src/backend/core/use_cases/run_agent_daemon.py
│   │   [修改]
│   │   【总结】daemon 启动时启动 CoordinationHeartbeat（按仓库上下文登记状态评论），退出时停止并标记"已退出"。
│   └── src/backend/core/use_cases/run_agent_once.py
│       [修改]
│       【总结】kc run 与 supervise 进程同样启停 CoordinationHeartbeat。
│
├── Config
│   ├── src/backend/infrastructure/config/agent_runner_settings_models.py
│   │   [修改]
│   │   【总结】[agent_runner.daemon] 增加 presence_enabled（默认 true）与 lease_ttl_seconds（默认 1200，最小 120）。
│   ├── src/backend/core/shared/models/agent_runner.py
│   │   [修改]
│   │   【总结】对应 domain dataclass 字段同步。
│   └── src/backend/engines/agent_runner/factories/__init__.py
│       [修改]
│       【总结】settings → domain 的字段映射同步（避免默认值掩盖漏映射）。
│
├── API / CLI
│   ├── src/backend/api/cli_typer_peers.py
│   │   [新增]
│   │   【总结】kc peers 命令：表格/--json，复用 --repo / --repo-id 选择器与现有输出与退出码约定。
│   └── src/backend/api/cli_typer.py
│       [修改]
│       【总结】注册 peers 命令。
│
├── Tests
│   ├── tests/test_agent_runner_claim_lease.py [新增]
│   │   【总结】标记往返、基线解析器对新评论的兼容、远端判定边界、is_local_claim、归属确认。
│   ├── tests/test_coordination_heartbeat.py [新增]
│   │   【总结】续期节奏、失败计时、ttl/2 自我隔离触发终止、预算记录与暂停豁免。
│   ├── tests/test_machine_presence.py [新增]
│   │   【总结】状态 Issue 创建与重复收敛、槽位复用、离线判定、关闭开关、peers 汇总含"未上报认领"。
│   ├── tests/test_agent_runner_reconcile.py [修改]
│   │   【总结】远端过期/未过期/无租约/预算耗尽/返工保持 running 五种情形。
│   ├── tests/test_run_target_admission.py [修改]
│   │   【总结】远端租约有效时冲突带剩余时间、过期时放行。
│   ├── tests/test_agent_runner_claim_arbitration.py [修改]
│   │   【总结】同名主机不同机器编号时不剔除对方投标；kc:claim 文本逐字节不变。
│   └── tests/conftest.py [修改]
│       【总结】FakeGitHubClient 实现 list_issue_comment_records，支持注入评论 updated_at。
│
└── Docs / Skill
    ├── docs/guides/agent-runner.md [修改]
    │   【总结】多机部署、崩溃对账、定向准入、首次领取仲裁四节同步租约、远端接手、状态 Issue 与 kc peers。
    ├── src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md [修改]
    │   【总结】Routes 表新增"看别的机器"路由。
    └── src/backend/engines/agent_runner/templates/skills/kedacode-operator/references/watch.md [修改]
        【总结】说明 kc peers --json 字段与"未上报认领"的含义。
```

### 7.3 Risk Classification Register

| change point | tier | decisive dimension / override | intervention | oracle / gate |
|---|---|---|---|---|
| 远端租约过期接手 + 持有者自我隔离 + 推送前归属确认 | R3 | 正确性：同一 Issue 双重执行；固定区：core 编排 + 并发 | 人工确认（决策一） | rv-2、rv-3 |
| 状态 Issue 默认开启并公开 | R2 | 安全/隐私：公开仓库暴露机器信息；外部可见行为 | 人工确认（决策二） | rv-4 |
| `kc:claim` 格式保持不变、新标记独立 | R3 | 兼容：旧版误判导致双跑 | 执行者 + 判别性测试（归入决策一的证据） | rv-2 中基线解析器对新评论的断言 |
| 按机器编号判定本机 | R2 | 正确性：同名主机误回收 | 执行者 + 强 oracle（并入 rv-4 场景） | rv-4 |
| `kc peers` 输出 | R1 | 单一只读入口 | 执行者 + 端到端 | rv-1 |
| 配置项与三处映射 | R1 | 局部；漏映射被默认值掩盖 | 执行者 + 非默认值的配置往返测试 | 单测：设置 `lease_ttl_seconds=900` 后标记里出现 `ttl="900"` |
| 文档与 operator skill 同步 | R0 | 文档 | 执行者 + 搜索断言 | §9 Documentation Acceptance |

### 7.4 Executor Drift Guard

文件清单是起点。动手前先跑：

```bash
rg -n "arbitrate_first_claim\(|format_claim_comment|parse_claim_marker|_CLAIM_MARKER_PATTERN" src/backend
rg -n "claim\.host|this_host|socket\.gethostname\(\)" src/backend/core
rg -n "def push_changes|create_pull_request|gh pr create" src/backend/core src/backend/infrastructure
rg -n "class FakeGitHubClient|def list_issue_comment_entries" tests src/backend
rg -n "reclaim_ttl_seconds|reconcile_stale_attempts" src/backend docs
```

- 第二条命令列出的每个"按主机名判定本机"的位置，都要改用 `is_local_claim`，否则同名主机问题只修了一半。
- 第一条命令列出的所有认领入口，都必须登记续期；漏登记的入口在长时间运行后会被别的机器误接手。
- 交付前必须确认 `kc:claim` 的格式化输出与本 PRD 基线逐字节一致（rv-2 中的兼容断言）。

### 7.5 Flow Diagram

```mermaid
sequenceDiagram
    participant B as "B 机 (持有者)"
    participant GH as "GitHub Issue #42"
    participant A as "A 机 (对账方)"
    B->>GH: "认领评论: kc:claim + kc:lease(ttl=1200)"
    loop "每 ttl/10 秒"
        B->>GH: "编辑评论 (刷新 updated_at)"
    end
    Note over B: "断电 / 断网"
    A->>GH: "对账: 读可信评论记录"
    GH-->>A: "updated_at 距今 < ttl → live, 不碰"
    Note over B: "断网满 ttl/2: 终止 agent, 标记失去认领"
    A->>GH: "再次对账: updated_at 距今 ≥ ttl → expired"
    A->>GH: "对账评论 remote_lease_expired + 回 agent/ready"
    Note over B: "恢复联网后推送前确认归属: 最近认领不是自己 → 放弃交付"
```

### 7.6 Realistic Validation Plan

各场景在一个隔离的端到端环境中运行：两个独立的 `KEDACODE_HOME` 目录模拟两台机器，PATH 上放一个假的 `gh`，它用 JSON 文件模拟 Issue、评论、标签，并由服务器端维护 `updated_at`，支持注入时间偏移；agent 用确定性的假 agent；git 与 kc CLI 都是真实的。所有 RV 脚本放在 `tasks/evidence/<prd-stem>/scripts/`。真实 GitHub 场景作为可选项，用 `KC_RV_SANDBOX_REPO=<owner/repo>` 开启；没有凭据时跑上面的假 `gh` 场景。

```yaml
- id: rv-1
  behavior: 两台机器对同一仓库运行时，任一台上的 kc peers 都能看到两台机器以及各自在做的 Issue
  reviewer: human
  real_entry: "KEDACODE_HOME=$HOME_A kc peers && KEDACODE_HOME=$HOME_A kc peers --json（A、B 两个 daemon 由 RV 脚本启动，B 正在执行 #42）"
  expected: "表格两行机器（A 空闲、B 在做 #42 并显示 agent 与开始时间）；--json 的 machines 数组长度为 2，B 的 issues[0].number == 42；假 gh 的状态 Issue 有 A、B 各一条状态评论"
  mock_boundary: "只替换 gh（文件模拟 GitHub）与 agent CLI；kc CLI、daemon、git、状态目录全部真实"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  presentation: "tasks/evidence/<prd-stem>/rv-1-kc-peers.txt（表格与 JSON 原样输出）+ 可选的真实 GitHub 沙箱状态 Issue 网页截图 rv-1-presence-issue.png；自检：表格中 B 一行的 Issue 列是 #42"
  negative_control: "假 gh 设置 FAKE_GH_HIDE_MACHINE_ID=<B 的 machine_id> 隐藏 B 的状态评论，再运行同一个真实 kc peers --json 入口。"
  expected_fail: "--json 的 machines 只有 1 台，machines 长度为 2 的断言失败；去掉隐藏后真实入口读到两台机器。"
- id: rv-2
  behavior: 带租约的远端认领到期前不被接手、到期后被放回队列；无租约的旧认领永不被接手；kc:claim 格式与基线解析器兼容
  reviewer: verifier
  real_entry: "RV 脚本：B 用真实 kc run --issue 42 认领后 kill -9；A 运行真实 kc daemon，假 gh 分别把服务端 updated_at 存为当前时间前 ttl-30s 与 ttl+30s；另取只有 kc:claim、没有 kc:lease 的旧认领 #43，服务端时间设为 24 小时前"
  expected: "ttl-30s 时 #42 仍是 agent/running、对账评论 0 条；ttl+30s 后 #42 恰好 1 条含 remote_lease_expired 的对账评论并回到 agent/ready；#43 始终 agent/running、对账评论 0 条；把新认领评论正文交给基线中的 parse_claim_marker_detail，能解析出相同的 host/pid/started_at/agent"
  mock_boundary: "只替换 gh 与 agent CLI；对账、准入、认领评论渲染走真实 kc 代码；假 gh 仅修改它存储的服务器端 updated_at，客户端使用实际 UTC 时钟"
  tier: R3
  test_layer: e2e
  required_for_acceptance: true
  critical_value_source: "假 gh 存储的评论 updated_at（服务器端时间）与评论正文中的 kc:lease ttl"
  must_cross: "kc daemon 进程 → reconcile_stale_attempts → IGitHubClient(list_issue_comment_records, trusted_only) → gh 子进程 → 文件存储 → 标签写回"
  forbidden_bypasses: "直接调用 classify_remote_lease 代替 daemon；在测试里手写对账评论；用客户端本地时间代替假 gh 存储的服务端 updated_at"
  fresh_state_probe: "对账结束后用一个新的 gh 进程读取 #42/#43 的标签与评论数"
  final_tree_evidence: "rv-2 输出由 scripts/working_tree_hash.py 计算的 candidate implementation tree（排除 PRD/evidence delivery records）；任何 core/use_cases 下的改动后重跑"
  negative_control: "当 #42 服务端 updated_at 已早于声明 TTL 1230 秒时，设置 FAKE_GH_FORCE_UPDATED_AT_ISSUE=42，让假 gh 对真实 kc daemon 返回新鲜更新时间。"
  expected_fail: "目标 Issue 仍停在 agent/running 且 remote_lease_expired 评论为 0 条；要求过期后恰好一条对账评论的断言失败。"
- id: rv-3
  behavior: 持有者续期连续失败满 ttl/2 时终止自己的 agent 且不交付；恢复联网后发现认领已易主同样不推送
  reviewer: verifier
  real_entry: "RV 脚本经真实 kc run --issue 42 执行三种时序：无竞争认领负控；A 在 agent 完成后、push_changes 认领读取前写入新 kc:claim；以及真实推送后、开 PR 认领读取前写入新 kc:claim。另以 gh 对租约评论持续返回网络错误模拟断网。"
  expected: "断网满 ttl/2 后 agent PID 不再存活，kc run 放弃 delivery，不再启动 recovery attempt；失租后远端 ref 未变、pr create 为 0、初始 ready→running 领取以外的标签写入为 0。竞争认领若在 push_changes 读取前出现，则没有推送；若在开 PR 读取前出现，则已推送的 ref 保留但不会创建 PR。"
  mock_boundary: "只替换 gh 与 agent CLI；续期线程、进程登记簿与终止、push_changes 和 create_draft_pr 前的归属确认走真实代码；git 远端是本地裸仓库"
  tier: R3
  test_layer: e2e
  required_for_acceptance: true
  critical_value_source: "本地裸仓库的 refs/heads/issue-42 与假 gh 的调用日志"
  must_cross: "kc run 进程 → CoordinationHeartbeat → attempt_process_registry 终止 → run_agent_execution_loop / handler 传播 ClaimLeaseLost → push_changes 与 create_draft_pr 前置确认"
  forbidden_bypasses: "在测试中直接设置失去认领标志；绕过 push_changes 调用 git push；用 monkeypatch 替换终止函数"
  fresh_state_probe: "每个 kc run 退出后，用新进程执行 git ls-remote、读取假 gh 调用日志并核对 agent PID 存活"
  final_tree_evidence: "rv-3 输出由 scripts/working_tree_hash.py 计算的 candidate implementation tree（排除 PRD/evidence delivery records）；任何 run_agent_once / run_agent_execution_loop / coordination_heartbeat / agent_runner_publish 改动后重跑"
  negative_control: "ownership 负控设置 FAKE_GH_DISABLE_CLAIM_SWITCH=1，真实 agent 提交后不向假 gh 写入竞争认领；真实 direct-pr 发布路径继续推送并尝试 gh pr create。"
  expected_fail: "远端 ref 从 absent 变为 issue-42 提交 SHA，且 gh pr create 调用数大于 0；要求失去认领后无推送、无 PR 的 oracle 按预期失败。"
- id: rv-4
  behavior: 状态 Issue 默认创建且重复时收敛为一条；关闭开关后不写状态但租约照常；同名主机按机器编号区分
  reviewer: verifier
  real_entry: "RV 脚本启动两份真实 kc daemon，并用假 gh barrier 让两边并发读到空状态板后同时尝试创建；随后经 TOML 更新 presence_enabled=false，以真实 kc run 续租；另由真实 daemon 对账同主机名、不同 machine_id 的活动远端认领"
  expected: "无故障注入时两机并发首启最终只有 1 条带 kedacode/presence 标签的 open Issue，重复项移除标签并留一条指向正本的留言；默认开启会创建状态；关闭后已有评论的 updated_at 不变，但 claim 评论 kc:lease.updated_at 续期；同名主机不同 machine_id 的活动认领保持 agent/running、不产生过期对账评论"
  mock_boundary: "只替换 gh 与 agent CLI；配置读取走真实 settings → domain 映射"
  tier: R2
  test_layer: e2e
  required_for_acceptance: true
  critical_value_source: "假 gh 中 Issue 的标签集合与评论 updated_at"
  must_cross: "kc daemon 进程 → machine_presence → IGitHubClient → gh 子进程 → 文件存储"
  forbidden_bypasses: "在测试里直接构造 AppConfig 跳过 TOML 解析；手动删除重复 Issue"
  fresh_state_probe: "daemon 停止后以及 kc run 续租后，用新 gh 进程读取状态 Issue 标签、评论 updated_at 和认领租约 updated_at"
  final_tree_evidence: "rv-4 输出由 scripts/working_tree_hash.py 计算的 candidate implementation tree（排除 PRD/evidence delivery records）；machine_presence、配置映射或 duplicate cleanup 改动后重跑"
  negative_control: "并发首次启动时设置 FAKE_GH_FAIL_PRESENCE_DEDUP=1，让假 gh 拒绝删除重复 Issue 的 kedacode/presence 标签。"
  expected_fail: "两台真实 daemon 各创建一条状态 Issue，open_presence_issues=2，要求恰好一条的断言失败；去掉故障注入后重试收敛为一条并写入指向留言。"
```

**失败排查**：rv-1、rv-4 先检查两个进程是否真的使用了不同的 `KEDACODE_HOME`（`machine.json` 是否不同）以及假 `gh` 是否在 PATH 最前面；rv-2 先检查假 `gh` 返回的评论 JSON 是否带 `updated_at`、认领评论是否带 `kc:lease`；rv-3 先检查假 agent 是否经过 attempt 进程登记簿（只有走 `Popen` 的路径会登记），以及 `push_changes` 前的确认是否用了本进程的 comment id。

### 7.7 ER Diagram

No data model changes in this PRD.（只新增本机文件 `~/.kedacode/machine.json`，它是机器身份，不是数据库结构。）

### 7.8 Low-Fidelity Prototype

不需要：没有前端改动。CLI 输出示意：

```text
$ kc peers
机器                  版本     状态   最后上报        正在处理
studio-mac (a1b2…)    0.42.0   在线   16:31:05        —
office-mbp (c3d4…)    0.42.0   在线   16:30:58        #42 实现 · claude · 15:02 开始
持有者未上报状态的认领：
#43  host=old-runner  可能是旧版本 kc，或该机器已关闭状态上报
```

### 7.9 Interactive Prototype Change Log

No interactive prototype file changes in this PRD.

### 7.10 External Validation

No external validation required; repository evidence was sufficient.（GitHub REST 评论对象含 `updated_at`，这一点由仓库现有 `gh api .../issues/{n}/comments` 的调用路径可直接验证；实现时用一次真实 `gh api` 调用确认字段存在。）

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - P1-REFACTOR-20261020-021715-purge-residual-iar-naming
- Gate type: hard
- Sequence: via-main
- Notes: 交付顺序依赖而非构建依赖：两份 PRD 改同一组认领标记的格式化与解析函数，先做命名清理可避免重复改动和合并冲突；用户 2026-10-24 要求由 kc 自动保证顺序，因此设为 hard。与 `P1-FEAT-20261018-034216-kc-tailscale-machine-onboarding` 无顺序依赖；它今后可以复用本 PRD 的 `machine.json` 机器编号。与 `P1-FEAT-20261009-161453-kc-hosted-runner-deployment` 无依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 看什么 | 呈递物 | 10 秒自检 |
|---|---|---|
| 在一台机器上看到两台机器及各自在做的 Issue（rv-1） | `tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt`；打开：`open "tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt"` | 表格里 B 那一行的"正在处理"列是 `#42`；JSON 中 `machines` 长度为 2 |

未在此展示的是 `reviewer: verifier` 的证据组：rv-2（远端接手与旧认领不受影响）、rv-3（自我隔离与推送前确认）、rv-4（状态 Issue 收敛、开关、同名主机）。这三组结论在 9.2 中，只在失败时需要人看。

### 9.2 Acceptance Evidence Package

按风险排序：决策一（rv-2、rv-3，R3）→ 决策二（rv-4，R2）→ rv-1 与 R1/R0 门禁。rv-1 至 rv-4 均通过真实 `kc` CLI/daemon 与本地 Git，GitHub 和 agent CLI 由无凭据替身提供。每个场景先执行负控并观察到 oracle 失败，再去掉故障注入观察到通过；四项在默认租约调整后重新运行，共享 candidate implementation tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`（不含本 PRD 与 evidence delivery records）：

| 顺序 | 结果与判别证据 | 原始证据 |
|---|---|---|
| 决策一 · rv-2（R3） | 负控强制返回新鲜服务器时间，过期 oracle 观察到 0 条接手评论、#42 仍为 `agent/running`；默认 1200 秒租约下正常路径 1170 秒未接手、1230 秒后恰有 1 条 `remote_lease_expired` 并回到 `agent/ready`。无租约 #43 在模拟 24 小时后仍为 `agent/running`；基线解析器读到 `kc:claim` 的 host、pid、started_at、agent。 | `rv-2-remote-lease.txt` |
| 决策一 · rv-3（R3） | 负控确实推送分支并尝试开 PR，保护 oracle 失败；绿色路径分别在推送前与开 PR 前发现认领易主并阻止交付。续期失败到 120 秒 TTL 的一半时 agent 子进程退出；失租后的远端分支不存在、PR 调用为 0、标签写入为 0（初始认领的 2 次除外）。 | `rv-3-self-fence.txt` |
| 决策二 · rv-4（R2） | 负控拒绝重复标签移除后留下 2 条状态 Issue，唯一板 oracle 失败；重试及并发首启绿色路径都收敛至 1 条。关闭状态开关后状态评论不变而租约继续续期；同主机名、不同 machine_id 的远端有效租约保持运行。 | `rv-4-presence.txt` |
| 双机可见 · rv-1（R1） | 负控隐藏 B 评论后真实 `kc peers --json` 只见 1 台，数量断言失败；绿色表格与 JSON 都见两台在线机器，B 正在处理 #42 / `codex`，状态 Issue 有两条评论。 | `rv-1-kc-peers.txt` |

每项的完整实际命令、负控、预期失败、输出摘要、解释与风险见 `evidence.json`；终端原始输出只含本项内容。执行者最终代码审查完成；独立 verifier 由 runner 后续门禁单独执行，当前不把执行者自审记为 verifier `PASS`。

### Human-Confirmed (来自 Part A 风险地图)

- [x] 决策一：接受"远端认领租约过期（默认 20 分钟）即可被放回队列，持有者 10 分钟续不上期就自停"及默认时长 — 依据 rv-2、rv-3 结果 <!-- 证据: 用户于 2026-10-26 明确同意，并确认租约默认值为 1200 秒。 -->
- [x] 决策二：接受状态 Issue 默认开启（包括公开仓库），只提供按仓库关闭的开关 — 依据 rv-4 结果 <!-- 证据: 用户于 2026-10-26 明确同意。 -->
- [ ] 已查看 9.1 呈递区，`kc peers` 的输出内容与格式符合预期

### Architecture Acceptance

- [x] 判定逻辑（租约解析与判定、状态评论渲染与解析、重复状态 Issue 的收敛选择）位于 `src/backend/core/use_cases/`，GitHub I/O 只经 `IGitHubClient`；`rg -n "import subprocess|gh api" src/backend/core/use_cases/agent_runner_claim_lease.py src/backend/core/use_cases/machine_presence.py src/backend/core/use_cases/coordination_heartbeat.py` 无结果。证据：`src/backend/core/use_cases/agent_runner_claim_lease.py`、`machine_presence.py`、`coordination_heartbeat.py`；源码搜索退出码 1（无命中）。
- [x] 没有新增数据库表或迁移：`git diff --name-only main... | rg "alembic|migrations"` 无结果。证据：当前变更文件清单不含 Alembic 或 migration 路径。
- [x] 本机判定统一经 `is_local_claim`：`rg -n "claim_host\s*==\s*host" src/backend/core` 只命中 `is_local_claim` 内部的旧版回退分支。证据：旧认领只在 `agent_runner_claim_lease.py` 回退至主机名；`agent_runner_reconcile.py`、`run_target_admission.py`、`agent_runner_claim_arbitration.py` 调用统一 helper。

### Behavior Acceptance

- [x] rv-2 通过：默认 1200 秒租约下，1170 秒时不接手、1230 秒后恰好 1 条 `remote_lease_expired` 对账评论并回到 `agent/ready`，旧认领永不接手（证据：`rv-2-remote-lease.txt`；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] rv-3 通过：ttl/2 时 agent 被终止；失租后远端分支 ref 未变化、`pr create` 为 0 次、标签写入为 0 次（初始 ready→running 认领的 2 次标签写入除外，证据：`rv-3-self-fence.txt`；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] rv-4 通过：状态 Issue 恰好 1 条、关闭开关后状态停止更新但租约照常、同名主机不被误对账（证据：`rv-4-presence.txt`；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] `kc:claim` 文本与本 PRD 基线逐字节一致，基线解析器能解析新认领评论（证据：`rv-2-remote-lease.txt` 中的兼容断言；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] 租约有效的远端认领，`kc run --issue N` 返回退出码 5，提示中包含剩余时间（证据：当前九模块命令 `149 passed`，包含 `tests/test_run_target_admission.py::test_cli_run_on_live_remote_lease_exits_conflict`；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] 同名主机 + 同数字 PID 的**另一台机器**先认领时，仲裁不按本机重入放行：带租约的投标一律按机器编号判定（当前九模块命令 `149 passed` 覆盖 `test_same_host_same_pid_remote_lease_bid_is_not_a_reentry` 与同机重入回归；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。
- [x] 本进程未登记认领时，推送/开 PR 前的归属栅栏仍回读服务端按机器编号确认，确认不了就拒绝交付（当前九模块命令 `149 passed` 覆盖两个 publish-fence 回归；candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`）。

### Dependency Acceptance

- [x] 未新增第三方依赖：`git diff main... -- pyproject.toml uv.lock` 无依赖变更。证据：变更文件清单无 `pyproject.toml` 或 `uv.lock`。

### Validation Acceptance

- [x] 最高保真入口：rv-1 至 rv-4 均通过真实 `kc` CLI 与 daemon 进程执行，只替换 `gh` 与 agent CLI；每份证据记录由 RV tree helper 计算的 candidate implementation tree（不含 PRD/evidence delivery records），且彼此相同并对应最终实现代码。默认租约调整后重新执行，四份 `rv-*.txt` 均记录 `552d0b85faf6748fa773d3d7e4f8f7454089db77`，manifest 位于 `evidence.json`。
- [x] 配置三处映射：设置 `lease_ttl_seconds = 900` 后，新认领评论中出现 `ttl="900"`（证据：`tests/test_agent_runner_claim_lease.py::test_configured_ttl_is_written_into_new_claim_comment`；对应相关测试输出）。
- [x] 全量相关测试：`uv run pytest -o addopts="" tests/test_agent_runner_claim_lease.py tests/test_coordination_heartbeat.py tests/test_machine_presence.py tests/test_agent_runner_reconcile.py tests/test_run_target_admission.py tests/test_agent_runner_claim_arbitration.py tests/test_agent_runner_reclaim.py tests/test_github_client.py tests/test_kc_peers.py` 全部通过（不依赖 testmon 增量）。默认租约调整后的当前树结果为 `149 passed`。完整单元套件 `5043 passed, 1 skipped` 是先前 tree `6658504731e5dbc2cfc5b751d470123789c3464c` 的历史结果，未在本次默认值改动后重跑。

### Documentation Acceptance

- [x] `docs/guides/agent-runner.md` 的多机部署、崩溃对账、定向准入、首次领取仲裁四节已更新；`rg -n "kc peers|kedacode/presence|remote_lease_expired|lease_ttl_seconds" docs/guides/agent-runner.md` 四个词都能命中。证据：四个术语搜索均有命中。
- [x] 文档中旧的"系统没有强制接管远端认领的入口"等表述已改为准确描述（带租约且过期可被接手；租约有效时仍不能接手）：`rg -n "跨机器的认领无法探测远端进程" docs` 的命中处已同步改写。证据：旧语义搜索无命中；agent-runner guide 明确区分有效租约与过期租约。
- [x] operator skill 新增路由：`rg -n "kc peers" src/backend/engines/agent_runner/templates/skills/kedacode-operator` 有命中。证据：`SKILL.md` 与 `references/peers.md`。

### Delivery Readiness

- [x] 推荐方案已完整实现，没有遗留的 Phase 2 或临时兼容层。证据：最终代码审查、四项 RV 与相关回归测试；没有新增运行时依赖或临时旁路。
- [x] 如果交付走 PR，PR 证据评论包含 9.1 呈递区的内容；否则完成消息原样附上 9.1 内容。证据：本次不创建 PR，完成消息将逐字附上 9.1 行。
- [x] `just lint --full` 与上述 pytest 通过，单文件非空行均少于 1000 行。证据：最终 `just lint --full`、相关测试 `149 passed` 与 `just lint --reuse` 通过；全文件行数检查无超限。`just test`（testmon 增量档）通过，完整单元套件 `uv run pytest -o addopts="" tests/` 在最终候选树上为 `5043 passed, 1 skipped`，针对性全量命令不依赖 testmon 亦 `149 passed`。
- [~] 独立 verifier 的最终判定 — runner-owned gate: runner 在所有 executor-owned 清单项完成后执行独立审查并记录有效 verdict；执行者不得把自身代码审查记为 verifier `PASS`。
- [~] 人审 HTML 的真实浏览器渲染与翻页 smoke — runner-owned gate: 在可用浏览器中确认零 `pageerror`、首卡可见、能到末页生成 Markdown。执行者的 CUA 环境没有可用 browser tab 且拒绝访问已安装浏览器；未把静态结构检查冒充浏览器通过。

## 10. Functional Requirements

- **FR-1**：**机器编号**：kc 首次需要时在状态目录生成并保存 `machine.json`（uuid4），之后一直复用；文件损坏时重建并告警。主机名只用于显示。
- **FR-2**：**状态 Issue**：开启状态上报的仓库里，kc 维护一条带 `kedacode/presence` 标签的 open Issue。找不到就创建；有多条时保留编号最小的一条，其余移除标签并留言指向正本。
- **FR-3**：**状态评论**：每个持续运行的 kc 进程（daemon、`kc run`、supervise-daemon）在状态 Issue 中占一条评论，每 60 秒原地更新。内容包括人读表格（主机名、kc 版本、进程角色、最后上报时间、手上的 Issue 编号/阶段/agent/开始时间）和机器可读的隐藏标记与 JSON。本机已退出进程的评论会被复用；正常退出时标记为已退出；超过 5 分钟未更新即为离线。
- **FR-4**：**`kc peers`**：读取当前仓库的状态 Issue，按机器汇总在线状态和手上的 Issue，并列出"持有者未上报状态"的运行中认领；支持表格与 `--json`；仓库没有状态 Issue 时给出说明，退出码为 0。
- **FR-5**：**认领租约**：新的认领评论在认领标记之后追加独立的 `kc:lease` 标记（机器编号、声明时长、最后续期时间）以及人读的续期说明；认领标记本身的文本保持不变（其前缀迁移归命名清理 PRD）。本 PRD 新增的标记与标识符一律不带 `iar`。持有进程每 `ttl/10` 秒编辑该评论续期，直到本次处理结束。续期计入 API 用量，但不受预算暂停跳过。
- **FR-6**：**远端接手**：对账遇到来自别的机器、带 `kc:lease` 的认领时，用评论的服务器端 `updated_at` 判断是否过期（以持有者声明的时长为准，只信任有写权限作者的评论）。过期时：有未消费的返工标记则保持 `agent/running`；恢复预算未耗尽则放回 `agent/ready`；预算耗尽则判 `agent/failed`。每种处置都写一条原因为 `remote_lease_expired` 的对账评论，并保持幂等。没有 `kc:lease` 的远端认领一律不碰。定向 `kc run --issue N` 在租约有效时返回冲突并提示剩余时间，过期时先执行同一对账再认领。
- **FR-7**：**自我隔离**：持有者某个认领"距上次续期成功"达到 `ttl/2` 时，终止该 Issue 当前在册的 agent 子进程，标记失去认领，之后不推送、不开 PR、不改标签。每次推送和开 PR 之前，回读确认最近一条可信认领仍是本进程那条评论，否则放弃交付。
- **FR-8**：**本机判定**：带 `kc:lease` 的认领用机器编号判断是否本机；没有的退回主机名。对账、准入、仲裁三处统一使用同一判定。
- **FR-9**：**配置**：仓库级 `[agent_runner.daemon]` 新增 `presence_enabled`（默认 `true`）和 `lease_ttl_seconds`（默认 `1200`，低于 `120` 时按 `120` 处理并告警）。关闭状态上报不影响租约与远端接手。
- **FR-10**：**文档与 skill 同步**：更新 `docs/guides/agent-runner.md` 中相关章节与 `kedacode-operator` skill，说明 `kc peers`、状态 Issue、租约、远端接手和自我隔离。

## 11. Non-Goals

- `kc hub`、Tailscale 或任何需要额外服务器、网络配置的协调方式（后续可能需要，本期不动）。
- 跨仓库的全局机器名单。
- agent 之间的消息传递或任务拆分协作。
- Console 网页上的机器页面。
- 强制接管租约仍有效的远端认领。
- 远端接手时续传对方的会话或未推送的本地进度。
- 自动删除离线状态评论或关闭状态 Issue。

## 12. Risks And Follow-Ups

- **时钟偏差**：接手方用本机 UTC 时间对比服务器时间戳；偏差超过 `ttl/2` 时，可能在持有者自停之前接手。推送前确认归属可以挡住其中一方的交付，但两边的 agent 调用都会被消耗。文档会写明这一前提。
- **未推送的进度会重做**：远端接手只能从远端分支已推送的状态开始，上一台机器未推送的 WIP 会被重新实现。
- **API 用量**：每个持有的认领每分钟 1 次编辑，每个进程每分钟 1 次状态编辑，外加 `kc peers` 和对账的读取。默认配置下，单机三个并发 Issue 约为每小时 240 次，远低于每小时 5000 次的配额；多台机器共用同一个 GitHub 账号时要合计。
- **混跑期**：旧版 kc 的认领没有租约，宕机后仍然会卡住，直到所有执行端都升级。`kc peers` 会把它们列为"持有者未上报状态"，便于定位。
- **后续（不在本期）**：Tailscale PRD 的机器页面可以把 `kc peers` 的数据源作为在线状态来源；`kc hub` 落地后，可以把续期和状态上报迁移到 hub，GitHub 继续作为账本。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 共享状态放哪 | 继续只用 GitHub Issue 与评论 | `kc hub` + Tailscale 中心服务 | 用户要求"只装 kc 和登录 GitHub"即可使用，中心服务需要额外部署和网络配置。 |
| D-02 | 租约字段放哪 | 同一认领评论里的独立 `kc:lease` 标记 | 给认领标记加属性 | 现有解析器要求属性顺序固定且紧跟 `-->`，加属性会让未升级的 kc 认不出新认领并重复执行；独立标记还让本 PRD 与命名清理 PRD 的前缀迁移互不干扰。 |
| D-09 | 与命名清理 PRD 的顺序 | hard 依赖（via-main），命名清理合并后才开工 | soft 依赖、两份并行开发 | 两份都改同一组认领标记函数，并行开发必然冲突；交给 runner 的依赖门禁执行顺序，不靠人盯。 |
| D-03 | 过期判定用哪个时间 | 评论的服务器端 `updated_at` + 持有者声明的时长 | 评论正文里由持有者写入的时间戳 | 正文时间来自持有者的时钟，服务器时间戳只需要信任接手方一侧的时钟。 |
| D-04 | 续期挂在哪 | 进程级后台线程 | daemon tick | `run_once` 执行 Issue 期间阻塞，tick 间隔还会随空闲退避拉长，无法保证续期频率。 |
| D-05 | 远端接手方式 | 放回队列从已推送状态重跑 | 续传远端会话 | 远端的 worktree、会话和 WIP checkpoint 只存在于那台机器，接手方拿不到。 |
| D-06 | 防双交付 | 持有者 ttl/2 自停 + 推送前确认归属 | 只靠接手方等待更长时间 | 等待再久也挡不住"断网但进程还活着"的持有者在恢复后推送，只有持有者侧检查才能堵住。 |
| D-07 | 查看入口 | `kc peers` + 状态 Issue 网页 | Console 机器页面 | 本期保持后端改动；Console 机器页面已在 Tailscale PRD 中规划，避免两份页面设计。 |
| D-08 | 状态评论粒度 | 每个 kc 进程一条，复用本机已退出进程的评论 | 每台机器一条、多个进程共同写 | 多个进程写同一条评论会互相覆盖；复用槽位把评论数控制在单机并发进程数以内。 |

### Final Reconciliation

- Interpretation: 交付实现仍是每仓一块 GitHub 状态 Issue 与认领评论中的独立 `kc:lease`；命名清理依赖已在基线完成（`f4886655`），所以当前认领标记是 `kc:claim`。它的格式不变，rv-2 用基线解析器验证 host、pid、started_at 与 agent 可读。
- Public behavior and contracts: 实现入口为 `kc peers`，支持 `--repo`、`--repo-id` 与 `--json`；`presence_enabled` 默认为开启，关闭只停止状态 Issue 读写，租约续期继续工作；`lease_ttl_seconds` 默认 1200，配置低于 120 时运行期按 120 处理并告警。配置经 settings、domain dataclass 与 factory 映射传递。CLI 表格与 GitHub 状态评论的人读时间按生成机器本地时区显示，JSON 时间保留带偏移 UTC。
- Related PRD status: 归档的命名清理 PRD 与 hard via-main 依赖由基线 commit `f4886655` 满足；Tailscale onboarding 与 hosted-runner deployment 仍独立，无额外顺序依赖。
- Requirements and risks: rv-1 到 rv-4 均以真实 kc CLI/daemon、真实本地 git、假 GitHub 与确定性 agent CLI 运行；服务器时间通过假 GitHub 的 `updated_at` 设置。rv-3 区分初始领取的两次标签写入与失租后的零次标签写入，并覆盖推送前与开 PR 前两个归属读取点。远端接手仍受双方时钟偏差小于 ttl/2 的 PRD 前提约束；状态评论仍在公开仓库可见。
- **实现中发现并纳入的边界**：进程因失租被终止后，`ClaimLeaseLost` 必须穿过执行恢复循环并阻止启动新的 recovery attempt；该修复已由定向回归测试与 rv-3 子进程场景验证。`kc run --takeover` 在停本机 daemon 前先拒绝有效远端租约，停止后再次准入；旧 takeover reclaim 路径也改为按 machine_id 识别本机，避免同名主机误回收。状态 Issue 重复项先写带 canonical 标记的清理留言，再移除重复标签；若留言失败，重复项仍留在查询集合中供下一次重试，回归测试覆盖失败后重试。低 TTL 配置留给运行期下限钳制，机器编号文件仅接受带时区创建时间的 UUID4。
- **当前执行者证据树**：租约默认值调整后，rv-1 至 rv-4 逐项重跑，四份原始输出与 `evidence.json` 均记录 candidate implementation tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`；该身份排除 PRD 与 evidence delivery-record 路径。九模块相关测试 `149 passed`。独立 verifier 尚未在该树重跑；runner 后续门禁需给出 verifier 结论，执行者自审不冒充 `PASS`。
- **人审包检查**：Markdown 与 HTML 源码静态核对包含相同的三张决定卡和结果卡；官方 warning checker 因未修改的模板注释中出现字面 `<h2>` 产生 2 张卡的误报，HTMLParser 检查实际正文为 4 张卡。真实浏览器检查因 CUA inventory 无 browser 且两个已安装浏览器均拒绝访问，记录为 `REVIEW_INCIDENT / INCONCLUSIVE`；runner 需在可用浏览器环境重试翻页、首卡可见与结果生成检查。
- **验收投影**：实现侧清单项在对应验证通过后勾选；用户已确认决策一、决策二，9.1 呈递审阅仍未完成，因此状态横幅仍为 `🧍 待人工验收`。

## Change Log

### Align marker compatibility and realistic validation evidence
- Type: evidence / scope clarification
- Before: The oracle described the old `iar:claim` marker and an “old parser” without accounting for the prerequisite migration already present in the worktree base; rv-3 also said zero label writes without separating initial claim setup from post-loss writes; the explicit daemon takeover path still skipped remote target admission and reclaim used hostname only.
- After: The compatibility oracle compares the current `kc:claim` baseline parser and keeps the claim marker byte-for-byte unchanged; rv-1 through rv-4 each declare an executed negative control; rv-3 covers both push and pre-PR ownership fences and states that zero label writes applies after lease loss, excluding the two initial claim writes. Targeted takeover checks a live remote lease before stopping the local daemon, rechecks after stop, and reclaim uses machine_id. Low TTL values clamp to 120 seconds with a warning, and human issue start times display in local time.
- Reason: The hard dependency PRD was merged into the worktree base before this feature began; focused code review exposed takeover and local-time paths outside the first set of RV scenarios; realistic validation exposed the exact production boundary where a lost-lease child error could otherwise enter recovery again.
- Impact: No user-visible safety promise or accepted scope was weakened. The validation wording now matches the merged marker name and observed label/publish sequencing; added regression tests cover the new takeover, TTL clamp, identity file and time display behavior.
- Review: executor review complete; independent verifier and human decisions pending

### Make duplicate-board cleanup retryable and refresh final evidence
- Type: implementation / evidence
- Before: Duplicate status Issues lost the `kedacode/presence` label before the pointer comment was written; a transient comment failure could make the duplicate undiscoverable for retry. The four RV records referenced an earlier candidate tree.
- After: The canonical pointer comment is ensured before label removal. A regression test injects the comment failure, proves the duplicate remains discoverable, then verifies a retry writes the pointer and removes the label. rv-1 through rv-4 were rerun and all record candidate tree `6658504731e5dbc2cfc5b751d470123789c3464c`.
- Reason: Preserve the PRD's promised duplicate-issue convergence and pointer comment under a transient GitHub write failure, and bind every realistic validation result to the final source tree.
- Impact: No scope or user-visible behavior changed; convergence now retries safely. The 4 realistic oracles passed with their negative controls, and focused tests cover the retry path.
- Review: executor code review complete; independent verifier and human decisions pending

### Reconcile archive-readiness fields and final test count
- Type: evidence / documentation
- Before: The PRD acceptance checker could not read the rv-2 `expected_fail` scalar because it began with a `#` token, Final Reconciliation used descriptive labels the checker did not recognize, and the focused test count predated the new duplicate-comment retry regression test.
- After: The expected failure is phrased without a leading Issue-number marker, Final Reconciliation uses the four required field labels, and the recorded focused result is 96 passed. The reviewer checklist headings use the same text in Markdown and HTML.
- Reason: Make the final evidence parseable and traceable without changing any accepted product behavior.
- Impact: No scope or behavior changed; archive readiness now reflects the final source and evidence.
- Review: executor code review complete; independent verifier and human decisions pending

### Record browser-review environment limitation
- Type: validation / delivery status
- Before: The interactive reviewer companion was generated, but the executing session had not recorded the browser-level pageerror, first-card, pagination, and result-card checks.
- After: Static Markdown/HTML content is cross-checked and the mechanical checker false-positive is explained; CUA reports no browser sessions and denies access to the installed Edge and Chrome-for-Testing apps. The real-browser check is explicitly left to the runner-owned handoff gate as `REVIEW_INCIDENT / INCONCLUSIVE`.
- Reason: Preserve the distinction between static artifact inspection and actual browser rendering.
- Impact: No product behavior or scope changed. Independent verifier/archive remains pending until the runner completes its verdict and real-browser checklist smoke.
- Review: executor review complete; independent verifier, browser smoke, and human decisions pending

### Close second-round review findings and rebind final evidence
- Type: implementation / evidence
- Before: Arbitration treated a winning bid as a same-process re-entry on hostname + PID alone, so two machines sharing a hostname and a numeric PID inside the concurrent-claim window both flipped the Issue to `agent/running`. The pre-push/pre-PR ownership fence returned silently whenever the claim was not registered under the publish-time repository id. Machine identity came only from the heartbeat, so a heartbeat-less entry judged its own leased claims remote. Duplicate `remote_lease_expired` reconcile comments depended on a stale comment snapshot, `kc peers` rendered comment-authored hostname/version/agent straight into Rich cells, `json.dumps(ensure_ascii=False)` left `>` unescaped so a `-->` hostname truncated its own presence block, the guide described the lease request bound as one 5-second limit, and an unreadable claim comment surfaced as a bare traceback instead of exit 5. §9 and the verifier report still cited the pre-review candidate tree and `96 passed`.
- After: `ClaimBid` carries the lease it declared and the re-entry test goes through `is_local_claim`, so a leased bid counts as ours only when the machine id matches (legacy unleased bids keep the hostname fallback). The fence reuses `_resolve_claim_repo_id` for repo_id/config drift and otherwise re-reads the server to confirm the latest trusted claim belongs to this machine, raising `ClaimLeaseLost` when it cannot. `current_machine_id()` sources identity from the heartbeat first and the persisted `machine.json` second, with `tests/conftest.py` isolating the state directory. Reconcile re-checks the dedupe hash immediately before writing and documents the residual multi-writer window. Presence payloads escape `<`/`>`, and `kc peers` strips control characters, escapes markup and bounds length before table rendering. The guide states the per-attempt limit with the 3-attempt/17-second worst case, serial renewal head-of-line effect, and monotonic self-fence clock, and the CLI maps claim-record read failures to `ExitCode.CONFLICT`. rv-1 through rv-4 were rerun and all record candidate tree `6658504731e5dbc2cfc5b751d470123789c3464c`; the focused suite is `149 passed` across nine modules and the full unit suite `5043 passed, 1 skipped`.
- Reason: A reviewer found the same-hostname harm the Issue itself named, a fail-open path in FR-7's second brake, and identity, comment, output-safety and evidence-binding gaps; the acceptance source of truth has to name the tree the reruns actually used.
- Impact: FR-8 locality now holds on the arbitration path and FR-7 refuses delivery that cannot be attributed, which is fail-closed for unattributable publishes. Legacy hostname-only claims keep their previous fallback. No CLI subcommand, flag or machine-readable output changed, so the packaged operator skill needs no update. Independent verifier still has to rerun on the final tree, and the browser smoke stays `REVIEW_INCIDENT / INCONCLUSIVE`.
- Review: executor review complete for both rounds; independent verifier on the final tree, browser smoke, and human decisions pending

### Record human decisions and widen the default lease grace period
- Type: user decision / configuration default
- Before: Default `lease_ttl_seconds` was 600 seconds (10-minute remote expiry, 5-minute owner self-fence), and both human decisions were unchecked.
- After: Default `lease_ttl_seconds` is 1200 seconds; renewal cadence follows the existing TTL/10 rule (120 seconds by default) and owner self-fence remains TTL/2 (10 minutes). The user approved the lease policy and public status Issue default with a repository-level opt-out. The `kc peers` presentation review remains open.
- Reason: Add more tolerance for temporary connectivity or GitHub API interruptions before stopping an active agent, while keeping a bounded recovery time after a machine goes away.
- Impact: Default remote takeover can take up to 20 minutes; renewal calls are less frequent under the existing TTL/10 rule. No new setting or service was added. RV-1 through RV-4 and the nine relevant test modules were rerun on current candidate tree `552d0b85faf6748fa773d3d7e4f8f7454089db77`; independent verifier and browser presentation review remain pending.
- Review: User decisions confirmed 2026-10-26; executor validation passed, independent verifier and remaining presentation review pending.
