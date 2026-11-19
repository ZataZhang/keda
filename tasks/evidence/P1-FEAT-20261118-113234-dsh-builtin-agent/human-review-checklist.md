# 人工审查清单 · dsh 接入 kc 内置 agent 注册表

- PRD：[`tasks/archive/P1-FEAT-20261118-113234-dsh-builtin-agent.md`](../../../tasks/archive/P1-FEAT-20261118-113234-dsh-builtin-agent.md)
- 交付状态：**已确认（2026-11-18 12:39 聊天回复）**：1) A 环境继承；2) B 乐观档（强制只读 + 声明续传）；3) 呈递面确认。执行侧验证与证据已完成。
- 交互版（可直接点选并生成回复）：同目录 `human-review-checklist.html`，或运行 `just prd review tasks/archive/P1-FEAT-20261118-113234-dsh-builtin-agent.md`。

> **回答方式**：在对话里按 `1) A`、`2) A`、`3) 确认` 的格式回复即可；也可以直接编辑 PRD §2 的两个选项框与 §1 的解读签署框。判错任何一项都不需要重做协议，但会影响一处声明或文档前置条件。

---

## 第 1 项 · dsh 的模型凭据由谁提供？

**你在决定什么：** dsh 自己不读 kc 的配置，它要求 `DEEPSEEK_API_KEY` 出现在**启动 kc 的那个进程环境**里。kc 的 agent 子进程环境是"净化档"（以 `os.environ` 为底，只剔除 `SERVER__PORT` 和一组 CodeBuddy 会话变量），所以只要 kc 启动时带着这个变量，dsh 就能拿到；kc 不需要为它写任何注入代码。

**如果判错：** 若你其实希望 kc 从 `config.toml` 的 `api_key_env` 主动注入凭据，那么当前实现不会这么做——部署在"kc 由 launchd/GUI 启动、环境里没有 key"的机器上时，dsh 会在模型调用前以 `MISSING_CREDENTIAL` 失败（不会挂起、不会空成功）。

**PRD 原文（§2 决策一）：**

> kc 的 agent 子进程环境走**净化档**：以当前进程环境为底，只剔除 `AGENT_CHILD_ENV_DENYLIST` 里的会话私有变量（CodeBuddy 的一组会话变量与 `SERVER__PORT`），`DEEPSEEK_API_KEY` 这类凭据变量**原样透传**；本机正在运行的 `kc console` 进程环境里已经有 `DEEPSEEK_API_KEY`，因此"kc 继承环境"这条路径不需要任何代码改动。……**推荐：接受环境继承，把这个前置条件写进文档与排障路径。**

**已有证据：** `rv-3-dsh-protocol.txt` 里无凭据运行得到 `MISSING_CREDENTIAL`、退出码 1、stderr 带 `dsh:` 诊断；`rv-5-doctor.txt` 是同一 agent 的失败/成功对照。

**请选择：**
- [ ] **A（推荐）** 接受环境继承，文档写明"凭据必须存在于启动 kc 的环境里"
- [ ] **B** 本次一并做"按用途注入环境变量"能力（范围更大，需要另行设计与测试，可能不在本轮交付）

---

## 第 2 项 · 能力声明取保守档，还是按文档乐观声明？

**你在决定什么：** dsh 有三个能力点没有拿到"强制"级证据——只读沙箱是否真的阻断写入、能否承担 `judge`（禁用全部工具）、`--session-id` 续传在同 cwd/同 preset 约束下是否可靠。当前实现三处都取保守档：`generate` 用 `--permission-mode read-only` 但 `read_only_enforced = false`；不声明 `judge`；不声明续传。

**如果判错：** 若你要求"只读必须是强制保证"或"必须支持续传"，那么依赖这两项声明的功能（如停滞监督的强制只读诊断、崩溃后的自动续跑）对 dsh 会停用/开新会话，而不是假装支持——功能可用，但比 claude/codex 少这两档保证。

**PRD 原文（§2 决策二）：**

> **推荐：三项都取保守档。** 备选是"按文档乐观声明"（`read_only_enforced=true`、声明续传），代价是声明无法被本轮证据支持，且续传失败会让重试静默变难。

**已有证据：** `uv run kc agent doctor dsh --all-profiles` 只对 `judge` 报错并指名已声明的四个用途（`rv-5-doctor.txt`）；`rv-7-protocol-render.txt` 覆盖渲染映射；`rv-6-usage-parsing.txt` 覆盖用量互斥。

**请选择：**
- [ ] **A（推荐）** 保守档：`read_only_enforced=false`、不声明 judge、不声明续传
- [ ] **B** 乐观档：按文档声明强制只读/续传（需要额外实测证据与追赶范围）

---

## 第 3 项 · 人审呈递面是否与预期一致？

**你在决定什么：** 这次唯一"用户看得见"的变化是 console 里多一个 `dsh` 选项、CLI 列表里多一个 agent。下面是真实 console 页面（工作树代码起服，`http://127.0.0.1:54667`）里展开「模型预设 → Agent」下拉的截图，`dsh` 在列表末尾：

![console 模型预设的 Agent 下拉，dsh 在列表末尾](rv-4-console-agents.png)

*图 1：真实页面截图（real user flow），全局层 Agent 下拉含 dsh，由 `scripts/capture_console_agents.cjs` 采集。*

> 图打不开时直接打开原图：`open tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/rv-4-console-agents.png`

![console 生命周期矩阵保存后实现阶段生效 AGENT 为 dsh](rv-4-console-saved.png)

*图 2：真实保存后的生命周期矩阵（实现/修复/收尾的生效 AGENT = dsh，toast「生命周期设置已保存」）；`config.toml` 写回后用备份恢复并校验 SHA-256 一致。*

> 图打不开时直接打开原图：`open tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/rv-4-console-saved.png`

**如果判错：** 若你期望的是别的位置（例如生命周期矩阵行内直接选 agent），那属于产品交互变更，不在本 PRD 范围——本 PRD 只保证注册表数据驱动出的选项可达。

**已有证据：**
- `rv-4-console-agents.txt`：页面地址、下拉选项全集（含 `dsh`、`mcode · 未安装` 等）、`GET /api/v1/agent-runner/lifecycle-settings` 返回的 `agents` 与 dsh 的 `declared_profiles`。
- `rv-3-dsh-protocol.txt`：真实 dsh 进程的事件流解析（session id 同源一致、退出码自洽、非法权限档渲染 `[agent error]`）。
- `rv-9-full-test-lint.txt`：`just test all` 5398 passed / 1 skipped，`just test` 的 `just lint --full` 通过；`rv-9-mkdocs.txt`：`mkdocs build --strict` 通过。

**请选择：**
- [ ] **确认** 呈递面符合预期
- [ ] **有问题**（请在备注里写清位置/期望）

---

## 回答模板

```text
1) A   # 或 B + 说明
2) A   # 或 B + 说明
3) 确认 # 或 有问题 + 怎么改
```
