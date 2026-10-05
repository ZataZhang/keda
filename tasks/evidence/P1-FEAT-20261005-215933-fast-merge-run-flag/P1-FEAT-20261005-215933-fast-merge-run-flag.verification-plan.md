# Realistic Validation 验证计划 — `iar run --fast-merge`

PRD: `tasks/pending/P1-FEAT-20261005-215933-fast-merge-run-flag.md`
分支: `issue-207` @ `f44a3b4f`

## 验证目标

证明一次性旗标 `--fast-merge` 在真实执行链路中：builder 提交后立即开带标注的 Draft PR 且跳过 Phase 4.5 的 rv_reexec/verifier 两层门禁；不传旗标时默认门禁逐字节不变；stack 依赖 Issue 在 agent 启动前以 USAGE(2) 拒绝；CLI 表面（help/schema/退出码）契约完整。

## Oracle 分层（风险序 rv-1 → rv-3 → rv-2 / rv-4）

| id | 保真度 | 真实入口 | mock 边界（PRD mock_boundary 允许范围） | 判别力负控 |
|---|---|---|---|---|
| rv-1 (R2, human) | 进程内真实 `run_once(fast_merge=...)` 执行循环 + 发布链路 | GitHub 客户端 + agent 子进程 | 默认 run 无 marker/skip-log（红基线） |
| rv-2 (R1, verifier) | 真实 pytest：三个既有验证测试文件 + fast_merge 门禁序列差分 | 无（仓库既有 harness） | 移除旁路 → ['evidence'] 短序列断言变红 |
| rv-3 (R2, verifier) | 真实 `run_run_command` 派发路径 | 仓库解析/gh 认证/daemon 锁被中和，stack 判定与 parse_dependency_marker 为真 | 去掉 stack 标记 → 派发到达 target（不抛错） |
| rv-4 (R1, verifier) | 真实已安装 `.venv/bin/iar` help/schema/退出码 + 契约测试 | 无 | 移除旗标定义 → help/schema present=False |

rv-1 的 negative control 由 rv-2 承担（PRD §Realistic Validation 负例说明）：同一场景去掉旗标即 RED，未为制造负例改动任何生产代码。

## 证据绑定最终代码树

- 每个 RV 脚本（capture/setup/oracle）位于 gitignored 的 `.iar/evidence/scripts/`，**不进入代码 diff**（runner 会拒绝 RV 脚本混入变更集）。
- 原始 `.txt` 输出与 `evidence.json` manifest 同样留在 `.iar/evidence/`（gitignored）；keda 复跑对最终树断言 exit 0 + `stdout_assertions` 精确子串。
- 冻结凭证：`git diff HEAD -- src tests | sha256sum = 58e1bed3698ad7b5eca6146785be11eb28ae34ef3e822b99942d53e402f1289c`（PR 合并前 src/tests 若有变更须重采）。
