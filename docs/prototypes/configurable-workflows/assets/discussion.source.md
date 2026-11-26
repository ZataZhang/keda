# 实际页面中的讨论流程截图来源

- 日期为 2026-11-26，工具为 Playwright / Chromium，视口为 1440 × 1100，浅色主题。
- 原型入口为 `../index.html`，准备步骤为选择讨论与内容生成工作流。
- 页面底稿来自真实 `/app/repositories/lifecycle/`，当前全局配置范围。
- 复用共享 `../../assets/console-page-shell.html`、实际构建 CSS 和字体、`lifecycle-settings-frame.html`、`actual-workflow-graphs.html` 与 `actual-sheet-header.html`。
- 原型交互来自 `../page-shell.js` 和 `../prototype.js`。截图隐藏评审 Dock 和热点描边。
- 验证层级为 `interactive prototype / component preview`。源页面框架来自真实渲染，新节点能力、运行和恢复状态使用示例。
- 外壳、字体、共享样式、页面框架或原型视觉发生相关变更后，重截全部引用它们的目标图。
