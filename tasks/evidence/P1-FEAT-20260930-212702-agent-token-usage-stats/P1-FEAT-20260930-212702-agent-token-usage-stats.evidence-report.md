# Realistic Validation 证据报告 — Agent Token 用量统计

PRD: `tasks/pending/P1-FEAT-20260930-212702-agent-token-usage-stats.md`
分支: `feat/agent-token-usage-stats` @ `b14573b4`
日期: 2026-10-03

## 机器门禁

- `CI=true just test all`：**2747 passed, 1 skipped**（日志 `/tmp/just_test_all5.log`，flag 已绑定本 tree）
- 行数红线 `check_max_file_lines.py --max-lines 1000`：触碰文件全过
- 前端 `tsc --noEmit`：0 error；eslint 对触碰文件无新增 error（存量 2 处 setState warning 为主仓同样存在）
- `just lint`：pre-commit 全部 hook 通过（check-test-flag 依赖 staged tree 一致，属提交时点校验）

## Oracle 结果

| id | 内容 | 结果 | 证据 |
|---|---|---|---|
| rv-1 (R2, verifier) | 假 agent（claude stream-json result 带 usage）→ 真 SubprocessRunner 执行循环 → ATTEMPT detail token_usage 逐字段一致 → fresh store 读回 + Stats 聚合 | PASS | `tests/test_agent_token_usage_flow.py::test_fake_agent_usage_reaches_ledger_and_stats` |
| rv-2 (R1) | 提取/收集器：四字段、缺 usage→None、畸形降级、非 result 行忽略、plain 交错、双路径赋值 | PASS | `tests/test_agent_stream_usage.py`（16 例） |
| rv-3 (R1) | 观测事件不污染相位推导与时长归属（插入前后逐位一致；全观测 run 按终态降级） | PASS | `tests/test_prd_lifecycle.py::test_observation_events_do_not_change_*` |
| rv-4 (R1) | 聚合口径：attempt→implement 归组、缺失排除、总量=四项之和、端点 HTTP 透出 token_usage | PASS | `tests/test_agent_token_stats.py` + `tests/test_prd_lifecycle.py::test_stats_endpoint_transparently_exposes_token_usage` |
| rv-5 (R2, human) | 真实入口：隔离 `IAR_CONFIG` console（真实登录、真实账本、真实端点）截图三张 | PASS（下图） | `rv-5-lifecycle-token.png` / `rv-5-event-detail-token.png` / `rv-5-stats-token.png` |
| rv-6 (R1, negative) | 无 usage / 畸形 usage（字符串）假 agent：attempt 正常完成、detail 无 token_usage、不抛异常 | PASS | `tests/test_agent_token_usage_flow.py::test_missing_usage_* / test_malformed_usage_*` |

negative_control 说明：rv-1/rv-6 的负控均为 tests 侧 fixture 变体（去掉 usage 字段 / 写入字符串 usage），未改产线代码。

## rv-5 呈递

真实入口：`IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar console --no-browser --port 8399`，
账本数据由种子脚本经**真实采集链路**写入（`tests/test_agent_token_usage_flow.py` 同构：假 agent → 真 SubprocessRunner → 真执行循环 → 真账本写入；仅 agent CLI 本身为输出可控假脚本）。

1. `rv-5-lifecycle-token.png` — PRD 详情「执行过程」时间线：`Agent 尝试` + 两条 `Token 用量`（flow=verify / flow=supervise）观测事件
2. `rv-5-event-detail-token.png` — attempt 事件抽屉：`token_usage` 四字段 `{"cache_creation_input_tokens":120,"cache_read_input_tokens":800,"input_tokens":1200,"output_tokens":340}` 与种子值逐字段一致
3. `rv-5-stats-token.png` — Stats 页 Token 汇总区：按流程（实现 2.5k/38%、验证 2.9k/77%、评审 2.7k/61%）与按 agent（claude 5.2k、codex 2.9k）两张表 + 口径说明

**10 秒自检**：Stats「实现」行 input=1.2k 与执行过程 attempt 事件 `input_tokens:1200` 一致；claude agent 合计 5.2k = 实现 2.46k + 评审 2.73k（两调用）✓。

## 待实测项（PRD §12）

- codex/kimi/pi 真机 usage 形状：未实测（实施机无对应 CLI 登录态）；plain 路径解析已就绪，形状确认后只需扩展字段映射
- claude `result.usage` 会话累计语义：与决策一（单次调用合计）自洽，待真机一次运行核对

## 独立 verifier 结论

- 结论：**PASS-with-notes**（0 HIGH / 1 MEDIUM）；报告见同目录 `*.verifier-report.md`
- MEDIUM：FR-6 字面口径在 roadmap 单 PRD 端点未以聚合字段满足（token 经 `events[].detail` 透传，PRD §7 机制描述措辞不准，功能无缺口）——已按 record 路径修订 PRD 措辞

## 证据身份（Evidence Identity）

- verified_head_sha: `44f685c8`（完整值见 `git rev-parse 44f685c8`）
- verified_tree_sha（排除记录路径 tasks/pending|archive/<prd>、tasks/evidence/<stem>）: `ddb6a329e8843897210ea0ac9ea72eed62ea04c9`
- 冻结凭证：`git diff HEAD -- src tests frontend-public docs | sha256sum` = 空树哈希（工作区与 HEAD 一致）

## 证据文件 SHA-256

```text
（由发布脚本填充，见证据分支同名文件旁 sha256 清单）
```
