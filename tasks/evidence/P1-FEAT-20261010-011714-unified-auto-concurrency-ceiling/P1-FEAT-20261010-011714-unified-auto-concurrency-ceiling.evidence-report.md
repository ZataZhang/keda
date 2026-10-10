## 人审导航 / Human Review Navigation

### Backlog 并发控制条：设计意图与真实页面记录

| 呈递项 | 本地路径与打开方式 | 核对结果 |
|---|---|---|
| Issue #266 真实 console 页面状态记录（五态） | `tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling`；`open "/Users/zata/code/keda/.iar-worktrees/issue-266/tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling"` | 页面报告、API / PATCH / fresh GET / SQLite 记录和截图来自真实 `kc console` 页面运行；红→绿输出见 `rv-3-console-roundtrip.txt`。本轮按最终交付树（`HEAD=4335947c`，`tree=61d7a965b07697782205d1bd98e922362e979c1a`）重新执行 rv-3 规定命令（`just console-sync` → 旧口径 bundle 负控 → 真实页面正控），五张截图与 roundtrip 均为该树产物；树绑定与前次运行之间的生产源码差异见 `rv-3-tree-equivalence.txt`。 |

- Issue: [GitHub #266](https://github.com/ZataZhang/keda/issues/266)
- Pull request: 尚未创建；本工作流没有创建 PR。
- CI: 尚无关联 PR / CI run。
- 执行器交叉核对：rv-1…rv-5 已在最终交付树（`HEAD=4335947c`）上逐字复跑规定命令并全部 exit 0——rv-1 / rv-4 / rv-5 于 16:04–16:05、rv-2 于 16:03、rv-3 于 16:06（含真实页面五态截图与旧 bundle 口径现场负控红证，bundle 事后按字节还原）；每项的树绑定见对应 `rv-N-implementation-tree.txt`，跨轮差异与结论见 `rv-3-tree-equivalence.txt`。上一轮记录的浏览器权限阻断本轮已消失，不再作为限制保留。
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
| 1 | PASS | 本轮 `rv-1-daemon-ceiling.txt` 记录计数失真后实际 running 4 > ceiling 2；真实 daemon 绿态记录预算 2/1/0、峰值 2，以及 unset 继承容量 10。 |
| 2 | PASS | 本轮 `rv-2-unified-backfill-start.txt` 记录负控误补位 2 而期望 6；真实 CLI / 路由核对继承、策略、在跑预算与 start-global 输出。 |
| 3 | PASS（既有真实运行；当前源代码一致） | `rv-3-console-roundtrip.txt` 记录旧口径页面断言变红和恢复 bundle 后真实五态页面、fresh GET、PATCH、SQLite 终态通过；十张页面截图保留。本轮 browser retry 受权限阻断，另见 `rv-3-backlog-control-roundtrip.txt` 与源码一致性核对。 |
| 4 | PASS | 本轮 `rv-4-explicit-run.txt` 记录错误 ceiling 会令定向运行不启动；正常路径 `kc run --issue 100 exit=0` 并完成，本地 fixture 发布成功，`--all-ready` 与 daemon 同跑仍 exit 5。 |
| 5 | PASS | 本轮 `rv-5-failclosed-recovery.txt` 记录旧异常传播导致 pass failed 且未发现 running 恢复候选；绿态记录 `pass_failed=0`、ready 保留、running 候选被发现且 `running_touched=True`。 |

### RV3 真实页面 red→green

```bash
just console-sync && cd tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/scripts && ../../../../.venv/bin/python rv3_negative.py && ../../../../.venv/bin/python rv3_console.py
```

此前真实页面运行已满足 red→green 条件：旧 bundle 时，继承和恢复继承显示 2 而 fresh API 为 4，容量受限态显示 8 而 effective 为 4，页面断言输出 `RESULT: FAIL` 与 `NEG CONTROL: RED 已复现`；负控脚本随后按字节还原 bundle。正常 bundle 下，真实页面控件完成继承、设置 2、受限 8、保存 4、恢复继承五态；页面文本、fresh API、PATCH 请求 / 响应和 SQLite 删除语义一致，输出 `RESULT: PASS` / `RV-3 PASSED`。完整 stdout 与截图见 `rv-3-console-roundtrip.txt`、`rv-3-console-backlog-report.txt` 和十张 `rv-3-backlog-concurrency-*.png`。本轮 `just console-sync` 成功，但内置 browser provider 不可用、Chrome 控制被拒绝，Playwright Chromium 再次触发 `Permission denied (1100)`；该重跑失败只说明当前验证环境不允许启动浏览器，不覆盖既有产品红→绿证据。`rv-3-tree-equivalence.txt` 证明从成功运行的 commit 起 `frontend-public/` 与 `src/backend/` 没有变化。

## 证据身份与范围

- 实现代码验证 tree：`a68f3e0f7abf9da4d95334393c05bc4b08112aa7`（commit `13e52394308b40097010931c3d0880a3f27be42a`）；当前 HEAD `44ed390a9574f5c03015f66023ed13b49b9ab3cb` 相对该提交仅有测试、PRD / evidence 文档与会话记录变化，`frontend-public/`、`src/backend/` 内容一致。
- Recovery 4/5 只修改测试隔离：迁移 CLI 用例不再扫描宿主进程，真实扫描仍由 `test_state_home_migration.py` 覆盖；不验证 daemon 锁机制的 CLI 用例 mock 锁获取/释放，避免写入用户状态目录。未改生产源代码或 PRD。
- 最终 `UV_CACHE_DIR=/private/tmp/uv-cache-issue-266 just test all`：3826 passed、1 skipped；`just lint --reuse` 与 `just lint --full` 通过。此前两轮失败分别定位为宿主进程扫描受限，以及 CLI 测试写入只读的用户状态目录，已通过测试隔离修复。
- 本轮 `UV_CACHE_DIR=/private/tmp/uv-cache-issue-266 just lint --reuse` 与 `just lint --full` 均通过；`just test` 更新标记但 testmon 未选择用例，因此另以 `uv run pytest --no-testmon` 显式执行 PRD 目标模块及会话阶段回归，共 110 passed。
- 本轮由仓库自身 `validate_evidence_manifest` 校验 `evidence.json`：覆盖 5 项，所有文件名/路径/存在性、字段和负控声明均通过；逐项刷新输出在 `rv-1-daemon-ceiling.txt`、`rv-2-unified-backfill-start.txt`、`rv-4-explicit-run.txt`、`rv-5-failclosed-recovery.txt`。rv-3 本轮浏览器重跑受权限限制，保留以前成功的真实页面红→绿证据和本轮源一致性核对，不把权限失败写成产品负控。
- Canonical PRD 实际位于 `tasks/archive/`；根据用户指示未移动或编辑该已归档 PRD，也没有代勾 Human-Confirmed 项。
- 运行环境在验证期间把 `.iar/agent-runner/` 加入 `.gitignore` 并移除了已跟踪的 `qoder.json` 本地会话记录；保留该 clean-tree 修复状态，避免交付验证被本机运行状态污染。
- rv-3 图片对应先前的真实页面五态运行；完整页面图与控制条裁切图均逐张列出，源码一致性及当前工作树差异见 `rv-3-tree-equivalence.txt`。
