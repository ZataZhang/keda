# Verification Plan — Agent 与预设执行表现统计

PRD: [`tasks/archive/P2-FEAT-20261009-171037-agent-preset-performance-stats.md`](../../archive/P2-FEAT-20261009-171037-agent-preset-performance-stats.md)

## rv-1 — API 与 SQLite 聚合

- Entry: `uv run pytest tests/test_console_stats.py tests/test_agent_runner_console_api.py -k agent_performance -q`
- Boundary: 真实 FastAPI 测试路由、core 聚合用例、ConsoleStore SQLite 查询；测试独立写入隔离数据库，再以新连接 fresh-read，并通过新 API 请求读取聚合结果。
- Check: 仓库和时间过滤、成功 / 非成功次数与比例、失败分类、线性 P50/P90、已删除 preset 快照、空 preset、未知 failure type、同名跨仓库分组和独立 run outcome。
- Expected: API 字段与独立查询到的 SQLite 原始样本一致；不读取当前配置或 `attempt_records.detail`。

## rv-2 — Stats 页面与静态控制台

- Entry: `just e2e tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts`
- Setup: `just console-sync` 后启动 `kc console`，经真实 `/app/stats` 页面验证；只替代新统计 API 响应。
- Check: Agent / preset / model 表，成功率、失败类型、耗时，独立 run outcome，仓库 / 天数筛选，空态、错误态以及 375px 窄屏横向滚动。
- Expected: 页面呈现与确定性响应相符，新增统计 API 错误不影响其他 Stats 区块。
- Negative control: 将 fixture 的 preset `attempt_count` 从 4 改为 5 并保留断言，确认样本数断言失败，再恢复 fixture。
- Evidence: 真实 `kc console` 页面 PNG、窄屏检查和 Playwright 结果；只有通过真实页面入口取得的画面才可作为呈递截图。

## Regression checks

- `pnpm typecheck` and `pnpm build` in `frontend-public/`
- `uv run mkdocs build --strict`
- Existing Stats routes and other dashboard sections remain unchanged.
