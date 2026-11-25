# Issue #443 验证计划

在隔离 SQLite 中为同一 Issue 写入三次运行和实际调用事件，通过生产构建的 Console Stats 页面、真实 FastAPI 服务和 Chromium 验证 Agent 列、执行器顺序、回切、连续重复折叠、旧记录及运行隔离；再对照运行详情时间线。使用改动前前端执行同一断言作为负控。

执行命令：`bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh positive-control`。原始证据和 manifest 由证据发布流程另行保存。
