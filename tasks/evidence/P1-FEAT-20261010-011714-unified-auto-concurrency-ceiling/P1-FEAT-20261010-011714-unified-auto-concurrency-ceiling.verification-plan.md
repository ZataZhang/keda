# 验证计划：统一自动执行并发上限

- Issue: [#266](https://github.com/ZataZhang/keda/issues/266)
- Canonical PRD: `tasks/archive/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling.md`（本轮发现已归档；未修改、未移动，也未勾选 Human-Confirmed）
- 验证对象：当前 review 工作树（base HEAD `bf9e39e200b0b4f831e23be68118bc429124e505`）；最终生产源码 diff 指纹记于每项 `rv-N-implementation-tree.txt`。
- 隔离方式：RV fixture 使用真实 CLI / daemon / console API / SQLite / Git 逻辑，只替换明确注明的 GitHub CLI 与 agent 外部边界；RV3 页面不允许 mock。
- 结论：RV1、RV2、RV4、RV5 已在当前 review 工作树通过。RV3 曾在此前验证树完成真实页面 red→green；本轮重新执行在 Chromium 启动时被 macOS `Permission denied (1100)` 阻断，未产生页面断言结论。RV3 涉及的前端页面、设置 GET/PATCH 路由与设置读写用例未被本轮改动；已保留历史真实页面证据与当前重跑诊断，见证据报告。

| RV | 检查点 | 本轮状态 | 关键证据 |
|---|---|---|---|
| 1 | daemon 认领 ceiling、扣除在跑数、未设置继承容量 | PASS | `rv-1-negative-control.txt`、`rv-1-daemon-command.txt`、`rv-1-daemon-claim-budget.log` |
| 2 | backlog advance 与全局开始使用 ceiling / source / free slots | PASS | `rv-2-command-output.txt`、`rv-2-advance-ceiling-report.txt`、`rv-2-global-start.txt` |
| 3 | 真实 Backlog 页面三态、设置、恢复继承与 fresh 读回 | 此前 PASS；本轮重跑 INCONCLUSIVE（浏览器启动受阻） | 历史真实运行的 `rv-3-console-roundtrip.txt`、`rv-3-negative-control.txt`、`rv-3-console-backlog-report.txt` 与截图；本轮诊断 `rv-3-backlog-control-roundtrip.txt` |
| 4 | 显式定向运行豁免、`--all-ready` 与 daemon 互斥 | PASS | `rv-4-negative-control.txt`、`rv-4-explicit-command.txt`、`rv-4-explicit-run-exempt.txt` |
| 5 | running 计数失败时 fail-closed 且续做恢复发现 | PASS | `rv-5-negative-control.txt`、`rv-5-fail-closed.log`、`rv-5-inflight-probe.txt` |

## 每项复现命令与判定

### RV1 — daemon 认领

```bash
cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts
../../../../.venv/bin/python rv1_negative.py && ../../../../.venv/bin/python rv1_daemon.py
```

- 负控先让 running 计数返回空值，实际状态应达到 `[100, 101, 200, 201]`、`count=4 > ceiling=2` 并打印 `NEG CONTROL: RED reproduced`。
- 绿态检查策略 2 时预算随预置 running 数变为 2 / 1 / 0，峰值不超过 2；unset 继承容量 10 并可认领 6 个。
- 本轮输出与断言通过。

### RV2 — 补位与全局开始

```bash
cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts
../../../../.venv/bin/python verifier_probe.py && ../../../../.venv/bin/python rv2_advance.py && ../../../../.venv/bin/python rv2_global_start.py
```

- 负控将策略 2 错当成继承行为时，6 个 pending 只补入 2 而非期望 6，必须打印 `NEG CONTROL: RED reproduced`。
- 绿态核对 unset/策略 2/已有 running 的补位数；本轮另加入没有 PRD 锚点的 `agent/running` Issue，验证 advance 与 start-global 均扣减该仓库级 running 数，并在 ceiling 已满时不启动；报告包含 ceiling、source、running 与 free slots。
- 本轮输出与断言通过。

### RV3 — Backlog 真实页面 red→green

```bash
just console-sync && cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts && ../../../../.venv/bin/python rv3_negative.py && ../../../../.venv/bin/python rv3_console.py
```

- **预期红态：** 注入旧显示口径后，真实页面断言返回非零、打印 `RESULT: FAIL`；恢复 bundle 后再运行同一页面流程，需 `RESULT: PASS` 并验证继承 / 设置 / 受限 / 保存 4 / 恢复继承五态、fresh GET、PATCH 与 SQLite 行删除。
- **此前实际：** 在 `HEAD=4335947c` 的验证树上，旧静态 bundle 使真实页面断言变红；恢复 bundle 后真实页面流程通过继承、设置 2、受限 8、保存 4、恢复继承五态，fresh GET、PATCH 和 SQLite 终态正确。原页面记录和截图保留。
- **本轮实际：** `just console-sync` 成功；旧口径 bundle 故障注入后，Playwright Chromium 在页面启动前被 macOS `Permission denied (1100)` 终止，因此本轮没有新的页面 PASS 或产品红态。bundle 已按字节还原。失败诊断写入 `rv-3-backlog-control-roundtrip.txt`。本轮改动未触及前端源码、Backlog settings GET/PATCH 路由或设置读写用例；旧页面证据只对这些未变的页面边界继续有效，不代表整棵工作树相同。

### RV4 — 显式运行豁免

```bash
cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts
../../../../.venv/bin/python rv4_negative.py && ../../../../.venv/bin/python rv4_explicit.py
```

- 负控把 ceiling 错误传入显式入口，目标 Issue 不应启动，探针应为空且断言失败。
- 绿态 `kc run --issue 100` 应 exit 0、agent start/end 命中 Issue 100、完成并发布本地 fixture commit；daemon 活跃时 `kc run --all-ready` 仍 exit 5。
- 本轮输出与断言通过；发布远端是 fixture 内的本地 bare repository，不验证 GitHub 服务。

### RV5 — 计数失败 fail-closed

```bash
cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts
../../../../.venv/bin/python rv5_negative.py && ../../../../.venv/bin/python rv5_failclosed.py && ../../../../.venv/bin/python verifier_probe_inflight.py
```

- 负控恢复旧异常传播行为，预期 daemon pass failed 且 running 恢复候选未发现，检查应变红。
- 绿态预期失败轮为 0、无新认领、ready 项保留、running 恢复候选发现且在途状态继续推进。
- 本轮输出与断言通过；fail-closed 日志次数受轮询时序影响，不把次数作为稳定断言。

## 验证范围

本轮 reviewer 修复追加覆盖：ready 预算为 0 时仍进入已有 `direct_pr_cleanup` 收尾通道，普通 ready 仍不认领。该边界由 `tests/test_agent_runner_orchestrate.py::test_run_once_direct_pr_cleanup_bypasses_ready_claim_budget` 锁定，并在同一真实编排入口的定向测试中通过。运行时文件变化后，rv-1、rv-4、rv-5 真实入口均重新执行；rv-2 的补位 / 全局开始边界未变，RV-3 页面与设置 GET/PATCH 边界未变。RV-3 重跑仍因 Chromium `Permission denied (1100)` 为 INCONCLUSIVE。
