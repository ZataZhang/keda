# `runtime-board-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#dashboard`。
- 状态准备：直接打开默认 Dashboard；页面使用固定任务 fixture；隐藏底部 prototype chrome 与可点击区域描边。
- 截图命令：启动 MkDocs 后，在仓库根目录运行 `node docs/prototypes/assets/capture-runtime-prototype.mjs`。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；全页截图。
- 采集日期：2026-10-29。
- 用途：Prototype Hub 缩略图及 PRD 目标态；验证层级为 `component preview`，仅表达设计意图。
- 视觉保持不变区域：KedaCode Console 导航、状态筛选、左侧任务卡片与右侧日志详情。
- 失效条件：共享产品 shell、`runtime-board.css`、`prd-lifecycle-observability.html` 或 Dashboard 固定 fixture 结构变化时重截。
