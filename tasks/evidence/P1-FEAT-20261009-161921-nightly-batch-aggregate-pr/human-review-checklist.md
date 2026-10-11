# Human Review Checklist — 夜间任务批次聚合为单一总 PR

Review entry: [evidence report](P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.evidence-report.md). The PR and CI links are pending runner publication. Reply for each item with **同意** or **指出差异**; this checklist records implementation acceptance, not a claim that external GitHub behavior was tested.

## 1. 总 PR 是这一批 PRD 的唯一正式交付 / 验收事件

**决定与错误后果：** 确认唯一总 Draft PR 的 v2 正文精确列出整批唯一 PRD，并明确总 PR 合并会接受这些 PRD。漏列会把未审阅的 PRD 算作已验收；多列会扩大人工接受范围。

> “总 PR 是这一批 PRD 的唯一正式交付 / 验收事件：人工审阅实现 PR 中的本地正文构造与 v2 contract；GitHub 实际 body、PRD 合并验收记录和 merge 结果保持未验证（§2 决定一）。”

**查看：** [evidence report 的 rv-3](P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.evidence-report.md#rv-3--本地-v1--v2-pr-body-contract) 与 [v2 body contract 实现](../../../src/backend/core/use_cases/agent_runner_pr_body_contract.py)。预期本地完整集合通过、漏项 / 重复项 / 越权路径 / 来源不匹配被拒绝；真实 PR body 与 merge acceptance 留空。

## 2. 来源 PR 只在聚合成功后关闭并链接总 PR

**决定与错误后果：** 确认关闭调用只能出现在组合验证、总 PR 发布和 required checks 成功之后。提前关闭会让失败批次失去可恢复的开放来源入口。

> “来源 PR 只在聚合成功后关闭并链接总 PR：人工审阅关闭调用的代码前置条件；真实 checks、关闭评论、PR 状态和 branch 保留不验证，不以 mock 作为证据（§2 决定二）。”

**查看：** [聚合 lifecycle](../../../src/backend/core/use_cases/agent_runner_batch_aggregate.py) 与 [evidence report 的 rv-4](P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.evidence-report.md#rv-4--github-外部行为未验证)。预期代码先要求组合树验证与总 PR 上的 required checks 为 `SUCCESS`，之后才调用 source close；真实 GitHub 状态、关闭评论与 branch retention 未验证。

## 3. 人读呈递面

**决定与错误后果：** 确认报告把本地 PASS 与外部未验证边界分开呈递。若把本地测试误称为 GitHub 状态通过，会造成错误的 merge 决策。

> “9.1 Human Review Surface 已呈递：实现 PR evidence comment 呈递本地 CLI / Git / body contract 报告，并明确 GitHub 外部行为尚未验证。”

**查看：** 本 checklist 和 [evidence report](P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.evidence-report.md)。runner 发布实现 PR / CI 后补上对应链接和 stable evidence comment；当前不声称该发布已发生。
