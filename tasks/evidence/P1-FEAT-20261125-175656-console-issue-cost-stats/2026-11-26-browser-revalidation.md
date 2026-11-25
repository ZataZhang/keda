# Stats 真入口补验

生产基线为 PR #452 的 `ee4c68931893dc92b700dd4616b0ab2e9049ac91`。在独立检出使用锁定依赖构建静态前端，由该提交的 `kc console` 提供真实同源路由。补验仅修改 Playwright 的价格设置链接定位器，将断言限定到模型计价区。生产代码保持基线。

- RV-1：真实 SQLite 与 Console HTTP 集成测试 1 条通过。见 [原始测试输出](rv-1-cost-api.txt)。
- RV-2：真实 Console 页面使用确定性 Stats API 夹具，覆盖搜索、排序、筛选、仓库状态跳转、Issue 执行和模型展开、美元估价与缺失状态。见 [Issue 账本](rv-2-issue-stats.png)、[模型展开](rv-2-issue-models.png)、[仓库状态](rv-2-repository-status.png)。
- RV-3：同一 Console 页面和夹具验证 UTC 日期选区与计价覆盖率。见 [用量趋势](rv-3-trend-and-coverage-trend.png)、[模型计价](rv-3-trend-and-coverage-pricing.png)。
- 浏览器验证共 3 条通过。见 [原始测试输出](rv-2-3-browser.txt)。截图层级为真实 Console 用户流程，Stats API 数据使用夹具，按 [静态设计原型](../../../docs/prototypes/issue-cost-stats/index.html)核对。

## 独立复核结果

**BLOCKER**。真实页面截图及当前浏览器断言有效。仍需完成原始 PRD 的以下两项后再复核。

1. 趋势图日期柱目前不可交互，趋势页没有随选区更新的 Issue 账本。测试只修改全局日期输入后切换页签，未覆盖趋势到 Issue 的路径。
2. RV-1 的 SQLite 与 HTTP 测试核对用量。当前美元断言使用另写的浏览器夹具。需要从同一份真实账本 HTTP 响应接入生产计价逻辑，独立手算并核对 Issue、执行与模型分项。

修复后重跑相关证据，记录最终实现树摘要，完成 Final Reconciliation。当前证据用于定位和复核，验收状态仍为验证阻塞。
