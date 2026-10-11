# PRD: Verifier 运行中的单 Issue 人工接管

- GitHub Issue: https://github.com/ZataZhang/keda/issues/278

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，那里是唯一依赖事实源。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，那里是唯一事实源。

本文按两层组织：Part A 供人确认行为和边界；Part B 供执行者实现、验证和交付。内容借鉴受控语言、模块化信息组织和 Agent 可验证性，不代表符合 ASD-STE100 或 S1000D 的正式认证要求。

## Feature Overview (功能一览)

本区只概览 §10 Functional Requirements；验收行为以 §1 的行为样例为准。

- **只接管当前 Issue 的 verifier**（FR-1、FR-2）：操作者能从 Issue 详情确认当前运行和提交，并只停止这一轮 verifier。
- **把取消与验证结论分开**（FR-3、FR-5）：取消记录为人工接管；不生成 PASS、RED 或 YELLOW，也不添加 verifier 通过标签或启动自动合并。
- **保留人工审查入口**（FR-3、FR-5）：保留 Draft PR 和现有证据，Issue 转为人工 review；操作者自行检查、标记 PR 就绪并决定是否合并。
- **处理完成与合并竞态**（FR-4）：verifier 完成与接管请求只允许一个结果获胜；外部合并或 HEAD 变化由前后 fresh read 检查并停止后续流程，过期结论不得保留。
- **在真实 Dashboard 清晰呈递接管状态**（FR-1、FR-3、FR-6、FR-7）：Issue 详情展示可接管条件、处理结果、目标提交和不可接管原因，并按交互原型的信息层级突出当前状态与下一步操作。
- **限定本机控制边界**（§11）：控制只作用于当前主机上由 KedaCode 持有的 Issue 执行；不停止 daemon 或其他 Issue。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

Agent Runner 会先创建 Draft PR，再运行独立 verifier。verifier 尚未结束时，操作者目前没有只停止当前 Issue 验证并接手审查的产品入口。现有全局接管会影响同一 daemon 中的多个在途任务，不能安全替代单 Issue 接管。

如果操作者此时在 GitHub 手动合并，runner 可能仍继续完成 verifier 后续处理。取消验证也不能被误记为验证通过。操作者需要一个按 Issue 精确定位、能处理并发完成、留下交接记录的路径。

### Interpretation (解读回显)

以下行为样例是本 PRD 的可纠正版本。每行会成为 §7 Realistic Validation Plan 中对应的验收 oracle；修改操作或结果单元格，就会修改验收标准。

#### 行为样例

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | Issue 的 verifier 正在运行时，在详情页选择“接管此 Issue”并确认 | 只有该 Issue 的 verifier 子进程停止；Draft PR 保留；Issue 转入人工 review；真实页面清楚呈递 verifier 状态、目标提交、接管范围和下一步；没有 validation/verifier-passed；merge queue 不自动合并 |
| 🤖 自动验证 | verifier 已先完成，随后操作者提交接管请求 | 请求被拒绝为状态已变化；已完成 verdict 保持原样；不改 Issue 或 PR 状态 |
| 🤖 自动验证 | verifier 完成与接管请求并发发生 | 只有一个结果生效；不得同时记录 verifier PASS 和人工接管，也不得把 PR 状态回退 |
| 👀 人审 + 自动验证 | 接管成功后打开关联 Draft PR | 可继续检查当前 HEAD；合并由操作者在现有验证签收和仓库保护规则下手动执行；自动合并仍因缺少 verifier 通过状态而被阻断 |
| 🤖 自动验证 | verifier 运行期间，操作者在 GitHub 外部合并 PR | runner 在应用 verdict 前发现合并时不写入标签或启动后续阶段；若合并与标签写入同时发生，后置 fresh read 会清理旧标签并停止后续阶段。分支保护负责阻止绕过 required check 的合并 |

这些行为行会逐项映射到 §7 的验收 oracle。修改样例即修改验收标准。

#### 我默默定了这些

- 接管入口仅在当前 Issue 的 verifier 确实运行、且其执行 owner 在本机可控制时出现。
- 操作者确认后，系统先请求精确运行身份和 HEAD 的取消；只有 verifier 子进程已停止后才报告“已交接”。
- 接管保留 Draft PR 与现有证据，不自动将 PR 标为 Ready，也不合并。
- 接管不是验证结论：不产生 PASS、RED、YELLOW，不添加 verifier 通过标签；持久的 autopilot hold 会暂停该 PR 的自动合并，直到当前 HEAD 有新的真实 GREEN。
- 人工 review 使用现有 agent/review 状态；人工自行打开 PR、检查当前 HEAD，并使用现有 Realistic Validation 签收和仓库保护规则决定是否合并。
- verifier 结果应用前重新读取 PR 状态和 HEAD；已合并或 HEAD 已变化时丢弃旧结果。
- 当前安装以本机单用户 Console 和共享本地状态库为边界；远程主机上的运行不提供接管操作。
- 视觉范围限定在 Dashboard 的 Issue 详情和接管确认/结果状态；保留现有 Dashboard 导航和信息架构，交互原型用于表达页面信息层级和状态呈递目标。

#### 我理解为不做

- 不提供停止整个 daemon、接管其他 Issue 或远程主机任务的入口。
- 不把取消当作验证通过，不自动将 Draft PR 转为 Ready，不自动合并。
- 不重写 verifier 的判断规则、Realistic Validation 内容或 PR 证据格式。

本 PRD 解决的是“操作者能否把一个仍在验证的 Issue 安全交给自己”。接管只终止该 Issue 当前 verifier，并把 PR 留给人检查；它不代表实现已经通过验证。只有操作者明确完成现有人工审查和签收后，才能手动决定是否合并。runner 不得将过期 verdict 应用到已变化或已合并的 PR。

### What The User Gets

操作者在真实 Dashboard 的 Issue 详情中一眼看到 verifier 当前状态、目标 HEAD 和接管范围。运行状态卡片提供明确的接管动作；确认后，页面区分处理中、已交接、状态变化和失败，并提供保留的 Draft PR 入口。页面按交互原型的信息层级突出状态与下一步，同时保留 Console 的现有导航和页面关系。操作者可以继续审查并手动合并；其他 Issue 和 daemon 继续运行。

### Measurable Objectives

- 接管请求在运行身份或 HEAD 不匹配时不产生副作用，并明确告知状态已变化。
- 接管成功时，目标 verifier 进程组已退出；daemon 和其他 Issue 的进程仍存活。
- 接管路径不写入 verifier PASS、RED、YELLOW 或 validation/verifier-passed；merge queue 对 autopilot_hold 状态 fail closed，即使仓库关闭 require_verifier_pass。
- verifier 完成与人工接管同时发生时仅一个结果生效；PR 合并或 HEAD 漂移时，runner 停止后续自动阶段，并清理可清理的过期 verifier 标签。
- 操作者可以通过真实 Console Issue 详情完成接管，并看到 Draft PR、HEAD 和人工交接状态。
- 在桌面和窄视口下，真实 Issue 详情都能区分 verifier 状态、目标 HEAD、操作范围与下一步；接管结果不被长文本、滚动容器或确认对话框遮挡。

## 2. Human Review Map (介入与风险地图)

### 决定：接管停止 verifier，但把审查和合并留给人

建议将“接管”定义为停止当前 Issue 的 verifier，并把该 Issue 转到人工 review。操作者随后打开保留的 Draft PR，自行检查当前 HEAD，并通过现有验证签收和仓库保护规则决定是否合并。这样能缩短等待，同时不把未完成验证伪装成通过，也不会影响其他任务。

真实 Dashboard 的 Issue 详情也纳入本次交付：以交互原型的状态层级和信息组织为参考，让运行状态、目标 HEAD、接管范围、操作和结果容易辨认，同时保留现有导航和仓库/Issue 选择关系。视觉范围限于 Issue 详情及其确认/结果状态，不扩展为全 Console 改版。

主要风险是把“取消”误读为“验证通过”，导致自动合并或跳过人工审查。PRD 因此要求取消留下独立状态、verifier 通过标签保持缺失，并设置不依赖 require_verifier_pass 配置的 autopilot hold。

**请确认：** 是否采用这条路径：接管只停止当前 Issue 的 verifier；PR 保持 Draft；之后由你在现有验证签收和分支保护规则下手动审查及决定合并，并按交互原型的信息层级完善真实 Dashboard Issue 详情？

**验收：** 接管后真实页面和 Issue 记录都明确显示“人工接管，verifier 未完成”；操作者能辨认当前状态、目标 HEAD 和 PR 入口；桌面与窄视口下状态和操作都可见；自动合并未获准；PR 和其验证证据仍可访问。若目标仓库没有将现有验证签收 check 设为 required，产品不承诺阻止具有权限的人绕过该仓库规则。

**自动门禁，不需要逐项人工审阅：** 精确运行身份校验、取消与完成竞态、子进程树隔离、PR HEAD/merged 状态复核、autopilot hold、验证标签不变、API 状态码、Console 页面和类型契约由自动 oracle 与独立 verifier 检查。

**本次明确不涉及：** 业务数据库迁移；跨主机取消；daemon 全局控制；全局 autopilot 配置或 branch protection 改动。

## 3. Usage And Impact After Implementation

- **Console 操作者**：在 Dashboard 的 Issue 详情查看 verifier 状态、关联 PR 和当前 HEAD。只有存在本机活动 verifier 时才能发起接管。确认后等待“已交接”结果，再通过 PR 链接审查。页面不会把“请求已提交”提前显示为“进程已停止”。
- **PR reviewer / 合并操作者**：接管后继续使用同一 Draft PR 和已有验证材料。若要合并，先自行检查当前 PR HEAD，按仓库已配置的 Realistic Validation 签收与分支保护规则操作；操作者手动将 Draft 标为 Ready 并决定是否合并。接管不改变 branch protection。required check 只有在仓库管理员已配置时才会物理阻止合并。
- **Runner / daemon 操作者**：仅目标 Issue 的 verifier 被取消。daemon 和其他 Issue 继续运行。runner 保存取消结果，并让 post-PR supervisor、review_once 与 merge queue 跳过该 PR，直到当前 HEAD 有新的 verifier GREEN。
- **自动合并队列**：接管后暂停该 PR 的自动合并；即使 require_verifier_pass 配置关闭也不例外。新 verifier run 对当前 HEAD 获得真实 GREEN 后才能解除此阻断。
- **API / 集成调用方**：现有白名单 Issue action 增加一种动作和运行身份条件。调用方仍使用 Console 的本机 API；不接受任意进程号、命令或文件路径。

兼容影响：未启用 verifier、非 verifier 阶段、旧运行记录和旧 Console 客户端继续使用现有行为；新字段可缺省。frontend-admin 不参与此功能。产品沿用仓库现有 branch protection 配置；若目标仓库未将 Realistic Validation sign-off 设为 required check，手动合并仍由其 GitHub 权限策略决定。

## 4. Requirement Shape

- **Actor**：本机 Console 操作者、当前 Issue 的 Agent Runner、PR reviewer / 合并操作者。
- **Trigger**：某个 Issue 的独立 verifier 处于活动状态，操作者在 Console 中明确确认接管。
- **Expected behavior**：按运行身份和 PR HEAD 取消目标 verifier；保留 PR；把 Issue 交给人工 review；不产生 verifier verdict 或自动合并资格；处理并发完成和外部合并。
- **Scope boundary**：仅支持同一主机上由当前 Runner 管理的单 Issue verifier；不提供 daemon/其他 Issue/远程运行控制。
- **Presentation boundary**：视觉改进聚焦于真实 Dashboard 的 Issue 详情、接管确认和结果状态；以交互原型的信息层级为参考，保留现有导航和仓库/Issue 选择关系。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 当前路径和已有事实

- `docs/guides/agent-runner.md` 描述的发布顺序是：先创建或复用 Draft PR，再运行 verifier；只有 verifier 通过后才进入 pre-PR AI code review。
- `run_agent_execution_loop.py` 在 `before_verifier_callback` 发布 Draft PR 后调用 `run_verifier_gate`。verifier 的 RED 进入已有修复循环；GREEN 由发布用例设置 `validation/verifier-passed`。
- `run_verifier_agent.py` 通过 `IProcessRunner` 执行 verifier CLI；`process_runner.py` 已有终止子进程树的实现，但 Console 的 `IRunnerProcessSupervisor.stop()` 管理的是已登记的完整 Runner 进程，不是目标 Issue 内的 verifier 子进程。
- `console_actions.py` 和 `POST /agent-runner/console/repositories/{repo_id}/issues/{issue_number}/actions` 已提供 Issue 级白名单动作。当前动作是 retry、blocked continue 和 failed publish recovery。
- `IssueDetail` 是 Dashboard 右侧 Issue 详情组件，显示 labels、PR、worktree、事件时间线和异常；其中可以增量呈递当前验证状态和接管操作。
- `.github/workflows/validation-gate.yml` 检查 PR body 的 Realistic Validation 人工签收项。仓库说明需要将该 check 配为 required。自动合并队列另检查 `validation/verifier-passed`；人工接管不得补这个标签。

### Existing Path / Reuse Candidates

- 在 execute_issue_action 的白名单中增加专用动作；继续复用现有 Console action 审计和错误响应。
- 在 `run_verifier_gate` / `run_verifier_agent` 执行期间注册当前 repo_id + issue_number + run_id + head_sha，在 verifier 子进程等待过程中轮询精确取消请求。
- 现有 IProcessRunner 是阻塞式接口；只复用 process_runner.py 内部已有的子进程树终止实现，并为 verifier 增加可选取消检查回调。不得调用 stop_runner_process 去停止 daemon。
- 用同一 console.history_db_path 的 SQLite 实现跨 Console API 进程与 Runner 进程共享控制请求。新建 IIssueRunControl 核心端口；不把命令写进只读职责的生命周期账本或运行历史接口。
- 在现有 Issue monitoring snapshot 中暴露活动 verifier 的简化状态，在现有 `IssueDetail` 中呈递状态和动作；Issue 详情的控制状态从共享 store fresh read，不从 30 秒 overview cache 推断。
- 现有 validation/verifier-passed 标签与 autopilot 检查继续作为独立验证结论事实源；取消不复用或伪造 verdict API。扩展 agent_runner_merge_queue.py，在有接管记录时检查 autopilot_hold。

### Architecture Constraints

- 后端边界保持 api -> core -> engines -> infrastructure。HTTP 路由只校验请求并调用 use case；运行状态与竞争规则在 core；SQLite 和文件/进程具体操作留在 infrastructure。
- IRunHistoryStore / IPrdLifecycleStore 是旁路观测账本，不能被改成控制总线。控制请求必须有独立端口和可原子更新的基础设施实现。
- 同一机上的 Console 与 Runner 使用 console.history_db_path。不新增端口、常驻服务或网络控制面。
- 本机 API 继续沿用 Console 的单用户与 loopback 信任边界；请求只能以仓库、Issue、run id 和 HEAD SHA 作为目标，不能指定 PID 或任意命令。
- verifier 被取消时必须结束其独立子进程树并等待回收；禁止杀 daemon、builder、reviewer 或其他 Issue 的进程。
- SQLite schema 只属于 Console 本地状态库；不新增 SQLAlchemy 模型或 Alembic migration。
- PRD 清单和变更树是规划入口，不是穷尽列表；实现前用 §7 Executor Drift Guard 重新搜索接口、调用点和文档。

### Frontend Impact

完整栈变更，涉及 `frontend-public/`（Next.js 16、React 19、TypeScript、pnpm）。主入口为 `/app/dashboard` 选中 Issue 后显示的 `IssueDetail`；实际 Dashboard 在宽屏以仓库/Issue 区和详情区并列，窄屏改为单列。修改 `frontend-public/components/agent-runner/issue-detail.tsx`，同步 `frontend-public/lib/api/console.ts` 与 `frontend-public/lib/api/types.ts`。后端监控 DTO / endpoint 提供活动 verifier 和 current-head 信息；Issue action API 接收接管请求。

视觉实现沿用 `frontend-public/components/ui/` 中的 Card、Badge、Button 和 Dialog，以及真实 Dashboard 的父级布局、主题和滚动边界。Issue 详情应先呈递 verifier 状态，再让操作者识别 Issue、运行身份、目标 HEAD、接管影响范围和 PR 入口；长时间线等辅助信息保持次级层级。状态卡片、确认对话框、处理中、成功、冲突和失败都必须由真实页面状态驱动。桌面和窄视口均须覆盖，不能只在静态 HTML 原型里成立。

E2E 入口是仓库级 `just e2e`，测试包说明位于 `tests/playwright-e2e/README.md`。`frontend-public/package.json` 没有单独 E2E script；前端检查脚本为 `pnpm typecheck`、`pnpm lint`、`pnpm build`。

### Existing PRD Relationship

- tasks/pending/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md：同属 Issue/生命周期详情视图，可能并行触达 IssueDetail 或时间线；没有行为依赖。实施时协调组件边界并复用其状态呈递，不等待其交付。
- tasks/pending/P1-FEAT-20261009-123512-kc-agentic-entry-and-stall-supervision.md：讨论本机停滞监督，但明确排除 Console 改造、远端进程终止和其他 Runner 接管。本 PRD 限于操作者主动停止一个 verifier，相邻但不重复，也不依赖它。
- tasks/archive/P1-FEAT-20260628-041733-realistic-validation-independent-verifier-gate.md：定义 PASS/RED/YELLOW 和 validation/verifier-passed 的门禁语义。本 PRD 保持这些语义，新增的取消结果不是第四种 verdict，也不改变既有验证要求。
- tasks/archive/P1-FEAT-20260611-205725-agent-runner-unified-ops-console.md：Console 的本机操作和进程监督边界。单 Issue 控制要复用 Console action 路径，但不能用全局 Runner process stop。

### Potential Redundancy Risks

- 不新建第二套 Issue 事件或监控时间线；只新增控制状态，并在现有 snapshot 中投影当前活动状态。
- 不复用 daemon PID、全局 --takeover 或 Console 的全进程 stop 作为“单 Issue”取消。
- 不将取消映射成 ValidationVerdict，也不引入另一套 verifier-pass 标签。
- 不引入网络服务、Redis、定时轮询服务或跨主机协调。

## 6. Recommendation

### Recommended Approach

在现有 Issue action API 上增加一个按 run id 和 HEAD SHA 定位的接管动作。Console 将请求写入共享 SQLite 控制状态；活动 verifier 在自己的子进程等待循环中观察请求并结束自己的子进程树。runner 与 API 通过原子状态转移裁决“verifier 已结束”与“人请求接管”的竞态。post-PR supervisor、review_once 和 merge queue 读取接管 hold，暂停该 PR 的全部自动处理。

这条路径用到当前 Console 和 verifier 的边界，不需要停止整个 Runner。控制状态独立于只读观测账本，可以明确表达请求、确认和终态。SQLite 已是 Console 与 runner 的共享本机存储，支持跨进程原子更新；不新增服务。

**ROI 判断：** 紧急接管的发生频率未知，但等待 verifier 时的影响高。实现成本集中在共享 SQLite 控制状态、verifier 子进程可取消等待，以及三个现有自动处理入口读取 hold。该方案复用当前 Console、Runner 和状态库，不引入常驻服务或跨主机协调；这是满足“只停一个 Issue、确认确实停下、避免自动流程继续接手”的最小完整路径。仅提供 GitHub 手动操作成本更低，但不能停止 verifier，也不能确认 runner 已交接。

### Proposed Solution Summary (实现机制)

- Runner 在 verifier 开始前写入活动身份：仓库、Issue、PR number、execution/run id、verifier phase、PR HEAD SHA 和状态 running。
- Console snapshot 只公开可展示字段和是否可接管；按钮只在当前记录仍为 running 时显示。
- 接管请求携带 UI 读到的 run id 和 HEAD SHA。core 重新读取活动状态并通过 SQLite 原子 compare-and-set 将 running 改为 takeover_requested；运行已结束、HEAD 漂移或 id 不符时返回冲突，不写接受状态。
- IProcessRunner 增加默认关闭的可选取消检查回调；只有 verifier 调用提供该回调。ProcessRunner 在等待过程中调用回调；若目标请求匹配，则终止并回收当前 verifier 子进程树，再抛出可识别的 InterruptedError。其他调用不传回调，保持现有超时与重试行为。
- verifier 用例只在 control store 确认存在匹配的 takeover_requested 时，将 InterruptedError 归类为 cancelled_by_human；保存可用的部分输出但不解析 verdict，然后由 runner 完成交接。
- 交接完成后，用现有状态转换将 Issue 置为 agent/review，写一条带 run id 和 HEAD SHA 的 Issue comment / 审计记录；Draft PR 保持原状。将控制记录状态设为 taken_over 并置 autopilot_hold=true；post-PR supervisor、review_once 和 merge queue 都跳过该 PR，即使 require_verifier_pass 被关闭。只有新 verifier 对当前 HEAD 返回 GREEN 才能清除 hold 并恢复自动处理。
- verifier 正常完成时先原子认领完成状态；然后 fresh-read PR 的 merged 状态与 HEAD。若 PR 已合并，停止后续阶段；若 PR 仍 open 但 HEAD 不同，不应用 verdict，记录 verifier_stale、设置 autopilot_hold 并交由人工检查。任何 verdict side effect 后再次读取 PR；发现期间发生合并或 HEAD 漂移时清理旧 verifier 标签并停止后续阶段。
- 新表使用现有 Console SQLite schema version 管理；PR 合并 / 关闭后按 Console store 的终态保留策略清理。

### Alternatives Considered

- **停止整个 daemon 进程**：会中断其他 Issue，与用户需要“只接管这个 Issue”冲突，拒绝。
- **调用 stop_runner_process**：该 API 管理完整托管进程，不持有 Issue verifier 的专属运行身份，会扩大停止范围，拒绝。
- **仅增加前端按钮但不发取消信号**：会让用户误以为控制成功；后台 verifier 仍可能写 verdict 或继续审查，拒绝。
- **通过 GitHub 标签作为唯一控制信号**：daemon 必须等待远程轮询，且标签缺少当前 run id 和 HEAD 的原子条件；不作为控制通道。
- **把请求写进生命周期事件**：生命周期账本契约是旁路、追加式、只供观察，不参与编排决策；拒绝混用。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

1. **活动状态登记**：在 run_agent_execution_loop.py 进入 verifier 前，用新的 IIssueRunControl 写入当前 execution id、Issue、PR number、PR head SHA 和 running。若当前 Issue 没有 PR、验证未启用或没有活动 verifier，则不开放接管。
2. **Console 查询**：monitoring use case 将活动控制记录作为可选 DTO 加到 IssueMonitoringSnapshot。不可用或无活动记录时返回 null / can_take_over=false；不得仅从“最新生命周期事件”推测 verifier 仍在运行。
3. **请求接管**：execute_issue_action 对动作 take_over_verifier 校验仓库、Issue 正数、run id、HEAD SHA，并通过 IIssueRunControl.request_takeover(expected_run_id, expected_head_sha) 原子更新。重复请求幂等返回当前状态；目标变化由专用冲突异常映射为 HTTP 409；其他错误沿用 Console 审计。HTTP 请求最多等待 5 秒；若取消仍在处理，返回 202 和 takeover_requested，界面继续查询状态，不把“请求已登记”伪报为“进程已停止”。
4. **执行取消**：run_agent_with_prompt_resilient 只在 verifier 调用中传入取消检查回调；ProcessRunner 等待期间以不超过 1 秒的间隔查询 IIssueRunControl。收到匹配请求后，ProcessRunner 只终止 verifier CLI 的子进程组并等待退出，再抛出 InterruptedError；runner 仅在 control store 再次确认 run id 与 HEAD 后将其映射为人工取消。若 runner 确认无法终止或回收目标进程，控制状态转为 takeover_failed；Issue 不转 agent/review，操作者看到可重试的失败原因。等待超过 5 秒本身只代表仍在处理中，不作为取消失败。
5. **交接完成**：记录 cancelled_by_human，保存可用的部分 verifier 输出并明确无 verdict；清除仅属于 verifier 通过的旧标签；将控制状态设为 taken_over 并置 autopilot_hold=true；Issue 转 agent/review；保留同一个 Draft PR，不自动改为 Ready。
6. **正常完成与竞争**：verifier 在写 verdict 前 compare-and-set running -> verifier_completed。如果接管先赢，verdict 不可发布；如果完成先赢，接管请求返回冲突。随后 fresh-read PR；若 PR 已合并，记录 pr_merged 并停止后续阶段；若 PR 仍 open 但 HEAD 已变化，记录 verifier_stale、置 autopilot_hold=true 并交由人工检查，不应用旧 verdict 或启动后续自动阶段。只有 PR 仍 open 且 HEAD 等于 verifier 输入 SHA 时才可应用 verdict。应用 GitHub 副作用后再次读取 PR；若期间发生合并或 HEAD 漂移，立即清理过期 verifier 标签并停止后续阶段。GitHub 的读取与写入不是原子操作，因此 branch protection / required checks 仍是防止外部绕过合并的最终门禁。
7. **重启与孤儿请求**：控制请求始终带 run id 和 SHA；新 run 不消费旧 run 的待处理请求。进程重启后发现没有对应活动 owner 的待处理行时，标记为失效并要求人工查看，不自动停止其他进程或重放请求。
8. **界面**：在 IssueDetail 内将当前 verifier 状态、head SHA、PR 链接和接管动作放在同一块。点击后用确认 Dialog 说明“只停止此 Issue verifier；PR 保持 Draft；不会自动合并”。请求期间按钮禁用并显示“正在停止”；超过 5 秒仍未确认时显示“仍在停止，尚未交接”并继续查询；收到 Runner 完成确认后显示“已交给你审查”。将操作错误分为“运行已结束 / HEAD 已变化 / 无法停止”，提供刷新或打开 PR 的具体下一步。低频诊断信息折叠展示，默认先呈递状态和动作。

### Change Impact Tree

以下为当前分析到的主要触达文件，实施时用 drift guard 补全。

~~~text
.
├── src/backend/core/shared/interfaces/runner_console.py [修改]
│   【总结】增加独立 Issue 运行控制端口与运行身份/请求状态模型
├── src/backend/core/shared/interfaces/agent_runner.py [修改]
│   【总结】为阻塞式命令执行端口增加默认关闭的可选取消回调契约
├── src/backend/core/use_cases/agent_runner_issue_control.py [新增]
│   【总结】定义按 Issue、run id 和 HEAD SHA 校验并提交接管请求的用例
├── src/backend/core/use_cases/console_actions.py [修改]
│   【总结】把 take_over_verifier 加入 Issue 动作白名单并保留审计
├── src/backend/core/use_cases/run_agent_execution_loop.py [修改]
│   【总结】登记活动 verifier 并串联接管、正常完成和 HEAD 复核
├── src/backend/core/use_cases/run_verifier_agent.py [修改]
│   【总结】在 verifier 子进程等待期间轮询精确取消并保存无 verdict 的部分输出
├── src/backend/core/use_cases/run_agent_once.py [修改]
│   【总结】只向 verifier CLI 调用传递取消检查，并阻止取消进入超时恢复或瞬态重试
├── src/backend/core/use_cases/agent_runner_publication.py [修改]
│   【总结】verdict 应用前确认 PR 未合并且 HEAD 与验证输入一致
├── src/backend/core/use_cases/agent_runner_merge_queue.py [修改]
│   【总结】merge queue 对人工接管中的 PR fail closed，不受 verifier 标签配置关闭影响
├── src/backend/core/use_cases/agent_runner_supervisor.py [修改]
│   【总结】post-PR supervisor 对人工接管中的 Issue 不再启动 Agent 审查或修复
├── src/backend/core/use_cases/review_once.py [修改]
│   【总结】review_once 跳过仍由人接管的 Issue
├── src/backend/core/use_cases/agent_runner_monitor.py [修改]
│   【总结】向 Issue snapshot 添加当前 verifier 状态和接管可用性
├── src/backend/api/routes/agent_runner.py [修改]
│   【总结】将运行控制投影到现有 Issue monitoring 响应
├── src/backend/api/routes/agent_runner_console.py [修改]
│   【总结】为 Issue 接管动作解析显式 run id 与 HEAD SHA
├── src/backend/infrastructure/persistence/console_store.py [修改]
│   【总结】实现 SQLite 活动运行状态、接管请求的原子状态转移和 schema 更新
├── src/backend/infrastructure/process_runner.py [修改]
│   【总结】实现取消回调轮询，并用现有子进程树清理逻辑终止和回收 verifier
├── src/backend/engines/agent_runner/factories/__init__.py [修改]
│   【总结】将 SQLite issue control adapter 装配给 Console 与 runner
├── frontend-public/components/agent-runner/issue-detail.tsx [修改]
│   【总结】按原型的信息层级呈递 verifier 状态、确认接管、取消等待与人工审查入口，并复用真实 Dashboard 组件和响应式布局
├── frontend-public/lib/api/console.ts [修改]
│   【总结】添加带 run id 和 HEAD SHA 的接管 API 调用
├── frontend-public/lib/api/types.ts [修改]
│   【总结】同步活动 verifier 与接管响应类型
├── tests/test_console_store.py [修改]
│   【总结】验证 SQLite 控制状态原子转移、幂等和过期请求隔离
├── tests/test_console_actions.py [修改]
│   【总结】验证合法接管、状态冲突、错误审计和非目标 Issue 不变
├── tests/test_run_verifier_agent.py [修改]
│   【总结】验证只结束 verifier 子进程组并保留 cancelled 无 verdict 结果
├── tests/test_process_runner.py [修改]
│   【总结】验证取消回调触发时终止并回收子进程树，默认调用行为不变
├── tests/test_agent_runner_merge_queue.py [修改]
│   【总结】验证接管态 PR 不会被自动合并，包括关闭 verifier-pass 配置的场景
├── tests/test_agent_runner_supervisor_entrypoints.py [修改]
│   【总结】验证接管态 Issue 不会重新启动 post-PR supervisor
├── tests/test_pr_supervisor.py [修改]
│   【总结】验证 review_once 对人工接管 Issue 保持跳过
├── tests/test_agent_runner_console_api.py [修改]
│   【总结】验证 Console action 请求契约与 stale-state 409
├── tests/playwright-e2e/tests/workflows/verifier-takeover.spec.ts [新增]
│   【总结】从真实 Dashboard Issue 详情验证确认、等待、结果呈递、失败态及桌面/窄视口布局
└── docs/guides/agent-runner.md [修改]
    【总结】说明单 Issue 人工接管、手动合并边界和 verifier 竞态语义
~~~

### Risk Classification Register

| Change point | Tier | Decisive dimension / override | Intervention | Failure-discriminating oracle |
|---|---|---|---|---|
| SQLite 跨进程控制、verifier 子进程取消与 autopilot hold | R3 | 并发、取消范围和运行 owner 错误可能停止错误任务或误放行 | 执行器 + 独立 verifier；人看真实交接状态 | rv-1、rv-2：确认目标进程退出、daemon/其他 Issue 存活，接管态不触发 supervisor/review_once/merge queue，且竞争只产生一个终态 |
| PR HEAD / merged 状态复核与 verdict 副作用 | R3 | 过期结论写标签可能错误放行自动合并 | 执行器 + 独立 verifier | rv-2：变更 HEAD 或先合并 PR 后，旧 verdict 不改变 PR 标签或后续阶段 |
| Console 接管交互与详情视觉层级 | R2 | 用户必须辨认目标 Issue、状态、HEAD 和操作后果 | 人审 + 自动验证 | rv-1：真实页面在桌面与窄视口呈递运行、确认、处理中、交接结果，且不脱离生产布局 |
| 文档和状态说明 | R1 | 文档流程可能与代码状态不一致 | 执行器 + lint / 文档审阅 | §9 文档项：guide 描述与状态迁移、人工签收边界一致 |

### Executor Drift Guard

实现前重新搜索并检查所有调用点；列表不是穷尽清单：

~~~bash
rg -n "run_verifier_gate|run_verifier_agent|apply_verifier_verdict_to_pr|before_verifier_callback" src/backend
rg -n "execute_issue_action|ISSUE_ACTIONS|IssueActionRequest|IssueMonitoringSnapshot|build_issue_snapshot" src/backend frontend-public
rg -n "stop_runner_process|stop_repository_persistent_processes|run_takeover|--takeover" src/backend docs/guides/agent-runner.md
rg -n "validation/verifier-passed|require_verifier_pass|verifier-passed" src/backend docs .github/workflows
rg -n "executeIssueAction|IssueDetail|IssueMonitoringSnapshot" frontend-public
~~~

确认 Console API 与 runner 读取的是同一个 console.history_db_path；确认新控制状态不被 monitoring cache 当作事实源；确认所有 verdict side effects 都在 merged/head 复核之后。若发现 daemon worker 不在本机共享 store 上运行，按本 PRD 的边界拒绝接管并明确展示原因，不改为停止完整 daemon。

### Flow / Architecture Diagram

~~~mermaid
sequenceDiagram
    participant H as Console 操作者
    participant UI as Issue 详情
    participant API as Console API
    participant DB as 本机 SQLite 控制状态
    participant R as Issue Runner
    participant V as Verifier 子进程组
    participant GH as GitHub PR
    H->>UI: 确认接管当前 run / HEAD
    UI->>API: take_over_verifier(run_id, head_sha)
    API->>DB: 原子 running → takeover_requested
    DB-->>R: verifier 等待循环发现请求
    R->>V: 终止并回收目标 verifier 子进程组
    V-->>R: 已退出，无 verdict
    R->>GH: 保留 Draft PR，记录人工 review 状态
    R->>DB: takeover_requested → taken_over
    DB-->>UI: 交接完成
    H->>GH: 检查 HEAD 与现有验证证据，手动决定是否合并
~~~

### 交互原型与生产界面目标

本 PRD 关联的交互原型为 [`docs/prototypes/verifier-human-takeover.html`](../../docs/prototypes/verifier-human-takeover.html)，说明见 [`docs/prototypes/verifier-human-takeover.md`](../../docs/prototypes/verifier-human-takeover.md)。原型演示 Dashboard 详情、verifier 状态、接管确认、处理中和结果状态，是信息层级与交互的视觉参考；其静态 fixture 不连接真实 API、SQLite 或进程，不能代替生产实现或真实入口验证。

生产页面需在真实 `/app/dashboard` 内保持现有 Dashboard 导航、仓库/Issue 选择区和详情区关系，并在选中 Issue 的详情中实现以下层级：

- 首屏优先呈递 Issue 标识、verifier 状态和目标 HEAD；当前运行身份与受影响范围可直接辨认。
- 仅在目标 Issue 存在可控制的活动 verifier 时显示接管动作；确认对话框说明只停止该 Issue、保留 Draft PR、取消不代表通过且不会自动合并。
- 确认后显示处理中，收到进程退出确认后再显示人工交接成功；冲突、HEAD 变化、owner 不可达和停止失败均显示准确原因与下一步。
- PR 链接和当前 HEAD 在交接结果中持续可见；时间线、异常和诊断信息保持次级阅读层级。
- 复用生产 UI 组件、主题、Dialog/Portal、详情滚动容器和响应式布局。不得把原型的假数据或独立 CSS 外壳作为产品实现。

视觉证据在同一状态、主题、数据和视口下并排呈递原型目标与真实 Dashboard 页面。至少覆盖桌面 `1440×900` 与窄视口 `390×844`；窄视口检查详情滚动、按钮可达、对话框边界和横向溢出。

下面的线框只表达状态和动作关系，具体信息层级与呈递风格以交互原型为参考。

~~~text
┌─ Issue #42 · PR #58 ─────────────────────────────────────┐
│ Verifier 正在运行             PR: Draft · HEAD: a1b2c3d │
│                                                          │
│ [接管此 Issue]                                            │
│                                                          │
│ 确认接管                                                   │
│ 只停止 Issue #42 当前 verifier。PR 会保持 Draft；         │
│ 取消不会被记为验证通过，也不会自动合并。                    │
│                                  [返回] [停止并交给我]     │
└──────────────────────────────────────────────────────────┘

接管完成后： “已交给你审查 · verifier 未完成” + [打开 Draft PR]
~~~

线框说明控制关系，交互原型说明视觉与状态呈递目标。实施证据需将原型画面与真实 Dashboard 页面按相同状态、数据、主题和视口配对，并标注验证层级与真实入口。

### Interactive Prototype Change Log

| File Path | Change Type | Before | After | Why |
|---|---|---|---|---|
| `docs/prototypes/verifier-human-takeover.html` | 新增 | 无 | 展示 Dashboard 中 verifier 运行、确认接管、处理中与交接结果 | 先评审真实 Issue 详情的信息层级和操作路径 |
| `docs/prototypes/assets/verifier-human-takeover.css` | 新增 | 无 | 实现静态 Dashboard fixture 的页面布局、状态卡片和确认对话框视觉 | 让目标层级可直接观察 |
| `docs/prototypes/assets/verifier-human-takeover.js` | 新增 | 无 | 以稳定 fixture 演示接管成功、冲突、HEAD 变化和失败分支 | 覆盖关键状态，不连接生产服务 |
| `docs/prototypes/verifier-human-takeover.md` | 新增 | 无 | 记录原型入口、场景交互、数据模型和模拟边界 | 让原型可复查和维护 |
| `docs/prototypes/assets/prototype-hub.js` | 修改 | 原型未登记 | 在 Prototype Hub 中增加本原型入口与元数据 | 让评审者能从现有原型目录发现和打开 |

### ER Diagram

新增本机 SQLite 运行控制记录；不触及业务数据库。

~~~mermaid
erDiagram
    ISSUE_RUN_CONTROL {
        string repo_id PK
        int issue_number PK
        int pr_number PK
        string run_id
        string head_sha
        string phase
        string state
        bool autopilot_hold
        string request_id
        string requested_at
        string completed_at
    }
~~~

运行状态只允许沿 running → takeover_requested → taken_over / takeover_failed，或 running → verifier_completed / verifier_stale / pr_merged 前进。成功接管和 HEAD 漂移时将 autopilot_hold 设为 true；post-PR supervisor、review_once 和 merge queue 检查该字段，不受 require_verifier_pass 配置影响。对于已置位的 hold，只有同一 PR 当前 HEAD 的新 verifier 返回 GREEN 后才能清除；RED、YELLOW、取消、HEAD 漂移或状态未知均保留 hold。PR 合并 / 关闭后可清理终态记录。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 操作者从真实 Console Issue 详情接管一个正在运行的 verifier，并只停止该 Issue。
  reviewer: human
  real_entry: "just e2e tests/workflows/verifier-takeover.spec.ts"
  expected: "真实 Dashboard Issue 详情按原型的信息层级呈递状态、目标 HEAD、作用范围和操作；确认后先显示处理中，目标 verifier 子进程树退出后才显示已交给你审查；Draft PR 和 HEAD 保留；Issue 为 agent/review；daemon 和第二个 Issue 继续运行；没有 verifier verdict 或 validation/verifier-passed；merge queue 不会合并该 PR。桌面 1440×900 与窄视口 390×844 均无内容遮挡、按钮不可达或横向溢出。"
  mock_boundary: "外部 GitHub CLI/API transport 和 verifier 可由确定性测试替身提供；真实浏览器、Next 页面、FastAPI Console API、SQLite 控制状态、执行编排、进程组终止和进程回收必须真实运行。"
  tier: R3
  test_layer: e2e
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/rv-1-verifier-takeover.png 与 rv-1-verifier-takeover-narrow.png；与 docs/prototypes/verifier-human-takeover.html 的对应状态按同状态、主题、数据和视口并列，覆盖桌面 1440×900 与窄视口 390×844。完成呈递需提供原型入口、真实页面截图和从 /app/dashboard 到目标状态的操作路径。人审可核对“verifier 未完成”、目标 PR HEAD、接管范围及真实布局。"
  critical_value_source: "Issue 详情 API 返回的活动 run_id 与 PR head_sha；接管请求必须原样使用这两个值。"
  must_cross: "真实浏览器 /app/dashboard → IssueDetail → Console API → 本机 SQLite compare-and-set → 当前 runner wait loop → verifier process group → fresh monitoring / issue read。"
  forbidden_bypasses: "不得直接调用 core use case 代替页面/API；不得 mock SQLite control port、执行编排或 process termination；不得停止 daemon；不得由测试重建或替换 run_id / head_sha。"
  fresh_state_probe: "使用新的 SQLite connection 和新的 Console 请求读取 terminal control state、Issue label/comment、PR 状态；通过独立进程探测确认目标 verifier PID group 已退出，daemon 与第二个 Issue 的 probe process 仍存活。"
  final_tree_evidence: "保存完整 E2E 报告、测试输出、截图和当前 Git tree；任何控制状态、进程终止、API 或 IssueDetail 变更后重跑，并将证据绑定待交付最终 tree。"
  negative_control: "同一 E2E 测试将 UI 读取的 head_sha 替换为已过期值后再次提交接管；替换仅发生在测试边界。"
  expected_fail: "API 返回冲突；状态不变；目标 verifier 仍运行；Issue 不转 agent/review；没有 accepted takeover audit。"

- id: rv-2
  behavior: verifier 完成、人工接管和 PR 外部合并竞争时，只应用当前 run 与当前 HEAD 的有效结果。
  reviewer: verifier
  real_entry: "just e2e tests/workflows/verifier-takeover.spec.ts"
  expected: "completion 先赢时接管返回冲突；takeover 先赢时 verifier 结果不落地；PR 已合并时停止后续阶段；HEAD 漂移时旧 verdict 不生效、设置 verifier_stale 与 autopilot_hold 并停止自动处理；若外部变化发生在 verdict side effect 期间，后置 fresh read 清理过期标签；只有当前 HEAD 的新 GREEN 能清除 hold。"
  mock_boundary: "GitHub PR 读写边界可使用可编程 fake transport 构造并发顺序；Console API、SQLite 原子状态转移、runner orchestration 和 verdict side-effect 代码必须真实运行。"
  tier: R3
  test_layer: e2e
  required_for_acceptance: true
  critical_value_source: "活动控制记录中 verifier 启动时捕获的 head_sha；GitHub fresh read 返回的当前 head_sha 与 merged_at。"
  must_cross: "真实 Console action → SQLite CAS → verifier completion callback → fresh GitHub PR read → verdict label / subsequent review boundary。"
  forbidden_bypasses: "不得只测状态机 helper；不得用初始缓存 PR snapshot 代替 fresh GitHub read；不得将取消映射为任何 ValidationVerdict。"
  fresh_state_probe: "创建新的 store/API reader 与新的 GitHub client read，确认最终唯一状态、PR label、Issue label/comment 和后续阶段调用记录。"
  final_tree_evidence: "记录两种 CAS 顺序、merged PR 与 changed-head 场景的 E2E 报告和 Git tree；任何 side-effect 顺序变化后重跑。"
  negative_control: "在测试边界将最终 fresh PR read 替换为初始 head snapshot，使 changed-head 场景误用旧状态。"
  expected_fail: "负控必须使 rv-2 失败，明确指出旧 verdict 被错误应用到过期或已合并 PR。"
```

失败排查顺序：先检查 Console 与 daemon 是否使用同一 console.history_db_path，再检查 SQLite CAS 状态及 run/head 身份，之后检查 run_verifier_agent 的等待循环和 process group，最后检查 verdict 应用前读取的 PR head / merged 状态。若 E2E 失败只在 CI 出现，先检查 just e2e 启动的 backend/public stack 和 fake GitHub transport 配置。

### Validation Commands

按成本由低到高执行；以下为实现后计划命令，不在编写 PRD 时运行：

~~~bash
uv run pytest tests/test_console_store.py tests/test_console_actions.py tests/test_run_verifier_agent.py tests/test_agent_runner_console_api.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py tests/test_process_runner.py
(cd frontend-public && pnpm typecheck && pnpm lint && pnpm build)
just e2e tests/workflows/verifier-takeover.spec.ts
just test
~~~

just test 是本机核心验证档；无需为普通交付运行 just test all。just e2e 依照 Playwright README 启停或复用 stack。若前置服务、凭据或测试 transport 缺失，先修复 E2E 配置；不得把 component preview 截图称为真实入口验证。

### External Validation

No external validation required; repository evidence was sufficient.

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 生命周期时间线 PRD 与 IssueDetail 存在软文件重叠，实施时协调组件边界；不阻塞开工或交付。停滞监督 PRD 与本功能相邻但无依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 要看什么 | 呈递材料 | 约 10 秒自检 |
|---|---|---|
| 真实 Dashboard Issue 详情按原型的信息层级展示运行、确认、处理中和接管完成状态；保留 Draft PR 与目标 HEAD。 | 原型入口：[`docs/prototypes/verifier-human-takeover.html`](../../docs/prototypes/verifier-human-takeover.html)。实施后生成 `tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/rv-1-verifier-takeover.png` 与 `rv-1-verifier-takeover-narrow.png`，分别与同状态、同主题、同数据、同视口的生产页面并列，在 PR 证据评论呈递。 | 在原型与真实页面中核对状态、Issue 范围、HEAD 和 PR 入口；检查窄屏下按钮可达、对话框不越界且无横向溢出。打开真实入口：`just prd review tasks/pending/P1-FEAT-20261011-160913-verifier-human-takeover.md`。 |

reviewer: verifier 的 rv-2 并发、stale-head、外部合并和数据库状态 oracle 不放在人读呈递区；只在失败时上报。

当前 PRD 尚未实施，以上真实页面截图路径是交付目标，文件待实现后生成。交付消息需内嵌真实截图，并提供同状态、同视口的原型对照和从 Dashboard 进入的操作路径；只保存文件不展示视为未交付。

### 9.2 Acceptance Evidence Package

风险顺序：R3 接管和竞态证据优先；随后 R2 页面结果与类型、文档门禁。每项在实现后填写对应 rv-<n> 证据文件，并绑定最终 Git tree。

#### Human-Confirmed

- [ ] 确认接管语义为“只停止当前 Issue 的 verifier；保留 Draft PR；不产生 verifier PASS 或自动合并；之后由操作者手动审查并决定合并”。证据：rv-1-verifier-takeover.png 与 PR evidence comment 中同 viewport 的交互原型 / 真实页面对照。
- [ ] 确认真实 Dashboard Issue 详情达到原型的信息层级：运行状态、目标 HEAD、接管范围、PR 入口容易辨认，确认/处理中/成功/失败状态清楚，桌面和窄视口都可用。证据：`docs/prototypes/verifier-human-takeover.html` 与真实入口截图 `rv-1-verifier-takeover.png`、`rv-1-verifier-takeover-narrow.png` 按状态和视口配对；完成消息中内嵌呈递物。

#### Architecture Acceptance

- [ ] IssueRunControl 与观测型 IRunHistoryStore 职责分开；SQLite 适配器通过 compare-and-set 保证唯一终态。证据：tests/test_console_store.py 输出和 rv-2 报告。
- [ ] 控制记录绑定 repo_id + issue_number + run_id + head_sha；过期 run 请求不会影响后续 run。证据：SQLite fresh read 的 rv-1 / rv-2 报告。
- [ ] 不新增服务、Alembic migration 或远程控制面。证据：git diff --name-only 与数据库迁移目录审查。

#### Behavior Acceptance

- [ ] 取消只终止目标 verifier 子进程树，等待进程回收；daemon 和其他 Issue 继续运行。证据：rv-1 进程探测和 E2E 报告。
- [ ] 取消输出标明 cancelled_by_human 且无 verdict；不进入 RED recovery 或 pre-PR review。证据：rv-1 Issue comment、verifier 日志和后续阶段 probe。
- [ ] autopilot_hold 会阻止 post-PR supervisor、review_once 和 merge queue 继续自动处理，即使仓库关闭 require_verifier_pass；只有当前 HEAD 的新 verifier GREEN 才可清除 hold。证据：rv-2 自动入口 probes。
- [ ] verifier 完成与 takeover 竞争时只有一个本地终态；PR 已合并或 HEAD 漂移时旧 verdict 不生效、后续自动阶段停止，最终 GitHub 状态与控制记录一致。证据：rv-2 各种竞态顺序的 fresh-state 报告。

#### Frontend Acceptance

- [ ] Dashboard Issue 详情只在活动 verifier 可接管时显示动作；对话框明确停止范围和不自动合并；处理中和成功/冲突状态可辨认。证据：rv-1 真实入口截图及 Playwright trace。
- [ ] 真实 Issue 详情按原型的信息层级呈递状态、HEAD、接管范围和 PR 入口，并复用生产 Dashboard 的布局、主题、组件与 Dialog/Portal；桌面和窄视口下长文本、滚动和对话框均可用。证据：同状态、同主题、同数据、同视口的原型/生产页面对照截图及真实入口路径。
- [ ] 前端 action payload 原样传送当前 run_id 和 head_sha；响应类型与后端 DTO 对齐。证据：frontend-public typecheck 与 rv-1 network assertion。

#### Documentation Acceptance

- [ ] docs/guides/agent-runner.md 描述接管状态、Draft PR 保留、人工合并和 verifier 竞态边界；不将取消称为通过。证据：文档 diff 与文档链接检查。
- [ ] PRD 的 FR、Feature Overview、行为样例、oracle 和 checklist 一一对应。证据：rg -n '^## |^### ' tasks/pending/P1-FEAT-20261011-160913-verifier-human-takeover.md 和 PRD review。

#### Validation Acceptance

- [ ] uv run pytest tests/test_console_store.py tests/test_console_actions.py tests/test_run_verifier_agent.py tests/test_agent_runner_console_api.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py tests/test_process_runner.py 通过。证据：命令输出归档。
- [ ] (cd frontend-public && pnpm typecheck && pnpm lint && pnpm build) 通过。证据：三个命令输出归档。
- [ ] 通过真实浏览器入口 just e2e tests/workflows/verifier-takeover.spec.ts 验证 UI → API → SQLite → runner → verifier process group，且 fresh read 观察到 handoff；不得用组件预览替代。证据：rv-1 E2E 报告、trace、截图和 final-tree hash。
- [ ] rv-2 的 stale run、stale HEAD、verifier completion race 与 merged PR 场景通过；反例能使 oracle 报红。证据：rv-2-verifier-race.txt 和对应测试报告。
- [ ] 所有 R2/R3 行为证据均绑定交付 tree；相关实现变化后已重跑对应 oracle。证据：verification report 中的 tree id。

#### Delivery Readiness

- [ ] 所有用户可见截图与交互原型对照已在 PR evidence comment 呈递；完成消息内嵌 9.1 表面内容。证据：PR comment 链接与消息截图。
- [ ] PR 正文声明“接管不会自动接受或合并”；现有 Realistic Validation 签收要求和 branch protection 保持。证据：PR body 与 validation-gate required check 状态。
- [ ] PRD 已按仓库流程完成独立 verifier PASS、执行侧 Final Reconciliation，并随交付改动归档；仅 Human-Confirmed 项可保持未勾选。证据：verifier report、PRD archive path 与验收横幅。

#### Runner-Owned Gates

- [~] 独立 verifier 对本 PRD 的 R2/R3 证据与最终 Git tree 复核通过 — runner-owned gate: 独立验证。
- [~] PR 创建、证据评论发布和交付归档 — runner-owned gate: PR 发布与归档。

## 10. Functional Requirements

- **FR-1: 活动 verifier 可识别**：Console 的 Issue 状态必须能区分 verifier 正在运行、已请求接管、已接管、接管失败、验证已过期、已完成、PR 已合并、不可控制和状态未知；只有运行身份和 HEAD SHA 可复核的本机 verifier 可以接管。
- **FR-2: 精确接管请求**：接管请求必须绑定仓库、Issue、run id 和 HEAD SHA。旧请求、重复请求和状态变化须幂等或明确冲突，不得应用到新 run。
- **FR-3: 单 Issue 取消与人工交接**：接管确认后只终止该 Issue 当前 verifier 子进程树。runner 回收进程后才可标记交接完成、将 Issue 转至现有 agent/review 并记录 run id 与 HEAD SHA。保留 Draft PR 和验证证据；取消不是 verdict，不设置 validation/verifier-passed，不运行依赖 verifier PASS 的步骤。
- **FR-4: 竞态和过期结果保护**：verifier 完成与接管请求通过原子状态转移裁决；verdict 应用前后 fresh-read GitHub PR 状态。PR 已合并时停止后续阶段；PR HEAD 与 verifier 输入不同时，丢弃 verdict、设置 autopilot hold 并交由人工检查。GitHub 状态读取与写入之间仍存在外部竞态，最终合并保护由仓库 required checks / branch protection 提供。
- **FR-5: 人工审查和自动合并边界**：接管不会将 Draft PR 标记为 Ready 或调用 merge；操作者必须自行审查当前 HEAD 并使用现有签收 / branch protection 决定手动合并。post-PR supervisor、review_once 和 merge queue 都必须跳过 autopilot_hold=true 的 PR，即使 require_verifier_pass=false；只有新 verifier 对当前 HEAD 给出真实 GREEN 后才可解除自动处理阻断。
- **FR-6: 可观察失败**：等待超时但 runner 仍在处理时，显示尚未确认并继续查询；owner 不可达、存储不可用、确认无法回收进程或运行身份不匹配时，不得宣称交接完成，须显示可操作错误并记录请求结果。不能确定取消范围时必须 fail closed。
- **FR-7: 真实页面状态可辨认**：真实 Dashboard Issue 详情按清晰的信息层级呈递当前状态、目标 HEAD、接管影响范围、PR 入口和下一步；确认、处理中、成功、状态冲突和失败必须可区分，并遵循生产布局和响应式约束。HTML 原型只定义视觉/交互参考，不作为产品页面或验证替身。

## 11. Non-Goals

- 不远程停止进程，不跨主机查找或控制 Runner owner。
- 不停止整个 daemon、其他 Issue、builder、pre-PR reviewer 或 Console 服务。
- 不增加新的 verdict 类型来替代取消状态；不改变现有 GREEN/RED/YELLOW 语义。
- 不自动签收验证、自动将 Draft PR 标为 Ready、自动合并或关闭 Issue。
- 不新增通用进程控制平台、HTTP 服务、Redis 或用户/权限系统。
- 不修改 .github/workflows/validation-gate.yml 的现有人工验证签收规则或 branch protection 配置。
- 不扩展为 Console 全站视觉改版；本次视觉工作聚焦于 Issue 详情中的 verifier 状态卡片、接管对话框和结果呈递。

## 12. Risks And Follow-Ups

- **SQLite 路径不一致**：Console 与 Runner 若读取不同的 console.history_db_path，控制请求不可见。启动时或动作响应须显式报不可控，不得回退到 kill daemon。
- **进程退出延迟**：等待超过 5 秒仍未回收时，UI 显示“仍在停止，尚未交接”并继续查询；只有 runner 确认无法终止或回收进程时才显示取消失败。两种情况都不把 Issue 转入人工 review。后续若真实运行频繁出现回收失败，再评估更精细的进程生命周期能力。
- **外部直接合并或改动 HEAD**：用户可能从 GitHub 绕过 Console 接管并在 verifier 运行时合并，也可能在验证期间更新 PR HEAD。runner 在应用 verdict 前后都要 fresh-read；发现已合并或 HEAD 漂移时停止后续阶段、清理可清理的旧标签并保留人工处理状态。GitHub 读取和写入不能构成原子事务，无法消除检查与写入之间的极短竞态；只有仓库配置的 required checks / branch protection 能阻止有权限的人绕过验证直接合并。
- **Issue 详情时间线并行变更**：生命周期时间线 PRD 可能同时修改呈递组件；实施前协调组件拆分，避免双方重复维护运行状态展示。

## 13. Decision Log

| ID | Decision question | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 如何让操作者在 verifier 结束前接手一个 Issue？ | 以 run id + HEAD SHA 为目标，在共享本机 SQLite 控制状态中发出单 Issue 取消请求；进程回收后转人工 review。 | 停 daemon、杀托管进程、仅增加 UI 按钮、GitHub label polling。 | 只有跨进程原子请求能精准命中当前运行并验证取消结果，同时避免影响其他 Issue。 |
| D-02 | 接管是否允许自动合并？ | 不允许；保留 Draft PR，人工检查并手动决定；接管态暂停所有自动处理，直到新 verifier 对当前 HEAD 给出真实 GREEN。 | 将取消视为 PASS、仅依赖可关闭的 verifier-pass 配置或由 Console 接管动作自动 merge。 | verifier-pass 配置可以被关闭，接管态仍必须保持人工所有权，不能让自动流程将取消视为授权。 |
| D-03 | 如何处理 verifier 与人工接管并发？ | 原子 compare-and-set 决定唯一终态，verdict 副作用前 fresh-read PR merged 状态和 HEAD。 | 以 UI 缓存状态或时间先后猜测赢家。 | 本机与 GitHub 状态可能在请求期间变化，旧快照不足以防止过期 verdict。 |
