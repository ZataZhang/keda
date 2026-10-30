# `runtime-evaluation-baseline-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#evaluation-baseline`。
- 状态准备：从 Prototype Hub 进入 Console 原型，点击「评测基线」；切换真实 Agent 与校准候选验证候选执行估算，回到真实 Agent / repeats=3 / 不含留出题后截取重复结果卡片。
- 截图命令：启动 MkDocs 后，在仓库根目录运行 `node docs/prototypes/assets/capture-runtime-prototype.mjs`。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；结果卡片截图。
- 采集日期：2026-10-29。
- 用途：M13 PRD 目标态；验证层级为 `component preview`，表格使用固定 fixture，表达设计意图，不是生产运行证据。
- 关键状态：两条稳定的 3/3 结果、两条不稳定的 2/3 结果；不稳定题单列两条预设结果；执行故障单独计数。
- 失效条件：评测基线 HTML、`runtime-board.css`、`runtime-board-prototype.js` 或重复结果 fixture 语义变化时重截。
