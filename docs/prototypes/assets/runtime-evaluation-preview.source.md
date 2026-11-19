# `runtime-evaluation-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#evaluations`。
- 状态准备：直接深链模型评测视图；页面使用固定对比 fixture；隐藏底部 prototype chrome 与可点击区域描边。
- 截图命令：启动 MkDocs 后，在仓库根目录运行 `node docs/prototypes/assets/capture-runtime-prototype.mjs`。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；全页截图。
- 采集日期：2026-10-29。
- 用途：模型与 Agent 评测 PRD 的目标态；验证层级为 `component preview`，评分数据为静态演示值。
- 视觉保持不变区域：评测集信息、多个执行预设的同批次对比、多个评审器分数与分歧、确定性检查/耗时/成本独立列。
- 失效条件：`runtime-board.css`、`prd-lifecycle-observability.html`、评测固定 fixture 或评分摘要语义变化时重截。
