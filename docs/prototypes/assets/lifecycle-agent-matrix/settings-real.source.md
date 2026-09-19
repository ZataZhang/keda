# settings-real.png 来源（浏览器截图）

- 类型：浏览器截图（非 AI 生成，因此不写生成提示词）
- 代码入口：`frontend-public/app/(app)/app/settings/page.tsx`（真实路由 `/app/settings/`）
- 状态准备：`just run all` 起本机栈；backdrop 为本机单用户会话（`zata · zata@localhost`），无额外种子数据
- 截图命令：`cd tests/playwright-e2e && PLAYWRIGHT_SKIP_STACK_BOOT=1 pnpm exec playwright test --grep @visual tests/workflows/prototype-screenshots.spec.ts --project=chromium`（见 `tests/workflows/prototype-screenshots.spec.ts` 的「采集设置页底图」）
- 画布尺寸：1440×1200，deviceScaleFactor 2
- 采集日期：2026-09-18
- 用途：`docs/prototypes/lifecycle-agent-matrix.html` 视图一底图。覆盖层从 y=116（真实"关于 iar 管理终端"卡片上沿）开始向下覆盖，并在其中先绘制新增的**全局**矩阵卡片（写 `config.toml`），再按真实样式复刻被下推的既有卡片与退出登录按钮。仓库级矩阵不在本页，入口在 Roadmap 的仓库行齿轮。
- 热点坐标来源：同一页面上 `getBoundingClientRect()` 实测（侧栏导航项、内容列 x=288 / 宽 1120）
