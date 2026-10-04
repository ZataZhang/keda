# Realistic Validation Plan — Agent Token 用量统计

事实源：PRD `tasks/archive/P1-FEAT-20260930-212702-agent-token-usage-stats.md` §7.6（本文件是其执行投影，供 verifier 与人工按清单逐条复核）。

被测边界（真实）：假 agent 子进程（claude stream-json 信封 result 事件）→ 真 SubprocessRunner → 真执行循环 → 真账本写入 → fresh 读端点/页面渲染。仅 agent CLI 本身由输出可控的假脚本替代。

## Oracle 清单

| id | tier | reviewer | behavior | real_entry | 证据 |
|---|---|---|---|---|---|
| rv-1 | R2 | verifier | 假 agent usage 经真实执行循环落账，detail 四字段逐字段一致，fresh 读端点读回 | `uv run pytest tests/test_agent_token_usage_flow.py -o addopts=""` | evidence-report §rv-1 |
| rv-2 | R1 | verifier | 提取/收集器解析与容错（四字段、缺 usage→None、畸形降级、plain 交错） | `uv run pytest tests/test_agent_stream_usage.py tests/test_process_runner.py -o addopts=""` | 同上 §rv-2 |
| rv-3 | R1 | verifier | 观测事件不改变阶段推导与时长归属 | `uv run pytest tests/test_prd_lifecycle.py -o addopts=""` | 同上 §rv-3 |
| rv-4 | R1 | verifier | 聚合口径（分组、总量=四项之和、缺失排除）+ 端点透出 | `uv run pytest tests/test_agent_token_stats.py tests/test_prd_lifecycle.py -o addopts=""` | 同上 §rv-4 |
| rv-5 | R2 | human | 真实 console：执行过程 token 行 + Stats 汇总区 | `IAR_CONFIG=/tmp/token-usage-rv/iar.toml uv run iar console --no-browser --port 8399` | rv-5-*.png 三张 |
| rv-6 | R1 | verifier | 缺失/畸形 usage 降级：attempt 正常、无 token 数据、负控可红 | `uv run pytest tests/test_agent_token_usage_flow.py -o addopts=""` | 同上 §rv-6 |

negative_control 均为 tests 侧 fixture 变体（去 usage 字段 / usage 写成字符串），未改产线代码。

失败排查提示：先查假 agent 输出是否真进入 stdout 捕获，再查收集器是否挂在 run() 的流式与 plain 两条路径。
