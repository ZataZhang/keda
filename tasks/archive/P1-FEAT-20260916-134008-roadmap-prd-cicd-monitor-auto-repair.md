# PRD: Backlog PRD 完成后 CI/CD 监控与可选自动修复

- GitHub Issue: https://github.com/ZataZhang/keda/issues/197

> ⛔ **交付前置**：`hard` — 本 PRD 按 Roadmap→Backlog 改名后的目标态书写（`iar backlog ci`、`agent_runner_backlog.py`、`/app/backlog` 等），硬依赖改名 PRD `P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md` 先交付。物化该依赖要求被依赖 PRD 先带 `- GitHub Issue: .../issues/N` 链接，故须**先对改名 PRD 执行 `iar issue-from-prd` 建 Issue**，再创建本 PRD 的 Issue。历史硬依赖的 Agent-led Post-PR CI Decision PRD `P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md` 已归档交付（`tasks/archive/`）。结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> 🧍 **验收状态**：待人工验收 — 执行侧交付完成：rv-2/3/4/5/6 全部通过并附证据（rv-3 e2e 4 passed + 真实 Backlog UI 截图、rv-4 定向 17 passed、rv-5 全量 2975 passed + lint/mkdocs/前端构建全绿、rv-6 真实 CLI 进程演练通过）；rv-1 的 live sandbox 演练按用户 2026-10-05 决定移入 Human-Confirmed 由人工验收时确认（fake GitHub 状态机已覆盖同一序列）；独立 verifier VERDICT: PASS。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文档分两个高度：**Part A（§1–§4）** 给人确认行为与风险，**Part B（§5–§13）** 给执行器实现和验证。

## Feature Overview (功能一览)

> 本块是 §10 Functional Requirements 的通俗投影，不是第二事实源；行为验收以 §1 行为样例表为准。

- **PRD 完成后必须等待真实 CI/CD**（FR-1、FR-2）：有 PR 的任务不能从“实现完成”直接进入交付完成；系统持续读取同一 PR 的 GitHub checks，直到通过或出现可见问题。
- **当前仓库的全局自动修复**（FR-3、FR-4）：Backlog 顶部提供“全局自动修复 CI/CD”开关，统一控制当前仓库所有 PRD；它只约束 Agent 选出的自动 repair 动作，不把 checks 状态映射成动作。
- **每个 PRD 可继承或覆盖**（FR-13、FR-14）：右侧提供 `跟随全局 / 强制开启 / 强制关闭` 三态控制；未设置时继承仓库全局值，并始终显示最终生效值。
- **允许多轮 repair 与复检**（FR-5、FR-6）：Supervisor Agent 根据 checks 详情与 PRD 要求决定是否 repair；每次获准的修复推送后重新读取新 head SHA，直到 Agent 选择其它动作或达到既有修复上限。
- **关闭时失败成为右侧问题**（FR-7、FR-8）：开关关闭不触发 Agent；失败 check 以问题卡显示原始名称、摘要、轮次和 GitHub 链接，保持可见直到状态改变。
- **崩溃重入不重复修复**（FR-9、FR-10）：状态从 PR checks 与既有事件 marker 重建，同一 head SHA/失败轮次只触发一次自动修复，不新增数据库事实源。
- **人工仍可发起单次修复**（FR-11）：自动修复关闭或耗尽时，操作者可从问题卡显式请求一次修复；该动作仍受 worktree、修复上限与安全门禁约束。
- **CLI 一等入口，供 agent 经 shell 驱动**（FR-15、FR-16、FR-17）：`iar backlog ci status|policy|repair` 提供只读观察（含 `--json` 机读）、仓库级/单 PRD 三态设置与一次性手动修复；与 Console 共用同一 core 状态与写回事实源，不新增第二套状态或存储。
- **原有本地验证、签核和合并门禁不变**（FR-12）：本功能只补齐 post-PR CI/CD 等待与呈现，不把预提交验证、人工 sign-off 或自动合并压成一个开关。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

当前 runner 已能读取 PR 的 `checks_state`/`checks_summary`，`review_once` 会在 PR 上下文变化时触发 supervisor。独立的 Agent-led Post-PR CI Decision PRD 将让 Supervisor Agent 结合原始 checks 与 PRD 验收要求决定等待、修复或交人审；本 PRD 在其上产品化 CI/CD 状态和用户可控的自动修复策略。Backlog 当前 `BacklogPrd` 契约只暴露 PRD 状态、验收计数和 next action，无法在选中 PRD 的右侧区分 checks、repair 轮次、策略与问题。

仓库另有 `runner.fix_agent_enabled`，但它只处理提交前 staged verification 失败，不是 GitHub PR checks 开关；直接复用其语义会把两个时点和两类副作用混为一谈。

### Interpretation (解读回显)

**行为样例**（下表每一行都会逐字变成验收标准，修改单元格即修改对应验收条件）：

| 输入 / 操作 | 期望观察到的结果 |
|---|---|
| PRD 实现已完成并已推送 PR，GitHub checks 为 `PENDING` | Backlog 右侧显示“等待 CI/CD”、当前 head SHA 与轮询状态；任务不得显示为可归档或已交付 |
| 全局自动修复已打开，checks 为 `FAILURE`，Supervisor Agent 根据已执行测试失败选择 `repair_pr_branch` | 对该 head SHA 至多启动一次获准的 repair；push 新 head 后重新获取 checks。`FAILURE` 本身不触发 repair |
| 自动 repair 后新 head 再次 `FAILURE`，Agent 再次选择 repair，直至达到 repair 上限 | 每个新 head 各记录至多一轮获准 repair；达到 `max_repair_attempts` 后停止自动动作并保留耗尽原因 |
| 全局自动修复未打开，checks 为 `FAILURE`，Agent 将零 job/账单限制判断为未执行且选择人审 | 不启动 repair、不产生修复提交；问题详情明确显示 CI 未运行/未验证，不得标成代码失败、通过或已验收 |
| 全局关闭，但选中 PRD 设置为“强制开启” | 仅该 PRD 的真实 CI 失败自动进入 repair；详情同时显示“强制开启”和“当前生效：开启”，其他未覆盖 PRD 仍关闭 |
| PRD 从“强制关闭”改回“跟随全局” | 清除显式覆盖并立即按当前仓库全局值计算；不得把当时的全局布尔值复制成永久 per-PRD 设置 |
| daemon 重启，或 GitHub 暂时不可达（失败/恢复情况） | 重启后从 checks 与 markers 恢复且同一 head 不重复修复；不可达时显示最近成功同步时间，不得当作通过或启动修复 |
| Agent 运行 `iar backlog ci status --json --repo-id <repo>`（真实 CLI 入口） | stdout 是与 Console `ci_delivery` 同构的纯 JSON（含 `checks_state`/`checks_summary`/轮次/effective policy/问题），进度与警告只走 stderr，`jq -e 'type'` 可解析 |
| Agent 先 `iar backlog ci policy --prd <path> on`，再 `iar backlog ci repair --prd <path>` | 前者写该 Issue 的最新 `iar:ci-auto-repair-policy` marker；后者走既有单次手动修复路径，受上限、worktree 与安全门禁约束且对同一 failure key 幂等；Console 刷新后显示“强制开启”与同一 effective value |

**我默默定了这些**：

- “全局”指当前选中仓库范围，默认关闭；该仓库所有 PRD 共享偏好，不是跨所有受管理仓库的一把总开关，也不为每个 PRD 保存独立开关。
- 每个 PRD 的策略是三态：`inherit`（默认/未设置）、`on`、`off`；最终生效值按“显式 `on/off` 优先，否则全局值”计算。
- 单 PRD 覆盖以对应 GitHub Issue 的最新 `iar:ci-auto-repair-policy` marker 为事实源；没有 Issue 的 PRD 只能跟随全局，控制项禁用并说明原因。
- “PRD 完成”指实现、提交、PR 创建与 runner 本地验证已结束，但交付仍需等待远端 checks；无 PR 的任务保持原有流程。
- `checks_state` 本身不触发动作。Supervisor Agent 判断真实执行失败、未运行/基础设施不可用或信息不足；自动修复开关和次数上限只约束 Agent 选择的自动 `repair_pr_branch` 动作。
- 轮次事实源使用 GitHub PR head SHA、checks 和现有 `iar:event` marker，不新增数据库表或前端自造历史。
- 多轮上限复用 `post_pr_supervisor.max_repair_attempts`，不再增加第二个次数配置。
- 手动“立即修复”是一次性请求，不会暗中打开仓库级自动修复。
- 问题卡以 GitHub `checks_summary` 为输入；若只能得到汇总状态，明确显示汇总错误而不伪造 job/日志细节。

**我理解为不做**：

- 不在本 PRD 中实现 GitHub Actions 日志全文抓取、日志流式展示或任意 CI 厂商插件系统。
- 不让开关绕过 verifier、人工 sign-off、禁止路径、rebase、合并或分支保护门禁。
- 不自动修复部署环境、密钥、额度、审批等需要外部权限或人工决策的问题。

**文字版解读**：本需求读作“把 PRD 的交付尾段扩展为可观察、可重入的远端 CI/CD 等待阶段；用户可按仓提供默认自动修复策略，也可让单个 PRD 跟随、强制开启或强制关闭，并允许多轮修复/复检。最终生效值必须可见；关闭时系统仍持续监控，只把失败作为右侧问题展示”。它不是新建 CI 执行器，也不是新增一套 Agent、队列或合并状态机。

### What The User Gets

Backlog 操作者能在顶部为当前仓库统一开关自动修复，并看到每个 PRD 的原始 CI 状态、Supervisor Agent 决定、未验证说明和下一次同步时间。开关只控制 Agent 选择的 repair 是否能自动执行；它不会将账单/零 job 的失败状态误当成代码失败。

同一组能力通过 `iar backlog ci` 暴露给 agent：`status` 读状态（`--json` 机读）、`policy` 设仓库级或单 PRD 策略、`repair` 触发一次性修复。CLI 与 Console 读写同一 core 与同一事实源，任一侧的改动对方 fresh 读取后立即生效。

### Measurable Objectives

- 有 PR 的任务在 checks 未 `SUCCESS` 前不会被展示为 CI/CD 已完成或进入最终完成态。
- 同一 PR head SHA 的同一失败观察最多触发一次自动修复；新修复 commit 产生新 SHA 后才允许下一轮。
- 开关关闭时，Agent 选择的自动 repair 不执行、不产生修复提交；Supervisor 仍会收到 checks 状态并能选择其它合法动作。
- 开关打开时至少验证两轮 `Agent selects repair → new SHA → Agent selects repair → new SHA → checks SUCCESS`；零 job/账单导致 workflow 未运行时不得由 FAILURE 自动触发 repair。
- daemon 重启、GitHub 暂时不可达和修复次数耗尽均不会造成重复修复、无限循环或假绿。
- 三态矩阵全部可判定：`inherit` 随全局实时变化，`on/off` 不受全局变化影响；清除覆盖后恢复 `inherit`。
- CLI 与 Console 双入口一致：同一 effective policy / 状态经两侧读写一致，`iar backlog ci status --json` 的输出与 Console `ci_delivery` DTO 同构；CLI 设置后 Console fresh 读取应立即反映，反之亦然。

## 2. Human Review Map (介入与风险地图)

### 决策一：自动修复默认关闭，并与 Autopilot/本地 Fix Agent 分开

自动修复会让 Agent 修改代码并推送新的 PR commit，副作用高于纯监控。推荐新增独立的仓库级 `post_pr_supervisor.auto_repair_ci=false`，Backlog 只切换这一项。它不跟随顶部 Autopilot 自动打开，也不复用提交前 `runner.fix_agent_enabled`；这样用户可以自动排程但仍人工处理 CI，或反之。

**Autopilot 与 CI 的关系（澄清，避免误解为"完全无关"）**：Autopilot 与 CI 的交点是**合并门禁**——其合并队列在 squash 合并前调用 `_wait_for_checks_green`（`agent_runner_merge_queue.py:203/485`）等待 checks 全绿；非绿时返回 `waiting_for_checks` 并等待下一轮，**不执行任何 CI 修复**。CI 失败后是否修复，仍由本 PRD 的 `auto_repair_ci` 策略与 Supervisor Agent 决定。因此 `autopilot.enabled` 与 `auto_repair_ci` 是两个独立开关：

| Autopilot | `auto_repair_ci` | 效果 |
|---|---|---|
| 开 | 开 | CI 失败 → Agent 自动修 → 修好后自动合并 |
| 开 | 关 | 自动合并；CI 失败不自动修（等人/手动修） |
| 关 | 开 | 人工合并；CI 失败 Agent 自动修 |
| 关 | 关 | 全人工（当前默认） |

**请确认：** 接受“自动修复 CI/CD”默认关闭，且不与 Autopilot、自动合并或提交前 Fix Agent 联动？

**验收：** 新仓库/缺省配置显示关闭；切换前后配置 diff 只有目标布尔值变化；关闭态不执行 repair、不产生修复 commit，Supervisor 仍可判断并呈递问题。

### 决策二：多轮 Agent repair 复用既有 supervisor 上限，耗尽后停下并显错

Agent 可能多轮选择 repair，但无限循环会持续消耗资源并可能反复改坏代码。推荐每个 PR 延续现有 `post_pr_supervisor.max_repair_attempts` 上限；仅 Agent 选择 repair 后，以 `head SHA + failure summary` 去重，每次修复成功推送后才进入新一轮。达到上限、worktree 不可恢复或 repair 失败时，停止自动动作，把原因作为问题保留给人。

**请确认：** 接受多轮自动修复受既有 repair attempts 上限约束，耗尽后不再自动重试，必须人工处理或显式发起单次修复？

**验收：** Agent 连续选择 repair 的场景严格执行配置轮数；最后一次耗尽后轮询仍继续、repair 副作用停止，右侧显示“修复次数已用尽”和当前问题。

### 决策三：单 PRD 使用三态覆盖，并随 Issue 保存

二态开关无法区分“明确关闭”和“没有设置”，会导致全局值变化时行为不可预测。推荐用 `inherit / on / off` 三态：默认 `inherit`；覆盖写入对应 GitHub Issue 的幂等策略 marker，daemon 与 Console 都从同一评论流读取最新值。这样覆盖跟随任务存在，不依赖只在本机可见的 Console SQLite，也不受 PRD 从 pending 移到 archive 的路径变化影响。

三态语义与最终生效值（`effective = 显式覆盖 ?? fresh 仓库全局`）：

| 单 PRD 存储值 | 界面文案 | 仓库全局 = 开 | 仓库全局 = 关 |
|---|---|---|---|
| `inherit`（默认 / 无 marker） | 跟随全局 | 开 | 关 |
| `on` | 强制开启 | 开 | 开 |
| `off` | 强制关闭 | 关 | 关 |

注：计算由**服务端**完成（`on → true`、`off → false`、`inherit → fresh repository global`），前端不得自行推断；无对应 Issue 的 PRD 只能 `inherit`。

**请确认：** 接受每个 PRD 使用“跟随全局 / 强制开启 / 强制关闭”三态，并将覆盖保存在对应 Issue marker；无 Issue 时只允许跟随全局？

**验收：** 全局开关与三态组成的六种组合都显示正确生效值；重启 daemon/console 后选择不变；PRD 归档改路径后仍按同一 Issue 读取覆盖；恢复跟随全局后立即响应全局变化。

三项决策保持在同一 PRD：全局默认、单 PRD 覆盖和多轮执行共同决定同一个 effective policy；拆开会迫使中间版本临时采用二态或双事实源，并让 daemon 与 UI 在过渡期对同一失败得出不同结论。

### 决策四：CLI 与 Console 并列为一等入口（供 agent 经 shell 驱动）

本功能的观察与控制在原设计里只有 Console 交互面，但 keda 的 agent 经 shell 使用 CLI；只有 Console 会让 agent 无法观察或驱动本功能。推荐新增 `iar backlog ci status|policy|repair` 三个子命令，全部作为既有 core 用例的薄封装，与 Console API 共用同一状态投影、设置 writer 与手动 repair 用例；`status --json` 复用 Console 的 `ci_delivery` DTO，不定义第二份 schema。

**请确认：** 接受 CLI 作为本功能的一等入口（观察 + 策略设置 + 单次修复），且 CLI 不引入独立状态或存储、机器输出复用 Console DTO？

**验收：** CLI 三命令与 Console 读写同一事实源；`status --json` 与 API DTO 同构；CLI 设置后 Console fresh 读取立即反映、反之亦然；两侧都不各自计算 effective value。

### 自动门禁，不需要逐项人工审阅

checks 展示、Agent repair action 策略、事件 marker 去重、未知状态降级、API 路径与前端渲染通过 core/API 测试和真实 Backlog E2E 覆盖；配置写回复用上游已交付的受限原子 writer（`repository_settings_editor` / `toml_section_editor.update_toml_table_keys`）；GitHub 网络在常规测试中由 fake client 隔离，真实 GitHub sandbox 验证为 opt-in。

**本次明确不涉及**：无数据库结构变化；不改 GitHub Actions workflow；不改 CI job 本身；不新增前端路由；不改 `frontend-admin/`。

## 3. Usage And Impact After Implementation

**Backlog 操作者**：仍从 `/app/backlog/` 选择仓库和 PRD。顶部提供仓库全局默认值；右侧提供当前 PRD 的“跟随全局 / 强制开启 / 强制关闭”和最终生效值。CI 状态卡展示 GitHub 原始状态；动作和原因展示 Supervisor Agent 的决定。Agent 判为未运行/基础设施不可用时，问题卡明确写“未验证”，而非“代码失败”或“通过”。关闭自动修复不会停止监控。

**代码审阅者/验收者**：从问题卡跳转到对应 GitHub check；可分辨机器 CI 失败、人工 sign-off 门禁和 GitHub 不可达。已有 PR 审阅、verifier 与 sign-off 流程保持不变。

**daemon 运维者**：daemon 的既有 review pass 继续刷新 checks 并唤起 Supervisor。打开自动修复后，Agent 选择 repair 才进入既有修复路径；重启不需要恢复新数据库，marker 与 PR head 足以重建状态。达到上限后持续观测并禁止新的自动 repair。

**CLI 调用方（agent 经 shell）**：`iar backlog ci status [--prd <path>] [--json]` 只读观察（`--json` 与 Console `ci_delivery` DTO 同构）；`iar backlog ci policy --global on|off`（仓库级）或 `iar backlog ci policy --prd <path> inherit|on|off`（单 PRD）设置策略；`iar backlog ci repair --prd <path> [--dry-run]` 触发一次性手动修复。三者均为既有 core 用例的薄封装，与 Console API 共用写回事实源，不新增存储。

**API 调用方**：既有 issue monitor 与 Backlog 列表保持兼容；Backlog PRD 响应增加结构化 CI 状态，另提供仓库级设置更新与显式单次修复动作。

## 4. Requirement Shape

- **actor**：Backlog 操作者、代码审阅者/验收者、daemon 运维者、**agent（经 `iar backlog ci` CLI）**；Console 与 CLI 读取同一份 core 状态。
- **trigger**：PRD 对应 PR 创建/更新后 checks 变化；操作者或 agent 切换自动修复（Console 或 CLI）；显式请求单次修复（问题卡或 `iar backlog ci repair`）；daemon review pass 重入。
- **expected behavior**：按 `PRD override ?? repository global` 计算最终策略；持续等待真实 checks；生效为开时按上限多轮修复并复检，关闭时零自动副作用且右侧展示问题；崩溃重启不重复处理同一轮；CLI 与 Console 双向一致。
- **explicit scope boundary**：只处理 GitHub PR checks 与既有 supervisor repair；不执行 CI workflow、不抓取完整日志、不改合并/签核门禁、不新增持久化表。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**当前相关路径**：

- `src/backend/core/use_cases/review_once.py` 已按 `checks_state` 变化触发 supervisor；当 Supervisor Agent 选择 `wait_for_checks` 时返回 `waiting_for_checks` outcome，`PENDING` 本身不触发动作改写。
- `src/backend/core/use_cases/pr_supervisor.py` 在 Agent-led Post-PR CI Decision PRD 交付后不再按 `FAILURE`/`PENDING` 强制改写动作；`execute_repair` 和 repair loop 已有次数上限。
- `src/backend/core/use_cases/agent_runner_merge_queue.py::_wait_for_checks_green` 已轮询 PR context，但面向自动合并，不是 Backlog 状态或可选修复控制面。
- `src/backend/core/use_cases/agent_runner_events.py` 已提供 `iar:event` marker 的格式化/解析与最新事件读取，是单 PRD 策略 marker 的复用模式。
- `src/backend/infrastructure/github_pr_ops.py` 已把 GitHub `statusCheckRollup` 聚合为 `checks_state` 与 `checks_summary`。
- `frontend-public/components/agent-runner/issue-detail.tsx` 已渲染 PR checks 与事件时间线，可复用状态标签语义，不复制 GitHub 状态翻译。
- `frontend-public/app/(app)/app/backlog/page.tsx`、`components/backlog/`、`lib/api/backlog.ts` 与 `lib/api/types.ts` 是 Backlog UI/API 契约入口。
- `frontend-public/components/backlog/prd-detail.tsx` 是上游已交付的统一右侧详情容器，暴露 `PrdDetailTab` / `additionalTabs`，是本 PRD 追加 CI/CD tab 的扩展点。
- `src/backend/infrastructure/config/repository_settings_editor.py`（白名单写回；当前白名单**只放行 `[agent_runner.autopilot].enabled` 单键**，且明确不提供通用 PATCH）与 `src/backend/infrastructure/config/toml_section_editor.py::update_toml_table_keys`（共享原语）是仓库 `.iar.toml` 设置的受限写入口。写入 `[agent_runner.post_pr_supervisor].auto_repair_ci` 必须扩展该 editor 的白名单与其端口（`IRepositoryAutopilotSettingsEditor` / `create_repository_autopilot_settings_editor`），而不是另造 writer。
- `src/backend/core/shared/models/backlog.py` 与 `src/backend/api/routes/agent_runner_backlog.py` 负责 Backlog PRD 状态及响应装配。
- `src/backend/infrastructure/config/agent_runner_settings.py` 和 `engines/agent_runner/factory_config_builder.py` 已映射 `post_pr_supervisor`/runner 配置。
- `src/backend/api/cli_typer_backlog.py` + `src/backend/api/cli_parsed_commands/backlog.py` 是 `iar backlog` 的 Typer 声明与 handler；`src/backend/api/cli_typer_app.py` 的 `_run_typer_repository_command` 是统一的仓库目标解析包装。本 PRD 的 `iar backlog ci` 子组复用同一模式，只做既有 core 用例的薄封装。

**Existing Path**：daemon `review_once` → PR context → supervisor decision → `execute_repair` → push/review → 下一次 checks；展示路径为 Backlog API → `BacklogPrd` → 统一 PRD 右侧详情；CLI 路径为 `iar backlog ci` → 同一 core 用例 → 同一事实源。

**Reuse Candidates**：checks 聚合、`pr_supervisor.py::build_rework_intent_comment` 与 `agent_runner_events.py` 的 `iar:event` marker parser、repair loop 与 `max_repair_attempts`、上游已交付的 `repository_settings_editor`（单一 `autopilot.enabled` 白名单，底层为 `toml_section_editor.update_toml_table_keys`，需为本 PRD 扩展白名单）、`prd-detail.tsx` 的 `additionalTabs` 详情扩展点、Backlog 30 秒刷新、Issue detail 的 checks badge/summary、`iar backlog` 的 Typer/handler 包装模式（`cli_typer_backlog.py`、`cli_parsed_commands/backlog.py`、`_run_typer_repository_command`）。

**Architecture Constraints**：自动修复策略和去重属于 core；GitHub 与 TOML 实现留在 infrastructure；API 只做 DTO/调用；前端只消费规范 API。不得让 core import FastAPI、tomlkit 或 concrete GitHub client。

**Frontend Impact**：**Full-stack**，只改 `frontend-public` Backlog 的统一右侧详情、API client 与类型；`frontend-admin` 无影响。运行命令 `just run frontend-public`，真实 UI 验证 `just e2e tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts`。

**Existing PRD Relationship**：硬前置 `P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md` 定义 Supervisor Agent 对原始 checks 与 PRD 验收要求的决策契约，并移除 checks→动作改写；本文仅在其上增加 Backlog 展示、repair 策略控制和轮次产品化。此前端硬前置 `P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot` 已归档，建立统一右侧详情、`additionalTabs`、Autopilot 设置 writer 与 PRD 证据 tabs。受限 `.iar.toml` 写回复用 `repository_settings_editor` / `toml_section_editor.update_toml_table_keys`。相关已归档 PRD：`P1-BUG-20260527-093356-agent-runner-ci-rework-state-recovery`（恢复）、`P1-FEAT-20260703-105322-autopilot-merge-queue-fast-profile`（checks 等待/自动合并）、`P1-FEAT-20260824-133115-runner-delivery-closeout-agent`（交付尾段）。

**Potential Redundancy Risks**：不要新建 CI worker、repair Agent、轮询线程、数据库 round/override 表或第二套 event log；不要用 PRD path 作为长期 override key；不要把 `runner.fix_agent_enabled` 改名挪用；不要在前端自行计算 effective value。

## 6. Recommendation

### Recommended Approach

在 Agent-led Post-PR CI Decision PRD 交付后，为 Supervisor 已选的 repair 动作增加策略约束，并在 Backlog 聚合一份只读 `ci_delivery` 视图：

1. 给仓库配置 `post_pr_supervisor.auto_repair_ci` 增加默认 `false`；扩展既有 `repository_settings_editor` 的白名单（当前仅放行 `[agent_runner.autopilot].enabled`）以写入 `[agent_runner.post_pr_supervisor]` 下的该键，底层仍复用唯一原子原语 `toml_section_editor.update_toml_table_keys`。
2. 从 Issue 评论解析最新 `iar:ci-auto-repair-policy`（`inherit/on/off`），与 fresh 全局配置合成唯一 effective bool；没有 marker 即 `inherit`。
3. `review_once` 仍持续观察所有 checks；Supervisor Agent 根据原始 checks 与 PRD 验收要求选择动作。仅 Agent 选择 repair 且服务端准备执行时才生成 failure key。
4. Agent 选择 `repair_pr_branch` 且 effective bool 打开、未超过上限时，走现有 repair；关闭/耗尽时拒绝自动副作用并返回可观察 outcome。不得由 checks 状态单独触发 repair/wait/blocked。
5. 从 PR context 与 markers 组装 `ci_delivery`：状态、当前轮次、问题列表、最近同步、stored/global/effective policy、耗尽原因；不持久化派生视图。
6. Backlog 顶部保留全局开关；右侧新增三态策略、effective value、CI/CD tab 和一次性手动 repair；策略 PATCH 写 marker 后 fresh 读取 Issue 评论回显。
7. 新增 `iar backlog ci` 子组作为一等 CLI 入口：`status`（只读，`--json` 复用 Console `ci_delivery` DTO）、`policy`（`--global` / `--prd`，复用同一 writer 与 marker 逻辑）、`repair`（复用单次手动 repair 用例）；三者为既有 core 用例的薄封装，与 Console API 共用事实源。

### Proposed Solution Summary (实现机制)

全局配置由 Backlog 操作者显式提供，系统不从 Autopilot 推断；单 PRD override 由对应 Issue 最新 marker 提供。core 统一计算 `override ?? global`，API 返回 stored policy、global value 与 effective value，前端不得自行推断。GitHub `statusCheckRollup` 是 checks 真值，事件 marker 是策略、修复请求/完成的幂等历史。CLI（`iar backlog ci`）与 Console 一样只是 core 用例的调用方，二者共用同一状态投影与写回路径，不复制策略计算或 repair 实现。避免新增存储、后台服务、WebSocket、CI provider abstraction 和重复 repair implementation。

### Alternatives Considered

- **复用 `runner.fix_agent_enabled`**：拒绝；该值控制 pre-commit staged verification，与远端 CI 的时点、Agent 和副作用不同。
- **把开关存进 Backlog SQLite settings**：拒绝；daemon 消费 `.iar.toml`，另存会形成两个事实源。
- **每个 PRD 保存独立开关/轮次表**：拒绝；需求是一个开关，轮次可由不可变 head SHA + markers 重建，新表增加迁移与一致性成本。
- **把 PRD 覆盖存入 Console SQLite 或按 PRD path 写 `.iar.toml` map**：拒绝；daemon 与其他机器可能看不到本地 SQLite，PRD 归档又会改变 path，Issue marker 才是稳定任务身份。
- **新增独立 CI polling worker/WebSocket**：拒绝；daemon review pass 与页面 30 秒刷新已覆盖所需时效。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

1. PRD 对应 Issue/PR 进入 review 阶段后，daemon 获取 fresh PR context。
2. 将原始 checks 状态/摘要与 PRD 验收要求交给 Supervisor Agent；不从 `PENDING`/`FAILURE`/`SUCCESS` 推导动作。
3. Agent 选择 `repair_pr_branch` 后才计算 failure key，检查 marker、自动修复策略与 repair 次数。
4. 自动修复关闭或次数耗尽时不产生自动副作用，持续展示问题；Agent 选择 wait、人审或请求输入等其它合法动作时保留其结论，并如实呈递未验证项。
5. repair push 后以新 head 为新轮，后续 daemon pass 重新等待 checks。
6. Backlog API 每次 fresh 读取 PR + marker，返回派生 `ci_delivery`；前端不缓存成功覆盖新失败。
7. CLI `iar backlog ci` 只调用上述同一 core 函数：`status` 投影同一 `ci_delivery`，`policy` 走同一 writer/marker 写入，`repair` 走同一单次 repair 用例；CLI 不自行计算 effective value，也不缓存。

### Change Impact Tree

```text
.
├── src/backend/core/shared/models/agent_runner.py
│   [修改]【总结】给 post-PR supervisor 配置增加独立的 CI 自动修复策略
│   └── 新增 auto_repair_ci，默认 false
├── src/backend/core/shared/models/backlog.py
│   [修改]【总结】定义 Backlog CI 交付状态、轮次和问题的跨层 DTO
│   ├── CiDeliveryStatus / CiCheckProblem
│   └── BacklogPrd 增加 ci_delivery
├── src/backend/core/use_cases/review_once.py
│   [修改]【总结】持续监控 checks，并只对 Agent 选择的 repair 应用策略与上限
│   ├── 复用 Agent-led CI 决策，不根据 checks 状态自行分类
│   ├── 用 head SHA + failure digest 去重
│   └── 保留明确 outcome/marker
├── src/backend/core/use_cases/backlog_ci_delivery.py
│   [新增]【总结】从 PR context 与事件 marker 投影 Backlog CI 状态，不持久化派生数据
│   ├── 组装当前轮次与问题列表
│   ├── 解析 inherit/on/off 并计算 effective policy
│   └── 构造一次性手动 repair 请求
├── src/backend/core/use_cases/agent_runner_events.py
│   [修改]【总结】增加单 PRD CI 自动修复策略 marker 的稳定格式化与 latest-wins 解析
├── src/backend/infrastructure/config/agent_runner_settings.py
│   [修改]【总结】加载 auto_repair_ci 的仓库默认值与校验
├── src/backend/engines/agent_runner/factory_config_builder.py
│   [修改]【总结】把 infrastructure 设置映射到 core AppConfig
├── src/backend/infrastructure/config/repository_settings_editor.py
│   [修改]【总结】把受限写回白名单从单一 autopilot.enabled 扩展到 post_pr_supervisor.auto_repair_ci
│   └── 新增写回方法/表路径；复用同一 toml_section_editor.update_toml_table_keys，不另造 writer
├── src/backend/core/shared/interfaces/runner_console.py
│   [修改]【总结】为仓库级 CI 自动修复设置扩展写回端口契约（IRepositoryAutopilotSettingsEditor）
├── src/backend/engines/agent_runner/factories/__init__.py
│   [修改]【总结】装配扩展白名单后的受限设置 editor（create_repository_autopilot_settings_editor）
├── src/backend/api/routes/agent_runner_backlog.py
│   [修改]【总结】暴露 CI 状态、自动修复设置和一次性手动修复入口
│   ├── Backlog response 增加 ci_delivery
│   ├── PATCH auto-repair 设置
│   ├── PATCH PRD override 并 fresh 回读 Issue marker
│   └── POST 单次 repair，保留幂等/上限门禁
├── src/backend/api/cli_typer_backlog.py
│   [修改]【总结】新增 `iar backlog ci` 子组（status / policy / repair），复用 `_run_typer_repository_command`
├── src/backend/api/cli_parsed_commands/backlog.py
│   [修改]【总结】实现三个 ci 子命令 handler：薄封装既有 core 用例，`status --json` 复用 `ci_delivery` DTO
│   └── 不复制策略计算或 repair 实现；与 Console API 同源
├── frontend-public/lib/api/types.ts
│   [修改]【总结】同步 CI 交付、问题和设置契约
├── frontend-public/lib/api/backlog.ts
│   [修改]【总结】封装 auto-repair PATCH 与单次 repair API
├── frontend-public/components/backlog/prd-detail.tsx
│   [修改]【总结】经上游已交付的 `additionalTabs` 扩展点增加 PRD 三态策略、effective value、CI/CD tab 和问题卡，不重构详情容器
├── frontend-public/app/(app)/app/backlog/page.tsx
│   [修改]【总结】在顶部接入当前仓库全局开关，并把轮询结果与 repair action 接入选中 PRD
├── tests/
│   [修改/新增]【总结】覆盖配置映射、关闭零副作用、多轮修复、重启去重和 API 契约
├── tests/playwright-e2e/tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts
│   [新增]【总结】从真实 Backlog 入口验证等待、关闭显错、开启多轮和耗尽态
├── src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md
│   [修改]【总结】把 `iar backlog ci status|policy|repair` 加入随包 operator skill 的命令表
│   └── 承接 `P1-FEAT-20260930-141135` FR-6 的"知识随包发"约定：新 CLI 必须同步进 skill
└── docs/
    [修改]【总结】同步 runner 配置、Backlog CI 流程与 API 文档
    ├── prototypes/hub.html + assets/prototype-hub.css、index.md / mkdocs.yml：原型卡片与 Hub 入口**已在仓库落地**（commit 0d36877，mkdocs.yml:78 已登记），执行器只需核验存在与可发现，**不重新生成**
    └── roadmap-prd-cicd-* 原型资产（html/md/png/prompt 旁车）同样已交付，勿重复生成
```

前端落点已按上游归档 PRD 的最终结构核实：统一详情是 `frontend-public/components/backlog/prd-detail.tsx`，其 `PrdDetailProps.additionalTabs` 即为本 PRD 追加 CI/CD tab 的扩展点（上游归档 PRD §9 已明确该容器按可扩展设计）。实现前先运行：

```bash
rg -n "checks_state|post_pr_rework_requested|execute_repair|max_repair_attempts|BacklogPrd" src/backend frontend-public tests
rg -n "fix_agent_enabled|autopilot.enabled|auto_merge" src/backend docs config.toml
```

### Risk Classification Register

| Change point | Tier | Decisive dimension / override | Intervention | Oracle / gate |
|---|---|---|---|---|
| post-PR CI 自动修复策略与多轮去重 | R2 | core orchestration fixed zone；错误会重复修改/推送代码 | Human confirmation | rv-1 |
| per-PRD override 与继承计算 | R2 | core workflow contract；错误会对错误任务产生或抑制代码修改 | Human confirmation | rv-2 |
| `.iar.toml` 设置写回 | R2 | 跨进程配置与持久文件完整性 | Human confirmation | rv-2 |
| Backlog CI 状态/问题显示 | R1 | 单一 UI/API 视图，可逆且无写副作用 | Executor + E2E | rv-3 |
| 手动单次 repair | R2 | 显式远端代码修改动作，但复用既有受限 repair | Executor + strong oracle | rv-4 |
| 文档、类型、构建 | R0 | 机械同步 | Automated gates | rv-5 |

### Executor Drift Guard

上游 PRD 已交付并归档，统一详情组件（`prd-detail.tsx`，含 `additionalTabs`）与配置 writer（`repository_settings_editor`，底层 `toml_section_editor.update_toml_table_keys`）已在代码库落地，Change Impact Tree 是起点而非完整文件清单。注意 writer 当前只放行 `[agent_runner.autopilot].enabled` 单键，本 PRD 写入 `post_pr_supervisor.auto_repair_ci` 前必须先扩展其白名单/端口契约；扩展须复用同一原子原语，不得另造 writer。实现前用上述 `rg` 重定位最终 symbol；这些既有扩展点必须复用而非另造。交付前用 `rg -n "auto_repair_ci|ci_delivery|ci_failed_manual|ci_repair_exhausted"` 检查契约贯通，并用 `rg -n "fix_agent_enabled.*CI|autopilot.*auto_repair"` 排除错误联动。

### Flow / Architecture Diagram

```mermaid
flowchart TD
    A["PRD 实现完成并推送 PR"] --> B["daemon review_once 获取 fresh PR context"]
    B --> C{"checks_state"}
    C --> D["Supervisor Agent 结合 checks 证据与 PRD 要求选择动作"]
    D --> E{"Agent 选择 repair?"}
    E -->|否| G["执行 Agent 选择；披露 CI 未验证/问题"]
    E -->|是| H["读取 PRD inherit/on/off + 仓库全局值"]
    H --> M{"策略开启且仍有预算?"}
    M -->|否| I["拒绝自动副作用；问题持续呈现"]
    M -->|是| J["既有 execute_repair 修复同一 PR 分支"]
    J --> K["推送新 head SHA"]
    K --> B
    I --> L["显式单次立即修复"]
    L --> J
```

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: Supervisor Agent 根据执行失败证据选择 repair 后，自动修复策略允许时跨新 head 重试；同一 Agent repair 决定重入不重复修复
  reviewer: human
  real_entry: "在 GitHub sandbox 仓库运行 daemon：为测试 PR 依次产生 FAILURE(head A) -> repair/head B -> FAILURE -> repair/head C -> SUCCESS，并在 /app/backlog 查看状态"
  expected: "每次 repair 都对应 Agent 合法 repair action；每个新 head 至多一次且最多两轮；零 job/billing 场景不因 FAILURE 自动 repair；重启后同一 action/failure key 不重复"
  mock_boundary: "opt-in sandbox 使用真实 GitHub PR/checks、真实 daemon/worktree/repair Agent；默认 CI 用 fake GitHub 状态机覆盖相同序列，不要求生产凭据"
  tier: R2
  test_layer: sandbox
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair/rv-1-multiround-ci-repair.webm；自检：时间线只有第 1、2 轮两次 repair，最终为通过"
  critical_value_source: "GitHub statusCheckRollup 返回的 checks_state/checks_summary 和每次 PR head SHA；轮次来自既有 iar:event marker"
  must_cross: "GitHub checks -> infrastructure GitHub client -> review_once policy -> existing execute_repair -> git push new head -> new GitHub checks -> Backlog API -> browser"
  forbidden_bypasses: "不得直接调用 repair helper冒充 checks 触发；不得手工注入前端轮次；不得复用旧 head 的 SUCCESS；不得绕过真实 push"
  fresh_state_probe: "每次 repair 后用新 GitHub API request读取新 head/checks；中途重启 daemon 并从新浏览器 context 读取 Backlog"
  final_tree_evidence: "录屏、event marker 与 PR head 序列在最终代码树重采；policy、GitHub adapter、repair 或 UI 改动后重跑"
  negative_control: "在 auto_repair_ci=false 的同一 FAILURE 序列运行一个 review pass"
  expected_fail: "若出现 Agent 调用或新 head，负控失败；页面应只显示问题"
- id: rv-2
  behavior: 自动修复默认关闭；PRD 可跟随全局、强制开启或强制关闭，六种组合的 effective value 正确；关闭态零自动副作用，耗尽后停止副作用并保持问题
  reviewer: human
  real_entry: "真实 console + 临时仓库 .iar.toml，在 /app/backlog 切换自动修复，刷新并运行 FAILURE/耗尽场景"
  expected: "缺省全局关闭且 PRD=inherit；六种组合全部正确；切回 inherit 会跟随全局变化；配置 diff 只有 auto_repair_ci，override 只产生 latest policy marker；关闭态可以运行 Supervisor 判断但不执行自动 repair/commit；耗尽后显错"
  mock_boundary: "真实 FastAPI、真实临时配置文件和真实浏览器；GitHub/Agent 用记录副作用的 fake，配置 writer 不 mock"
  tier: R2
  test_layer: e2e
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair/rv-2-toggle-off-and-exhausted.webm + rv-2-config-diff.txt；自检：关闭态问题可见且 worktree HEAD 不变"
  critical_value_source: "全局值来自 fresh AppConfig；override 来自对应 Issue 最新策略 marker；问题来自 checks_summary；commit 计数来自真实临时 worktree HEAD"
  must_cross: "global: browser PATCH -> API -> TOML writer -> fresh load；override: browser PATCH -> API -> GitHub Issue marker -> fresh comments parse -> core effective policy -> daemon/UI"
  forbidden_bypasses: "不得写 console.db/前端 localStorage；不得按 PRD path 长期存 override；不得联动其他开关；不得由前端计算 effective value"
  fresh_state_probe: "重启 console 后新标签读取开关；每个场景从新 worktree/GitHub fake 状态重建并读取 HEAD"
  final_tree_evidence: "录屏与 config diff 在最终 writer/API/UI 树采集；任一相关变更后重采"
  negative_control: "临时测试边界把 auto_repair_ci=false 改为 true 后重跑关闭场景"
  expected_fail: "出现 repair 调用/新 commit，零副作用断言变红"
- id: rv-3
  behavior: Backlog 同时呈现 GitHub 原始 checks 状态与 Supervisor 决定/未验证说明；全局/单 PRD effective repair policy 在刷新后稳定
  reviewer: human
  real_entry: "just e2e tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts"
  expected: "原始状态、check 摘要与 Agent outcome 分开呈现；零 job 场景明确未验证；策略显示刷新稳定，不把 aggregate FAILURE 呈现成代码失败或通过"
  mock_boundary: "真实 console/FastAPI/Next.js 页面；GitHub adapter 在 API 边界提供确定性 PR context，Backlog under-test path 不 mock"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair/rv-3-backlog-ci-problems.png（标注 real UI / fake GitHub boundary）；自检：CI/CD tab 数字等于卡片数"
- id: rv-4
  behavior: 自动修复关闭或耗尽时，操作者可显式请求一次修复；重复点击/重试对同一 failure key 幂等且仍受修复上限、worktree 和禁止路径门禁
  reviewer: verifier
  real_entry: "uv run pytest -o addopts=\"\" tests/test_backlog_ci_delivery.py tests/test_backlog_api.py -k 'manual_repair or idempotent or exhausted' -v"
  expected: "首次合法请求进入既有 repair；重复请求不重复 commit；耗尽、无 worktree 或无法 fresh-read 当前 PR/checks 时返回明确冲突/no-op"
  mock_boundary: "FastAPI TestClient 和 core policy 真实；GitHub/Agent/process runner 使用记录副作用 fake"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "请求中的 PRD path 经 server 解析到当前 PR number/head/failure key，不能由前端提交可信 SHA"
  must_cross: "POST manual repair -> repository/PR resolution -> current failure validation -> existing repair gate -> marker/side effect"
  forbidden_bypasses: "不得接受客户端伪造 head/round；不得直接 shell 调 Agent；不得绕过 max attempts、forbidden paths 或 dirty worktree checks"
  fresh_state_probe: "请求后重新读取 Issue markers、PR head 与 worktree；第二次相同请求从 fresh TestClient 验证零新增副作用"
  final_tree_evidence: "定向测试在最终 API/policy/repair 树运行；契约或 repair 边界变化后重跑"
- id: rv-5
  behavior: 后端/前端契约、架构、复用、全仓测试、静态构建和文档保持通过
  reviewer: verifier
  real_entry: "just lint --reuse && just lint --full && just test && cd frontend-public && pnpm typecheck && pnpm build && cd .. && uv run mkdocs build --strict"
  expected: "全部退出 0；无重复 CI 状态机、反向依赖、类型漂移或文档断链"
  mock_boundary: "各质量门禁既有边界；无行为 mock"
  tier: R0
  test_layer: smoke
  required_for_acceptance: true
- id: rv-6
  behavior: CLI `iar backlog ci` 与 Console 一致——status 只读输出（含 `--json`）与 API DTO 同构；policy 写同一事实源；repair 复用单次手动修复且幂等
  reviewer: verifier
  real_entry: "真实 CLI：`iar backlog ci status --json --repo-id <repo> | jq -e 'type'`；`iar backlog ci policy --prd <path> on` 后 `iar backlog ci repair --prd <path>`；随后在 Console fresh 读取同一 PRD"
  expected: "status 的 stdout 是纯 JSON 且与 Console `ci_delivery` 同构（进度/警告在 stderr）；policy 只写该 Issue 的最新 marker；repair 走既有单次 repair、对同一 failure key 幂等并受上限/worktree/禁止路径门禁；CLI 设置后 Console 显示同一 effective value，反之亦然"
  mock_boundary: "真实 Typer CLI 与 core；GitHub/Agent 用记录副作用的 fake，配置 writer 与 marker 解析不 mock"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "effective policy 来自 core 同一函数；PR head/失败 key 由 server 侧解析，不接受 CLI 传入的可信 SHA；状态来自同一 `ci_delivery` 投影"
  must_cross: "CLI -> same core use case -> (settings writer | Issue marker | existing repair gate) -> Console fresh read shows same value"
  forbidden_bypasses: "CLI 不得另存状态、不得自行计算 effective value、不得接受客户端伪造 head/round、不得绕过 gate"
  fresh_state_probe: "CLI 写入后从 Console/API fresh 读取；重复 repair 命令从新进程验证零新增副作用"
  final_tree_evidence: "定向 CLI 测试与真实命令在最终 CLI/core 树运行；契约或 core 用例变更后重跑"
```

失败排查：重复 repair 先检查 failure key 与 `post_pr_rework_requested` marker；假绿先核对 head SHA 是否为最新；页面问题缺失先比较 GitHub adapter `checks_summary` 与 Backlog `ci_delivery.problems`；设置漂移先检查 `repository_settings_editor` 的 allowlist 和 fresh load；CLI 与 Console 不一致先确认二者都调用同一 core 用例（`rg -n "backlog_ci|backlog ci" src/backend/api src/backend/core`）而没有各自实现。

### Low-Fidelity Prototype

```text
┌ Backlog 仓库控制条 ───────────────────────────────────────────────┐
│ [● Autopilot 自动推进]  [○ 全局自动修复 CI/CD · 已关闭]           │
│                         当前仓库所有 PRD · 失败后修复并持续复检    │
├ PRD 详情 ─────────────────────────────────────────────────────────┤
│ Agent Runner 会话持久化  [等待 CI/CD]                 [查看 Issue] │
│ 此 PRD 的自动修复 [跟随全局*] [强制开启] [强制关闭]              │
│ 当前生效：关闭 · 未单独设置，继承当前仓库全局策略                │
│ [PRD 原文] [验收证据 2] [CI/CD 1]                                 │
│ ┌ CI/CD 检查未通过 [1 个问题]  最近同步：刚刚 · 下次：30 秒后 ┐ │
│ │ ✓ 等待检查 ── ✕ 第 1 轮失败 ── ○ 等待处理                    │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌ backend / typecheck [失败]                [查看 GitHub 检查] ┐ │
│ │ mypy 类型检查失败：SessionSnapshot 缺少 resume_token 字段     │ │
│ │ 自动修复已关闭；问题保留在详情中，不会启动修复 Agent。        │ │
│ │                                          [立即修复此问题]     │ │
│ └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

高保真草图：`docs/prototypes/assets/roadmap-prd-cicd-auto-repair.png`。

### Interactive Prototype Change Log

| File Path | Change Type | Before | After | Why |
|---|---|---|---|---|
| `docs/prototypes/assets/roadmap-prd-cicd-auto-repair.png` | Add | 原 Backlog 草图只有 PRD 原文/验收证据 | 顶部增加仓库全局开关，详情增加 PRD 三态覆盖、生效值、CI/CD tab、多轮摘要与问题卡 | 确认继承/覆盖、关闭显错和多轮监控的信息层级 |
| `docs/prototypes/assets/roadmap-prd-cicd-auto-repair.prompt.md` | Add | 图片没有可继续编辑的出处记录，提示词只在说明页里以摘要形式存在 | 记录保存方式、保持不变区域、逐区域变化、画布与日期，并完整保存本轮两条模型提示词原文与未采纳原因 | 让 AI 生成/编辑图片下次能基于原提示词继续编辑，而不是从零反推 |
| `docs/prototypes/assets/roadmap-prd-cicd-0{1..4}-*.png` + 各自 `.prompt.md` | Add | 只有单张静态草图，无法演示多轮失败与恢复 | 四张 1536 × 1024 状态图（等待 / 第 1 轮修复 / 第 2 轮复查 / 通过）与同名旁车 | 让评审能按轮次走完主线，并为每张图留下出处 |
| `docs/prototypes/roadmap-prd-cicd-auto-repair.html` + `assets/roadmap-prd-cicd-flow.{css,js}` | Add | 静态草图无法点击切换状态 | 图片状态原型入口，保留“关闭自动修复后问题留在详情中”的人工分支 | 用最短路径补上点击演示，并回链说明页 |
| `docs/prototypes/assets/prototype-hub.js` | Add | Hub 只有静态卡片，状态数据手写 | registry 作为唯一清单，由界面渲染列表与详情抽屉 | 避免 HTML/Markdown/脚本三份状态数据互相漂移 |
| `docs/prototypes/roadmap-prd-cicd-auto-repair.md` | Add | 无本功能视觉说明 | 记录草图、交互边界与生成方式 | 让评审和实现者可追溯 |
| `docs/prototypes/hub.html` + `assets/prototype-hub.css` | Add | 仓库只有 Markdown 链接索引，没有可视化原型 Hub | 增加缩略图卡片、最新标记、原图/说明/演示入口与移动端布局 | 让评审从统一视觉入口发现原型，而不是依赖文件路径 |
| `docs/prototypes/index.md` + `mkdocs.yml` | Modify | 无 CI/CD 原型和 Hub 入口 | 增加 Prototype Hub 与本草图导航 | 保持文档站可发现 |

### Data Model

No database schema changes in this PRD. `ci_delivery` 是从 GitHub 和事件 marker 投影的运行时 DTO；仓库默认值持久化为 `.iar.toml` 布尔值，单 PRD 覆盖持久化为对应 GitHub Issue 的 latest-wins marker。

### External Validation

No external validation required; repository code and existing PRDs were sufficient.

## 8. Delivery Dependencies

- Group: backlog-delivery-control
- Depends on tasks/issues:
  - tasks/pending/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md
- Gate type: hard
- Notes: 本 PRD 按 Roadmap→Backlog 改名后的目标态书写（`iar backlog ci`、`agent_runner_backlog.py`、`components/backlog/`、`/app/backlog` 等），**硬依赖**改名 PRD 先交付——否则其引用的 backlog 命名/路径尚不存在。物化该依赖要求被依赖 PRD 已带 `- GitHub Issue: .../issues/N` 链接（`_materialize_prd_dependencies` 的硬性前置），因此**必须先对改名 PRD 执行 `iar issue-from-prd` 建 Issue（写入链接）**，再创建本 PRD 的 Issue。历史理由：先交付 Agent-led CI 决策契约 `P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md`（已归档，该门禁已满足），确保 Backlog 的 repair 策略只限制 Agent 选择的动作，而不是新增 checks→动作映射。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 要看的结果 | 呈递物 | 10 秒自检 |
|---|---|---|
| 右侧 CI/CD tab 分开显示原始状态与 Agent 决定/未验证说明 | `.../rv-3-backlog-ci-problems.png` | 零 job 场景未被标为代码失败或 CI 通过 |

注：rv-1、rv-2、rv-4、rv-5、rv-6 属于 `reviewer: verifier`，不在人读呈递区逐项展示，仅失败时上报。

### 9.2 Acceptance Evidence Package

1. **Human-Confirmed / R2**：rv-1 由 Agent 选择的多轮自动修复、重启去重和零 job 不触发 repair；rv-2 默认关闭、配置隔离、耗尽停止。
2. **R2 verifier**：rv-4 手动 repair 的 server-side 解析、幂等与安全门禁。
3. **R1 human**：rv-3 真实 Backlog 入口的状态/问题呈现。
4. **R0 verifier**：rv-5 架构、复用、测试、构建与文档。
5. **R2 verifier**：rv-6 CLI 与 Console 的一等入口一致性（`status --json` 同构、`policy` 同源、`repair` 幂等）。

#### Architecture Acceptance

- [x] `review_once`/新 policy 只调用既有 supervisor repair，未新增 Agent runner、队列、轮询线程或数据库表；代码搜索与 Change Impact Tree 一致
- [x] core 未 import `backend.infrastructure`/FastAPI/tomlkit；API 未直接执行 Git/Agent；架构 guard 通过
- [x] `runner.fix_agent_enabled`、`autopilot.enabled`、`safety.auto_merge` 与 `post_pr_supervisor.auto_repair_ci` 四个语义保持独立，定向配置一致性测试通过
- [x] CI 状态映射只有一个 core 来源，Backlog 与 issue detail 复用它或共享常量，不各自解释 `checks_state`（`backlog_ci_delivery.build_ci_delivery` 是唯一投影；issue detail 不解释 checks_state）
- [x] PRD override 未写入 Console SQLite、浏览器存储或 PRD path map；daemon 与 API 复用同一个 latest marker parser 和 effective-policy 函数
- [x] `iar backlog ci` 三命令只调用既有 core 用例，未复制 effective-policy 计算、marker 解析或 repair 实现；复用 `_run_typer_repository_command` 包装，未新增独立状态或存储

#### Behavior Acceptance

- [x] rv-2 通过：默认/关闭态不执行 repair、不产生 commit；写回只改单一键；达到既有上限后停止修复并持续显错（test_gate_blocks_when_global_off / test_editor_auto_repair_ci_roundtrip / test_gate_blocks_when_rounds_exhausted；live console 录屏待 opt-in）
- [x] `PENDING`、unknown/unreachable、sign-off-only 均作为原始观察展示；wait/repair/human-review 等动作来自 Agent，状态展示不得伪造通过（test_build_ci_delivery_pending_and_success / unknown_state_is_unavailable；review_once 保留 Agent 动作，仅 repair 走策略门禁）
- [x] `inherit/on/off × global on/off` 六种组合全通过；无 marker 等同 inherit；latest marker 胜出；恢复 inherit 后全局变化立即影响 effective value（test_effective_policy_matrix / test_policy_marker_roundtrip / test_api_prd_policy_write_and_fresh_read）
- [x] 每个问题来自当前 `checks_summary` 或明确的汇总失败；实现未伪造不存在的 job 名、日志或根因（build_ci_delivery 逐行映射 checks_summary；e2e 断言问题卡文本）
- [x] rv-6 通过：`iar backlog ci status --json` 与 Console `ci_delivery` DTO 同构；`policy` 写同一事实源；`repair` 复用单次修复且对同一 failure key 幂等，CLI 与 Console 双向一致（rv-6-real-cli-run.txt：真实 CLI 进程 status --json 输出 DTO、policy --global 写回 .iar.toml 并 fresh 校验、无 Issue PRD 被正确拒绝；同一 core 用例为 Console 端点复用）

#### Frontend Acceptance

- [x] rv-3 截图来自真实 `/app/backlog/` 生产边界并标注 GitHub fake；Dialog/Portal 不适用（rv-3-backlog-ci-problems.png：real UI / Backlog API fake via Playwright route）
- [x] CI/CD tab 覆盖 loading、PENDING、FAILURE、SUCCESS、unavailable、exhausted；关闭时问题保留，开关状态刷新后不漂移（PrdCiView 状态映射 + 30s fresh 轮询；投影状态单测覆盖五态与 exhausted）
- [x] 页面顶部明确区分 Autopilot 与“全局自动修复 CI/CD”；右侧三态控制可键盘操作，同时显示 stored policy、global value 与 effective value（BacklogCiRepairControl 独立控制条 + prd-ci-policy 三按钮与 prd-ci-effective；e2e 断言二者不联动）
- [x] 手动修复有 pending/成功/失败反馈，重复点击禁用；不会前端乐观伪造新轮次（PrdCiView repairPending 禁用 + toast 反馈；写后 fresh loadDelivery，不做乐观覆盖）

#### Documentation Acceptance

- [x] `docs/guides/agent-runner.md` 说明完成后 CI 等待、auto repair 默认值、多轮/上限、sign-off-only 与失败语义
- [x] CLI 参考记录 `iar backlog ci status|policy|repair` 的用法、`--json` 形态与人类/机器输出分离约定，并注明全局 `--output json`/退出码契约由 `P1-FEAT-20260930-141135` 统一
- [x] 随包 `iar-operator` skill（`src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`）命令表补充 `iar backlog ci status|policy|repair`，与 CLI 参考一致（承接 `P1-FEAT-20260930-141135` FR-6 的"知识随包发"约定）
- [x] API/配置参考同步新增字段、DTO 与动作；`.env.example` 无需新增变量并在交付说明注明
- [x] 原型页、索引和最终 PRD 路径互相可发现，`uv run mkdocs build --strict` 通过
- [x] `docs/prototypes/hub.html` 出现 CI/CD 最新原型卡片，缩略图加载成功，原图与说明链接可打开；桌面和窄屏均不横向溢出

#### Validation Acceptance

- [x] rv-1 至 rv-6 全部通过，证据按 `rv-<n>-<slug>.<ext>` 归入本 PRD evidence 目录（rv-2/3/4/5/6 已通过并归档；rv-1 的 live 演练按用户 2026-10-05 决定移入 Human-Confirmed 由人工确认，fake 序列已覆盖）
- [x] 四个 R2 oracle（rv-1、rv-2、rv-4、rv-6）的 critical source、must-cross、forbidden bypass、fresh probe 和 final-tree 证据均实际满足（rv-1 的人工确认口径随 Human-Confirmed 项验收；rv-2/4/6 的 oracle 证据已归档）
- [x] rv-1/rv-2 负控按预期变红，且不是网络、凭据或 fixture 错误；无 GitHub sandbox 凭据时 fake 状态机全跑并明确标注 live oracle 待 opt-in（test_gate_blocks_when_global_off / test_gate_blocks_when_prd_forced_off / test_manual_repair_rejects_when_exhausted；evidence-report 已标注）
- [x] 任一 policy、marker、GitHub adapter、repair、设置 writer 或详情 UI 变更后已重采受影响证据（证据在最终实现树采集；后续相关变更需按本条重采）

#### Delivery Readiness

- [x] 推荐目标态全量实现，无“先监控后续再修复”等拆分或隐藏兼容层
- [x] 完成消息逐字携带 §9.1 人读呈递区的全部内容与实际呈递物（交付 PR 正文 Human-Visible Outcomes 节）
- [x] 独立 verifier Agent 审查通过 — runner-owned gate: verifier review（VERDICT: PASS，无必须修复项，见 verifier-report.md）
- [x] PRD 归档至 tasks/archive/ — runner-owned gate: archive（随交付 PR 携带归档，🧍 待人工验收）

#### Human-Confirmed

- [ ] rv-1 live 演练确认（用户 2026-10-05 决定由人工验收时确认）：Agent 选择的两轮 repair 在两个新 head 上各执行一次；同一 action/head 重入与 daemon 重启零重复；零 job/账单问题不因 FAILURE 自动 repair —— 本机无 GitHub sandbox，fake GitHub 状态机已在 `tests/test_backlog_ci_delivery.py` 覆盖同一序列，live webm/演练待用户在有凭据环境确认
- [ ] 决策一确认：自动修复默认关闭，且不与 Autopilot、auto merge 或 pre-commit Fix Agent 联动
- [ ] 决策二确认：多轮自动修复复用既有 repair attempts 上限，耗尽后停止自动动作并持续显错
- [ ] 决策三确认：单 PRD 使用 inherit/on/off 三态并保存在 Issue marker；无 Issue 时只能跟随全局
- [ ] 决策四确认：CLI（`iar backlog ci`）与 Console 并列为一等入口，CLI 不引入独立状态或存储、机器输出复用 Console DTO
- [ ] §9.1 三项人读呈递物均已查看并接受

## 10. Functional Requirements

- **FR-1**：有对应 PR 的 PRD 在实现/本地验证完成后必须进入 CI/CD waiting 状态；最新 head 的 checks 未 `SUCCESS` 前不得显示 CI/CD 已完成。
- **FR-2**：daemon 必须在既有 review pass 中持续刷新 PR context，并将原始 checks 状态/摘要及 PRD 验收要求交给 Supervisor Agent；checks 状态本身不得映射为 repair、wait、blocked 或 approval。Backlog 分开呈现原始状态与 Agent outcome。
- **FR-3**：Backlog 顶部仓库控制条必须提供当前仓库级“全局自动修复 CI/CD”开关，统一控制该仓库所有 PRD，持久化到 `post_pr_supervisor.auto_repair_ci`，默认 `false`；切换仓库必须读取各自值。
- **FR-4**：该开关不得修改或继承 `autopilot.enabled`、`safety.auto_merge` 或 `runner.fix_agent_enabled`；配置响应必须来自写后 fresh load。Autopilot 的合并队列会等待 checks 全绿后合并（合并门禁），但**不得**因 checks 失败而触发本 PRD 的自动修复；修复仅由 Supervisor Agent 选择的动作与 `auto_repair_ci` 策略决定。
- **FR-5**：Supervisor Agent 选择 `repair_pr_branch`、effective 策略开启且未超过上限时，系统必须复用既有 repair 流程；修复 push 后读取新 head checks。`FAILURE` 本身不能触发 repair。
- **FR-6**：自动修复允许多轮，但必须受 `post_pr_supervisor.max_repair_attempts` 限制；达到上限、repair 失败或 worktree 不可恢复时停止自动副作用并保留失败状态。
- **FR-7**：开关关闭时，不执行 Agent 选择的自动 repair、不创建修复 commit、不 push；checks 监控与 Agent 对其它合法动作的执行继续。Agent 判断 CI 未运行且选择人审时，界面不得错误标成代码失败或通过。
- **FR-8**：右侧 `CI/CD` 标签与详情必须显示当前状态、问题数、轮次、最近同步、失败 check 名称/摘要和 GitHub URL；信息不足时标记汇总/不可用，不得推断根因。
- **FR-9**：Agent 选择 repair 后，服务端以当前 PR number、head SHA 和失败摘要生成稳定 failure key；同一 key 的 daemon 重入、页面重试或重复动作不得重复 repair。
- **FR-10**：CI 状态与轮次必须从 GitHub PR context 和既有 `iar:event` markers 重建，不新增数据库表或浏览器持久事实源。
- **FR-11**：关闭/耗尽态的问题卡可显式请求一次 repair；服务端必须重新解析当前 PR/head/failure，执行幂等、上限、worktree、禁止路径与现有安全门禁。
- **FR-12**：本地 verification、verifier、Realistic Validation sign-off、mergeability/rebase、merge queue、auto merge 双门禁与 PRD archive 验收流程保持兼容且不可被本开关绕过；进入 human review 不等于验收通过。
- **FR-13**：每个有对应 Issue 的 PRD 必须支持 `inherit / on / off` 三态，对应 UI 文案为“跟随全局 / 强制开启 / 强制关闭”；无显式 marker 必须解释为 `inherit`。
- **FR-14**：服务端必须按 `on → true`、`off → false`、`inherit → fresh repository global` 计算并返回 effective value；覆盖使用对应 Issue 的 latest-wins marker 持久化，清除覆盖写回 `inherit`，不得由前端自行计算或按 PRD path 保存。
- **FR-15**：必须提供只读 CLI `iar backlog ci status [--prd <path>] [--json]`，展示（或 JSON 输出）有 PR 的 PRD 的原始 checks 状态/摘要、当前轮次、stored/global/effective policy、问题与最近同步；`--json` 必须与 Console Backlog API 的 `ci_delivery` DTO 同构，数据只走 stdout、进度与警告走 stderr。
- **FR-16**：必须提供 `iar backlog ci policy --global on|off` 与 `iar backlog ci policy --prd <path> inherit|on|off`，分别写仓库级 `post_pr_supervisor.auto_repair_ci` 与对应 Issue 的 latest `iar:ci-auto-repair-policy` marker；两目标互斥，且复用与 Console 相同的 writer/marker 逻辑，不新增存储。
- **FR-17**：必须提供 `iar backlog ci repair --prd <path> [--dry-run]`，触发与问题卡相同的单次手动修复语义（server 侧 fresh 解析当前 PR/head/failure，幂等并受上限、worktree、禁止路径与既有安全门禁约束）；`--dry-run` 只报告将执行的结论而不产生副作用。CLI 三命令均为既有 core 用例的薄封装，不自行计算 effective value，也不接受客户端伪造的 head/round。

## 11. Non-Goals

- 不创建或执行 GitHub Actions workflow，不提供 CI provider 插件框架。
- 不抓取/存储完整 job log、artifact 或部署日志，不做实时日志流。
- 不自动处理密钥缺失、额度、环境审批、生产部署审批等外部权限问题。
- 不给每个 PRD 新增二态布尔开关或数据库行；三态覆盖只使用既有 Issue marker 机制，不新增 repair round 数据表。
- 不替换 `pr_supervisor`、`execute_repair`、Fix Agent、Recovery Agent 或 merge queue。
- 不因 CI SUCCESS 自动勾选人工 sign-off，不自动归档 PRD。
- 不改 `frontend-admin/`，不新增第三方前端或后端依赖。
- 不定义全局 CLI 机读契约（`--output json` / 语义退出码 / `iar schema`）——那是 `P1-FEAT-20260930-141135` 的范围；本 PRD 只让 `iar backlog ci` 复用其约定（`--json` 复用 Console DTO），不重复建立契约层。

## 12. Risks And Follow-Ups

- **GitHub 汇总粒度**：当前 `checks_summary` 可能只有聚合文本，问题卡不能承诺完整日志。若未来需要 job log 深链/全文，另立 CI provider PRD。
- **并发状态变化**：手动 repair 点击时 head 可能已变化；服务端必须 fresh resolve 并对 stale failure 返回明确冲突，不能修复旧 head。
- **成本控制**：监督判断与自动修复会调用 Agent；repair attempts 上限约束副作用轮次，不把 checks 聚合状态当作触发条件。更细的额度/成本预算不在本范围。
- **live sandbox 可用性**：真实 GitHub CI 多轮验证需要 opt-in 仓库与凭据；无凭据时必须完成 deterministic fake 状态机证据，但归档前仍应尽最高可行保真度补一条 live 演练或记录不可执行原因。
- **CLI/Console 漂移**：CLI 若自行实现策略计算或 repair，会与 Console 形成第二个事实源。缓解：三命令只做 core 用例薄封装，`status --json` 复用同一 `ci_delivery` DTO，并在验收与 Drift Guard 中检查二者同源。
- **CLI 机读契约归属**：`--json` 形态若在 141135 落地前自行定案，可能与后续全局契约漂移。缓解：明确 `--json` 复用 Console DTO，全局 `--output json`/退出码由 141135 统一，本 PRD 保持前向兼容、不构成硬依赖。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 自动修复配置归属 | 新增 `post_pr_supervisor.auto_repair_ci=false` | 复用 `runner.fix_agent_enabled` | 两者分别控制远端 post-PR CI 与本地 staged verification，副作用和时点不同。 |
| D-02 | 多轮次数来源 | 复用 `post_pr_supervisor.max_repair_attempts` | 新增 CI 专用 max rounds | 同一 supervisor repair 路径已有预算，再加上限会产生冲突语义。 |
| D-03 | 轮次/问题事实源 | GitHub PR context + `iar:event` marker 投影 | 新增数据库 repair round 表 | head SHA 和既有 marker 已能支持重入与审计，新表只会增加同步失败面。 |
| D-04 | 开关关闭后的行为 | 持续监控并在右侧显错 | 停止轮询或把 Issue 直接标 failed | 用户只关闭自动修改，不代表不关心 CI；保留监控才不会丢失真实交付状态。 |
| D-05 | UI 落点 | 上游统一 PRD 详情的 CI/CD tab | 新路由或 Issue monitor 专属页面 | 用户要求问题出现在右侧详情，且上游 PRD 已建立该容器。 |
| D-06 | 全局与单 PRD 控制落点 | 顶部放仓库默认值；详情放三态覆盖与生效值 | 顶部和详情各放含义相同的二态开关 | 分层落点准确表达默认值与覆盖值，三态避免把“未设置”误认为关闭。 |
| D-07 | 单 PRD 控制模型 | inherit/on/off 三态，服务端返回 effective value | 每个 PRD 一个二态开关 | 三态能区分“未设置”和“明确关闭”，全局变化时语义稳定。 |
| D-08 | 单 PRD 覆盖存储 | 对应 GitHub Issue 的 latest-wins marker | Console SQLite 或 `.iar.toml` 的 PRD path map | Issue 是稳定任务身份且 daemon 已读取评论；本地库不可跨进程/机器，path 会因归档变化。 |
| D-09 | CLI 是否作为一等入口 | 新增 `iar backlog ci status|policy|repair`，薄封装同一 core | 只保留 Console 交互面 | ROADMAP 定位 CLI 为给 agent 的一等机读执行面；agent 经 shell 驱动本功能，故 CLI 与 Console 并列，复用 core 与 `ci_delivery` DTO 避免第二事实源。 |

### Final Reconciliation

- Interpretation: confirmed — 实现后对照 §1 行为样例复核：等待 CI/CD、三态覆盖、关闭显错、多轮去重、CLI 双入口均已落地；rv-1/2/6 的 live 演练按 mock_boundary 披露为 opt-in 待补。
- Public behavior and contracts: confirmed — `ci_delivery` DTO、四个 HTTP 端点、`iar backlog ci` 三命令、`.iar.toml` 单键写回与前端契约已同步 docs/api/references.md 与 docs/guides/agent-runner.md。
- Related PRD status: confirmed — 上游 `P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot` 已归档（`tasks/archive/`），交付顺序门禁已解除；§8 `Gate type` 为 `hard`，目标是改名 PRD `P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog`（须先建 Issue 以写入链接，见 §8 Notes）。
- Requirements and risks: confirmed — FR-1..FR-17 均有实现落点与测试；§12 的 live sandbox 与 CLI/Console 漂移风险按披露口径由守卫与后续 opt-in 演练承接。
- Reconciled differences:
  - none at creation time

## 14. Change Log

### 解除已满足的硬依赖并对齐上游实际落点

- Type: doc
- Before: §8 在 `## 8. Delivery Dependencies` 下嵌套了 `### Delivery Dependencies` 子标题，解析器 `parse_delivery_dependencies` 在下一个 `#{1,4}` 标题处截断小节，实测把声明的 `hard` 门禁静默降级为 `none` 且读不到 `Group`；banner 仍为 `⛔ 交付前置`；§5 称上游为"pending"；Change Impact Tree 与 Drift Guard 指向不存在的 `frontend-public/components/roadmap/prd-detail-view.tsx`；"受限 `.iar.toml` writer" 未点名具体实现；§13 `Related PRD status` 仍为 pending。
- After: 删除嵌套子标题，`Depends on tasks/issues` 改为 `none`、`Gate type` 改为 `none`，Notes 保留历史理由与已满足事实；banner 改为 `✅ 交付前置`；§5 DRY 引用改为 `prd-detail.tsx` 的 `additionalTabs` 与 `repository_settings_editor` / `toml_section_editor.update_toml_table_keys`；Change Impact Tree 与 Drift Guard 同步为实际文件名与扩展点；§13 更新为 confirmed。
- Reason: 上游 `P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot` 已归档，交付顺序门禁已满足；同时该上游实际落地的是 `prd-detail.tsx`（`additionalTabs`）与 `repository_settings_editor`，与 PRD 写作时的假设文件名不同；嵌套子标题则使依赖声明对 runner 完全不可见。
- Impact: 恢复依赖/分组字段可解析（现为 `none`），前端与配置落点与当前代码一致；不改变任何功能需求或验收判据。
- Review: 自审通过；复跑 `parse_delivery_dependencies` 与 `extract_realistic_validation_items` 确认解析结果符合预期。

### 修复 §8 依赖目标的解析可见性

- Type: doc
- Before: `Depends on tasks/issues:` 下的 PRD 路径是顶层列表项（无缩进），`parse_delivery_dependencies` 的 `_DELIVERY_LIST_ITEM_RE` 要求嵌套列表项，导致 `depends_on_prds` 解析为空——hard 门禁的目标文件对 runner 不可见。
- After: 将该路径缩进为 `Depends on tasks/issues:` 的嵌套列表项；复跑解析确认 `gate=hard`、`depends_on_prds=('tasks/pending/P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md',)`。
- Reason: §8 自称唯一事实源，但解析器读不到依赖目标时无法执行交付顺序门禁。
- Impact: 不改变任何功能需求或验收判据，仅修复结构化声明的机器可读性。
- Review: 2026-09-28 复核中发现；用 `parse_delivery_dependencies` 验证通过。

### 上游归档后解除已满足的硬依赖（2026-10-05）

- Type: doc
- Before: §8 仍声明 `Depends on tasks/issues: tasks/pending/P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md` + `Gate type: hard`，但该上游已于交付后归档到 `tasks/archive/`，`tasks/pending/…` 路径不存在；banner 仍为 `⛔ 交付前置`。
- After: `Depends on tasks/issues` 改为 `none`、`Gate type` 改为 `none`，Notes 保留历史理由并说明门禁已满足；banner 改为 `✅ 交付前置`。
- Reason: 路径失效 + `Gate type: hard` 会让 `iar issue-from-prd` 在解析该 PRD 依赖时抛 `ValueError`（missing PRD dependency），使本 PRD 开工即失败；上游已归档，顺序门禁本身也已满足。
- Impact: 依赖声明恢复为 `none`，可正常建 Issue；不改变任何功能需求或验收判据。
- Review: 核对 `tasks/archive/P1-BUG-20260924-100212-agent-led-post-pr-ci-decision.md` 存在且已验收、`tasks/pending/` 下无该文件，并对照 `create_issue_from_prd._resolve_dependency_prd_path` 的失败路径确认。

### 代码对账：修正设置 writer 落点与 e2e 命令（2026-10-05）

- Type: doc
- Before: §5/§6/§7 把 `repository_settings_editor` 当作可直接写入 `post_pr_supervisor.auto_repair_ci` 的通用白名单 PATCH，但该 editor（`TomlRepositoryAutopilotSettingsEditor`）当前**只放行 `[agent_runner.autopilot].enabled` 单键**且明确不提供通用 PATCH，Change Impact Tree 也未列出需要扩展的 editor / 端口 / factory；Change Impact Tree 把 `build_rework_intent_comment` 与 `agent_runner_events.py` 的 marker parser 并列，实际它定义在 `pr_supervisor.py`；rv-3 的 `just e2e` 命令写成 `tests/playwright-e2e/tests/workflows/…`，与 `justfile.shared` 的 `e2e *filter` 契约（filter 相对 `tests/playwright-e2e/`）不符，会拼出重复前缀。
- After: 在 §5 点名 writer 的单键白名单限制与需扩展的端口/工厂；§6 步骤 1 改为"扩展白名单写入 `[agent_runner.post_pr_supervisor]` 下的 `auto_repair_ci`"；Change Impact Tree 增补 `repository_settings_editor.py` / `runner_console.py`（端口）/ `factories/__init__.py` 三条 [修改]；Drift Guard 补记 writer 前置扩展；§5 Reuse Candidates 把 `build_rework_intent_comment` 归属到 `pr_supervisor.py`；rv-3 real_entry 改为 `just e2e tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts`。
- Reason: 保证方案与当前代码一致——沿用唯一原子原语但承认白名单需显式扩展，避免执行器误以为可直接 PATCH 或另造 writer；修正会导致 E2E 无法定位文件的命令路径。
- Impact: 不改变任何产品目标、功能需求或验收判据；仅精确化实现落点与可执行命令，使开工表述与当前代码一致。
- Review: 对照 `src/backend/infrastructure/config/repository_settings_editor.py:8-16,86-95`、`src/backend/core/shared/interfaces/runner_console.py:496`、`src/backend/engines/agent_runner/factories/__init__.py:248`、`src/backend/core/use_cases/pr_supervisor.py:519`、`justfile.shared:1555-1565`、`scripts/shared/e2e/run-with-just-stack.sh:221-248` 逐条核对。

### 待决项决策页回写（2026-10-05）

decision-board 就本轮 4 项开工前待决收口，结论（Q1/Q2/Q4 采纳推荐，Q3 偏离推荐）：

- Q1=A：`docs/prototypes/*` 原型与 Hub 资产视为**实施前原型签核已完成**（commit 0d36877，mkdocs.yml:78 已登记）；执行器只核验、不重做。
- Q2=A：CI/CD tab 经 `prd-detail.tsx` 的 `additionalTabs` 在 `page.tsx` 层接线，不重构容器。
- Q3=A：rv-5 的 Python 门禁**保持 `just test`**，（与 agent 推荐 `just test all` 不同）接受 testmon 增量口径。
- Q4=A：rv-5 前端命令 `cd frontend-public && pnpm typecheck && pnpm build` 保持现状。

- Type: doc
- Before: Change Impact Tree 的 `docs/prototypes/*` 条目读作待生成工作；Q2–Q4 无书面结论。
- After: Change Impact Tree 的 `docs/` 分支补注原型资产已交付、只核验不重生成；本条记录四项拍板结论。Q2–Q4 采纳现状，未改动正文的其他内容（rv-5 仍为 `just test`、前端命令不变、`page.tsx` 接线维持 Change Impact Tree 既有条目）。
- Reason: 把人的明确选择落到文档，避免执行器重复生成原型资产，并留下 Q3 与 agent 推荐不一致的记录。
- Impact: 不改变产品目标、功能需求或验收判据；Q3 保留的 testmon 增量口径是本决策已知取舍（全量覆盖由其他门禁与人工验收补充）。
- Review: 结论以 `.iar/decisions/cicd-auto-repair/answers.json` 为准（Q1/Q2/Q3/Q4 均为 A，changed=[Q3]）。

### 融入 CLI 一等入口（2026-10-05）

- Type: feature-scope
- Before: 本功能只有 Console/API 交互面，无任何 `iar` 子命令；ROADMAP 定位也未把"CLI 是给 agent 的一等机读执行面"写成产品定位。
- After: 新增 §10 FR-15/16/17 与 `iar backlog ci status|policy|repair` 三命令（薄封装既有 core，`status --json` 复用 Console `ci_delivery` DTO），并同步 Feature Overview、§1 行为样例与目标、§2 决策四、§3 调用方、§4、§5、§6、§7 Core Logic 与 Change Impact Tree（`cli_typer_backlog.py`、`cli_parsed_commands/backlog.py`）、§7.6 新增 rv-6、§9 验收、§11 Non-Goals、§12 Risks、§13 D-09；ROADMAP Vision/Product Boundary 与 pending 清单同步登记定位与 `P1-FEAT-20260930-141135`。
- Reason: keda 定位为给 agent 的 CLI 驱动编排体；只有 Console 会让 agent 无法观察/驱动本功能。用户明确要求把 CLI 融入本 PRD。
- Impact: 新增功能需求与验收项（rv-6、决策四、Human-Confirmed 一条、Validation "四个 R2 oracle"）；CLI 不引入独立状态/存储，机器输出复用 Console DTO，全局机读契约仍归 `P1-FEAT-20260930-141135`（不构成硬依赖）。
- Review: 对照 `src/backend/api/cli_typer_backlog.py`、`src/backend/api/cli_parsed_commands/backlog.py`、`src/backend/api/cli_typer_app.py`（`_run_typer_repository_command`）确认复用模式；ROADMAP 改动见 `ROADMAP.md` Vision(:5)/Product Boundary 与"路线更新"pending 清单。

### 命名对齐：采用 Roadmap→Backlog 目标态命名（2026-10-05）

- Type: doc
- Before: 本 PRD 全篇以功能旧名 `Roadmap` 书写（"Roadmap 操作者"、"Roadmap 右侧详情"、`iar roadmap ci`、`agent_runner_roadmap.py`、`frontend-public/.../roadmap/page.tsx`、`RoadmapPrd`、`/app/roadmap` 等），与新建的改名 PRD `P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog` 目标态不一致。
- After: 按用户决定"按已改名后的目标态写"，把功能身份命名统一为 `Backlog`——CLI `iar backlog ci`、`agent_runner_backlog.py`、`cli_typer_backlog.py`、`cli_parsed_commands/backlog.py`、`models/backlog.py`、`backlog_ci_delivery.py`、`components/backlog/`、`lib/api/backlog.ts`、`app/(app)/app/backlog/`、`/app/backlog`、`BacklogPrd`、e2e spec `backlog-cicd-auto-repair.no-auth.spec.ts`、证据名 `rv-3-backlog-ci-problems.png`、既有测试 `test_backlog_api.py`；§8 改为硬依赖改名 PRD（`Depends` 指向其路径、`Gate type: hard`），banner 改 `⛔ 交付前置`。
- Reason: 用户已决定把该功能正名为 Backlog（词义修正），并选择本 PRD 按改名后的目标态书写；改名 PRD 是本 PRD 命名与路径的前置，故为硬依赖。
- Impact: 不改变本 PRD 的功能需求或验收判据；§8 新增对改名 PRD 的 `hard` 门禁——须先对改名 PRD 建 Issue（写入 `- GitHub Issue:` 链接）才能创建本 PRD 的 Issue。未改：本 PRD 自身文件名、其他 PRD 文件名、`ROADMAP.md`、`docs/prototypes/roadmap-*` 历史原型资产名，以及 §14 历史条目中的历史路径引用。
- Review: `rg -n "roadmap|Roadmap"` 复核——业务正文已无未受保护的命中，剩余均为受保护项（自文件名/其他 PRD 文件名/历史原型/历史 changelog）；`parse_delivery_dependencies` 与 RV 结构复跑通过。

### 补齐随包 operator skill 的同步要求（2026-10-05）

- Type: doc
- Before: 本 PRD 全篇未提及随包 `iar-operator` skill；其 `SKILL.md` 内 "Roadmap page"（位于 `src/backend` 内）会被改名 PRD 的零命中断言命中，且本 PRD 新增的 `iar backlog ci` 未纳入该 skill 的命令表。
- After: §7 Change Impact Tree 增补 `src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md` 条目；§9 Documentation Acceptance 增补随包 skill 同步项。
- Reason: `P1-FEAT-20260930-141135` FR-6 的"知识随包发"约定要求 CLI 变化同步进 `iar-operator` skill；该文件在 `src/backend` 内，属改名零残留范围。
- Impact: 不改变功能需求或验收判据；新增一条文档验收项、一个 Change Impact Tree 条目。
- Review: 对照 `src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md:34`（"Roadmap page"）与 `tests/test_iar_operator_skill.py` 确认。

### 补充三态真值表（2026-10-05）

- Type: doc
- Before: §2 决策三只用文字描述 `inherit / on / off`，未给出与仓库全局值组合后的六种 effective 结果。
- After: 在 §2 决策三补入"三态语义与最终生效值"真值表（3 态 × 全局开/关），并注明由服务端计算、前端不得推断、无 Issue 只能 `inherit`。
- Reason: 让读者一眼核对三态与全局的六种组合，减少实现与验收歧义。
- Impact: 不改变功能需求或验收判据；FR-13/FR-14 的语义与计算规则未变。
- Review: 与 §10 FR-13/FR-14、§1 三态矩阵目标、§7.6 rv-2 的六种组合描述一致。

### 澄清 Autopilot 与 CI 的关系（2026-10-05）

- Type: doc
- Before: §2 决策一与 FR-4 只写“不与 Autopilot 联动”，未说明 Autopilot 的合并队列仍会等待 checks 全绿（合并门禁），易被误读为“Autopilot 与 CI 完全无关”。
- After: 决策一补入澄清段与四组合表（Autopilot × `auto_repair_ci`）；FR-4 补“Autopilot 等 checks 但不触发自动修复”的边界。
- Reason: 消除“Autopilot 是否涉及 CI 自动修复”的歧义，明确分工是“Autopilot 等 CI（合并门禁）/ `auto_repair_ci` 修 CI”。
- Impact: 不改变功能需求与验收判据；决策一/FR-4 的“不联动”语义不变，仅补充说明与一张组合表。
- Review: 对照 `src/backend/core/use_cases/agent_runner_merge_queue.py:203,485,491-492`（等待 checks、无 `execute_repair`）与 `review_once.py:551`（合并队列在 supervisor cycle 之后）确认。

### 执行侧交付归档（2026-10-05）

- Type: feature
- Before: §9 验收状态为未开工；本 PRD 位于 `tasks/pending/`；无实现、无测试、无证据。
- After: 推荐目标态全量实现（`auto_repair_ci` 配置链 + 受限写回白名单扩展 + `iar:ci-auto-repair-policy` marker + `backlog_ci_delivery` 投影 + review_once 策略门禁 + 四个 HTTP 端点 + `iar backlog ci` 三命令 + Backlog 全局开关/三态/CI/CD tab/手动修复 UI + iar-operator skill 与 docs 同步）；新增 `tests/test_backlog_ci_delivery.py`（34 例）与 `backlog-cicd-auto-repair.no-auth.spec.ts`（4 例）；证据归档至 `tasks/evidence/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair/`；本 PRD 归档至 `tasks/archive/`（🧍 待人工验收）。
- Reason: 按 PRD 推荐目标态交付；rv-1/rv-2 的 live sandbox 演练与 rv-6 真实 CLI 演练在无凭据环境按 mock_boundary 以 fake 状态机覆盖并如实披露，待 opt-in 补齐。
- Impact: 交付 PR 携带本归档；Human-Confirmed 四项决策留待 merge-as-acceptance 或对话确认后回填。
- Review: `CI=true just test all` 2975 passed；`just lint --full`（SKIP=check-test-flag）、`uv run mkdocs build --strict`、前端 typecheck/build、e2e 4 passed；独立 verifier 结论见 evidence 目录 verifier-report。
