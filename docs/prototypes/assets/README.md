# Prototypes Assets

本目录统一使用通用资源命名，避免业务语义耦合到文件名。

## 通用入口

- `prototype.css`: 默认原型页面样式入口（可直接在 `docs/prototypes/*.html` 引用）。
- `prototype.js`: 默认原型页面交互入口（状态推进、场景切换、时间线渲染）。
- `prototype-common.css`: 跨页面共享样式变量和基础组件。
- `prototype-hub.css`: Prototype Hub 目录、筛选、详情抽屉与响应式布局。
- `prototype-hub.js`: Prototype Hub 原型 registry、搜索筛选与详情渲染。
- `roadmap-prd-cicd-flow.css` / `roadmap-prd-cicd-flow.js`: CI/CD 多轮监控与自动修复状态播放器。
- `component-library.css` / `component-library.js`: 原型组件库的展示布局与交互示例。
- `component-detail.js`: 根据组件详情入口渲染变体、状态矩阵与组合示例。

## 页面资源命名

- `prototype-page-01.css` / `prototype-page-01.js`
- `prototype-page-02.css` / `prototype-page-02.js`
- `prototype-page-03.css` / `prototype-page-03.js`
- `prototype-page-04.css` / `prototype-page-04.js`
- `prototype-page-05.css` / `prototype-page-05.js`

新增页面时建议按 `prototype-page-xx.*` 延续，保证目录可维护性。
