# 人工验收清单：多评审模型的 Agent 与模型效果评测

PRD: [`tasks/archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md`](../../archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md)

本清单汇总 PRD §9 的两项 `Human-Confirmed`，不替代 Acceptance Checklist。交互版通过 `just prd review tasks/archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md` 打开同目录 `human-review-checklist.html`（逐步按钮作答、内嵌截图、末页生成可复制 Markdown）；该 HTML 已用真实浏览器完成呈现检查（首屏卡片可见、翻页、结果生成、`pageErrors=0`，记录见 [human-review-checklist-browser-check.txt](human-review-checklist-browser-check.txt)）。

## 1. 确认分数语义：评分只是同批比较参考，不成为任何门禁

**你要确认的决定：** 是否接受「评审分数只用于同批结果的辅助比较，不进 CI、合并、发布或自动模型路由」。**如果判错：** 主观 LLM 分数会被当成客观真值并驱动路由/合并决策，中位数与分歧范围会误导执行预设的选择。

**PRD 原文（§2 决策一，请确认段）：**

> **请确认：** 评审分数只用于同批结果的辅助比较；逐评审结果与确定性检查分开展示；本功能不会将评分用于 CI、合并、发布或自动模型路由。

**PRD 原文（§9 Human-Confirmed 第 1 项）：**

> - [ ] 确认分数语义按 §2 决策一：不同 judge 的独立判断和 deterministic checks 分开展示，不计算默认加权总分；任何分数不成为 CI、合并、发布或自动路由门禁。（对照 `rv-1` 与 prototype/production 配对。）

**要看什么（生产截图，与目标图同状态对照）：**

![多执行模型、多评审者的结果对照（生产截图）](rv-1-evaluation-results.png)

> 验证层级：production capture；候选执行来自本地 fixture repo 的隔离 worktree，评审调用由 fixture 评审 CLI 进程承接，经真实 judge 进程边界（空目录 + 禁工具，外发边界见证据报告）。约 10 秒自检：两个执行预设与两个 judge 可展开；确定性 checks 与评分**不同列**；逐维分数、理由、中位数与分歧范围可见；页面上没有任何加权总分或通过/失败门禁。

目标图（component preview / design intent，不是生产入口证明）：

![多执行模型、多评审者的结果对照目标态](../../../docs/prototypes/assets/runtime-evaluation-preview.png)

机器侧复核证据：`rv-1-run-detail.json`（矩阵 4 候选、checks passed/failed 分列）、`rv-1-sqlite-fresh-read.txt`（8 条逐评审行；API 中位数与独立重算一致；单值篡改即改变中位数/范围，证明汇总由明细真实派生）。

**请按以下格式回复：** `同意`，或 `不同意：<希望调整的分数口径>`。

## 2. 确认外发与本地数据范围：预览后逐次授权，拒绝即零外发

**你要确认的决定：** 是否接受「评分模型只在页面上逐次预览并明确授权后，才发送预览中列明的确切 payload；完成/拒绝/取消后清除临时 payload，只本地保留结构化分数与 hash」。**如果判错：** 代码差异或敏感文本可能在你未确认的情况下发给外部 provider——发出后无法由本机撤回。

**PRD 原文（§2 决策二，请确认段）：**

> **请确认：** 候选执行按启动时显示并选择的 Agent 预设运行；评审由所选评审预设的 agent CLI 以禁用工具、空目录形态运行，只有当前页面确认的评审预设会收到预览中列明的内容（该 CLI 会联系其自己的模型服务），确认前不发起 judge 请求。评测暂存 payload 只在等待授权 / 评分期间本地保存，完成、拒绝或取消后删除；长期保留结构化分数、简短依据、来源快照与 hash，不保留 API key、原始工具日志或完整代码差异。

**PRD 原文（§9 Human-Confirmed 第 2 项）：**

> - [ ] 确认外发和本地数据范围按 §2 决策二：模型调用前逐次查看实际 payload 并确认；评测结果本地保存，拒绝/完成后临时 payload 清除；API key 和完整 transcript 不落库。（对照 `rv-2` 预览、真实请求 hash、拒绝负控和 fresh deletion 证据。）

**要看什么（生产截图，真实页面 Dialog 入口）：**

![评审 payload 预览与逐次授权（生产截图）](rv-2-payload-consent.png)

> 验证层级：production capture（真实 `/app/evaluations/` 页面展开的预览 Dialog，非直接渲染 modal）。约 10 秒自检：可见目标评审预设（agent/模型）、任务说明、执行摘要、真实 Diff 与 checks；批准后实际发送内容与预览 hash 逐字节一致。

拒绝路径负控：页面点击拒绝后评审进程调用数为 0、8 条 unauthorized 行维度分为空、聚合为空、暂存清除（见 [rv-2-consent-negative-control.txt](rv-2-consent-negative-control.txt) 与 [rv-2-after-page-deny.png](rv-2-after-page-deny.png)）。

目标图（component preview / design intent）：

![评审 payload 外发授权目标态](../../../docs/prototypes/assets/runtime-consent-preview.png)

**请按以下格式回复：** `同意`，或 `不同意：<希望收紧/放宽的外发边界>`。

## 回复汇总

两项人工确认分别回复 `同意` / `不同意：<调整诉求>`；也可以先在本机交互页作答，把末页生成的 Markdown 整段粘回对话。若发现生产截图与期望不符，请写明页面、状态与差异项。确认后横幅将翻为 ✅ 已验收并只回填验收记录；若发现 PRD 自身 oracle 或范围未达成，PRD 会被移回 `tasks/pending/` 重开。
