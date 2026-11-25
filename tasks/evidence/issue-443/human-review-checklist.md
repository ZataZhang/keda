# Issue #443 人工审阅清单

## 人工决策

- [ ] 确认 Stats 的 Issue 分组保留 Agent 列，执行器列按实际调用顺序呈现；单执行器、切换回切、连续重复折叠和旧记录「未记录」符合预期。

## 状态与改动

- Issue: [#443](https://github.com/ZataZhang/keda/issues/443)
- 分支：`issue-443`；PR 尚未创建，提交请求后由 runner 发布以 `main` 为 base 的 PR。
- API 按不同 Issue 批量读取持久化调用事件，再按每条运行的时间窗口和 `run_id` 归属；Stats 保留 Agent 并新增执行器列。指南已更新。

## 证据

- [负控：基线表头缺少执行器列](rv-1-negative-control.txt)
- [绿验证终端记录：真实 Stats 页面、运行列表 API、详情时间线与 Chromium 浏览器断言](rv-1-console-stats.txt)
- [真实入口截图：Stats Issue 分组表格](rv-1-console-stats.png)
- [结构化 manifest](evidence.json)；复跑脚本位于 `scripts/`。

![真实入口截图：Agent 列保留，执行器列展示实际调用顺序](rv-1-console-stats.png)

## 已运行命令

- `bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh negative-control`：改动前按预期以退出码 1 失败，缺少执行器表头。
- `bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh positive-control`：通过真实生产构建、Console HTTP 服务和 Playwright Chromium；检查列顺序、三轮内容、无失败请求、无错误 toast，并保存上方截图。
- `kc evidence check --evidence tasks/evidence/issue-443`：1/1 PASS，stdout 断言通过（19.0 秒）；命令输出另有本机日志文件目录权限警告，不影响检查结果。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just lint --reuse`：通过。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just lint --full`：通过。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just test`：5689 passed，3 skipped。

## 尚未验证与下一步

- 当前浏览器证据为真实入口流程：生产构建的 `/app/stats/` → 展开 Issue 分组 → 真实 FastAPI API 与隔离 SQLite；Chromium 视口 1440×1000、默认主题。Stats overview 使用不可解析的本地 registry fixture 返回空统计，以避免访问 GitHub；没有验证窄视口或其他浏览器。
- 自动证据自检已完成并通过；PR 创建后仍需人工检查截图、代码与证据，特别确认目标浏览器中的列宽和长执行器顺序。
- 下一步：检查 runner 创建的 PR、查看代码与证据、完成人工决策；接受后将 PR 合入 `main`。
- 关闭条件：以 `main` 为 base 的 PR 包含 `Closes #443` 并合入。
