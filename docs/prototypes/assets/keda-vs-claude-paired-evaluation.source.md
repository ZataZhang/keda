# 配对评测目标图来源

- 资产：keda-vs-claude-paired-evaluation.svg
- 类型：手工绘制的单状态低保真目标图；不是浏览器截图、AI 生成图片或交互原型。
- 画布：1440 × 1100。
- 代码依据：frontend-public/app/(app)/app/evaluations/page.tsx、frontend-public/components/evaluations/run-detail-panel.tsx、docs/prototypes/prd-lifecycle-observability.md。
- 目标：在同一题下并列表达 direct Claude 与 Keda workflow 的重复测试结果、流程摘要、耗时和 Agent CLI 调用数；不显示费用、总分或胜者。
- 示例内容：3 次重复中 direct 通过 3/3，Keda 通过 1/3；耗时和调用数是版面占位数据，不表示已运行的模型结果。
- 限制：静态图只锁定信息层级和主要字段，不承诺最终 DOM、响应式布局、真实数据或生产行为。实际交付必须通过真实 Evaluations 页面和 API 验证。
- 更新失效条件：候选结果 DTO、配对摘要字段、RunDetailPanel 的结果区结构或已批准的比较口径发生变化。
