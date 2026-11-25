# Stats 真入口补验

实现树（排除 PRD 与其证据目录）Git tree：`27ace7be56801ff9dc0886a13e96c9fc9bec9fe9`。

静态生产构建由同一检出的 `kc console` 提供。真实 Console 页面、AppShell、筛选和计价组件均运行，测试拦截 Stats API 响应。配套 [静态设计原型](../../../docs/prototypes/issue-cost-stats/index.html)。

- RV-1：实际 SQLite 账本 → Console HTTP 用量响应及持久化价格响应 → 生产页面计价。用测试环境变量 `KC_ISSUE_COST_HTTP_FIXTURE` 将两个 HTTP 响应导出并原样交给 Playwright。模型单价为输入 2、输出 4、缓存读 1、缓存写 3 USD / 百万 Token。第 1 次执行手算 (100×2 + 50×4 + 10×1 + 5×3)/1M = 0.000425 USD，第 2 次 (80×2 + 40×4 + 5×1)/1M = 0.000325 USD。第 3 次模型未知，Issue 共 320 Token，已计价 290 Token，Issue 与 model-a 均为 0.000750 USD，Issue 标为部分计价。见 [SQLite/HTTP 测试输出](rv-1-cost-api.txt)、[页面计价用例](rv-2-3-browser.txt)。
- RV-2：真实路由覆盖搜索、排序、筛选、仓库跳转、Issue 执行和模型展开、美元估价与缺失状态。见 [Issue 账本](rv-2-issue-stats.png)、[模型展开](rv-2-issue-models.png)、[仓库状态](rv-2-repository-status.png)。
- RV-3：点击趋势图 11 月 23 日日期，断言请求的 UTC 开始和结束日期、下方 Issue 集合与期间执行；切换模型与计价，核对 0% 覆盖和 200 万 Token 分母。见 [用量趋势](rv-3-trend-and-coverage-trend.png)、[模型计价](rv-3-trend-and-coverage-pricing.png)。

浏览器共 4 条通过。截图验证层级为真实 Console 用户流程，数据由 Playwright 提供；RV-1 的计价数据直接来自 SQLite 夹具驱动的 HTTP 端点。

## 独立复核

**PASS**。独立复核重新导出真实 HTTP 数据并运行测试，复算最终树为 `27ace7be56801ff9dc0886a13e96c9fc9bec9fe9`。最终浏览器输出 4 条通过，覆盖率分母断言精确核对 0 / 2000.0k。全套 Playwright TypeScript 检查仅保留既有的 idea-inbox.spec.ts(124,40) TS2339。人工验收尚未完成。

## 同步 main 后补验

整合 main 的 f3ae998a 后，两条 SQLite 时间窗查询复用运行记录列清单，保留主线新增的记录 ID 与调用会话字段。API 和运行历史相关测试 51 条通过；生产 Console 浏览器测试 4 条通过。合并候选实现树为 `522a7e21cffc6cdb739f212df77b190ecc60ef65`。
