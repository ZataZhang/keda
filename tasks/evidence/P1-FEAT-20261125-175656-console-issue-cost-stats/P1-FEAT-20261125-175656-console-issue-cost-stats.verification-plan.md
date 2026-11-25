# 验证计划与执行状态

PRD：[P1-FEAT-20261125-175656-console-issue-cost-stats.md](../../pending/P1-FEAT-20261125-175656-console-issue-cost-stats.md)
Issue：[GitHub #449](https://github.com/ZataZhang/keda/issues/449)

## Oracle 状态

| Oracle | 风险 | 计划入口 | 执行状态 | 证据 |
|---|---:|---|---|---|
| RV-1 | R2 | uv run pytest tests/test_agent_runner_console_api.py::test_issue_cost_stats_reads_sqlite_and_console_http_with_repo_issue_key -q | SQLite + Console HTTP 账本归属断言通过；完整 oracle 尚未完成，因为美元显示依赖 RV-2/RV-3 的真实页面验证。 | 本地原始输出 rv-1-cost-api.txt；测试结果 1 passed。 |
| RV-2 | R1 | just e2e tests/smoke/stats-issue-cost-ledger.no-auth.spec.ts | REVIEW_INCIDENT / INCONCLUSIVE。Next dev 遇到 EMFILE；生产静态导出和真实 /app/stats 路由 HTTP 探测成功，但 Chromium 在测试体前因 macOS bootstrap 权限错误退出。 | 生产截图未生成；诊断见本地原始文件 rv-2-browser-launch.txt。 |
| RV-3 | R1 | just e2e tests/smoke/stats-issue-cost-ledger.no-auth.spec.ts | REVIEW_INCIDENT / INCONCLUSIVE。浏览器启动失败，日期筛选、趋势与覆盖率断言未执行。 | rv-3-trend-and-coverage-trend.png 和 rv-3-trend-and-coverage-pricing.png 未生成；共用 rv-2-browser-launch.txt 诊断。 |

## 交付级验证

- SKIP=check-test-flag UV_CACHE_DIR=/private/tmp/uv-cache-issue-449 just lint --full：通过。
- UV_CACHE_DIR=/private/tmp/uv-cache-issue-449 just lint --reuse：通过。
- UV_CACHE_DIR=/private/tmp/uv-cache-issue-449 just test：完整执行 5689 passed, 3 skipped, 1 deselected in 470.89s。随后仅前端改动后的调用没有选中 Python 测试（no tests ran in 0.06s）；Python 源码未再变化。
- 后端目标集成：1 passed；临时 SQLite store 和 FastAPI Console HTTP 路由均为真实实现，只替换 GitHub 网络边界。
- frontend-public 的 pnpm run lint && pnpm run typecheck：退出码 0；lint 为 0 errors、105 warnings，typecheck 通过。
- frontend-public 的 pnpm run build：通过，静态路由表含 /app/stats。
- E2E 新 spec 定向 ESLint：通过。E2E 包级 lint/typecheck 仍报告未改动旧文件中的既有问题，详见 evidence report。
- UV_CACHE_DIR=/private/tmp/uv-cache-issue-449 uv run mkdocs build --strict：通过；仅有仓库已有的未纳入导航页提示。

## 验收边界

RV-1 的数据库和 HTTP 归属证据不证明浏览器 USD 呈现。RV-2/RV-3 尚无真实页面交互或截图证据；生产静态路由 HTTP 200 只证明导出页面可提供。独立 verifier 尚未运行，由 runner 在浏览器 incident 解决后处理。
