# Idea: keda 启动 agent 的选型思路

日期：2026-09-11

## 背景

keda（kedacode / iar）需要一个"启动 agent"来执行 issue。两个候选方案：

- **方案 A：复用现有 agent CLI**（claude / codex / opencode / pi CLI 等），keda 只做编排。
- **方案 B：自建 agent**——基于 `@earendil-works/pi-agent-core`（zata-shared 的 `Agent/runtimes/pi-task-agent` 骨架）。

## 结论

**选方案 A：复用现有 CLI，不自建 agent。**

keda 的价值在编排层（issue 队列、worktree 隔离、标签路由、失败降级、多方辩论），不在 agent 内部（文件编辑、shell 执行、上下文压缩、沙箱）。agent 内部是另一个赛道的竞赛：

| 维度 | 复用 CLI | 自建 (pi-agent-core) |
|---|---|---|
| 工具质量（编辑/搜索/沙箱） | 厂商团队持续打磨，开箱即用 | 全部自己扛，难以追平 |
| 模型生态变化 | CLI 升级自动跟上 | 每个 provider 的 API 变动都变成维护负担 |
| 认证 | 用户各自的 claude/codex 登录 | 自己管 key、限流、重试 |
| 输出协议差异 | 主要成本，但适配层 PRD 已收敛为"一段配置注册块" | 单一协议，但只此一家 |
| 用户迁移成本 | 用户已有 claude/codex 订阅，零成本 | 需为新 agent 单独配模型 |

关键前提：`tasks/pending/P1-FEAT-20260911-010513-agent-cli-adapter-layer.md` 的适配层落地后，接一个新 agent（如 opencode、pi CLI）的边际成本就是写一段声明式注册块，方案 A 最大的劣势（协议碎片化）正是该 PRD 要解决的。

## 自建 agent（pi-task-agent）的定位

骨架不废弃，角色调整为：

1. **实验性注册项**：作为第 5 个 agent 纯配置接入，用于需要自定义工具或 `AGENT_RUNTIME_*` 便宜模型（如 qwen）的低风险批量 issue 场景。
2. **对冲**：厂商 CLI 出现破坏性协议变更或限制自动化调用时的退路。
3. **学习载体**：保留 pi-agent-core 的探索价值，但不承担 keda 主路径的质量责任。

注：接入时 pi-task-agent 需补齐 keda 的 CLI 契约——非交互 stdin prompt、可解析输出流（JSON Lines 事件流或先 plain）、只读工具开关、确定性退出码。

## 下一步

- 优先落地 agent CLI 适配层 PRD，以 pi CLI 作为第 4 个 agent 验证。
- 评估接入 opencode（开源、多模型、协议稳定），作为"不锁厂商"诉求的更优解。
- "不被单一 agent 绑定"用复用 + 注册表即可满足，不需要自建运行时。
