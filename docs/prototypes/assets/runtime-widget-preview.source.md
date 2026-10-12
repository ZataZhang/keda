# `runtime-widget-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#dashboard`。
- 状态准备：从默认 Dashboard 点击「开启桌面悬浮窗」；使用固定任务 fixture；隐藏底部 prototype chrome、可点击区域描边和活动焦点描边。
- 截图命令：启动 MkDocs 后，在仓库根目录运行 `node docs/prototypes/assets/capture-runtime-prototype.mjs`；脚本经按钮点击打开预览后截图。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；视口截图，保留对话框背景遮罩。
- 采集日期：2026-10-12。
- 用途：PRD 桌面悬浮窗目标态；验证层级为 `component preview`，不证明跨应用置顶、原生打包或 IPC。
- 视觉保持不变区域：紧凑四任务列表、状态标签、当前阶段、实际触达读数比例条及弱信号披露。
- 失效条件：`runtime-board.css`、`prd-lifecycle-observability.html`、改动树触达语义或桌面小窗布局变化时重截。
