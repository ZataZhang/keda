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
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q` | **53 passed**（整改后，含 PRD 覆盖端到端用例） |
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

## 3. 证据强度与已知限制（诚实披露）

1. **rv-3 / rv-4 / rv-6 / rv-7 的"真实浏览器截图呈递"未执行**：本报告只以后端 TestClient +
   磁盘文件内容为事实源验证了写回语义与文件隔离；PRD 要求的 `tasks/evidence/<stem>/rv-*.png`
   呈递物**不存在**，§9.1 呈递区四项**未经人过目**。
2. **Playwright e2e 未运行**：新增 `data-testid` 已就位，但 `tests/playwright-e2e`
   未安装依赖、未编写/执行 `--grep lifecycle-agent` 用例；前端界面仅经 typecheck + lint + 静态构建验证，
   验证层级为 **component/static build**，**不是** production composition / real user flow。
3. **§9.2 的 Human-Confirmed 三项（决策一/二/四/五）未经人确认**。
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

- 代码、单测、文档、配置模板齐备；`just test` 全绿；独立 verifier round 2 判 **PASS with caveats**。
- **仍未达到 PRD §9 的完整验收**：缺人工确认（§9.2 Human-Confirmed）、真实浏览器呈递截图
  （rv-3/4/6/7 的 `.png`）、Playwright e2e；这些不由执行者自行判过。
- 因此本 PRD **不归档**（保留在 `tasks/pending/`），仅提交实施代码、文档与证据。
