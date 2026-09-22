# 失败上下文交接 · 界面草图

本页归档 `tasks/pending/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure.md` 的界面示意。它要回答一件事：recovery 耗尽后，"上一轮做到哪、为什么没过"这份交接记录长什么样，以及它怎么同时服务**下一轮 agent** 和**人**。

注意本 PRD 的 `Frontend Impact` 是 `No frontend impact`——两层表面都是 **GitHub 页面本身**（Issue 评论 + Draft PR），不是 `frontend-public/` 的任何页面。因此本原型复刻的是 GitHub 的信息层级，而不是产品 UI。

## 面板 A：Issue 交接评论（持久载体）

![Issue 交接评论](assets/blocked-draft-pr-01-failure-context-comment.png)

一条带 `iar:failure-context` marker 的 Issue 评论。marker 只携带键值元数据（`checkpoint` / `attempts` / `verifier` / `evidence`）供**确定性定位**；人读正文是 Markdown，说明快照性质、卡在哪、verifier 判定摘要、尝试历史摘要、缺失呈递物、诊断路径与快照 SHA。

这一层的关键作用是：**下一个 claim 把它读回来注入 continuation prompt**，agent 不必从零反推。选择 Issue 评论而不是"只放进 PR 正文"，是因为**没有安全 commit 时根本没有 PR**，而"无快照也要交班"是必须覆盖的一格。

## 面板 B：Draft PR 正文（同源的人读表面）

![Draft PR 正文](assets/blocked-draft-pr-02-draft-pr-body.png)

同一份 payload 的对外表面，由既有只读 content generator 生成。标签区只有既有的 `agent/review`，并显式标出**不存在** `validation/verifier-passed`——不可合并的判定归因给既有门禁，正文写什么都不改变状态。

## 关键信息层级

- **一个事实源，一个同源表面**：交接评论是唯一事实源（回灌与人的回溯都以它为准）；PR 正文是"发布那一刻的快照"，固定回链该评论，但复用既有 PR 时不保证被改写。
- **marker 不是状态**：`iar:failure-context` 沿用仓库既有 `iar:*` marker 约定，只用于定位，不是标签、不是状态枚举。
- **限量回灌**：只取**最近一条**记录并截断；更早的历史记录不会进入 prompt。
- **快照性质必须写明**：它是 `checkpoint_uncommitted_progress` 建的 **WIP 中途快照**，不是"做完但没过验收"——不写清会让人误判工作量与风险。
- **不做失败性质判定**："这算产品失败还是审核事故"只由 Agent 在正文里陈述，机器不建状态，因此也不可机器核对。

## 图片来源（provenance）

两张图是**本地 HTML 渲染截图**，不是 AI 生图、也不是运行时截图：

- 源文件：`docs/prototypes/blocked-draft-pr-surface.html`（自包含，无外部依赖）
- 渲染方式：Playwright / Chromium，`deviceScaleFactor: 2`，按 `#shot-handoff-comment` 与 `#shot-draft-pr-body` 两个区块分别截取

图中文字是**真实排版文本**，不存在 AI 生图的错字或整图重绘问题；但它是设计意图示意，**不构成任何验收证据**。

## 实现解释

耗尽链路：`run_agent_until_committed` 抛 `MaxRetriesExceededError` → `agent_runner_issue_handlers.py` 的 `except` 分支先做既有 `checkpoint_uncommitted_progress()` → **新增**写交接评论（带 marker）→ **有快照才**调 `publish_changes(..., require_prd_archived=False)` 发布 Draft PR → 原样 `raise`。下一个 claim 在 `build_progress_continuation_prompt` 处读回最近一条记录。

**刻意不新增**判定器、状态枚举、标签、发布模式或存储；`require_prd_archived` 是唯一放宽的安全检查，且只在耗尽路径传入。代价（失败性质不可机器核对、回灌可能继承错误判断）记在 PRD §12。
