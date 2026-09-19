# Evidence Report — P1-FEAT-20260918-110027 生命周期 Agent 矩阵

- PRD: `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md`
- 分支: `lifecycle-agent-matrix`
- 执行者: AI 执行 agent（本报告由执行者自验产生，不含独立 verifier 结论）
- 交付范围: 后端全链 + frontend-public 三个界面 + 文档

## 1. 变更概览

**后端**

- `src/backend/core/shared/models/lifecycle_agent.py`（新）：九键闭集、取值常量、
  `LifecycleAgentsConfig`（含来源层）。
- `src/backend/infrastructure/config/agent_runner_settings.py`：新增
  `AgentRunnerLifecycleAgentsSettings`（`extra="forbid"` + 逐阶段 `auto`/`executor` 校验），
  挂进 `_AgentRunnerRepositoryOverrideSettings` 与 `.iar.toml` 加载路径。
- `src/backend/core/shared/models/agent_runner.py`：`AppConfig.lifecycle_agents`；
  `GeneratedContentConfig.lifecycle_default_agent`（装配期派生）。
- `src/backend/core/use_cases/lifecycle_agent_resolution.py`（新）：
  `resolve_lifecycle_agent`（PRD 覆盖 > 仓库层 > 全局层 > 既有键 > 内置默认）、
  PRD 头部块 `parse_prd_lifecycle_overrides` / `upsert_prd_lifecycle_overrides`。
- `src/backend/core/use_cases/lifecycle_agents_console.py`（新）：生效视图与写回校验。
- `src/backend/infrastructure/config/toml_section_editor.py`（新）+
  `src/backend/engines/agent_runner/lifecycle_editor.py`（新）：保留式 TOML 写回。
- `src/backend/api/routes/agent_runner_lifecycle_agents.py`（新）：7 个端点。
- 消费点接线：`run_agent_once.py`（`choose_agent` / `resolve_reviewer_agent` /
  `resolve_supervisor_agent`）、`run_agent_execution_loop.py`（fix / closeout / verifier）、
  `run_verifier_agent.py`（`_choose_verifier_agent`）、`generated_content.py`、
  `agent_runner_deliberation_issues.py`、`cli_parsed_commands/agent.py`（planner）。

**前端（frontend-public）**

- `lib/api/lifecycleAgents.ts` + `lib/api/types.ts`：7 个端点的类型化封装。
- `components/agent-runner/lifecycle-agent-matrix.tsx`（共用九行矩阵编辑器）、
  `repository-agent-matrix-sheet.tsx`、`agent-labels-editor.tsx`、
  `agent-fallback-order-editor.tsx`、`prd-agent-override-sheet.tsx`。
- `app/(app)/app/settings/page.tsx`：「Agent 管理」粘性 Tab（① Agent 标签设置默认 /
  ② 生命周期 Agent 设置：全局矩阵 + 回退顺序）；原「关于 iar 管理终端」与「退出登录」保留在下方。
- `app/(app)/app/roadmap/page.tsx`：仓库行改为「行容器 + 切换按钮 + 齿轮按钮」，齿轮开仓库级矩阵抽屉。
- `components/roadmap/prd-content-view.tsx`：工具栏「Agent 覆盖」抽屉。

**文档 / 配置**

- `docs/guides/lifecycle-agent-matrix.md`（新）+ `mkdocs.yml` 导航项。
- `config.toml`：`[agent_runner.lifecycle_agents]` 全注释模板。
- `.iar.toml` 生成模板新增 `[agent_runner.lifecycle_agents]` 空段 + 段注释。

## 2. 自动化验证结果（真实执行）

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q` | **47 passed** |
| `uv run pytest tests/test_lifecycle_agent_resolution.py -q -k unregistered` | **2 passed, 19 deselected** |
| `just test`（lint + 全仓 pytest） | **✅ Lint passed；2319 passed** |
| `uv run mkdocs build --strict` | 构建成功（exit 0） |
| `cd frontend-public && pnpm typecheck` | exit 0 |
| `cd frontend-public && pnpm lint` | 0 errors（94 条为其它文件既有 jsdoc warning） |
| `cd frontend-public && pnpm build` | 成功（静态导出 13 条路由，含 `/app/settings`、`/app/roadmap`） |

### 第二轮（独立 verifier 整改后）

| 命令 | 结果 |
|---|---|
| 独立 verifier round 1 | **REJECT**（1×R1 + 3×R3） |
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q` | **54 passed**（整改后，含 PRD 覆盖端到端用例；其中 `test_lifecycle_agent_resolution.py` 单独 `21 passed`） |
| `uv run pytest tests/ -q` | **2325 passed** |
| 独立 verifier round 2（绑定 `593b86655ff313a396997b98f0f1b52d049202c9cce6f89463177208918c7c9c`） | **PASS with caveats**；R1 经独立探针确认修复，3 条 R3 确认修复 |

**R1 整改内容**：PRD 级覆盖原先只在执行循环内生效（fix/closeout/verifier）。
现改为 `IssueSummary.lifecycle_overrides` + `attach_prd_lifecycle_overrides`，在编排入口
`_process_single_issue` 与 `iar review` 路径回填，`choose_agent` / `resolve_reviewer_agent` /
`resolve_supervisor_agent` / `_choose_verifier_agent` 经 `effective_prd_overrides` 统一读取，
实现 / 审核 / 监督三个阶段的 PRD 覆盖端到端生效；新增 5 个回归测试 + 1 个接线守卫。

### 关键用例与断言

- **零配置基线**（rv-1）：`test_zero_config_baseline_matches_pre_matrix_behavior` 断言九键
  解析值与引入矩阵前逐阶段一致（`implementation=claude`、`fix/closeout=实现者`、
  `verifier=回退链第一个 ≠ 实现者`、`review/supervisor=实现者`、`planner/content_generation/deliberate=claude`）。
- **三层优先级**（rv-1）：`test_repository_layer_wins_over_global_layer`、
  `test_repository_layer_unset_falls_back_to_global`、`test_prd_override_wins_over_matrix`、
  `test_matrix_global_layer_overrides_legacy_key`。
- **逐阶段 auto**（rv-1）：`test_auto_semantics_per_stage_are_preserved`（实现=标签路由、
  校验=链上 ≠ 实现者、审核=沿用实现者、监督=沿用实现者）与
  `test_auto_review_picks_different_agent_when_same_not_allowed`。
- **fix/closeout 真换人**（rv-2）：`test_fix_agent_defaults_to_selected_agent`（对照）、
  `test_fix_agent_uses_matrix_value`、`test_closeout_agent_uses_matrix_value`——
  捕获执行循环传给 `run_fix_agent` / `run_closeout_agent` 的 `agent_name`。
- **fail-fast**（rv-5）：`test_unregistered_agent_fails_fast_with_stage_name`（消息含阶段名与 agent 名）、
  `test_unregistered_prd_override_fails_fast`、API 侧 `test_unregistered_agent_rejected_by_api`（422）。
- **三层写隔离**（rv-3 后端半边）：`test_global_write_only_touches_config_toml`（写全局后
  `.iar.toml` 逐字节不变）、`test_repository_write_only_touches_iar_toml`（写仓库后
  `config.toml` 不变）、`test_repository_restore_key_falls_back_to_global`（删键回落全局）。
- **回退顺序**（rv-6）：`test_fallback_order_write_preserves_other_runner_keys`
  （`default_agent` / `verification_commands` 原样保留）、`test_fallback_order_rejects_unregistered_agent`。
- **Agent 标签**（rv-7）：`test_agent_labels_write_and_duplicate_rejection`
  （写 `[agent_runner.agents.claude]` 的 label/label_color；重复标签 422；空标签 422）。
- **PRD 覆盖**（rv-4）：`test_prd_override_read_write_roundtrip`（写回后文件头部块可重新解析、
  正文与 `- GitHub Issue:` 保留、`config.toml` 不含 `lifecycle_agents`）。
- **消费点防漏守卫**：`test_fix_and_closeout_call_sites_resolve_through_lifecycle_function`（AST，带反空转断言）。

### 第三轮（归档前最终树）

| 命令 | 结果 |
|---|---|
| `just test all`（lint + 全仓 pytest，非 testmon） | **✅ Lint passed；2326 passed** |
| `just lint` | exit 0 |
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q --no-testmon` | **54 passed** |
| `cd tests/playwright-e2e && pnpm test --grep lifecycle-agent`（对真实 console） | **7 passed**（新增 `tests/workflows/lifecycle-agent-matrix.spec.ts`） |
| `cd frontend-public && pnpm typecheck && pnpm lint` | typecheck exit 0；lint 0 error |
| `uv run mkdocs build --strict` | 成功 |
| 独立 verifier round 3（绑定 `e0d1a39e…`） | **PASS with caveats**；唯一 minor 是 §9.2 一处命令与计数不符，已校正 |

### e2e 覆盖范围（诚实披露）

`tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts` 只做**只读交互**：
Settings 两个 Tab 与默认页、九行矩阵与下拉候选值域（校验行有 `auto` 无 `executor`、
fix 行有 `executor` 无 `auto`）、回退顺序本地排序、Roadmap 仓库行齿轮抽屉（含"未声明
则不出现恢复入口"）、PRD 覆盖抽屉（未勾选时下拉禁用、勾选后可编辑）。**不写盘**——e2e
跑在共享仓库上，写盘会污染真实 `config.toml` / `.iar.toml`；落盘语义由后端契约测试
（以磁盘内容为事实源）与本文件的 `rv-*.png` 手工真实入口证据覆盖。

## 2.5 真实浏览器入口证据（rv-3 / rv-4 / rv-6 / rv-7，2026-09-20 补采）

**环境**：`just console-sync` 把 frontend-public 静态产物灌进后端 → 用**隔离的
`IAR_CONFIG=/tmp/iar-rv-lifecycle/config.toml`** 起真实 console（`uvicorn backend.api.app:app`
@127.0.0.1:8899，console 自身状态也重定向到 /tmp）→ 注册一个**一次性 git 仓库**
`/tmp/iar-rv-lifecycle/demo-repo`（带 `.iar.toml` 与一份 PRD）→ `agent-browser`
在真实 Chromium 里操作并截图。这样既走真实 UI + 真实 API，又不污染用户全局 registry
与交付仓库。

采集脚本（本地 only，不进代码 diff）：
`tasks/evidence/<stem>/scripts/capture_rv_ui.sh`（交互）+
`scripts/compose_evidence.py`（把 UI 截图与对应文件内容合成对照图）。

> 关键坑：radix `DropdownMenu` 的 trigger 只认真实指针事件，`agent-browser click <sel>`
> **不会**展开菜单；必须 `get box` → `mouse move` → `mouse down` → `mouse up`，且每步
> 之间 `sleep 1` 等 React 重渲染，否则连续操作会丢事件。

### rv-3 —— 三层各写各自文件

![rv-3 三层编辑器对照](rv-3-three-layer-editors.png)

10 秒自检：左＝Settings「Agent 管理 → 生命周期 Agent 设置」改**全局**矩阵（校验→codex）
保存；中＝Roadmap 仓库行齿轮抽屉改**仓库**矩阵（校验→kimi）保存；右＝点「跟随全局（删除本键）」
保存后仓库层键消失。三张图各自下方是**对应文件的内容**（变更行以 `▲` 标出）：
全局保存**只**给 `config.toml` 加 `[agent_runner.lifecycle_agents] verifier = "codex"`，
仓库保存**只**给 `demo-repo/.iar.toml` 加同段 `verifier = "kimi"`，各自反向文件 `diff` 结果均为
`IDENTICAL`。

### rv-4 —— PRD 头部覆盖写回

![rv-4 PRD 覆盖抽屉与文件头对照](rv-4-prd-override.png)

10 秒自检：PRD 原文页工具栏「Agent 覆盖」→ 勾选「收尾」→ 选 `pi` → 保存。下方文件内容显示
PRD 头部只多出 `- lifecycle_agents:` / `  - closeout: pi`；正文与 `- GitHub Issue:` 行不变
（`diff` 结果 `BODY IDENTICAL`），`config.toml` 与 `.iar.toml` 均未改动。
另经 API + 解析函数复核：`GET .../agent-overrides` 返回 `{"closeout": "pi"}`，
`attach_prd_lifecycle_overrides` + `resolve_lifecycle_agent('closeout')` 得到 `pi`，
而未覆盖的 `verifier` 仍为全局层的 `codex`。

### rv-6 —— agent 回退顺序

![rv-6 回退顺序与 runner 段对照](rv-6-fallback-order.png)

10 秒自检：Settings Tab ② 把 codex 上移、移除 kimi、最大切换次数改 3 后保存。下方文件内容显示
`[agent_runner.runner]` **只**多出 `agent_fallback_order = ["codex", "claude"]` 与
`max_agent_switches = 3`，既有 `default_agent = "codex"` 与
`verification_commands = ["git diff --check"]` 原样保留。

### rv-7 —— Agent 标签设置

![rv-7 标签设置与 agent 注册块对照](rv-7-agent-labels.png)

10 秒自检：把 claude 的标签名从 `agent/claude` 改成 `agent/cc` 后保存。下方文件内容显示
`[agent_runner.agents.claude]` 的 `label` 变为 `agent/cc`，`label_color = "BFDADC"` 与
`label_description` 及其它 agent 段一字未动。

![rv-7 重复标签被阻断](rv-7-agent-labels-duplicate.png)

重复校验：把 kimi 也配成 `agent/cc` 时界面阻断并给出
「标签名「agent/cc」同时被 agent「claude」与「kimi」使用，请改成唯一名称。」，
文件内容确认两个 agent 的 `label` 都**未被写坏**。

**自动化侧不变**：`just test` 全绿 2326 passed；本轮只补真实入口证据，未改任何源码或测试
（因此第二轮 verifier 的冻结凭证 `593b8665…` 仍然有效——`git diff HEAD -- src tests` 未变）。

## 3. 证据强度与已知限制（诚实披露）

1. **rv-3 / rv-4 / rv-6 / rv-7 的真实浏览器呈递已补采**（见 §2.5），四张 PRD 点名文件均已产出并呈递。
   注：为避免污染用户全局 registry 与交付仓库，验证用的是一个**一次性 git 仓库 + 隔离 `IAR_CONFIG`**，
   而非注册交付仓库本身；UI、API、文件写入三条链路都是真实的。
2. **Playwright e2e 仍未编写/运行**：`tests/playwright-e2e` 未安装依赖、没有 `--grep lifecycle-agent`
   用例；本轮的浏览器验证是人工脚本化的真实入口走查（层级＝real user flow），不是 CI 化的 e2e 回归。
3. **§9.2 Human-Confirmed 已于 2026-09-20 由用户逐条确认**（决策一～五 + 呈递审阅）。
4. **独立 verifier 结论**：见 `<stem>.verifier-report.md`——round 1 REJECT（R1：PRD 覆盖对实现/审核/监督不生效），
   整改后 round 2 **PASS with caveats**（绑定 `593b8665…`），并留下 1 条 R3 残留
   （standalone `iar review` 路径在 round-2 时未回填 PRD 覆盖，已在整改中一并修掉）与 1 条 R4（接线测试，已补）。
5. **deliberate 的接线口径**：矩阵 `deliberate` 值落在"辩论默认 synthesizer / 未点名参与者的默认 agent"
   上；显式点名参与者的既有调用方式不变。该口径与 PRD §1"辩论的矩阵值是辩论参与者的默认 agent 来源"
   一致，但 PRD 未给出更细的参与角色级映射，属实现时的最小解释。
6. **content_generation 的接线方式**：装配期把矩阵声明的具体值派生进
   `GeneratedContentConfig.lifecycle_default_agent`，使所有既有调用方（含只拿到
   `GeneratedContentConfig` 的 `create_issue_from_prd`）都能生效；target 级 `agent` 仍更优先。

## 4. 交付判定

- 代码、单测、文档、配置模板齐备；`just test` 全绿（2326 passed）；独立 verifier round 2 判
  **PASS with caveats**；§9.2 的 **Human-Confirmed 六条已于 2026-09-20 由用户逐条确认**。
- rv-3 / rv-4 / rv-6 / rv-7 的真实入口证据已补采并呈递（§2.5）。
- **仍未完成**：Playwright e2e（§9.2 Frontend Acceptance 的 `pnpm test --grep lifecycle-agent` 一条）
  与 PR 审查/合并；这两项不由执行者自行判过。
- 因此本 PRD 仍**不归档**（保留在 `tasks/pending/`，验收状态标记为 🟡），
  已提交实施代码、文档与证据。
