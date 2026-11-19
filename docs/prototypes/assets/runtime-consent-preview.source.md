# `runtime-consent-preview.png` 来源

- 来源类型：Playwright / Chromium 浏览器渲染截图，不是 AI 生成图或生产运行截图。
- 源页面：`docs/prototypes/prd-lifecycle-observability.html#evaluations`。
- 状态准备：从评测默认页点击「开始对比」→ 配置对照 →「开始本地候选执行」；进入评审 payload 预览后隐藏 prototype chrome 和焦点描边。
- 截图命令：启动 MkDocs 后，在仓库根目录运行 `node docs/prototypes/assets/capture-runtime-prototype.mjs`。
- 画布：1440×1000 CSS px，deviceScaleFactor 1；保留评审预览对话框及背景遮罩。
- 采集日期：2026-10-29。
- 用途：呈现逐模型的静态 payload 预览、可用成本和显式授权/拒绝控制；验证层级为 `component preview`，不证明 provider 请求边界或实际费用。
- 视觉保持不变区域：接收模型与样本选择、将发送的完整静态内容、批次说明、默认未勾选的授权框及禁用的确认按钮。
- 失效条件：评测对话框结构、外发内容、默认关闭授权语义、成本披露或 `runtime-board.css` 变化时重截。
