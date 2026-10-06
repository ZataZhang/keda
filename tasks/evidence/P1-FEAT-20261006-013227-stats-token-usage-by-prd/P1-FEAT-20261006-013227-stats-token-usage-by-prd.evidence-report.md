# Realistic Validation 证据报告 — Stats 页 Token 用量补「按 PRD」维度汇总

PRD: `tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`
分支: `issue-209` @ `8e6645cf`（实现未提交，由 runner 提交）
日期: 2026-10-06

## 人审导航 / Human Review Navigation

你只需要看两件事（都在真实入口采集，非组件预览）：

1. **三张表同屏了吗？** Token 用量区特写（真实 `/app/stats`，`iar console` + 真实账本）：

![Token 用量区三张表特写（real entry point）](rv-4-token-usage-section.png)

2. **页面和命令行数字对得上吗？** 并排看同一时刻的两张图：

![Stats 页全页截图](rv-2-page-by-prd.png)

![iar tokens --days 30 输出](rv-2-cli-tokens.png)

- 页面 `#194 → 7133.1k`，CLI `#194 → 7133.1k`；全部 9 行逐格相等（机器断言 `rv-2-cli-vs-page.txt`）。
- 打开方式：`open "tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/rv-4-token-usage-section.png"`，或 `just console-sync` 后访问 <http://127.0.0.1:8313/app/stats>（滚动到「PRD 执行分析」卡片底部）。
- 交互清单：`just prd review tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`（本地 HTML，含内嵌截图与作答回传）。

## 机器门禁

- `CI=true just test all`：**3068 passed, 1 skipped**（`--no-testmon` 强制全跑；flag 绑定最终代码树，PRD 勾选轮重跑确认）
- `just lint --full`：全部 hook 通过（架构检查、ruff、ruff-format、jscpd、pylint-duplicate-code、`check_max_file_lines`；`stats/page.tsx` 非空行数增后复查仍 < 1000）
- 提交进仓库的 e2e spec：`just e2e tests/smoke/stats-token-usage-by-prd.spec.ts` 真实浏览器 **4 passed**（三表行序与单元格、旧响应表级空态、窗口无用量区级空态）

## Oracle 结果（风险序 rv-2 → rv-1 / rv-3 / rv-4 / rv-5）

| id | tier/reviewer | 内容 | 结果 | 证据 |
|---|---|---|---|---|
| rv-2 | R2 / human | 真实浏览器 DOM 9 行 vs 真实 CLI vs fresh 端点响应三方逐格对照（Issue/PRD/总量/四项明细/调用数/执行次数）；×2 负控实测 RED（页面 14266.2k vs 期望 7133.1k，非零退出） | PASS | `rv-2-cli-vs-page.txt` + `rv-2-page-by-prd.png` + `rv-2-cli-tokens.png` |
| rv-1 | R1 / verifier | 真实端点响应含非空 `token_usage_by_prd`（9 条），元素键集完整；改动前实例同断言 RED（字段缺失） | PASS | `rv-1-prd-lifecycle-contract.txt` |
| rv-3 | R1 / verifier | 真实 `iar console` + 副本账本 DROP 表注入：无保护端点 500（注入有效），`stats/prd-lifecycle` 仍 200 且 `token_usage_by_prd=[]`、`token_usage` 空、`runs=[]` | PASS | `rv-3-degraded-store.txt` |
| rv-4 | R1 / human | 真实 `/app/stats` 三表同屏（y 4149 < 4399 < 4575）、首行最大消耗、首列 Issue 号；摘表负控实测 RED（超时非零退出） | PASS | `rv-4-stats-token-by-prd.txt` + `rv-4-stats-token-by-prd.png` + `rv-4-token-usage-section.png` |
| rv-5 | R0 / verifier | 改动前后真实端点 `token_usage` 规范化 JSON 逐字节一致（70 行）；`aggregate_token_usage` 函数体 35 行 HEAD vs 工作区一致 | PASS | `rv-5-no-regression.txt` |

每个 oracle 的红→绿判别力（negative_control + expected_fail）见 `.iar/evidence/evidence.json`；keda 复跑对最终树断言 exit 0 + `stdout_assertions` 精确子串。

## 架构验收

- 二次读账本零发生：`rg -n 'list_lifecycle_runs' src/backend/core/use_cases/agent_runner_token_stats.py` 命中仅 `_list_window_runs`（CLI 自读路径）与参数 docstring；Stats 挂载点以 `run_records=` / `events_by_run=` 传入同一读集，`test_prd_lifecycle_stats_reuses_ledger_reads_for_by_prd` 用读计数 store 锁死 `run_list_calls == 1 && event_list_calls == 1`。
- 依赖方向：唯一新增 import 为 `core → core`（`agent_runner_lifecycle.py` → `agent_runner_token_stats.py`）；api 层与路由零改动（`_serialize` 自动携带新字段）。
- `iar tokens` CLI 表面未动（FR-9）：`git diff` 不含 `cli_typer_tokens.py`。
- 降级语义：账本读失败 → `token_usage_by_prd=[]` 且端点 200（rv-3 真实 HTTP 验证），与既有 Stats 降级规则一致。

## 披露与限制

- 真实账本 30 天窗口内没有「同一 PRD ≥2 次执行且都有用量」的样本（`#193` 的两行是两个不同 PRD 文件）。合并行为由 `test_same_prd_multiple_runs_merge`（run_count==2、四字段求和）与 `#194` 行 `执行次数=1` 的列在场共同支撑；页面真实数据下一旦出现重试合并即按该逻辑渲染。
- rv-1/rv-5 的「改动前」对照使用机器上常驻的 8313 守护实例（main 树代码）。它同时是负控来源；若该实例被重启加载新代码，对照失效——采集时已实测其响应不含新字段。
- 原始截图/txt 为 gitignored 本地采集物（`tasks/evidence/**` 只允许 .md 进历史）；PR 评审以本目录 `open` 命令与 `just prd review` 为呈递入口。
