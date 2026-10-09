# Idea Inbox — 总结（AI 派生，可重写；事实以 ideas.md 为准）

_最后更新：2026-10-09 16:30_

## 主题聚类

- **Backlog、Roadmap 与 Idea Inbox** — 路线图、前端 idea inbox、依赖调度及 backlog 控制已由归档任务覆盖；loop 定时执行也已落地。近期“选目录添加仓库”已有页面实现；统一查看/编辑生命周期配置由当前 pending PRD 覆盖。（来源：2026-06-14 19:07, 2026-06-14 19:41, 2026-06-14 19:53, 2026-06-14 20:15, 2026-06-15 00:50, 2026-06-23 10:21, 2026-09-18 11:19；归档：`P1-FEAT-20260614-200054-frontend-prd-roadmap.md`、`P1-FEAT-20260614-203810-frontend-idea-inbox-cross-platform.md`、`P1-FEAT-20260703-105330-roadmap-continuous-scheduling.md`、`P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md`；待办：`tasks/pending/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md`）

- **执行器可观测性、会话与恢复** — 运行输出、attempt 历史、会话恢复、调用追踪等已有归档实现；子进程环境净化仍在 pending。另有 verifier 长任务的 stdout 捕获缺陷，当前代码仍可见提前返回路径，尚未找到覆盖它的归档任务。（来源：2026-06-15 00:48, 2026-06-15 09:30, 2026-06-26 13:39, 2026-06-26 15:55, 2026-07-31 15:37, 2026-09-28 23:17, 2026-09-29 10:57；归档：`P1-FEAT-20260626-174127-agent-runner-attempt-history-persistence.md`、`P1-FEAT-20260930-225000-daemon-crash-reconciliation-session-resume.md`、`P1-FEAT-20261008-015223-agent-invocation-tracing-and-stall-diagnosis.md`；待办：`tasks/pending/P1-BUG-20260928-232844-agent-runner-child-env-sanitize.md`）

- **验证证据与 Review** — 证据结构、逐项格式、独立 verifier、污染检测等已有归档任务。2026-09-29 记录的 verifier stdout 丢失是不同问题，不能因证据门禁已有 PRD 就视为已解决。（来源：2026-06-14 20:24, 2026-09-29 10:57；归档：`P1-FEAT-20260614-203811-structured-validation-evidence.md`、`20260611-143619-validation-evidence-per-item-format-check.md`、`P1-FEAT-20260628-041733-realistic-validation-independent-verifier-gate.md`、`20260625-095725-prd-realistic-validation-evidence-contamination-detection.md`）

- **Memory、Token 与执行评估** — memory 持久化、token 统计已有归档实现；“记忆分层”和 IAR benchmark 尚无清晰验收口径，建议等出现持续使用痛点或具体评估场景再拆分。（来源：2026-06-26 15:56, 2026-06-26 15:59, 2026-06-26 16:01, 2026-07-03 16:17；归档：`P1-FEAT-20260626-093933-agent-runner-memory-persistence.md`、`P1-FEAT-20260930-212702-agent-token-usage-stats.md`、`P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`）

- **部署与通知** — PR preview 和 Docker runner 有归档任务；2026-10-09 已明确远端托管保持 GitHub Issue/PR 代码交付、本地用户继续免费，首期数据库测试交给 CI，并提出自动资源清理与磁盘保护。专用 VM、费用责任及数据保留仍待人工确认；对应 PRD：`tasks/pending/P1-FEAT-20261009-161453-kc-hosted-runner-deployment.md`。审阅邮件没有独立需求边界，也需与 GitHub 自带通知比较收益。（来源：2026-06-14 21:16, 2026-06-14 21:17, 2026-06-15 00:41, 2026-06-26 15:57；归档：`P1-FEAT-20260614-224914-pr-preview-deployment.md`、`P2-FEAT-20260707-141659-iar-runner-docker-containerization.md`）

- **模型切换、定时任务与多 Agent** — Loop 定时任务已有归档实现；cc-switch 自动切换缺接口调研。PRD 拆分为多任务并由 Agent 组验收尚无实现，其并发、隔离和集成边界也未确定。（来源：2026-06-17 09:44, 2026-06-23 10:21, 2026-09-20 00:28；归档：`P2-FEAT-20260623-102437-iar-loop-scheduled-recurring-tasks.md`）

- **日志与资源卫生** — 日志配置健壮性已修复；`LOG_DIR` 与 `log_file` 的行为差异是兼容性决策，改动前需明确迁移范围。IAR Issue worktree 数据库未纳入现有 GC；SIGTERM checkpoint 已明确选择暂不做。（来源：2026-06-30 09:52, 2026-09-30 14:53, 2026-09-30 19:59, 2026-10-05 · iar-issue-db-reclaim；归档：`P0-BUG-20260930-145323-logging-config-robustness.md`）

## 可执行候选

- **Verifier 长时间运行时 stdout 被提前截断/丢失** → 建议优先升级为 `P1-BUG` PRD 或窄范围 bugfix。`_communicate_with_activity_tracking()` 在两个 reader `join(timeout=5)` 后直接拼接并返回，长时 verifier 可能还未写出最终 verdict；原记录含 `response chars: 0` 的现场证据，失败会阻断验收并触发重复恢复。范围应聚焦“等子进程完成后收全输出”，并与 Codex 浏览器沙箱限制分开验证。（来源：2026-09-29 10:57；当前代码：`src/backend/infrastructure/process_runner.py`）

- **IAR Issue worktree 孤儿数据库回收** → 低优先级 `P2` 候选。优先扩展现有 dry-run GC 的识别与存活判定；不建议直接在 Issue 关闭时自动 DROP，除非先解决并发 worktree 风险。只有在孤儿库持续累积时再排期。（来源：2026-10-05 · iar-issue-db-reclaim）

- **多 Agent 拆分 PRD 并组队验收** → 可先做决策/范围型 PRD，不直接承诺实现。触发条件：大型 PRD 经常因单 Agent 串行执行成为瓶颈。需先确定任务独立性、worktree 隔离、冲突集成、失败重试与验收组职责。（来源：2026-09-20 00:28）

## 待澄清问题

- 2026-09-29 的 Issue #39 当时决定由用户直接接管。是否仍希望 Keda 修复通用 verifier stdout 捕获缺陷？（来源：2026-09-29 10:57）
- 多 Agent 拆分何时触发，是否需要独立 worktree，以及谁负责冲突集成？（来源：2026-09-20 00:28）
- 是否持续产生足够多的 IAR Issue 孤儿库来值得排期？（来源：2026-10-05 · iar-issue-db-reclaim）
- cc-switch 是否提供稳定接口；benchmark 的完成度由谁评定、人工介入时间如何计量？（来源：2026-06-17 09:44, 2026-06-26 16:01）
- 是否仍需要审阅邮件通知，还是现有 GitHub 通知已满足提醒场景？（来源：2026-06-15 00:41）
- `LOG_DIR` 是否需要改变现有落盘路径；如需要，如何迁移依赖旧路径的仓库？（来源：2026-09-30 19:59）

## 已升级

- **Agent Runner 子进程环境净化** → `tasks/pending/P1-BUG-20260928-232844-agent-runner-child-env-sanitize.md`。（来源：2026-09-28 23:17）
- **托管 daemon 部署与自动资源清理** → `tasks/pending/P1-FEAT-20261009-161453-kc-hosted-runner-deployment.md`。待确认每客户隔离/模型费用边界和数据保留周期。（来源：2026-10-09 16:14）
- **路线图、前端 Idea Inbox、验证证据、PR preview/Docker、Loop、Memory、Token 统计、会话恢复与调用追踪** → 已有对应归档 PRD，详见上方主题来源。
- **PR 标题误用 Agent 开场白** → 当前 `generated_content.py` 已剥离对话式开场白，并有回归测试；无需再开 PRD。（来源：2026-10-05 · 坏 PR 标题）
- **`iar init` 测试污染真实配置** → 当前回归测试显式检查仓库 `config.toml` 不变；测试隔离问题已有修正，无需再开 PRD。（来源：2026-10-05 · iar init 测试隔离失效）
- **`iar init` 缺少 `[agent_runner.autopilot]`** → 当前初始化构造器显式提供 `AgentRunnerAutopilotSettings()`；无需再开 PRD。（来源：2026-07-09）
- **目录选择与 attempt 起止时间呈现** → 当前仓库页面已有目录选择入口，Backlog 生命周期详情展示开始/结束时间；无需重复立项。（来源：2026-09-18 11:19, 2026-07-31 15:37）
- **夜间任务批次总 PR** → 已升级为 `tasks/pending/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.md`；按单次 opt-in 批次生成唯一总 PR，扩展多 PRD 合并验收，并在成功后关闭来源 PR。（来源：ideas.md 2026-10-09 16:05；本轮确认：总 PR 是唯一正式 PR、来源 PR 自动关闭并标注取代。）
