# 证据报告：扩展内置 agent 覆盖——mcode（MiniMax Code）

对应 PRD：`tasks/archive/P1-FEAT-20261116-120000-mcode-builtin-agent.md`
实施日期：2026-11-16 · 实施者：minimax Code（M3.1-Flash）

> rv-3 / rv-4 的原始 CLI 输出按仓库惯例不进版本库；rv-7 的两张截图与摘要文本是本次验收呈递物，已按 PRD 要求显式纳入版本库。

---

## 1. 结论

机器层 oracle 全部通过。核心回归线成立：既有七个 agent 的命令行黄金快照**零 diff**，出厂注册块与代码内默认逐字段一致。

`mcode` 的端到端行为已用**真实 mcode CLI**（本机 v0.7.7）验证：事件流被正确渲染（正文 / 思考 / 工具调用三类可见），会话 id 逐字一致，token 用量三项逐字段 1:1 映射，平台 `total_tokens` 与 mcode 上报的 `totalTokens` 完全相等。

## 2. rv-1 / rv-2 / rv-4 / rv-5 / rv-6 / rv-8（机器层）

```text
rv-1  uv run pytest -o addopts='' tests/test_agent_spec_config.py -q       → 18 passed
rv-2  uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q  → 39 passed
rv-4  kc agent list / doctor --protocols / 四用途真实 argv                  → 见 rv-4-agent-doctor.txt
rv-5  uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -k mcode
        → test_mcode_has_no_judge_profile passed（抛 UnknownProfileError，指名 judge 与已声明用途）
rv-6  派生 SUPPORTED_AGENT_SPECS → 含 ('mcode','minimax')，顺序在原七个之后
        include_top_level = ('auth', 'config.yaml', 'skills')   # 白名单语义
        exclude_subpaths = ['.builtin-skills', '.internal-skills']
rv-8  文档 / 原型 / sync_template 适配器清单枚举文本断言                    → 通过
rv-9  just test all（最终分支）                                       → 5331 passed, 1 skipped；lint 通过
```

> **rv-6 的一处实施期修正**：初稿按"运行时状态目录"直觉给 `auth_exclude` 列了 `background-tasks` / `cache` / `logs` 等，实施期读 `container_auth.py` 才发现**导入是白名单语义**——只复制 `auth_include` 列出的条目，`auth_exclude` 只是"白名单内也排除"的兜底。因此那些运行时目录根本不会被复制，属于无效声明，已收敛为 `.builtin-skills` / `.internal-skills` 两项并在代码与文档里注明白名单语义。

### rv-2 的回归线（既有 agent 零 diff）

`tests/test_agent_invocation_golden.py` 39 条全绿，其中既有的 codex / claude / kimi / pi / codebuddy / qoder / opencode 快照**未改动、未失败**。mcode 新增 5 条：

```text
test_golden_mcode_run         → mcode exec --cwd <wt> --permission full --output-format stream-json --input -
test_golden_mcode_deliberate  → 同上，read_only=True
test_golden_mcode_generate    → 同上，read_only=True
test_golden_mcode_repl        → 同上，read_only=False
test_mcode_has_no_judge_profile → 抛 UnknownProfileError，指名 judge 与已声明用途
```

### rv-3：真实 mcode CLI 端到端（核心证据）

完整输出见同目录 `rv-3-mcode-protocol.txt`。脚本 `scripts/probe_mcode_protocol.py` 做**同源对照**：把**同一份**原始事件流同时交给平台观测器与渲染器，避免两轮会话互相干扰。

```text
事件行数：96
原始 sessionId = 'mvs_4ea5db1423ca45abbe00f3ef5b80f1b9'
原始 usage（camelCase）= {"inputTokens": 15403, "outputTokens": 429, "cacheReadTokens": 20224, "totalTokens": 36056}
观测 session_id = 'mvs_4ea5db1423ca45abbe00f3ef5b80f1b9'
观测 token_usage = TokenUsage(input_tokens=15403, output_tokens=429, cache_read_input_tokens=20224, cache_creation_input_tokens=0)
session id 一致：True
inputTokens  15403 → input_tokens  15403
outputTokens 429 → output_tokens 429
cacheReadTokens 20224 → cache_read_input_tokens 20224
total_tokens（平台口径）= 36056
```

**映射结论（PRD §FR-6 的实现不确定性已闭环）**：mcode 的 `inputTokens` **不含**缓存命中部分——`inputTokens + cacheReadTokens + outputTokens == totalTokens`（15403 + 20224 + 429 == 36056），与 keda 的 Anthropic 口径一致，因此可 **1:1 直映射**，无需任何换算。

渲染后的可读输出含三段可见内容：agent 思考（`[agent thinking]` 前缀）、工具调用（`[agent tool] bash: <命令>`）、最终正文。端到端中继（生产协议 + 真实子进程）退出码 0，session id 与 token 用量均非空。

> 证据目录里的另一次采集恰好命中缓存（`cacheReadTokens=17024`），同样逐字段相等——两种缓存状态都验证过。

**最终树复跑（2026-11-16）**：更新后的 `uv run python scripts/probe_mcode_protocol.py` 再次执行真实 mcode CLI 与生产协议中继。90 行事件流的 session id 一致、camelCase 三项与 total tokens 逐字段一致（`20760 + 130688 + 1002 = 152450`）、真实正文在 renderer 输出中、生产中继退出码为 0 且 session/token usage 非空；探针退出码为 0。

### rv-4：真实 CLI 入口

完整输出见同目录 `rv-4-agent-doctor.txt`。关键片段：

```text
$ kc agent list
...
mcode (bin: mcode, label: agent/mcode) available (/Users/zata/.local/state/fnm_multishells/.../bin/mcode)
  run: delivery=stdin, protocol=mcode-json-lines, writable
  deliberate: delivery=stdin, protocol=mcode-json-lines, read-only
  generate: delivery=stdin, protocol=mcode-json-lines, read-only
  repl: delivery=stdin, protocol=mcode-json-lines, writable

$ kc agent doctor --protocols
claude-stream-json
codex-json-lines
mcode-json-lines
opencode-json-lines
pi-json-lines
plain
```

mcode 出现在注册表**末尾**，原有七个的名字与顺序不变。

**一处既有行为（非本次引入）**：`kc agent doctor mcode --all-profiles` 因 mcode 刻意不声明 `judge` 而 fail-fast（`not_found: agent 'mcode' has no 'judge' profile (declared: run, deliberate, generate, repl)`），打不出 argv。`opencode` 同样如此（`not_found: agent 'opencode' has no 'generate' profile`）——这是 doctor 遍历全部五种用途、遇缺即报的既有设计。因此 rv-4 的 argv 证据改用 `build_agent_invocation`（与真实执行**完全同一条路径**）逐用途打印：

```text
mcode · run
  argv: mcode exec --cwd /tmp/worktree --permission full --output-format stream-json --input -
  prompt_delivery: stdin
  output_protocol: mcode-json-lines
  read_only: False
mcode · deliberate   → 同一 argv，read_only: True
mcode · generate     → 同一 argv，read_only: True
mcode · repl         → 同一 argv，read_only: False
```

## 3. 实施过程中的发现

### 3.1 doctor 的沙箱告警应保留

`--permission full` 只是 mcode 的 yolo 执行档，不提供沙箱保护；因此 writable 的 `run` / `repl` 继续显示告警是正确的：

```text
[yellow]WARN:[/] agent 'mcode' profile 'run' is writable but declares no
sandbox/approval flag; the agent runs without local sandboxing.
```

不能把 `--permission` 加入沙箱 / 审批标记，否则会把允许写入误认成受保护的执行方式。复核时发现曾有这样的短暂实现，已撤销；生产 doctor 告警保持原状。

### 3.2 `--permission off` 不禁用工具（写进 PRD 决策二与文档）

实测 mcode v0.7.7：`--permission off` **仍然执行工具并写入文件**——让它写 `/tmp/mcode_off_probe.txt` 并读回，文件确实被创建并读到内容；让它删文件也照做。`--help` 写明 `ask requires TUI/ACP`，即 `off` 关掉的是**交互式权限询问**，不是工具能力。

因此 mcode **无法**提供 `read_only_enforced` 那一档保证，`judge` 用途**不声明**（假只读声明会被只读决策入口的门禁采信，比没有 planner 更危险）。`generate` 声明 `read_only = true` 但 `read_only_enforced` 保持 `false`，与 claude 系 `generate` 的处理一致。

## 4. 覆盖对照

| PRD rv | 状态 | 证据 |
|---|---|---|
| rv-1 出厂块与内置默认一致 | PASS | `tests/test_agent_spec_config.py` 18 passed |
| rv-2 命令行黄金快照 + 既有零 diff | PASS | `tests/test_agent_invocation_golden.py` 39 passed |
| rv-3 真实事件流渲染 + 观测 | PASS | `rv-3-mcode-protocol.txt`（同源对照逐字段相等） |
| rv-4 真实 CLI 入口 | PASS | `rv-4-agent-doctor.txt` |
| rv-5 未声明 judge 时 fail-fast | PASS | `test_mcode_has_no_judge_profile` |
| rv-6 容器认证派生目标目录 | PASS | 派生表含 `('mcode','minimax')`，顺序在原七个之后 |
| rv-7 console 两层下拉 | PASS | [全局截图](rv-7-console-agents-global.png)、[仓库截图](rv-7-console-agents-repo.png)；候选值与先前写回记录见 `rv-7-console-agents.txt` |
| rv-8 文档/原型/skill 枚举同步 | PASS | 文本断言通过 |
| rv-9 全量测试与 lint | PASS | 最终分支 `just test all`: 5331 passed, 1 skipped；`just lint --reuse` / `just lint --full` 通过 |

## 5. rv-7 真实控制台验证

- **真实页面截图已留存**：在源码启动的 console（`http://127.0.0.1:52344`）打开全局与仓库 `keda` 视角的模型预设区，展开 Agent 下拉；两张截图都显示 `mcode`。截图捕获时没有 mock 前后端，也没有写配置。

- 页面展示候选值由真实 API 返回，具体全局/仓库数组见 `rv-7-console-agents.txt`。此前的真实仓库保存操作创建了临时预设 `mcode-ui-smoke`，接口视图返回 `agent=mcode`、`source=repository_only`，并确认 `.kedacode.toml` 写入；其后已恢复测试配置。因当时未保存逐字 TOML 片段，证据文本明确披露这一限制，不伪造文件原文。

- [全局 Agent 下拉](rv-7-console-agents-global.png)
- [仓库 Agent 下拉](rv-7-console-agents-repo.png)
- 候选值与写回记录：`rv-7-console-agents.txt`
- **`mcode` 未进 `agent_fallback_order`**：刻意为之（不改既有回退链），用户可显式在 console 里配置。
