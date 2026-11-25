# Issue #443 验证计划

## 审阅修复复验

- `rv-1` 通过生产构建的 `/app/stats/` 和真实 FastAPI 服务打开 Issue #443 分组及五条详情。数据由独立进程写入隔离 SQLite，再由新启动的服务读出。覆盖旧记录同秒交接、5000 条后续事件、两条同时间窗的新记录、切换回切、连续重复、无事件和自定义执行器 `constructor`。
- 浏览器以列表 API 返回的 `record_id` 核对每次点击实际发出的 `GET /api/v1/agent-runner/console/runs/attempts` 请求，并核对详情渲染顺序。Stats 页面、详情弹窗、HTTP 路由和 SQLite 均走生产入口。隔离的 registry 避免访问 GitHub。
- 阳性运行 `bash tasks/evidence/issue-443/scripts/run_console_stats_repair.sh`。负控运行 `bash tasks/evidence/issue-443/scripts/check_console_stats_repair_baseline.sh`，用修复前的 API 复现错归与截断，再执行原前端查表表达式复现 `constructor`。

## 首轮实现验证

在隔离 SQLite 中为同一 Issue 写入三次运行和实际调用事件，通过生产构建的 Console Stats 页面、真实 FastAPI 服务和 Chromium 验证 Agent 列、执行器顺序、回切、连续重复折叠、旧记录及运行隔离；再对照运行详情时间线。使用改动前前端执行同一断言作为负控。

执行命令：`bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh positive-control`。原始证据和 manifest 由证据发布流程另行保存。
