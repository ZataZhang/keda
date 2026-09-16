# 独立 verifier 报告 · P1-PERF-20260916-102117 iar console dashboard 快照同步

- 审查对象：`perf/iar-console-dashboard-snapshot-sync`（worktree `/Users/zata/code/keda-worktrees/perf/iar-console-dashboard-snapshot-sync`，基线 `0d3687777ede` + 工作区改动）
- 审查方式：**只读** Explore agent（无 Write/Edit 工具），禁止一切 git 写操作、禁止 `just lint/test/e2e/sync-template`；结论全部来自代理自己读到的代码与自己跑出的输出
- 轮次：第一轮全量复核（2026-09-17）→ 执行者修正 → 第二轮针对修正与重采证据的复核（2026-09-17）

## 结论摘要

- 第一轮：**PASS with caveats** —— 核心机制（v4 迁移、唯一写库入口、按仓库协调器、lifespan 调度、快照/设置端点、前端快照首屏）逐条与实现相符，无阻塞缺陷；提出 5 处非阻塞缺口 + 1 处证据口径矛盾。
- 第二轮：**PASS with caveats** —— 5 条修正全部真实落地（用不落盘探针逐条复现），重采的 rv-2/rv-3 证据自洽，`rv-3-settings-panel.png` 的口径矛盾已消除（像素级确认）；剩余为口径/覆盖类建议，无阻塞。

## 第一轮逐项复核（摘要）

| 命题 | 结论 | 代理给出的关键证据 |
|---|---|---|
| v3→v4 迁移保留历史、既有表零改动 | PASS | `_SCHEMA_VERSION=4` + v4 分支只新增两表；测试用自带 v3 DDL 播种并以独立连接复核；`uv run pytest tests/test_console_store.py -v` 17 passed |
| 写失败不伪装成功 | PASS | `persist_monitoring_result` 按仓库收集失败；`_scan_repository_and_persist` 抛 `MonitorSyncError`；PATCH 写失败 500；36 passed |
| 不为写库重复扫描 | PASS | 代码只有一次 `_build_overview_response`；`test_overview_job_persists_snapshot_without_rescanning` 断言调用次数 |
| lifespan 单实例 / import 零副作用 / 并发去重 | PASS | 启停只在 `app.py` lifespan；子进程 import 探针；barrier 型并发用例；14 仓库并发探针全部并行、同仓库 10 并发只启动一次 |
| registry 过滤与 unreachable 透传 | PASS | 读路径先建启用集合过滤；端点由 registry 解析失败项填充（非硬编码空列表） |
| 前端快照首屏 / 空态不建 job / PATCH canonical path | PASS | 首屏与 15s 轮询同源；空态由 `sync_status` 驱动且无自动 job；e2e 断言 PATCH 的 URL 与请求体 |
| 证据真实性 | PASS（1 处矛盾） | rv-1 负控脚本确实切换源码树；webm 有效且时长与时间线吻合；**`rv-3-settings-panel.png` 曾显示"已保存但高亮 5 分钟"** |
| PRD 自洽性 | PASS（2 处口径） | 三条断言命令均由代理重跑为真；指出"基线 2126"不可复现、"唯一改动的既有测试文件"对 Playwright spec 不适用 |
| 反例探针 | 9 条 | 其中 5 条被采纳为待修项（见下），另 4 条确认为既有语义或已覆盖 |

第一轮代理明确列出"我无法验证的部分"：未逐帧观看 webm、未重跑 `just lint/e2e/build`、未在实现前树上单独复跑负控、rv-3 重启保持与负控是脚本外手工补录。

## 发现与处置（第一轮）

| # | 发现 | 严重度 | 处置 | 第二轮确认 |
|---|---|---|---|---|
| 1 | 设置面板挂载 GET 会覆盖用户刚做的选择（截图矛盾根因） | 建议 | `monitor-settings-panel.tsx` 加 `userEditedRef` 守卫 | 落地：守卫在 `persist()` 同步置位，先点击后响应时 GET 被丢弃；像素采样确认新截图为「开启 + 1 分钟 + 已保存」 |
| 2 | `stop()` join 超时仍清句柄，配合 `start()` 可起第二个循环 | 建议 | 超时保留句柄 + warning；`start()` 被 `is_alive()` 挡住 | 落地：探针显示 stop 超时后 `running=True`、句柄同一对象、线程数不变，释放后干净停止 |
| 3 | `_threads` 只在测试专用路径回收 | 建议 | `_run_scan` finally 回收已结束句柄 | 落地：探针 25 次扫描后仍为 1；新增单测改为**不经过** `wait_until_idle` 断言（第二轮指出原写法是假护栏，已修正） |
| 4 | `in_flight_repo_ids` 死参数 | 建议 | 删除参数与 route 侧取值 | 落地：全仓仅剩协调器公开方法与测试调用；响应字段与前端类型逐字段一致 |
| 5 | 合法 JSON 但结构错误的快照被当 ready 下发 | 建议 | 新增 `_is_renderable_overview` 最低结构校验 | 落地：8 组探针（含合法控制组）符合预期 |
| 6 | PRD 口径：基线测试数 / job 读回快照未记入 Change Log | 建议 | 修正 PRD §9 与 §14 | 已同步 |
| 7 | 证据：`rv-3-settings-panel.png` 与叙述矛盾 | 建议（接近阻塞人审） | 修竞态 + 重拍 + 采集脚本内加计算样式断言 | 矛盾消除 |
| 8 | 竞态守卫无自动化覆盖 | 建议 | 先补了一条「延迟初始 GET」的 e2e 用例；对抗自检（临时移除守卫重跑）显示该用例在两条路径下都通过、不具判别力，遂删除，改为在 PRD §12 登记为跟进项 | 覆盖缺口如实保留，未留假护栏 |

## 第二轮复核的关键输出

- 面板竞态：`monitor-settings-panel.tsx:51/57/64/76` 的守卫与置位顺序正确；代理用 node 复刻同构时序验证"后到的 GET 被丢弃"。
- 调度器护栏探针输出（节选）：`running after timed-out stop: True` → `handle kept (same object): True` → `after start() again: same handle object: True | live thread count unchanged: True`。
- 协调器句柄：`after scan 1: len=1`、`after scan 2..5: len=2`、`settled after 25 scans: 1`（有界）。
- 重采证据体检：rv-3 周期 01:58:29→01:59:29 = 60s 与原始 `/tmp/rv-console-2.log` 逐字一致；关闭窗口切片正确、0 条新周期；重启日志头部与文件引用逐字一致；negative control 基线树 `_SCHEMA_VERSION=3` 且无新端点；rv-2 时间线 UTC/本地换算自洽、时延 18.3–24.2ms；11 个 txt 无隐藏 FAILED；`git diff --cached --name-only` 中证据目录只含 3 个 `.md`。
- 代理自跑 `uv run pytest tests/test_monitor_scheduler.py tests/test_monitor_snapshots.py tests/test_monitor_api.py tests/test_console_store.py -q --no-testmon` → **64 passed**（与证据拆分口径 52 + 12 一致）。

## 第二轮遗留建议（未阻塞，已记录）

1. `rv-6-lint-repo.txt` 首次采集早于修正落盘：已在修正后重跑 `just lint --repo` 并覆盖该证据（0 error）。
2. 本节所引用的第一轮报告在会话中产生，未落盘为独立文件；本文件即两轮结论的合并记录。
3. 面板 `loadError` 文案把"初始加载失败"与"保存失败"统一显示为"保存失败："（既有行为，非本次引入）。
4. PATCH 恒发两个字段、取自本地 state：若用户在初始 GET 返回前改间隔，会用本地默认 `sync_enabled=true` 覆盖服务端可能为 false 的值（修正前后行为一致，属既有设计取舍）。

## 代理明确无法验证的部分

- 未逐帧观看 `rv-2-dashboard-refresh.webm`（只验证格式/时长/与时间线一致）。
- 未重跑 `just lint --repo` / `just test all` / `just e2e` / `just frontend-public build`（受只读约束），改用同版本 ruff 对改动文件做局部替代验证。
- rv-3 重启前后的 GET 响应体原文与静默窗口起点的 `scanned_at` 中间值已无磁盘残留可对账（结论不受影响）。
- 未在实现前代码树上重跑 rv-1/rv-3 负控（只核实脚本逻辑、基线路径与输出中的版本常量）。
