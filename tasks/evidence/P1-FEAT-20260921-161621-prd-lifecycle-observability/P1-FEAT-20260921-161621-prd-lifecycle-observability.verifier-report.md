# Verifier Report — P1-FEAT-20260921-161621 PRD 生命周期观测与执行分析

- PRD: `tasks/pending/P1-FEAT-20260921-161621-prd-lifecycle-observability.md`
- 分支 / worktree: `prd-lifecycle-observability`
- 独立 verifier: 只读复核（禁改文件、禁 git 写操作），两轮
- 对应证据: `<stem>.evidence-report.md`、`<stem>.verification-plan.md`

## Round 1

- 冻结凭证: `git diff HEAD -- src tests frontend-public docs | shasum -a 256`
  = `70da13f416639aa60aa160dd0c15bd708b5c319c1153ca61f40524fbae0cc664` @ HEAD `dc2b99bb`（MATCH）
- 结论: **REJECT**（1 HIGH / 2 MEDIUM / 4 LOW）

| Sev | 位置 | 问题 | 处理 |
|---|---|---|---|
| HIGH | `agent_runner_lifecycle.py` 结束边界 + `console_store.py` `finish_lifecycle_run` | 终态后同 stable run 重试会追加更晚事件，`finished_at` 早于事件 → 「执行 + 等待 + 阻塞 == 端到端」被破坏，且出现 `outcome=failed / in_progress=false` 却 `current_phase=reviewing` | 已修：新增 `reopen_lifecycle_run` + 非终态事件重开 run；`classify_durations` 钳制结束边界；新增 2 条回归 |
| MEDIUM | `tests/playwright-e2e/.../roadmap-prd-lifecycle.spec.ts` | spec 与真实 harness 写同名证据文件，重跑会覆盖真实响应 | 已修：spec 产物改到 `tasks/evidence/<stem>/e2e/` |
| MEDIUM | PRD rv-1 `real_entry` | 指向 mock 读端点的 spec，无法满足 `must_cross` 的 SQLite 穿越 | 已修：rv-1/rv-2 `real_entry` 改为 `capture_real_console.sh` 并加 `entry_note` |
| LOW | `classify_durations` 排序 | 按原始时间字符串排序，跨时区偏移时字典序 ≠ 时序 | 已修：改按解析后 `datetime`（`_event_sort_key`）+ 回归 |
| LOW | `record_lifecycle_terminal` docstring | 与 `finish_lifecycle_run` 的无条件写行为不符 | 已修：docstring 改写 + `reopen` 仅对已收口 run 生效 |
| LOW | Change Impact Tree / `UNBLOCKED` 无生产者 | 树点名未触达的 `agent_runner_closeout.py`；`UNBLOCKED` 只在 fixture 出现 | 已修：树对齐实测文件；runner 在 blocked_resolution 产出 `UNBLOCKED`、rework 产出 `RETRY` |
| LOW | 未声明的 rework/unblock 观测缺口 | 仅披露了 `interactive_decision` | 已修：证据报告补充 `blocked_continue` 等已知限制 |

## Round 2

- 冻结凭证: `git diff HEAD -- src tests frontend-public docs | shasum -a 256`
  = `c3f4c40652d056a31d78db903252d535c10e33086fbd5e6df6a96ffe34b4291c` @ HEAD `dc2b99bb`（MATCH）
- 结论: **PASS**

复核要点（verifier 原文要点）：

- **H1 已修且稳健**：用真实 SQLite store + 真实聚合器复现 Round 1 场景，四类耗时相加
  恒等于端到端、`in_progress=true`、`outcome`/`finished_at` 清空、`failed` 事件仍在时间线；
  多重终态 + 多次重开后最终 `MERGED` 正确收口；直接改行绕过 reopen 的脏数据也满足恒等式。
- **M1/M2 已修**：证据目录分离；harness 确为真实 uvicorn + 真实 `/api/v1` + 真实 SQLite，
  仅 mock `/api/auth/me`。
- **L2/L4 已完全修复**；L1（核心数学）与 L3（主项）已修，余下残差见下。
- 回归复核：`183 passed`、`check_architecture.py` ✅、前后端 DTO 逐字段对齐。
- 未引入新的 HIGH/MEDIUM。

### 残差（verifier 判定 LOW，已披露并接受）

1. **L1 残差（显示顺序）**：`build_prd_lifecycle_detail` 的事件列表与前端
   `prd-lifecycle-view.tsx` 的 `localeCompare` 仍按原始字符串顺序呈现，未复用
   `_event_sort_key`。**仅人为注入混合时区偏移行时才可能显现**；生产写入统一为
   `now_iso()`（`+00:00`），字典序与时序一致，故不构成现实缺陷。核心数学（阶段推导与
   耗时分类）已按解析时间排序。
2. **N1（reopen 时机，LOW）**：`record_lifecycle_event` 对任何非终态事件都重开已收口 run，
   不比较事件时间与 `finished_at`。若有人回填一条早于 `MERGED` 的非终态事件，会把已完成
   run 翻回进行中。生产事件按动作时间单调追加，不会触发；作为设计取舍接受。
3. **N2（窄屏证据文件名）**：Round 2 复核时看到 harness 声明 `rv-1c-narrow-...png` 但磁盘
   缺失、且根目录有 17:34 的旧 e2e 产物。该观察基于复核中途快照；执行器随后已改为
   `rv-2b-narrow-prd-stats.png`（真实 390×844 页面并断言无横向溢出）并清理了旧文件，
   当前证据目录与本报告一致。
4. **`capture_real_console.sh` 用真实 store 函数种账本，而非真跑一次 runner**（LOW）：
   PRD rv-1 的 `expected` 已说明使用 fixture PRD；harness 的价值在于“真实 HTTP + 真实
   SQLite + 真实浏览器”的穿越，而不是证明 runner 端到端跑通（后者由 pytest 集成用例覆盖）。

## 结论

- **verdict: PASS**（Round 2），冻结凭证 `c3f4c40...` @ `dc2b99bb`。
- 通过范围：rv-1、rv-2、rv-3、rv-4、rv-5 的来源、真实边界、fresh-state 与反例审查。
- 残余风险：上列 4 条 LOW，均已写入证据报告「已知限制」，未发现需要阻断归档的缺陷。
