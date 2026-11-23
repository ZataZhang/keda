# Roadmap

KedaCode 以 `kc` CLI 为核心。任何具备终端和仓库权限的 Agent 都能通过同一套命令执行、验证和交付任务。目标是降低每次合格交付的总成本，尽量减少中途人工接管。

核心能力优先通过 CLI 提供。内置 Console 是开箱可用的默认前端，用户可以用独立前端插件调用 `kc`，按自己的工作流组织页面。

## 现在 · 验证单 Agent 交付

在同题、同模型和相同时间预算下[对照 Keda 工作流与原生 Agent][comparison]，评估验收通过率、稳定性和每次合格交付的成本。在真实任务中记录返工与人工接管，验证能否在更少干预下交付合格结果。题集、分组和判分口径见[详细评测方案][improvement]。

## 下一步 · Agent Teams（数字员工）

把可重复的工作交给职责明确的 Agent 团队，通过 CLI 拆分任务、执行、交接和复核。先选一种适合分工的任务，与单 Agent 在相同总预算下对照。用质量、成本和人工接管数据决定扩展范围。

## 持续 · 维护 Issue 到 PR 的交付链

维护 CLI、Runner 和[随 CLI 分发的管理终端][console-delivery]，优先修复影响正常交付或评测可信度的问题。

[产品边界与候选方向][boundaries] · [路线历史][history]

[comparison]: tasks/pending/P1-FEAT-20261019-180938-m13-keda-vs-claude-workflow-evaluation.md
[console-delivery]: tasks/archive/P1-FEAT-20260910-111901-iar-console-bundled-web-terminal.md
[improvement]: docs/project/agent-improvement.md
[boundaries]: docs/project/product-boundaries.md
[history]: docs/project/roadmap-history.md
