# 人工验收清单：Agent 与预设执行表现统计

PRD: [`tasks/pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md`](../../pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md)

本清单汇总 PRD §9 的两项 `Human-Confirmed`，不替代 Acceptance Checklist。交互版通过 `just prd review tasks/pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md` 打开同目录 HTML；当前受管环境没有可用浏览器，尚未验证 HTML 呈现。

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

当前没有真实浏览器截图或修改后 375px 检查结果。此前 E2E 真实页面的数据展示、筛选、空态和错误隔离检查通过；窄屏检查曾因侧栏展开导致表格容器不可见而失败。当前 spec 已加入页面已有的“收起导航栏”操作，但最新 `just e2e` 重跑在 auth setup 启动 Chromium 时因 `MachPortRendezvousServer Permission denied (1100)` 退出，页面用例未运行。`kc console` HTTP 200 只证明静态文件可服务，不证明浏览器渲染。详见 [rv-2-e2e-final-red.txt](rv-2-e2e-final-red.txt)、[rv-2-console-http-final.txt](rv-2-console-http-final.txt) 和 [rv-2-browser-access-final.txt](rv-2-browser-access-final.txt)。

**请按以下格式回复：** `已完成复核`，或 `尚未完成 / 发现差异：<具体项目>`。

## Stats 页面复核步骤

在当前 worktree 执行 `just console-sync`，再运行 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run kc console`，于真实 `/app/stats` 页面选择全部仓库和 30 天窗口。确认这些内容：

- [ ] Agent 表每行的 attempt 数、成功 / 非成功数和比例、P50 / P90、失败分类可读。
- [ ] 预设表保留 `removed-preset` 历史快照，并显示模型与对应 Agent。
- [ ] 未绑定 / 历史未记录 preset 计数独立显示。
- [ ] `completed` / `failed` / `blocked` 的任务耗时按整项 run 结果独立分组。
- [ ] 全部仓库视图显示仓库标识；筛选仓库及 7 / 30 / 90 天后，新统计随筛选更新。
- [ ] 新统计读取失败时显示独立错误，原 Stats 区块仍可见。
- [ ] 375px 窄屏下表格横向滚动，列标题与数据仍可辨认。

**回复时说明：** 两项人工确认分别同意 / 不同意；第二项也可说明尚未复核，并列出差异或需要补充的证据。
