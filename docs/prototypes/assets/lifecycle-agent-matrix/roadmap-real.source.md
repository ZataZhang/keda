# Backlog 历史底图（文件名 roadmap-real.png）来源（浏览器截图）

- 类型：浏览器截图（非 AI 生成，因此不写生成提示词）
- 代码入口：`frontend-public/app/(app)/app/backlog/page.tsx`（真实路由 `/app/backlog/`，默认视图「依赖图」）
- 状态准备：`just run all` 起本机栈；`registry` 中启用仓库 `keda-main`，Backlog 拉到 5 个待办 PRD
- 截图命令：`cd tests/playwright-e2e && PLAYWRIGHT_SKIP_STACK_BOOT=1 pnpm exec playwright test --grep @visual tests/workflows/prototype-screenshots.spec.ts --project=chromium`（见「采集 Backlog 依赖图底图」，以 `[data-testid="prd-open-content"]` 出现为就绪条件）
- 画布尺寸：1440×1200，deviceScaleFactor 2
- 采集日期：2026-09-18
- 用途：`docs/prototypes/lifecycle-agent-matrix.html` Backlog 视图底图。截图采集于 2026-09-18，侧栏静态文字仍是历史名称 Roadmap；原型在该位置覆盖显示 Backlog。前端现行路由为 `/app/backlog/`。仓库行右侧 ⚙ 覆盖层会进入统一生命周期设置页并预选仓库。
- 热点坐标来源：同一页面上 `getBoundingClientRect()` 实测——PRD 卡片 x=561,y=445,w=220,h=64；受管理仓库每行 x=295,y=72+34i,w=226,h=32（10 行，齿轮落在行内右侧 x=490,y=行顶+5,w=22,h=22）
