# 独立 Verifier 报告 — P1-FEAT-20261004-174104-agent-token-usage-cli

- **verdict: PASS-with-notes**
- 验证时间：2026-10-04
- Verifier：独立 verifier（CodeBuddy / GLM），全程只读；唯一写入为本报告
- worktree：`/Users/zata/code/keda-worktrees/feat/agent-token-usage-cli`（分支 `feat/agent-token-usage-cli`）

## 0. 冻结凭证核对

| 凭证 | 期望 | 实测 | 结论 |
|---|---|---|---|
| `git rev-parse HEAD` | 2a5ede0… | `2a5ede09648391c299114c3bf8ac87c2efaf1d25` | ✅ 一致 |
| `git diff HEAD -- src tests \| sha256` 前缀 | 76ea36ce | `76ea36ce54276ad126346b4f181f33f73ccf462c33fe3ee2621752358583ea2b` | ✅ 一致 |

工作区交付态：已跟踪修改 `src/backend/api/cli_typer_app.py`（+1 行挂载）、`docs/guides/agent-runner.md`（+3 行）；未跟踪新增 `src/backend/api/cli_typer_tokens.py`、`tests/test_cli_tokens.py`、PRD 本体。与 PRD §7 Change Impact Tree 一致。

## 1. 执行过的命令清单（全部只读，/tmp 探针除外）

1. `git rev-parse HEAD` / `git branch --show-current`
2. `git diff HEAD -- src tests | shasum -a 256`
3. `git diff HEAD --stat` / `git status --porcelain`
4. `git diff HEAD -- src/backend/api/cli_typer_app.py docs/guides/agent-runner.md`
5. `uv run pytest tests/test_cli_tokens.py -o addopts="" -q` → **6 passed**
6. `uv run pytest tests/test_agent_token_stats.py -o addopts="" -q` → **8 passed**（抽跑）
7. `uv run python hooks/shared/check_architecture.py` → **PASS**（扫描 281 文件，无违规）
8. `rg -n "by_flow" src/backend/api/` → 仅 `cli_typer_tokens.py:134,143`（呈现层取值）
9. `rg -n "aggregate_token_usage|build_prd_lifecycle_stats" src/backend/api/` → CLI 仅 import/调用 `build_prd_lifecycle_stats`
10. 通读：`cli_typer_tokens.py` 全文、`tests/test_cli_tokens.py` 全文、`agent_runner_lifecycle.py:630-760`、`agent_runner_token_stats.py:60-140`、`routes/agent_runner_console.py:78-92,360-395`、`engines/agent_runner/factories/__init__.py`、`infrastructure/persistence/console_store.py`（`__init__`/`_connect`/`_migrate`）、前端对照 `frontend-public/app/(app)/app/stats/page.tsx:460-530`、`frontend-public/components/roadmap/prd-lifecycle-view.tsx:185-230`
11. `IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar tokens --repo-id keda-main --days 30` → 退出码 0，两张表
12. 同上 + `COLUMNS=160` → 列头完整
13. 同上 + `--json` → 结构核验
14. 坏库探针：`IAR_CONFIG=/tmp/token-usage-rv-broken/iar.toml`（垃圾 db 文件）→ 退出码 1，stderr 含 traceback
15. `uv run iar tokens --help` / `uv run iar --help | rg tokens` → 顶级命令可见
16. `wc -l` 触碰文件：`cli_typer_tokens.py` 147、`cli_typer_app.py` 398、`test_cli_tokens.py` 171

## 2. 逐 rv 核验

### rv-1：有数据时两张汇总表，数值与聚合一致 — ✅ PASS

真实入口 `IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar tokens --repo-id keda-main --days 30`（种子账本 `/tmp/token-usage-rv/console.db`，经真实写入路径播种）：

- 两张表（按流程 / 按 agent）均输出，列：分组、总量、输入、输出、缓存读、缓存写、命中率、调用数（FR-1 列集完整）。
- 逐行自检「总量 = 输入 + 输出 + 缓存读 + 缓存写」：
  - 验证：640+150+2100+0 = 2890 → 显 `2.9k` ✅；命中率 2100/2740 = 76.6% → 显 `77%` ✅
  - 评审：880+260+1500+90 = 2730 → `2.7k` ✅；1500/2470 = 60.7% → `61%` ✅
  - 实现：1200+340+800+120 = 2460 → `2.5k` ✅；800/2120 = 37.7% → `38%` ✅
  - claude（2 次）：5190 → `5.2k` ✅；2300/4590 = 50.1% → `50%` ✅；调用数 2 ✅
- `COLUMNS=160` 重跑：8 列列头全部完整无截断。
- 数值与 `build_prd_lifecycle_stats` 同源：表格行直接遍历 `stats.token_usage.by_flow/by_agent`（`cli_typer_tokens.py:143-144`），无第二套聚合。
- 单测 `test_tokens_table_matches_ledger_aggregation` 以 `build_prd_lifecycle_stats` 输出为对照断言（2460 一致）。

### rv-2：空态 / 参数边界无 traceback — ✅ PASS

- 单测 `test_tokens_empty_ledger_renders_empty_state`：空账本 → 「暂无 token 用量数据」、退出码 0 ✅
- `test_tokens_days_are_clamped[0]` / `[9999]`：收敛到 1–365，退出码 0 ✅（钳制双保险：CLI `_clamp_days` + 聚合内部 `agent_runner_lifecycle.py:639` 同规则 `min(max(days,1),365)`）
- 不存在的 repo-id：过滤后 `by_flow`/`by_agent` 为空 → 空态路径，退出码 0（同空账本分支）
- 真实入口退出码 0，无 traceback。

### rv-3：--json 与 stats 端点同构 — ✅ PASS

真实 `--json` 输出：`{"repo_id", "days", "token_usage": {"by_flow", "by_agent"}}`，分组字段 `input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens/total_tokens/usage_count` —— 与端点 `_serialize(stats)`（递归 `asdict`，`routes/agent_runner_console.py:80-92`）对 `token_usage` 的序列化逐字段同构；单测断言 `json.loads` 成功及字段值。FR-4 满足。

## 3. 口径单源与架构

- `check_architecture.py` PASS；CLI 经 `backend.core.use_cases.agent_runner_factory` 门面取 store（api→core，合法），与路由层同模式。
- CLI 内无重写聚合：`by_flow` 仅呈现层取值；聚合唯一入口 `build_prd_lifecycle_stats`（内部 `aggregate_token_usage(window_events)`，`agent_runner_lifecycle.py:752`）。
- FR-2 命中率口径与前端逐字一致：`_cache_hit_rate`（命中 ÷ (input+cache_read+cache_creation)，input_side≤0 显「—」）≡ 前端 `cacheHitRate`（`stats/page.tsx:474-481`，inputSide≤0 返回 null）；`_FLOW_LABELS` 与前端 `TOKEN_FLOW_LABELS` 一致。
- 行数红线：147/398/171，远低于 1000。

## 4. FR 逐条结论

| FR | 结论 | 证据 |
|---|---|---|
| FR-1 两表 + 8 列 + 逐字段一致 | ✅ | §2 rv-1 |
| FR-2 命中率口径 + 「—」降级 | ✅ | §3，与前端同规则 |
| FR-3 --repo-id / --days 钳制 1–365 | ✅ | rv-2 单测 + 钳制双保险 |
| FR-4 --json 同构 | ✅ | §2 rv-3 |
| FR-5 空态退出 0；账本不可用非零码单行无 traceback | ⚠️ 部分 | 读失败路径 ✅；**store 构造失败路径 traceback 逃逸**（见 M-1） |

## 5. 问题分级

### MEDIUM

- **M-1 账本构造失败绕过错误处理，traceback 逃逸 FR-5 承诺**。`cli_typer_tokens.py:108` 的 `create_console_store()` 在 try 块之外，而 `SqliteConsoleStore.__init__` 急连库并跑 migration（`console_store.py` `_connect`/`_migrate`）。实测复现：`history_db_path` 指向非 SQLite 文件 → 退出码 1（满足非零码）但 stderr 打出 Rich traceback 面板 + `DatabaseError: file is not a database`，违背 FR-5「全程不产生 traceback」。单测的 `BrokenStore` 只覆盖读失败，未覆盖构造失败。修复为一行：把 `create_console_store()` 纳入 try。注：周边代码（console 路由等）同样裸调构造，CLI 未低于既有健壮性水位，故不定 HIGH；建议合并前在同分支补一行修复 + 一条坏库用例。

### LOW

- **L-1 §9.2 Architecture Acceptance 自检命令与实现不完全对应**：`rg -n "aggregate_token_usage" src/backend/api/cli_typer_tokens.py` 实际无命中（CLI 调 `build_prd_lifecycle_stats`，`aggregate_token_usage` 在其内部调用）。检查意图（CLI 无重复聚合）已满足，但按字面执行该 rg 会误判。勾选该项时请按意图解释或修订自检命令。
- **L-2 PRD 文案残留（两处）**：① §7.6 rv-2/rv-3 的 `real_entry` 写 `tests/test_cli_console_tokens.py`，实际文件为 `tests/test_cli_tokens.py`；② §9 Delivery Readiness「命令进入 `iar console --help`」与 D-01 顶级挂载反转不一致（实测 `iar --help`/`iar tokens --help` 正常、`iar console --help` 不含 tokens，行为符合 D-01）。归档前建议同步修订。
- **L-3 §9.1 呈递物在验证时点尚未落位 worktree**：声称「已采集…见证据目录 `*.evidence-report.md` §rv-1」，但 worktree 内 `tasks/evidence/P1-FEAT-…/` 目录此前不存在（原始采集件在 `/tmp/token-usage-rv/rv1_output*.txt`、`rv1_json.json`）。归档前需把证据报告落入证据目录（本报告已先行落位该目录）。

## 6. 结论

交付实现了顶级 `iar tokens` 只读查询命令：口径单源（复用 `build_prd_lifecycle_stats`/`aggregate_token_usage`，零新口径）、架构合法、8 列两张表与「—」降级与前端规则一致、`--json` 与端点同构、空态与参数边界健壮，单测 6/6 + 抽跑 8/8 全绿，真实入口（含宽列与 JSON）复验通过。唯一实质缺陷为 M-1（坏库时 traceback 逃逸，一行可修），另有 3 条 PRD 文案/落位类 LOW。

verifier-verdict: PASS-with-notes
