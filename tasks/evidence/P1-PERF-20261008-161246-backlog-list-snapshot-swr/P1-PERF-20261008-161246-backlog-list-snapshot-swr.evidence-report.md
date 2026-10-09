# Evidence Report — P1-PERF-20261008-161246-backlog-list-snapshot-swr（Issue #246）

## 人审导航 / Human Review Navigation

> 两个肉眼呈递物如下（截图为 local-only 采集物，不进版本库；在仓库工作树内直接打开即可）。
> 打开方式：`open "tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/<文件>"`（相对仓库根目录）。

### 1 · 重启 console 后 Backlog 页 1 秒内秒开 + 「数据截至 HH:MM:SS」（rv-1）

![rv-1 重启后首屏秒开（真实入口截图）](rv-1-first-paint.png)

- 预期看到：3 条真实 PRD 卡片；表头 `3 个 PRD · 数据截至 07:07:21`（时间以实际采集为准）。
- 验证层级：**real-entry**——`kc console` 真实进程重启后，Playwright chromium 打开 `http://127.0.0.1:8319/app/backlog/`，实测导航→条目可见 **166ms**。
- 10 秒自检复跑：`set -o pipefail; bash tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/scripts/rv1_capture.sh 2>&1 | tee tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/rv-1-first-paint-run.txt`
- 配套帧：刷新后时间戳不变（同一份快照原样返回）：

![rv-1 刷新自检：时间戳不变](rv-1-reload-same-timestamp.png)

- 负控制帧（删除快照行 + GitHub 不可达时，页面停在「正在同步…」空态）：

![rv-1 负控制：无快照空态](rv-1-negative-control-no-snapshot.png)

### 2 · 数据过期时「后台更新中」出现、追平后消失（rv-7 两帧合成）

![rv-7 stale→fresh 两帧合成](rv-7-stale-fresh.png)

- 预期看到：上帧 `3 个 PRD · 数据截至 08:00:00 · 后台更新中…`；下帧 `4 个 PRD · 数据截至 08:05:00`，新条目「Backlog E2E Fresh Arrival P…」出现、提示消失。
- 验证层级：**e2e real-entry**——真实 dev 栈 + 真实浏览器渲染与轮询逻辑；仅列表 API 响应时序为 route mock（这正是被测的 stale→fresh 轮询契约本身）。
- 单帧原图：`rv-7-stale.png`（stale 帧）与 `rv-7-fresh.png`（fresh 帧）：

![rv-7 stale 帧原图](rv-7-stale.png)

![rv-7 fresh 帧原图](rv-7-fresh.png)

`rv-7-no-snapshot.png`（无快照「正在同步」空态）：

![rv-7 无快照空态帧](rv-7-no-snapshot.png)

- 10 秒自检：本地 console 停留 Backlog 页，等数据过期（>30s）后观察「后台更新中」出现又消失。
- 复跑：`bash tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/scripts/rv7_capture.sh`

PR / CI 链接：由 runner 在交付 PR 创建后回填。

**执行器已交叉核对**：7/7 oracle 全部先负控制变红、后 green 变绿；机器断言组（rv-2～rv-6）不占用你的人审时间，明细见下节。

---

## 交付明细（执行侧）

- 分支 / HEAD：`issue-246 @ a74d8078`（实现与补强已随分支提交；本节数字为门禁复跑在最终树上的实测值）
- 采集物清单与机器可读断言：`evidence.json`（version 1，7 项，均含 negative_control / expected_fail / stdout_assertions）
- 采集计划与命令：`P1-PERF-20261008-161246-backlog-list-snapshot-swr.verification-plan.md`
- 人审清单：`human-review-checklist.md`（+ 交互版 `human-review-checklist.html`）
- 所有采集脚本位于 `scripts/`，不进代码 diff（已核验 `git diff --name-only` 无 RV 脚本）

### rv-1 · 重启后秒开（R2 · human）

命令：`set -o pipefail; bash scripts/rv1_capture.sh 2>&1 | tee rv-1-first-paint-run.txt`（报告同时到 stdout 与证据文件，门禁复跑断言的是 stdout）
关键判定行（逐字；其中 PID / 毫秒数 / ISO 时间戳每次重跑按当次实测刷新，判定阈值与结论不变）：
- `VERDICT: 服务进程 PID 由 91742 变为 92470，快照行原样存活 ✓`
- `- 响应耗时 0.012451s < 500ms ✓`
- `RV1 VERDICT: PASS — first-paint-after-restart（firstPaintMs=166，scanned_at=2026-10-08T23:07:21+00:00）`
- `- 两次读取的 scanned_at 均为 2026-10-08T23:07:21+00:00 ✓`
- 负控制：`prds=[] stale=true scanned_at=null`、空态持续 8s+、浏览器 `RV1 VERDICT: PASS — negative-control-no-snapshot`
- 断言 A（失败不写脏快照）：等到 console 日志 `Monitor sync failed` 判负后 `- 快照表行数仍为 0（重建失败即中止，不落成一份降级快照）✓`，读取仍 28.2ms
- 断言 B（恢复探针）：只删 gh 桩的 blocked 标记（同一进程、同一 PATH）后重扫自动落地，`- 恢复后响应: prds=3, stale=False, scanned_at=2026-10-08T23:08:47+00:00`，条目状态取自真实 GitHub 解析（blocked / running）
- `OVERALL: PASS — rv-1 正控制（重启后秒开 + 数据截至 + 时间戳稳定）与负控制（删快照→空态、失败不写脏快照、恢复自愈）均按预期`

### rv-2 · 快照持久化跨进程重启（R2 · verifier）

命令：`uv run python scripts/rv_pytest_capture.py --item 2 --out rv-2-snapshot-persistence.txt`
负控制（upsert→no-op）`4 failed, 2 passed` → green `6 passed`；`VERDICT: green run 全绿 ✓`。

### rv-3 · stale 契约 + 后台触发 + 去重（R2 · verifier）

命令：同上 `--item 3`；负控制（拿掉 request_sync）`7 failed, 2 passed` → green `9 passed`。

### rv-4 · 启动预取 / sync 关闭不扫 / import 无副作用（R1 · verifier）

命令：同上 `--item 4`；负控制（missing provider 置空）`1 failed, 4 passed` → green `5 passed`。

### rv-5 · 失败保留旧快照（R1 · verifier）

命令：同上 `--item 5`；负控制（失败清场写空态）`1 failed, 1 passed` → green `2 passed`。

### rv-6 · start 触发重扫 + 旧缓存彻底移除（R1 · verifier）

命令：同上 `--item 6`；负控制（拿掉 resync 触发）`1 failed, 2 passed` → green `3 passed`；
缓存核查：main 基线（`git show main:` 只读）同一文件 `_BACKLOG_CACHE` 命中 10 行 → 交付树 `src/`+`tests/` 零命中；`OVERALL: PASS`。

### rv-7 · 前端短轮询 + 新鲜度提示 + 空态（R1 · human，e2e）

命令：`bash scripts/rv7_capture.sh` → `rv-7-e2e-run.txt`
负控制（page.tsx 换成 `scripts/rv7_page_baseline.tsx` 改动前实现）`2 failed`（E2E-6/E2E-7 红，EXIT trap 恢复并做注入前后校验和比对）→ green `6 passed (9.2s)` → `OVERALL: PASS`。

## 全局门禁

- `CI=true just test all`：最终交付树全绿（3693 passed / 1 skipped，exit 0）
- `just lint`：通过（pre-commit 全项 Passed，`just test`/`just lint` 标记随最终树重跑刷新）
- 前端 `pnpm typecheck` 通过；`pnpm lint` 仅 4 处未触碰文件的历史基线 error（prd-ci-view / prd-evidence-view / prd-lifecycle-view 等，非本次引入）
- 独立 verifier 结论：由 runner 执行后回填（`<prd-stem>.verifier-report.md`）
