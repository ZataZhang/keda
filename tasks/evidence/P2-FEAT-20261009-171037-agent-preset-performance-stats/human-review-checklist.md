# 人工验收清单：Agent 与预设执行表现统计

PRD: [`tasks/pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md`](../../pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md)

本清单汇总 PRD §9 的两项 `Human-Confirmed`，不替代 Acceptance Checklist。交互版通过 `just prd review tasks/pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md` 打开同目录 HTML；该 HTML 已通过真实浏览器呈现检查（首屏卡片、翻页、结果生成与内嵌截图均正常，见 [rv-2-prd-review-browser-check.txt](rv-2-prd-review-browser-check.txt)）。

## 1. 确认统计归属按单次 attempt 计算

**你要确认的决定：** Agent 与预设的成功率按单次执行计算；若归属错了，失败后被替换的 Agent 可能被记成整项任务成功，比较结果会误导选择。

**PRD 原文（§2 决策一）：**

> 是否接受 Agent / 预设成功率按 attempt 计算，且整项任务结果不归属到单个 Agent 或预设？

attempt 指一条 Agent 单次执行记录。最终 `completed` / `failed` / `blocked` 只进入整项任务汇总，不复制给某个 Agent 或预设。自动证据以临时 SQLite 样本通过真实 API 路由核对该口径；详见 [rv-1-api-green.txt](rv-1-api-green.txt) 和 [rv-1-api-json.json](rv-1-api-json.json)。

**请按以下格式回复：** `同意`，或 `不同意：<希望调整的口径>`。

## 2. 查看真实 Stats 页面呈递与窄屏结果

**你要确认的决定：** 页面呈递应能读出 attempt 表和独立任务耗时；若呈递判断错了，关键指标可能不可见，窄屏布局也可能无法操作。

**PRD 原文（§9 Human-Confirmed）：**

> §9.1 人读呈递区已查看，或按表中复核方法完成检查。

**对应的 §9.1 复核对象：**

> rv-2：Agent 与预设行是否显示样本数、成功 / 非成功比例、P50/P90 和失败分类；整项任务是否按 outcome 独立呈现。

呈递截图来自 `just console-sync` 后由 worktree `uv run kc console` 服务、真实浏览器打开的 `/app/stats` 页面（全部仓库 + 30 天窗口，无 mock，数据为本地运行账本的真实记录）：桌面 [rv-2-stats-agent-performance.png](rv-2-stats-agent-performance.png)，窄屏 375px 收起导航后 [rv-2-stats-agent-performance-narrow.png](rv-2-stats-agent-performance-narrow.png)（容器 173px < 内容 920px，`overflow-x: auto` 横向滚动）。自动化门禁：`just e2e tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts` 5/5 通过（[rv-2-e2e-green-final.txt](rv-2-e2e-green-final.txt)）；预设样本数负控 4→5 变红、恢复转绿（[rv-2-e2e-negative-control.txt](rv-2-e2e-negative-control.txt)）。

**请按以下格式回复：** `已完成复核`，或 `尚未完成 / 发现差异：<具体项目>`。

## Stats 页面复核步骤

在当前 worktree 执行 `just console-sync`，再运行 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run kc console`，于真实 `/app/stats` 页面选择全部仓库和 30 天窗口。确认这些内容：

- [ ] Agent 表每行的 attempt 数、成功 / 非成功数和比例、P50 / P90、失败分类可读。
- [ ] 预设表按历史记录显示预设名、模型与对应 Agent（快照口径，不由当前配置反推；`removed-preset` 场景由 E2E 断言覆盖）。
- [ ] 未绑定 / 历史未记录 preset 计数独立显示。
- [ ] `completed` / `failed` / `blocked` 的任务耗时按整项 run 结果独立分组。
- [ ] 全部仓库视图显示仓库标识；筛选仓库及 7 / 30 / 90 天后，新统计随筛选更新。
- [ ] 新统计读取失败时显示独立错误，原 Stats 区块仍可见。
- [ ] 375px 窄屏下表格横向滚动，列标题与数据仍可辨认。

**回复时说明：** 两项人工确认分别同意 / 不同意；第二项也可说明尚未复核，并列出差异或需要补充的证据。
