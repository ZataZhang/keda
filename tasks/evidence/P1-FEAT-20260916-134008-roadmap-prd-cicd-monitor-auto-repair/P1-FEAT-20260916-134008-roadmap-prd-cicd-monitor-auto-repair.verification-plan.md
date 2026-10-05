# Verification Plan（执行侧）

对应 PRD §7.6 rv-1..rv-6。执行环境：macOS 本地 worktree，无 GitHub sandbox 凭据。

- rv-1 / rv-2（live sandbox / 真实 console 录屏）：**opt-in 待补**。fake GitHub 状态机已在
  `tests/test_backlog_ci_delivery.py` 覆盖同一序列（多轮去重、耗尽、零 job 不自动修、
  六组合 effective 矩阵、幂等手动修复），负控（全局关闭时 gate 拒绝）同样在测。
- rv-3：真实 `/app/backlog` 生产边界 + Backlog API 确定性 fake（Playwright route），
  `just e2e tests/workflows/backlog-cicd-auto-repair.no-auth.spec.ts` → 4 passed；
  截图 `rv-3-backlog-ci-problems.png`（标注：real UI / fake GitHub boundary）。
- rv-4：`uv run pytest -o addopts="" tests/test_backlog_ci_delivery.py -k
  "manual_repair or idempotent or exhausted or matrix or marker" -v` → 17 passed。
- rv-5：`CI=true just test all` → 2975 passed, 1 skipped；`just lint --full`（SKIP=check-test-flag）全绿；
  `uv run mkdocs build --strict` 通过；`frontend-public` typecheck + build 通过。
- rv-6：CLI 三命令已实现并接入 dispatch（`iar backlog ci status|policy|repair`），
  `tests/test_iar_operator_skill.py` 守卫命令示例与 CLI 一致；**真实 CLI 进程端到端演练未跑**，
  待 verifier / 人工验收时按 §7.6 real_entry 补。
