# Issue #443 验证证据

真实 `/app/stats/` 页面通过 Chromium 展示三次独立运行：Claude、Claude → Codex → Claude、未记录；Agent 列保留，运行详情时间线与列表一致。页面无失败请求或错误提示。真实入口使用隔离 SQLite 和本地 registry fixture，视口为 1440×1000；其他浏览器和窄视口未覆盖。

截图（本地图片）：

![Console Stats 运行记录](rv-1-console-stats.png)

本地查看：`open "/Users/zata/code/keda/.kc-worktrees/issue-443/tasks/evidence/issue-443/rv-1-console-stats.png"`

原始输出见 `rv-1-console-stats.txt`，负控见 `rv-1-negative-control.txt`；完整 manifest 和原始文件由证据发布流程另行发布。
