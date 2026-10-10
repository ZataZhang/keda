## 人审导航 / Human Review Navigation

### Backlog 并发控制条：设计意图与真实页面记录

| 呈递项 | 本地路径与打开方式 | 核对结果 |
|---|---|---|
| Issue #266 真实 console 页面状态记录（五态） | `tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling`；`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling"` | 页面报告、API / PATCH / fresh GET / SQLite 记录和截图均来自本轮真实 `kc console` 页面验证；完整 red→green 输出见 `rv-3-console-roundtrip.txt`。 |

- Issue: [GitHub #266](https://github.com/ZataZhang/keda/issues/266)
- Pull request: 尚未创建；本工作流没有创建 PR。
- CI: 尚无关联 PR / CI run。
- 执行器交叉核对：检查了本轮页面图、页面报告、负控红态 stdout、green stdout 与 manifest。RV1–RV5 均通过；旧 sandbox Chromium 权限错误已由宿主侧重跑排除。
- 快速自检：`rv-3-console-roundtrip.txt` 包含 `NEG CONTROL PASSED`、`RESULT: PASS` 和 `RV-3 PASSED`；`rv-3-console-backlog-report.txt` 记录 policy=2、capped effective=4、saved4-fresh=4 及恢复后 `[DB 终态] ... None`。

#### 目标原型（design intent；不是运行时证据）

![Backlog 并发控制条目标原型](../../../docs/prototypes/assets/backlog-unified-concurrency-ceiling.png)

原型图只表达控制条的目标设计。以下截图来自本轮真实 `kc console` 页面流程；验证跨过页面、API 与 SQLite 边界。

#### 继承态

**目标值：** 并发 4（继承 runner 配置）；fresh GET source=inherited、effective=4、max_parallel=null。

![继承态真实 console 控制条（rv-3）](rv-3-backlog-concurrency-inherit.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-inherit.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-inherit.png"`。

![继承态完整页面（rv-3）](rv-3-backlog-concurrency-inherit-page.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-inherit-page.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-inherit-page.png"`。

#### 策略设置态

**目标值：** 并发 2（Backlog 设置）；PATCH max_parallel=2 返回 200，fresh GET source=policy、effective=2。

![策略设置态真实 console 控制条（rv-3）](rv-3-backlog-concurrency-policy.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-policy.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-policy.png"`。

![策略设置态完整页面（rv-3）](rv-3-backlog-concurrency-policy-page.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-policy-page.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-policy-page.png"`。

#### 受容量限制态

**目标值：** 策略为 8、容量为 4 时显示并发 4（受 runner 容量限制）；fresh GET source=capped_by_capacity、effective=4。

![受容量限制态真实 console 控制条（rv-3）](rv-3-backlog-concurrency-capped.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-capped.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-capped.png"`。

![受容量限制态完整页面（rv-3）](rv-3-backlog-concurrency-capped-page.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-capped-page.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-capped-page.png"`。

#### 保存后 fresh 态

**目标值：** 刷新后并发 4（Backlog 设置）；PATCH / fresh GET 都返回策略 4。

![保存后 fresh 态真实 console 控制条（rv-3）](rv-3-backlog-concurrency-saved4-fresh.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-saved4-fresh.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-saved4-fresh.png"`。

![保存后 fresh 态完整页面（rv-3）](rv-3-backlog-concurrency-saved4-fresh-page.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-saved4-fresh-page.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-saved4-fresh-page.png"`。

#### 恢复继承 fresh 态

**目标值：** 并发 4（继承 runner 配置）；PATCH max_parallel=null 后设置行删除，fresh GET source=inherited。

![恢复继承 fresh 态真实 console 控制条（rv-3）](rv-3-backlog-concurrency-restored-fresh.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-restored-fresh.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-restored-fresh.png"`。

![恢复继承 fresh 态完整页面（rv-3）](rv-3-backlog-concurrency-restored-fresh-page.png)

本地图片（本轮真实页面截图）：`rv-3-backlog-concurrency-restored-fresh-page.png`。
打开原图：`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/rv-3-backlog-concurrency-restored-fresh-page.png"`。

## RV 验证结果

| RV | 结果 | red→green 证据与结论 |
|---|---|---|
| 1 | PASS | `rv-1-negative-control.txt` 记录计数失真后实际 running 4 > ceiling 2；绿态 `rv-1-daemon-command.txt` 与 `rv-1-daemon-claim-budget.log` 记录预算 2/1/0、峰值 2，以及 unset 继承 10。 |
| 2 | PASS | `rv-2-command-output.txt` 记录负控误补位 2 而期望 6；随后真实 CLI / 路由核对 unset、策略 2、在跑预算与 start-global 的输出。 |
| 3 | PASS | 宿主侧完整命令 `rv-3-console-roundtrip.txt` 记录旧口径页面断言变红和恢复 bundle 后真实五态页面、fresh GET、PATCH、SQLite 终态通过；本轮十张页面与控制条截图一并记录。 |
| 4 | PASS | `rv-4-negative-control.txt` 证明错误 ceiling 会令定向运行不启动；`rv-4-explicit-command.txt` 记录 `kc run --issue 100 exit=0`、start/end、完成和本地 fixture 发布，`--all-ready` 与 daemon 同跑仍 exit 5。 |
| 5 | PASS | `rv-5-negative-control.txt` 记录旧异常传播导致 pass failed 且未发现 running 恢复候选；绿态日志 / 探针记录 `pass_failed=0`、ready 保留、running 候选被发现且 `running_touched=True`。 |

### RV3 真实页面 red→green

```bash
just console-sync && cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts && ../../../../.venv/bin/python rv3_negative.py && ../../../../.venv/bin/python rv3_console.py
```

宿主侧重跑已满足 red→green 条件：旧 bundle 时，继承和恢复继承显示 2 而 fresh API 为 4，容量受限态显示 8 而 effective 为 4，页面断言输出 `RESULT: FAIL` 与 `NEG CONTROL: RED 已复现`；负控脚本随后按字节还原 bundle。正常 bundle 下，真实页面控件完成继承、设置 2、受限 8、保存 4、恢复继承五态；页面文本、fresh API、PATCH 请求 / 响应和 SQLite 删除语义一致，输出 `RESULT: PASS` / `RV-3 PASSED`。完整 stdout 与截图见 `rv-3-console-roundtrip.txt`、`rv-3-console-backlog-report.txt` 和十张 `rv-3-backlog-concurrency-*.png`。此前 Codex 子进程 sandbox 中 Chromium 的 `Permission denied (1100)` 已通过宿主 shell 执行排除；旧诊断文件只记录已恢复的问题，不代表当前限制。

## 证据身份与范围

- 实现 tree：`a68f3e0f7abf9da4d95334393c05bc4b08112aa7`（commit `13e52394308b40097010931c3d0880a3f27be42a`）。
- Recovery 4/5 只修改测试隔离：迁移 CLI 用例不再扫描宿主进程，真实扫描仍由 `test_state_home_migration.py` 覆盖；不验证 daemon 锁机制的 CLI 用例 mock 锁获取/释放，避免写入用户状态目录。未改生产源代码或 PRD。
- 最终 `UV_CACHE_DIR=/private/tmp/uv-cache-issue-266 just test all`：3826 passed、1 skipped；`just lint --reuse` 与 `just lint --full` 通过。此前两轮失败分别定位为宿主进程扫描受限，以及 CLI 测试写入只读的用户状态目录，已通过测试隔离修复。
- 本轮重新执行 `just lint --reuse`、`just lint --full` 均通过；`uv run pytest --no-testmon tests/test_agent_runner_cli.py tests/test_cli_config_migrate.py -q` 为 163 passed。`just test` 的本地标记匹配 HEAD（`13e52394`）并按仓库规则跳过，此处不把跳过当作新增测试证据。
- 仓库 `validate_evidence_manifest` 解析器此前确认 manifest 覆盖 5 项、字段完整；更新 RV3 实际 stdout 与十张本轮截图后，manifest 文件列表已再次核对无缺失，红绿断言均在 `rv-3-console-roundtrip.txt` 中满足。
- Canonical PRD 当前位于 `tasks/archive/`；本轮没有修改或移动 PRD，也没有勾选任何 Human-Confirmed 项。由于 RV3 产品红→绿证据仍缺失，本轮不能发出 commit request。
- RV3 截图文件名分别对应本轮真实页面状态；完整页面图与控制条裁切图均逐张列出。
