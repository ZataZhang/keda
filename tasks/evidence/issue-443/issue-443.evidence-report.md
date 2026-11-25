# Issue #443 验证证据

## 第二次恢复复验

| 入口 | 结果 |
| --- | --- |
| Stats → 展开 Issue → 查看详情 | 两个方向的同秒交接均验证通过。任一轮缺少汇总时，另一轮显示「未记录」，详情没有串入 Codex 调用。 |
| 值守看板 → 打开任务抽屉 → 等待轮询 | 汇总落库前后请求 URL 相同，API 返回相同的 Codex 调用，抽屉继续显示该调用。 |

生产 Console 构建通过。Chromium 经真实页面及 FastAPI 调用详情路由读取隔离 SQLite。值守看板任务摘要使用固定运行中状态，以保留原查询并单独验证汇总落库；原始日志与真实 GitHub 同步不在本轮验证范围内。

在原 PR head `f8b92d42`，两个 API 回归断言按预期失败：后轮返回了前轮的 Codex 调用，实时轮询在汇总写入后返回空。修复后，Console API、运行历史和 SQLite 存储定向测试共 83 项通过，本地核心测试 537 项通过。`just lint --repo`、严格文档构建和生产 Console 构建通过。

[Chromium 输出、截图与负控](https://github.com/ZataZhang/keda/tree/iar-evidence/issue-443/recovery-polling)

## 人审导航

| 真实用户流程 | 核对结果 |
| --- | --- |
| `/app/stats/` → 展开 Issue #443 | Agent 均为 `qoder`。五行执行器依次为 `constructor`、Claude、未记录、Codex → Claude → Codex、Claude。后来的 5000 条事件未影响最早的 Claude。 |
| 点击首行与第二行的「查看详情」 | 两条记录起止时间相同，浏览器分别携带不同记录主键，详情各显示该行自己的调用。 |
| 点击旧记录的「查看详情」 | 同秒交接两轮分别显示 Claude 与 Codex 开始的调用序列，回切保留，详情逐次展示重复调用。 |

执行器列和五个详情均由 Chromium 自动核对。视口 1440×1000、浅色主题，页面与 API 共用真实 FastAPI 服务，测试数据来自隔离 SQLite。窄视口和其他浏览器未覆盖。[PR #444](https://github.com/ZataZhang/keda/pull/444) · [PR CI](https://github.com/ZataZhang/keda/pull/444/checks)

![真实用户流程中 Stats 展示五轮执行器记录](rv-1-console-stats-repair.png)

本地图。运行 `open "/Users/zata/code/keda/.kc-worktrees/issue-443/tasks/evidence/issue-443/rv-1-console-stats-repair.png"`。

![真实用户流程中所选运行的详情仅展示 constructor](rv-1-console-detail-repair.png)

本地图。运行 `open "/Users/zata/code/keda/.kc-worktrees/issue-443/tasks/evidence/issue-443/rv-1-console-detail-repair.png"`。

修复前的负控见 `rv-1-console-stats-repair-baseline.txt`，最终工作树的浏览器和 API 阳性输出见 `rv-1-console-stats-repair.txt`，工作树指纹见 `rv-1-final-tree.txt`。

## 首轮实现证据

真实 `/app/stats/` 页面通过 Chromium 展示三次独立运行：Claude、Claude → Codex → Claude、未记录；Agent 列保留，运行详情时间线与列表一致。页面无失败请求或错误提示。真实入口使用隔离 SQLite 和本地 registry fixture，视口为 1440×1000；其他浏览器和窄视口未覆盖。

截图（本地图片）：

![Console Stats 运行记录](rv-1-console-stats.png)

本地查看：`open "/Users/zata/code/keda/.kc-worktrees/issue-443/tasks/evidence/issue-443/rv-1-console-stats.png"`

原始输出见 `rv-1-console-stats.txt`，负控见 `rv-1-negative-control.txt`；完整 manifest 和原始文件由证据发布流程另行发布。
