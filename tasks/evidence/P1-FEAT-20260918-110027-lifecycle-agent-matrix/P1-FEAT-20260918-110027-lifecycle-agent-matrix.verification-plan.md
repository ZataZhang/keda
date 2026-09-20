# Verification Plan — P1-FEAT-20260918-110027 生命周期 Agent 矩阵

- PRD: `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md`
- 实施分支: `lifecycle-agent-matrix`（worktree `/Users/zata/code/keda-worktrees/lifecycle-agent-matrix`）
- 本文件是执行者的自我验证计划；独立 verifier 的结论见 `<stem>.verifier-report.md`。

## 验证层级与范围

| 层 | 覆盖对象 | 入口 |
|---|---|---|
| unit / integration（pytest） | 解析优先级链、九键零配置基线、`auto` 逐阶段语义、fail-fast、fix/closeout 路由、console API 契约与保留式写回 | `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q` |
| 全仓回归 | 既有行为不被破坏 | `just test`（lint + 2319 项 pytest） |
| 文档构建 | 新增文档页与导航 | `uv run mkdocs build --strict` |
| 前端静态检查 | 类型与 lint | `cd frontend-public && pnpm typecheck && pnpm lint` |

**未覆盖（本次不声称完成）**：rv-3 / rv-4 / rv-6 / rv-7 要求的真实浏览器截图呈递、
Playwright e2e（`pnpm test --grep lifecycle-agent`）、§9.2 的 Human-Confirmed 决策确认、
独立 verifier 之外的发布链路验证。

## 映射到 PRD 的 Realistic Validation Plan

- **rv-1**（优先级链 + 零配置基线 + 逐阶段 `auto`）：
  `uv run pytest tests/test_lifecycle_agent_resolution.py -q`
  - 关键用例：`test_zero_config_baseline_matches_pre_matrix_behavior`、
    `test_repository_layer_wins_over_global_layer`、`test_prd_override_wins_over_matrix`、
    `test_auto_semantics_per_stage_are_preserved`。
  - 值来源：tmp `config.toml` / 真实 git 仓库 `.iar.toml` 文本 → pydantic settings →
    `AppConfig` → `resolve_lifecycle_agent`，不直接构造解析结果。
- **rv-2**（fix/closeout 真换人 + 默认跟随对照）：
  `uv run pytest tests/test_lifecycle_agent_routing.py -q`
  - `test_fix_agent_defaults_to_selected_agent` / `test_fix_agent_uses_matrix_value`
    / `test_closeout_agent_uses_matrix_value` 捕获执行循环传给
    `run_fix_agent` / `run_closeout_agent` 的 `agent_name`。
- **rv-5**（未注册 agent fail-fast）：
  `uv run pytest tests/test_lifecycle_agent_resolution.py -q -k unregistered`
- **rv-6**（回退顺序写 runner 段且既有键不变）：
  `uv run pytest tests/test_lifecycle_agents_console_api.py -k fallback_order -q`
- **rv-7**（标签写注册块 + 重复拒绝）：
  `uv run pytest tests/test_lifecycle_agents_console_api.py -k agent_labels -q`
- **rv-3**（三层各写各自文件）：由 `tests/test_lifecycle_agents_console_api.py`
  的 `test_global_write_only_touches_config_toml` /
  `test_repository_write_only_touches_iar_toml` /
  `test_repository_restore_key_falls_back_to_global` 以后端 + 文件内容为事实源覆盖；
  **真实浏览器截图呈递未执行**。
- **rv-4**（PRD 覆盖写回文件头部）：`test_prd_override_read_write_roundtrip`。

## 反空转与防漏

- `test_fix_and_closeout_call_sites_resolve_through_lifecycle_function`（AST 守卫）
  逐个检查 `run_fix_agent` / `run_closeout_agent` 的首参必须来自
  `resolve_lifecycle_agent`，并带"找不到调用点即失败"的反空转断言。
- 解析单测全部经真实 TOML 文本与 settings 合并，未 fake 解析函数。

## 未验证项的处理

上述"未覆盖"项在 `<stem>.evidence-report.md` 中显式列出，不以组件级或内存态证据冒充
真实验证；对应 §9.2 清单条目不勾选。
