# Verifier 运行中的单 Issue 人工接管

这是一个 **interactive prototype**（交互原型），以静态 fixture 模拟 Dashboard 中的 Issue 详情和人工接管流程。它不调用生产 API，不会停止本机进程，也不代表功能已实现。

## 打开方式

- Hub 入口：[Prototype Hub](hub.html) →「Verifier 运行中的单 Issue 人工接管」
- 直接入口：[打开交互演示](verifier-human-takeover.html)
- 本地预览：在仓库根目录运行 `uv run mkdocs serve`，打开 `http://127.0.0.1:8000/prototypes/verifier-human-takeover.html`

## 状态模型

```text
Dashboard · Issue #42 · verifier running
  ├─ 接管此 Issue → 确认 Dialog → 正在停止 → 已交给你审查
  ├─ 状态切换：请求仍在处理
  ├─ 状态切换：verifier 已先完成 → 接管冲突
  ├─ 状态切换：PR HEAD 已变化 → 旧结果无效、暂停自动处理
  └─ 状态切换：无法停止 → 未交接、可重试
```

## 点击说明

- **接管此 Issue**：打开确认框，说明停止范围、Draft PR 状态及取消不是验证结论。
- **停止并交给我**：进入“正在停止”，约两秒后模拟 Runner 确认子进程已退出，再显示“已交给你审查”。
- **返回 / × / Escape / 点击遮罩**：关闭确认框，不发起模拟接管。
- **底部「场景」**：切换固定演示状态。选择“请求仍在处理”后，再打开场景菜单并点“演示 Runner 确认进程已退出”可转到成功态；正常接管流程也会自动进入成功态。
- **查看 PR 详情 / 打开 Draft PR**：滚动到同页 PR 信息卡；不打开 GitHub。
- **底部原型 Dock**：返回 Hub、打开说明、切换演示状态、显示可点击区域和重置。

## 模拟内容与设计依据

- 业务画布依据 `frontend-public/components/layout/app-shell.tsx`、`app-sidebar.tsx`、`frontend-public/app/(app)/app/dashboard/page.tsx`、`frontend-public/components/agent-runner/issue-detail.tsx` 和 `frontend-public/app/globals.css`。
- 组件关系、PR 与 worktree 字段、事件时间线结构参考现有 `IssueDetail` 和 `IssueMonitoringSnapshot`。
- 本原型使用静态示例 Issue #42、PR #58、run id 和 HEAD SHA；状态切换全部由浏览器本地 JavaScript 完成。
- 不使用图片，因此没有图片提示词或截图来源旁车。

## 生产实现仍需确认

- 活动 verifier、运行身份与当前 PR HEAD 的真实 API 契约。
- 确认窗口关闭后的取消语义，以及长时间等待时的轮询与取消恢复体验。
- Runner 对子进程树的真实取消与回收时间。
- 成功接管后 Issue label、评论、审计状态和自动处理 hold 的最终呈现。

## 验证层级

本文件是 **interactive prototype**。验证覆盖原型页面中的确认、等待、成功、冲突、HEAD 漂移和取消失败分支；没有通过生产 Dashboard、真实 Console API、SQLite 或 Runner 执行，因此不属于生产入口验证或 E2E 验收证据。
