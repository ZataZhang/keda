# Realistic Validation 证据报告 — Token 消耗 CLI 查询

PRD: `tasks/pending/P1-FEAT-20261004-174104-agent-token-usage-cli.md`
分支: `feat/agent-token-usage-cli`
日期: 2026-10-04

## 机器门禁

- `CI=true just test all`：全绿（含新增 7 例 CLI 测试；日志 `/tmp/just_test_cli3.log`）
- 架构检查 `check_architecture.py`：PASS（api 层经 core 门面取 `create_console_store`，无 engines 直依赖）
- verifier：PASS-with-notes（0 HIGH / 1 MEDIUM，MEDIUM 处方已修复并加负控回归）

## rv-1 真实入口 verbatim 输出（R1, human）

命令：`COLUMNS=160 IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar tokens --repo-id keda-main --days 30`

```text
Token 用量汇总（最近 30 天，仓库 keda-main；总量 = 输入 + 输出 + 缓存读 + 缓存写）
                             按流程
┏━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┓
┃ 分组 ┃ 总量 ┃ 输入 ┃ 输出 ┃ 缓存读 ┃ 缓存写 ┃ 命中率 ┃ 调用数 ┃
┡━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━┩
│ 验证 │ 2.9k │ 640  │ 150  │ 2.1k   │ 0      │ 77%    │      1 │
│ 评审 │ 2.7k │ 880  │ 260  │ 1.5k   │ 90     │ 61%    │      1 │
│ 实现 │ 2.5k │ 1.2k │ 340  │ 800    │ 120    │ 38%    │      1 │
└──────┴──────┴──────┴──────┴────────┴────────┴────────┴────────┘
                             按 agent
┏━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┓
┃ 分组   ┃ 总量 ┃ 输入 ┃ 输出 ┃ 缓存读 ┃ 缓存写 ┃ 命中率 ┃ 调用数 ┃
┡━━━━━━━━╇━━━━━━┇━━━━━━┇━━━━━━┇━━━━━━━━┇━━━━━━━━┇━━━━━━━━┇━━━━━━━━┩
│ claude │ 5.2k │ 2.1k │ 600  │ 2.3k   │ 210    │ 50%    │      2 │
│ codex  │ 2.9k │ 640  │ 150  │ 2.1k   │ 0      │ 77%    │      1 │
└────────┴──────┴──────┴──────┴────────┴────────┴────────┴────────┘
```

**10 秒自检**：实现行总量 2.5k = 1.2k + 340 + 800 + 120 ✓；命中率 实现 38% = 800/2100 ✓；claude 合计 5.2k = 实现 2.46k + 评审 2.73k ✓。

`--json` 佐证（节选，完整见 `rv1_json.json`）：`by_flow` 含 verify/supervise/implement，字段与 stats 端点 `token_usage` 同构。

## rv-2 空态与参数边界（R1, verifier）

PASS：空账本 → "暂无 token 用量数据（agent 未上报 usage 或所选范围无记录）。"退出码 0；`--days 0/9999` 收敛 1/365；不存在的 repo-id 空表。

## rv-3 `--json` 同构（R1, verifier）

PASS：`json.loads` 成功；顶层 repo_id/days/token_usage；`by_flow`/`by_agent` 字段名与 `PrdLifecycleStats` 序列化一致。

## 负控（verifier MEDIUM 处方）

PASS：坏库构造（`create_console_store` 构造即抛）→ 退出码 1 + 单行"token 查询失败：账本不可用（cannot open database file）"，无 traceback。回归测试 `test_tokens_broken_store_construction_degrades_gracefully`。

## verifier 结论

PASS-with-notes（0 HIGH / 1 MEDIUM）。MEDIUM（create_console_store 构造在 try 外）已修复并加负控回归。完整报告：`*.verifier-report.md`。

## 证据文件 SHA-256

见证据分支 `SHA256SUMS.txt`（随 PR 证据评论更新）。

## rv-4 按 PRD（Issue）粒度与 --issue 下钻（2026-10-04 scope 增补）

真实入口 verbatim（命令：`COLUMNS=150 IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar tokens --repo-id keda-main --days 30`，完整输出见 `rv4_output_byprd.txt`）：

```text
                                                    按 PRD（Issue）
┏━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┓
┃ Issue ┃ PRD                                                ┃ 总量 ┃ 输入 ┃ 输出 ┃ 缓存读 ┃ 缓存写 ┃ 命中率 ┃ 调用数 ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━┩
│  #212 │ P1-FEAT-20260930-212702-agent-token-usage-stats.md │ 8.1k │ 2.7k │ 750  │ 4.4k   │ 210    │ 60%    │      3 │
└───────┴────────────────────────────────────────────────────┴──────┴──────┴──────┴────────┴────────┴────────┴────────┘
```

**交叉自检**：#212 总量 8.1k = 8080（json `by_prd[0].totals.total_tokens`）= 按流程表 验证 2.9k + 评审 2.7k + 实现 2.5k 之和 ✓；命中率 60% = 4400 ÷ (2720+4400+210) ✓。

`--issue 212` 下钻（`rv4_output_issue_drilldown.txt`）：标题行注明 `Issue #212`，三张表与默认输出在该 Issue 上逐值一致（该账本仅一个 Issue，收窄语义由 `test_tokens_issue_drilldown_scopes_all_tables` 以双 PRD 种子负向断言：#9 与 codex 不出现）。

- 复现（机器侧）：`uv run pytest tests/test_agent_token_stats.py tests/test_cli_tokens.py -o addopts=""`（22 例：新增 3 例聚合分组/过滤 + 4 例 CLI 按 PRD 表/下钻/json by_prd/空态边界）
- 口径单源：`build_token_usage_by_prd` 每 run 先经 `aggregate_token_usage` 同款提取再合并；零用量 run 按"缺失排除"跳过（`test_groups_by_prd_and_excludes_missing_usage` 断言 issue 11 无全零行）
