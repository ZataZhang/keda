# PRD: Console 统计与成本按 Issue 汇总

- GitHub Issue: https://github.com/ZataZhang/keda/issues/449

> ✅ **交付前置**：无，可立即开工。
>
> ⬜ **验收状态**：未开工。

## Feature Overview (功能一览)

- 按 Issue 查看执行次数、Token 和美元估价，展开核对执行和模型明细。
- 仓库状态、用量趋势与模型计价各有独立入口。保留 Agent 表现与 PRD 执行分析。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Interpretation (解读回显)

用户已在会话批准采用原型 10 的行内展开设计、融合其他方案并交由 KC 实现。统计页默认打开 Issue 账本。当前状态来自 GitHub，期间开销来自本机账本。

| 场景 | 可验收结果 |
|---|---|
| 同一 Issue 执行三次，其中一次失败 | 表中一行；执行、Token、费用均纳入失败尝试 |
| 两个仓库有同号 Issue | 独立显示，链接到各自的 GitHub Issue |
| 缩小日期范围 | 期间次数、Token 与费用更新；当前状态保持 GitHub 口径 |
| 费用有缺项或已知免费 | 显示已知金额并标「部分计价」、显示「暂无估价」或显示确认的 $0 |

## 2. Human Review Map (介入与风险地图)

- 用全宽表及行内展开，子页签为执行记录、模型用量。
- 页级视图为按 Issue、仓库状态、用量趋势、模型与计价；保留已有诊断入口。
- 使用经用户确认的五张静态原型作为视觉基准。计价与筛选规则以本文为准。

## 3. Usage And Impact After Implementation

从 Stats 查最近执行或排序查高成本 Issue。仓库页状态计数可打开对应仓库与状态的 Issue 集合，用量趋势可按日期选区查看相应账本。

## 4. Requirement Shape

主表可按 Issue 标题或编号搜索，按仓库、日期和状态筛选，支持按最近执行、费用、Token 和执行次数排序。首次默认按最近执行降序。显示仓库、可跳到 GitHub 的 Issue 编号、标题、当前状态、期间执行次数、Token 总量和估算金额。展开可查看每次执行的结果、Token 和费用，以及模型输入、输出、缓存读写、估价和计价状态。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

Full-stack Console。真实入口为 frontend-public 的 /app/stats；复用该路由、Stats 页签、运行趋势、Token 成本区、现有 API 客户端及成本设置。后端复用 Console SQLite 调用账本、运行历史、GitHub Issue 状态与生命周期统计用例。仅在现有 API 无法提供仓库加 Issue、期间切片和单次执行归属时扩展读接口，遵守 api → core → engines → infrastructure 的边界。保留旧数据与既有调用者的可用行为。frontend-admin 不在范围。检查现有计划后未发现硬前置。

## 6. Recommendation

复用现有用量归属、费用算法和价格配置，避免衍生第二套费用账本。以仓库 ID 和 Issue 号为汇总键。未经关联的记录不分摊到 Issue，显示覆盖缺失；同一次调用只计算一次。

## 7. Implementation Guide

### 7.1 Core Logic

- 执行明细保留有运行但无 usage 的行。旧记录缺模型、缺价格、没有 usage 或发生截断时，按可用事实披露；不得构造美元数或把未知记为免费。
- GitHub 状态与日期独立。仓库入口跳转应能看到匹配当前状态但期间无运行的 Issue，正确标示无期间数据。获取失败时显示未知状态。
- 趋势按同一期间口径汇总，选择日期区间后 Issue 表按该区间更新；同一日期边界走项目现有时区规则。
- 模型计价页展示以 Token 数为分母的计价覆盖率，并保留现有价格设置入口。仓库完成率进度条退出默认首屏。

### 7.2 Change Impact Tree

- frontend-public 的 app Stats 路由、components/stats、lib/api/console.ts 及类型。
- 如需扩展，沿 agent_runner_console 路由与 Console Stats、生命周期、运行历史用例调整后端，复用既有 store，优先免迁移。
- tests/playwright-e2e 的 Stats 真实路由验证、相关后端集成验证、控制台用户指南与导航影响核对。

### 7.3 Risk Classification Register

| 风险 | 等级 | 证据 |
|---|---|---|
| 归属错误、重复计费、部分计价显示为免费 | R2 | rv-1 |
| 真实页面筛选、页签、展开或仓库跳转错误 | R1 | rv-2 |
| 趋势选区或覆盖率展示偏离口径 | R1 | rv-3 |

### 7.6 Realistic Validation Plan

- id: rv-1
  oracle: 经真实 SQLite store 和 Console HTTP 入口读取固定运行及调用记录，覆盖跨仓同号、PRD 路径变化、失败重试、日期外尝试、免费、缺价与无 usage。独立手算预期 Token 和美元，核对列表、执行分项和模型分项，重复请求结果稳定。仅可替换 GitHub 网络边界，不可 mock 汇总逻辑、SQLite 或 HTTP 路由。用最终实现树产生 rv-1-cost-api.txt。
- id: rv-2
  oracle: 经真实 Console Stats 路由验证默认页、搜索、价格排序、状态筛选、两个行内页签及仓库跳转。记录环境和夹具边界，留真实页面截图 rv-2-issue-stats.png，并和相同状态的原型配对。前端使用 npm run dev；E2E 按 tests/playwright-e2e/README.md 执行受影响 spec。
- id: rv-3
  oracle: 真实 Stats 页面验证日期区间选中后只有对应 Issue 期间数据，模型覆盖率分母为全部已记录 Token；保留输出或截图 rv-3-trend-and-coverage。
- 按项目标准运行 just test，核对前端 lint 与类型检查，提交最终实现树证据，由独立 verifier 验证。真实数据受权限限制时记录最高可行边界。

### 7.7 Low-Fidelity Prototype

[五张定稿图和来源](../../docs/prototypes/issue-cost-stats/prototype.md)，[图集](../../docs/prototypes/issue-cost-stats/index.html)。01 和 05 为同一行的两个展开状态。示意数字、跨图筛选控件差异及多出的表格不是产品要求。

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 使用现有 Stats 和账本能力。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

进入真实 Stats，查一个 Issue，比较汇总与执行、模型明细；从仓库阻塞数跳转，随后验证趋势选区与计价覆盖。PR 提供原型与真实页面对照。

### 9.2 Acceptance Evidence Package

按 rv-1、rv-2、rv-3 风险顺序呈递，记录最终 Git 树与验证边界。

### Human-Confirmed

- [ ] 已看主表、两个行内页签及三个辅助视图的对照，认可产品呈现。

### Behavior Acceptance

- [ ] rv-1 证明跨仓 Issue 隔离、重试纳入、期间切片与计价完整性。

### Architecture Acceptance

- [ ] 复用现有账本和计价语义，保留旧记录及既有 Stats 调用方。

### Frontend Acceptance

- [ ] rv-2 与 rv-3 证明真实路由的搜索、筛选、排序、展开、仓库跳转、趋势选区和覆盖率。

### Documentation Acceptance

- [ ] 控制台指南说明统计范围、计价状态和入口变化。

### Validation Acceptance

- [ ] 最终树的验证证据已归档并取得独立 verifier PASS。

### Delivery Readiness

- [ ] Draft PR 包含验证证据和人工验收入口。

## 10. Functional Requirements

- FR-1 Issue 账本与行内执行、模型明细。
- FR-2 GitHub 状态视图及仓库状态跳转。
- FR-3 期间趋势及选区过滤。
- FR-4 模型计价、覆盖率和已有诊断入口。

## 11. Non-Goals

不做真实供应商账单、价格历史账本、单位合格交付成本、无法归属的历史补账或自动合并。

## 12. Risks And Follow-Ups

若账本缺失执行关联，显示可证明的覆盖范围；用可验证的降级交付，避免重建未知记录。

## 13. Decision Log

用户在会话选择方案 10 和融合视图，并批准发布交接材料、建 Issue、点名启动 KC。

### Final Reconciliation

由执行者完成。

## Change Log

### 创建 PRD
- Type: scope
- Before: Stats 首页以仓库完成率为主，Issue 费用在明细底部。
- After: 以 Issue 账本为默认入口，融合三个辅助视图。
- Reason: 查清每个 Issue 的执行结果与开销。
- Impact: FR-1 至 FR-4，新增五张视觉基准。
- Review: 会话已确认方案与交接，交付验收待完成。
