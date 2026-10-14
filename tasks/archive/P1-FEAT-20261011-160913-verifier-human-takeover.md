# PRD: Verifier 运行中的单 Issue 人工接管

- GitHub Issue: https://github.com/ZataZhang/keda/issues/278

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，那里是唯一依赖事实源。

> 🧍 **验收状态**：待人工验收。
> 本行是 §9 Acceptance Checklist 的投影，那里是唯一事实源。
> 执行侧已取得可复核的行为、竞态和隔离真实浏览器证据。真实 Dashboard → Console API → SQLite CAS → runner → verifier 进程组链路通过；外部 GitHub 与 verifier CLI 按 mock_boundary 使用隔离替身。桌面/窄视口截图和 Playwright trace 已生成。人工逐图确认、独立 verifier 复核和 PR 证据发布仍待完成，见 §9.2 与证据报告。

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
  real_entry: "PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py —— 该 harness 用隔离 state home + 专属 console.db 起**真实** uvicorn Console API、真实 pnpm dev Dashboard 与真实 runner 子进程，再以 RV1_LIVE_E2E=1 运行 `just e2e tests/workflows/verifier-takeover-real-entry.spec.ts`（无 page.route 打桩）。`tests/workflows/verifier-takeover.spec.ts` 用 page.route 打桩 Issue 详情读取与接管写入，只证明界面/API 契约层，**不**作为本项 real_entry。"
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
  real_entry: "PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv2_real_entry_console_race_probe.py —— 接管经**真实 Console 动作路由**（POST …/issues/{n}/actions 的 take_over_verifier），CAS 走真实 SQLite 文件库，完成回调与 verdict 落地分别走生产 `VerifierRunHandle.mark_completed()` 与生产 `publish_verified_pr()`，PR 状态每次现读。仅 GitHub 读写边界按 mock_boundary 用可编程替身提供当前 head/merged。`tests/workflows/verifier-takeover.spec.ts` 打桩同一对 endpoint，不能作为本项 real_entry；helper 级 `rv2_race_and_staleness_probe.py` 只作补充。"
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
| 真实 Dashboard Issue 详情按原型的信息层级展示运行、确认、处理中和接管完成状态；保留 Draft PR 与目标 HEAD。 | 原型入口：[`docs/prototypes/verifier-human-takeover.html`](../../docs/prototypes/verifier-human-takeover.html)。实施后生成 `tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/rv-1-verifier-takeover.png` 与 `rv-1-verifier-takeover-narrow.png`，分别与同状态、同主题、同数据、同视口的生产页面并列，在 PR 证据评论呈递。 | 在原型与真实页面中核对状态、Issue 范围、HEAD 和 PR 入口；检查窄屏下按钮可达、对话框不越界且无横向溢出。打开真实入口：`just prd review tasks/archive/P1-FEAT-20261011-160913-verifier-human-takeover.md`（本 PRD 已随交付改动归档到 `tasks/archive/`）。 |

reviewer: verifier 的 rv-2 并发、stale-head、外部合并和数据库状态 oracle 不放在人读呈递区；只在失败时上报。

既有 `verifier-takeover.spec.ts` 仍是 API stub 的 UI 契约测试，不计作真实入口证据。本轮新增 opt-in `tests/playwright-e2e/tests/workflows/verifier-takeover-real-entry.spec.ts`，由隔离 harness `tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py` 启动生产 Dashboard、FastAPI、独立 HOME/console.db 和真实 runner 等待循环；只将外部 GitHub transport 与阻塞 verifier CLI 替换为确定性替身，浏览器侧没有 `page.route`。浏览器捕获实际 issue-detail GET 与 takeover POST，确认 HTTP 202、提交的 run_id/head_sha 与详情投影相同；随后从 SQLite 回读 `taken_over`，确认目标 verifier 子进程退出、Draft PR 保留、Issue 离开 `agent/running` 落到 review 态标签（`config.labels.review` 承载，rebase 后其规范名为 `agent/automated-review-passed`，见 §12）且 `autopilot_hold` 生效。交班后 Dashboard 重扫仓库并重读标签；页面列表与标签编辑器均显示 review。桌面/窄视口截图、Playwright trace、runner/API 日志及 facts 已写入证据目录。复现命令：`PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py`。真实页面与 prototype 的并排审阅、真实 GitHub transport、独立 verifier 签署和 PR evidence comment 仍未完成；对应 Human-Confirmed 项保持空框，Frontend/Validation runner-owned 项保持 `[~]`。

### 9.2 Acceptance Evidence Package

风险顺序：R3 接管和竞态证据优先；随后 R2 页面结果与类型、文档门禁。每项在实现后填写对应 rv-<n> 证据文件，并绑定最终 Git tree。

#### Human-Confirmed

- [ ] 确认接管语义为“只停止当前 Issue 的 verifier；保留 Draft PR；不产生 verifier PASS 或自动合并；之后由操作者手动审查并决定合并”。证据：rv-1-verifier-takeover.png 与 PR evidence comment 中同 viewport 的交互原型 / 真实页面对照。
- [ ] 确认真实 Dashboard Issue 详情达到原型的信息层级：运行状态、目标 HEAD、接管范围、PR 入口容易辨认，确认/处理中/成功/失败状态清楚，桌面和窄视口都可用。证据：`docs/prototypes/verifier-human-takeover.html` 与真实入口截图 `rv-1-verifier-takeover.png`、`rv-1-verifier-takeover-narrow.png` 按状态和视口配对；完成消息中内嵌呈递物。
- [ ] **处置 rv-1 真实浏览器入口**：执行侧已按选项①在隔离栈补跑真实浏览器入口，产出 `rv-1-verifier-takeover.png`、`rv-1-verifier-takeover-narrow.png` 与 `rv-1-verifier-takeover-trace.zip`，并记录实际 POST 绑定、runner 终态及进程组退出。外部 GitHub transport 与 verifier CLI 是 §7 mock_boundary 允许的替身；Dashboard、API、SQLite、runner 与浏览器是真实实现。本项继续留空，供人工核对两视口截图和 prototype 信息层级并确认最终呈递口径。

#### Architecture Acceptance

- [x] IssueRunControl 与观测型 IRunHistoryStore 职责分开；SQLite 适配器通过 compare-and-set 保证唯一终态。证据：tests/test_console_store.py 输出和 rv-2 报告。〔执行侧已核：新增 `issue_run_control.py` 端口与 `console_store_takeover.py` 适配器独立于观测台账；rv-2 `S3_EXACTLY_ONE_WINNER: True`（真实线程竞态仅一个终态）；`tests/test_console_store.py` 归入 rv-2 自动化层 222 passed。第九轮再补一条该适配器里唯一的 fail-open 分支：CAS 未命中却回读到同一 run/HEAD 的 `running` 行时，原先幂等返回 `pending`（→ 路由 202 + Console 审计 accepted）而库里其实什么都没写；现在重试一次 CAS 并要求回读离开 `running`，两次都空手返回新增 409 分类 `state_change_conflict`，使 `202` 必然意味着取消意图与 `autopilot_hold` 已持久。回归：`test_takeover_cas_miss_with_running_row_never_reports_accepted`（空手两次不得受理、控制行逐字未变）、`test_takeover_cas_retry_lands_the_request_after_a_first_miss`（重试必须真的落盘）、`test_use_case_request_takeover_maps_state_change_conflict_to_409`、`test_take_over_verifier_without_durable_cancel_intent_audits_rejected`（未落盘的请求不得留下 accepted 审计）。〕
- [x] 控制记录绑定 repo_id + issue_number + run_id + head_sha；过期 run 请求不会影响后续 run。证据：SQLite fresh read 的 rv-1 / rv-2 报告。〔执行侧已核：rv-1 负控 `REAL_STALE_HEAD_STATUS: binding_mismatch` + `REAL_STALE_HEAD_CANCEL_CHECK: False`（真实存储拒绝过期 HEAD）；rv-2 `S4_FINAL_STATE_STALE: True`（HEAD 漂移丢弃旧 verdict）。〕
- [x] 不新增服务、Alembic migration 或远程控制面。证据：git diff --name-only 与数据库迁移目录审查。〔执行侧已核：`git status` 变更集无 `alembic/versions/` 文件；控制面为旁路 SQLite 表 `issue_run_control`（`PRAGMA user_version` 幂等建表），非业务库、无远程 HTTP 服务。〕

#### Behavior Acceptance

- [x] 取消只终止目标 verifier 子进程树，等待进程回收；daemon 和其他 Issue 继续运行。证据：rv-1 进程探测和 E2E 报告。〔执行侧已核（真实 OS 进程）：rv-1 oracle `VERIFIER_GROUP_GONE: True`（目标子进程组被 SIGKILL 并回收）+ `CONTROL_PROCESS_SURVIVED: True`（发起接管的另一 OS 进程存活）；第六轮新增的 Console HTTP 段探针 `rv1_console_http_real_entry_probe.py` 在同一条真实路径上另证一次——`VERIFIER_GONE: True` 且同仓另一 Issue 的 verifier 替身 `other_verifier_alive=True`（接管只命中目标进程组）。浏览器 E2E 层见下方「验证限制」，spec 已交付未在此执行。〕
- [x] 取消输出标明 cancelled_by_human 且无 verdict；不进入 RED recovery 或 pre-PR review。证据：rv-1 Issue comment、verifier 日志和后续阶段 probe。〔执行侧已核：rv-1 `CANCELLED_BY_HUMAN: True` + `FINAL_STATE: taken_over`（无 verdict 落库）；`VerifierCancelledByHumanError` 刻意不进 fallback/recovery 元组，见 `agent_runner_issue_control.py`；rv-1 自动化层 141 passed 覆盖该分类。〕
- [x] autopilot_hold 会阻止 post-PR supervisor、review_once 和 merge queue 继续自动处理，即使仓库关闭 require_verifier_pass；只有当前 HEAD 的新 verifier GREEN 才可清除 hold。证据：rv-2 自动入口 probes。〔执行侧已核：rv-1 `AUTOPILOT_HOLD: True` + `MERGE_GATE_HELD: True`；rv-2 `S2_HOLD_SET` / `S4_HOLD_SET` / `S4_GATE_BLOCKS: True`；merge queue/supervisor/review_once 的 fail-closed 分支由自动化层（含 test_agent_runner_merge_queue、test_agent_runner_supervisor_entrypoints、test_pr_supervisor）覆盖；`discard_stale_verdict` 的绑定方向（只改写产出该 verdict 的那一轮控制行）另有 `test_discard_stale_verdict_holds_the_run_that_produced_it` / `test_discard_stale_verdict_leaves_a_newer_run_untouched` 两项回归。〕
- [x] verifier 完成与 takeover 竞争时只有一个本地终态；PR 已合并或 HEAD 漂移时旧 verdict 不生效、后续自动阶段停止，最终 GitHub 状态与控制记录一致。证据：rv-2 各种竞态顺序的 fresh-state 报告。〔执行侧已核：rv-2 `S1_COMPLETION_WON`+`S1_TAKEOVER_REJECTED_AS_CONFLICT`、`S2_TAKEOVER_ACCEPTED`+`S2_VERDICT_BLOCKED`、`S3_EXACTLY_ONE_WINNER`、`S4_STALE_DISCARDED`、`S5_MERGED_RECORDED`、`RV2_PROBE_OK: True`。〕

#### Frontend Acceptance

- [~] Dashboard Issue 详情只在活动 verifier 可接管时显示动作；对话框明确停止范围和不自动合并；处理中和成功/冲突状态可辨认。证据：`rv-1-verifier-takeover.png`、`rv-1-verifier-takeover-narrow.png`、`rv-1-verifier-takeover-trace.zip` 与真实入口 Playwright 输出。 — 执行侧真实浏览器路径已通过：运行中显示接管动作；真实 POST 返回 202；runner 取消目标进程组后 fresh read 呈「已交给你审查 · verifier 未完成」；无停止中残留提示。第九轮补：卡片在控制行**已是** `takeover_requested` 时挂载（页面重载、Issue 重开、第二个操作员）也会继续按交班档重读投影直到落定，`takeover_failed` 按可重读的终态呈递而不是死路；界面契约层见 `rv-1-takeover-contract-spec.txt`（Playwright 7 passed），把重读门恢复成修复前的「只有 running」该用例即转红。最终项仍待独立 verifier 与人工对照 prototype 核验。
- [~] 真实 Issue 详情按原型的信息层级呈递状态、HEAD、接管范围和 PR 入口，并复用生产 Dashboard 的布局、主题、组件与 Dialog/Portal；桌面和窄视口下长文本、滚动和对话框均可用。证据：真实入口桌面 1440×900、窄视口 390×844 截图与 Playwright trace。 — 两种视口的真实页面截图已生成，确认列表与标签面板都更新为 review；prototype/生产页面的并排视觉核对与人工通过待完成。
- [~] 前端 action payload 原样传送当前 run_id 和 head_sha；响应类型与后端 DTO 对齐。证据：frontend-public typecheck 与真实浏览器 request assertion。 — Playwright 捕获真实页面 POST body，并断言 `action`、`run_id`、`head_sha` 与此前详情投影相同；后端缺失/超长绑定契约另见 HTTP 与 API 测试。等待独立 verifier 复核最终 tree。

#### Documentation Acceptance

- [x] docs/guides/agent-runner.md 描述接管状态、Draft PR 保留、人工合并和 verifier 竞态边界；不将取消称为通过。证据：文档 diff 与文档链接检查。〔执行侧已核：新增「### 运行中独立 verifier 的人工接管」小节，逐条写明精确绑定运行身份、只停子进程树、取消≠通过（不写 `validation/verifier-passed`）、autopilot_hold fail-closed、竞态收敛与 `hold` 仅由同 HEAD 新 GREEN 清除；未把取消表述为通过。〕
- [x] PRD 的 FR、Feature Overview、行为样例、oracle 和 checklist 一一对应。证据：rg -n '^## |^### ' tasks/pending/P1-FEAT-20261011-160913-verifier-human-takeover.md 和 PRD review。〔执行侧已核：`grep -cE '^## |^### '` 得 42 个结构化标题，§10 FR-1..FR-7 与 §7 rv-1/rv-2、§9 各组一一对应；PRD checker `--all` 可解析 FR 索引。〕

#### Validation Acceptance

- [x] uv run pytest tests/test_console_store.py tests/test_console_actions.py tests/test_run_verifier_agent.py tests/test_agent_runner_console_api.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py tests/test_process_runner.py 通过。证据：命令输出归档。〔执行侧已核：该 8 文件合集 `-o addopts=""` 在第九轮交付树复跑 **284 passed**（rv-1 入口契约层 141、rv-2 竞态/fail-closed 层 222，其中 `tests/test_agent_runner_issue_control.py` 不在该 8 文件清单内、单列于 rv-1 层），见 `rv-2-validation-8-file-tests.txt`。〕
- [x] (cd frontend-public && pnpm typecheck && pnpm lint && pnpm build) 通过。证据：三个命令输出归档。〔执行侧已核（合并前评审在最终树复跑）：`pnpm typecheck` exit 0；`pnpm build`（`output: export`）exit 0；`pnpm lint` 对本 PR 改动的 6 个前端文件（`app/(app)/app/dashboard/page.tsx`、`components/agent-runner/issue-detail.tsx`、`components/agent-runner/verifier-takeover-card.tsx`、`lib/api/console.ts`、`lib/api/types.ts`、`lib/api/client.ts`）逐文件 0 error（仅仓库既有 `jsdoc/require-jsdoc` warn），但**仓库全量 exit 1**：3 条基线 `react-hooks/set-state-in-effect` error 位于本 PR 未触碰、也不在 `git diff main...HEAD` 内的 `components/agent-runner/run-detail-dialog.tsx`、`components/backlog/prd-ci-view.tsx`、`components/backlog/prd-lifecycle-view.tsx`，非本 PR 回归，按 §11 范围外留待各自归属修复。〕
- [~] 通过真实浏览器入口验证 UI → API → SQLite → runner → verifier process group，且 fresh read 观察到 handoff；不得用组件预览替代。证据：`rv-1-browser-runner-e2e.txt`、`rv-1-browser-playwright.txt`、两视口截图、trace 与 `rv-1-browser-runner-facts.json`。 — 隔离的真实 Dashboard E2E 已执行成功：无 `page.route`；POST 返回 202；SQLite/runner 最终态、目标进程退出、Draft PR 保留、Issue review、autopilot hold 和交班 comment 均通过独立断言。外部 GitHub 用 file-backed gh transport；独立 verifier 复核和证据发布仍是 runner-owned gate。 **第十二轮如实记下本项仍未跨越的一段**：§7 rv-1 的 `expected` 含「daemon 和第二个 Issue 继续运行」，而真实浏览器 harness `rv1_browser_runner_e2e.py` 目前只驱动 Issue 278 一条（fake gh transport 按单一 issue number 索引），该段现由另一条真实入口取证——Console HTTP 层探针 `rv1_console_http_real_entry_probe.py` 在同一条真实路径上断言目标 verifier 进程组消失、同仓另一 Issue 的 verifier 替身仍存活。本轮**没有**把浏览器 harness 扩成两条 Issue 并跑绿，因此「同一浏览器运行内同时持有第二个 Issue 的 verifier 并断言其存活 + daemon 存活」这一段仍是缺口，本项保持 `[~]`，不以 helper 层或另一探针的存在勾成完成。扩展点在 harness 的 gh transport 键（单一 issue）与 runner 脚本模板（写死 `ISSUE_NUMBER`），需要至少两个 runner 子进程与各自的 blocker 才能在同一次运行内成立。同一条缺口也适用于 rv-1 的 `negative_control`（过期 `head_sha` → 409、状态不变、verifier 仍活、无 agent/review、无 accepted 审计）：它目前是在**真实 Console HTTP + 真实 SQLite** 这一层跑绿的（`rv1_console_http_real_entry_probe.py` 打出 `STALE_HEAD_STATUS: 409 outcome=binding_mismatch other_issue_state=running other_verifier_alive=True other_cancel_check=False other_accepted_audits=0`，即「过期绑定被拒 + 第二个 Issue 的 verifier 存活」两条同时成立），而不是在同一次浏览器点击里；浏览器层的 409 呈递由打桩 spec 的 `failure state (409 HEAD drift)` 用例覆盖界面侧。
- [x] rv-2 的 stale run、stale HEAD、verifier completion race 与 merged PR 场景通过；反例能使 oracle 报红。证据：`rv-2-real-entry-console-race.txt`（本轮新增的真实入口 capture）与 `rv-2-verifier-race.txt`（helper 级补充）。〔执行侧已核：真实入口 oracle `scripts/rv2_real_entry_console_race_probe.py` 按 §7 rv-2 的 `must_cross` 逐段 crossing——接管经真实 Console 动作路由（S1 完成先赢 → HTTP **409** + `takeover_outcome=completed_conflict`；S2 动作先赢 → HTTP **202** + `accepted`，生产完成回调落空 `S2_COMPLETION_CALLBACK_LOSES: True`，被接管的一轮不产出 verdict、`validation/verifier-passed` 不出现），verdict 落地经生产 `publish_verified_pr()`，PR 状态每次现读；S3 真实双线程并发下 `S3_EXACTLY_ONE_WINNER: True`；S4 完成后 HEAD 漂移 → `S4_STALE_VERDICT_NOT_PUBLISHED` / `S4_FINAL_STATE_STALE` / `S4_HOLD_SET` / `S4_GATE_BLOCKS`；S5 外部合并 → `S5_FINAL_STATE_PR_MERGED` 且后续阶段停止；S6 接管落闸后只有同 HEAD 的新 GREEN 清闸（`S6_OLD_RUN_CANNOT_CLEAR: True`）。负控 `RV2_EXPECTED_FAIL: 误用初始 head 快照时漂移检测失效，过期 verdict 会被错误保留（检查点有效）` 证明「用初始快照代替 fresh read」这一缺陷形态会让 oracle 变红。31 条判定全真、`RV2_ALL_OK: True`、脚本 exit 0。此前 helper 级版本直接调 `store.request_issue_takeover()`，评审据 §7 `forbidden_bypasses`「不得只测状态机 helper」判其低于声明入口，本轮补齐真实入口而不替换旧 capture。〕
- [x] R2/R3 行为证据绑定到可解析的代码 tree，并对后续实现增量补充对应 oracle。证据：各 `rv-*.txt` 开头绑定块的 `HEAD`/`TREE`/`PUSHED_ANCHOR`/`RUN_ON`/`CODE_DELTA`/`CODE_DELTA_PROOF`，以及真实浏览器 capture 自记的 `CAPTURE_CODE_*`。〔执行侧已核（第九轮：按评审 finding 1 在交付树全量重跑重绑）：finding 1 的实质是**绑定失效**而非证据不足——capture 绑的是 rebase 前的 `da043953` / tree `736dd811`，而 PR head 已换，`agent_runner_validation_gate.py` 在 `evidence_marker.head_sha != pr_context.head_sha` 时会重置验收清单，那份证据包在交付 head 上按构造不算数；`git diff --name-only da043953 HEAD` 与本 PR 文件集相交 25 项（含 main 侧 `agent_runner_merge_queue.py` +24/−16、`review_once.py` +30/−1），这些 rebase 带进来的改动此前从未进过 rv-2 的 fail-closed 链。本轮因此把两条 manifest command、11 份单项 capture 与真实浏览器 harness 全部在交付树上重跑：`HEAD=82ab9f182dec2b8fa193ddb8d2b97e88213a8b7d` / `TREE=1e4c8907d3be6bba533a27fb8e1f0de1a1657d5c`、`PUSHED_ANCHOR=82ab9f18…`（远端 `zata/issue-278` 与本地 head 一致，GitHub 现在即可解析）、`CODE_DELTA` = 本轮 13 个行为/界面/测试/文档文件（由 `git diff --name-only 82ab9f18 -- src tests docs frontend-public` 现算，非手写），`DELIVERY_TREE` 由 `scripts/rv_bind_tree.sh` 用不碰真索引的临时索引在记录件定稿后绑到 runner 的下一个提交之 tree 并重算校验未漂移；第八轮写在 capture、`evidence.json`、三份报告与本 PRD 里的 `885ca6f6` / `1db934ae`（tree `ed153d91`）已逐处改指为历史锚点，不再声称它是本 PR 当前 head。重跑同时暴露一处**红在判据拼写、不红在行为**的缺陷：main 已把 review 态的规范名从 `agent/review` 改成 `agent/automated-review-passed`（仍由 `config.labels.review` 承载），harness 与真实入口 spec 写死了旧名，现按运行侧同一份配置解析（harness 把值打进 `REVIEW_LABEL`）并新增 `NO_VERDICT_PASSED_LABEL` 断言；标签语义是否该改投 `human/awaiting-review` 属需求层决定，见 §12 与证据报告，执行侧不擅改。capture 清单另增 `rv-1-takeover-contract-spec.txt`（界面契约层 Playwright 7 passed，含本轮两条新用例）与两份 `rv-<item>-chain-stdout.txt`；复核入口仍是 `PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv_stdout_assertions.py`，本轮实测 item 1 的 42 条与 item 2 的 18 条 pattern 全部在该 item `command` 自身的 stdout 里命中、两条 command 均 exit 0（`RV_STDOUT_ASSERTIONS: OK`）。行为源代码均在该 tree；PRD/evidence Markdown 仅为记录更新，不参与行为代码断言。 **第十轮交付锚点（评审 finding 2：入库文件必须自己说清交付的是哪个 head）**：此前 `27cfc95a` / `c34c1a28` 在本 PRD、`evidence.json` 与三份报告里出现 0 次，只活在未入库的 `.txt` 绑定块里，只看 PR 的读者无法把 manifest 与交付提交对上。现逐字写出：`DELIVERED_HEAD=27cfc95a4e60bfc360021ecb7f2dbd31f9bd0c18` / `DELIVERY_TREE=c34c1a28c5ed8b595a090bd7852ef1f3d2a1dd3f`（= `git rev-parse 27cfc95a^{tree}`，即 runner 交付第九轮那 13 个行为/界面/测试/文档文件的提交，`gh api repos/ZataZhang/keda/git/trees/c34c1a28c5ed8b595a090bd7852ef1f3d2a1dd3f` 随时可解析。第十一轮删掉第十轮在此三处写的「远端 `zata/issue-278` 当前指向它」：那是一句随下一个提交立刻变假的现在时断言，本轮复核时远端 tip 已是其后的记录件提交 `f0eb0c9b` / tree `141b84ca`；改成只声明可现算的不变量——`27cfc95a` 是**最后一个改动行为代码的提交**）。该值指名的是**上一个已存在的提交**，因此写进这些入库文件不构成自指。只读复核（**本句第十/十一轮口径已被下面第十二轮撤销并替换**，保留原文只为留下改错的痕迹）：原先写的是 `git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 为空 ⇒ 该提交之后只动记录件；第十二轮实测该 diff 为 20 个文件非空，可现算的窄口径改为 `git diff --name-only 27cfc95a -- src/backend/infrastructure/persistence/console_store_takeover.py src/backend/core/use_cases/agent_runner_issue_control.py src/backend/core/use_cases/run_verifier_agent.py frontend-public/components/agent-runner/verifier-takeover-card.tsx` 为空 ⇒ 接管行为代码与 capture 所跑的树逐字一致；`git merge-tree --write-tree 73c082c335c1ae0c715c2f7208b4e3278fc58e38 27cfc95a` exit 0 ⇒ 对 live main（`73c082c3`）干净可合并。远端 `iar-evidence/issue-278`（tip `30aab5de` 仍写着 `da043953` / `736dd811` / `DELIVERY_TREE=__PENDING__`）与 PR evidence marker 需 runner 按最终推送 head 重发，push 不在执行器权限内。 **第十一轮（评审 finding 1 复发，executor，2026-10-14）复核**：绑定不变量三条仍然成立——`git rev-parse 27cfc95a^{tree}` = `c34c1a28…`、`git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 为空（本轮只动记录件）、`git diff --name-only 82ab9f18 27cfc95a -- src tests docs frontend-public` 恰为 capture 头声明的 13 个文件；`git merge-tree --write-tree 73c082c3 HEAD` exit 0。门禁复跑：rv-1 层 **141 passed**、§9 原列 8 文件集 **284 passed**、`test_run_verifier_agent / test_kedacode_operator_skill / test_agent_runner_prompt_contract / test_agent_runner_structured_evidence / test_agent_runner_validation / tests/guards` **550 passed + 1 skipped**，与本包记录计数逐字一致，故 capture 无需重跑。已发布包与交付包的差集按 blob 量化在证据报告「已发布包 vs 交付包」小节（45 vs 42 路径、缺 3 条第九轮件、29 条同名 blob 更早，并记有交付侧 `evidence.json` 的体积与 git blob 供重发后对表），marker 现值因 GitHub API rate limit 未能由执行器复读（沿用 finding 的 `da043953` 观测）。 **第十二轮（评审 finding「绑定不变量已被交付 head 打破」+「PR 与 main 冲突」，executor，2026-10-15）**：上一轮写在三处入库文件里的那句可现算不变量**已被后续两个提交打破，本轮先撤销它**——`git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 在本工作树实测 **20 个文件非空**，`27cfc95a` **不再是**「最后一个改动行为代码的提交」。改述成两条仍然成立的窄口径：① **接管本身的行为代码**（`console_store_takeover.py`、`agent_runner_issue_control.py`、`run_verifier_agent.py` 与其拆分模块、前端接管卡与 `lib/api/*`）自 `27cfc95a` 起未被证据 identity 那两轮触碰：`git diff --name-only 27cfc95a -- src/backend/infrastructure/persistence/console_store_takeover.py src/backend/core/use_cases/agent_runner_issue_control.py src/backend/core/use_cases/run_verifier_agent.py src/backend/core/use_cases/agent_runner_verifier_publication.py frontend-public/components/agent-runner/verifier-takeover-card.tsx` 实测**只**多出 `agent_runner_verifier_publication.py` 一个文件、且只是本轮的 logger 名一行；进程拆除侧另有 `process_runner.py` / `process_runner_streams.py` 的本轮改动（将剩余的终止调用改走创建时刻读到的进程组身份）。rv-1 / rv-2 的接管判据因此仍可在当前树逐字复现（本轮实测：rv-1 入口层 **142 passed**、§9 原列 8 文件集 **286 passed**、六项门禁集 **556 passed + 1 skipped**；上一轮记录的 141 / 284 / 550 已过期，差值来自本轮新增的 HTTP 层 409 用例与拆分模块 logger 身份用例，以及 `8106c3ae` / `992ae61e` 为证据 identity 路径新增的用例）；② 其余差异全部落在**证据 identity / publication 路径与测试脚手架**（`agent_runner_validation_gate.py`、`agent_runner_validation_identity.py`、`agent_runner_structured_evidence*.py`、`agent_runner_validation_publication.py`、`tests/conftest.py`、`docs/guides/agent-runner.md`），不改接管行为，也不改 rv-* capture 的判定逻辑。**capture 必须在下一个提交落地后按最终 head 重绑**：`scripts/rv_recapture_all.sh` 重跑 + `scripts/rv_bind_tree.sh` 绑 `DELIVERY_TREE`，执行器无权也**不**在任何入库文件里写本轮的交付 SHA（那会立刻变假）。本轮同时修掉**阻断合并的冲突**：`git merge-tree --write-tree main HEAD` 原先在 `agent_runner_publication.py` 与 `review_once.py` 各冲一个 hunk（main 把两处 import 块整体重排并新增 `GitHubApiRateLimitError` 守卫），本轮把 import 顺序对齐 main 并让本 PR 的新增 import 落在可对齐的锚点上，用 `git merge-file` 对 base/main/工作树逐文件复算 97 个分叉文件 → **0 冲突**，且合并结果同时保留 main 的 7 处 `GitHubApiRateLimitError` 引用与本 PR 的交班 import；`tests/test_agent_runner_prompt_contract.py` 里与共享 `tests/support/agent_runner.root_config_app_config` 重复的本地 `_root_config_app_config()` 已删除（该重复才是评审所说「keep the shared helper, drop the local duplicate」，但本地副本在本分支而非 main，已在分支侧删）。`Validate Template` 硬门禁仍红的 `pr_supervisor.py`（本树 1015 非空行、main 已 1091）**不在本 PR 的文件集内**、blob 与 base 一致，属 main 侧既有债（仓库已挂在 `fix-ci-python-file-line-limits`），按 §11 范围与本 Issue 的 PRD 边界不在本轮拆分。证据包路径数由 45 增至 **47**（新增 `rv-2-real-entry-console-race.txt`），远端 `iar-evidence/issue-278` 与 PR marker 仍待 runner 重发。〕

#### Delivery Readiness

- [~] 所有用户可见截图与交互原型对照已在 PR evidence comment 呈递；完成消息内嵌 9.1 表面内容。证据：PR comment 链接与消息截图。 — 当前真实桌面/窄视口截图和 trace 已在本地证据目录生成，并已按验证层级就地嵌入执行侧报告（`check_prd_evidence.sh` exit 0）。**PR evidence comment 与 evidence marker 仍未发布到当前 PR head**：远端 `iar-evidence/issue-278` tip `30aab5de` 里写的还是 rebase 前的 `HEAD=da043953` / `TREE=736dd811` / `DELIVERY_TREE=__PENDING__`，最新 marker 的 `head_sha` 同样是 `da043953`，而 PR head 已推进到其后的记录件提交（第十一轮 2026-10-14 复核：远端 `zata/issue-278` tip 为 `f0eb0c9b` / tree `141b84ca`，行为代码仍停在 `27cfc95a` / tree `c34c1a28`）；`agent_runner_validation_gate.py:138` 在 `evidence_marker.head_sha != pr_context.head_sha` 时重置 PR 正文勾选、摘掉 `validation/verifier-passed` 并发 `phase=validation_reset` 评论，所以「Realistic Validation sign-off」在重发之前不可能变绿——这也意味着合并队列的自动勾选会被同一轮重置。重发证据分支与重贴 marker 属 runner 侧动作（执行器不 push、不评论）：runner 在修复或 rebase 推送后走 `agent_runner_issue_handlers.py` 尾段的 `publish_validation_evidence_best_effort`，`head_sha` 由 `get_head_sha(...)` 现取，因此 marker 天然等于**最终推送的那个 head**，不该照抄任何记录件里写的 SHA；人工兜底入口是 `kc recover --issue 278`（`recover_publish.py` 走同一条发布）。已发布 tip 与交付包的逐 blob 差集、以及重发后 reviewer 可跑的三条只读判据（`git ls-tree -r <ev-tip>` 应 45 条、4 份入库件 blob 与 `git hash-object` 对表、未入库 capture 的自记 `HEAD`/`TREE`/`CODE_DELTA`/`DELIVERY_TREE` 与报告对表）见证据报告「已发布包 vs 交付包」小节；重发后应确认该 PR 不再出现新的 `phase=validation_reset` 事件。Human-Confirmed 页面核对仍待人工。
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
- **交班标签的语义需人拍板（第九轮 rebase 暴露）**：接管交班把 Issue 落到 `config.labels.review` 承载的标签上，而 main 已把该标签的规范名从 `agent/review` 改为 `agent/automated-review-passed`，其含义是「自动评审已通过，可转人工 review 或自动合并」。把「机器结论作废、交回给人」落在「自动评审已通过」上，读 GitHub 的人会被误导：合并侧没有即时危险（`autopilot_hold` 让 post-PR supervisor、`review_once` 与合并队列三重 fail closed，rv-2 `S2`/`S4` 与本层 `AUTOPILOT_HOLD: True` 在案），但标签本身的说法是错的。候选处置是改投 `human/awaiting-review`，代价是 FR-3 的表述、`docs/guides/agent-runner.md`、随包 Skill 与相应测试都要跟着改；本轮只把**证据判据**接回同一份运行侧配置（harness 用 `AppConfig().labels.review` 解析并打出 `REVIEW_LABEL`，spec 用 `REVIEW_STATE_CHIP` 匹配两种可接受拼写），不改交班目标——需求层决定不由执行侧代行。
- **`pr_supervisor.py` 超 1000 非空行（继承自 main，不在本 PR 改动面）**：CI 严格行门命令在本 head 上 exit 1，唯一超额文件是 `src/backend/core/use_cases/pr_supervisor.py`（1015 非空行），该文件既不在 `git diff main...HEAD` 里也不被本 PR 触碰（本 PR 改动的 `src/backend` 文件最大 887 非空行）。`hooks/max_file_lines.allowlist.txt` 自 2026-09-15 起是空名单且禁止新增豁免，因此拆分应在 main 上用自己的重构 PRD 做，不在本接管 PR 里顺手拆无关模块；main 当前未设 branch protection，这条红灯不是远端合并硬阻断，处置次序（main 先修 vs 本分支携带）交人工裁定。
- **继承自 main 的 Skill 漂移守卫红（顺序需人拍板）**：`test_packaged_skill_command_examples_match_cli_help` 在 main 自己的 `3d65d008` 上就红在 `SKILL.md: 未收录的命令示例：kc daemon --all`（CI run 38292110956），与本 PRD 无关——`kc daemon --all` 合法（daemon 组回调自带 `--all`），但 `_ALLOWED_FLAGS` 只有 `("daemon", "run")` / `("daemon", "status")`，而 `build_command_schema` 只导出叶子命令，所以直接加 `("daemon",)` 会被同文件的 `test_packaged_skill_whitelist_matches_runtime_schema` 判成「schema 中不存在命令」。CI 跑的是 pull_request 的 merge ref，本 PR 不带着修就永远绿不了，故第五轮**选择在本分支携带**：守卫改为从真实 Typer 命令树补出「自身声明旗标的命令组」路径，并把示例路径匹配收敛为最长匹配。若人更希望这条修在 main 上落地（本 PR 只留接管内容），正确动作是在 main 上落地同一处 `_real_flags_by_path()` 派生，然后把本 PR 的 `tests/test_kedacode_operator_skill.py` 改动整份丢弃、rebase 后由 main 的版本接管——两种顺序都让 merge ref 变绿，差别只在这份派生逻辑归谁维护。

## 13. Decision Log

| ID | Decision question | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 如何让操作者在 verifier 结束前接手一个 Issue？ | 以 run id + HEAD SHA 为目标，在共享本机 SQLite 控制状态中发出单 Issue 取消请求；进程回收后转人工 review。 | 停 daemon、杀托管进程、仅增加 UI 按钮、GitHub label polling。 | 只有跨进程原子请求能精准命中当前运行并验证取消结果，同时避免影响其他 Issue。 |
| D-02 | 接管是否允许自动合并？ | 不允许；保留 Draft PR，人工检查并手动决定；接管态暂停所有自动处理，直到新 verifier 对当前 HEAD 给出真实 GREEN。 | 将取消视为 PASS、仅依赖可关闭的 verifier-pass 配置或由 Console 接管动作自动 merge。 | verifier-pass 配置可以被关闭，接管态仍必须保持人工所有权，不能让自动流程将取消视为授权。 |
| D-03 | 如何处理 verifier 与人工接管并发？ | 原子 compare-and-set 决定唯一终态，verdict 副作用前 fresh-read PR merged 状态和 HEAD。 | 以 UI 缓存状态或时间先后猜测赢家。 | 本机与 GitHub 状态可能在请求期间变化，旧快照不足以防止过期 verdict。 |

### Final Reconciliation

- Interpretation: confirmed — §1 行为样例逐条对应实现且语义未漂移：接管只终止当前 Issue 的 verifier 子进程组并等待回收，Draft PR 与既有证据保留，Issue 转入现有 review 态标签（本文写作 `agent/review`，rebase 后同一状态位的规范名是 `agent/automated-review-passed`，由 `config.labels.review` 承载）；取消记录为 cancelled_by_human 而非任何 verdict，不写 validation/verifier-passed，不启动自动合并；持久的 autopilot_hold 即便仓库关闭 require_verifier_pass 也让 post-PR supervisor、review_once、merge queue 三重 fail-closed，阻断只由同一 HEAD 的真实 fresh verifier GREEN 解除。verifier 完成/接管、外部合并、HEAD 漂移三类竞态经原子 CAS 收敛为唯一终态，过期 verdict 被丢弃。
- Public behavior and contracts: confirmed — Issue 动作端点新增 `take_over_verifier`，请求体绑定 repo_id + issue_number + run_id + head_sha，缺 run_id/head_sha 直接 400；详情字符串 `takeover outcome=<状态>; observed_state=<观测态>`，HTTP 语义按 TAKEOVER_HTTP_STATUS 映射（accepted/pending 回 202、handed_over 回 200、binding_mismatch/completed_conflict/not_found/state_change_conflict 回 409，未知结果兜底 409）；第九轮起 `202` 附带持久性承诺：CAS 反复空手却仍读到 `running` 时返回新增的 `state_change_conflict`，没有落进库的取消意图不得被记成受理，也不得在 Console 审计里出现 accepted；Issue 详情 GET 响应新增 `active_verifier` 只读投影（run_id、head_sha、state、can_take_over、pr_number、autopilot_hold）。控制面为旁路 SQLite 表 `issue_run_control`，经 `PRAGMA user_version` 幂等建表、compare-and-set 保唯一终态，未新增 Alembic migration、常驻服务、Redis 或远程控制面；前端 `verifier-takeover-card.tsx` 仅在投影存在活动 verifier 且 state=running 时渲染接管按钮，复用生产 Dashboard/IssueDetail 组件树与 Dialog/Portal；重读覆盖 `running` 与 `takeover_requested` 两个在途状态（卡片可能由第二个操作员或在请求已登记之后才挂载），`takeover_failed` 按可重读的终态呈递而非死路。`docs/guides/agent-runner.md` 已补「运行中独立 verifier 的人工接管」小节。
- Related PRD status: confirmed — 本 PRD 无硬依赖（§8：Depends none、Gate none、Sequence via-main）；与生命周期时间线 PRD 在 IssueDetail 仅软文件重叠，交付以独立接管卡组件降低冲突面；与停滞监督 PRD 相邻但无契约耦合。`tasks/pending/` 其余 PRD 与本功能无重叠或冲突。
- Requirements and risks: confirmed — FR-1 至 FR-7 与 §7 的 rv-1、rv-2 oracle 一一对应并在 `evidence.json` 落档；R3 交班与竞态证据由真实跨进程 oracle（同一 console.db WAL、真实 SubprocessRunner 进程组 SIGKILL 与回收、真实 CAS）先经负控报红再转绿；§12 四类风险（SQLite 路径不一致须显式报不可控、5 秒回收窗口的“仍在停止/交接失败”两态、外部合并与 HEAD 漂移的 verdict 前后 fresh-read、IssueDetail 并发变更的组件协调）均落到对应分支与测试。
- Reconciled differences:
  - rv-1 与 rv-2 在 §7 声明的 real_entry：原 `verifier-takeover.spec.ts` 仍是 API stub 契约用例，不作为 rv-1 证据。本轮另以 `RV1_LIVE_E2E=1` 启用的 `verifier-takeover-real-entry.spec.ts` 穿过真实 `/app/dashboard`、Issue 详情 GET、接管 POST、Console API、独立 SQLite CAS、真实 runner 等待循环和 verifier 进程组；隔离 harness 使用独立 HOME/console.db、file-backed `gh` 替身及真实阻塞子进程。浏览器 POST 202 与 run_id/head_sha 绑定、handoff fresh read、review 态标签（同一状态位，现名 `agent/automated-review-passed`）、Draft PR、autopilot hold、verifier 进程退出均有断言；桌面/窄视口截图和 trace 已生成。真实 GitHub transport、prototype 并排人工审阅、独立 verifier 复核与 PR 证据发布仍待完成，因此不勾 Human-Confirmed，也不声称人工验收。
  - `tests/test_agent_runner_issue_control.py` 当前 36 项纳入 rv-1 自动化层，未出现在 §9 Validation Acceptance 第 1 条原列的 8 文件清单中；该 8 文件合集为 284 passed，rv-1 层为 141、rv-2 层为 222，各自命令可复现。第七轮把「受影响后端合集」capture 由 16 文件扩到 17 文件（加入 `tests/test_agent_runner_recovery.py`，logger 身份用例在其中）；第九轮该合集 549 passed = 第七轮的 542 + 本 PR 新增 6 条（CAS 三条、Console 审计一条、终止身份两条）+ rebase 带进来的 main 侧测试增量 1 条——逐文件 `--collect-only` 核过（`test_agent_runner_issue_control.py` 33→36、`test_console_actions.py` 12→13、`test_process_runner.py` 55→57），使本轮改动的测试文件也有 capture 而不是只有报告文字。**第十二轮刷新**：上述 284 / 141 / 222 / 549 是第九轮的实测值，本轮新增 2 条用例（HTTP 层 `state_change_conflict` 409、`agent_runner_verifier_publication` 的 logger 身份回归）后在同一条命令上实测 **286 / 142 / 223 / 551**（§9 绑定项与 `evidence.json` 的 `stdout_assertions` 已按实测值改写，不留旧计数冒充现值）。
  - **PR 证据格式（v1 → v2 marker）的重写落在 §1「我理解为不做」上**：`8106c3ae` / `992ae61e` 新增 `agent_runner_validation_identity.py` 并把签收判据从「PR head 变了」改为「PR **贡献 patch** 变了」，属对本 PRD 自我裁决面的扩张，理由、v1 三键兼容与可回退口径逐条登记在 §14 第十二轮的「原因」段。执行侧不把它写成「本来就在范围内」，也不擅自 revert 已由 owner 推送的提交。

## 14. Change Log

### 第十二轮评审修复：消掉与 main 的合并冲突、把接管受理判据钉到 HTTP 层、补真实入口 rv-2 oracle，并**记录 PR 证据格式重写这项范围偏离**（pre-PR reviewer findings，executor，2026-10-15）

- 类型：fix + evidence（行为代码、测试、文档与记录件同改；本轮**新增**一条对 §1「我理解为不做」的偏离登记，见「原因」段）
- 原文：评审给出的 HIGH/medium 条目里，能由执行器在本分支关闭的四条是——① `Merge conflict with main still blocks the PR`（GitHub 报 `mergeable=CONFLICTING`，finding 当时点的是两个测试文件与 main 侧 `_root_config_app_config()` 重复）；② `rv-2 required acceptance evidence is stale and executed below its own declared entry point`（`rv2_race_and_staleness_probe.py` 直接调 `store.request_issue_takeover()`，撞 §7 `forbidden_bypasses`「不得只测状态机 helper」，且 `_publish_verdict_side_effects` 只是**镜像**生产发布判定）；③ `No TestClient-level assertion for the 409 state_change_conflict response`；④ `PRD §7 rv-1/rv-2 real_entry still names the API-stub spec as the executable acceptance path` 加上 `verifier-takeover.spec.ts` 头部仍把 rv-1 写成「等人工处置的缺口」——这条陈旧披露正是 rv-1 finding 连续五个周期复发的原因。另有 low 两条：拆分模块 `agent_runner_verifier_publication.py` 仍用 `getLogger(__name__)`（与三个同批拆分模块的钉名口径不一致），以及 `Takeover termination re-reads os.getpgid on a possibly recycled pid` 只修了接管与二次拆除、超时/停滞/pty 三处仍是裸调用。其余条目（`run_verifier_agent.py` 与 `agent_runner_structured_evidence.py` 的 1000 行硬门、SKILL.md 合并态 71 行、`kc daemon --all` 漂移门、marker 兼容回放、`take_over_verifier`/`active_verifier` 在文档与随包 skill 里无名）**复核后确认在上一轮或本轮 HEAD 已关闭**，本轮只复现不重做：`check_max_file_lines.py` 现在只报 main 侧的 `pr_supervisor.py`；SKILL.md 分支态与 `merge-tree` 合并态均 70 非空行；`tests/test_marker_compat_replay.py` + `tests/test_kedacode_operator_skill.py` 共 109 条全绿。
- 变更后：**（a）合并冲突清零。** 真冲突已挪到 `agent_runner_publication.py` 与 `review_once.py`——main 把两处 import 块整体重排（并按 isort 口径排序），并给 `interfaces.agent_runner` import 加了 `GitHubApiRateLimitError`，与本 PR 在同块里新增的交班 import 相撞。本轮把 import 顺序对齐 main、把本 PR 的新增 import 放到三方都能对齐的锚点，再用 `git merge-file`（base = merge-base `73c082c3`，theirs = `main`，ours = 工作树）逐文件复算 **97 个分叉文件 → 0 冲突**，并断言合并态同时保住 main 的 7 处 `GitHubApiRateLimitError` 引用与本 PR 的 `finalize_verifier_takeover` / `publish_verified_pr` / `autopilot_hold_blocks`。关键约束：`GitHubApiRateLimitError` 这个类**只存在于 main**（`interfaces/agent_runner.py:401`），本分支引用它会整树 ImportError，所以分支侧 import 刻意不含它、由合并侧带进来——这也是 finding 里「main landed its own helper」的同类现象：分支不能预支 main 尚未存在的符号。**（b）本地重复 helper 删除。** `tests/test_agent_runner_prompt_contract.py` 的 `_root_config_app_config()`（连同 `_ROOT_CONFIG_TOML` 与随之失去用途的 `import os` / `build_app_config_from_settings` / `AgentRunnerSettings`）删掉，改用共享 `tests/support/agent_runner.root_config_app_config`——该副本在**本分支**而非 main，finding 的归因写反了，但「keep the shared helper, drop the local duplicate」的处置在分支侧成立。**（c）接管受理判据上移到 HTTP 层。** 新增 `tests/test_agent_runner_console_api.py::test_take_over_verifier_unpersisted_cas_returns_409`：经真实动作路由发 `take_over_verifier`，用可编程 CAS 存储制造「CAS 反复空手却仍读到同一 run/HEAD 的 running」，断言 HTTP **409** + `takeover_outcome == state_change_conflict` + `cas_attempts == 2`（未命中必须真的重试过）+ 控制行仍 `running`（不留半个取消意图或闸门）+ Console 审计只有 `rejected`。为此把只在存储层用例里出现的替身提升为共享模块 `tests/support/issue_run_control.py::ScriptedTakeoverCasStore`，两个测试层复用同一份、不复制粘贴。**（d）rv-2 真实入口 oracle 落地。** 新增 `tasks/evidence/…/scripts/rv2_real_entry_console_race_probe.py`：接管经**真实 Console 动作路由**（与浏览器真实入口同一个 endpoint）、CAS 走真实 SQLite 文件库、完成回调与 verdict 落地分别走生产 `VerifierRunHandle.mark_completed()` 与生产 `publish_verified_pr()`，PR 状态每次现读；仅 GitHub 读写边界按 §7 `mock_boundary` 用可编程替身提供当前 `head_sha`/`merged`。六个场景 31 条判定全真（`S1_CONSOLE_ACTION_409` + `completed_conflict`、`S2_CONSOLE_ACTION_202` + `S2_COMPLETION_CALLBACK_LOSES` + `S2_NO_VERDICT_FOR_CANCELLED_RUN` + 无 `validation/verifier-passed` + `S2_MERGE_QUEUE_BLOCKS`、`S3_EXACTLY_ONE_WINNER`、S4 漂移 → `verifier_stale` + 落闸、S5 外部合并 → `pr_merged` 且后续阶段停止、S6 接管落闸后只有同 HEAD 新 GREEN 清闸且旧 run 不能清），负控证明「拿初始 head 快照代替 fresh read」会让生产代码放行过期 verdict（检查点有效）。整条 item 2 的 manifest command 实测 exit 0，helper 级 capture 保留作补充、不再充当 real_entry。**（e）§7 与 spec 头部对齐。** rv-1 `real_entry` 改指 `rv1_browser_runner_e2e.py`（隔离 state home + 专属 `console.db` 起真实 uvicorn + 真实 pnpm dev + 真实 runner 子进程，`RV1_LIVE_E2E=1` 跑 `verifier-takeover-real-entry.spec.ts`，真实页面截图按 PRD 呈递名 `rv-1-verifier-takeover.png` / `-narrow.png` 由该 spec 产出），rv-2 改指本轮新脚本；`verifier-takeover.spec.ts` 头部把「rv-1 缺口等人工处置、关闭只有两条路」那段过期自述换成职责收敛说明（本文件只做界面/API 契约层：`binding_mismatch` 的 409 呈递、mounted-requested 继续重读、`takeover_failed` 可重读），**behavior / expected / must_cross / forbidden_bypasses 一字未改**。**（f）终止身份补齐。** `process_runner.py` 流式 `BaseException` 拆除、`_check_timeouts` 的超时与停滞两处、`process_runner_streams.py` 的 pty 拆除，全部改走 watchdog 在 `Popen` 之后只读一次的 `expected_process_group` + `expected_process_started_at`（同一个类里不再两种拆法并存）；两条相应回归改为断言身份参数确实随拆除调用一起传出。**（g）logger 身份与文档口径。** `agent_runner_verifier_publication.py` 钉回 `backend.core.use_cases.agent_runner_publication`，新增与三个同批拆分模块同款的身份回归；`docs/guides/agent-runner.md` 接管小节补出 `takeover race or external change` / `PR merged or HEAD drifted during verdict side effects` 两条关键字及其 logger 名，使「按发布流程 grep」能一次看到这两行。**（h）§9 绑定项撤销已假的不变量**（`git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 实测 20 个文件非空），改述成两条仍可现算的窄口径并把计数刷新为 rv-1 入口层 **142** / 8 文件集 **286** / rv-2 的 **223** 与 **551** / 六项门禁集 **556 passed + 1 skipped**；`evidence.json` item 2 的 command、evidence_files（新增 `rv-2-real-entry-console-race.txt`，证据包 45→**47** 路径）与 14 条真实入口断言同步。
- 原因：**本轮必须登记一条范围偏离。** `8106c3ae` / `992ae61e` 引入 v2 `iar:validation-evidence` marker（新增 `agent_runner_validation_identity.py` 187 行，marker 携带 `version=2 head= base= patch=`），改动 `agent_runner_validation_gate.py` 的签收匹配——这正是 §1「我理解为不做」里「不重写……PR 证据格式」所指的物件，而此前 PRD 没有为它写过任何一句理由，reviewer 只能从 diff 反推。本轮不撤销它，理由是它**决定了本 PRD 的 rv-1/rv-2 证据能不能被签收**：`agent_runner_validation_gate.py:137-158` 只比 `evidence_marker.head_sha != pr_context.head_sha`，而接管这套跨进程控制面的正确性并不随 rebase 改变，于是「main 前进一次」或「rebase 但行为没变」都会把已经复核过的证据判成 stale、重置勾选并摘掉 `validation/verifier-passed`——评审连着多个周期的「证据绑到过期 head」类 finding，机制上的根因就在这一条比较上。改为按 PR **贡献 patch 身份**（`base_sha` + `patch_id`）判等之后，行为或验收范围变化照样重置，单纯换 base 不再误伤。兼容性口径写清两条：v1 marker **仍是三键**（`head`/`branch`/`count`）且新增字段只以附加方式出现在 v2，`tests/test_marker_compat_replay.py` 用 `tests/support/marker_compat_baseline.json` 逐字段回放 v1 语义，本轮实测 109 条全绿；因此本 PR 修改了裁定自己证据的那道门禁，属于**自我裁决面**的改动，登记在此而不是留成未记录的扩张。若仓库 owner 认为它应独立成 PRD，拆分动作是 `git revert 8106c3ae 992ae61e` 后另开 PRD，与本分支的接管代码互不牵连，执行侧不擅自替 owner 决定。
- 影响：行为代码 4 个文件（`agent_runner_publication.py`、`review_once.py` 的 import 排布；`process_runner.py` / `process_runner_streams.py` 的拆除身份；`agent_runner_verifier_publication.py` 的 logger 名），测试 4 个文件 + 新增共享 `tests/support/issue_run_control.py`，文档 1 个文件，PRD 与 `evidence.json` 记录件，随包 skill 与前端**零改动**。§11 非目标、§7 的 behavior/expected/must_cross/forbidden_bypasses、rv-1/rv-2 的判定逻辑均未削弱；`pr_supervisor.py` 的 1000 行红灯仍按 main 侧既有债记录、不在本 PR 动手（blob 与 base 一致、不在本 PR 文件集内、`hooks/max_file_lines.allowlist.txt` 禁止新增豁免）。capture 的 `DELIVERY_TREE` 保持 `__PENDING__`，由 `scripts/rv_bind_tree.sh` 在 runner 的交付提交落地后绑定；远端 `iar-evidence/issue-278` 重发与 PR marker 重贴属 runner 侧动作，执行器不 push、不评论。**本轮未关闭的一条**：rv-1「daemon 和第二个 Issue 继续运行」在**同一次浏览器运行内**的断言仍未成立（harness 只驱动 Issue 278），已按上条在 §9 rv-1 项内记成缺口、保持 `[~]`，该段目前只有 Console HTTP 层探针的真实入口证据。

### 第十一轮评审修复：把入库记录里的「远端当前 head」措辞换成可现算的不变量，并逐 blob 量化已发布证据包的落后程度（pre-PR reviewer finding 1，executor，2026-10-14）

- 类型：evidence（纯记录件；不改行为代码、测试、范围与 oracle，因此第九轮在交付树 `c34c1a28` 上重跑的整套 capture 继续成立，无需再花一次真实浏览器链路去换一组等价数字）
- 原文：finding 1 指出已发布的证据包与 PR marker 仍绑 rebase 前的 `da043953`，签收门因此每次都把「Realistic Validation sign-off」重置回红，并要求按最终推送 head 重发后再确认不再出现 `validation_reset`。finding 2 指出 `Validate Template` 的 `pr_supervisor.py` 1015 非空行红灯是继承性的；其余 10 条（rv-1 真实入口、Skill 契约名、merge 冲突、`run_verifier_agent.py` 行数、界面在途重读、CAS 空手不得受理、终止身份防 pid 复用、拆分模块 logger 身份、`kc daemon --all` 漂移门）此前已解决。
- 变更后：① **删掉三处会随下一个提交立刻变假的现在时断言**。`evidence.json` 两个 item 与 PRD/证据报告各写着「远端 `zata/issue-278` 当前即指向 `27cfc95a`」——本轮复核时远端 tip 已是其后的记录件提交 `f0eb0c9b`（tree `141b84ca`），该句按构造已假。改成只声明可现算的不变量：`27cfc95a` 是**最后一个改动行为代码的提交**，判据是 `git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 为空（本轮实测为空），`gh api .../git/trees/c34c1a28…` 随时可解析。② **把 finding 1 的本地半边量化**：证据报告新增「已发布包 vs 交付包」小节，按 blob 逐条对表——已发布 tip `30aab5de` 带 42 路径、其中 13 条 blob 与交付一致、29 条不同（`rv-1-browser-runner-e2e.txt` 4,413 B 仍写着 `da043953` / `736dd811` / `DELIVERY_TREE=__PENDING__`），并整份缺第九轮的 3 条件（`rv-1-takeover-contract-spec.txt`、`scripts/rv1_contract_spec.sh`、`scripts/rv_upd_manifest_round9.py`）；交付包 45 路径（24 capture + 17 scripts + 4 入库件）会由 `list_evidence_upload_files` 递归全量带走。该小节同时记下交付侧 `evidence.json` 的体积 37,883 B 与 git blob `97ef2d6c7c1eef5b4966bdf3b79d51afa37b9561`（定稿后现算），并给出重发后 reviewer 可只读跑的三条判据（45 条路径、4 份入库件 blob 与 `git hash-object` 对表、未入库 capture 的自记绑定与报告对表）。③ **§9 Delivery Readiness 补上 marker 侧的机制口径**：`agent_runner_validation_gate.py:138` 判 stale 后重置勾选 + 摘 `validation/verifier-passed` + 发 `phase=validation_reset`，重发入口是 runner 在推送后走 `agent_runner_issue_handlers.py` 尾段的 `publish_validation_evidence_best_effort`（`head_sha` 由 `get_head_sha` 现取，天然等于最终推送 head），人工兜底 `kc recover --issue 278`。
- 原因：这一条没有行为可改，问题全在**发布状态**与**记录措辞**。发布状态属 runner（push 与 PR 评论不在执行器权限内，且本轮 GitHub API rate limit 已耗尽，执行器连 marker 现值都无法复读，只能沿用 finding 的观测）；记录措辞却是执行器自己写坏的——「当前指向」这类现在时断言在每个后续提交落地后都会自伪，而一个自称绑定交付 head 的记录如果假在时间上，reviewer 与闸门都无从判断它说的是哪棵树。把断言换成「最后一个改行为代码的提交 + 一条只读 diff 判据」之后，记录不再随 head 移动而失效，重发之后也能用 blob 而不是靠叙述证明「已发布 = 已交付」。
- 影响：入库改动 3 份（PRD、`evidence.json`、evidence-report），行为代码、测试、Skill 与文档零改动；`evidence.json` 仍是 2 items / 42 + 18 条 `stdout_assertions`（未增删断言，只改叙述）。finding 2 按继承性红灯记录，未在本 PR 动手：`src/backend/core/use_cases/pr_supervisor.py` 不在 `git diff --name-only 73c082c3 HEAD` 的本 PR 文件集内，且与 base `73c082c3`（live main）逐字相同（本轮改动的 `src/backend` 文件没有进入 1000 行门的视野——CI 那条命令的扫描范围是 `--glob "*.py" src/backend`）；拆分属 main 自己的重构，仓库已有一条未合并的本地分支 `fix-ci-python-file-line-limits` 把它拆到 982 非空行（新增 `pr_supervisor_diff.py` / `pr_supervisor_findings.py`），`hooks/max_file_lines.allowlist.txt` 亦禁止新增豁免，故处置权归仓库 owner，与本 Issue 无关。
- 验证（本轮实测）：`uv run pytest -o addopts="" -q --no-header` 的 rv-1 层（Console 动作 + HTTP + issue control + process runner）**141 passed**、§9 原列 8 文件集 **284 passed**，均与本包 capture 记录逐字相同；接管/契约/Skill/证据结构/发布/守卫层（`tests/test_run_verifier_agent.py tests/test_kedacode_operator_skill.py tests/test_agent_runner_prompt_contract.py tests/test_agent_runner_structured_evidence.py tests/test_agent_runner_validation.py tests/guards`）**550 passed + 1 skipped**，其中含 `kc daemon --all` 随包 Skill 漂移门与 1000 行守卫；改动前 `just test` 对 HEAD 的树 flag valid（`issue-278 @ f0eb0c9b` / tree `141b84ca`）。本轮三份记录件里 `evidence.json` 计入 test-effective tree，`check-test-flag` hook 因此把有效树判成 `6d1f9edb` 并拒绝提交（先红后绿，不是绕过去的：`pre-commit run --files` 复核时该 hook 从 Failed 转 Passed），故重跑 `just test`：full lint exit 0、pytest 侧 testmon 判定无代码增量而**收集 0 项**（`no tests ran in 0.06s`），flag 刷新到该有效树。0 项收集不构成行为证据，所以上面三个定向计数是用 `-o addopts=""` 直跑取来的；`CI=1 just test` 也跑了一次以绕开 flag 缓存，同样 exit 0。绑定复算：`git rev-parse 27cfc95a^{tree}` = `c34c1a28…`、`git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 空、`git diff --name-only 82ab9f18 27cfc95a -- src tests docs frontend-public` 恰为 13 个 CODE_DELTA 文件、`git merge-tree --write-tree 73c082c3 HEAD` exit 0。记录件门禁：`bash scripts/shared/just/check_prd_evidence.sh tasks/archive/P1-FEAT-20261011-160913-verifier-human-takeover.md .` exit 0（四张图仍就地嵌入：两张 prototype + 两张真实 Dashboard 端到端页面）、`bash scripts/shared/just/scan_evidence_secrets.sh tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover` exit 0（No credential-shaped strings）、`git diff --check` 干净。验收清单核对本轮**不再引用 CLI 的 exit 0**：`hooks/shared/check_prd_acceptance_checklist.py` 的 `_candidate_prd_paths` 按设计只收 active（`tasks/pending/`）或本次 staged 归档的 PRD，对这份**已在 archive 且未 staged** 的件会返回空集合并 exit 0——那是没有检查对象的 0，不是通过的 0（与本包第八轮「exit 0 却断言打不中」、`just lint --full` flag 缓存两次踩过的同一类坑）。本轮改直调它的校验函数核对：`_unchecked_items_in_acceptance_section(本 PRD 全文)` 得 **0 项执行器欠勾**（§9 的 9 条 `[~]` 属 runner-owned / 人工项，按该门自己的规则不计入），负控把 `- [x] docs/guides/agent-runner.md …` 一条改回 `[ ]` 后同一函数报出 **L508 该项未勾**，证明这道核对有鉴别力。
- 未做（权限外）：证据分支重发、PR marker 重贴与「不再出现 `validation_reset`」的复读都由 runner 在最终推送后执行；执行器不 push、不评论，本轮 `gh` 亦被 rate limit 拒绝。`Human-Confirmed` 与全部 `[~]` runner-owned 项一律未动，PRD 继续以 `🧍 待人工验收` 留在 `tasks/archive/`。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上核对本次叙述与 blob 事实一致；第十轮与第九轮的 ⑤「已解决」结论本轮逐条重跑未回退。

### 第十轮评审修复：交付锚点写进入库文件（pre-PR reviewer finding 2，executor，2026-10-14）

- 类型：evidence（纯记录件，不改行为代码、范围与 oracle）
- 原文：评审 finding 1 指出远端 `iar-evidence/issue-278`（tip `30aab5de`）与 PR marker 仍绑 rebase 前的 `da043953` / tree `736dd811`、`DELIVERY_TREE=__PENDING__`，签收门因此把「Realistic Validation sign-off」重置成 FAILURE——该条明确是 runner 侧发布动作。finding 2 指出同一件事的本地半边：交付锚点 `27cfc95a` / `c34c1a28` 只写在受 `.gitignore` 排除的 `.txt` capture 里，入库的 `evidence.json` 与 PRD §9 停在 `HEAD=82ab9f18` / `TREE=1e4c8907`，只读 PR 的人无法把 manifest 与交付 head 对上。
- 变更后：① `evidence.json` 两个 item 各写明 `DELIVERED_HEAD=27cfc95a4e60bfc360021ecb7f2dbd31f9bd0c18` / `DELIVERY_TREE=c34c1a28c5ed8b595a090bd7852ef1f3d2a1dd3f`，并把第八轮那句「DELIVERY_TREE 只写在 `.txt` 绑定块里，写进来会自指」改成可核口径：交付提交落地后该值指名的是**上一个已存在的提交**，写进入库文件不再是自指。② 同一批改掉 manifest 叙述里与自身 `stdout_assertions` 矛盾的旧计数（item 1 的 135→141，item 2 的 281/542→284/549），历史句中引用的旧 pattern 串保留并加「第八轮计数」标注。③ PRD §9 绑定项与本文件的 finding 1 叙述同步补写该锚点与两条只读复核命令（`git diff --name-only 27cfc95a HEAD -- src tests docs frontend-public` 为空、`git merge-tree --write-tree 73c082c335c1ae0c715c2f7208b4e3278fc58e38 27cfc95a` exit 0）。
- 变更后 ④：补上 `check_prd_evidence.sh` 的一条真实红灯——第九轮新增的两张真实浏览器截图（`rv-1-verifier-takeover.png` / `-narrow.png`）只写进了人审导航表、没有在报告里 `![]()` 就地嵌入，该门在修复前 exit 1（「下列证据图片没有在证据报告里就地嵌入」）；现按验证层级分别标注为**真实 Dashboard 端到端运行页面**（与两张 prototype 渲染明确区分）后嵌入，checker exit 0。两张图已逐张目视核对：详情头「独立 verifier 人工接管」显示「已交给你审查 · verifier 未完成」、run/head 与投影一致、标签已离开 `agent/running` 落到 `automated-review-passed`、PR #281 保留 mergeable，窄视口 390 不越界。
- 未做（权限外）：finding 1 要求的证据分支重发与 PR marker 重贴由 runner 执行；marker 的 `head_sha` 必须是**最终推送的那个 head**（本轮记录件提交之后即不再是 `27cfc95a`），照抄本节写的值反而会再次触发绑定重置。
- 验证（本轮实测）：`just test` exit 0（full lint passed；testmon 判定本轮只动记录件，增量收集 0 项）；受影响测试直跑 `uv run pytest -o addopts="" -q --no-header tests/test_console_actions.py tests/test_agent_runner_console_api.py tests/test_agent_runner_issue_control.py tests/test_process_runner.py tests/test_kedacode_operator_skill.py tests/test_agent_runner_prompt_contract.py tests/test_marker_compat_replay.py` **261 passed**；`tests/test_agent_runner_structured_evidence.py tests/test_backlog_prd_progress.py` **56 passed**；`evidence.json` 重新解析为 2 items / 42+18 条断言（未增删断言）；`bash scripts/shared/just/check_prd_evidence.sh` 与 `scan_evidence_secrets.sh` 均 exit 0。行为代码零改动，故第九轮在交付树 `c34c1a28` 上重跑的 capture 集合仍然成立。

### 第九轮评审修复：接管卡在途重读、CAS 未落盘不得受理、终止身份防 pid 复用，证据在交付树全量重绑（pre-PR reviewer findings，executor，2026-10-14）

- 类型：code + test + docs + evidence（三条行为缺陷 + 一条绑定失效；不改需求、范围与 oracle 定义）
- 原文：评审给本分支六条 actionable findings。① finding 1：rv-1/rv-2 capture 与已发布证据包绑的是 rebase 前的 `da043953` / tree `736dd811`，而 PR head 已换到 `82ab9f18` / tree `1e4c8907`，`agent_runner_validation_gate.py` 在 `evidence_marker.head_sha != pr_context.head_sha` 时直接重置清单——那份证据包在交付 head 上按构造不算数，且 `git diff --name-only da043953 HEAD` 与本 PR 文件集相交 25 项（含 main 侧 `agent_runner_merge_queue.py`、`review_once.py`）从未进过 rv-2 的 fail-closed 链。② finding 2：`verifier-takeover-card.tsx` 的空闲重读只在 `verifier?.state === "running"` 时启动，而这张卡可能在控制行**已经**被别的操作员翻成 `takeover_requested` 之后才挂载（页面重载、Issue 重开、第二个终端；父级按 `${repoId}:${issue.number}` keyed，`labelRefreshVersion` 那个 key 属于标签编辑器不是本卡），于是它只发一次 GET 就永远停在「已请求接管，正在停止」，既不确认 `taken_over`，也因 `can_take_over` 为假而没有任何出口——与本 PR 自己写在 `docs/guides/agent-runner.md` 的「界面继续轮询控制行投影，直到状态离开 running/takeover_requested」和 objective 6 相互矛盾。③ finding 3：`console_store_takeover.py` 在 CAS 匹配 0 行后回读，若仍读到同一 run/HEAD 的 `running` 行就幂等返回 `pending`，路由据此回 202、`console_actions.py` 把动作审计成 accepted，而那一次 UPDATE 什么都没写（无 `request_id`、无 `takeover_requested`、无 `autopilot_hold`），runner 永远观察不到取消，verifier 照样能发 PASS 并放行合并队列——一个自称 fail-closed 的特性里唯一的 fail-open 分支，且与方法 docstring 承诺的「CAS 未命中时不写任何状态」自相矛盾。④ finding 4：`_check_takeover_cancel` 用不带身份的 `_terminate_process_tree` 终止；子进程被 `communicate()` 回收后 `raise_if_cancelled` 抛进 except 分支，第二次拆除落到非身份分支，对**可能已被复用的 pid** 再取一次 `os.getpgid`（只排除 0/1 与 runner 自己的组），pid 复用时会把 SIGKILL 发给无关进程组——带身份的原语早已存在并用在停滞取消路径上。⑤ finding 5（main 的 `pr_supervisor.py` 1015 非空行）属继承性红灯，finding 6/7/8/9/10（rv-1 真实浏览器入口、Skill 契约名、merge 冲突、`run_verifier_agent.py` 行数、拆分模块 logger 身份）在本轮之前已解决，本轮仅按要求把它们重跑到交付树的树上。
- 变更后：① **界面**：重读门改为覆盖 `running` 与 `takeover_requested`（后者按 1.5s 交班档、前者按 15s 空闲档），落定 `taken_over` 时同样通知父级重扫并重读标签，其余终态停止加速轮询；`takeover_failed` 给出「没能确证停止这轮 verifier：它不会产出任何 verdict，保持位继续冻结自动监督……」的说明与「重新读取状态」出口；把 startPolling 与空闲轮询共用的落定反应抽成 `applyHandoverOutcome`，避免两处各写一份判定。**新增契约用例**两条（`verifier-takeover.spec.ts`）：mounted-requested（本机从未 POST，交班落定只能来自界面重读）与 takeover_failed（终态可重读、绝不出现「已交给你审查」与 Draft PR 链接）。② **存储**：CAS 未命中且回读仍 `running` 时重试一次并继续要求回读离开 `running`，两次都空手返回新增分类 `state_change_conflict`（core 端口常量 + 端口 docstring 的持久性契约 + `TAKEOVER_HTTP_STATUS` 409 + 镜像常量守卫同步），使 `accepted`/`pending` 都对调用方承诺「取消意图与 `autopilot_hold` 已经持久」；控制行 SELECT 收敛成一处常量。**新增回归** 4 条：空手两次必须 `state_change_conflict` 且控制行逐字未变、只空手一次时重试必须真的落盘、映射 409、未落盘的请求不得留下 accepted 审计。③ **进程终止**：`_ProcessWatchdog` 在 `Popen` 之后由 `_process_termination_identity()` 只读一次「进程组 + 创建时刻」，watchdog 命中取消那一轮与 except 分支收尾那一轮都走 `watchdog.terminate()` 复用这份身份（读不到身份时退回既有无校验路径，绝不因读不到就放弃拆除——否则管道写端仍有人持有、`communicate()` 永远等不到 EOF）；`process_runner_streams.py` 的 claude-stream 收尾同形修正。超时路径维持原状（评审明确该形状早于本 PR，本轮不在范围内动它）。**新增回归** 2 条：整轮取消里 `os.getpgid` 只被调用一次、两次拆除都带 expected 身份；既有真实进程组用例（孙进程回收、旁观进程与 runner 存活）继续守住边界。④ **文档与 Skill**：`docs/guides/agent-runner.md` 补上 202 的持久性前提与 `state_change_conflict`、终止身份只读一次的说明、重读不以「本机发起请求」为前提以及 `takeover_failed` 的可重试呈递；随包 `references/triage.md` 把 `409 outcome=state_change_conflict` 纳入拒绝类，并写明轮询要覆盖 `running` **或** `takeover_requested`。⑤ **证据**：两条 manifest command、11 份单项 capture、真实浏览器 harness 全部在交付树重跑，锚点由 capture 现算；`evidence.json`、三份报告与本 PRD 里「`885ca6f6` / `1db934ae` 是本 PR 当前 head」的叙述逐处改指为历史；计数断言随新增回归改到 141 / 222 / 284 / 549；新增 `rv-1-takeover-contract-spec.txt` 与 `scripts/rv1_contract_spec.sh`；`rv1_frontend_checks.sh` 加第四层（Playwright 包 typecheck，判据是接管 spec 0 条类型错误，整包既有基线报错如实打出）；harness 在隔离 HOME 里补 `COREPACK_HOME`（离线时 `pnpm dev` 不再变成一次 npm registry 下载）并把 Playwright 失败残骸复制到 `.iar/evidence/<stem>/rv-1-browser-failure/`（不放 `tasks/evidence/`：那里白名单放行 `*.md`，残骸自带 `error-context.md`）。
- 原因：接管是「人按下的停止键」，它的可信度全押在两件事上——界面说的是回读到的事实，以及「受理」这个词背后真的落了盘。前者让第二个操作员对着一个已经翻过的控制行永远看到「正在停止」，等于把界面写成与文档相反的契约；后者更糟：202 + accepted 审计配上没有 `request_id`、没有闸门的控制行，会让被接管的 verifier 照常发 PASS 并把合并队列放行，而这正是本特性要防的那一件事。终止调用复用创建时刻的身份不是洁癖：pid 会被复用，SIGKILL 打到复用后的进程组就是打到别人的进程上。证据绑在换过的 head 上时，reviewer 与闸门都无从判断它算不算数，所以每次代码增量都要在最终 tree 上重跑、由 capture 自己现算锚点，而不是让旧 capture 顶着新说法。
- 影响：行为改动面 12 个入库文件（界面 1、`src/backend` 5、契约端口与用例 2、测试与 spec 5 份、文档/Skill 2——按目录计）；HTTP 契约新增一类 409 分类 `state_change_conflict`，状态机迁移边、CLI 表面、Alembic 迁移、日志文本与 verdict 判定零变化，被接管的运行依旧不产出任何 verdict。rv 分层计数 135→141、281→284、542→549（+7 里只有 6 条是本 PR 新增，另 1 条是 rebase 带进来的 main 侧用例，逐文件 `--collect-only` 核过）、222 未变；`just test all` 4274→**4301 passed / 1 skipped**（326 秒，exit 0）、`just lint --repo` exit 0、`RV_STDOUT_ASSERTIONS: OK`（item 1 的 42 条 + item 2 的 18 条全命中）、真实浏览器入口 `RV1_BROWSER_RUNNER_E2E_OK: True` 且八项 facts 全 True、界面契约层 7 passed。本轮重跑另暴露一处**红在判据拼写、不红在行为**的缺陷（main 把 review 态标签更名为 `agent/automated-review-passed`），已把 harness 与 spec 的判据接回运行侧同一份配置；该标签在语义上是否适合承载人工交班，作为需人拍板项写进 §12，执行侧未擅改交班目标。`Human-Confirmed` 与全部 `[~]` runner-owned 项一律未动，PRD 继续以 `🧍 待人工验收` 留在 `tasks/archive/`；继承性 `Validate Template` 红灯（main 的 `pr_supervisor.py` 1015 非空行）按 finding 5 记录为 inherited context，未在本 PR 顺手拆无关模块。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核三条行为修复、界面契约层与重绑后的 capture。§9 的 `[~]` 项（真实 GitHub transport、prototype 并排人工审阅、PR evidence 发布）仍属 runner-owned 与人工验收，本轮不勾。

### 第八轮：stdout 断言按 runner 口径改为可命中，全部 capture 在干净 HEAD 上重跑重绑（recovery attempt 1，executor，2026-10-13）

- 类型：evidence + doc（证据侧断言口径与 manifest/报告/PRD 记录；不改需求、范围、oracle 定义，也不改任何入库行为代码与测试）
- 原文：runner 报出 `Realistic Validation item 1 exited 0 but its stdout assertion #37 failed: must contain '2 passed' in stdout`。根因不是接管行为变红，而是**断言口径**：runner 把 `stdout_assertions` 打在 manifest 里那条 `command` **自身的 stdout** 上，而我第七轮自查用的是「marker 能不能在对应 `.txt` 里 grep 到」——`rv1_browser_runner_e2e.py` 用 `capture_output=True` 收走 Playwright 的计数摘要、只写进 `rv-1-browser-playwright.txt`，命令 stdout 里从来没有 `2 passed` 这一串，所以那是一条**永远命不中**的断言。按同一口径复查 item 2，发现它的 `'281 passed'` / `'542 passed'` 是同类缺陷：那两层 pytest 根本没进 item 2 的 `command`，计数只存在于各自 capture 文件里。
- 变更后：① `rv1_browser_runner_e2e.py` 把 Playwright 自己的计数摘要行原样 print 到 stdout（`PLAYWRIGHT_TALLY: 2 passed (17.2s)`；解析不到摘要行就直接非零退出，不自造计数），并新增由该摘要推出的 `PLAYWRIGHT_TALLY_PASSED: True`（出现 failed / flaky / did not run 即不为 True）；item 1 的断言从裸 `'2 passed'` 改为带标签的 `'PLAYWRIGHT_TALLY: 2 passed'` + `'PLAYWRIGHT_TALLY_PASSED: True'`。② item 2 的 `command` 扩成真正产出本 item 所列三份 pytest 层的链（探针 → 竞态/fail-closed 222 → §9 原列 8 文件集 281 → 受影响后端合集 542）。③ 新增复核入口 `scripts/rv_stdout_assertions.py`：逐 item 用 `shell=True` 原样跑 recorded command，只拿那份 stdout 逐条比对声明的 pattern，未命中即非零退出并说明该串「只在 stderr 里 / 哪儿都没有」，同时把原样 stdout 落为 `rv-1-chain-stdout.txt` / `rv-2-chain-stdout.txt` 交给 reviewer 按同一口径复核。实测 item 1 的 40 条、item 2 的 18 条全命中，两条 command 均 exit 0，`RV_STDOUT_ASSERTIONS: OK`。④ 证据侧脚本全部收敛进 `tasks/evidence/<stem>/scripts/`（受 `.gitignore` 的 `tasks/evidence/**` 排除，不进交付 diff），并把前端契约层 capture 的入口从宿主固定路径 `/tmp/...` 迁到仓库内 `scripts/rv1_frontend_checks.sh`（`mktemp -d` 工作文件、逐项打印 exit code、任一非零即 `RV1_FRONTEND_CHECKS: FAIL` 并非零退出，无 `|| true` 兜底）；该层刻意**不**接进 item 1 的 command 链（`pnpm build` 会把链路拉到 runner 超时预算里），按其文件内记录的命令逐字复核。⑤ 全部 14 份 capture 在当前 HEAD 的干净代码状态重跑重绑（`HEAD=885ca6f6` / `TREE=e69e7059`、`CODE_DELTA=(none)`、`PUSHED_ANCHOR` 改从 `zata/issue-278` 远端跟踪引用现算 = `1db934ae` / tree `ed153d91`），`DELIVERY_TREE` 由 `scripts/rv_bind_tree.sh` 用不碰真索引的临时索引在记录件定稿后绑入并重算校验；§9 绑定项、`evidence.json` 的 `output_summary` 与三份报告同批改指。
- 原因：一次 exit 0 只证明命令没崩，不证明检查点成立——声明一条按构造就打不中的断言，等于把门禁写成装饰；runner 的判据是 command 自身的 stdout，所以证据必须让 reviewer 在同一条口径下可复核，而不是把 marker 藏进另一份文件里。把复核口径固化成一个可重跑脚本，是为了让下一轮改证据时这个缺陷类自己会报红，而不是等人再发现一次。宿主 `/tmp` 依赖会让 review 者拿不到可复制的入口，故一并收进仓库内脚本（但保持在交付 tree 之外）。
- 影响：入库行为源代码与测试一字未改（`git diff --name-only 885ca6f6 -- src tests docs frontend-public` 为空），接管/取消/竞态/交班的判定逻辑、日志文本与 HTTP/CLI 契约零变化；rv 分层计数与第七轮逐字相同（135 / 222 / 281 / 542），真实浏览器入口 Playwright 2 passed，运维触点探针正控 5/5 / 负控 0/5，`just test all` 在同一 HEAD 复跑 **4274 passed / 1 skipped**（253 秒，exit 0）。manifest 里 item 1 现为 18 份 evidence_files / 40 条 stdout 断言、item 2 为 5 份 / 18 条。`Human-Confirmed` 三项与全部 `[~]` runner-owned 项一律未动，PRD 继续以 `🧍 待人工验收` 留在 `tasks/archive/`；继承性 `Validate Template` 红灯（main 的 `pr_supervisor.py` 1015 非空行）与本 Issue 无关的 `config.toml` 自动合并开关两项披露保持原样。
- 审核：执行器自记。本轮属 runner 报出的证据基础设施缺陷修复，不是新增验收面；待独立 verifier 在最终 committed tree 上按 `rv_stdout_assertions.py` 的口径复核两条 command 的 stdout 断言与重绑后的 capture。若人工认为「stdout 断言可命中性」应当作为常驻门禁而非本轮自查，处置（例如把它接进 runner 侧校验）交人工裁定，执行侧不擅自扩需求。

### 第七轮评审修复：交接链路拆分模块的 logger 身份钉回原名、运维触点探针扩到第 5 条关键行、全量证据重绑（pre-PR reviewer，2026-10-13）

- 类型：code + test + docs + evidence（运维可观察性与取证口径，不改需求、范围与 oracle 定义）
- 原文：评审第 6 轮把 1000 行门禁拆出的两个 verifier 模块的 logger 钉回 `run_verifier_agent`，但同一条门禁在本分支自己的功能提交 `ac74d40d` 里还拆出过第三处：`agent_runner_failure_handoff.py` 从 `agent_runner_issue_handlers.py` 分出来时带的是 `_logger = logging.getLogger(__name__)`，于是「交班回链无法解析」这类交接诊断改挂到 `backend.core.use_cases.agent_runner_failure_handoff` 名下，而 `docs/guides/agent-runner.md` 的排查顺序让操作员按拆分前的名字 grep——功能可用，运维路径断了。同轮另有两项非代码事实：开工时索引区已 staged 一处与本 Issue 无关的仓库根 `config.toml` 改动（`[agent_runner.autopilot] enabled = false → true`），而交付提交用 `git add -A` 会把它一并带进 PR；`Validate Template` 的 1000 行硬门在合并 ref 上仍红，红在 main 自己的 `pr_supervisor.py`（1015 非空行）。
- 变更后：① `agent_runner_failure_handoff.py` 的 `_logger` 改为定值 `backend.core.use_cases.agent_runner_issue_handlers`（日志文本、分支与行为一字未改，只把 logger 身份钉回拆分前），并按 `tests/test_run_verifier_agent.py` 既有先例新增 `test_split_handoff_module_keeps_the_issue_handlers_logger_identity`（把两个模块的名字同时钉住——去掉定值即转红）。② `docs/guides/agent-runner.md` 排查段落同步说明「写入步骤已迁到 `agent_runner_failure_handoff.py`，这些行仍记在 logger `backend.core.use_cases.agent_runner_issue_handlers` 名下」。③ 运维触点探针 `rv1_verifier_log_identity_probe.py` 扩到第 5 条关键行：按操作员真实触点用生产 `Logger`/handler/formatter 写真实日文件，再 grep 那份文件断言 `%(name)s`——正控 `OPERATOR_GREP_HITS: 5/5`（前四类 `run_verifier_agent`、交接类 `agent_runner_issue_handlers`），`--negative-control` 在测试边界把三个拆分模块的 `_logger` 改回随文件名走（仓库源码不改），命中 `0/5` 并打印 `RV1_LOG_EXPECTED_FAIL`。④ 受影响后端 capture 由 16 文件扩到 17 文件（加入本轮被改动的 `tests/test_agent_runner_recovery.py`）→ 542 passed，manifest 的 rv-2 `output_summary` 与断言同步。⑤ 12 份 `rv-*.txt`（10 份共用同一绑定头，2 份浏览器 capture 由 harness 自带绑定块与真实 stdout） 全量重跑并重绑到 `HEAD=1db934ae` / `TREE=ed153d91` + 本轮 3 文件 `CODE_DELTA` + `DELIVERY_TREE`，§9 绑定项、`evidence.json` 与三份报告同批改指。⑥ 那处 staged `config.toml` 改动恢复到 HEAD，21 行 diff 原样保存在 gitignored 的 `.iar/evidence/local-config/issue-278-autopilot-enabled.patch`，并在执行侧报告 Open items 披露；main 的 `pr_supervisor.py` 单跑 CI 同一条命令复现 `[ERROR] 1015 非空行` / exit 1，作为继承性红灯记录，处置次序交人工裁定。
- 原因：操作员按文档排查「这轮 verifier 是被人工停掉的、还是自己跑超时的、交班有没有写成功」时，唯一线索是那几行诊断挂在哪个 logger 名下——按 #279 的拆分做法，模块文件换了名字不该跟着换，否则文档承诺的那条 grep 再也找不到行；这与第六轮 verifier 两处同源，是同一个缺陷类的第三处，因此按评审要求「不扩大验收范围」就地补钉，不新立需求。证据的可信度取决于 reviewer 能否独立解析它引用的 tree，所以每次代码增量都重跑并改绑，而不是让旧 capture 顶着新说法。自动合并开关与本 Issue 的方向相反（接管是人的动作），把它混进本 PR 会让一次代码合并顺带打开仓库级合并队列，故排除并留可恢复的补丁。
- 影响：行为源代码改动面只有 3 个文件（`src/backend/core/use_cases/agent_runner_failure_handoff.py`、`tests/test_agent_runner_recovery.py`、`docs/guides/agent-runner.md`），`git diff --name-only 1db934ae -- src tests docs frontend-public` 即为全部；接管/取消/竞态判定、HTTP/CLI 契约、状态机、Alembic 迁移、日志文本零变化，唯一可观察差异是交接诊断行所属的 logger 名。rv 分层计数 135 / 222 / 281 与第六轮逐字相同，受影响后端合集 524 → 542（新增的 18 项全部来自刚加入该 capture 的 `tests/test_agent_runner_recovery.py`），`just test all` 4273 → **4274 passed / 1 skipped**（本轮唯一新用例）。`Human-Confirmed` 与 `[~]` runner-owned 项一律未动，PRD 继续以 `🧍 待人工验收` 留在 `tasks/archive/`。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核 logger 身份修复、capture 重绑与 17 文件合集；继承性 CI 红灯与自动合并开关的处置由人工裁定。

### 隔离真实浏览器入口与交班后快照刷新（executor，2026-10-13）

- 类型：implementation + evidence（修正交班后 Dashboard 监控/标签快照陈旧，并补真实浏览器入口证据）。
- 原文：交班卡 fresh-read 已显示 `taken_over`，但父级只读本地监控快照，Issue 列表可能继续显示 `running`；标签编辑器自己的 fresh-read 也停留在交班前。真实浏览器 `real_entry` 曾标为未执行。
- 变更后：交班确认后重扫所属仓库，并重读标签面板。隔离 Playwright 入口真实走生产 Dashboard/API/SQLite/runner/verifier 进程组，断言 202 响应、run_id/head_sha 原样绑定、handoff、review 标签、Draft PR 保留、hold 与进程退出；桌面/窄视口截图和 trace 已生成。
- 原因：避免同一页面同时显示“已交给你审查”和旧的 `running` 标签；按既有 PRD mock_boundary 用确定性外部 GitHub 与 verifier CLI 替身，执行无需触碰机器级 Console DB 或真实 Issue。
- 影响：Dashboard 在 handoff 后呈现一致的 review 状态。Human-Confirmed 项仍未勾；真实 GitHub transport、prototype 并排人工核对、独立 verifier 和 PR evidence publication 未声称完成。
- 验证：真实浏览器链路在 `HEAD=9d3e0f3fb22dc0d452b4d042f2426856da232a74` / `TREE=9c9caa49e3275ea9f701598cf352ff6a5a25774a` 运行；`rv-1-browser-runner-e2e.txt`、`rv-1-browser-playwright.txt`、`rv-1-browser-runner-facts.json`、两视口截图和 trace 均记录通过结果。〔该绑定已被第七轮全量重跑取代，见本节顶部条目；此处保留当轮原记数值。〕
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核浏览器入口证据与交班后快照刷新。


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

### 第三轮评审修复：与 main 的 golden 配置测试冲突收敛、超长绑定回 400、rv-1 处置交回人工（pre-PR reviewer，2026-10-13）

- 类型：code + test + docs + evidence（修缺陷与验证侧根因，不改需求、范围与 oracle 定义）
- 原文：`git merge-tree main HEAD` 在 `tests/test_agent_config_consistency.py`（1 hunk）与 `tests/test_agent_runner_prompt_contract.py`（3 hunks）报 CONFLICTING，PR 停在 `mergeable=CONFLICTING`；根因是本 PR 与 main 各自独立修同一处宿主配置泄漏——本 PR 用 `tests/support/agent_runner.py` 的共享 helper，main 在同一批行上新增局部 `_root_config_app_config()` 并把 `test_factory_build_app_config_maps_supervising` 改成空 TOML 装配。另有两项：超长 `run_id`/`head_sha` 因 pydantic `max_length` 漂成 422，而文档与随包 skill 承诺畸形/过期绑定一律 400；rv-1 的真实浏览器入口被 stub 挡死，远端 CI 也没有 `tests/playwright-e2e` 入口，deferred gate 零覆盖。
- 变更后：① 泄漏修在根因：`tests/conftest.py` 的会话级 `_isolate_ambient_iar_config` 在剥离 `KEDACODE_CONFIG`/`IAR_CONFIG` 后**重建** `config.agent_runner` 单例，使 `build_app_config()` 在托管 runner 子进程里与 CI 解析到同一份仓库根配置（负控：临时撤回该行，`test_prompt_contract_execution_prompt_map_check` 立刻按宿主模板缺 map check 段转红）。② 两个冲突文件回到「谁也不重复谁」：`test_agent_runner_prompt_contract.py` 恢复 merge-base 文本，由 main 已落地的局部 pin 版本接管；`test_agent_config_consistency.py` 的 `maps_supervising` 同样交回 main 的空 TOML 版本，本 PR 只保留 main 未触及的仓库根来源断言（`root_config_toml_path()` + `root_config_app_config()`）。共享 helper 改为**显式 pin** `KEDACODE_CONFIG` 到仓库根（main 之后 `[agent_runner]` 段默认取状态目录，仅剥变量会读回机器配置），并拆出 `pinned_root_config_env()` / `default_config_discovery_env()` 两个语境。合并洁净度按 3-way 逐文件核对：改动集内全部文件与 main 零冲突。③ 长度护栏从模型层移到 handler 层（`_validate_issue_action_bindings`），超长绑定回 400 并保留进入存储前的上限，新增 `test_take_over_verifier_oversized_identity_returns_400`；`docs/guides/agent-runner.md` 与随包 `kedacode-operator` SKILL 同步「缺绑定或超长 → 400」。④ rv-1：spec 头与 §9/§13 改成可执行事实——`just e2e-install` 能装齐依赖与 chromium，缺的是隔离可登录全栈（本机 `kc console` 正占用机器级 `console.history.db` 与本 Issue 真实注册表，种假 `running` 控制行会污染操作员状态），且 stub 的两个端点正是 `must_cross` 的两段，跑绿也不构成证据；处置选项新增为第 3 条 `Human-Confirmed`，rv-1 与相关 `[~]` 一律保持未勾。⑤ 顺手修掉本 PR 自带 spec 的一处包内 eslint error（`import('@playwright/test').Page` → `import type { Page }`）。
- 原因：PR 不可合并是 high 级交付阻断，但「谁的实现留下」不由偏好决定——两处修复语义等价时，取上游已落地者、把共同根因收到 conftest，既消除重复又消除同一泄漏的第三处复发；400/422 的分歧属于对外契约，操作员侧按文档只分支 400/409/503；rv-1 属人工验收层，执行侧无权用 stub 层证据代答，也不该在本机把伪造控制状态写进真实控制面。
- 影响：HTTP 契约新增一类 400（超长绑定），CLI 表面与状态机不变；rv-1 自动化契约层 134→135、§9 原列 8 文件合集 279→280、受影响后端合集 522→523（rv-2 竞态层 221 不变）；全部 8 份证据 `.txt` 与三份报告、`evidence.json`、§9 文本一并重绑到 HEAD `7e0eb5af` + record-excluded tree `b6e92e21facdc8ba87893bfc632735f1a2b3edd3`（此前 §9 的 tree 标注落后两个评审轮，已对齐）。
- 审核：pre-PR reviewer 自记；`just test all`、`just lint --full`、`uv run mkdocs build --strict` 与三条跨进程 oracle 的复跑结果见证据报告「门禁」条，独立 verifier 仍须在最终 committed tree 上复核。

### 第四轮评审修复：超长 verifier 模块拆分过 CI 行门、证据绑定改回可解析 ref（pre-PR reviewer，2026-10-13）

- 类型：code + evidence + docs（工程一致性与取证口径，不改需求、范围与 oracle 定义）
- 原文：CI `Validate Template`（run 38301120693 / job 114984019375）以 `src/backend/core/use_cases/run_verifier_agent.py` 1007 非空行超过 1000 上限失败——本分支从 `5e2a8386` 起越线（`main` 与 merge-base 均为 813、`9f26fd87` 为 970，已逐 commit 复核）。本地看不到这条失败，是因为该检查只挂在 `.github/workflows/ci.yml` 与 `cd.yml`，而 `pre-commit`（以及复用它、带 `--warn-only` 的 `just lint --reuse` / `just lint --full`）只告警不阻断——`just test all` 与 pre-commit 全绿正是这个原因。另两处绑定缺陷：发布在 `iar-evidence/issue-278` 的证据包仍绑 `5e2a8386` / `e66a427e`，PR 的两个证据 marker 分别写 `head=9f26fd87` 与 `head=7e0eb5af`，从未指向交付提交 `abc3a557`；而 §9 那条已勾选断言引用的 `b6e92e21…` 是临时 index 生成的合成 tree，`git rev-list --all` 查不到，reviewer 既无法 cat-file 也无从复核。
- 变更后：① 按 #279（`2177bf66`）的做法把该文件拆成三个聚焦模块——`agent_runner_verdict.py`（结论模型 / 隐藏 marker / prompt 组装，169 非空行）、`agent_runner_verifier_response_log.py`（原始响应落盘：出结论、超时被杀、人工接管取消三条路径，118 行）、`agent_runner_verdict_pr_effects.py`（verdict 的 PR 侧副作用：GREEN 置 label、YELLOW 贴警告评论，87 行）；编排、候选 agent 选择与取消/竞态收敛留在原模块（降至 684 行），协议名继续由 `run_verifier_agent` 重新导出，因此既有导入路径与测试 monkeypatch 锚点（`rva.run_verifier_agent` / `rva.load_evidence_manifest` / `rva.get_head_sha` / `rva.has_changes` / `rva.run_agent_with_prompt_resilient`）逐字不变，属纯搬移、无行为变更。**未调高 1000 上限、未把文件加进 `hooks/max_file_lines.allowlist.txt`、未改 `tests/guards/**`**。② 8 份 `rv-*.txt` 在拆分后的工作树逐字复跑，绑定块由裸合成 tree id 改为 `HEAD`/`TREE`/`RUN_ON`/`CODE_DELTA`/`RECORD_EXCLUDED` 五项：`TREE` 用真实可解析的 `abc3a557^{tree}`，`RUN_ON` 与 `CODE_DELTA` 说清 capture 实际跑在该提交之上的哪 4 个文件上，`RECORD_EXCLUDED` 把记录性文件的排除范围写在 `.txt` 里（此前只有报告 caveat、`.txt` 头没有）；`evidence.json` 两条 `output_summary`、三份报告与 §9 同步改指。③ 修正 `rv-1-frontend-checks.txt` 的 ESLint 取数方式：前几轮用 shell 字符串传含 `(app)` 的路径，记录的退出码不是 eslint 的；改 argv 形式后 0 errors / 27 warnings / exit 0 可复现。
- 原因：CI 行门是交付阻断项，但调上限或加白名单等于把债推给下一个改这个文件的人；按既有先例拆分才动到根因——协议（可确定性解析的契约）与编排（进程、竞态、接管）本来就不是一件事，分开后两者都能独立阅读。证据的价值取决于 reviewer 能否独立复核：一个不可达的合成 tree id 让"已绑定交付 tree"这条 `[x]` 变成无法验证的断言，比不引用更糟，所以绑定必须改用 ref 可解析的形式并把排除关系写在证据本体里。
- 影响：后端导入表面与模块对外行为不变（`from backend.core.use_cases.run_verifier_agent import …` 全部照旧），HTTP/CLI 契约、状态机、Alembic 迁移零变化；`just test all` **4270 passed / 1 skipped** 与上一轮同数（拆分不增删用例），rv 分层计数不变（135 / 221 / 280 / 523），三支跨进程探针 marker 全 True。把新包推上 `iar-evidence/issue-278` 并改写 PR 的 `iar:validation-evidence` marker 属 runner 侧发布动作（`agent_runner_issue_handlers.py:730` → `agent_runner_validation_publication.py:92`），执行器不 push。
- 复核：CI 严格命令 `uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend --allow-list-file hooks/max_file_lines.allowlist.txt` exit 0；`just lint --reuse`（jscpd / pylint duplicate-code / check-architecture / check-guidelines-consistency / check-max-file-lines）全 Passed、`just lint --full` exit 0、`just test` 17 passed（testmon 增量档）、`just test all` 4270 passed / 1 skipped、`uv run mkdocs build --strict` exit 0、`git merge-tree --write-tree main HEAD` 无冲突路径；8 份 `.txt` 全部由本轮逐字复跑生成（命令、输出与真实 exit code 原样入档，无手写数值）。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核拆分等价性与重绑结果。

### 第五轮评审修复：随包 Skill 越界内容收回 references、Skill 漂移守卫补组回调派生、证据绑定指向 capture 所在的已提交 tree（pre-PR reviewer，2026-10-13）

- 类型：docs + test + evidence（工程一致性与取证口径，不改需求、范围与 oracle 定义，不改行为源代码）
- 原文：三项 high/medium 交付阻断都在交付物外围。① `Validate Template` 测的是 pull_request 的 **merge ref**：main 自 merge-base `2177bf66` 起把 `SKILL.md` 从 69 增到 70 非空行，本分支又独立 +1 行写接管条目，`git merge-tree main HEAD`（tree `7ad99a99`）得 71 行，超 `tests/test_kedacode_operator_skill.py:695` 的常驻上限 70——分支单独看是 70 行、绿的。② 同一 merge ref 上 `test_packaged_skill_command_examples_match_cli_help` 红在 `SKILL.md: 未收录的命令示例：kc daemon --all`：main 把 `kc daemon --all` 写进 `SKILL.md` 与 `references/daemon.md`，而 `_ALLOWED_FLAGS` 只有 `("daemon", "run")` / `("daemon", "status")`；main 自身在同一条断言上已经红（CI run 38292110956；本地亦可判定：`git grep 'kc daemon --all' main` 命中 main 的这两份受管文本，而 main 版白名单同样没有 `("daemon",)` 键），本 PR 继承的是**既有红**。③ 8 份 `rv-*.txt`、`evidence.json` 与三份报告仍绑 `abc3a557` / `a295951a`，那是交付提交 `750d03cb` 的父提交，其 tree 不含它自己 `CODE_DELTA` 列出的三个拆分模块，而 §9 已把「所有 R2/R3 行为证据均绑定交付 tree」勾成 `[x]`；远端 `iar-evidence/issue-278` tip `bec30aea` 更旧，绑的是不可达合成 tree `e66a427e`。另有 rv-1 真实浏览器入口（spec stub 的正是 `must_cross` 的两段）与「谁先修 main 的红」两项非执行侧可代答的处置。
- 变更后：① 接管条目从 `SKILL.md` 收回：细节（`active_verifier` 字段、`take_over_verifier` 端点与 400/409 绑定、`autopilot_hold` fail-closed、孤儿请求收敛为 `takeover_failed`、与 `kc run --all-ready --takeover` 的对比）整体迁进 `references/triage.md` 新增小节，主文件只在既有「daemon 互斥」条目尾部留一条路由指针（点名两个契约名并指向 卡住了 路由），并给该路由行与 frontmatter description 补上「人工接管这个 Issue 的验证」触发词——分支相对 merge-base 回到 **净 0 行**（69），合并 ref 因此是 70 行、在常驻上限内。② 漂移守卫不再凭记忆放行：新增 `_group_callback_flags_by_path()` 从真实 Typer 命令树收集「自身声明旗标的命令组」（本机派生只有 `("daemon",)` 与 `("console",)` 两条），`_real_flags_by_path()` 据此补齐路径，白名单加 `("daemon",)` 并照抄派生出的 13 个真实旗标；示例路径匹配改**最长匹配**（组入口与其叶子同时在表时，先命中组会把叶子降级成组的旗标集），并把逐例判定抽成 `_check_command_examples()` 供合并语境复用。新增 2 项守卫用例：一条按同一判定喂入合并 ref 才出现的 `kc daemon --all`（正例 + 「收录路径≠放行任意旗标」的反例），一条按组在前的字典序钉住最长匹配。③ 8 份 `rv-*.txt` 在交付树 `412ca523` 之上新跑并原样入档（命令、输出、真实 exit code），绑定块改指 `HEAD=750d03cb` / `TREE=412ca523…`，`CODE_DELTA` 只列本轮 3 个非行为文件，行为源代码一致性由 `git status --porcelain -- src/**/*.py frontend-public` 空输出证明；`evidence.json` 两条 `output_summary`、三份报告与 §9 同步改指，并在 §9 明确写出第四轮那条 `[x]` 的绑定写法不成立（绑定错，非行为差异）。
- 原因：`SKILL.md` 的 70 行常驻上限是「agent 每次载入的固定成本」，超线的代价由所有路由共担，所以接管细节属于按需载入的 references，主文件只留路由；继承自 main 的红不能等 main——CI 在 merge ref 上跑，本 PR 不带着修就永远是红的，而修法是让守卫去看**真实命令树**而不是把 `--all` 抄进一张凭记忆的表（`kc daemon --all` 确实合法：daemon 组回调自带 `--all`），这样合并后 `kc skill repair-operator` 之类的路径同理只需命令树真实存在即可判定。证据的可信度取决于 reviewer 能否独立解析它引用的 tree：绑到自己改动集的父树上，等于让「已绑定交付 tree」这条勾选无法验证，因此把绑定的语义固定为「capture 实际所在的那棵已提交 tree + 用 `CODE_DELTA` 声明差异面」。
- 影响：行为源代码零改动（`src/**/*.py`、`frontend-public`、HTTP/CLI 契约、状态机、Alembic 迁移均不变），改动集只有随包 Skill 两份文本、Skill 守卫测试与本证据目录；`tests/test_kedacode_operator_skill.py` 25 → **27 passed**，rv 分层计数与上一轮逐字相同（135 / 221 / 280 / 523），三支跨进程探针与两支负控 marker 全数复现。
- 复核：CI 的 `Validate Template` 语义按 merge ref 复现——用 `git merge-file` 把 main 与本工作树三份受管文本 + 守卫测试本体合成到临时目录后运行全部无 fixture 的 Skill 守卫：合并后 `SKILL.md` 非空行 **70**（≤ 70），`test_packaged_skill_command_examples_match_cli_help` 与常驻底线/路由/内容守卫 16 项全 PASS；该次模拟里 `test_packaged_skill_whitelist_matches_runtime_schema` 唯一一条红是 `schema 中不存在命令 ('skill', 'repair-operator')`——该命令只存在于 main 的 `src/backend/api`，模拟跑的是本分支安装的 CLI，属模拟环境差异，合并 ref 上源码与测试同树即消失。另复跑 `just lint --repo`（全 hooks Passed）、`uv run pytest tests/test_kedacode_operator_skill.py`、`uv run mkdocs build --strict` 与 `just test all`，数值见证据报告「门禁」条。真实浏览器 `just e2e` 层与 `iar-evidence/issue-278` 证据分支重传仍分别挂在 §9 Human-Confirmed 第 3 条与 runner-owned 门禁上，未因本轮改动而变勾。
- 审核：执行器自记，待独立 verifier 在最终 committed tree 上复核重绑与守卫派生。
