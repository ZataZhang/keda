# PRD: 在受保护分支的仓库发布与恢复 PRD

- GitHub Issue: https://github.com/ZataZhang/keda/issues/400

> ✅ **交付前置**：无，可立即开工。
> ⬜ **验收状态**：未开工。

# Part A · 人审层

## 1. Introduction & Goals

协作者创建 Issue 后，PRD 发布因主分支保护被拒绝，留下无法执行的 Issue。允许协作者显式选择将 PRD 发布到任务分支，并把此前失败的发布补到原 Issue 上。

| 场景 | 预期 |
|---|---|
| 新建且选择任务分支发布 | 发布成功后按请求加入就绪队列，现有默认模式保持兼容 |
| Issue 已创建但 PRD 未发布 | 复用原 Issue，补发 PRD，重复执行不会创建新 Issue |
| 远端任务分支有不同内容 | 明确报告冲突并保留远端内容 |
| 按 Issue 执行且主分支没有 PRD | runner 读取任务分支的 PRD 和执行器覆盖，继续原有 Draft PR 流程 |

- [ ] 我确认这段解读与行为样例准确表达了我的需求。

需求方已在对话中确认上述方案与执行范围，复选框留给需求方本人操作。

## 2. Human Review Map

需求方已确认显式启用新模式，首版按 Issue 执行，使用 #398 验证已有 Issue 恢复。可观察验收为原 Issue 可执行且 PRD 与实现进入同一个 PR。

## 3. Usage And Impact After Implementation

遇到受保护的主分支时，协作者可以直接选择任务分支。遇到半完成发布时，错误输出给出 Issue 链接和恢复方法。

## 4. Requirement Shape

新建与补发共用发布规则。发布未完成时，Issue 保持未就绪。需要从主分支扫描 PRD 的列表和自动推进留待后续。

# Part B · 执行器层

## 5. Repository Context And Architecture Fit

复用 create_issue_from_prd、现有 Git 发布适配器以及 agent/rework-prd 的任务分支约定。agent_runner_worktree_create 已协调远端同名分支。agent_runner_orchestration_runtime 在 worktree 创建之前调用 attach_prd_lifecycle_overrides，需覆盖这条读取路径。CLI 负责参数和输出，core 编排，底层 Git 操作走现有适配能力。

## 6. Recommendation

增加显式任务分支发布选项与已有 Issue 恢复入口。沿用远端基线，在隔离 Git 工作区只提交目标 PRD，以普通非强制推送发布 issue-<N>。校验 PRD 路径和 Issue 的仓库、编号与现有 PRD 锚点。相同目标内容重试可成功并补全之前中断的元数据写回。分支有不同内容时报告冲突，不覆盖分支。Issue 链接固定到发布提交，保留机器锚点。

## 7. Implementation Plan

核对发布预检、交互和 JSON 行为，复用可用 Git 与 Issue 接口。处理 Issue 创建后发生的回写、推送、元数据或标签更新失败，使恢复可重试。调度在选择执行器前解析任务分支 PRD，并确保实际 worktree 使用该内容。同步配置/CLI 指南与随包 operator skill。

### 7.6 Realistic Validation Plan

- rv-1：通过真实 kc CLI 和临时 Git 远端验证拒绝主分支推送时，显式任务分支发布仍成功。Git 使用真实进程，使用测试替身接收 Issue API 调用，检查 ready 写入顺序。
- rv-2：制造推送失败后通过恢复入口复用同一 Issue；再次恢复同一内容成功；远端分支预先含不同内容时失败，比较前后远端提交并检查没有创建重复 Issue。
- rv-3：从干净 checkout 运行按 Issue 的 runner 入口，真实 Git 获取任务分支，保留 Issue 解析和执行器选择，替换付费实现 Agent 为可观察替身。断言主分支没有 PRD、执行器覆盖有效且实际 worktree 包含 PRD。
- rv-4：验证现有发布默认行为以及不发布选项，并运行相关测试、lint 和文档构建。
- 人工实地验收：修复可用后，由维护流程以现有 #398 和 #399 里的 PRD 验证。不要在自动测试或实现阶段改动 #398 或 #399。

## 8. Delivery Dependencies

- Gate: none
- Depends on tasks/issues: none
- Sequence: none

## 9. Acceptance Checklist

### Human-Confirmed

- [ ] 检查 #398 恢复的实际结果及分支上的 PRD 链接。

### Implementation Acceptance

- [ ] 显式分支发布支持受保护的主分支，默认行为兼容（rv-1、rv-4）。
- [ ] 失败信息说明原 Issue 的状态与恢复入口，恢复幂等并拒绝冲突（rv-2）。
- [ ] 按 Issue 执行使用分支 PRD 与执行器覆盖（rv-3）。
- [ ] CLI 文档和随包 operator skill 已同步（rv-4）。

## 10. Functional Requirements

FR-1 显式任务分支发布。FR-2 复用已有 Issue 恢复发布。FR-3 按 Issue 执行分支 PRD。FR-4 推送成功前不加 ready，失败说明恢复方式。

## 11. Non-Goals

不修改主分支保护、不改 backlog 主分支扫描、不实现多平台 PRD、不在实现任务中操作 #398/#399。

## 12. Risks And Follow-Ups

已有分支可能承载实现，发布入口必须避免覆盖。固定提交链接与实际任务分支后续演进需保持语义明确。#398 恢复成功后再处置 #399。

## 13. Decision Log

需求方确认先做 #400、显式启用分支发布、首版按 Issue 执行，并以复用 #398 完成实际验收。

## 14. Change Log

- 2026-11-19：根据 #400 和对话中确认的范围创建 PRD。
