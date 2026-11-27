# Idea Inbox — 总结（AI 派生，可重写）

_最后更新：2026-11-27 13:56_

[ideas.md](ideas.md) 保留想法原文和生命周期标记。本页结合当前代码、PRD 位置与验收横幅整理排期。冻结项的重启条件是本次盘点的建议。

## 主题聚类

- **交付编排与运行控制** — 路线图、依赖调度和 Backlog 控制已有交付。近期两条节点编排想法已收敛到 CLI 可配置节点与可恢复工作流。11 月 26 日提出的「先交付后验证」另存于 Inbox。（来源 2026-06-14 19:07–20:15、2026-06-15 00:50、2026-11-13 10:23、2026-11-19 23:15、2026-11-26 22:53）
- **运行可观测性与验收** — attempt 历史、会话恢复、调用追踪、Console 进度和结构化验收证据已有交付。Verifier 退出前写出的 stdout 现有捕获回归用例。本机 Agent 也已有按需浏览器入口。（来源 2026-06-14 20:24、2026-06-15 00:48 / 09:30、2026-06-26 13:39 / 15:55、2026-07-31 15:37、2026-09-29 10:57、2026-10-10 09:25、2026-10-15 20:42）
- **CLI 优先与构想工作台** — CLI 提供核心能力，默认前端供直接使用、修改和参考。构想工作台把实时对话、可自定义的产物提示词和可替换客户端放到同一份 PRD。（来源 2026-11-17 13:40、2026-11-19 23:02、2026-11-20 00:19 / 00:24）
- **评测与运行数据** — 已有 Memory 持久化与 Token 统计。配对评测沿用 Roadmap 的现有方案，通用 benchmark、样本管理系统与记忆分层按实际需求重启。（来源 2026-06-26 15:56 / 15:59 / 16:01、2026-07-03 16:17、2026-10-19 11:44 / 17:10 / 17:13）。[当前路线图](../../ROADMAP.md)
- **部署与运维** — PR preview、Docker Runner、Loop 与托管 daemon 已有归档交付，托管 daemon 仍待人工验收。日志迁移和 Issue worktree 孤儿库回收按运维触发条件重启。（来源 2026-06-14 21:16 / 21:17、2026-06-23 10:21、2026-06-26 15:57、2026-09-30 19:59、2026-10-05、2026-10-09 16:14）

## 可执行候选

本轮暂无建议新开的 PRD。已成形的工作见「已升级」，近期优先级以 [ROADMAP.md](../../ROADMAP.md) 为准。

## 冻结／有明确触发条件再启动

| 想法 | 重启条件与范围 |
| --- | --- |
| 多 Agent 拆分 PRD 并组队验收 | 大型 PRD 反复受单 Agent 串行执行拖累时，先用一个可独立拆分的案例比较交付收益，再确定 worktree 隔离、冲突集成与独立验收责任。（来源 2026-09-20 00:28） |
| IAR benchmark、工程证据与样本构建系统 | 现有配对评测无法回答某项具体决策，或部署样本反复因缺少原始输入、跨 commit 关联或验收记录而无法判分时，再扩展评测或采集能力。先沿用现有题集、口径与执行记录。（来源 2026-06-26 16:01、2026-10-19 11:44 / 17:10 / 17:13）。[M13 对照评测 PRD](../pending/P1-FEAT-20261019-180938-m13-keda-vs-claude-workflow-evaluation.md) |
| Issue worktree 孤儿数据库回收 | 孤儿库持续累积并造成磁盘或人工清理负担时，先扩展 dry-run 盘点。当前 Issue 新库名使用 `_kc_issue_`，历史记录使用 `_iar_issue_`，现有 GC 所有权匹配只认 `_wt_`。启动前明确新旧库名归属与活跃 worktree 判定。（来源 2026-10-05 · iar-issue-db-reclaim）。[建库代码](../../src/backend/core/use_cases/worktree_database.py) · [GC 脚本](../../scripts/shared/worktree/gc_worktree_databases.py) |
| `LOG_DIR` 迁移与日志能力升级 | 已有部署因目录配置歧义反复出现落盘问题时，再确定 `LOG_DIR`、`log_file`、托管进程日志与旧配置的迁移规则。结构化日志、关联键等在现有诊断方式无法满足具体场景时按项评估。（来源 2026-09-30 14:53 / 15:55 / 19:59）。[日志健壮性归档 PRD](../archive/P0-BUG-20260930-145323-logging-config-robustness.md) |
| 构想工作台与可替换客户端 | 保留已成形、未开工的 P2 PRD。出现需要反复对话并同步预览产物的真实使用场景，且现有 Console 流程无法满足时，再用默认工作台与一个自定义客户端验证接口。（来源 2026-11-17 13:40、2026-11-19 23:02、2026-11-20 00:19 / 00:24）。[构想工作台 PRD](../pending/P2-FEAT-20261120-012205-cli-first-concept-workbench.md) |
| 先交付后验证（`deliver-before-validation`） | 已从节点与工作流 PRD 移出，仅保留为待处理想法。等急需先接收代码或 Draft PR 的场景再次出现，再确定交付点、后台验证绑定的提交、恢复方式和失败处置。（来源 2026-11-26 22:53）。[相关工作流 PRD 的范围记录](../pending/P1-FEAT-20261126-214529-configurable-durable-workflows.md#11-non-goals) |
| 模型切换、记忆分层与外部审阅通知 | 分别等到额度耗尽反复中断任务且有可靠切换接口、现有 Memory 无法满足具体检索或复用场景、桌面系统通知与 GitHub 通知无法满足离机提醒时再评估。（来源 2026-06-15 00:41、2026-06-17 09:44、2026-07-03 16:17） |
| Review 基线新鲜度与证据文件名 | 基线更新反复导致漏审时，再验证审查输入的刷新和复审时机。固定文件名反复造成无效返工时，再评估以验证项和 manifest 对账的替代方案。（来源 2026-10-10 18:20、2026-10-14 02:18） |
| Loop 巡查计数 `unknown` | 复发且现有记录无法定位 GitHub 状态读取失败时，先保留失败原因，再决定是否跳过该轮建 Issue。当前配方仍会丢弃 `gh` 的 stderr。（来源 2026-10-23 05:09）。[排查指南](../../docs/guides/loop.md) · [巡查配方](../loops/keda-blocker-monitor-30m.md) |

## 待澄清问题

冻结项的待决问题随对应重启条件一起处理。本轮总结没有新增需要立即拍板的问题。

## 已升级

### 正式待办

- **CLI 可配置节点与可恢复工作流** — 创建和管理节点与工作流，覆盖现有 Runner 能力，并支持监控、恢复和审计。（来源 2026-11-13 10:23、2026-11-19 23:15）。[工作流 PRD](../pending/P1-FEAT-20261126-214529-configurable-durable-workflows.md)
- **Idea Inbox 双模型生成与复核** — 写作、独立复核与人工批准共用一套草稿流程，支持网页、CLI 和签名 inbound。（来源 2026-10-12 19:19）。[双模型 PRD](../pending/P1-FEAT-20261013-013037-two-stage-prd-generation.md)

### 已归档

| 想法 | 归档记录 |
| --- | --- |
| Console「计划 vs 产出」进度（2026-10-10 09:25） | 已验收，按合并态追认。[Console 进度 PRD](../archive/P1-FEAT-20261010-093054-console-prd-progress-signals.md) |
| Agent Runner 子进程环境净化（2026-09-28 23:17） | 已交付。9 月旧草稿的元数据仍是 `pending-review`，以正式归档 PRD 为准。[归档 PRD](../archive/P1-BUG-20260928-232844-agent-runner-child-env-sanitize.md) · [旧草稿](prd-drafts/20260928-231700-agent-runner-child-env-sanitize.md) |
| 托管 daemon 部署与自动资源清理（2026-10-09 16:14） | 已归档，待人工验收。[托管 Runner PRD](../archive/P1-FEAT-20261009-161453-kc-hosted-runner-deployment.md) |
| 夜间任务批次总 PR（2026-10-09 16:05） | 已验收。显式批次产出唯一总 PR，成功后关闭被取代的来源 PR。[批次总 PR 的 PRD](../archive/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.md) |
| 生命周期 Agent／模型／推理深度统一设置（2026-09-18 11:19） | 已验收，按合并态追认。[生命周期设置 PRD](../archive/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md) |
| 统一运行时看板与桌面进度窗（2026-10-12 10:04） | 已验收，按合并态追认。[运行看板 PRD](../archive/P1-FEAT-20261012-103222-unified-runtime-dashboard.md) |
| 人工处理提醒（2026-06-15 00:41） | 桌面 App 系统通知已归档，待人工验收。离机提醒仍在冻结池。[桌面通知 PRD](../archive/P2-FEAT-20261120-145338-companion-human-action-notifications.md) |
| 早期路线图、Inbox 与验收基础（2026-06-14 19:07–20:24、2026-06-15 00:50） | [路线图](../archive/P1-FEAT-20260614-200054-frontend-prd-roadmap.md) · [依赖调度](../archive/P1-FEAT-20260703-105330-roadmap-continuous-scheduling.md) · [跨平台 Inbox](../archive/P1-FEAT-20260614-203810-frontend-idea-inbox-cross-platform.md) · [结构化验收证据](../archive/P1-FEAT-20260614-203811-structured-validation-evidence.md) |
| PR preview、Docker Runner 与 Loop 调度（2026-06-14 21:16 / 21:17、2026-06-23 10:21、2026-06-26 15:57、2026-07-03） | [PR preview](../archive/P1-FEAT-20260614-224914-pr-preview-deployment.md) · [Docker Runner](../archive/P2-FEAT-20260707-141659-iar-runner-docker-containerization.md) · [Loop 调度](../archive/P2-FEAT-20260623-102437-iar-loop-scheduled-recurring-tasks.md) · [每夜整理](../archive/P1-FEAT-20260702-134625-nightly-cleanup-loop.md) |
| 运行记录、恢复、Memory 与 Token 统计（2026-06-15 00:48 / 09:30、2026-06-26 13:39–15:59、2026-07-31 15:37） | [attempt 历史](../archive/P1-FEAT-20260626-174127-agent-runner-attempt-history-persistence.md) · [会话恢复](../archive/P1-FEAT-20260930-225000-daemon-crash-reconciliation-session-resume.md) · [调用追踪](../archive/P1-FEAT-20261008-015223-agent-invocation-tracing-and-stall-diagnosis.md) · [Memory](../archive/P1-FEAT-20260626-093933-agent-runner-memory-persistence.md) · [Token 统计](../archive/P1-FEAT-20260930-212702-agent-token-usage-stats.md) |

## 生命周期盘点

- **已修复 · Verifier stdout 提前截断。** `68789332` 让 `_communicate_with_activity_tracking()` 先等待子进程退出，再按共享截止时间等待管道线程。现有回归用例覆盖持续输出进度、超过旧等待窗口后才写出最终 stdout 的场景。原始条目仍标为 `pending`，本页按当前实现收录为已解决。（来源 2026-09-29 10:57）。[捕获实现](../../src/backend/infrastructure/process_runner.py) · [回归用例](../../tests/test_process_runner.py)
- **另有本机浏览器入口。** 同一历史条目还记录了 Codex 沙箱内直接启动 Chromium 失败。`a50d92de` 新增 `kc browser`，受支持的本机可写 Agent 调用（含独立 verifier）可以使用宿主启动的 Chromium。原 Issue #39 由用户接管，本次盘点基于当前实现和文档。（来源 2026-09-29 10:57）。[浏览器使用与适用范围](../../docs/guides/browser-validation.md)
- **已落实 · 路线图瘦身。** 根目录路线图已收敛为「现在、下一步、后续」，并链接独立的路线历史页。（来源 2026-10-16 02:07）。[路线图](../../ROADMAP.md) · [路线历史](../../docs/project/roadmap-history.md)
- **已落实 · 录屏证据与 PR 汇总。** 当前代码有录像健康检查、报告内嵌检查和 PR 评论中的录像列表与大小汇总。（来源 2026-10-15 20:42）。[证据检查](../../src/backend/core/use_cases/agent_runner_video_evidence.py) · [PR 证据发布](../../src/backend/core/use_cases/agent_runner_validation_publication.py)
- **其他已有处理。** PR 标题开场白清理、`init` 配置测试隔离及默认 `autopilot`、目录选择与 attempt 起止时间沿用已处理记录。（来源 2026-07-09、2026-07-31 15:37、2026-09-18 11:19、2026-10-05 · iar init 测试隔离失效 / 坏 PR 标题）
- **已放弃。** SIGTERM checkpoint，原条目保留了明确决定。（来源 2026-06-30 09:52）
