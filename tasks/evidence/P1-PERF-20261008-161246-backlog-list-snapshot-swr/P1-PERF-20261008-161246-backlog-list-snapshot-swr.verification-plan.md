# Verification Plan — P1-PERF-20261008-161246-backlog-list-snapshot-swr

> 本文件是 PRD §7 Realistic Validation Plan 的执行投影：每条 oracle 的采集入口、
> 负控制注入、预期变红形态与产出证据文件。全部脚本位于 `scripts/`（不随代码提交）。
> 通用纪律：**负控制先行**——先注入故障证明断言会红，记录 `expected_fail`，再恢复
> 交付树跑绿；退出码 0 ≠ 通过，每个检查点必须打印可核对的判定行。

## rv-1 · 快照存在时 console 重启后 1 秒内秒开列表（R2 · human）

- 真实入口：`kc console`（隔离场景 `/tmp/rv246-rv1-scene`，独立 console.db，端口 8319）+ Playwright chromium 打开 `http://127.0.0.1:<port>/app/backlog/`
- 采集命令：`bash scripts/rv1_capture.sh`（浏览器测量用 `scripts/rv1_firstpaint.mjs`）
- 关键断言：冷启动预取真实 gh 扫描落库 → 杀进程重启（监听 PID 必变）→ 列表接口响应 <500ms 且 `prds>0`/`stale=false`/`scanned_at` 非空 → 浏览器导航到 PRD 条目可见 ≤1000ms 且表头匹配 `数据截至 HH:MM` → TTL 窗口内连续两次读取 `scanned_at` 不变，页面所属响应与 curl 直读一致
- 负控制：DELETE `backlog_prd_snapshots` 全部行 + PATH 前置**可开关**的 `gh` 边界桩（blocked 标记存在时 sleep 60，被客户端 15s 超时击杀 → 本轮重建必然失败；删掉标记后同一个桩原样转发真实 gh），重启后同路径：接口必须返回 `prds=[] + stale=true + scanned_at=null`，空态持续 ≥8 秒（重扫不阻塞读），浏览器停在「正在同步」；断言 A——等到 console 日志出现 `Monitor sync failed` 判负后，快照表行数必须仍为 0（失败的重建不得把降级结果写成快照）；断言 B——把 GitHub 切回可达后，后台重扫必须自动落地并恢复 `stale=false` + 新 `scanned_at`，证明空态是"重建中/重建失败"而非"什么都没发生"
- expected_fail：若读路径仍内联等待扫描 → 首屏耗时回到数十秒 / 空态窗口不出现；若快照未跨进程持久化 → 重启后首请求 prds 为空且 >500ms
- 产出：`rv-1-first-paint-run.txt`、`rv-1-first-paint.png`、`rv-1-reload-same-timestamp.png`、`rv-1-negative-control-no-snapshot.png`

## rv-2 · 快照持久化跨进程重启有效，新表不影响既有表（R2 · verifier）

- 真实入口：`uv run pytest`（6 个 node id，含 `test_console_store.py::test_backlog_snapshot_migration_v10_only_adds_its_table`）
- 采集命令：`uv run python scripts/rv_pytest_capture.py --item <N> --out <证据目录>/rv-<N>-<slug>.txt`（负控制红→green 两次运行，故障只注入临时副本树 `-o pythonpath=<tmp>/src`，交付树全程不改）
- 负控制：临时副本把 `upsert_backlog_snapshot` 改成 no-op（`-o pythonpath=<tmp>/src`），4 个持久化用例必须红；恢复后全绿
- expected_fail：持久化缺失（仅内存缓存）时新 store 实例读回 None → 测试红
- 产出：`rv-2-snapshot-persistence.txt`

## rv-3 · 列表接口立即返回、过期携带 stale 并触发后台重扫（R2 · verifier）

- 真实入口：`uv run pytest`（9 个 node id：快照编排 + `test_backlog_api.py` 路由级用例）
- 负控制：临时副本拿掉 `ensure_fresh_backlog_snapshot` 的 `request_sync` 触发 → 7 个用例红
- expected_fail：若接口内联构建响应，GET 耗时不受控且探针在响应前被同步调用 → 红
- 产出：`rv-3-stale-contract.txt`

## rv-4 · console 启动预取为缺失快照的启用仓库补扫（R1 · verifier）

- 真实入口：`uv run pytest`（5 个 node id：missing 判定、import 无副作用、lifespan 拥有循环并预取、sync 关闭不扫、stop/wake 幂等）
- 负控制：临时副本把 `missing_repo_id_provider` 置空 → `test_lifespan_owns_the_backlog_loop_and_prefetches_missing_repos` 红
- expected_fail：预取失效时 lifespan 用例等不到快照行落库 → 红
- 产出：`rv-4-startup-prefetch.txt`

## rv-5 · 同步失败时旧快照原样保留（R1 · verifier）

- 真实入口：`uv run pytest`（`test_failed_scan_keeps_previous_snapshot`、`test_persist_failure_propagates_and_keeps_previous_snapshot`）
- 负控制：临时副本让扫描抛错时把快照改写为空态（模拟失败清场）→ 用例红
- expected_fail：失败清场时读路径拿到空数据 → 红
- 产出：`rv-5-failure-keeps-snapshot.txt`

## rv-6 · start 动作触发该仓库立即重扫，旧缓存路径彻底移除（R1 · verifier）

- 真实入口：`uv run pytest`（3 个 node id）+ `scripts/rv6_cache_removal_check.py` 对交付树 `rg _BACKLOG_CACHE` 基线/终态对比
- 负控制：临时副本拿掉 `start_backlog_prd` 成功后的 `request_backlog_resync` → `test_start_prd_triggers_one_resync` 红
- expected_fail：失效语义回退为删缓存/不触发时，探针计数为 0 → 红；缓存残留时 removal check 非零命中 → 红
- 产出：`rv-6-start-triggers-resync.txt`

## rv-7 · 前端 stale 短轮询自动追平 + 新鲜度提示 + 无快照空态（R1 · human，e2e）

- 真实入口：`just run all` 随机端口 dev 栈 + `just e2e tests/smoke/backlog-realistic.spec.ts`（E2E-6/E2E-7 为本次新增）
- 采集命令：`bash scripts/rv7_capture.sh`（内部先负控制后 green；截图经 `RV_EVIDENCE_DIR` 落到本目录，合成用 `scripts/rv7_compose.mjs`）
- mock 边界：仅列表 API 响应时序（stale→fresh，这正是被测轮询契约本身）；渲染、轮询、提示逻辑全真
- 负控制：把 `page.tsx` 换成基线副本 `scripts/rv7_page_baseline.tsx`（改动前实现：固定 30s 轮询、无新鲜度提示）只影响测试运行时磁盘编译产物；基线不取 `git show HEAD:`，因为门禁复跑发生在提交之后，那时 HEAD 已是新实现，EXIT trap 恢复，交付树不留痕
- 关键断言：stale 帧显示「数据截至 + 后台更新中」；第 2 次列表请求与第 1 次间隔 ≤5000ms；fresh 后提示消失、新条目出现；无快照（`prds=[] scanned_at=null stale=true`）显示「正在同步」空态且无错误文案
- expected_fail：旧实现下「数据截至/后台更新中」不渲染、追平间隔回到 30s → E2E-6 红；无快照时空态文案不符 → E2E-7 红
- 产出：`rv-7-e2e-run.txt`、`rv-7-stale.png`、`rv-7-fresh.png`、`rv-7-no-snapshot.png`、`rv-7-stale-fresh.png`（合成呈递）

## 全局门禁（非 rv 条目，随交付树核验）

- `CI=true just test all` 全绿（3685 passed / 1 skipped，见运行日志 `/tmp/rv246-test-all.log`）
- `just lint`（后端）+ `pnpm typecheck`（frontend-public）通过；前端既有 4 处 lint error 为未触碰文件的历史基线
- `rg -n "_BACKLOG_CACHE" src/ tests/` 零命中（rv-6 内记录基线 10 命中 → 交付树 0）
