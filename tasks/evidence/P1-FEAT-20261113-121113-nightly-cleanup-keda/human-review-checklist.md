# Human Review Checklist

当前状态：PRD 为 `🧍 待人工验收`，人审勾选仍留空。请在原始输出发布到 `kc-evidence/issue-366` 并附入 PR 评论、PR 分支同步后，再依据下方最终差异确认是否接受本轮 CI 修复及额外的慢主机测试上限。

## 1. 当前分支和实际差异

- 本地 `issue-366` 已 rebase 到远端 `main`：`51461449cb1069113b3d6a827297f92c16bcff2f`。本地 HEAD 为 `1bfc64e25efbe1edb393bc722ff935c8d9f380e3`。
- 重基后的 base 已含 `tests/test_kedacode_operator_skill.py` 中的 `--local` 白名单修正；该文件不再是当前 PR 差异的一部分。
- 当前对比 base 的 7 个文件：新增归档 PRD、四个 Markdown evidence/review 文件、删除旧 pending PRD，以及 `tests/test_process_runner.py` 的测试超时调整。
- 唯一测试代码差异将总墙钟上限从 20 秒提高到 60 秒；`inactivity_timeout=3`、子进程进度输出及 `FINAL VERDICT` 断言保持不变。

## 2. 验证证据

- 原始 CI 触发：[run 44548773786](https://github.com/ZataZhang/keda/actions/runs/44548773786) 在固定触发 SHA `4c58ad0df1b1437ea2336303a64d486ada00ea89` 因 `--local` 未登记而失败。该修正现在已在重基 base `51461449` 中。
- rv-1：`test_packaged_skill_command_examples_match_cli_help` 与 runtime-schema 测试在重基后各 `1 passed`。本地原始输出分别为 `rv-1-cli-skill-flag-check.txt`（SHA-256 `ce2bf1437f64ba9f0ea55993b55e4243022f4d84b05f68f562630fac33dd1b8e`）和 `rv-1-cli-schema-check.txt`（SHA-256 `86cdb73759396920fcafec463b6f0bccbba4798ad2bdca6a738a9d7870b3083d`）。
- rv-2：20 秒版本在重基后的本地核心测试运行中以 `TimeoutExpired after 20s` 失败；该全量选择运行有 `5523 passed, 1 skipped, 1 failed`。60 秒版本的直接复跑 `1 passed in 22.89s`；之后 testmon 选择的 `just test` 通过（`1 passed, 68 deselected`），且 full-lint 阶段通过。原始通过输出 `rv-2-process-runner-timeout-check.txt` 的 SHA-256 为 `2dee2645d4a4fdd027fdf38bc33c194fca9c634fd1061ae2c8a2e8cb03d4ecd8`。
- `just lint --reuse` 与 `git diff --check 51461449` 已通过；严格 MkDocs 构建和 PRD checker 的最终结果见证据报告。

三个 raw output 目前都只在本地忽略目录中，未发布到 `kc-evidence/issue-366`。用户要求不得 push，因此它们还没有达到人审前的远端呈递条件。

## 3. Reviewer findings 状态

- **Rebase：本地已完成。** 远端 `issue-366` ref 仍在旧 SHA `2c1042485b0980cfc33a2ab315ea0fd8969737a4`，因为禁止 push。
- **rv-1 原始输出：未发布。** 两份新输出和哈希已保留；需要 runner 发布并附 PR evidence comment。
- **PR 标题：未更新。** 将标题改为 `cleanup(ci): stabilize slow-host process runner test` 的 GitHub connector 请求被自动审批门禁拒绝，提示 `approval policy is never`。
- **PR body：本地材料已刷新，远端仍旧。** PR body 仍引用旧 SHA `525a3549` 与 99-file 列表。应在重基分支发布后按真实最终差异更新。
- **process-runner timeout finding：争议。** 按 20 秒恢复后，重基验证实际复现超时；60 秒修复后的定向测试通过。这个额外 CI 可靠性改动已同步写入 PRD 和验证记录，供人工确认范围。
- **Issue comment 与 `scope/ci` label：未完成。** Issue #366 当前缺少该 label 和本轮 comment，仍需 runner 处理。
- **独立 rv-1：已解决 / 旧 finding 过期。** 原 verifier report 已有独立 PASS；重基后两项相关测试也已重新通过。

## 4. 人工确认

- [ ] 我接受当前 CI 修复记录、`timeout=60` 的测试可靠性调整，以及 docs checkpoint 继续保持未确认。
- [ ] 我确认没有将本地测试结果描述为远端 CI 结果；所有未发布输出和远端 PR/Issue 更新均明确保留为待办。
