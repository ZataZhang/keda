# Agent 模型预设（Model Presets in the Lifecycle）

> 配套 PRD：`tasks/archive/P1-FEAT-20260930-130445-agent-model-preset-switching.md`（交付后归档路径）。

一个**命名预设 = 一组（agent + 模型 + 推理档）**。运维者在配置里定义 `plan` / `work` 之类预设；
keda 拉起 agent CLI 时按 agent 级声明式模板注入对应的模型与推理深度参数，不用再手工改各 CLI 的
全局配置。预设再通过 **阶段 → 预设绑定层** 接到各个生命周期——校验可用强模型慢推理、实现可用快
模型高推理；每个阶段只在自己的触发条件满足时读取绑定，不要求所有阶段连续执行。

完全可选：预设、绑定、命令行旗标三者都可以不设置；任何一层都没设时，各阶段的行为与过去
**逐字节一致**（argv 黄金快照零 diff）。

## 1. 定义预设

在 `config.toml`（全局）或仓库 `.kedacode.toml`（仓库级，同键覆盖全局）写：

```toml
[agent_runner.presets.plan]
agent = "codebuddy"
model = "glm-5.3-flash"
reasoning_effort = "max"

[agent_runner.presets.work]
agent = "codebuddy"
model = "deepseek-v4.1-flash"
reasoning_effort = "high"
```

- `agent` 必填，须已注册（`[agent_runner.agents.<name>]` 或内置）。
- `model` / `reasoning_effort` 可选；两者都缺省时预设只改 agent 路由，不改 argv。
- 未知预设名在解析期 fail-fast（带已定义清单），绝不静默回落——"切了模型"不能成为假象。

枚举全部预设：`kc agent presets`。

## 2. Agent 级模型参数模板

模型参数怎么注入由**每个 agent 自己声明**（`[agent_runner.agents.<name>]`）：

```toml
[agent_runner.agents.codebuddy]
model_args = ["--model", "{model}"]
reasoning_effort_args = ["--settings", "{\"reasoningEffort\":\"{effort}\"}"]
```

- 七个内置 agent 都声明了 `model_args`：`claude` / `codebuddy` / `qoder` 用 `--model`，
  `codex` / `kimi` / `opencode` 用 `-m/--model`，`pi` 用 `--model`（后四个以本机
  `--help` 确认，未做端到端实跑）。
- `reasoning_effort_args` 只有 `codebuddy`（`--settings reasoningEffort`）、
  `qoder`（`--reasoning-effort`）与 `pi`（`--thinking`）声明；其余 agent 保持为空。
- **为空表示"未核实语法"**：该 agent 命中带模型/推理档的绑定时直接报错
  （`ModelNotSupportedError`，指名 agent 与缺失模板），绝不静默忽略。
- 注入位置：profile `args` 之后、展开器与 `tail_args` 之前；占位符闭集扩为
  `{cwd}` / `{worktree}` / `{prompt}` / `{model}` / `{effort}`。
- 预览注入结果：`kc agent doctor <agent> --preset <名> [--json]`（doctor 是 what-if 工具，
  不做"执行 agent == 预设 agent"的丢弃判定，模板缺失时如实报错）。

## 3. 阶段 → 预设 绑定

```toml
[agent_runner.lifecycle_presets]
verifier = "plan"        # 校验用强模型慢推理
implementation = "work"  # 实现用快模型高推理
```

九键闭集与生命周期 Agent 矩阵同键（implementation / fix / closeout / verifier / review /
supervisor / planner / content_generation / deliberate）。

**绑定后该阶段整体由预设决定**：agent、模型、推理档都取预设声明——遮蔽矩阵同键的 agent
声明。生效 agent 也随之变化（`resolve_lifecycle_agent` 返回预设声明的 agent）。

优先级（高到低）：

1. 命令行显式 `--agent`（绑定让位，模型绑定由换人丢弃规则处理）；
2. 命令行 `--preset` / `--model` / `--reasoning-effort`（一次性锚定 + 同名字段覆盖）；
3. PRD 头部 `lifecycle_presets` 块（除 planner 外八键，随 Issue 流动）；
4. 仓库 `.kedacode.toml` > 全局 `config.toml`（同键仓库层赢）。

未绑定预设的阶段：继续走生命周期矩阵 / 既有散落键 / 内置默认，行为零变化。

### executor 阶段的继承

`fix` / `closeout` 未自绑预设时跟随实现者，并**继承实现阶段的模型绑定**（同一 agent、同一
模型命名空间，不算换人）。它们自绑预设时用自己的绑定。

## 4. 换人丢弃规则

不同 CLI 的模型命名空间互不相通（codebuddy 的 `--settings` 对 claude 无意义），因此：

- 执行 agent 与预设声明 agent **不一致**（跨 agent 回退、显式 `--agent` 换人）时，模型绑定
  整体丢弃并写 WARN 日志；绝不把 A CLI 的模型参数塞给 B CLI。
- 同 agent 的原地瞬态重试不换人，绑定保持有效。
- 绑定生效的 attempt 在账本（`attempt_records`，schema v6）写入实际生效的 `preset` / `model`；
  未绑定或被丢弃为 NULL。

## 5. 命令行一次性旗标

| 命令 | 锚定阶段 | 旗标 |
|---|---|---|
| `kc run` / `kc daemon` | implementation | `--preset <名> [--model <id>] [--reasoning-effort <档>]` |
| `kc supervise` / `kc supervise-daemon` | supervisor | 同上 |
| `kc ask` | planner | 同上 |
| `kc issue create` | content_generation | 同上 |
| `kc agent doctor` | what-if 预览 | `--preset` / `--model` / `--reasoning-effort` / `--lifecycle <键>` |

- `--preset` 把该阶段锚定到指定预设并**覆盖绑定同名字段**；`--model` / `--reasoning-effort`
  是预设同名字段的一次性覆盖（必须与 `--preset` 同用）。
- 仓库层 `.kedacode.toml` 的显式绑定仍会赢过 CLI 合成的全局层绑定；PRD 块（随 Issue 流动）最高。
- PRD 头部块写法（与 `lifecycle_agents` 块同型）：

```markdown
- lifecycle_presets:
  - verifier: plan
  - review: work
```

## 6. 排查与观测

- `kc agent presets`：列出全部预设。
- `kc agent doctor <agent> --preset <名>`：预览将被执行的完整 argv。
- `kc agent doctor --lifecycle <键>`：按阶段视角打印"解析出的 agent + 绑定的模型参数"；
  fix / closeout 在无实现者上下文时如实返回 `follows_implementation`。
- console 生命周期矩阵视图每行新增 `preset` / `model` / `reasoning_effort` 三个只读字段。
- attempt 账本 `attempt_records` 新增 `preset` / `model` 可空列（schema v6，自动迁移）。
