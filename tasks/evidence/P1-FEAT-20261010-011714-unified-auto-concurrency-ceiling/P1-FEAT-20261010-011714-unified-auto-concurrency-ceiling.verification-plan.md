# 验证计划：统一自动执行并发上限

- Issue: [#266](https://github.com/ZataZhang/keda/issues/266)
- Canonical PRD: `tasks/archive/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling.md`（本轮发现已归档；未修改、未移动，也未勾选 Human-Confirmed）
- 验证对象：生产实现 commit `13e52394308b40097010931c3d0880a3f27be42a`，tree `a68f3e0f7abf9da4d95334393c05bc4b08112aa7`
- 隔离方式：RV fixture 使用真实 CLI / daemon / console API / SQLite / Git 逻辑，只替换明确注明的 GitHub CLI 与 agent 外部边界；RV3 页面不允许 mock。
- 结论：RV1–RV5 本轮证据均为绿；RV3 在宿主侧完整运行真实页面 red→green，旧 bundle 页面断言变红，恢复后五态及 API / SQLite 断言通过。

| RV | 检查点 | 本轮状态 | 关键证据 |
|---|---|---|---|
| 1 | daemon 认领 ceiling、扣除在跑数、未设置继承容量 | PASS | `rv-1-negative-control.txt`、`rv-1-daemon-command.txt`、`rv-1-daemon-claim-budget.log` |
| 2 | backlog advance 与全局开始使用 ceiling / source / free slots | PASS | `rv-2-command-output.txt`、`rv-2-advance-ceiling-report.txt`、`rv-2-global-start.txt` |
| 3 | 真实 Backlog 页面三态、设置、恢复继承与 fresh 读回 | PASS | `rv-3-console-roundtrip.txt`、`rv-3-negative-control.txt`、`rv-3-console-backlog-report.txt` 与十张本轮截图 |
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
- 绿态核对 unset/策略 2/已有 running 的补位数，以及全局开始按容量 4 或策略 2 启动并排队；报告须包含 ceiling、source 与 free slots。
- 本轮输出与断言通过。

### RV3 — Backlog 真实页面 red→green

```bash
just console-sync && cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts && ../../../../.venv/bin/python rv3_negative.py && ../../../../.venv/bin/python rv3_console.py
```

- **预期红态：** 注入旧显示口径后，真实页面断言返回非零、打印 `RESULT: FAIL`；恢复 bundle 后再运行同一页面流程，需 `RESULT: PASS` 并验证继承 / 设置 / 受限 / 保存 4 / 恢复继承五态、fresh GET、PATCH 与 SQLite 行删除。
- **本轮实际：** 宿主 shell 执行上方命令成功。故障注入的旧静态 bundle 使真实页面断言变红：继承和恢复继承显示 2 而 fresh API 为 4，受限态显示 8 而 effective 为 4；输出 `RESULT: FAIL` / `NEG CONTROL: RED 已复现`。脚本 finally 按字节还原 bundle 后，真实页面流程通过继承、设置 2、受限 8、保存 4、恢复继承五态，fresh GET、PATCH 和 SQLite 终态均正确，输出 `RESULT: PASS` / `RV-3 PASSED`。
- 全量命令 stdout 为 `rv-3-console-roundtrip.txt`；页面断言与请求记录在 `rv-3-console-backlog-report.txt`；本轮完整页面和控制条截图列于证据 manifest。此前受 sandbox 影响的 Chromium `Permission denied (1100)` 诊断保留为历史恢复记录，不作为当前失败证据。

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

Recovery 4/5 修正两处受宿主权限影响的测试隔离：CLI 配置迁移用例不再扫描宿主进程，daemon 参数用例不再写真实用户状态目录。最终执行 `UV_CACHE_DIR=/private/tmp/uv-cache-issue-266 just test all`，结果为 3826 passed、1 skipped；`just lint --reuse` 与 `just lint --full` 通过。未改生产实现或 PRD。

RV1–RV5 的结构化证据对应实现 commit `13e52394308b40097010931c3d0880a3f27be42a`。RV3 的 red→green 已在宿主侧完成；历史 sandbox 权限错误不影响本轮页面结论。
