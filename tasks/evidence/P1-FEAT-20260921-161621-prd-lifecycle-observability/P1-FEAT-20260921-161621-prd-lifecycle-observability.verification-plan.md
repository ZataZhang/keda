# Verification Plan — P1-FEAT-20260921-161621 PRD 生命周期观测与执行分析

- PRD: `tasks/pending/P1-FEAT-20260921-161621-prd-lifecycle-observability.md`
- 实施分支: `prd-lifecycle-observability`（worktree `/Users/zata/code/keda-worktrees/prd-lifecycle-observability`）
- 基线与最终树: 见 `<stem>.evidence-report.md` 的“最终代码树”一节
- 本文件是执行者的自我验证计划；独立 verifier 的结论见 `<stem>.verifier-report.md`。

## 验证层级与范围

| 层 | 覆盖对象 | 入口 |
|---|---|---|
| integration（pytest） | 事件幂等、阶段推导、四类耗时可复算、分位数、旧记录降级、存储故障负控 | `uv run pytest tests/test_prd_lifecycle.py -q -o addopts=""` |
| integration（pytest） | schema v4→v5 迁移、run/event 追加与 fresh read | `uv run pytest tests/test_console_store.py -q -o addopts=""` |
| API 契约（pytest + TestClient） | Roadmap lifecycle 详情端点、Console PRD 统计端点（fresh HTTP read） | `uv run pytest tests/test_prd_lifecycle.py -k endpoint -q -o addopts=""` |
| 真实入口（浏览器 + 真实后端 + 真实 SQLite） | rv-1 / rv-2：静态前端 → `/api/v1` → FastAPI → core 聚合 → SQLite → 渲染 | `SKIP_CONSOLE_SYNC=1 bash tasks/evidence/<stem>/scripts/capture_real_console.sh` |
| e2e（Playwright，真实 Next.js 页面 + 浏览器交互） | Roadmap 执行过程标签、事件抽屉、失败/重试历史、不完整告警、Stats 多维筛选往返 | `PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local just e2e tests/smoke/roadmap-prd-lifecycle.spec.ts` |
| 全仓回归（pytest） | 既有行为不被旁路观测破坏 | `uv run pytest tests -q -o addopts=""` |
| 架构边界 | core/infrastructure/api 依赖方向 | `uv run python hooks/shared/check_architecture.py` |
| 文档构建 | 新增指南小节 + 原型导航 | `uv run mkdocs build --strict` |
| 前端静态检查 | 类型、构建 | `pnpm --dir frontend-public typecheck` / `build` |
| 人工原型 | rv-5 交互原型：失败轨迹、事件抽屉与 Stats 往返 | `uv run mkdocs serve` → `http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html` |

## 映射到 PRD 的 Realistic Validation Plan

- **rv-1（Roadmap 单 PRD 执行过程）**：真实入口脚本 `capture_real_console.sh`
  在隔离 `HOME` + `IAR_CONFIG` 下启动真实 uvicorn（同时提供已 `console-sync`
  的静态前端与真实 `/api/v1`），只 mock 会话守卫，其余请求全部真实。产出
  `rv-1-roadmap-lifecycle.png`（当前阶段 + 四项耗时 + 有序时间线）与
  `rv-1b-roadmap-event-drawer.png`（事件抽屉显示 run id / 时间 / Agent / 原因），
  以及 fresh HTTP 响应 `rv-1-lifecycle-detail.json`。
  - 值来源：真实 SQLite 账本写入 → 真实 `roadmap/prds/{encoded}/lifecycle`
    响应中的 `run_id` / `current_phase` / `durations` / `events`。
  - 必经边界：browser → Next.js 静态页 → `/api/v1` → FastAPI roadmap route →
    core 聚合 → SQLite → fresh HTTP 读回 → 浏览器渲染。
  - 禁止旁路：未直接渲染组件、未手工注入 React state、未前端重算时间线。
  - fresh-state probe：页面同源 `context.request.get(...)` 再读一次，与页面同源。
- **rv-2（Stats PRD 端到端统计）**：同一脚本产出 `rv-2-prd-stats.png` 与
  `rv-2-stats-response.json`。统计 SQLite fixture 由真实写入函数种入，脚本独立
  读出 avg/median/P90/平均阻塞/阶段瓶颈/每 PRD 明细；进行中与未关联旧记录不进入
  完成分位数。
- **rv-3（观测写入失败旁路降级）**：`tests/test_prd_lifecycle.py`
  的 `test_store_failure_marks_run_incomplete_without_raising` 注入
  `append_lifecycle_event` 抛错，断言主流程不抛异常且 fresh 读回
  `history_complete=false`；负控 `test_negative_control_healthy_store_stays_complete`
  去掉故障后必须为 `true`（去掉故障该负控会变红）。
- **rv-4（旧记录无法关联的降级）**：`test_legacy_runs_are_counted_and_excluded`
  断言旧 `run_records` 计入 `unlinked_run_count` 且不进入完成分位数；
  `test_console_prd_lifecycle_stats_endpoint` 断言 API 层同样披露。
- **rv-5（原型可从 Hub 打开并往返）**：`docs/prototypes/prd-lifecycle-observability.html`
  + Hub registry（`docs/prototypes/assets/prototype-hub.js`）已登记，
  `mkdocs build --strict` 通过；静态 fixture 明确标注模拟边界。
  - 呈递路径：Hub → 原型 → Roadmap 详情 → 事件抽屉 → 失败与重试 → Stats →
    Roadmap → Hub。

## 反空转与防漏

- 幂等：`test_event_append_is_idempotent_by_event_key` 同 key 重复写只留一条。
- 历史不被覆盖：`test_runner_events_do_not_overwrite_roadmap_prd_path` +
  时间线 fixture 同时保留 attempt/retry/recovered。
- 耗尽性：`test_duration_classification_is_mutually_exclusive` 断言
  `active + waiting + blocked == end_to_end`，页面数字可由时间线复算。
- 分位数：`test_stats_percentiles_over_completed_runs_only` 固定 fixture 手工
  可复算（600/1200/1800 → avg 1200、median 1200、P90 1680）。

## 未验证项的处理

- **Playwright spec 与真实 SQLite 穿越的分工（已按 R1 复核整改并写回 PRD）**：
  rv-1 / rv-2 的 `real_entry` 已改为 `capture_real_console.sh`（真实 uvicorn +
  真实 FastAPI + 真实 SQLite + 真实 Chromium，只 mock 会话守卫）；
  `just e2e tests/smoke/roadmap-prd-lifecycle.spec.ts` 降级为补充 UI 流程入口，
  其产物写入 evidence 目录的 `e2e/` 子目录，**不会覆盖** harness 的真实响应。
  两者均保留，provenance 可区分。
- **PRD 改名别名**：稳定 run id 以 Issue 编号为锚，改名后同一 run 继续累积事件并
  更新为最新 `prd_path`；但未实现“旧路径 → 新路径”的别名表，旧路径书签查不到
  历史。列为已知限制，不勾选对应假设为已完成。
- **`interactive_decision._execute_review_once` 路径未接观测**：该入口不经
  CLI/daemon 的 `run_history_store`，其中 review 事件不落账本；CLI `iar review` /
  `iar review-daemon` 已接入。列为已知限制。
- **观测缺口注入的 UI**：浏览器层不完整告警由 e2e fixture 覆盖（`RV-3b`）；后端
  真实故障负控由 pytest 覆盖（rv-3）。两者在同一验收项下互补，不互相替代。

## R1 复核整改回归

第一轮独立 verifier 判 REJECT（1 HIGH + 2 MEDIUM + 4 LOW），整改后新增回归：

- **HIGH（终态后重试破坏耗时等式）**：新增
  `test_retry_after_terminal_reopens_run_and_keeps_duration_invariant`（终态后非终态
  事件重开 run，四类耗时仍相加等于端到端，失败事件仍在时间线）、
  `test_classify_durations_stays_consistent_when_finish_precedes_events`（防御性钳制）。
- **LOW（跨时区字典序）**：`test_classify_durations_orders_events_by_real_time_across_offsets`。
- **M1（证据文件名冲突）**：e2e spec 产物改到 `e2e/` 子目录。
- **M2（rv-1 real_entry 与实测入口不一致）**：PRD Realistic Validation Plan 已改写。
