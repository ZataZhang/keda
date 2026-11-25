# Issue #443 人工审阅清单

## 人工决策

- [ ] 确认 Stats 的 Issue 分组保留 Agent 列，执行器列按实际调用顺序呈现；单执行器、切换回切、连续重复折叠和旧记录「未记录」符合预期。

## 状态与改动

- Issue: [#443](https://github.com/ZataZhang/keda/issues/443)
- PR：[#444](https://github.com/ZataZhang/keda/pull/444)（`issue-443` → `main`）：已公开、开启，非 draft，尚未合并。
- 状态核对日期：2026-11-25；合并前以 PR 页面实时状态为准。
- 列表和详情共享调用会话归属。新记录保存会话 ID，旧记录按完整会话核对秒级边界。Stats 保留 Agent 列，并显示自定义执行器的持久化名称。

## 审阅修复证据

- [复验报告](issue-443.evidence-report.md#人审导航)记录最终工作树的 Stats 表格与详情截图。Chromium 逐行核对 `constructor`、同秒交接、后续 5000 条事件及每次详情请求携带的记录主键。
- 修复前的运行列表、详情 API 和标签查表均复现审阅问题。输出保存在本地 `rv-1-console-stats-repair-baseline.txt`。

## 首轮实现证据

- [负控：基线表头缺少执行器列](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/rv-1-negative-control.txt)
- [绿验证终端记录：真实 Stats 页面、运行列表 API、详情时间线与 Chromium 浏览器断言](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/rv-1-console-stats.txt)
- [真实入口截图：Stats Issue 分组表格](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/rv-1-console-stats.png)
- [结构化 manifest](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/evidence.json)；[复跑脚本](https://github.com/ZataZhang/keda/tree/iar-evidence/issue-443/scripts)和[独立验证报告](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-443/issue-443.verifier-report.md)也已发布。原始证据位于独立证据分支，复跑前需将脚本及相关文件放回 `tasks/evidence/issue-443/`。

![真实入口截图：Agent 列保留，执行器列展示实际调用顺序](https://raw.githubusercontent.com/ZataZhang/keda/refs/heads/iar-evidence/issue-443/rv-1-console-stats.png)

## 已运行命令

- `bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh negative-control`：改动前按预期以退出码 1 失败，缺少执行器表头。
- `bash tasks/evidence/issue-443/scripts/run_console_stats_real_entry.sh positive-control`：通过真实生产构建、Console HTTP 服务和 Playwright Chromium；检查列顺序、三轮内容、无失败请求、无错误 toast，并保存上方截图。
- `kc evidence check --evidence tasks/evidence/issue-443`：1/1 PASS，stdout 断言通过（19.0 秒）；命令输出另有本机日志文件目录权限警告，不影响检查结果。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just lint --reuse`：通过。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just lint --full`：通过。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache just test`：5689 passed，3 skipped。
- 独立 verifier 对实现提交 `77c15925d21112c8d6c7d168df50ce17019f84e3` 给出 PASS：真实入口阳性通过，手动构建父提交前端后负控按预期失败，相关 API 测试 41 passed。负控脚本不会自行切换旧版构建。
- PR #444 的已完成 CI 检查通过（部分不适用任务跳过）；合并前仍应查看 PR 最新 checks。本次修复仅更新审阅记录，以上行为验证针对上述实现提交，并非本次文档改动后重跑。

## 本轮审阅记录修复检查

- 已核对 PR #444 元数据及独立证据分支：正负控终端记录、manifest、独立验证报告均可读取；截图与复跑脚本的原始链接均返回 HTTP 200，截图具有 PNG 文件签名。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache uv run python hooks/shared/check_guidelines_consistency.py`：通过。
- `UV_CACHE_DIR=/private/tmp/issue-443-uv-cache uv run mkdocs build --strict`：通过。
- `git diff --check` 与 `git diff --cached --check`：通过。此次只修正文档，不重新声称执行了上方行为测试；原始证据和实现提交对应关系保持不变。

## 尚未验证与下一步

- 当前浏览器证据为真实入口流程：生产构建的 `/app/stats/` → 展开 Issue 分组 → 真实 FastAPI API 与隔离 SQLite；Chromium 视口 1440×1000、默认主题。Stats overview 使用不可解析的本地 registry fixture 返回空统计，以避免访问 GitHub；没有验证窄视口或其他浏览器。
- 上述独立验证覆盖旧提交的非重叠窗口。修复前的读取上限是每个 Issue 最近 5000 条事件。修复后按目标会话和旧记录的候选范围批量读取；旧记录仅在归属唯一时展示。列宽和长执行器序列仍属于人工审阅项。
- 下一步：审阅 PR #444 的代码、已发布证据及最新 checks，完成人工决策；接受后将 PR 合入 `main`。
- 关闭条件：以 `main` 为 base 的 PR 包含 `Closes #443` 并合入。
