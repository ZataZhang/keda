# Verifier Report — run-daemon-autopilot-control-surface

- PRD：`tasks/pending/P1-FEAT-20261005-161633-run-daemon-autopilot-control-surface.md`
- verified_head_sha = `d23edff406388cb3cceb7e2f1c1669f9c6f8cc3e`（分支 `feat/run-daemon-control-surface`）
- verified_tree_sha = `4546d8fca13685df5a4821da40ab11cb45671691`（临时 index 排除 `tasks/pending/P1-FEAT-20261005-161633-…md`、`tasks/archive/P1-FEAT-20261005-161633-…md`、`tasks/evidence/run-daemon-autopilot-control-surface/` 后 `git write-tree`）
- 复核人：独立 verifier（只读，2026-10-05）

## 结论：**PASS**

## 冻结凭证核对

`git diff HEAD~1 -- src tests | shasum -a 256` =
`35708f1555cc12429669bf8044e48ba366c5782ae2c1be8755df16b9e949b80a`
前 16 位 `35708f1555cc1242` 与任务下发凭证**一致**，凭证有效。复核期间未修改 src/tests/docs/tasks（仅本报告文件）。

## 逐 oracle 独立判定表

| rv-id | 判定 | 依据 | verifier 实跑命令与关键输出 |
|---|---|---|---|
| rv-1（定向只处理指定 Issue） | PASS（带披露） | 代码事实：`agent_runner_orchestration_runtime.py` target_issue 非 None 时 ready/running/blocked 三条候选通道均收窄为 `[target_issue_summary]`（标签不匹配则为空），其余 Issue 不触碰，仍走依赖门禁与 claim；单测覆盖收窄两态。真实入口用 `--dry-run --json` 机器预览携带 `"target_issue": 42` | `uv run python tasks/evidence/…/rv_real_entry.py` → `[PASS] rv-1 … exit=1 stdout head='{"dry_run": true, …, "target_issue": 42, "all_ready": false, …}'`。GitHub 边界按 PRD mock_boundary 允许 mock；披露：真实入口为 dry-run 预览而非带真实 GitHub 的完整 run（完整定向行为由 Fake 客户端单测覆盖） |
| rv-2（daemon 在跑默认拒绝、不双 claim） | PASS | `cli_parsed_commands/runner.py` 用 `find_live_daemon_pid`（只读探测锁文件 + 探活）在无 `--takeover` 时抛 `ExitCode.CONFLICT`（5），提示 stop 命令与 `--takeover`；negative control（持锁进程不被拒绝路径破坏）在脚本中断言并通过 | 实跑 rv_real_entry.py：`[PASS] rv-2 run rejected while daemon lock held: exit=5 mentions takeover=True`；`[PASS] rv-2 negative control: holder pid 26169 still alive=True` |
| rv-4（run 无 --autopilot） | PASS | `cli_parser.py` / `cli_typer_runner.py` 的 run 未声明 `--autopilot`/`--concurrency`；`--help` 实测 | `.venv/bin/iar run --help`：出现 `--issue`/`--all-ready`/`--takeover`/`--yes`，无 `--autopilot`、无 `--concurrency` |
| rv-5（无目标用法错误；--all-ready 等价旧行为） | PASS | 无目标 → `CliError(ExitCode.USAGE)`，suggestion 列出三种目标形态；`--all-ready` 走原 `list_ready_issues` 按优先级捞队列路径（target_issue=None 时代码与旧路径一致）；`tests/test_agent_runner_cli.py` 9 处旧用例迁移到 `--all-ready`，negative control（无目标不得静默捞队列）成立 | `.venv/bin/iar run` → exit=2，`usage_error: iar run requires a target: pass --issue <N>, a PRD path, or --all-ready.` |
| rv-6（--takeover 优雅停 + 无孤儿 + reclaim） | PASS（带披露） | `engines/agent_runner/run_takeover.py`：停前快照后代进程组 → 托管走 `PidfileProcessSupervisor.stop` / 未托管 SIGTERM → 等待 → 超时才 SIGKILL → `_sweep_orphan_groups` 补扫 → `reclaim_stale_running_issues`；daemon 侧 `run_agent_daemon.py` SIGTERM 钩子先整组终止在途 agent 树；确认门：`--json` 模式必须 `--yes`，交互模式 `Confirm.ask`；negative control（拒绝确认不停 daemon）有单测 | 实跑 rv_real_entry.py：`[PASS] rv-6 final_signal=sigterm daemon exited=True`；`[PASS] rv-6 negative control: leftover descendant pid=None`。披露：daemon 以真实 `/bin/sh -c "sleep 120 & wait"`（独立进程组）持锁，接管编排为交付的真实 `take_over_daemon`（脚本已如实声明） |
| rv-7（PRD 回链两态解析） | PASS | `run_target_resolve.py` 复用既有 `ISSUE_LINK_LINE_RE`/`ISSUE_NUMBER_RE`；无回链（含占位符）→ `RunTargetResolveError` → `CliError(USAGE)` 且 suggestion=`iar issue create …`，绝不回退捞队列 | rv_real_entry.py：`[PASS] rv-7 PRD link two-state: linked->#417; unlinked error mentions 'iar issue create': True` |
| rv-8（daemon --autopilot/--no-autopilot 仅覆盖调度、不 arm 合并） | PASS | `run_agent_daemon.py`：`autopilot_enabled = override if override is not None else config.autopilot.enabled`，仅落在调度门控（`advance_backlog_queue` 前）；`git diff HEAD~1 --stat` 中 **无 `agent_runner_merge_queue.py`**，review 侧合并未被触碰；旗标每次 pass 都用同一 override（进程内锁定），未传时每轮热读配置 | rv_real_entry.py：`[PASS] rv-8 daemon run flags 含 '--autopilot --no-autopilot'`；调度三态由 `test_agent_runner_run_targeting.py` 单测覆盖（配置关+flag 开调度 / 配置开+flag 关调度 / 缺省跟随配置） |

## 实跑汇总（全部真实执行）

1. `CI=true uv run pytest tests/test_agent_runner_run_targeting.py tests/test_agent_runner_cli.py tests/test_iar_operator_skill.py -q -o addopts=""` → **178 passed**。
2. `CI=true uv run pytest -q -o addopts=""`（全量）→ **3012 passed, 1 skipped**（202.76s），与 evidence-report 的 `3012 passed` 一致，无需修正。
3. `.venv/bin/iar run`（无目标）→ exit 2 + 三种目标形态提示。
4. `.venv/bin/iar run --help` → 有 `--issue`/`--all-ready`/`--takeover`/`--yes`，无 `--autopilot`/`--concurrency`。
5. `uv run python tasks/evidence/…/rv_real_entry.py` → **9/9 PASS**（脚本经审读为真实进程入口探针，非自证脚本）。

## §11 Non-Goals 核对

- run 无 `--autopilot`/`--concurrency`：✅（--help 实测 + 解析层无声明）。
- `iar daemon --autopilot` 不 arm 合并：✅（门控只落调度；merge_queue 文件未在 diff 中）。
- 不默认接管：✅（仅显式 `--takeover` 才进入 `_confirm_and_take_over_daemons`）。
- SIGKILL 非首手段：✅（SIGTERM → 等待 → 超时才升级，daemon 侧钩子同序）。
- 无 DB/HTTP/前端改动：✅（diff 仅 src/backend api/core/engines、docs、skill、tests、tasks/evidence）。
- 不保留"无目标即捞队列"：✅（exit 2）。

## FR-8 优先级与代码事实

- `flag > repo .iar.toml > 全局`：override 非 None 时无条件覆盖最终 `context.config.autopilot.enabled`（repo 覆盖全局由既有配置加载完成），每次 pass 同值 → 进程内锁定；`None` 时每轮热读。✅
- 旗标不 arm 合并：调度门控表达式之外无任何对 `safety.auto_merge`/`process_merge_queue` 的引用变化。✅

## 文档与 skill 同步

- `docs/guides/agent-runner.md`：新增「run 与 daemon 的执行语义与控制面」章节（目标必填/互斥/接管/autopilot 覆盖），命令示例全部迁移为 `--all-ready`/`--issue` 形态。✅
- `iar-operator SKILL.md`：run 目标必填（breaking）、默认互斥与 `--takeover`、autopilot 属于 daemon 且不 arm 合并、`iar backlog advance` 手动调度均已写入。✅
- `tests/test_iar_operator_skill.py` skill↔CLI 漂移守卫已更新（178 用例中含其通过）。✅

## 发现的问题清单（不阻塞 PASS）

1. **§9.1 人读呈递物缺失**：PRD 要求 `rv-1-targeted-run.png` 与 `rv-6-takeover.png`，证据目录目前只有脚本与 JSON 结果，无 png 呈递物。rv-1/rv-6 的 reviewer=human，交付 PR 呈递或人工验收前需补齐（或在 PR 正文用等价文本呈递并说明）。
2. **rv-1 真实入口为 dry-run 预览**：PRD mock_boundary 允许 GitHub mock、"run 入口真实"以 CLI 解析+预览满足；完整定向执行（含 claim）由 Fake 客户端单测覆盖。可接受，但呈递时应如实标注该边界。
3. evidence-report 中 `rv-8 daemon 调度门控三态（单测）` 一行放在"真实入口层证据"表格内，层级标注略有混用（三态来自单测）；不影响 oracle 结论。

## 结论依据

冻结凭证匹配；PRD §7.6 全部 7 个 oracle（rv-1/2/4/5/6/7/8）均被真实证据独立支持，negative control 齐备且实跑可复现；Non-Goals 与 FR-1..FR-8 的代码事实核对全部通过；全量 3012 passed 复现；文档与 skill 同步到位。三项披露/缺口见问题清单，均不构成 oracle 失败或阻塞合并的阻塞项。


## 附录：增量复核（2026-10-05，2229f736）

- 变更：`src/backend/api/cli_parser.py` 三处新增 argparse help 文本压缩
  （CI 硬行数上限 1007 → 996 非空行），另将 rv_real_entry.py /
  rv-real-entry-results.json 从证据分支回补到代码分支。无行为变化。
- 复核：定向测试 190 passed（test_agent_runner_cli / run_targeting /
  iar_operator_skill / cli_schema）；rv_real_entry.py 复跑 9/9 PASS；
  verified head 更新为 `2229f736`，record-excluded verified tree 更新为
  `8fdf6935095e714942b294cf9301defb2c4fc5f2`。
- 结论：增量复核 PASS，主结论不变。

## 附录：增量复核（2026-10-05，72778653）

- head：`72778653`（分支 feat/run-daemon-control-surface）。
- 改动面确认：`2229f736..72778653` 仅含两个提交——d96e9011（证据报告
  追加说明）与 72778653（`tests/test_agent_runner_run_targeting.py` 的
  `test_typer_run_help_has_no_autopilot` 断言加固：先去除 ANSI 转义与
  全部空白再做子串断言，使 `iar run --help` 断言与 CI 80 列终端宽度下
  Rich 的着色/折行无关；import re 移入函数内）。src 无任何改动。
  另核对 `d23edff4..2229f736` 的 src 改动仅为
  `src/backend/api/cli_parser.py` 三处 help 文本压缩（3+/14-），无行为
  变化，与上一节附录结论一致。
- 复跑：`CI=true uv run pytest tests/test_agent_runner_run_targeting.py
  -q -o addopts=""` → 20 passed。
- record-excluded verified tree（临时 index 排除 tasks/pending、
  tasks/archive 下同名 PRD 及 tasks/evidence/run-daemon-autopilot-
  control-surface/ 后 write-tree）：
  `e85ce3a4e333c1f3f4ec2baa8e1e1451f85e45e3`，与预期一致。
- 结论：增量复核 **PASS**，主结论（2229f736 PASS）不变，verified head
  推进至 `72778653`。
