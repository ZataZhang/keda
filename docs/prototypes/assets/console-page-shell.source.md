# 实际 Console 共享外壳来源

- 真实入口为 `/app/repositories/lifecycle/`，当前后端端口 8313 托管的 Console 静态构建。
- 日期为 2026-11-26，采集工具为 Playwright / Chromium，浅色主题。
- DOM 来自 AppShell 和 AppSidebar，页面内容替换为 `data-prototype-page-slot`。
- HTML 根字体类与构建 CSS 原样保存，CSS 的字体引用改为同目录文件，正式 App 图标从实际页面读取。
- 框架资源属于渲染快照，生产侧栏、主题、字体、页面布局或构建样式变更后，重新导出本外壳并重截全部引用它的原型图。
- 当前引用者为 `configurable-workflows/index.html`，实际页面状态和新业务状态的验证分别记录。
