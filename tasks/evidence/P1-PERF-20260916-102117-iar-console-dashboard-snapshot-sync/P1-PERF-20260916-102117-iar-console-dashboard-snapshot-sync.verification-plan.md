# 验证计划 · iar console dashboard 本地快照缓存与可配置后台定时同步

PRD：`tasks/pending/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync.md`
分支：`perf/iar-console-dashboard-snapshot-sync`（worktree `../keda-worktrees/perf/iar-console-dashboard-snapshot-sync`）
执行时间：2026-09-16 / 2026-09-17

## 验收项 → 可执行验证映射

| 验收项 | 验证方式 | 命令 / 入口 | 证据文件 |
|---|---|---|---|
| rv-1 v3 旧库迁移到 v4 且旧数据保留（决策一） | integration：v3 种子库（手工 CREATE + 3 条 run_records + `user_version=3`）经 `SqliteConsoleStore` 打开后用独立连接复核 | `uv run pytest tests/test_console_store.py -k "monitor or migrate" -v` | `rv-1-migration.txt` |
| rv-1 negative control | 同一探针脚本在实现前代码树（`git archive main`）上运行 | `PYTHONPATH=/tmp/keda-rv1-baseline/src /usr/bin/python3 scripts/rv1_negative_control.py /tmp/keda-rv1-baseline` | `rv-1-negative-control.txt` |
| rv-2 真实入口首屏秒开 + 手动刷新不阻塞（FR-1/2/5/8） | real-entry：`IAR_CONFIG=/tmp/iar-rv-monitor/config.toml uv run iar console --port 8313 --no-browser` + Chromium 录屏（首面/刷新中/刷新后三段） | `node scripts/rv2_dashboard_capture.cjs`（Playwright，`NODE_PATH=<e2e>/node_modules`） | `rv-2-dashboard-refresh.webm`、`rv-2-dashboard-timeline.txt` |
| rv-2 快照接口本地响应时延 | real-entry：对运行中的 console 连打 5 次快照接口 | `curl -w '%{time_total}' .../overview/snapshots` | `rv-2-snapshot-latency.txt` |
| rv-3 设置即时生效 / 周期一致 / 关闭后零自动同步 / 手动刷新仍可用（FR-3/4） | real-entry：界面改为 1 分钟 → 观察两个周期 → 界面关闭 → 观察两个同长度窗口 → 手动刷新 | `node scripts/rv3_settings_and_scheduler.cjs` | `rv-3-settings-panel.png`、`rv-3-settings-disabled.png`、`rv-3-scheduler-observation.md` |
| rv-3 negative control | 实现前代码上 PATCH 该端点 | 见"负控设计" | 记录于 `rv-3-scheduler-observation.md` |
| rv-4 调度生命周期 / 并发去重 / 写失败传播（FR-3/5/6/7） | integration：子进程 import 探针、TestClient lifespan、真实线程与事件、tmp SQLite | `uv run pytest tests/test_monitor_scheduler.py tests/test_monitor_snapshots.py tests/test_console_store.py -v` | `rv-4-scheduler-and-core-tests.txt` |
| rv-4 HTTP 契约（快照端点、设置 GET/PATCH、job 写回与失败） | integration：tmp 库注入 + TestClient | `uv run pytest tests/test_monitor_api.py -v` | `rv-4-monitor-api-tests.txt` |
| rv-5 dashboard 前端（FR-2/6/8） | e2e：stub 快照/设置接口，真实浏览器渲染 | `just e2e tests/smoke/agent-runner-monitor.spec.ts` | `rv-5-e2e-monitor.txt` |
| rv-5 既有 console 页面回归 | e2e：console 四页 smoke（含 dashboard 无法访问警示） | `just e2e tests/workflows/console-pages.no-auth.spec.ts` | `rv-5-e2e-console-pages.txt` |
| rv-6 全仓门禁 | 仓库级 lint + 全量测试 + mkdocs | `just lint --repo`（含 `just test`、`mkdocs build --strict`） | `rv-6-lint-repo.txt` |
| rv-6 前端构建 | typecheck + 静态导出 | `just frontend-public typecheck`、`just frontend-public build` | `rv-6-frontend-build.txt` |

## Mock 边界

- rv-1：SQLite 用 `tmp_path` 真实文件库，不 mock；不涉及 GitHub。
- rv-2 / rv-3：真实 console 进程、真实 SQLite、真实 Chromium；GitHub 侧用本机已认证的 `gh` 真扫单仓库（`IAR_CONFIG` 把 registry 缩到 1 个仓库，只影响证据运行，不改提交文件）。
- rv-4 / rv-5：GitHub/`gh` 边界用 fake 扫描器（记录调用次数与并发窗口）或 Playwright 路由 stub；FastAPI lifespan、真实线程/事件、SQLite 与浏览器渲染均真跑。
- rv-6：无 mock。

## 负控设计

| Oracle | 负控 | 期望差异 |
|---|---|---|
| rv-1 | 在实现前代码树（`main @ 0d36877`，`_SCHEMA_VERSION=3`）上运行同一探针 | 实现前：`no such table: monitoring_snapshots`、`user_version=3`（红）；实现后：两表存在、`user_version=4`、3 条历史行保留（绿） |
| rv-3 | 在实现前代码上 `curl -X PATCH .../console/monitor/settings` | 404 Not Found，证明该端点与设置持久化此前不存在 |
| rv-4 | 写库注入异常（`upsert_monitor_snapshot` 抛错 / `save_monitor_settings` 抛错） | 正确实现：job 失败 + 旧快照 scanned_at 不变 + 500 响应；若吞错则会出现"刷新成功但数据未更新" |
| rv-5 | 空快照 stub（`pending_first_sync`） | 页面显示空态且**不**自行创建 overview job；有快照 stub 时同一路径渲染仓库卡片 |

## 对抗自检（实施期执行）

- rv-1：迁移用例断言的是"v3 种子库 → 新代码打开 → 独立连接复核"，而不是"用新代码建库后查表"；负控在实现前代码树上必须红。
- rv-3：设置值以 `GET /console/monitor/settings` 读回为准（不是只看界面文案），周期以 console 日志中的 `Monitor sync cycle` 行数计数，关闭窗口内该计数必须为 0。
- rv-5：设置面板断言的是 PATCH 的 canonical path 与请求体（不是"点击没报错"）。
- rv-4：并发用例通过 barrier/事件确认真实并行与去重，不依赖 sleep 计时。
