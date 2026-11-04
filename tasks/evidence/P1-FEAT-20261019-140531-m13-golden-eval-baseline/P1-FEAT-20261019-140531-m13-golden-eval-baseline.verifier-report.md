# 独立验证状态与执行器代码复核

## Verdict

**Independent verifier: PENDING RUNNER — no independent PASS is claimed.** 当前会话未提供独立 verifier 运行入口；本报告记录执行器的代码与证据复核，不能替代 runner 的独立结论。PRD 已由 runner 放在 `tasks/archive/`，顶部状态为 `🧍 待人工验收`，四个 `Human-Confirmed` 项保持未勾选。

实现源码的 verified Git tree（从当前 HEAD 与工作树重算，排除 PRD、证据目录和 `.agent-runner/commit-request.json`）为 `257da6a287b2ffc4df327186bcad66022a0fb4e3`。RV-2 与 RV-5 复跑命令输出并断言该 tree；RV-1/3/4/6 的输出采集于前次源码 tree（`fb21a75247599f6d64d1162c9c6e113b0ead80be`，其采集基线为 `59049dec0d2accd91d8448e1bf25c48711452a0e`），与当前实现相比差异为 README 来源映射文档与本次 review 新增的校准创建门禁；workspace/execution/checks 与 suite 材料未变，按 PRD 重跑规则继续适用。

## Executor review findings

Pre-PR 代码审查曾发现并修复以下四项缺陷：全局关闭 runner 复跑、校准未绑定已解析的逐题 commit/tree、重复预设会触发账本唯一键冲突、UI 将任意 `@` 前缀预设误判为校准候选。上一次 follow-up review 另发现并修复一项文档缺口：PRD FR-8 要求的 12 题来源映射此前只存在于 `rv-2-seed-selection.json`，未写入 `tasks/evaluation-suites/README.md`；现已补齐来源表。本轮 follow-up review 再发现并修复：旧格式 suite 或不含 `gold_patch` 的题目也能创建 `@noop` / `@gold` 校准 run，随后以执行故障告终；现在 `build_run_snapshot` 在创建阶段拒绝这两种校准请求并逐条说明原因，另有负向测试覆盖。同时修正 ROADMAP 对已归档 PRD 的过期路径引用与 `CalibrationResults` 的错误注释。evidence tree 已更新为 `257da6a287b2ffc4df327186bcad66022a0fb4e3`，并在该 tree 上复跑 RV-2 与 RV-5。定向测试与真实 API RV 已复核；未解决的产品缺陷为零。关键边界按源码与证据核对如下：

- API 路由依赖方向保持在 core；Git、文件与进程副作用位于 evaluation engine/workspace/checks，SQLite 迁移在 persistence。
- 校准和真实候选共用执行单元；候选快照为无 remote 的单提交仓库；隐藏/判分文件与依赖清单在 Agent 结束后恢复，再运行固定 `uv-pytest` 配方。
- 校准由逐条结果、suite 内容 hash 和当前每题 commit/tree 共同派生；run 创建拒绝重复执行/评审预设；重复索引参与唯一约束，迁移保留旧行与评分外键。
- 结果输出无合成总分与阈值；故障结果与通过执行但判分失败分开累计。
- RV1–RV6 负控与真实入口结果见 `evidence.json` 和对应 `rv-*.txt`。验证计划记录了执行边界、结果和局限。

代码复用自检发现三个扩展的编排签名超过 4 个参数：`build_run_snapshot`（9）、`_execute_cell`（9）和 `create_run`（5）。本次 review follow-up 已将这些签名收敛为类型化 `BuildRunSnapshotInput`、`_EvaluationCellContext` 与 `EvaluationRunCreateRequest`；API 到 core 的依赖方向保持不变，现有入口与负向校验均经回归测试。参数收敛 finding 已解决。

以上是执行器复核，不是独立 verifier 的 PASS。独立 verifier 应从干净视角复核三条 R2/R3 证据链的关键值来源、跨越边界、禁止旁路、新进程/连接的持久态探针和最终 tree；还应核对 PRD checklist 与来源材料。verifier 若发现代码缺陷，按 finding 更新实现与相关 RV 证据；若 verifier 环境无法完成审查，应报告 `REVIEW_INCIDENT / INCONCLUSIVE`，不得改写为产品失败或 PASS。

## Validation limits and risks

- `just test all` 在前一源码 tree `c90a803f09b4cae2657d48567c92d70e74c2d91c` 完成 5,197 passed、1 skipped。源码 tree `59049dec0d2accd91d8448e1bf25c48711452a0e` 的复跑约到 83% 时被 runner execution timeout 中断，期间未见测试失败，但不能算通过；该 tree 的评测相关聚焦测试 73 passed。最终 tree `257da6a287b2ffc4df327186bcad66022a0fb4e3` 增加了校准创建门禁、来源映射和两处文档/注释修正；本轮在该 tree 上重跑评测相关聚焦测试 59 passed，`just test` 通过，全量测试由 runner 最终门禁复核。
- `just lint --reuse`、`just lint --full`、前端 TypeScript 检查与 `uv run --frozen mkdocs build --strict` 均通过。
- `frontend-public` `pnpm build` 与 `just console-sync` 通过；真实 FastAPI 静态 Console E2E 逐项运行 5 个 no-auth 用例，5/5 通过，其中包含普通 `@foo` 预设边界回归。普通 Next dev server 入口在当前 macOS sandbox 中曾遇到 route root / Chromium Mach-port 限制，故 E2E 使用仓库证据 harness 的生产静态入口和单进程 Chromium。
- 真实模型效果实验按 PRD 明确为 opt-in，未调用真实 Agent 模型；使用 fail-loud 确定性 CLI 探针覆盖了 Agent 调度接缝。
- 公开 keda 仓库联网泄漏与模型记忆无法由快照隔离消除，仍按 PRD 披露；不可逆的 SQLite 唯一约束重建由人工决策项覆盖。

## Runner-owned next gates

1. 在独立环境生成并签发 verifier verdict；在 verdict 前本报告不表示 PASS。
2. 运行必需 CI / repo 发布门禁，确认最终 tree 与上方 verified tree 等价。
3. PRD 归档已由 runner 完成；后续 PR/evidence publication 仍由 runner 按发布流程处理。执行器没有创建 PR、推送分支或提交 Git。
