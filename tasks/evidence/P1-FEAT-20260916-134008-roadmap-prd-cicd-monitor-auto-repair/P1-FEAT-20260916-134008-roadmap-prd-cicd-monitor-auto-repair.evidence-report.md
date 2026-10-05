# Evidence Report（执行侧自检）

- PRD: P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair（GitHub Issue #197）
- 分支: feat/backlog-cicd-auto-repair
- 日期: 2026-10-05

## 结果摘要

| RV | 结果 | 证据 |
|---|---|---|
| rv-1 | fake 状态机序列通过；live webm 待 opt-in | tests/test_backlog_ci_delivery.py（enqueue 幂等 / gate 耗尽 / 零 job 不修） |
| rv-2 | 六组合矩阵 + 写后 fresh readback 通过 | 同上 test_effective_policy_matrix / test_editor_auto_repair_ci_roundtrip / test_api_global_toggle_fresh_readback |
| rv-3 | 4 passed（real UI / fake GitHub boundary） | rv-3-backlog-ci-problems.png；e2e spec `tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts` |
| rv-4 | 17 passed | rv-4-manual-repair-and-policy-tests.txt |
| rv-5 | 2975 passed；lint/mkdocs/前端构建全绿 | rv-5-gates-summary.txt |
| rv-6 | 命令面 + 守卫通过；真实 CLI 演练待补 | tests/test_iar_operator_skill.py |

## 披露的限制 / mock 边界

- rv-1/rv-2 的真实 daemon + 真实 GitHub sandbox 多轮演练为 opt-in，未在本环境执行；
  同一行为序列由 fake GitHub 状态机覆盖（PRD rv-1 mock_boundary 允许）。
- rv-3 的截图来自真实 Next.js 页面，Backlog API 由 Playwright route 提供确定性数据
  （与既有 backlog 系列 e2e 同一边界）；GitHub 边界不可达。
- rv-6 未在真实 CLI 进程中执行端到端命令（需注册仓库上下文）。
- `.env.example` 无新增变量（本功能不需要新环境变量）。
- pnpm 构建在本 worktree 曾因 corepack 11.3.0 的 verify-deps + ignoredBuilds 状态报错；
  清理 node_modules/.modules.yaml 的 ignoredBuilds 后构建通过（本机环境问题，非交付物问题）。
