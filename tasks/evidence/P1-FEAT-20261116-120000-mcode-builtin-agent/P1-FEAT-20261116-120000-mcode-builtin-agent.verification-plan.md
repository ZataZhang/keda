# 验证计划：扩展内置 agent 覆盖——mcode（MiniMax Code）

对应 PRD：`tasks/archive/P1-FEAT-20261116-120000-mcode-builtin-agent.md`
最终验证基线：由交付 PR 的最终提交记录；代码与记录文件分离后计算 verified tree。

## 验证目标

证明三件事：①`mcode` 真的进入内置注册表且既有七个 agent 的行为逐字节不变；②mcode 的**真实**事件流被正确渲染，会话 id 与 token 用量被正确观测；③console 两层下拉在真实入口里真的渲染出 `mcode`，且写回真的落盘。

## 风险分级与对应策略

本 PRD 无 R2/R3 改动点（无跨组件、跨进程、外部契约的不确定改动），全部按 R0/R1 深度取证：始终字段 + 一条能真正判别本次失败的断言。

| 改动点 | tier | 取证方式 |
|---|---|---|
| mcode 的纯数据注册 | R1 | 出厂块守卫 + 命令行黄金快照（既有七条零 diff） |
| `mcode-json-lines` 协议 | R1 | 协议渲染单测 + 真实事件流端到端 |
| 观测层补 `sessionId` / camelCase usage | R1 | 同源对照（原始上报 vs 平台观测逐字段相等） |
| mcode 的 yolo 权限档声明 | R1 | argv 黄金快照确认使用 `--permission full`；doctor 对 writable profile 保留无沙箱告警 |
| 容器认证派生名 | R1 | 派生结果断言 |
| 文档与原型枚举 | R0 | 文本断言 + 独立搜索复核 |
| console 可见性 | R1 | 真实入口截图 + 候选值集合断言 + 写回落盘 |

## oracle（与 PRD §7.6 同源，此处只列执行方式）

| id | 层 | 命令 / 入口 | 判定 |
|---|---|---|---|
| rv-1 | unit | `uv run pytest -o addopts='' tests/test_agent_spec_config.py -q` | 出厂块与内置 spec 逐字段一致 |
| rv-2 | unit | `uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q` | 五条新形态正确 + 既有七条零 diff |
| rv-3 | integration（真实 mcode CLI） | `uv run python scripts/probe_mcode_protocol.py` | 渲染含正文；session id 与原始逐字相等；usage 三项逐字段相等；平台 total_tokens == mcode totalTokens |
| rv-4 | smoke（真实 CLI） | `kc agent list` / `kc agent doctor --protocols` + `build_agent_invocation` 逐用途打印 argv | `mcode` 在注册表末尾；协议已注册；四条 argv 与设计一致 |
| rv-5 | unit | `uv run pytest ... -k mcode_has_no_judge_profile` | 未声明用途 fail-fast 且指名已声明用途 |
| rv-6 | integration | `container_auth.SUPPORTED_AGENT_SPECS` 派生断言 | 派生目录名 `minimax` 正确、顺序在原七个之后 |
| rv-7 | manual（真实 console） | 源码版 console 生产页面操作 | 全局与仓库两层下拉均观察到 `mcode`；真实 API 候选值、截图与先前仓库写回记录见 `rv-7-console-agents.{txt,png}` |
| rv-8 | unit | 文本断言 + 独立 `rg` 复核 | 文档/原型/skill 枚举无遗漏 |
| rv-9 | full suite | `just test all` | 最终树 5331 passed, 1 skipped；lint 通过 |

> **两条流程约束（实测踩过）**：
> ① **不要加 `CI=true`**——`just test` 内部会调 `just lint --full`，而 lint 的 `check-test-flag` 钩子要求 test 标记；`CI=true` 会让 just 走另一条分支从而在 lint 阶段自锁。`just test` 内部已用 `SKIP=check-test-flag` 正确编排二者。
> ② **本地档只跑子集**——`just test` 走本地档是 939 passed / 1306 deselected，要覆盖全量请用 `just test all`（`-n auto` 并行）。

## 负控与基线

- **rv-3 的负控**：探针脚本的同源对照本身即负控——若观测层没补 `sessionId` 与 camelCase usage，`观测 session_id` 会是 `None`、`观测 token_usage` 会是 `None`，对照行会显示 `False` 与 `None`。改动前的代码可由 `git show 807c4df3:src/backend/infrastructure/agent_stream_usage.py` 直接对照确认。
- **rv-5 的负控**：`mcode` 声明了 `judge` 就会让该断言失败——注册表里删掉 judge 段即可复现"不再 fail-fast"，证明断言真的在判别。
- **rv-2 的负控**：改动既有 agent 的 argv 会让既有七条快照立刻失败，证明这条快照线有判别力（本次未改动它们，全部通过）。

## 高保真入口

- rv-3 用**真实 mcode CLI**（本机 v0.7.7），不是 pytest 替身；事件流由真实子进程产出。
- rv-4 用真实 `kc` CLI 与真实 `build_agent_invocation`（与真实执行完全同一条路径），不是手写 argv 字符串。
- rv-7 用真实 console 实例：后端为被测 worktree 代码、`HOME` / 配置指向隔离 fixture；前端沿用主仓库已构建的静态产物（本 PRD 未改前端代码）；仅 mock 登录态，`lifecycle-agents` 与写回接口走真实后端。**不 mock 那个 agents 数组**——mock 它等于自己写答案。

## 明确不验证（并说明原因）

- **rv-7 截图与写回**：全局、仓库真实页面截图已保存为 `rv-7-console-agents-global.png` / `rv-7-console-agents-repo.png`；真实 API 候选值与此前 `.kedacode.toml` 写回记录收录在 `rv-7-console-agents.txt`。原写回操作没有保留逐字 TOML 片段，证据文本披露此限制。
- **mcode 承载完整 Issue 流程的真实执行**：会消耗模型额度并产生真实 PR 副作用。本轮只验证到"真实 mcode 进程 + 生产协议 + 真实事件流"的端到端（rv-3），完整 Issue 流程由后续 runner 运行覆盖。
- **`mcode` 的 `acp` 子命令接入**：PRD §6 备选 D 明确排除，属独立议题。
- **runner 容器镜像预装 mcode**：PRD §11 明确排除；本机无法验证镜像构建。
