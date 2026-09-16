# 证据报告 · iar console dashboard 本地快照缓存与可配置后台定时同步

PRD：`tasks/pending/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync.md`
分支：`perf/iar-console-dashboard-snapshot-sync`（worktree `/Users/zata/code/keda-worktrees/perf/iar-console-dashboard-snapshot-sync`）
最终实现树：`0d3687777ede`（main）+ 本分支工作区改动（全部证据在**修正后**的实现树上重采，见下「独立复核与修正」）
证据采集日期：2026-09-16 / 2026-09-17

## 人审导航 / Human Review Navigation

> 原始证据（录屏 / 截图 / 日志 / txt）按仓库约定**不进 git**（`tasks/evidence/**` 白名单只提交 `.md`），
> 因此下表中的二进制与 txt 呈递物是**本机可见、GitHub 不可见**的 local-only 文件；
> 请在本机 worktree 内按"打开命令"直接查看。

| 要看的结果 | 呈递物（本机绝对路径） | 打开命令 | 逐项期望值 | 执行者已核对 |
|---|---|---|---|---|
| dashboard 首屏来自本地快照、秒开、显示"上次同步"，手动刷新期间旧数据仍可浏览、完成后数据与时间戳更新 | `/Users/zata/code/keda-worktrees/perf/iar-console-dashboard-snapshot-sync/tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-2-dashboard-refresh.webm`（local-only） | `open tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-2-dashboard-refresh.webm` | 录屏三段：①全新环境显示"尚未同步"空态 → 后台首扫完成后自动出数据；②重进页面首屏 **109ms** 渲染出仓库卡片与"上次同步 2026-09-17 02:03:38"；③点"刷新全部"后旧卡片仍在、设置面板可交互、时间戳在扫描完成后变为 02:03:45 | 已核对：`rv-2-dashboard-timeline.txt` 的四条时间戳与录屏一致；快照接口 5 次计时 18.3–24.2ms |
| 快照接口本地响应远小于 1s | `.../rv-2-snapshot-latency.txt`（local-only） | `cat tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-2-snapshot-latency.txt` | 5 次 `GET /api/v1/agent-runner/overview/snapshots` 全部 HTTP 200，`time_total` 0.0183–0.0242s | 已核对：全部 < 500ms 阈值一个数量级以上 |
| 设置面板改间隔立即生效、周期与设置一致（60s）、关闭后零自动同步、重启后设置保持 | `.../rv-3-scheduler-observation.md`（**随提交进入 git**）+ `.../rv-3-settings-panel.png`、`.../rv-3-settings-disabled.png`（local-only） | `cat tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-3-scheduler-observation.md`；`open tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-3-settings-panel.png` | 面板展开态：自动同步「开启」+ 同步间隔「1 分钟」为深色选中态、状态"已保存 ✓"；观察记录给出 01:58:29 与 01:59:29 两条周期记录（**相差 60s**）、关闭窗口 130s 内 0 条新周期记录、手动刷新仍更新快照、重启后 `GET` 返回同一份设置 | 已核对：面板选中态由脚本用计算样式断言（唯一非白底按钮 = 刚保存的那个），与截图同一次采集；设置值一律以 `GET .../monitor/settings` 响应原值为准 |

**内联呈递（本机 Markdown 预览可见，GitHub 上因 gitignore 不显示，属 local-only）：**

![rv-3 设置面板展开态：开启 + 1 分钟选中，已保存](rv-3-settings-panel.png)

- PR：[#143 feat(console): dashboard 本地快照缓存与可配置后台定时同步](https://github.com/ZataZhang/keda/pull/143)
- CI：本分支运行 `just lint --repo`（含 full lint / reuse / `just test` / `mkdocs build --strict`）、`just test all`、`just e2e`（证据见下表）

**执行者已替人完成的核对**：rv-1、rv-4、rv-5、rv-6 全为自动化 oracle（迁移往返、调度生命周期、HTTP 契约、Playwright、门禁），
按 PRD §9.1 约定不在人读呈递区展示，仅在下表给出原始输出位置；人读只需看上表三项。

## Oracle 结果

| Oracle | 结论 | 证据文件 | 关键值 |
|---|---|---|---|
| rv-1 v3→v4 迁移保留历史 | PASS | `rv-1-migration.txt` | 迁移相关 5 项全绿（`test_v3_database_migrates_to_v4_and_keeps_history` 等） |
| rv-1 negative control（实现前） | 期望红 ✅ | `rv-1-negative-control.txt` | main `_SCHEMA_VERSION=3` → `no such table: monitoring_snapshots`、`user_version=3`、exit=1；实现后 `user_version=4`、3 条历史行保留、exit=0 |
| rv-2 真实入口 dashboard | PASS | `rv-2-dashboard-refresh.webm`、`rv-2-dashboard-timeline.txt`、`rv-2-snapshot-latency.txt` | 空态可见；首屏 109ms；刷新中旧卡片可见 + 面板可交互 + 时间戳未变；刷新后 02:03:38 → 02:03:45；接口 18.3–24.2ms |
| rv-3 设置持久化与调度一致 | PASS | `rv-3-scheduler-observation.md`、`rv-3-settings-panel.png`、`rv-3-settings-disabled.png` | PATCH→GET 读回 516ms；面板选中态断言通过；周期 01:58:29 / 01:59:29（60s）；关闭窗口 130s 内 0 条新周期记录；重启后设置逐字段一致且 0 条自动同步；手动刷新成功 |
| rv-3 negative control（实现前） | 期望红 ✅ | `rv-3-scheduler-observation.md` 末节 | 实现前三个新增端点全 404；实现后全 200 |
| rv-4 调度与并发（core/store/scheduler） | PASS | `rv-4-scheduler-and-core-tests.txt`（52 passed） | import 零副作用子进程探针、lifespan 单实例与回收、stop 超时保留句柄、同 repo 去重 / 跨 repo 并行、线程句柄回收、写失败传播、快照形状校验、关闭后零扫描 |
| rv-4 HTTP 契约（快照/设置/job） | PASS | `rv-4-monitor-api-tests.txt`（12 passed） | unreachable 透传、区间 422、两字段必填、PATCH 唤醒、写失败 500、job 写回与失败传播 |
| rv-5 dashboard e2e | PASS | `rv-5-e2e-monitor.txt`（4 passed） | 快照驱动首屏 + 上次同步时间；空态不建扫描 job；设置面板 PATCH canonical path |
| rv-5 既有 console 页面回归 | PASS | `rv-5-e2e-console-pages.txt`（5 passed） | dashboard 无法访问警示条（既有断言，此前在 main 上已红）连同四页 smoke 全绿 |
| rv-6 仓库门禁 | PASS | `rv-6-lint-repo.txt` | `just lint --repo` 全链路 0 error |
| rv-6 全量测试 | PASS | `rv-6-full-test-suite.txt` | `just test all`（`--no-testmon`）**2190 passed**（本分支新增 64 项 monitor 相关用例） |
| rv-6 前端构建 | PASS | `rv-6-frontend-build.txt` | `tsc --noEmit` 无错；静态导出成功（13 页，含 `/app/dashboard`） |

## 独立复核与修正（2026-09-17）

第一轮独立 verifier（只读 Explore agent）给出 **PASS with caveats**，结论"功能可进入 PR/归档，无阻塞缺陷"，
同时指出 5 处非阻塞缺口与 1 处证据口径矛盾（`rv-3-settings-panel.png` 曾截到"已保存但高亮 5 分钟"）。
执行者逐条处置并**重采受影响证据**：

| 复核发现 | 处置 | 复核方式 |
|---|---|---|
| 设置面板挂载 GET 会覆盖用户刚做的选择（截图矛盾的根因） | `monitor-settings-panel.tsx` 加 `userEditedRef` 守卫 | `rv-3-settings-panel.png` 重拍；脚本内新增计算样式断言（唯一非白底 = 刚保存的间隔），断言不通过则采集失败 |
| `MonitorSyncScheduler.stop()` join 超时仍清句柄 → 可能起第二个循环 | 超时保留句柄 + warning；`start()` 被 `is_alive()` 挡住 | 新增 `test_stop_timeout_keeps_handle_so_no_second_loop_starts` |
| 协调器 `_threads` 只在测试专用路径回收 | `_run_scan` 的 finally 顺手回收 | 新增 `test_coordinator_reclaims_finished_thread_handles` |
| `get_snapshot_overview` 的 `in_flight_repo_ids` 是死参数 | 删除参数与 route 侧取值 | core/route 单测与 API 单测全绿 |
| 合法 JSON 但结构错误的快照被当 ready 下发 | 新增 `_is_renderable_overview` 最低结构校验 | 新增 `test_malformed_snapshot_is_treated_as_missing`（3 组参数） |
| PRD 口径：基线测试数、job 读回快照的实现偏差 | 见 PRD §14 Change Log 新增两条 | 本节与 PRD 已同步 |

修正后复跑：rv-4（52+12 passed）、rv-5（4+5 passed）、rv-6（2190 passed、lint 0 error、前端构建通过），
rv-2（webm + 计时）与 rv-3（截图 + 观察记录）全部重采。

## 变更清单（与 PRD §7 对照）

- 实现文件与 PRD §7 一致；实现期新增的测试文件（`tests/test_monitor_scheduler.py`、`tests/test_monitor_api.py`）
  与 e2e 改动、以及独立复核后的修正，均见 PRD §14 Change Log。
- 额外修正：dashboard 恢复"注册路径失效"警示条（改由快照响应的 `unreachable_repositories` 驱动），
  使既有 e2e 断言在 main 上已红的 `console-pages.no-auth.spec.ts` 重新变绿（见 Change Log 第一条）。
- 未修改任何既有 API 契约：`/overview`、`/overview/per-repo`、`/overview/jobs/{id}`、`/issues/{n}` 行为不变。

## Mock 边界与负控

- **真实执行**：SQLite（tmp 真实文件库 / 真实 `~/.iar/console.db` 结构）、FastAPI lifespan 与真实线程、
  Chromium 真实渲染、真实 `iar console` 进程、真实 `gh` 扫描（单仓库 registry，经 `IAR_CONFIG` 覆盖，不改提交文件）。
- **打桩**：GitHub/`gh` 在单测与 e2e 中以 fake 扫描器 / Playwright 路由 stub 替代；API 测试把
  `_build_overview_response` 换成固定 payload（与"调用次数"断言互补）。
- **负控**：rv-1（实现前 `no such table`）、rv-3（实现前 404）；rv-4 用注入写库异常验证"失败不伪装成功"。

## 已知限制

1. rv-2/rv-3 的录屏与截图是**本机 local-only** 文件（仓库约定原始证据不进 git），GitHub 上无法直接观看。
2. rv-3 关闭窗口内 `scanned_at` 仍会变化一次：来自关闭前已派发的在途扫描（PRD 明确"已在途扫描不取消"）；
   该窗口是否有**新的自动调度**由周期日志计数判定（0 条）。
3. 真实 `gh` 扫描在本机执行，未覆盖 GitHub 网络故障的极端组合；该路径由 rv-4 的写失败/读取失败注入用例覆盖。
4. 未做多仓库（>2）的端到端时延压测；协调器的并发语义由 `barrier` 型单测覆盖（14 仓库并发探针见 verifier 报告）。
