# Issue #443 人工审阅清单

## 人工决策

- [ ] 确认 Stats 的 Issue 分组保留 Agent 列，执行器列按实际调用顺序呈现；单执行器、切换回切、连续重复折叠和旧记录「未记录」符合预期。

## 状态与改动

- Issue: [#443](https://github.com/ZataZhang/keda/issues/443)
- PR：[#444](https://github.com/ZataZhang/keda/pull/444)（`issue-443` → `main`）：已公开、开启，非 draft，尚未合并。
- 状态核对日期：2026-11-26；合并前以 PR 页面实时状态为准。
- 列表和详情共享调用会话归属。新记录保存会话 ID，旧记录按完整会话核对秒级边界。Stats 保留 Agent 列，并显示自定义执行器的持久化名称。

## 审阅修复证据

- [复验报告](issue-443.evidence-report.md#人审导航)记录最终工作树的 Stats 表格与详情截图。Chromium 逐行核对 `constructor`、同秒交接、后续 5000 条事件及每次详情请求携带的记录主键。
- 修复前的运行列表、详情 API 和标签查表均复现审阅问题。输出保存在本地 `rv-1-console-stats-repair-baseline.txt`。

## 同秒交接复验

- 旧记录缺少会话 ID 时，只给能够确认在交接边界前创建的会话归入边界内开始的调用。新一轮已经落调用但还没有运行汇总时，旧行仍显示「未记录」，打开详情也没有新一轮的 Codex 调用。
- 在旧 PR head `f53d51be` 上，新增的两个 API 回归用例按预期失败。旧行错误返回 `["codex"]`。两个用例分别覆盖旧会话 ID 无时间戳、旧记录的起止时间落在同一秒。
- 在修复后的工作树上，真实生产构建的 `/app/stats/` 浏览器验证通过。使用隔离 SQLite，逐行核对 6 条运行及详情请求，覆盖缺汇总、两个已有汇总的同秒交接、可区分的相同窗口、回切、未知执行器和 5000 条后续事件。值守看板仍可查询运行中的调用。
- [Chromium 输出](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/recovery-same-second/chromium.txt)、[列表截图](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/recovery-same-second/rv-1-console-stats-repair.png)、[旧记录详情截图](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/recovery-same-second/rv-3-pending-handoff-detail.png)、[负控](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/recovery-same-second/baseline.txt)。

## 第二次恢复复验

- [复验报告](issue-443.evidence-report.md#第二次恢复复验)记录两种同秒串轮以及值守看板在汇总写入前后的轮询结果。
- 审阅时可以直接对照 [Chromium 输出、截图与 API 负控](https://github.com/ZataZhang/keda/tree/iar-evidence/issue-443/recovery-polling)。

## 尚未验证与下一步

- 当前浏览器证据为真实入口流程：生产构建的 `/app/stats/` → 展开 Issue 分组 → 真实 FastAPI API 与隔离 SQLite；Chromium 视口 1440×1000、默认主题。Stats overview 使用不可解析的本地 registry fixture 返回空统计，以避免访问 GitHub；没有验证窄视口或其他浏览器。
- 上述独立验证覆盖旧提交的非重叠窗口。修复前的读取上限是每个 Issue 最近 5000 条事件。修复后按目标会话和旧记录的候选范围批量读取；旧记录仅在归属唯一时展示。列宽和长执行器序列仍属于人工审阅项。
- 下一步：审阅 PR #444 的代码、已发布证据及最新 checks，完成人工决策；接受后将 PR 合入 `main`。
- 关闭条件：以 `main` 为 base 的 PR 包含 `Closes #443` 并合入。
