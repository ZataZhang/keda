# `runtime-board-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#dashboard`。
- 状态准备：直接打开默认总览；页面使用固定任务示例；隐藏底部原型控件与可点击区域描边。
- 采集方式：通过 Chromium / Playwright 从本地 HTML 入口单独重截 Dashboard；视口、倍率、原型控件遮罩与 [`capture-runtime-prototype.mjs`](capture-runtime-prototype.mjs) 的 Dashboard 采集设置一致。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；全页截图。
- 采集日期：2026-11-09（中文本地化后重截）。
- 用途：Prototype Hub 缩略图及 PRD 目标态；验证层级为 `component preview`，仅表达设计意图。
- 中文本地化：将导航、筛选、任务卡片、状态、模型占位名、日志和详情区域统一为简体中文；保留 KedaCode 品牌名与任务编号。
- 视觉结构：KedaCode 导航、状态筛选、左侧任务卡片与右侧日志详情的布局保持不变。
- 失效条件：共享产品 shell、`runtime-board.css`、`prd-lifecycle-observability.html` 或 Dashboard 固定 fixture 结构变化时重截。
