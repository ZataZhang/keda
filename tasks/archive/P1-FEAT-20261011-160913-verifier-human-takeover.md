# PRD: Verifier 运行中的单 Issue 人工接管

- GitHub Issue: https://github.com/ZataZhang/keda/issues/278

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，那里是唯一依赖事实源。

> 🧍 **验收状态**：待人工验收。
> 本行是 §9 Acceptance Checklist 的投影，那里是唯一事实源。
> 执行侧交付已完成可复核的行为与竞态证据（真实跨进程 oracle + 294 项自动化契约测试）；`Human-Confirmed` 与依赖真实浏览器 `just e2e` 的呈递项尚未勾选，见 §9.2 与证据报告「验证限制」。

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
- 人工 review 使用现有 agent/automated-review-passed 状态，并添加 human/awaiting-review；人工自行打开 PR、检查当前 HEAD，并使用现有 Realistic Validation 签收和仓库保护规则决定是否合并。
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
- 交接完成后，用现有状态转换将 Issue 置为 agent/automated-review-passed 并添加 human/awaiting-review，写一条带 run id 和 HEAD SHA 的 Issue comment / 审计记录；Draft PR 保持原状。将控制记录状态设为 taken_over 并置 autopilot_hold=true；post-PR supervisor、review_once 和 merge queue 都跳过该 PR，即使 require_verifier_pass 被关闭。只有新 verifier 对当前 HEAD 返回 GREEN 才能清除 hold 并恢复自动处理。
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
4. **执行取消**：run_agent_with_prompt_resilient 只在 verifier 调用中传入取消检查回调；ProcessRunner 等待期间以不超过 1 秒的间隔查询 IIssueRunControl。收到匹配请求后，ProcessRunner 只终止 verifier CLI 的子进程组并等待退出，再抛出 InterruptedError；runner 仅在 control store 再次确认 run id 与 HEAD 后将其映射为人工取消。若 runner 确认无法终止或回收目标进程，控制状态转为 takeover_failed；Issue 不转 agent/automated-review-passed，操作者看到可重试的失败原因。等待超过 5 秒本身只代表仍在处理中，不作为取消失败。
5. **交接完成**：记录 cancelled_by_human，保存可用的部分 verifier 输出并明确无 verdict；清除仅属于 verifier 通过的旧标签；将控制状态设为 taken_over 并置 autopilot_hold=true；Issue 转 agent/automated-review-passed 并添加 human/awaiting-review；保留同一个 Draft PR，不自动改为 Ready。
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
  expected: "真实 Dashboard Issue 详情按原型的信息层级呈递状态、目标 HEAD、作用范围和操作；确认后先显示处理中，目标 verifier 子进程树退出后才显示已交给你审查；Draft PR 和 HEAD 保留；Issue 为 agent/automated-review-passed 且带 human/awaiting-review；daemon 和第二个 Issue 继续运行；没有 verifier verdict 或 validation/verifier-passed；merge queue 不会合并该 PR。桌面 1440×900 与窄视口 390×844 均无内容遮挡、按钮不可达或横向溢出。"
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
  expected_fail: "API 返回冲突；状态不变；目标 verifier 仍运行；Issue 不转 agent/automated-review-passed、不添加 human/awaiting-review；没有 accepted takeover audit。"

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

真实 Dashboard 接管流程的代码已实现（`frontend-public/components/agent-runner/verifier-takeover-card.tsx` 及 Dashboard/IssueDetail 接线）。Playwright 用例 `tests/playwright-e2e/tests/workflows/verifier-takeover.spec.ts` 通过 `page.route` stub Issue 详情与接管动作 API，只覆盖真实组件树上的 UI/API 契约，**不覆盖 PRD 所需的真实 API→SQLite→runner 路径**；本机也缺 node_modules 与可登录的完整栈，`just e2e` 未执行，故上述 `rv-1-*.png` 真实页面截图尚未生成。交班/竞态后端语义由 `tasks/evidence/…/scripts/rv*.py` 的真实跨进程 oracle 与 294 项后端契约测试覆盖（见 §9.2 与 `evidence.json`）。仍需补充不 stub 产品 API 的真实入口用例，并在具备栈的环境运行，核对真实页面状态、Issue 范围、HEAD、PR 入口与窄屏布局；只保存文件不展示视为未交付。

### 9.2 Acceptance Evidence Package

风险顺序：R3 接管和竞态证据优先；随后 R2 页面结果与类型、文档门禁。每项在实现后填写对应 rv-<n> 证据文件，并绑定最终 Git tree。

#### Human-Confirmed

- [ ] 确认接管语义为“只停止当前 Issue 的 verifier；保留 Draft PR；不产生 verifier PASS 或自动合并；之后由操作者手动审查并决定合并”。证据：rv-1-verifier-takeover.png 与 PR evidence comment 中同 viewport 的交互原型 / 真实页面对照。
- [ ] 确认真实 Dashboard Issue 详情达到原型的信息层级：运行状态、目标 HEAD、接管范围、PR 入口容易辨认，确认/处理中/成功/失败状态清楚，桌面和窄视口都可用。证据：`docs/prototypes/verifier-human-takeover.html` 与真实入口截图 `rv-1-verifier-takeover.png`、`rv-1-verifier-takeover-narrow.png` 按状态和视口配对；完成消息中内嵌呈递物。

#### Architecture Acceptance

- [x] IssueRunControl 与观测型 IRunHistoryStore 职责分开；SQLite 适配器通过 compare-and-set 保证唯一终态。证据：tests/test_console_store.py 输出和 rv-2 报告。〔执行侧已核：新增 `issue_run_control.py` 端口与 `console_store_takeover.py` 适配器独立于观测台账；rv-2 `S3_EXACTLY_ONE_WINNER: True`（真实线程竞态仅一个终态）；`tests/test_console_store.py` 归入 rv-2 自动化层 175 passed。〕
- [x] 控制记录绑定 repo_id + issue_number + run_id + head_sha；过期 run 请求不会影响后续 run。证据：SQLite fresh read 的 rv-1 / rv-2 报告。〔执行侧已核：rv-1 负控 `REAL_STALE_HEAD_STATUS: binding_mismatch` + `REAL_STALE_HEAD_CANCEL_CHECK: False`（真实存储拒绝过期 HEAD）；rv-2 `S4_FINAL_STATE_STALE: True`（HEAD 漂移丢弃旧 verdict）。〕
- [x] 不新增服务、Alembic migration 或远程控制面。证据：git diff --name-only 与数据库迁移目录审查。〔执行侧已核：`git status` 变更集无 `alembic/versions/` 文件；控制面为旁路 SQLite 表 `issue_run_control`（`PRAGMA user_version` 幂等建表），非业务库、无远程 HTTP 服务。〕

#### Behavior Acceptance

- [x] 取消只终止目标 verifier 子进程树，等待进程回收；daemon 和其他 Issue 继续运行。证据：rv-1 进程探测和 E2E 报告。〔执行侧已核（真实 OS 进程）：rv-1 oracle `VERIFIER_GROUP_GONE: True`（目标子进程组被 SIGKILL 并回收）+ `CONTROL_PROCESS_SURVIVED: True`（发起接管的另一 OS 进程存活）；浏览器 E2E 层见下方「验证限制」，spec 已交付未在此执行。〕
- [x] 取消输出标明 cancelled_by_human 且无 verdict；不进入 RED recovery 或 pre-PR review。证据：rv-1 Issue comment、verifier 日志和后续阶段 probe。〔执行侧已核：rv-1 `CANCELLED_BY_HUMAN: True` + `FINAL_STATE: taken_over`（无 verdict 落库）；`VerifierCancelledByHumanError` 刻意不进 fallback/recovery 元组，见 `agent_runner_issue_control.py`；rv-1 自动化层 119 passed 覆盖该分类。〕
- [x] autopilot_hold 会阻止 post-PR supervisor、review_once 和 merge queue 继续自动处理，即使仓库关闭 require_verifier_pass；只有当前 HEAD 的新 verifier GREEN 才可清除 hold。证据：rv-2 自动入口 probes。〔执行侧已核：rv-1 `AUTOPILOT_HOLD: True` + `MERGE_GATE_HELD: True`；rv-2 `S2_HOLD_SET` / `S4_HOLD_SET` / `S4_GATE_BLOCKS: True`；merge queue/supervisor/review_once 的 fail-closed 分支由自动化层（含 test_agent_runner_merge_queue、test_agent_runner_supervisor_entrypoints、test_pr_supervisor）覆盖；`discard_stale_verdict` 的绑定方向（只改写产出该 verdict 的那一轮控制行）另有 `test_discard_stale_verdict_holds_the_run_that_produced_it` / `test_discard_stale_verdict_leaves_a_newer_run_untouched` 两项回归。〕
- [x] verifier 完成与 takeover 竞争时只有一个本地终态；PR 已合并或 HEAD 漂移时旧 verdict 不生效、后续自动阶段停止，最终 GitHub 状态与控制记录一致。证据：rv-2 各种竞态顺序的 fresh-state 报告。〔执行侧已核：rv-2 `S1_COMPLETION_WON`+`S1_TAKEOVER_REJECTED_AS_CONFLICT`、`S2_TAKEOVER_ACCEPTED`+`S2_VERDICT_BLOCKED`、`S3_EXACTLY_ONE_WINNER`、`S4_STALE_DISCARDED`、`S5_MERGED_RECORDED`、`RV2_PROBE_OK: True`。〕

#### Frontend Acceptance

- [~] Dashboard Issue 详情只在活动 verifier 可接管时显示动作；对话框明确停止范围和不自动合并；处理中和成功/冲突状态可辨认。证据：rv-1 真实入口截图及 Playwright trace。 — runner-owned gate: 人工真实入口验收（`just e2e` 在 PR review/CI 阶段执行并产出 `rv-1-*.png` 截图与 trace，无头工作树无法本地跑浏览器）。执行侧已交付：`verifier-takeover-card.tsx` 仅在投影存在活动 verifier 时渲染、`running` 才显示接管按钮、Radix 对话框文案明确「只停止此 Issue 当前 verifier…取消不会被记为验证通过，也不会自动合并」、`requested`→轮询呈「正在停止」、`conflict`→「HEAD 已变化 / 运行已结束」；`tsc`/`eslint`/`build` 全绿；真实浏览器截图/trace 待人工/CI 补跑。
- [~] 真实 Issue 详情按原型的信息层级呈递状态、HEAD、接管范围和 PR 入口，并复用生产 Dashboard 的布局、主题、组件与 Dialog/Portal；桌面和窄视口下长文本、滚动和对话框均可用。证据：同状态、同主题、同数据、同视口的原型/生产页面对照截图及真实入口路径。 — runner-owned gate: 人工真实入口验收（PR review 阶段以 `just e2e` 生成桌面/窄视口对照截图并与原型并呈）。执行侧已交付：接管卡内嵌真实 Dashboard/IssueDetail 组件树并复用生产 Dialog/Portal；`verifier-takeover.spec.ts` 已编写桌面 1440×900 与窄 390×844 两视口用例，本环境未执行、无对照截图。
- [~] 前端 action payload 原样传送当前 run_id 和 head_sha；响应类型与后端 DTO 对齐。证据：frontend-public typecheck 与 rv-1 network assertion。 — runner-owned gate: 真实浏览器 network assertion（rv-1 POST body 抓取）在 `just e2e` 阶段完成。执行侧已核：`frontend-public` typecheck exit 0，`ActiveVerifier` / `ConsoleActionResult.takeover_*` 与后端 `routes/agent_runner.py` 的 `payload["active_verifier"]` 投影同构；接管卡提交时 `run_id=verifier.run_id` / `head_sha=verifier.head_sha` 原样透传、界面不自行构造；后端 `console_actions._execute_take_over_verifier` 缺 run_id/head_sha 回 400 的契约由 `tests/test_console_actions.py` 覆盖。仅浏览器端抓包待补。

#### Documentation Acceptance

- [x] docs/guides/agent-runner.md 描述接管状态、Draft PR 保留、人工合并和 verifier 竞态边界；不将取消称为通过。证据：文档 diff 与文档链接检查。〔执行侧已核：新增「### 运行中独立 verifier 的人工接管」小节，逐条写明精确绑定运行身份、只停子进程树、取消≠通过（不写 `validation/verifier-passed`）、autopilot_hold fail-closed、竞态收敛与 `hold` 仅由同 HEAD 新 GREEN 清除；未把取消表述为通过。〕
- [x] PRD 的 FR、Feature Overview、行为样例、oracle 和 checklist 一一对应。证据：rg -n '^## |^### ' tasks/pending/P1-FEAT-20261011-160913-verifier-human-takeover.md 和 PRD review。〔执行侧已核：`grep -cE '^## |^### '` 得 42 个结构化标题，§10 FR-1..FR-7 与 §7 rv-1/rv-2、§9 各组一一对应；PRD checker `--all` 可解析 FR 索引。〕

#### Validation Acceptance

- [x] uv run pytest tests/test_console_store.py tests/test_console_actions.py tests/test_run_verifier_agent.py tests/test_agent_runner_console_api.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py tests/test_process_runner.py 通过。证据：命令输出归档。〔执行侧已核：该 8 文件合集 `-o addopts=""` 复跑 **274 passed**（rv-1 层 119 + rv-2 层 175 = 294，去掉不在该 8 文件清单内的 `tests/test_agent_runner_issue_control.py` 20 项），见 `rv-2-validation-8-file-tests.txt`。〕
- [x] (cd frontend-public && pnpm typecheck && pnpm lint && pnpm build) 通过。证据：三个命令输出归档。〔执行侧已核（合并前评审在最终树复跑）：`pnpm typecheck` exit 0；`pnpm build`（`output: export`）exit 0；`pnpm lint` 对本 PR 改动的 6 个前端文件（`app/(app)/app/dashboard/page.tsx`、`components/agent-runner/issue-detail.tsx`、`components/agent-runner/verifier-takeover-card.tsx`、`lib/api/console.ts`、`lib/api/types.ts`、`lib/api/client.ts`）逐文件 0 error（仅仓库既有 `jsdoc/require-jsdoc` warn），但**仓库全量 exit 1**：3 条基线 `react-hooks/set-state-in-effect` error 位于本 PR 未触碰、也不在 `git diff main...HEAD` 内的 `components/agent-runner/run-detail-dialog.tsx`、`components/backlog/prd-ci-view.tsx`、`components/backlog/prd-lifecycle-view.tsx`，非本 PR 回归，按 §11 范围外留待各自归属修复。〕
- [~] 通过真实浏览器入口 just e2e tests/workflows/verifier-takeover.spec.ts 验证 UI → API → SQLite → runner → verifier process group，且 fresh read 观察到 handoff；不得用组件预览替代。证据：rv-1 E2E 报告、trace、截图和 final-tree hash。 — runner-owned gate: 真实浏览器 `just e2e` 于 PR review/CI 阶段执行（本环境无 `tests/playwright-e2e` 的 node_modules 与可登录栈，无法本地跑）。执行侧已交付：spec 按真实 Dashboard 入口编写（stub 仅在浏览器边界替换只读快照与接管动作端点，走真实 React 组件树 / Radix Dialog / 轮询回读）；进程/DB/handoff 语义由 `scripts/rv1_takeover_handoff_probe.py` 的真实跨进程 oracle 覆盖（同一 console.db WAL、真实 SubprocessRunner 进程组终止与回收、独立 OS 进程发起 CAS）。
- [x] rv-2 的 stale run、stale HEAD、verifier completion race 与 merged PR 场景通过；反例能使 oracle 报红。证据：rv-2-verifier-race.txt 和对应测试报告。〔执行侧已核：`RV2_PROBE_OK: True`；负控 `RV2_EXPECTED_FAIL: 误用初始 head 快照时漂移检测失效…（检查点有效）` + `S4_NEG_STALE_NOT_RECORDED: True`；先红后绿均记录在案。〕
- [x] 所有 R2/R3 行为证据均绑定交付 tree；相关实现变化后已重跑对应 oracle。证据：verification report 中的 tree id。〔执行侧已核：三份 oracle 与两层自动化均在本轮交付工作树复跑并绑定 HEAD `85d69699` / record-excluded tree `356fd11f`（见各 `rv-*.txt` 开头绑定块与报告「验证限制」）。R2 浏览器呈递层的 tree 绑定随其未执行的 e2e 一并披露，见上条。〕

#### Delivery Readiness

- [~] 所有用户可见截图与交互原型对照已在 PR evidence comment 呈递；完成消息内嵌 9.1 表面内容。证据：PR comment 链接与消息截图。 — runner-owned gate: PR 发布与证据评论（执行器不 `git add`/不建 PR；§9.1 呈递依赖上方 `[~]` 真实浏览器截图在 PR review 阶段产出后由 runner 内嵌）。
- [~] PR 正文声明“接管不会自动接受或合并”；现有 Realistic Validation 签收要求和 branch protection 保持。证据：PR body 与 validation-gate required check 状态。 — runner-owned gate: PR 发布（PR body 与 required check 由 runner 生成核对）。执行侧已在 §5、§9.1 与 `docs/guides/agent-runner.md` 声明接管不自动合并、不改 branch protection 与现有签收规则。
- [~] PRD 已按仓库流程完成独立 verifier PASS、执行侧 Final Reconciliation，并随交付改动归档；仅 Human-Confirmed 项可保持未勾选。证据：verifier report、PRD archive path 与验收横幅。 — runner-owned gate: 独立 verifier 复核与 pending→archive 归档（执行器不自行 `git mv`）。执行侧 Final Reconciliation 已完成（见 §13 文末）；本 PRD 含真实浏览器入口以 `[~]` 挂人工/CI 验收，归档门禁是否放行由 runner 判定。

#### Runner-Owned Gates

- [~] 独立 verifier 对本 PRD 的 R2/R3 证据与最终 Git tree 复核通过 — runner-owned gate: 独立验证。
- [~] PR 创建、证据评论发布和交付归档 — runner-owned gate: PR 发布与归档。

## 10. Functional Requirements

- **FR-1: 活动 verifier 可识别**：Console 的 Issue 状态必须能区分 verifier 正在运行、已请求接管、已接管、接管失败、验证已过期、已完成、PR 已合并、不可控制和状态未知；只有运行身份和 HEAD SHA 可复核的本机 verifier 可以接管。
- **FR-2: 精确接管请求**：接管请求必须绑定仓库、Issue、run id 和 HEAD SHA。旧请求、重复请求和状态变化须幂等或明确冲突，不得应用到新 run。
- **FR-3: 单 Issue 取消与人工交接**：接管确认后只终止该 Issue 当前 verifier 子进程树。runner 回收进程后才可标记交接完成、将 Issue 转至现有 agent/automated-review-passed 并添加 human/awaiting-review，记录 run id 与 HEAD SHA。保留 Draft PR 和验证证据；取消不是 verdict，不设置 validation/verifier-passed，不运行依赖 verifier PASS 的步骤。
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

### Final Reconciliation

- Interpretation: confirmed — §1 行为样例逐条对应实现且语义未漂移：接管只终止当前 Issue 的 verifier 子进程组并等待回收，Draft PR 与既有证据保留，Issue 转入现有 agent/review；取消记录为 cancelled_by_human 而非任何 verdict，不写 validation/verifier-passed，不启动自动合并；持久的 autopilot_hold 即便仓库关闭 require_verifier_pass 也让 post-PR supervisor、review_once、merge queue 三重 fail-closed，阻断只由同一 HEAD 的真实 fresh verifier GREEN 解除。verifier 完成/接管、外部合并、HEAD 漂移三类竞态经原子 CAS 收敛为唯一终态，过期 verdict 被丢弃。
- Public behavior and contracts: confirmed — Issue 动作端点新增 `take_over_verifier`，请求体绑定 repo_id + issue_number + run_id + head_sha，缺 run_id/head_sha 直接 400；详情字符串 `takeover outcome=<状态>; observed_state=<观测态>`，HTTP 语义按 TAKEOVER_HTTP_STATUS 映射（accepted/pending 回 202、handed_over 回 200、binding_mismatch/completed_conflict/not_found 回 409，未知结果兜底 409）；Issue 详情 GET 响应新增 `active_verifier` 只读投影（run_id、head_sha、state、can_take_over、pr_number、autopilot_hold）。控制面为旁路 SQLite 表 `issue_run_control`，经 `PRAGMA user_version` 幂等建表、compare-and-set 保唯一终态，未新增 Alembic migration、常驻服务、Redis 或远程控制面；前端 `verifier-takeover-card.tsx` 仅在投影存在活动 verifier 且 state=running 时渲染接管按钮，复用生产 Dashboard/IssueDetail 组件树与 Dialog/Portal。`docs/guides/agent-runner.md` 已补「运行中独立 verifier 的人工接管」小节。
- Related PRD status: confirmed — 本 PRD 无硬依赖（§8：Depends none、Gate none、Sequence via-main）；与生命周期时间线 PRD 在 IssueDetail 仅软文件重叠，交付以独立接管卡组件降低冲突面；与停滞监督 PRD 相邻但无契约耦合。`tasks/pending/` 其余 PRD 与本功能无重叠或冲突。
- Requirements and risks: confirmed — FR-1 至 FR-7 与 §7 的 rv-1、rv-2 oracle 一一对应并在 `evidence.json` 落档；R3 交班与竞态证据由真实跨进程 oracle（同一 console.db WAL、真实 SubprocessRunner 进程组 SIGKILL 与回收、真实 CAS）先经负控报红再转绿；§12 四类风险（SQLite 路径不一致须显式报不可控、5 秒回收窗口的“仍在停止/交接失败”两态、外部合并与 HEAD 漂移的 verdict 前后 fresh-read、IssueDetail 并发变更的组件协调）均落到对应分支与测试。
- Reconciled differences:
  - rv-1 与 rv-2 在 §7 声明的 real_entry 为真实浏览器 `just e2e tests/workflows/verifier-takeover.spec.ts`；当前 spec stub Issue 详情和接管产品 API，只覆盖 Dashboard 组件树上的 UI/API 契约，不能证明真实 Console API→SQLite→runner 路径。本执行环境又缺 `tests/playwright-e2e` node_modules 与可登录的完整栈，故 §9.1 的真实页面截图、Frontend Acceptance、Validation Acceptance 第 3 条与相关 Delivery Readiness、Human-Confirmed 项均保持未勾选；交班/竞态后端语义由真实跨进程 oracle + 294 项契约测试覆盖并如实披露，未伪造浏览器证据。
  - `tests/test_agent_runner_issue_control.py` 当前 20 项纳入 rv-1 自动化层，未出现在 §9 Validation Acceptance 第 1 条原列的 8 文件清单中；该 8 文件合集为 274 passed，rv-1 层为 119、rv-2 层为 175，各自命令可复现。

## 14. Change Log

### 执行侧交付记录（executor，2026-10-12）

- 类型：doc + evidence（实现落地记录与验收勾选；不改需求、范围与 oracle 定义）
- 原文：PRD 开工时横幅为 `⬜ 未开工`，§9 执行侧条目全部空框，无 §13 Final Reconciliation 与 §14 Change Log。
- 变更后：FR-1 至 FR-7 全部落地——IIssueRunControl 端口与 `console_store_takeover.py` SQLite 适配器（CAS 唯一终态、`PRAGMA user_version` 幂等建表）、`agent_runner_issue_control.py` 编排（VerifierRunHandle、active_verifier 投影、TAKEOVER_HTTP_STATUS）、`console_actions.py` 的 `take_over_verifier` 动作、`process_runner.py` 的 IProcessRunner cancel_check 看门狗（秒级轮询、仅 SIGKILL 目标进程组、抛 InterruptedError 不伪装超时）、merge queue/supervisor/review_once 对 autopilot_hold 的 fail-closed 分支、`routes/agent_runner.py` 的 active_verifier 只读投影与 `routes/agent_runner_console.py` 的 takeover_http_status、前端 `verifier-takeover-card.tsx` 及 IssueDetail/dashboard 接线、`docs/guides/agent-runner.md` 新小节、`tests/test_agent_runner_issue_control.py` 与 Playwright spec。横幅翻为 `🧍 待人工验收`；§9 Architecture/Behavior/Documentation/Validation 自动门禁项按证据勾选，前端/浏览器/Delivery/Human-Confirmed 保留未勾。
- 原因：执行侧实现与自动化证据完成，按 Machine Contract 随交付记录归档状态（归档只代表执行侧交付完成，不等人工验收）。
- 影响：接管路径端到端可用（UI → API → core 用例 → SQLite CAS → runner 进程组取消 → fresh-read 竞态收敛）；CLI 表面、Alembic 迁移、branch protection 零变化，无需同步随包 skill。
- 审核：执行器自记，交独立 verifier 与 §9.1 人工验收复核。

### 证据保真度披露：真实浏览器 e2e 层未在本环境执行（executor，2026-10-12）

- 类型：evidence（证据分层披露；不削弱、不删除验收要求本身）
- 原文：rv-1、rv-2 的 real_entry 声明真实浏览器 `just e2e`，§9.1 要求 `rv-1-verifier-takeover.png`/`-narrow.png` 真实页面截图。
- 变更后：本机无 `tests/playwright-e2e` 依赖与可登录栈，`just e2e` 未执行、未生成截图；交班与竞态语义改由 `scripts/rv1_takeover_handoff_probe.py`、`rv1_negative_control.py`、`rv2_race_and_staleness_probe.py` 三条真实跨进程 oracle（同 console.db WAL、真实子进程组终止/回收、真实 CAS 竞态）先红后绿覆盖，`evidence.json` 各 item 的 risks 字段逐条披露未穿越浏览器层，`evidence_files` 不含捏造的 `.png`。
- 原因：不得伪造人工/浏览器层证据，也不得静默删除人工验收要求；无头且避免共享 GitHub 写入的环境下，真实浏览器第二触点归 §9.1 人工/CI。
- 影响：Frontend Acceptance 三条、Validation Acceptance 第 3 条、Delivery Readiness 与 Human-Confirmed 两条保持未勾；人工在具备 `just e2e` 环境后须运行该 spec 核对真实页面状态、Issue 范围、HEAD、PR 入口与窄视口布局。
- 审核：披露待人工验收裁决（本条即 §9.1 呈递内容的一部分）。

### 收尾：Final Reconciliation、Change Log 与证据 manifest 补全（recovery 尝试 2，executor，2026-10-12）

- 类型：doc（补全归档门所需的 §13 Final Reconciliation、§14 Change Log 与证据目录 manifest/报告）
- 原文：交付早期仅有代码与散落的 `.txt` 证据，PRD 缺 Final Reconciliation 与 Change Log，证据目录缺 `evidence.json` 与三份 committed 报告。
- 变更后：追加 §13 Final Reconciliation 四必填字段 + Reconciled differences、§14 三条 Change Log；新增 `tasks/evidence/…/evidence.json`（rv-1 item_number 1、rv-2 item_number 2，命令为跨进程 oracle 与 `-o addopts=""` pytest 分层、stdout_assertions 取实测 marker、负控/期望红逐条列出）与 verification-plan / evidence-report / verifier-report 三份 `.md`；证据 `.txt` 重新绑定最终交付 tree。
- 原因：归档门与人工复核需要完整、可复现、绑定交付 tree 的证据包，缺任一项都会让 verifier BLOCKER。
- 影响：证据包对 rv-1/rv-2 自洽可复跑；不改代码行为与需求定义。
- 审核：执行器自记，待独立 verifier 复核证据充分性与 tree 绑定。

### 合并前评审修复：过期 verdict 落闸、最终复核接管窗口与 409 分类来源（pre-PR reviewer，2026-10-12）

- 类型：code + evidence（修缺陷与取证口径，不改需求、范围与 oracle 定义）
- 原文：`discard_stale_verdict` 以 `row.head_sha == verdict_input_sha` 作为「不改写」条件——而发布端 `_publish_verified_pr` 传入的正是 verifier 输入 head（等于控制行登记时的 `head_sha`），该守卫因此恒定短路，`verifier_stale` 从未落库、`autopilot_hold` 从未置位；`ensure_final_verifier_verdict` 未转发 `repo_id`，HEAD 变化后的最终复核重跑没有接管窗口；前端从人读审计 `detail` 文案正则刮取 409 分类；rv-2 的 S4 以漂移后的 head 作为 `verdict_input_sha` 调用，与生产调用契约相反，负控只是换参数重调同一函数；`IssueControlUnavailableError` docstring 声称回 503（实际路由映射 400）。
- 变更后：守卫改为 `row.head_sha != verdict_input_sha` 并补 docstring 说明「只改写产出该 verdict 的那一轮控制行」；publication 层新增 `_review_verify_create_pr_with_takeover_handoff`，把 `VerifierCancelledByHumanError` 的交班收敛到两个 `_finish_*` 入口（覆盖 4 个 handler 调用点），接管后直接返回、不建 PR、不发结论、不进监督；`ensure_final_verifier_verdict` 转发 `repo_id` 以开放重跑的接管窗口；`ApiRequestError` 携带 `payload`，`takeOverVerifier` 改读结构化 `takeover_outcome`；rv-2 的 S4 用 `_publish_verdict_side_effects` 逐条镜像发布判定，负控红线改为「`validation/verifier-passed` 被写到已漂移 PR」；新增 `test_discard_stale_verdict_holds_the_run_that_produced_it` / `test_discard_stale_verdict_leaves_a_newer_run_untouched` 两项回归（rv-1 层 112→114、合计 284→286），`tests/test_agent_runner_final_verification.py` 的 `run_verifier_gate` 替身改为接受 kwargs 并断言 `repo_id` 被转发（同时锁住 FR-1 在重跑路径上的接管窗口），证据 `.txt` 与 `evidence.json` 重新绑定最终 tree。
- 原因：FR-4「PR 已合并或 HEAD 与 verifier 输入不同时丢弃 verdict、置 autopilot hold、停止后续自动阶段」与 Measurable Objectives 3/4 在缺陷形态下不成立——`require_verifier_pass=false`（或 `prd-auto-merge`）时 merge queue 可能自动合并一个 HEAD 已漂移、verifier 实际未复核过的 PR；FR-1「区分 verifier 正在运行」在最终复核重跑路径上也会显示陈旧的 `verifier_completed`。
- 影响：闸门在 HEAD 漂移与外部合并路径上真正落下；最终复核重跑同样可被单 Issue 接管；前端 409 分类不再随审计文案措辞静默降级。rv-2 探针在临时把守卫改回 `==` 时报红（`S4_FINAL_STATE_STALE`/`S4_HOLD_SET`/`S4_GATE_BLOCKS` 全 False、`RV2_PROBE_OK: False`），证明修复后的检查点有牙。
- 复核：修复后的最终树复跑 `SKIP=check-test-flag just lint --full` exit 0、`just test all` exit 0（**4238 passed, 1 skipped**，`.last_tested_commit`/`.last_linted_commit` 已刷新）、`uv run mkdocs build --strict` exit 0、PRD checker `--check-provided --archive-ready` exit 0、`pnpm typecheck`/`pnpm build` exit 0；rv-1（114 passed）与 rv-2（172 passed）两层证据命令逐字复跑，五份 `.txt` 重新绑定 HEAD `ac74d40d` + 交付 tree。同时更正一处取证口径：`pnpm lint` 仓库全量当前 exit 1，3 条 `react-hooks/set-state-in-effect` error 位于本 PR 未触碰、也不在 `git diff main...HEAD` 内的 `run-detail-dialog.tsx` / `prd-ci-view.tsx` / `prd-lifecycle-view.tsx`，属基线问题；本 PR 改动的 6 个前端文件逐文件 0 error。§9 Validation Acceptance 第 2 条与两份报告的相应措辞已按此如实改写，不再声称「eslint 通过 / 前端全绿」。
- 审核：pre-PR reviewer 自记，待独立 verifier 在最终 committed tree 上复核。

### 合并前评审修复：取消竞态与发布闸门收敛（pre-PR reviewer，2026-10-12）

- 类型：code + evidence + docs（修复竞态边界与校正验证分层说明，不改需求范围与 oracle）
- 原文：Claude stream JSON 调用路径未传递取消检查；verifier 完成 CAS / 控制存储失败路径存在放行风险；已合并或 HEAD 漂移的 fresh-read 分支可能继续后续 Issue 发布；hold 检查存储不可用时未 fail closed，merge queue 仅入口检查 hold；rv-2 探针仍使用旧 `discard_stale_verdict` 签名。Playwright spec stub 了 Issue 详情和接管产品 API，却曾把验证层级标为真实入口。
- 变更后：取消回调覆盖 Claude stream 路径；verifier 完成状态必须成功 CAS 才能继续；发布前后 fresh-read 绑定 verifier run 与输入 HEAD，外部合并/HEAD 漂移会停止后续阶段并落闸；存储不可用时 hold 检查 fail closed，merge queue 在 merge 前再次检查；verdict fresh-read 发布逻辑提取至 `agent_runner_verifier_publication.py`，原发布模块降至 911 非空行且保持旧测试注入入口；rv-2 oracle 同步 run id。Playwright spec 标记为 API-mocked UI 契约，并明确不满足 PRD `real_entry`；未 stub 产品 API 的浏览器验收继续保留为 `[~]`。
- 原因：完成/接管/合并/HEAD 漂移共享竞态边界；所有决策须绑定当前 verifier run 和 HEAD，失败时不得发布 verdict 或允许自动处理。浏览器端模拟产品 API 只能证明 UI 状态映射，不能替代 PRD 声明的真实入口。
- 影响：修复树下 rv-1 119 passed、rv-2 175 passed、原列 8 文件合集 274 passed；跨进程接管与 SQLite 竞态探针通过。真实 Console API→SQLite→runner 的浏览器 E2E 未执行，也未被 UI/API stub spec 覆盖；`Human-Confirmed` 项保持未勾选。
- 复核：实施修复后的 `just test` 增量档为 71 passed、157 deselected；关闭 testmon 对 13 个受影响后端模块复跑 421 passed，其中 rv-1 119、rv-2 175、§9 原列 8 文件集 274 均通过。`just lint --reuse` / `just lint --full`、`uv run mkdocs build --strict`、`pnpm typecheck` 和 `pnpm build` 通过；6 个受影响前端文件逐文件 ESLint 为 0 errors（27 个 JSDoc warnings）。最终提交 tree 由 runner 提交时重新绑定证据。
- 审核：pre-PR reviewer 修复并记录；待独立 verifier 在最终 committed tree 上复核。

### 证据文件命名合规与 golden 配置的宿主泄漏修复（recovery 尝试 1，executor，2026-10-12）

- 类型：evidence + test（修证据命名/绑定与测试环境泄漏，不改需求、范围与 oracle 定义）
- 原文：`evidence.json` item 2 列出 `rv-validation-8-file-tests.txt` 与 `rv-final-affected-backend-tests.txt`，两者不匹配机器契约要求的 `rv-<item_number>-<slug>.<ext>`，独立 verifier 的 Realistic Validation 证据检查以「Item 2: evidence file ... does not match the required `rv-2-*` naming pattern」失败；证据 `.txt` 仍绑定上一轮 `7bff2f4b` / `eb32ac73`。同轮另一处阻塞与接管行为无关：托管 runner 向子进程注入 `KEDACODE_CONFIG`/`IAR_CONFIG` 指向机器级 `config.toml`，而 `settings.config` 单例在 conftest 导入 `backend` 时即定型，`_isolate_ambient_iar_config` 的会话级剥离来不及生效，使 `just test all` 首跑 4 failed / 4245 passed（`test_factory_build_app_config_maps_supervising` 读到 900 而非仓库根 1800；3 项 execution prompt 断言读到缺 PRD map check 规则段的旧模板）。
- 变更后：两个证据文件改名为 `rv-2-validation-8-file-tests.txt`、`rv-2-affected-backend-tests.txt`（旧文件删除，manifest `evidence_files` 与证据报告、验证计划、§9 文本同步改指），并在当前工作树逐字复跑全部 8 份 `.txt`（rv-1 探针/负控/入口层 119、rv-2 竞态探针/竞态层 175、原列 8 文件 274、13 模块 421、前端 typecheck+ESLint+build），统一绑定 HEAD `85d69699` / record-excluded tree `356fd11f`。测试侧新增 `tests/support/agent_runner.py::root_config_app_config()`（剥离宿主注入变量后按仓库根 `config.toml` 重新装配 `AppConfig`），3 处 golden 断言改用它，并在 `tests/conftest.py` 隔离 fixture 的 docstring 标注单例定型时机。
- 原因：证据文件命名是机器契约硬性项，命名不合规会让证据检查在实现之外失败；golden 断言必须测本仓 `config.toml`，否则任何机器级配置漂移的宿主都给出与 CI 不一致的结果——这是把断言对象固定回它声称要测的文件，不是放宽断言。
- 影响：`just test all` 在同一注入环境下 **4249 passed / 1 skipped**（此前 4 failed / 4245 passed），`just test` 46 passed、`just lint --full` exit 0、PRD checker `--check-provided --archive-ready` exit 0，证据命名项通过；oracle 计数不变（119/175/274/421），负控仍从真实模板删段转红。§9 勾选状态不变，`Human-Confirmed` 两项与真实浏览器入口 `[~]` 项继续保留。
- 复核：改名后的 8 份 `.txt` 全部由本轮逐字复跑生成（命令、输出与 exit code 原样入档，无手写数值）；`just test all` 与 `just test` 在本工作树复跑通过。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核命名合规与 tree 绑定。

### 合并前评审修复：闸门兜底、结论收回、孤儿收敛与状态码投影独立化（executor 复跑会话，2026-10-13）

- 类型：code + evidence + docs（补齐 FR-5/FR-6/FR-1 的失效分支与工程一致性，不改需求、范围与 oracle 定义）
- 原文：`autopilot_hold_blocks(repo_id=None)` 直接返回 `False` 放行；`mark_completed` 的 CAS 早于树漂移与 RED 两道检查，结论被拒时控制行仍停在 `verifier_completed`；`takeover_requested` 行在 owner 随重启消失后无人收敛，界面永远显示"仍在停止"；交班时控制行没有 PR 编号；接管状态码在 `agent_runner_console.py` 端点内就地改写 `Response.status_code`；`issue_run_control` 建表占用 `user_version=14`，与并行分支的同号迁移在机器级共享库上互相顶掉；前端接管卡整文件 `eslint-disable react-hooks/set-state-in-effect`。
- 变更后：① `IIssueRunControl` 新增 `has_autopilot_hold_for_issue`（不限仓库的 fail-closed 兜底，仅用于"不放行"方向）与 `set_issue_run_pr_number`（按 run_id 绑定回写 PR 编号），SQLite 适配器实现两法；`review_once`/`interactive_decision` 改为透传**注册表** repo_id，解析不出 repo_id 时闸门按 Issue 编号兜底、读不到一律按有闸门处理。② `VerifierRunHandle.mark_verdict_unpublished`：树漂移 / RED 拒绝结论时把 `verifier_completed` 收回为 `expired`（接管同刻落地则改记 `takeover_failed`），`run_verifier_gate` 两处 raise 前接线。③ `invalidate_orphaned_takeover_requests` 与投影读前的孤儿收敛（120 秒宽限，`takeover_requested → takeover_failed`，保持落闸、不停进程、不重放，PRD §7.7）。④ 接管状态码投影独立为 `routes/agent_runner_console_takeover.py::build_issue_action_response`，非接管动作序列化路径与特性前逐字节一致。⑤ 控制表退出 schema 版本号（`_SCHEMA_VERSION` 回退 14→13，建表保持版本门外无条件幂等）。⑥ 前端改为逐行豁免该规则；`docs/guides/agent-runner.md` 补 `active_verifier` / `take_over_verifier` 两个契约名的排查索引；随包 `kedacode-operator` SKILL.md 增补"单 Issue 接管是 Console 动作、不是 daemon 旗标"条目，与 `kc run --takeover` 全局语义显式区分。
- 原因：FR-5 要求 post-PR supervisor / review_once / merge queue 对 autopilot_hold **一律 fail closed**——按错 key 或无 key 查询查不到行等于把正在被人工接管的 Issue 放回自动流程；FR-1 的状态分类不能对着一段从未发布到 PR 的结论报"验证已完成"；§7.7 明确要求无 owner 的待处理请求标记失效交人查看而非永久"处理中"；schema 版本号冲突会让控制面在共享库上命中 "no such table"，属交付阻断。
- 影响：接管闸门在 repo_id 不可解析、结论未发布、owner 重启消失三条路径上真正落下或收回；HTTP 契约（202/200/409）不变；CLI 表面、Alembic 迁移、branch protection 零变化。修复层新增回归使 rv-1 自动化层 119→131、rv-2 竞态/fail-closed 层 175→221（并集纳入 review_once / interactive_decision 用例）、§9 原列 8 文件合集 274→278、受影响后端合集 421→519（新增 test_review_once / test_interactive_decision / test_agent_config_consistency 三文件）。真实浏览器 `just e2e` 层仍未执行，§9 相应 `[~]` 与 `Human-Confirmed` 两项保持原状。
- 复核：三条跨进程 oracle（rv1 接管闭环、rv1 负控、rv2 竞态/过期）与四层自动化命令在本树逐字复跑转绿（`RV1_PROBE_OK` / `RV1_NEGATIVE_CONTROL_OK` / `RV2_PROBE_OK` 均 True，负控红线 marker 原样入档）；`just test` 176 passed（`.last_tested_commit`/`.last_linted_commit` 刷新）、`just lint --full` exit 0、`uv run mkdocs build --strict` exit 0、`pnpm typecheck`/`pnpm build` exit 0、受影响前端文件 ESLint 0 errors。全部 8 份证据 `.txt` 重新绑定 HEAD `9f26fd87` + record-excluded tree `4ae99eb253b3671feb9e841903c5829a7bfa20a1`，`evidence.json` 的 command/output_summary/stdout_assertions 与三份 `.md` 报告同步更正；最终 committed tree 由 runner 提交后按机器契约复跑绑定。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核。

### 第二轮合并前评审修复：交班不因 PR 读失败中断、取消分类优先于超时、请求路径 fail-closed（pre-PR reviewer，2026-10-13）

- 类型：code + test + evidence（修缺陷与前端竞态边界并重新取证，不改需求、范围与 oracle 定义）
- 原文：`finalize_verifier_takeover` 在权威 PR 上下文读取失败时整段 except 吞掉，Issue 侧交班（labels 翻 review + 审计评论）随之丢失，操作员停在"仍在停止"；`_ProcessWatchdog.raise_if_timed_out` 先判超时后判取消，取消恰在超时窗口内落地时被误分类为 `TimeoutExpired` 走重试/修复循环而非 `cancelled_by_human`；`request_verifier_takeover` 不捕获存储异常，SQLite 故障以未分类 500 逃逸而非 `IssueControlUnavailableError`→503 fail-closed；`review_once` 观测记录与闸门查询共用一个 repo_id 表达式，注册表 id 为空时记录键混入目录名回落；`IssueActionRequest.run_id/head_sha` 无长度上限；前端接管卡请求失败时确认对话框仍盖着错误提示、reload 响应无序列守卫（慢响应可回退新状态）、空闲态不回读控制行；Playwright spec 的详情路由正则 `$` 锚点漏匹配带查询串的 URL，stub 从未生效。
- 变更后：① finalize 用 `pr_read_failed` 标志分离"读失败"与"确无 PR"：读失败只跳过合并推断（负控用例锁住"失败后不得转 `find_merged_pr_by_head`"），Issue 侧 labels 翻转与无 PR 链接的交班评论始终执行。② 看门狗 `raise_if_timed_out` 改为 `self._timed_out and not self._cancelled`，取消分类优先；两条 cancel-capable 路径均已确认事后调用 `raise_if_cancelled`。③ 请求用例把非 `IssueControlUnavailableError` 的存储异常统一包装后回 503。④ 观测记录键独立取 `repo_id or repo_path.name`，闸门保持只认注册表 id + Issue 编号兜底。⑤ `run_id`/`head_sha` 字段加 `max_length=128/64`。⑥ 前端：失败即关对话框露出错误、`reloadSeqRef` 序列守卫、running 空闲 15s 定时回读。⑦ spec 正则改 `(\?.*)?$` 并把详情状态机改为 POST 门控推进。新增回归 `test_use_case_request_takeover_wraps_storage_errors` / `test_takeover_sqlite_mirror_constants_match_core_port` / `test_process_watchdog_cancel_classification_wins_over_timeout`，并强化 `test_takeover_handoff_does_not_mark_pr_merged_on_github_read_failure` 断言交班继续。
- 原因：FR-3/§7.5 要求交班确认后 Issue 必须转入人工 review 并留审计——GitHub 读抖动不能把 Issue 侧交接一起吞掉；§7.4 要求人工取消不得进入超时恢复或瞬态重试，分类颠倒会让被接管的 verifier 被重新拉起；§11 与 API 边界要求控制面故障 fail closed；两条前端边界（对话框遮蔽错误、陈旧响应回退）会在操作员面前呈现误导状态，违背"界面绝不自称已停止/绝不误导下一步"。
- 影响：rv-1 契约层 131→134、§9 原列 8 文件合集 278→279、受影响后端合集 519→522（rv-2 竞态/fail-closed 层 221 不变）；HTTP 契约（202/200/409/503）与 CLI 表面不变。一项低危评审意见经论证后**有意不修**：接管 POST 无客户端超时——服务端按 §7.3 以 5 秒封顶返回 202，客户端先行超时会把一个可能已登记的接管误报为失败。
- 复核：三条跨进程 oracle 与四层自动化命令在本树逐字复跑转绿（`RV1_PROBE_OK`/`RV1_NEGATIVE_CONTROL_OK`/`RV2_PROBE_OK` 均 True）；`just test` 180 passed、`just lint --full` exit 0、`uv run mkdocs build --strict` exit 0、`pnpm typecheck`/`pnpm build` exit 0、受影响前端文件 ESLint 0 errors（27 个既有 JSDoc warnings）。全部 8 份证据 `.txt` 重新绑定 HEAD `5e2a8386` + record-excluded tree `e66a427ee823856c8a4521535ff83923b27daab6`，`evidence.json` 与三份 `.md` 报告同步更正。真实浏览器 `just e2e` 层执行状态不变（本环境仍缺依赖与可登录栈），§9 相应 `[~]` 与 `Human-Confirmed` 两项保持原状。
- 审核：pre-PR reviewer 自记，待独立 verifier 在最终 committed tree 上复核。
