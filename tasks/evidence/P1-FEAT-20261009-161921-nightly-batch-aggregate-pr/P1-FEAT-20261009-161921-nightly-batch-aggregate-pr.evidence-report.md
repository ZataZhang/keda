## 人审导航 / Human Review Navigation

实现 PR / CI 链接：**待 runner 发布**；本执行器未创建 PR、未访问 CI，也未访问 GitHub。以下本地证据和源码入口已由 executor 对照 §9.1 核对。请审阅时只把本报告中的本地结果视为本地证据；真实 GitHub 行为仍未验证。

| 人审点 | 呈递物 / 打开方式 | 自检：预期观察 |
|---|---|---|
| 总 PR 是否把正文列出的唯一 PRD 集声明为共同验收范围。 | 源码：[v2 body contract](../../../src/backend/core/use_cases/agent_runner_pr_body_contract.py)；终端证据：`open "/Users/zata/code/keda/.iar-worktrees/issue-258/tasks/evidence/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr/rv-3-body-contract.txt"`；复核说明：[human-review-checklist.md](human-review-checklist.md)。 | 完整、去重、排序一致的本地 fixture 通过；缺项、重复项、额外项、来源 mismatch 和授权句缺失被拒绝。真实 GitHub PR body 与实际合并验收仍未验证。 |
| 来源 PR 仅在组合验证和总 PR required checks 成功后关闭，且保留可重试记录。 | 源码：[聚合生命周期](../../../src/backend/core/use_cases/agent_runner_batch_aggregate.py)；本地 boundary 记录：[rv-4 未验证说明](rv-4-external-github-unverified.txt)；复核说明：[human-review-checklist.md](human-review-checklist.md)。 | 关闭调用位于总 Draft PR 的 body/head/base 与 checks 均验证成功之后；真实 PR 状态、评论、required checks 和远端 branch 保留未验证。 |
| 9.1 呈递面完整、可复核且未将未验证行为描述为通过。 | 本报告与 [verification plan](P1-FEAT-20261009-161921-nightly-batch-aggregate-pr.verification-plan.md)；CLI / Git / body contract 原始本地输出可用上列 `open` 方式查看。 | 本地 rv-1—rv-3 结果与 rv-4 明确未运行；PR / CI surface 由 runner 发布后补入。 |

## Delivery summary

实现包含默认关闭的 `kc run --all-ready --aggregate-pr`、显式 `kc pr aggregate --issue ...` / retry、worker 全部成功后的聚合编排、按依赖排序的隔离 Git worktree 集成、完整 tree 验证、来源 PRD 证据门、多 PRD v2 body contract、Draft PR 检查与延迟 source closeout，以及操作指南 / 随包 skill 同步。审阅发现并补上总 PR 创建失败后的恢复：确认同分支没有 PR 后，仅按远端 SHA lease 清理本次 batch ref；远端 ref 已变化时拒绝删除。

### rv-1 — 本地 CLI 参数与 help

- 结果：**PASS**；`tests/test_agent_runner_cli.py -k aggregate` 为 **4 passed, 149 deselected**。
- 观察：Typer 暴露 `--aggregate-pr` 与 `kc pr aggregate --issue`；parser / schema 断言默认关闭，且在 executor / client 入口前拒绝不支持组合和小于两个候选。
- 实际 CLI help：`kc run --help` 与 `kc pr aggregate --help` 均 exit 0；操作员可读到 opt-in 批次和重复 Issue 参数。
- 边界：未启动 `kc run` 队列、未读取仓库配置、未创建 GitHub 或 Git client。
- 终端输出：本机忽略文件 `rv-1-cli.txt`。

### rv-2 — 临时 Git tree 与 ref 保全

- 结果：**PASS**；当前 HEAD 工作树上的 `tests/test_agent_runner_batch_aggregate.py` 为 **19 passed**。
- 观察：真实本地 Git fixture 按来源顺序生成组合 tree；冲突 fixture 保持 base 与 source refs；未拥有的远端 batch ref 被拒绝，已知总 PR head 与远端 ref 不一致时拒绝覆盖；总 PR 创建失败后按当前远端 SHA lease 删除本次 batch ref，SHA 已变化时保留该 ref。fixture values 由本地测试创建，不代表 GitHub PR 状态。
- 终端输出：本机忽略文件 `rv-2-git.txt`。

### rv-3 — 本地 v1 / v2 PR body contract

- 结果：**PASS**；`tests/test_agent_runner_pr_body_contract.py -k 'aggregate or compliant_body'` 为 **18 passed, 29 deselected**。
- 观察：v2 接受完整的稳定唯一集合；拒绝遗漏、重复、额外路径、排序不稳定、来源集合 mismatch 和缺失授权句。普通 v1 单 PRD contract 回归仍通过。
- 终端输出：本机忽略文件 `rv-3-body-contract.txt`。

### rv-4 — GitHub 外部行为（未验证）

- 状态：**NOT RUN**，按要求不连接 GitHub，不读取或写入 Issue / PR，不检查 required checks，不关闭来源 PR，也不查看远端 branch 状态。
- 不使用 fake、录制响应或 mock 作为外部行为证据。实现代码定义这些调用的顺序和失败恢复，但不能据此声称真实 GitHub 行为通过。
- 记录：本机忽略文件 `rv-4-external-github-unverified.txt`。

## Static and focused test gates

- `UV_CACHE_DIR=/private/tmp/uv-cache-issue-258 uv run pytest --no-testmon -q tests/test_agent_runner_batch_aggregate.py`: **19 passed**; this is the current batch Git oracle evidence.
- `UV_CACHE_DIR=/private/tmp/uv-cache-issue-258 uv run pytest --no-testmon -q tests/test_agent_runner_direct_pr_authoritative_pr.py tests/test_agent_runner_direct_pr.py tests/test_agent_runner_fast_merge.py tests/test_kedacode_operator_skill.py tests/test_github_client.py`: **110 passed**.
- The default `just test` ran the batch file's 19 passing tests, then reported **10 failed, 4 deselected**. All ten failures are in unrelated `tests/test_cli_config_migrate.py` cases: sandbox process enumeration returns `EPERM` / scanner unavailable. A scoped `just test` with `JUST_LOCAL_TEST_TARGET=tests/test_agent_runner_batch_aggregate.py` reported **19 deselected** under pytest-testmon, so that invocation is not counted as evidence.
- `UV_CACHE_DIR=/private/tmp/uv-cache-issue-258 uv run mkdocs build --strict`: exited 0. MkDocs printed existing unlinked-page and reciprocal-anchor informational messages; no new documentation page or navigation entry was added in this change.
- The three rv test commands above were run with `--no-testmon` where applicable so their named subsets execute explicitly.
- `tests/test_github_client.py -k 'get_pull_request_context'`: **2 passed** as a local adapter compatibility check for the requested `isDraft` field. Its local process-runner fixture is not evidence of GitHub status, PR creation, checks, or close behavior and is not counted toward rv-4.

### Recovery verification

- The previous `just test all` failure was repaired: the authoritative PR fixture now includes its required `isDraft` field, non-aggregate `kc run` no longer dereferences aggregate-only settings, and the packaged skill route / CLI flag whitelist includes the aggregate commands. `tests/test_agent_runner_direct_pr_authoritative_pr.py`, `tests/test_agent_runner_direct_pr.py`, `tests/test_agent_runner_fast_merge.py`, and `tests/test_kedacode_operator_skill.py` completed with **25 passed** in the configured local pytest selection.
- A repeated `UV_CACHE_DIR=/private/tmp/keda-issue258-uv-cache just test all` completed full lint successfully and reported **3,829 passed, 14 failed, 1 skipped**. Four daemon CLI failures were caused by attempts to write `/Users/zata/.kedacode/daemon-locks/repo.lock` outside this worktree; rerunning those four with `HOME=/private/tmp/keda-issue258-home` completed **4 passed**.
- The remaining ten `kc config migrate` failures are environment-blocked. This sandbox rejects `ps` with `Operation not permitted`; a direct `psutil.process_iter(["pid", "cmdline"])` probe fails with `PermissionError(1, 'Operation not permitted (originated from sysctl() malloc 1/3)')`. The migration command correctly refuses when it cannot verify process occupancy. The production scanner and its tests were not changed, and this result is not recorded as a pass.
- With both an isolated writable home and `KEDACODE_PRD_SKILL_PATH=/Users/zata/.codex/skills/prd/SKILL.md`, the standard `just test` selection reported **148 passed, 10 failed, 231 deselected**. All ten failures are the same process-enumeration `EPERM`; aggregate, CLI, contract, and skill synchronization tests selected by this local run passed. An earlier isolated-home run without the skill path was a setup error (the home lacked the installed PRD skill) and is not used as validation evidence.
- `just lint --reuse` passed. Full pre-commit lint hooks passed during the post-change `just test` preflight; `just lint --full` also confirmed the current test/lint markers after the scoped test command. The regular local test gate remains partial because of the process-scanner sandbox limitation above.

### Review-cycle verification

Issue #258 代码评审后的修复轮（拆分队列聚合接线、补齐资格门披露、细分收尾诊断、更正聚合标注口径）在本最终树重新执行以下检查：

- CI 硬门用工作流原命令复跑：`uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend --allow-list-file hooks/max_file_lines.allowlist.txt` → **exit 0**。修复前同一命令报 `agent_runner_orchestration_runtime.py: 1045 非空行，超过上限 1000 行`；聚合队列接线落到新模块 `agent_runner_batch_aggregate_queue.py` 后，该调度入口文件为 **954 非空行**，未新增 allowlist 条目。
- 已发行 oracle 重跑：`scripts/run-rv-1.sh` **exit 0**；`scripts/run-rv-2.sh` **21 passed**（原 19，新增两份总 PR 正文空白规范化比较的本地用例，覆盖收尾门不再把正文差异误判为 checks 失败）；`scripts/run-rv-3.sh` **19 passed, 29 deselected**（原 18，新增一条钉住 aggregate-contract 标注只声明本地门、不声称合并队列拒绝的用例）。
- 新增队列接线 oracle：`uv run pytest --no-testmon -q tests/test_agent_runner_batch_aggregate_queue.py` → **5 passed**。只覆盖队列侧判定（批次数量门、未声明聚合时的退出码透传、整批失败短路、dry-run 预览），协作者换成"取属性即失败"的哨兵，因此这些用例不隐含任何 GitHub 调用，也不作为 rv-4 证据。
- 定向回归集：`uv run pytest -o addopts="" -q tests/test_agent_runner_batch_aggregate.py tests/test_agent_runner_pr_body_contract.py tests/test_agent_runner_batch_aggregate_queue.py tests/test_agent_runner_orchestrate.py tests/test_agent_runner_run_targeting.py tests/test_agent_runner_direct_pr_discovery.py tests/test_lifecycle_agent_routing.py tests/test_kedacode_operator_skill.py tests/test_agent_runner_cli.py` → **353 passed**。
- CI 等价全量选择：`uv run pytest tests/ -m "not expensive" --no-testmon --timeout=120` → **3853 passed, 1 skipped**。此前记录的 `kc config migrate` 进程枚举 `EPERM` 失败在本轮未复现，因此本行按实际结果记录为全绿，不作为环境限制。
- `uv run mkdocs build --strict` → **exit 0**，仍是既有的 unlinked-page / reciprocal-anchor 信息级提示；本次只在既有页面 `guides/agent-runner.md` 内新增一个 `###` 小节，未新增页面或导航项。
- `just lint --reuse` → duplicate-code、architecture layer、guidelines consistency、max-file-lines 四道 hook 全部 **Passed**；改动文件的 pre-commit（ruff + ruff-format + PRD checklist + max lines）全部 Passed。
- 披露项的可核对方式：指南新小节「批次聚合的资格门与两次调用」、`resolve_batch_sources` docstring、两条资格错误文案与随包 `SKILL.md` / `references/run-once.md` 条目都点名 `agent/review` 与 checks `SUCCESS`，并写明单次夜间队列调用通常需要在 checks 与标签落定后再执行 `kc pr aggregate`。本轮仍不连接 GitHub：真实 PR 创建、checks、来源关闭与远端分支状态维持 **rv-4 NOT RUN**，本修复不改变该边界。

## Review state

Independent verifier round 1 reports **PASS** in the sibling `verifier-report.md`. The verifier records ten unrelated sandbox `EPERM` migration failures and pending PR / CI presentation as non-blocking; no live GitHub behavior is claimed. PR / CI publication remains runner-owned, and the three `Human-Confirmed` items remain open.
