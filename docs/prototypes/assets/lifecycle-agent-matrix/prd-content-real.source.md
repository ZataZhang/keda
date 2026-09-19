# prd-content-real.png 来源（浏览器截图）

- 类型：浏览器截图（非 AI 生成，因此不写生成提示词）
- 代码入口：`frontend-public/components/roadmap/prd-content-view.tsx`（由 `/app/roadmap/` 点击 PRD 卡片 `[data-testid="prd-open-content"]` 进入）
- 状态准备：`just run all` 起本机栈；打开 `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md` 的原文
- 截图命令：`cd tests/playwright-e2e && PLAYWRIGHT_SKIP_STACK_BOOT=1 pnpm exec playwright test --grep @visual tests/workflows/prototype-screenshots.spec.ts --project=chromium`（见「采集 PRD 原文底图」，以 `[data-testid="prd-content-body"]` 出现为就绪条件）
- 画布尺寸：1440×1200，deviceScaleFactor 2
- 采集日期：2026-09-18
- 用途：`docs/prototypes/lifecycle-agent-matrix.html` 视图三底图。覆盖层只在工具栏右侧加「Agent 覆盖」按钮（x=1259,y=93,w≈120,h=32），点击后打开右侧 620px 抽屉；原文区域保持真实像素不动。
- 热点坐标来源：同一页面上 `getBoundingClientRect()` 实测（返回列表按钮 x=561,y=95,w=100,h=32；工具栏行 x=561,y=93,w=830）
