# Realistic Validation Plan — Token 消耗 CLI 查询

事实源：PRD `tasks/pending/P1-FEAT-20261004-174104-agent-token-usage-cli.md` §7.6（本文件是其执行投影）。

被测边界（真实）：真实 `iar tokens` 命令读取真实 SQLite 账本（种子数据经真实写入路径落账），输出真实渲染；仅账本数据内容由前置 PRD 的假 agent 链路种子化。

## Oracle 清单

| id | tier | reviewer | behavior | real_entry | 证据 |
|---|---|---|---|---|---|
| rv-1 | R1 | human | 有数据态：两张汇总表数值与账本聚合一致，含命中率与「—」降级 | `IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar tokens --repo-id keda-main --days 30`（COLUMNS=160 宽列） | evidence-report §rv-1 verbatim 输出 + `rv1_output_wide.txt` / `rv1_json.json` |
| rv-2 | R1 | verifier | 空账本 / 无匹配 / 非法天数：空态或收敛，无 traceback | `uv run pytest tests/test_cli_tokens.py -o addopts=""` | 同上 §rv-2 |
| rv-3 | R1 | verifier | `--json` 与 stats 端点 `token_usage` 同构 | 同上 | 同上 §rv-3 |
| 负控 | R1 | verifier | 坏库构造：单行错误退出码 1，traceback 不逃逸（verifier MEDIUM 处方） | 同上 | 同上 §负控 |
