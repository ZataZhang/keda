# Agent Runner 使用指南

`kc`（issue-agent-runner）是一个将 GitHub Issues 转为本地 AI Agent 队列的 CLI 工具，已按照本仓库的四层架构迁移并集成。

CLI 入口基于 Typer/Rich：`kc --help` 会展示分组命令、参数和别名；脚本可继续使用历史命令，日常人工操作优先使用更短的别名。

## 功能概述

- **init**：在目标 Git 仓库创建仓库本地 `.kedacode.toml` 配置
- **labels sync**：在目标仓库创建或更新标准 labels（`agent/ready`、`agent/running`、`agent/supervising` 等）
- **issue create**：从一个或多个 PRD Markdown 文件创建 GitHub Issue，默认在 ready 前发布 PRD（可用 `--no-publish-prd` 关闭，兼容旧命令 `issue-from-prd`）；也可以用 `--from-prompt "<一句话需求>"` 直接开一条**没有 PRD** 的 Issue（与 PRD 路径参数互斥，生成的正文不含 PRD 锚点，验收小节靠 `--require-validation` 显式开启）
- **run**：单次轮询执行，**目标必填**——`--issue <N>` 定向处理一个 Issue，或传 PRD 路径（解析其回链 Issue），或显式 `--all-ready` 按优先级处理整个 ready 队列（兼容旧命令 `run-once`；不传目标即用法错误）。daemon 的互斥**只挡队列轮询**：同仓 daemon 在跑时 `--all-ready` 拒绝（`--takeover` 显式接管），`kc run --issue <N>` 照常执行——定向不要求 `agent/ready`，只按认领状态把关（见「显式定向的领取准入」）
- **review**：单次检查 `agent/supervising` 和 `agent/review` 的 Issues，基于 PR 上下文变化运行 supervisor cycle（兼容旧命令 `review-once`）
- **review-daemon**：常驻进程，按指定间隔循环执行 `review-once`
- **daemon**：常驻进程，按指定间隔循环执行 `run-once`；是**唯一**运行 autopilot 调度阶段的地方，可用 `--autopilot` / `--no-autopilot` 按次覆盖配置（只影响调度，不影响自动合并）
- **ask**：受限自然语言决策入口，默认只生成计划，确认后执行白名单动作
- **worktree cleanup**：清理 GitHub Issue 已关闭、远端分支已删除但本地仍残留的 `issue-<number>` 分支和 KedaCode worktree

## 安装

`kc` 已通过 `pyproject.toml` 的 `[project.scripts]` 注册：

```bash
# 通过 uv 运行
uv run kc --help

# 或安装后直接使用
kc --help
```

### 初始化门禁

除 `kc init` 外，所有 `kc` 子命令在执行前都会检查目标仓库是否已完成初始化（仓库根目录存在有效的 `.kedacode.toml`）。未初始化时命令会立即以非零退出码失败，并提示先运行 `kc init`：

```bash
# 首次在目标仓库使用前，必须先初始化
kc init

# 之后才能执行其他命令
kc labels sync
kc run --all-ready --dry-run
```

`kc init` 本身不受门禁限制，包括 `--dry-run` 和 `--force` 形式。本 PRD 不提供 `--skip-init-check` 等绕过开关。

### Shell 自动补全

`kc` 支持生成 shell completion。zsh 用户安装后，输入 `kc is<Tab>` 可补全到 `issue`：

```bash
kc completion install --shell zsh
source ~/.zshrc
```

如需只查看脚本内容而不写入配置：

```bash
kc completion show --shell zsh
```

`completion install` 同时支持 `--shell bash` 和 `--shell fish`。

补全同时覆盖 `kedacode` 别名入口（别名清单从分发的 `[project.scripts]` 元数据派生），
`kedacode is<Tab>` 与 `kc is<Tab>` 行为一致；旧版本安装的补全重跑一次
`kc completion install` 即可升级。

## 面向 Agent 的机读契约

`kc` 的另一类调用方是脚本和别的 agent。为此有三条对外契约：**显式声明的机器格式**、**语义退出码**、**运行时自省**。完整取值表与错误 envelope 字段定义见 [`docs/api/references.md`](../api/references.md)，`kc --help` 末尾也直接印出退出码表。

### 机器格式必须显式声明

数据类命令接受 `--json`，它是 `--output json` 的别名；不传任何旗标时输出仍是给人看的表格，**即使 stdout 是管道也不自动切换**（避免静默改变既有脚本的输入）。

```bash
kc issue list --json                 # 等价于 --output json
kc run --all-ready --dry-run --output json   # 计划以 JSON 给出
kc daemon status --json
```

机器模式下 **stdout 只承载数据**：进度、警告、日志（含应用日志的 stdout handler）一律改绑到 stderr，`kc <cmd> --json > out.json` 拿到的就是可 `json.loads` 的单个文档。失败时 stderr 输出一个 envelope：

```json
{
  "error": "not_found",
  "message": "Repository 'keda' is not registered.",
  "suggestion": "kc registry list",
  "retryable": false,
  "exit_code": 3
}
```

`suggestion` 一定是一条可直接跑的下一步命令；调用方只该读 `$?` 与这个 envelope，不要 parse 英文句子。

当前暴露机器格式的命令：`run`、`logs`、`tokens`、`schema`、`issue create`、`issue list`、`worktree path`、`registry list`、`daemon status`、`loop list`、`agent list`、`agent presets`、`agent doctor`（以 `kc schema --json` 的实际导出为准，新增命令无需改本文）。

**例外**：`kc ask` 与 `kc deliberate` 的 `--output` 是**输出目录**（文本型参数），不是格式；那里 `--output json` 表示名为 `json` 的目录，两者也都不接受 `--json`。

### 语义退出码

名称列就是失败 envelope 里 `error` 的取值，也与 `kc schema --json` 的 `exit_codes.values` 一一对应——`$?` 与 stderr 不会各说一套。

| 码 | 名称（envelope `error`） | 含义 | 调用方动作 |
|---|---|---|---|
| `0` | `ok` | 请求完成（只读输出同样算成功） | 继续 |
| `1` | `error` | 未归类失败，保持历史语义 | 读 stderr 后重试或升级 |
| `2` | `usage_error` | 旗标/参数组合不成立（互斥、缺目标、非法 lifecycle key） | 修命令，原样重试必然再失败 |
| `3` | `not_found` | 目标不存在：仓库、registry 条目、agent、可执行文件、日志；显式定向的 Issue 读不到或已关闭 | 换目标或跑 `suggestion` |
| `4` | `permission_denied` | 未授权：GitHub 未认证、仓库被禁用 | 提示人来认证或启用，别静默重试 |
| `5` | `conflict` | 当前状态阻止：`--all-ready` 时同仓 daemon 正在轮询队列、显式定向的 Issue 被存活持有者认领或处于未解除的 `agent/blocked`、workflow 模板文件已安装、loop 条目已存在 | 改名 / 用户明确要求时 `--force` / 先停掉冲突进程 / 按 `suggestion` 走 `kc blocked-continue`（活跃认领的错误会点名持有者 host 与 PID） |
| `10` | `dry_run_ok` | 计划校验通过且未写入任何东西，可直接当 CI 门禁 | 仅在核对计划正文后视为绿灯 |

`10` 只在机器模式下用于 dry-run 成功，人类模式仍是 `0`；`1` 保留给尚未归类的失败，新码只在有明确类别的失败点启用，因此只看「是否非零」的旧脚本行为不变。

### 运行时自省：`kc schema --json`

命令树是唯一事实源——`kc schema --json` 直接从已注册的 Typer/click 应用派生，不维护第二份清单：

```bash
kc schema --json | jq '.commands[] | select(.name=="issue list") | .options[] | {name, type, required, enum, default}'
```

每个参数导出名称、类型、是否必填、枚举取值（`--output` 的 `["table","json"]` 即由此而来）、默认值与示例；顶层同时给出 `exit_codes` 与命令总数。给 agent 写调用代码前先跑它，而不是抄文档。

## labels sync 详解

`kc labels sync` 会在目标 GitHub 仓库中创建或更新一套标准化的 issue 标签，作为整个 agent-runner 工作流的状态基础设施。

### 为什么需要同步标签？

`kc` 依靠 GitHub labels 实现任务状态的自动流转：

```
创建 Issue ─┬─（排队）贴上 agent/ready → daemon 自主挑选并认领（换成 agent/running）
            └─（点名）kc run --issue <N> 直接认领：不要求任何状态标签
               ↓
        AI 做完 → push → pre-PR review → Draft PR → agent/supervising
               ↓
        supervisor 通过 → 换成 agent/review → 人工审完关闭
               ↓
        出问题 → 换成 agent/failed 或 agent/blocked
```

没有这些标签，`kc` 无法识别哪些 Issue 正在执行、哪些需要 review；**但"可以被执行"不再等于"带 `agent/ready`"**——就绪标签只是守护进程自主挑选的准入，人显式点名的 Issue 不需要它（定向 run 的把关条件是认领状态，见「显式定向的领取准入」）。

### Workflow label 互斥

六个 AI 执行状态标签（`agent/ready`、`agent/running`、`agent/supervising`、`agent/review`、`agent/failed`、`agent/blocked`）是互斥的 durable workflow labels。runner 在任何状态切换时都会清理其他 workflow labels，只保留目标状态。这意味着：

- 不会同时出现 `agent/running` + `agent/review`
- 不会同时出现 `agent/supervising` + `agent/failed`
- 历史脏状态（如同时贴有多个 workflow labels）会在下一次被处理时自动收敛到单一状态

工具路由标签（`agent/codex`、`agent/claude`、`agent/kimi`）、类型标签（`type/*`）等非 workflow labels 不会被清理。发布档位标签 `direct-pr` 也属于这一类：状态流转不会替它"消档"，只有认领者在确认 Draft PR 后显式消费它。

### 标准标签

| 类别 | 标签 | 颜色 | 作用 |
|---|---|---|---|
| **AI 执行状态** | `agent/ready` | 🟢 绿色 | 已进入队列，等待 daemon 自主挑选认领；**不是执行的前置条件**——`kc run --issue <N>` 点名任何 open Issue 都不需要该标签 |
| | `agent/running` | 🟡 黄色 | 代码正在被修改（首次实现或 PR branch rework）。首次认领由 claim marker 选举裁决，同一时刻只有一个持有者；被存活持有者占用的 Issue，定向 run 会报冲突而不是双跑 |
| | `agent/supervising` | 🔵 浅蓝 | Draft PR 已创建，自动 post-PR supervisor 正在审查或重新处理 |
| | `agent/review` | 🔵 蓝色 | 自动总控审查已通过，当前 PR 等待人类 review |
| | `agent/failed` | 🔴 红色 | AI runner 执行失败 |
| | `agent/blocked` | ⬛ 黑色 | AI runner 需要人工介入 |
| | `agent/waiting` | 🟠 橙色 | Issue 依赖未满足，等待上游 closure |
| **工具路由** | `agent/codex` | 🟣 紫色 | 指定使用 Codex 执行 |
| | `agent/claude` | 🩵 浅蓝 | 指定使用 Claude Code 执行 |
| | `agent/kimi` | 🩷 粉色 | 指定使用 Kimi 执行 |
| | `agent/pi` | 🟪 紫罗兰 | 指定使用 pi 执行 |
| | `agent/codebuddy` | 🔷 蓝 | 指定使用 CodeBuddy Code 执行 |
| | `agent/qoder` | 🟠 橙 | 指定使用 Qoder 执行 |
| | `agent/opencode` | 🩵 天蓝 | 指定使用 OpenCode 执行 |
| **来源标识** | `source/prd` | 🔵 深蓝 | Issue 关联了仓库内的 PRD 文件 |
| **异步讨论** | `agent/deliberate` | 🩶 浅灰 | 复杂需求先走 Issue 评论区异步讨论，收敛后换 `agent/rework-prd` 落地 PRD |
| **发布档位** | `direct-pr` | 🟠 橙 | 一次性直发声明：任何认领方（其他机器、批量队列、daemon）认领后读到它就把该 Issue 解析为直发档。**不是 workflow 状态标签**，发布确认后被认领者消费删除；见下节「Issue 上的 `direct-pr` 标签」 |
| **任务类型** | `type/feature` | 🔵 | 功能需求 |
| | `type/refactor` | 🟣 | 代码重构 |
| | `type/bug` | 🔴 | Bug 修复 |
| **队列状态** | `status/backlog` | 🩵 | 待办/未开始 |

> 标签名称可在全局 `config.toml` 或目标仓库 `.kedacode.toml` 的 `[agent_runner.labels]` 段自定义。

### 使用示例

```bash
# 同步当前目录对应的仓库
kc labels sync

# 同步指定路径的仓库
kc labels sync --repo /path/to/target-repo

# 同步指定配置仓库
kc labels sync --repo-id keda
```

首次使用 `kc` 时只需执行一次，后续标签会自动复用。

## Issue 依赖门禁（Dependency Gate）

当多个 Issue 之间存在先后依赖关系时（例如 B 组必须等 A 组全部合并），可以使用依赖门禁实现自动调度，无需人工盯着上游 PR 合并后再给下游 Issue 打 label。

### 基本原理

依赖门禁采用 **PRD 结构化依赖声明 + KedaCode materialized marker/label** 的无状态方案：

- PRD 中包含工具无关的 `Delivery Dependencies` 小节
- `kc issue create` 将 `Gate type: hard` 的依赖物化为 Issue body 中的 `<!-- iar:depends-on ... -->` marker
- runner 每次轮询时实时查询 GitHub 状态：依赖未满足时跳过领取、叠加 `agent/waiting` label 并写等待 comment
- 依赖未满足的 ready Issue 不消耗 `max_issues` 的处理额度；runner 会继续扫描后续 ready Issue，直到找到可领取任务或扫描窗口耗尽
- 上游 Issue 全部 closed 后，下一轮轮询自动移除 `agent/waiting` 并正常领取

### PRD 中的 Delivery Dependencies 语法

在 PRD 中添加如下小节：

```markdown
## Delivery Dependencies

- Depends on tasks/issues: #42, tasks/pending/P2-FEAT-20260527-190923-prd-from-issue.md
- Gate type: hard
- Sequence: via-main
- Notes: 等待上游 API 改造完成
```

字段说明：

| 字段 | 说明 |
|---|---|
| `Depends on tasks/issues` | 上游 Issue 编号或 PRD 引用；Issue 支持 `#N` / `N`，PRD 支持 repo-relative 路径或 `tasks/` 下唯一文件名/文件 stem，多个用逗号/分号或 Markdown 子列表分隔；引用后可以追加说明文字，解析器只提取引用 token |
| `Gate type` | `hard` = 阻塞门禁；`soft` = 仅文档信息，不阻塞；`none` = 不生成依赖 marker |
| `Sequence` | `via-main`（默认）= 等上游合并进主线后再开工；`stack` = 不等合并，直接基于上游分支开工（需 `Gate type: hard`，且只允许**单个**上游） |
| `Notes` | 自由备注 |

#### 排序策略（`Sequence`）

`Sequence` 是**工具无关**的排序意图；keda 的 runner 把它翻译成自己的行为，不使用 keda 的项目里该字段合法但惰性。

- **`via-main`（默认）**：下游等上游 Issue 关闭（即上游已合并进 base），再从**刷新过的** base 分支 fork。为消除"本地 base 落后导致下游看不到上游改动"的静默缺口，runner 在建 worktree 前会 `git fetch <remote> <base_branch>`，并优先从 `remote/base_branch` 这个远端跟踪引用 fork；fetch 失败时保守回退到本地 base 并告警。
- **`stack`**：下游在**上游分支就绪**（`issue-<上游>` 已有 open/merged PR）后即放行，worktree 从上游分支 `issue-<上游>` fork（**先 `git fetch` 刷新该上游分支**，取远端跟踪引用；仅当 fetch 失败但本地分支存在时才回退本地，两者都没有则 fail fast），从而保证下游包含上游尚未合并的改动。
  - 依赖门禁据此改用"等待上游分支就绪"，与等待 Issue 关闭不同。
  - 合并队列**不会**在中途合并 stack 链上的下游 PR（否则 rebase 到远端 base 会破坏"叠在上游"的基础）；等上游合并后，合并队列把下游 PR 的 base 重指到主线（收敛），再走既有 rebase + 合并。
  - **前置条件**：`stack` 只在 `Gate type: hard` 时物化 marker（`Gate type: none`/`soft` 会被静默忽略，等同 via-main）；且只支持**单个**上游，声明多个上游时 `kc issue create` fail fast（DAG 不在范围内）。
  - **收敛的已知代价**：keda 以 squash 合并上游，收敛时下游 `git rebase` 到 main 需要处理"上游提交已被 squash 进 main"的情况（多数被识别为空提交丢弃，但 base 同一区域被改动时仍可能冲突，冲突走既有 agent 解决路径）。这是被接受的复杂度，不是无代价的平移。

> 依赖 marker 里以 `mode="stack"` 承载该策略（如 `<!-- iar:depends-on #42 mode="stack" -->`）；未声明时默认 `via-main`。

PRD 引用只在 `kc issue create` 发布时解析，不会原样写入 Issue body。解析规则：

- 引用 PRD 已包含 `- GitHub Issue: .../issues/N` 时，物化为 `#N` 依赖 marker。
- 引用 PRD 还没有 Issue link 时，命令 fail fast，并提示先创建上游 Issue（`kc issue create` 会把 Issue 链接回写进 PRD），或改成明确的 Issue 编号。
- 无依赖时可以留空或写 `none`。

> 历史 PRD 里可能残留旧版 `Group` / `Depends on groups` 字段；解析器接受但忽略它们，不再产生任务组依赖。

### CLI 参数覆盖

即使 PRD 中没有写依赖，也可以在 `kc issue create` 时显式指定：

```bash
# 声明依赖上游 Issue #42 和 #43
kc issue create tasks/pending/foo.md --depends-on 42 --depends-on 43
```

CLI 参数与 PRD 声明会**合并去重**，CLI 参数优先级最高。

### runner 行为

- 无 `iar:depends-on` marker 的 Issue：行为与改动前完全一致（零破坏）
- 依赖未满足：`agent/ready` 保持，叠加 `agent/waiting`，写 comment 说明阻塞原因
- 依赖满足：自动移除 `agent/waiting`（若有），正常进入领取流程
- 上游出现 `agent/failed` 或 `agent/blocked`：等待 comment 中会点名该上游 Issue，提示 operator 干预
- 连续多轮 blockers 不变时只有一条 comment（按 blockers 集合去重）
- `--dry-run` 模式下打印等待原因但不写任何 GitHub 状态

### 状态流转补充

```text
agent/ready + 依赖未满足 → agent/waiting（跳过领取）
agent/ready + 依赖满足   → 移除 waiting（正常领取）
agent/waiting              → 上游 closed → 自动放行
```

依赖判定完全无状态：每次轮询现查现算，依据 GitHub 实时状态，不引入本地缓存。

## 中断恢复（worktree rebase 卡死）

runner 在 rebase 中途崩溃或被中断（例如 post-PR supervisor 正在把 PR 分支
rebase 到最新 base 时进程退出），会把 Issue 的 worktree 留在 **detached HEAD +
未完成 rebase** 的状态：分支 ref 仍指向原 tip，但 worktree HEAD 停在 base 上。

- **自动重新认领**：下一轮轮询发现仍是 `agent/running` 的 Issue 时，除了「有可发布
  的干净本地 commit」外，也会检测 worktree 是否处于 mid-rebase / detached 状态。命中
  即进入恢复路径，由 `_ensure_worktree_branch` 治愈：优先 `git rebase --continue`；
  有冲突则让 agent 解决并续跑；超过配置的修复次数才回退 `git rebase --abort` 并重挂
  分支。治愈后照常复用已有 commit 完成发布。
- **并发互斥**：恢复路径在动任何 git 之前先获取 **per-worktree 原子认领锁**
  （`.agent-runner/blocked-claim.lock`，与 blocked 恢复共用同一把锁），确保不会有两个
  runner 同时 rebase/发布同一个 worktree。抢锁失败的 runner 记一条日志后跳过本轮。
- **死进程接管**：锁文件记录持有者 PID；若持有者已退出，下一个 runner 会自动夺锁，
  从而接管被中断的工作。

> 局限：认领锁基于 PID 存活判断，对「进程仍在但已放弃该任务」的情形无法自动接管；
> 这类现在由崩溃对账的 `reclaim_ttl_seconds`（claim 老化）兜住，见下一节。

## 崩溃对账与 Agent 会话续传（daemon 死亡后的僵尸 attempt）

硬中断（`kill -9`、OOM、机器重启）不会把 Issue 从 `agent/running` 退回，而 daemon 只认领
`agent/ready`，所以"本机认领、进程已死"的僵尸 attempt 过去会永久卡死。现在 daemon 每轮 tick
开头（Phase -1，早于审议与领取）先做一次崩溃对账。

判定**只读四类现成事实**，不引入心跳表 / 租约库等第二状态源：

| 证据源 | 取法 |
|---|---|
| 本机活跃 attempt 状态 | Issue 上最近一条 claim 标记（`<!-- iar:claim host= pid= started_at= agent= -->`）与该 PID 是否存活 |
| Issue label | 是否仍是 `agent/running`（已关闭、已改别的状态一律不碰） |
| comment attempt history | 该 Issue 此前已被对账处置的次数（决定恢复预算是否耗尽） |
| worktree 现场文件状态 | `kc worktree path` 可得、目录存在且 `.git` 指针仍在 |

处置出口**恰好三个**，每个都写一条 `## Stale Attempt Reconciled` comment，四要素齐全（中断时间 /
中断原因分类 / 处置结论 / 依据），表格风格与既有 Attempt History 一致：

1. `resume-session`（续传恢复）：worktree 完好 + 上轮 agent 声明了续传能力 + worktree 里留有
   session 记录 → 回 `agent/ready`，下一轮 claim 直接续上原会话。
2. `re-enqueue`（重新入队）：上述任一条件不满足（agent 不可识别 / 不支持续传 / 无会话记录）→ 回
   `agent/ready`，全新会话重跑。
3. `mark-failed`（判失败）：worktree 不可解析（路径不存在或 `.git` 指针丢失，重跑只会在坏目录上
   白跑一轮），或跨进程恢复预算已耗尽（复用既有 `runner.max_recovery_attempts`，不新增预算键）。

`skip` 不是出口而是"不触碰"：claim 来自别的机器、或进程仍存活且 claim 未过 `reclaim_ttl_seconds`，
一律原样保留——宁可漏对账，也不把健康任务重新入队造成双跑。

对账动作幂等：comment 末尾带隐藏标记 `<!-- iar:reconcile hash="<16hex>" seq="<n>" -->`，hash 覆盖
Issue / 主机 / PID / 中断时间 / agent / 处置结论（不含 seq）。同一判定重放时零新增 comment；若上次
是在"comment 已写、label 未改回"之间被打断（写序固定为**先 comment 后 label**），重放会补齐那次缺失
的 label 写回而不再评论一次，因此不存在永久半完成态，也不会并发领取同一 worktree。

会话续传的实现分工：session id 由 agent 输出协议解析（`engines/agent_runner/output_protocols/claude_stream_json.py`），
core 侧 `agent_runner_session_store.py` 以 tmp + `os.replace` 原子写入 worktree 局部记录
`.iar/agent-runner/sessions/<agent>.json`；recovery 轮次经 `agent_spec` 的声明式
`supports_resume` / `resume_args` 模板拼出续传命令行（编排层不硬编码任何 CLI 的参数形态）。
记录缺失或 agent 未声明能力时**静默降级为全新会话**，降级发生在同一次 attempt 内，不额外消耗
recovery 轮次。

```toml
[agent_runner.daemon]
reconcile_stale_attempts = true   # 主开关；设为 false 即回到本特性落地前的现状
reclaim_ttl_seconds = 10800       # claim 含 started_at 且超过该时长，即便 PID 仍活也判为 stale
```

`reconcile_stale_attempts = false` 时 Phase -1 整轮空转，僵尸 Issue 保持 `agent/running` 不被触碰、
不留任何 comment（负控口径见 `.iar/evidence/`：rv-1 关掉开关后僵尸确实纹丝不动）。

## 复杂需求：异步 Issue 评论讨论（`agent/deliberate`）

`agent/rework-prd` 是一次性读 Issue 体 + 全部评论直接生成 PRD 的全自动管道，**适合简单需求**。
对需要来回澄清才能定清楚的复杂 Issue（"先讨论清楚再写 PRD"），请改用 Issue 评论区异步多轮讨论：

1. 创建 Issue 时打上 **`agent/deliberate`** 标签。系统不自动分诊，由人显式声明"这是需要讨论的复杂需求"。
2. `kc run --once` / `kc daemon` 在 Phase 0 发现带 `agent/deliberate` 的开放 Issue，会复用 `run_agent_deliberation` 引擎（后台 NoOp 视图，不弹 TTY）跑多角色内部互辩，并把 synthesizer 的输出贴成一条结构化"澄清问题清单"评论，类别固定为 5 个：
   `## 范围边界` / `## 约束` / `## 验收标准` / `## 技术选型` / `## 风险`。
3. 每条 AI 评论尾部追加一个隐藏的 `<!-- iar:event version=1 phase=deliberation_question_posted cycle=N issue_comments_count=K -->` marker；后续轮询靠它判断"轮到谁"。
4. 人直接在 GitHub 上回复评论补充信息。下一次轮询发现 `当前评论数 > marker.issue_comments_count`，轮到 AI 续问，`cycle` 递增。
5. 连续 `stale_rounds_before_hint`（默认 3）轮 AI 提问但用户回复信息量很少时，问题清单评论末尾会自动追加"讨论接近完成，可将标签改为 `agent/rework-prd` 落地"的软提示。
6. 讨论清楚后，**人手动**把 `agent/deliberate` 换成 `agent/rework-prd`。Phase 1 接管，把 Issue 体 + 完整讨论评论生成为 PRD 并 push draft PR。
7. 收敛完全由人换标签触发——AI 不自动判定信息够了、不自动改标签、不自动产 PRD。

> 失败隔离：单个 Issue 跑合议失败时打 `agent/failed` 标签 + 失败说明评论，不污染其它 Issue 也不中断 Phase 1/Phase 2。
> 成本：`max_deliberation_issues`（默认 1）+ `[agent_runner.deliberation].default_rounds`（默认 2）共同决定每轮合议 agent×rounds 次调用；高复杂度 Issue 临时调小 rounds 可以节能。

### 实现阶段 prompt 内联 PRD 全文

不论走的是 `agent/rework-prd` 还是 `agent/deliberate`，最终都会进入 Phase 2 写代码。
原本实现/恢复/续作 prompt 里只有一行"读 PRD 路径"的指针；现在
`agent_runner_feedback._build_prd_context_block` 会**把 worktree 内 PRD 文件正文直接内联进
prompt**（带长度上限，缺省 20 000 字符）。超过上限会尾部截断并附"完整 PRD 见 `<path>`"提示。

内联实现的好处：

- 实现 agent 冷启动时不再需要先 `cat` 整个 PRD，节省一轮文件 I/O 与上下文切换。
- PRD/讨论沉淀的上下文一步到位地到达写代码阶段，避免"PRD 全在 Issue 评论里、实现阶段只剩几行指针"的丢失。
- 内联受长度上限保护，且 PRD 文件缺失时优雅回退到原有指针文案。

## PR body 契约（prd skill PR-Native Acceptance）

runner 发布的 draft PR 正文要对齐 prd skill `references/pr-evidence-and-merge-acceptance.md`
要求的 PR-Native Acceptance 结构。契约只作用于 Issue body 带 `PRD path:` 锚点的
PRD 交付；无 PRD 的轻量 Issue 不新增要求。实现分四层，全部落在
`agent_runner_pr_body_contract.py`：

1. **prompt 教学**：draft PR 正文以 agent 模式生成时，`create_draft_pr` 会把 skill
   的发布契约参考文本注入生成 prompt（调用侧包装 config，`generated_content`
   与 skill 解耦）。教学要求 agent 在正文里写 `- PRD: <relative prd path>` 行和
   `<!-- iar:merge-acceptance version=1 -->`（"合并即接受"声明）hidden marker。
   PRD 由 runner 在本 PR 里归档，所以 PRD 行指向 `tasks/archive/` 下的位置（文件名取自
   Issue 的 `PRD path:` 锚点）；声明句说的是"合并即接受所列人审决策与可见结果，并授权
   合并后在该 PRD 上补记验收记录"（authorizes post-merge acceptance recording），
   不再是"授权合并后归档"。skill 或参考文档不可达时静默跳过教学。
2. **确定性正文补锚点**：正文不是 agent 写的——`fallback` 兜底正文（生成关闭、
   agent 失败，或产出不含 `Closes #N`）与 `template` 渲染出的 `body_template`
   （含 `fallback = "template"` 的中间兜底）——教学无从谈起，由
   `append_missing_contract_anchors` 在末尾直接补一个
   `## Human Acceptance And PRD Archive` 小节：`- PRD: <归档路径>` 行（Issue 路径不在
   `tasks/pending/` 下、换算不出归档路径时原样写 Issue 路径），加 merge-acceptance
   marker 与"合并即接受"声明句——合并者接受 PRD 记录的人审决策与可见结果，并授权合并后
   补记验收记录（勾选 Human-Confirmed 项、横幅改为 ✅ 已验收），前提是门禁保持全绿且合并
   树与验证树一致。形态取自 skill 参考文档，只指向 PRD，不内联决策与可见结果清单。小节
   标题与 marker 被合并队列与测试钉住，沿用旧名不改。只补缺失的锚点，重复执行不变。
3. **发布端软门**：正文（补锚点之后）做可 grep 锚点校验（`prd-link` 认 PRD 的归档
   路径，也认 Issue 记录的 pending 路径，存量 PR 与人手写的正文不会因此被标注）；缺失时**不阻断发布**，
   而是在正文末尾追加 `<!-- iar:pr-contract version=1 missing=... -->` 标注块，列出
   缺失锚点并说明合并队列将拒绝自动合并。经第 2 层之后仍缺锚点的只剩
   **agent 撰写的正文无视了教学**——标注正好暴露这个信号，agent 正文不会被静默
   补齐，"缺什么"对人可见，而不是像历史 PR 那样静默缺件。
4. **合并端硬门**：autopilot 合并队列在 verifier 门禁之后消费发布端写下的
   `iar:pr-contract` 标注——带标注的 PR 走 `skipped_pr_contract_missing` 静默
   跳过（不合规状态已在 PR body 标注块对人可见，逐轮评论只会刷屏）。机制
   上线前发布的存量 PR 无标注，不受影响。

设计原则是**发布端软、合并端硬**：正文格式问题永远不应阻止代码被推送和审阅
（那会触发恢复循环烧预算），只应阻止"看起来合格"地被自动合并。

## 仓库本地配置

`kc` 默认以当前 Git 仓库作为目标仓库。**首次在目标仓库使用前必须先执行 `kc init`**，否则除 `kc init` 外的所有命令都会失败并提示初始化。

```bash
cd /path/to/target-repo
uv run --project /path/to/keda kc init --dry-run
uv run --project /path/to/keda kc init
```

`kc init --dry-run` 只打印将要写入的内容，不创建文件。新生成的 `.kedacode.toml` 会包含全部**仓库级可覆盖**配置段及其具有默认值的字段，其中 `[agent_runner.autopilot]` 默认关闭；如需启用快速合并，还必须同时把 `[agent_runner.safety].auto_merge` 设为 `true`。这些初始值会固定为当前仓库的显式配置，后续修改全局默认值不会覆盖它们；**`generated_content` 是例外**：脚手架不写它（见下文「`generated_content` 不由 `kc init` 写入」）。`.kedacode.toml` 已存在时，`kc init` 会拒绝覆盖并把用户导向 `kc skill install`；确认需要重建时显式传入 `--force`。

`kc init` 成功写入本地配置后，还会自动把当前仓库注册（或更新路径）到全局 `config.toml` 的 `[agent_runner.repositories]` 中，使 `kc daemon` 默认即可在当前仓库启动。如果该 `repo_id` 已在 registry 中但指向不同路径，init 会自动更新 registry 路径到当前位置。

### 机器级 `config.toml` 的认定与托管进程继承

`config.toml` 同时装两类配置：**KedaCode 自己的** `[agent_runner]`（含生命周期矩阵、registry、超时、验证命令等），以及**宿主应用的** `[app]` / `[database]` / `[preview]` 等。两类按不同规则解析：

- `[agent_runner]`：`KEDACODE_CONFIG` → 从 cwd 向上查找（**只接受带 `[agent_runner]` 表的文件**）→ `~/.kedacode/config.toml`（缺失时从源码根 seed）→ keda 源码根 `config.toml`。模板派生项目在仓库根也有一份同名 `config.toml`，但那是应用配置、不含 `[agent_runner]`，因此不会被当成机器级配置：否则在目标仓库内运行的 runner 会整份顶掉 `~/.kedacode/config.toml`，矩阵、registry、超时等设置全部静默失配（历史上表现为"面板显示实现用 codebuddy、实际领活却用了 claude"）。
- 其它段（应用自己的配置）：仍按 cwd 向上查找最近的项目 `config.toml`，即目标仓库根那份——`preview_env.py` 等脚本依赖这个语义读到本仓的 `[preview]`。

面板（`kc console`）托管或触发的 runner 子进程，cwd 就是目标仓库（见 `resolve_console_spawn_cwd`）；因此面板会把自己当前生效的机器级 `config.toml` 以 `KEDACODE_CONFIG` 注入子进程，保证"界面上显示的配置"与"实际执行用的配置"是同一份。手动在目标仓库里直接执行 `kc run` / `kc daemon` 且未设 `KEDACODE_CONFIG` 时，机器级配置取 `~/.kedacode/config.toml`——想让某个仓库的每个阶段都用指定 agent，写该仓库 `.kedacode.toml` 的 `[agent_runner.lifecycle_agents]` 是最稳的一层（仓库层覆盖机器层）。

在"选 agent"之上，还可以给阶段绑定**命名模型预设**（`[agent_runner.presets.<name>]` + `[agent_runner.lifecycle_presets]`）：绑定后该阶段整体由预设决定 (agent, 模型, 推理档)，遮蔽矩阵同键声明；换人（fallback / 显式 `--agent`）时模型绑定自动丢弃并在日志与 `attempt_records`（`preset` / `model` 列）中标注。完整语义、优先级与命令行一次性旗标见 [Agent 模型预设](model-presets.md)。

### `.gitignore` 托管块与 prd skill 契约

`kc init` 会在目标仓库 `.gitignore` 写入一个 `# >>> iar (managed by iar init) >>>` 托管块（幂等、可重跑；`--no-update-gitignore` 跳过），其中除 `.iar/`、`.agent-runner/`、`.iar-worktrees/` 外还包含 `tasks/evidence` 白名单段：<!-- legacy-alias -->

```gitignore
tasks/evidence/**
!tasks/evidence/**/
!tasks/evidence/**/*.md
```

这段白名单是 daemon 流证据目录约定的 git 语义基础：执行 agent 把证据写进 `tasks/evidence/<prd-stem>/`（无 PRD 的 Issue 兜底 `tasks/evidence/issue-<N>/`），`git add -A` 天然只把 `.md` 文本报告（verification-plan / evidence-report / verifier-report）带进 commit，截图、录屏、oracle 脚本等原始产物不进 git 历史；发布前拦截（`ensure_no_evidence_paths_in_changes`）再兜底拒绝被 `git add -f` 强制加入的非 `.md` 证据产物。显式配置 `validation.evidence_dir = ".iar/evidence"` 的仓库不受此影响，保持整目录排除的旧行为。手工配置 `evidence_dir = "tasks/evidence"` 而绕过 init 的仓库必须自行保证上述白名单规则在场。

同时 `kc init` 会从远程模板仓库安装 prd / code-reviewer skill（`--force` 会传递给 skill 安装，允许覆盖本地改过的同名 skill）。PRD 格式约定（Change Log 条目结构、验收复选框语法、分组标题、rv-id 证据命名、证据目录布局）的唯一出处是 prd skill 的 `## Machine Contract (vN)` 章节，而且**解析实现也只有一份**——skill 自带的 `scripts/prd_contract.py`：kc 各 prompt 只注入一行指向该契约的指针、不复述教学，验收清单与 Change Log 也不再由 kc 自己解析，而是把 PRD 文本交给该脚本、取回 JSON 结构。**daemon 在每轮执行循环前预检**三件事：prd skill 可解析、契约主版本落在受支持集合内（当前 v3/v4/v5；集合语义让"skill 先 bump、keda 后跟进"的发版错峰不会卡住）、以及兄弟 `scripts/prd_contract.py` 与 `SKILL.md` 成套存在（只装半套会在启动时就点名缺哪个文件，而不是拖到交付门禁）。错误给出安全修复方式（重装 skill 用 `kc skill install`、重建仓库配置才用 `kc init --force`，或设置 `KEDACODE_PRD_SKILL_PATH` 指向完整 skill），不建议盲目运行可能覆盖用户级 Skill 的 `--force` 形式。**安装目标首位是 keda 自有目录 `~/.kedacode/skills`**：解析时优先取这份，因此用户删掉 agent 目录里的 prd 副本不影响 `kc` 的正常解析；其后才是各 agent 用户级目录。CI / 测试可用 `KEDACODE_SKILLS_DIR` 把安装根覆盖到临时目录。

KedaCode 自带的 `kedacode-operator` Skill 随 Python 发行包安装，无需联网下载。它是 hub + references 结构：主文件 `SKILL.md` 只有路由表、共享基线（只读请求不启动执行、`kc run` 必须带目标、机器输出用 `kc schema --json` 自检）与 Safety 不变量，各子命令的流程步骤拆在同目录 `references/*.md`（建 issue / 查 issue / 跑一次 / 看进度 / 卡住了 / 后台跑 / CI 交付 / 装环境与查 Agent 配置共 8 份）。命令语义与参数以本文档和真实命令树（`kc --help` / `kc schema --json`）为权威，references 只写"什么时候用、按什么顺序用、别做什么"的流程分工。`kc init --dry-run` 会显示目标路径；安装的一致性比对以**受管文件集合**（主 `SKILL.md` + `references/*.md`）逐字节为准，用户自己新增的文件不会触发冲突；目标下已有改动过的受管内容时默认保留并报告冲突，只有显式 `--force` 才覆盖。

ready Issue 按 GitHub label `priority/P0`、`priority/P1`、`priority/P2`、`priority/P3` 识别优先级，按 P0→P3 排序，同级按 Issue number 升序；缺少这些标签的 Issue 排在显式 P3 之后。`kc run --all-ready --dry-run` 与实际执行共用排序，并在预览中显示 priority；每轮排序范围是 GitHub 返回的最多 100 条 ready Issue 候选，不能据此承诺候选窗口以外的全局排序。`kc issue list --state`、`--label` 与 PR 筛选分别由公开参数应用。

### 用户级 Skill 的独立安装与刷新（`kc skill install`）

`kc init` 内嵌的 Skill 同步（远程模板 `prd` / `code-reviewer` + 随包 `kedacode-operator`）也可以不经仓库配置独立触发：`kc skill install`。它与 init 走同一份安装实现（内容来源、安装根解析、幂等跳过、旧名副本清理与 fail-closed 语义完全一致），但不读写 `.kedacode.toml`、不注册仓库、不同步 labels 与 gitignore——典型场景是升级 kedacode 后 operator skill 改名，而常用仓库里 `.kedacode.toml` 已存在、`kc init` 第一步就会被挡住，此时在各安装根补装新名 skill 并清理未被改动的旧名副本只需一条命令：

```bash
kc skill install --dry-run   # 预览全部安装根的安装计划，不写任何文件
kc skill install             # 安装/刷新全部用户级 Skill
```

与 init 相同的冲突语义：

- 用户改动过的同名 Skill（受管文件与随包/远程模板不一致）默认保留并报告冲突，`--force` 才覆盖；
- 旧名副本只有逐字节等于历史随包版本才自动删除，改动过的保留并回显路径；
- 远程模板 `prd` / `code-reviewer` 的本地 `SKILL.md` 与远程不同且未传 `--force` 时报错退出（fail-closed，不覆盖）。

`kc skill install` 面向用户级安装根，与目标仓库无关：传 `--repo` / `--repo-id` / `--config` 会被以用法错误拒绝（退出码 2）。

### 提交前验证命令自动探测

`kc init` 不会写死验证命令，而是按目标仓库实际情况探测 `[agent_runner.runner].verification_commands` 与 `pre_commit_verification_command`（实现见 `src/backend/engines/agent_runner/repository_local.py`）。探测只生成纯 shell 字符串条目；浏览器 E2E 结构化条目（`kind = "browser_e2e"`）需运营者手工添加，形态与执行语义见下文"浏览器 E2E 验证命令形态"一节：

- `verification_commands` 在 agent 请求 commit 后、staging 完成时运行；
  - 基线始终包含 `git diff --check`；
  - 声明了 `mkdocs` 依赖且存在 `mkdocs.yml` → 追加 `uv run mkdocs build`；
  - 存在 `just test` 配方（含经 `import 'justfile.shared'` 等导入的配方，以及 `@test` quiet 前缀）→ 追加 `just test`，并**优先选它**：`just test` 跑的就是 `git commit` 时 pre-commit 强制的同一组 lint/format/test 钩子，并刷新 `.last_tested_commit` 标记，因此 runner 验证通过后提交不会再被 pre-commit 挡下；
  - 否则走通用回退：声明了 `pre-commit` 依赖且有 `.pre-commit-config.yaml` → 追加 `uv run pre-commit run --all-files`；声明了 `pytest` 且有 `tests/` → 追加 `uv run pytest -q`。
- `pre_commit_verification_command` 在 `git add -A` 之后、`git commit` 之前单独运行。它的作用是把 `git commit` 时才会触发的 pre-commit 钩子提前到 runner 可控阶段执行：失败会转成 `VerificationFailedError`，由 Fix Agent 修复，而不是以裸 `CalledProcessError` 退出。只有仓库同时声明 `pre-commit` 依赖且存在 `.pre-commit-config.yaml` 时才会被写入；配置里包含 `check-test-flag` 时命令会带 `SKIP=check-test-flag` 前缀，避免在没有 `just test` 的仓库里死锁。

代码层面的默认值只有 `git diff --check`（避免在未 `kc init` 的仓库里因缺少 mkdocs/pytest 而直接失败）。`kc init` 会按上述规则生成更完整的项目专属命令列表。

> **autofix 钩子的一次幂等重试**：`ruff-format`、`trailing-whitespace`、`end-of-file-fixer` 这类钩子会**就地重写文件**再以非零码退出（`files were modified by this hook`），这是纯格式化、幂等可恢复的失败。`commit_requested_changes` 对**两道门禁都**做同一处理（`_run_commit_gate_with_autofix_retry`）：门禁非零时先看 `git diff --quiet` —— 若跟踪文件被改写过，就校验禁改路径、`git add -u` 重新 stage 并**再跑一次**；只有「非零但没改任何文件」（真实 lint/检查错误）或「重新 stage 后第二次仍非零」才判 `VerificationFailedError`。此处刻意只用 `-u`（本函数处理的是 autofix 就地重写已跟踪文件，重跑门禁才是目的）；门禁自己生成的未跟踪新文件由提交前那道 `_verification_left_unstaged_worktree_changes` 兜底补 `git add -A`。这一点对 `verification_commands` 尤其重要：它排在 `pre_commit_verification_command` 之前，而 `just test` / `just lint --full` 内部往往就是同一批 pre-commit 钩子，早期只给后者加容错时纯格式化失败会在更早的门被直接判死（实证：freshai Issue #96 的 pre-PR review 补丁）。注意 `just test` 一类配方常把 lint 输出重定向掉（`just lint --full >/dev/null 2>&1`），失败时 GitHub 评论里只剩 `ERROR: Lint failed`；判断是不是 autofix 可比对被改文件的 mtime 与失败时间戳，并在 worktree 里原地重跑一次同一条命令。

探测出的命令列表会被 runner 写入首次实现 prompt、Fix Agent prompt 和 Recovery Agent prompt，让 Agent 在编码和修复阶段都能看到完整的交付门禁。所有 prompt 都会提醒 Agent 在请求 commit 前检查项目规范（AGENTS.md、命名、依赖方向、文件编码、行长度限制等）。

> **check-test-flag 护栏**：若仓库装了 `check-test-flag` 钩子却没有可探测的 `just test` 配方，`kc init` 会**跳过** `verification_commands` 里的 `pre-commit run --all-files`（工作区级别的 bare pre-commit 会死锁）；而 `pre_commit_verification_command` 会带 `SKIP=check-test-flag` 前缀写入，这样 git add 后仍提前运行其余 lint/format 钩子，同时避免 check-test-flag 在 `just test` 标记未刷新时失败。`git commit` 本身仍会运行完整 pre-commit（含 check-test-flag）。这类仓库最佳做法仍是补一个 `just test` 配方或移除 check-test-flag。
>
> 探测只在 `kc init` 时发生：已存在的 `.kedacode.toml` 不会自动更新，需重跑 `kc init --force` 或手改 `verification_commands` / `pre_commit_verification_command` 才会采用新探测结果。

> **init 后务必复核 verification_commands / Please review verification_commands after init**
>
> `kc init`（含 `--dry-run`）完成后会打印一条黄色提示，列出当前生成的 `[agent_runner.runner].verification_commands`。规则探测只能识别常见技术栈，非 Python 仓库、自定义脚本或特殊结构可能只得到 `git diff --check`。
>
> 看到提示后请打开 `.kedacode.toml`，根据项目真实的 test / lint / build 流程检查并调整这些命令；也可以把列表复制到自己的 AI 工具询问建议。这是交付门禁的最后防线，务必确认它确实能拦住未通过检查的代码。
>
> After `kc init` (including `--dry-run`) a yellow reminder prints the detected `[agent_runner.runner].verification_commands`. Rule-based detection only covers common stacks; non-Python repos, custom scripts, or special layouts may receive only `git diff --check`.
>
> Please open `.kedacode.toml` and review the commands against your actual test / lint / build workflow, or paste the list into your own AI tool for suggestions. This is the final gate before the runner commits, so make sure it can actually block broken changes.

生成内容节选（完整且随版本演进的字段以 `kc init --dry-run` 输出为准；回归测试会校验除 `generated_content` 外的所有仓库级配置字段均被渲染）：

```toml
# KedaCode 本地仓库配置
# `kc init` 会写入仓库级配置的默认值（generated_content 除外，见文末示例）；这些值
# 随后显式覆盖全局默认值。没有默认值的可选字段、以及未写入的 generated_content，
# 才继承 config.toml / 环境变量 / 代码里的全局默认值。
# 本文件在 daemon 启动时读取一次：改完要重启 daemon 才生效（`kc run` 这类单轮
# 命令每次调用都重新读取，无需重启）。
# 完整字段说明见 docs/guides/agent-runner.md。

# 仓库身份标识（用于多仓库管理时区分不同仓库）
[agent_runner.repository]
# 仓库在 KedaCode 中的唯一标识，通常与远程仓库名一致
id = "target-repo"
# 是否允许 runner 处理该仓库的 Issue
enabled = true
# 管理终端 / 日志中显示的友好名称
display_name = "target-repo"
# 可选：GitHub owner/name（``gh pr list --repo`` 使用）。
# 缺省时 ``kc issue list`` 的 PR 列为空 + stderr 一次性 WARN。
github_repo = "owner/target-repo"

# Git 发布配置：推送 remote、目标基础分支 base_branch
[agent_runner.git]
# 推送分支和创建 PR 时使用的 Git remote 名称
remote = "origin"
# 创建 worktree 与 PR 的目标基础分支
base_branch = "main"

# Issue worktree 的创建与定位命令；默认使用 kc worktree，通常无需修改
[agent_runner.worktree]
# 创建新 worktree 的命令；{issue_number} 和 {base_branch} 会被替换
create_command = "kc worktree create --branch issue-{issue_number} --base-branch {base_branch}"
# 复用已有 worktree 时定位路径的命令
reuse_command = "kc worktree path --branch issue-{issue_number}"
# 获取 worktree 绝对路径的命令
path_command = "kc worktree path --branch issue-{issue_number}"
# daemon 在 Agent 运行前为 worktree 配置独立 PostgreSQL/MySQL 数据库；
# 需要 worktree 的 .env.local 包含 DATABASE_URL。
provision_database = true

# Runner 行为配置：每轮处理 Issue 数量、默认 agent、提交前验证命令
[agent_runner.runner]
# 每次轮询每个仓库最多处理多少个 Issue
max_issues = 1
# 单轮内并行处理的 Issue 数量：1 为串行（默认）；>1 时同一轮并行跑多个 Issue。
# 仅 `kc daemon --concurrency` 未指定时作为默认值。
max_concurrent_issues = 1
# 默认使用的 AI agent：auto / claude / codex / kimi
default_agent = "auto"
# Agent 失败后的最大重试次数
max_recovery_attempts = 5
# 每次重试前等待的秒数
recovery_retry_delay_seconds = 30
# 跨 agent fallback 链：主 agent 失败后依次尝试本机可用 agent。
# 某 agent 反复修不好或供应商受限时切到下一个；命令不存在则自动跳过。
# 设为空列表可关闭跨 agent 切换，回退到单 agent 行为。
agent_fallback_order = ["qoder", "codex", "claude"]
# 最多切换 agent 的次数（order=[a,b,c] 且 max_agent_switches=2 时最多尝试 3 个 agent）
max_agent_switches = 2
# 瞬时网络错误（socket 断开 / 5xx / 超时）的就地重试次数与退避秒数
transient_retry_attempts = 2
transient_retry_delay_seconds = 10
# 单次 agent 执行的 wall-clock 超时（秒）；超时会 kill 子进程并进入 recovery
timeout_seconds = 14400
# 是否启用 Fix Agent 层；false 时 staged 验证失败直接升级完整 Recovery Agent
fix_agent_enabled = true
# Fix Agent 阶段的 wall-clock 超时（秒）；未设置时沿用 timeout_seconds
fix_timeout_seconds = 1800
# 完整 Recovery Agent 阶段的 wall-clock 超时（秒）；未设置时沿用 timeout_seconds
recovery_timeout_seconds = 7200
# 是否启用交付收尾层（Closeout Agent）；false 时四类收尾失败直接整轮重跑
closeout_agent_enabled = true
# 文本类收尾（补勾选 / 补 Change Log / 修证据清单字段）的 wall-clock 超时（秒）
closeout_timeout_seconds = 600
# 视觉证据补采的 wall-clock 超时（秒）：要真启动应用截图/录屏，不与文本类共用
closeout_visual_timeout_seconds = 1800
# 无输出超时（秒）：agent 子进程在指定时间内没有 stdout/stderr 输出时被 kill
inactivity_timeout_seconds = 1200
# 提交前自动运行的验证命令；任一命令失败会进入 recovery
verification_commands = [
    "git diff --check",
]
# git add 后、git commit 前额外运行的 pre-commit 命令；失败转 Fix Agent
# 仅当仓库声明 pre-commit 依赖且存在 .pre-commit-config.yaml 时 kc init 才会写入
pre_commit_verification_command = "uv run pre-commit run --all-files"

### Agent 执行超时

`[agent_runner.runner]` 提供四类超时，防止 agent 子进程永久挂起：

| 配置项 | 默认值 | 作用 |
|---|---|---|
| `timeout_seconds` | `14400`（4 小时） | 单次 agent 执行的 wall-clock 上限。超过后 runner 会 kill 子进程，并将本次尝试记录为可恢复的 `AGENT_ERROR`，随后进入 recovery 流程。 |
| `fix_timeout_seconds` | `None`（沿用 `timeout_seconds`） | Fix Agent 阶段的 wall-clock 上限。用于修复提交前验证失败等局部问题，通常可以给一个比完整实现更短的预算。 |
| `recovery_timeout_seconds` | `None`（沿用 `timeout_seconds`） | 完整 Recovery Agent 阶段的 wall-clock 上限。Recovery Agent 需要基于失败摘要做全局重规划，可单独配置。 |
| `closeout_timeout_seconds` | `600`（10 分钟） | 文本类交付收尾（补勾选、补 Change Log、修证据清单字段）的 wall-clock 上限。留空时依次回退到 `fix_timeout_seconds`、再到 `timeout_seconds`。 |
| `closeout_visual_timeout_seconds` | `1800`（30 分钟） | 视觉证据补采的 wall-clock 上限。补一张截图要真把应用跑起来，因此与文本类收尾分开配置；留空时的回退链同上。 |
| `inactivity_timeout_seconds` | `1200`（20 分钟） | 无输出上限。只要 agent 子进程持续产生 stdout/stderr 数据，时钟就会重置；如果超过 20 分钟没有任何输出，runner 认为进程已卡死并 kill。 |

六类超时独立生效，满足任意一个都会终止子进程。`timeout_seconds` 是首次实现与默认 fallback 的基准；`fix_timeout_seconds` 与 `recovery_timeout_seconds` 分别覆盖 Fix Agent 与完整 Recovery Agent，`closeout_timeout_seconds` / `closeout_visual_timeout_seconds` 覆盖交付收尾层，未设置时自动回退到 `timeout_seconds`。

如果某个任务确实需要更长时间，可以在目标仓库的 `.kedacode.toml` 或全局 `config.toml` 中调大对应值；如果某类任务经常静默运行（例如大型编译），可适当提高 `inactivity_timeout_seconds`。

超时后的日志示例：

```text
Claude stream (Issue #19: ...) timed out after 14400s; terminating: claude ...
Claude stream (Issue #19: ...) inactive for 1200s; terminating: claude ...
Agent command failed for Issue #19; asking agent to recover (1/5).
```

### Fix Agent 与 Recovery Agent

在把 agent 修改提交进仓库前，runner 会先运行 `verification_commands`。为了让 agent 从一开始就清楚交付门禁，**首次实现 prompt、Fix Agent prompt、Recovery Agent prompt 都会包含 verification 上下文**：

- 首次实现 prompt 会列出完整的 `verification_commands` 列表，让 agent 在写代码时就知道最终要通过哪些检查。
- Fix Agent 和 Recovery Agent 会附带具体的失败命令、exit code、stdout/stderr，而不是只给一句抽象描述。

当验证失败时，runner 不会立即进入完整的 Recovery Agent，而是先尝试一次更轻量的 **Fix Agent**：

1. **Fix Agent 层**
   - Fix Agent 的 prompt 包含当前 verification 失败信息以及完整的 `verification_commands` 列表，并明确要求：只修导致失败的代码/测试；不要改 evidence、PRD Acceptance Checklist、commit request；不要切换分支或 push。
   - Fix Agent 使用 `fix_timeout_seconds` 作为超时预算，未配置时回退到 `timeout_seconds`。
   - Fix Agent 成功后，runner 会再次验证并尝试通过 commit proxy 提交；失败则进入完整的 Recovery Agent。
   - 通过 `fix_agent_enabled = false` 可以整体关闭这一层，staged 验证失败会直接升级到完整 Recovery Agent，避免在明显不适合局部修复的仓库里多消耗一次 agent 调用。

2. **完整 Recovery Agent**
   - 只有 Fix Agent 失败后，runner 才会启动完整的 Recovery Agent，基于更完整的上下文重规划实现。
   - Recovery Agent 的 prompt 包含格式化后的 failure summary，以及原始 verification 失败输出（命令、exit code、stdout/stderr），避免重复踩同样的坑。
   - Recovery Agent 使用 `recovery_timeout_seconds` 作为超时预算，未配置时回退到 `timeout_seconds`。
   - 如果该 agent 在 `agent_spec` 里声明了 `supports_resume`，这一轮**优先续传原会话**（`--resume <session_id>`，session id 来自 worktree 局部记录）而不是把失败摘要塞进一个全新 prompt；没有会话记录或未声明能力时静默降级为全新会话。详见「崩溃对账与 Agent 会话续传」。

这一分层修复的目的是把大量常见的 lint/类型错误（如 agent 遗漏 import、简单单测失败）用更短的超时和更聚焦的 prompt 解决，避免动辄调用一次完整的 recovery agent。

默认 Fix/Recovery prompt 先要求定位失败命令与受影响范围，以证据区分环境问题、既有失败和本次回归；先跑最小相关检查修复根因，再执行现有最终门禁。分类不会自动豁免失败或修改测试缓存策略。默认 pre-PR review 要求完整收集 findings 后集中修复；共享 review/supervisor 修复 prompt 要求逐项说明修复或有证据的异议。后续审查聚焦修复与受影响边界，仍核对最终代码树所需的验证与独立证据。自定义 `pre_pr_review.review_prompt_template` 会覆盖默认审查规则，维护方需同步这些约定。

Fix Agent 的每次启动、修复成功、修复失败以及被配置关闭跳过都会写入 runner 日志（`Starting Fix Agent` / `Fix Agent repaired` / `Fix Agent failed` / `Fix Agent disabled`），可以据此统计这一层的实际触发率与成功率，评估是否值得为仓库保留或关闭。

### 交付收尾层（Closeout Agent）

Fix Agent 只挂在「提交前验证失败」这一个入口。代码写完、验证全过之后还有一道**交付门禁**：PRD 验收清单要全勾、PRD 改动要有 Change Log、Realistic Validation 证据要齐备。这道门禁失败时，原先只有一种处理方式——整轮作废、重新调用完整实现 Agent。但其中很大一部分失败其实是「代码是对的，只差交付收尾动作」，为此重跑一次完整实现代价过高。

**Closeout Agent 是修复阶梯的第三层**，只接住这四类失败：

| 收尾类失败 | 触发点 |
|---|---|
| 验收清单有未勾项 | `_validate_prd_checklist` |
| PRD 改了但 Change Log 缺失 / 为零条 / 未追加 / 字段不全 | `_validate_prd_change_log` |
| 结构化证据清单（`evidence.json`）字段格式非法 | `validate_evidence_manifest` 的清单解析层 |
| 前端有改动但证据目录缺截图 / 录屏 | `ensure_frontend_visual_evidence` |

**明确不接这三类**，它们保持整轮重跑：

- RV 命令被 keda 复跑后未通过或超时（`ensure_validation_commands_pass`）
- 证据目录为空、证据与清单覆盖不匹配、证据文件交叉污染、缺 negative control、产物健全性不达标
- RV 辅助脚本放错位置（`ensure_no_misplaced_evidence_helpers`）

理由是这三类表达的是「行为没做对」或「验证没真跑过」，让一个轻量 pass 去消除这类信号等于自拆验证体系。

分流依据**不是匹配错误文案**，而是在抛出门禁错误的那行代码上显式声明分类（`DeliveryGateFailureKind`）。门禁文案是会随 prompt 调优被改写的英文散文，用正则分流必然漂移，且漂移方向恰恰最危险。未显式声明的抛出点默认按真失败处理，因此新增门禁时漏标的后果是「少省一点时间」，不是「门禁被绕过」。

一次收尾的完整流程：

1. **快照** —— 记录 worktree 改动路径的内容摘要、canonical PRD 全文、证据目录文件清单。
2. **启动收尾 agent** —— 复用当前轮次已选定的 agent（不引入「收尾用哪个 agent」这个新配置维度），prompt 只含该门禁失败与收尾约束，超时按类别取 `closeout_timeout_seconds` 或 `closeout_visual_timeout_seconds`。
3. **越界检查** —— 再取一次快照，比对内容摘要。收尾 pass 只被允许改 canonical PRD（含其归档目标路径）与证据目录；出现任何其他被改动的路径即判本次收尾失败。这条**不靠 prompt 约束**，靠 runner 自己比对强制。
4. **完整门禁链重跑** —— PRD 交付 + 证据齐备 + RV 脚本位置 + RV 命令复跑，全过才算收尾成功。

5. **留痕** —— runner 比对收尾前后的 PRD 文本与证据清单，算出「本轮勾了哪几项 / 追加了哪条 Change Log / 新增了哪些证据文件」，以 `delivery_closeout` + `recovered=true` 记一条 attempt，同步到 Issue 的尝试历史评论。**这份记录不采信 agent 自述**——agent 可以在总结里说谎，前后文本差异不会。

runner 在 commit proxy 固定实现提交后复跑 RV，再启动要求工作树干净的独立 verifier。pre-PR review 若提交补丁，runner 在创建 Draft PR 前对新的 HEAD 重新执行 RV 和独立 verifier；复用已有本地提交的发布路径也执行这道最终复核。最终复核失败时不创建 PR，也不沿用旧提交的 verifier 结论。

任一环节失败即升级为整轮重跑，与本层落地前的行为一致；同时 runner 会把收尾 pass 对 PRD 的编辑**原样撤销**。验收清单是这几道门禁里唯一没有独立验证源的一道（门禁只问「还有没有未勾项」），失败的收尾若把一个举不出证据的勾留在 worktree 里，随后的完整重跑会把它当成既成事实收下，「举证后才可勾选」就成了空话。越界写入的其他文件不撤销，按设计交给完整重跑重新处理。

收尾 pass 的 prompt 要求：勾选任何验收条目前必须指认出它依据的证据文件或命令输出；举不出依据的条目保持未勾并说明原因；不得修改源码、测试与 RV 命令；不得为了让条目通过而弱化、删除或改写条目文本。

日志关键字：`Starting Closeout Agent` / `Closeout Agent repaired` / `Closeout Agent failed` / `Closeout Agent disabled` / `Closeout Agent modified out-of-scope files` / `Delivery gates still fail after closeout`。收尾耗时进入 attempt 的 phase 分解（`closeout`）。

通过 `closeout_agent_enabled = false` 可以整层关掉，四类收尾失败回到整轮重跑。这个键走的是逐仓库配置路径，在目标仓库自己的 `.kedacode.toml` 里改完下一轮轮询即生效。

### WIP checkpoint 不合并

在实现阶段或 recovery 阶段，runner 可能调用 `checkpoint_uncommitted_progress` 把当前未提交进度保存为 `[Agent][WIP] Issue #N checkpoint` 形式的临时 commit，作为崩溃恢复点和跨 claim 续作的基础。这些 WIP checkpoint 不会被单独推送到远程分支上——它们只在本地分支存在，最终随功能 commit 一起被 push。

runner **不在 publish 前对 WIP checkpoint 做 squash**：

- 正常成功流程中，最终功能 commit 已经位于 WIP checkpoint 之上，`git reset --soft` 式的压平无法触发或会引入历史重写风险。
- WIP checkpoint 的历史噪音在最终合并 PR 时，由 GitHub/GitLab 的 squash merge 收敛为一条干净历史。
- checkpoint 的跨 claim 续作价值被完整保留。

checkpoint 的 `git add` 只使用 git 仍能匹配的路径（非禁改、且在工作区或 index 中存在）。这一点对**归档之后才失败**的尝试尤其关键：PRD 交付门禁（`ensure_prd_delivery_ready`）会用 `git mv` 把 PRD 从 `tasks/pending/` 移到 `tasks/archive/`，此后 `git status` 仍报告 `tasks/pending/` 源路径，但它在工作区和 index 里都已不存在——把它塞进 pathspec 会让整条 `git add` 以 `fatal: pathspec ... did not match any files`（exit 128）失败，连 agent 真正的在途代码一起丢掉，而 checkpoint 失败只记 warning，很容易被忽略。已 staged 的重命名与删除本身仍在 index 中，`git commit` 会照常带上，因此剔除这些 pathspec 不会丢内容。

### 跨 claim 失败交接与失败 Draft PR

recovery 预算耗尽不是终点，而是**交班点**。此前只有同一个 claim 内的重试带着失败上下文（`run_agent_execution_loop` 每轮把 `recovery_failure_summary` 拼进 recovery prompt）；跨 claim 的唯一入口 `build_progress_continuation_prompt` 既不读 Issue 评论、也不读 attempt 历史与 agent 自述，而 `_reuse_existing_local_commit` 又**不跑 verifier gate**——于是"上一轮 verifier 为什么判红"没有任何路径跨过 claim 边界，接手的新 claim 只能从 WIP 快照与 PRD 反推，同一个坑反复踩。

现在耗尽路径补上三步（全部在 `agent_runner_issue_handlers._process_ready_issue` 的耗尽 `except` 分支里）：

1. **写交接记录**：一条带 `<!-- iar:failure-context checkpoint=… attempts=… verifier=… evidence=… -->` marker 的 Issue 评论，正文由 `format_failure_context_comment` 渲染，复用既有的 `format_attempt_history`，verifier 结论取自 `MaxRetriesExceededError.attempt_results[*].detail`。
2. **发布同源的人读表面**：`checkpoint_sha` 非空时调用既有的 `publish_changes(..., require_prd_archived=False)`，发布或按分支复用同一个 Draft PR，并在 PR 正文尾部固定附一段指向该交接评论的回链（`<!-- iar:failure-context-ref -->`，重复耗尽时替换而非叠加）。
3. **回灌下一轮**：新 claim 构造 continuation prompt 时按 latest-wins 读回**最近一条**交接记录（`find_latest_failure_context_comment`），经 `truncate_failure_context_for_prompt` 限量截断后注入。

必须知道的边界：

- **触发条件只有 `MaxRetriesExceededError`**。`ProviderCapacityError`（限流/容量）不代表本轮工作未通过，`KeyboardInterrupt` 是用户主动中断且没有 `attempt_results`，二者保持既有行为：不写交接记录、不发布 PR。
- **没有安全 commit 时不发布任何 PR**（干净工作树 / 分支不符 / 改动全为禁改路径 → `checkpoint_sha is None`），但交接记录仍然写出——"无 commit 也要交班"是刻意覆盖的一格。
- **交接评论是唯一事实源**。Draft PR 正文只是"发布那一刻的快照"：`create_draft_pr` 命中已开 PR 时直接返回、不改写正文，所以正文可能落后。回灌和人的回溯都以交接评论为准。
- **`require_prd_archived=False` 是本功能唯一放宽的安全检查**，只在耗尽路径显式传入，默认值仍是 `True`；forbidden paths、evidence 泄漏、remote 与 branch 校验全部照旧。`tests/test_agent_runner_publish.py` 用白名单钉住允许点，新增泄漏会让测试失败。
- **不新增标签、状态枚举、失败判定器或门禁**。失败 Draft PR 拿不到 `validation/verifier-passed`，签核、合并与归档由**既有**门禁拒绝——包括有人手动把 Draft 标志取消之后仍然如此。
- **失败性质不进机器状态**：产品失败还是审核事故，只由 Agent 在正文里陈述，机器不核对。缓解是正文回链原始诊断（证据目录、verifier response log）供人自行对照，且无论正文怎么写都改不了标签与合并态。
- **交接记录可能把下一轮带偏**：回灌的是上一轮的结论，若它本身就错，错误会被继承。因此 prompt 明确标注这是"上一轮的陈述而非裁定"，且只取最近一条。
- **快照是 WIP 中途进度**，可能连编译都不过，不是"实现做完了但没过验收"。交接记录与 PR 正文都显式写明这一点。
- agent fallback 阶梯会为每个候选 agent 各耗尽一次，因此一次 claim 可能留下多条记录；每条对应一次真实快照，下一轮按 latest-wins 只读最近一条，PR 仍按分支复用同一个。

人工验证方式（runner 不产出这类证据，需人实际执行）：把一个 PRD 打到 recovery 耗尽，然后在 Issue 上找 `iar:failure-context` 评论、在 PR 列表找该分支的 Draft PR，确认 `gh pr view <N> --json labels,mergeable` 里没有 `validation/verifier-passed` 且签核/合并被拒、PRD 仍在 `tasks/pending/`；再重新 claim 同一个 Issue，确认续作 prompt 里出现了上一轮的结论。

### 非 claude agent 的实时输出（PTY）

`claude` 用 `--output-format stream-json` 显式吐增量事件，所以一直能实时看到进度。`kimi` / `codex` 没有这种流式协议，而且很多 CLI 在发现 stdout 是管道（非终端）时会把输出从行缓冲切成**块缓冲**——结果就是运行中只看到几个点、最后才一次性打印，期间只有 watchdog 的 `still running after Ns` 心跳。

为此 runner 在跑**非 claude** agent 时改用**伪终端（PTY）**：让子进程以为 stdout 是终端，从而恢复行缓冲、实时吐出进度。stdout/stderr 合并到同一 PTY（顺序自然、无双管道死锁），随后接入与 claude 相同的输出通道——并行时进入每个 Issue 各自的面板与日志文件（见下文「并行处理 Issue」）。`still running after Ns` 仍是正常的存活心跳，不是报错。

> PTY 只能让**会输出但被缓冲**的 agent 实时可见；如果某 agent 本就几乎不打印进度，PTY 也变不出内容。

# 发布前安全边界：自动合并开关、禁止提交的路径模式
[agent_runner.safety]
# 是否允许自动合并 PR（强烈建议保持 false）；
# 与 agent_runner.autopilot.enabled 同时为 true 才会真正生效（合并队列）。
auto_merge = false
# 提交前禁止变更的路径通配模式
forbidden_path_patterns = [
    ".env",
    ".env.*",
    "secrets/*",
    "docker-compose.prod.yml",
]

# Autopilot 快速档（合并队列）
# ---------------------------------------------------------------------------
# 启用后，supervisor approve 的 PR 不再停在"等人合并"，而是被一条串行
# 合并队列消费：verifier 门禁 → 自动签核 → rebase → 全量验证 → 禁改终扫
# → 等 checks 全绿 → squash 合并。
#
# 两个开关同时为真才生效（"双同意"），保证历史上 `safety.auto_merge = true`
# 不会单独触发自动合并：新仓必须显式在本段开启 ``enabled = true``。
#
# 迁移提示：本段首次落地后，任何已设为 ``safety.auto_merge = true`` 的环境
# 若不同时把下面 ``enabled`` 设为 ``true``，仍然回到"等人工合并"的现状——
# 自动签核 / 自动合并都不会发生。但 ``enabled`` 本段是全新键，既往配置不会
# 意外激活快速档。
[agent_runner.autopilot]
# 主开关。false（默认）时合并队列整段 no-op，严格档行为零变化。
enabled = false
# 仅支持 "squash"。非法值在配置加载期直接拒绝。
merge_method = "squash"
# 当 Issue 需要 validation 时，是否要求 validation/verifier-passed 标签先存在；
# 缺失则本轮跳过，留给 verifier / repair 流程。
require_verifier_pass = true
# verifier 绿灯后自动替人工勾选 PR body 的 Realistic Validation sign-off 清单
# （与 marker 评论均幂等）。
auto_sign_off = true
# 等 PR checks 全绿的最大秒数；超时则放弃本轮合并（不阻塞后续 PR）。
merge_check_timeout_seconds = 1800

# Realistic Validation 证据门禁配置
[agent_runner.validation]
# 是否启用 Realistic Validation 证据门禁
enabled = true
# worktree 内证据目录根；默认 tasks/evidence 时按任务分子目录
# （tasks/evidence/<prd-stem>/，无 PRD 的 Issue 用 issue-<N> 兜底），
# .md 文本报告经 .gitignore 白名单进版本库，原始产物被排除。
# 显式配置为其它值（如 legacy 的 .iar/evidence）则保持整目录排除的旧行为。
evidence_dir = "tasks/evidence"
# orphan 证据分支前缀
branch_prefix = "iar-evidence/"
# 是否逐项检查证据文件格式
evidence_format_check = true
# 是否用 agent 解析 PRD 中的格式要求
parse_evidence_format_with_agent = true

# 实现 Agent 的 prompt 模板；默认 phase 与自定义阶段模板
[agent_runner.prompts]
# 默认使用的 prompt 阶段
default_phase = "execution"

[agent_runner.prompts.phases]

# Draft PR 创建前的 AI review 门禁（push 之后、PR 之前）
[agent_runner.pre_pr_review]
# 是否启用 Draft PR 创建前的 AI review
enabled = true
# 执行 review 的 agent：auto / claude / codex / kimi
review_agent = "auto"
# 谁修复 review 报出的问题：self（审核者自己修，默认，与历史行为一致）/
# executor（交回本次实现者）/ 任意已注册 agent 名。注意与
# [agent_runner.runner].fix_agent_enabled 区分：后者是本地验证失败时的轻量修复 agent，
# 与本键无关。
repair_agent = "self"
# 是否允许实现 agent 与 reviewer 为同一个；为 false 时从 agent 注册表里取第一个
# 不等于实现者的 agent（不再硬编码 codex）
allow_same_agent = true
# review 不通过时的最大修复轮数（默认 2，最后一轮允许 reviewer 提供最终修复 commit request）
max_attempts = 2
# review agent 最长运行秒数
timeout_seconds = 1800
# reviewer 报出 findings 但未写 commit request 时，同一轮内追加提醒的最大次数（默认 1）
commit_request_reminder_attempts = 1
# 自定义 review 提示词；空列表走代码默认模板，默认模板会调用 code-reviewer skill 并要求输出 findings JSON 数组
review_prompt_template = []

# Draft PR 创建后的自动 supervisor 配置
[agent_runner.post_pr_supervisor]
# 是否启用 post-PR supervisor
enabled = true
# 执行 supervisor 的 agent
supervisor_agent = "auto"
# 谁执行 supervisor 判定后的代码修复：self（supervisor 自己修，默认）/
# executor（交回本次实现者，拿不到本次实现者时按 Issue 标签回落并在日志中注明来源）/
# 任意已注册 agent 名
repair_agent = "self"
# supervisor 要求修复时的最大修复 / rebase 次数
max_repair_attempts = 2
# supervisor agent 进程崩溃（API / 网络等基础设施错误）时同一 cycle 内的最大重试次数
max_agent_crash_retries = 5
# 崩溃重试的初始退避秒数，之后每次重试翻倍
crash_retry_initial_backoff_seconds = 30
# 崩溃重试单次退避等待的最大秒数
crash_retry_max_backoff_seconds = 600
# 命中这些路径前缀的文件 diff 全量进入 supervisor prompt，其余按预算截断
key_paths = []
# 非关键路径文件 diff 的字符预算
max_diff_chars = 6000
# 是否把历轮未解决 findings 注入下个 cycle 的 supervisor prompt
previous_findings_injection_enabled = true
# 跨 cycle finding artifact 落盘目录（worktree 相对路径，已被 .iar/ gitignore 排除）
findings_artifact_dir = ".iar/state"

# 交互式决策（kc ask）配置
[agent_runner.interactive_decision]
# 是否启用 kc ask
enabled = true
# 默认 planner agent
default_agent = "claude"
# 决策日志输出目录
default_output_dir = "logs/agent-runner/decisions"
# 规划 agent 超时秒数
planner_timeout_seconds = 120
# 输入上下文最大字符数
max_context_chars = 24000
# 是否允许 kc ask --yes 跳过确认
allow_execute_yes = true

# 多 agent 审议（kc deliberate）配置
[agent_runner.deliberation]
# 默认审议轮数
default_rounds = 2
# 默认汇总 agent
default_synthesizer = "claude"
# 审议会话输出目录
default_output_dir = "logs/agent-runner/deliberations"

# 审议角色：架构师
[agent_runner.deliberation.profiles.architect]
agent = "claude"
role = "architect"
behavior_prompt = "You are an experienced software architect. Analyze the requirement from a system design perspective. Focus on modularity, scalability, and maintainability."

# 审议角色：质疑者
[agent_runner.deliberation.profiles.skeptic]
agent = "kimi"
role = "skeptic"
behavior_prompt = "You are a skeptical reviewer. Challenge assumptions, identify risks, and point out edge cases. Ask hard questions that others might miss."

# 审议角色：实现者
[agent_runner.deliberation.profiles.implementer]
agent = "codex"
role = "implementer"
behavior_prompt = "You are a pragmatic implementer. Focus on feasibility, concrete steps, and implementation details. Highlight what can be built and what resources are needed."

```

仓库本地 `.kedacode.toml` 可覆盖 `labels`、`git`、`worktree`、`runner`、`memory`、`safety`、`autopilot`、`validation`、`prompts`、`pre_pr_review`、`post_pr_supervisor`、`daemon`、`generated_content`、`interactive_decision`、`repl` 和 `deliberation`。`config.toml` 继续保存全局默认值、环境级设置和 legacy registry，不应保存 token、API key 或账号凭据。

#### `generated_content` 不由 `kc init` 写入

脚手架只在文末留一段注释示例，不写入 `[agent_runner.generated_content]` 任何键。写入等于把当时的
代码默认值钉死在每个仓库里，之后默认值升级（例如 `mode` 从 `template` 改为 `agent`）就传不到
已初始化的仓库。需要偏离默认值时取消注释，**只写要改的键**，其余继续继承（合并规则见
下文「生成模式与回退」一节）。旧版脚手架已经写进去的钉子用 `kc config migrate` 清理，见下一小节。

#### 迁移旧脚手架钉死的 `generated_content`（`kc config migrate`）

早期的 `kc init` 会把整段 `generated_content`（含 `mode = "template"`、`output = "json"`）逐项
写进 `.kedacode.toml`，这些仓库因此一直停在 template 模式，跟不上新的默认值。`kc config migrate`
清掉这些旧钉子，让仓库重新继承当前默认值：

```bash
# 先预览：列出将清掉的键与完整 diff，不写文件
kc config migrate --dry-run --repo /path/to/repo

# 确认后执行（省略 --repo 则处理当前 Git 仓库）
kc config migrate --repo /path/to/repo
```

规则偏保守，宁可漏清也不误清：

- **只清值与旧脚手架写入过的值完全相等的键。** 值不同说明仓库主动改过，原样保留，也不出现在报告里。
  旧脚手架写过的值：`mode` 为 `template` 或 `agent`，`timeout_seconds` 为 `60` 或 `120`，
  `output` 为 `json`，`enabled` / `include_commit_log` / `include_diff_stat` 为 `true`，
  `fallback` 为 `template`，`max_input_chars` 为 `20000`，`agent` / `default_agent` 为 `auto`，
  `title_template` / `body_template` / `prompt` 为空串。
- **`mode = "template"` 且配置了自定义 `title_template` / `body_template`**：视为有意使用模板渲染，
  保留并在报告里说明原因。
- **`output` 且配置了自定义 `prompt`**：输出格式必须与提示词要求的回复格式一致，保留
  （除非它恰好等于该 target 现在的默认输出格式，那样清掉不改变任何行为）。
- **`enabled = false`**：最早一版脚手架写的就是它，但从值上分不清是遗留还是有意关闭；清掉会让仓库
  开始调 AI 生成正文，所以只在报告里列出、不清。想跟随当前默认（开启）就手动删掉这一行。
- **逐行编辑，不重新序列化**：注释、排版、其余配置逐字节保留；被清掉的键连同紧贴其上的注释一起
  删除，清空的表头随之删除。
- **写回前用 `tomllib` 重新解析校验**：结果必须恰好等于"原配置去掉被清的键"，否则拒绝写入；
  写入走同目录临时文件加原子替换。
- **可重复执行**：没有可清的键时什么都不做。
- **点分键、内联表、带引号表名写法的钉子**：定位不到"表头下的独立 `key = value` 语句"，只在报告里
  列出并提示手动删除，不编辑。

迁移后被清掉的键回到 `config.toml` / 代码默认值。默认 `mode` 是 `agent`，所以这些仓库的
`kc issue create`（含 `--from-prompt`）、开 Draft PR、rework-prd 会**先调一次 agent**（失败或超时再回退到模板）；
`default_agent` 与各 target 的 `agent` 也改为继承机器级配置。常驻的 `kc daemon` 需重启才会载入。

`--repo-id` 不支持（迁移针对单个仓库的 `.kedacode.toml`）。批量预览可以用 shell 循环：

```bash
for repo in ~/code/*/; do [ -f "$repo/.kedacode.toml" ] && kc config migrate --dry-run --repo "$repo"; done
```

单仓库命令的目标解析规则：

| 命令形态 | 目标解析 |
|---|---|
| `kc run` / `kc labels sync` / `kc review` / `kc issue create ...` | 当前 Git 仓库，合并当前仓库 `.kedacode.toml` |
| `kc run --repo /path/to/repo` | 指定 Git 仓库，合并 `/path/to/repo/.kedacode.toml` |
| `kc --repo /path/to/repo run` | 等价的顶层 selector 写法，适合把目标仓库放在命令前 |
| `kc run --repo-id keda` | 从 legacy registry 找到路径，再合并目标仓库 `.kedacode.toml` |
| `kc run --all` | 显式处理 `config.toml` 中所有 enabled registry entries |
| `kc daemon` / `kc review-daemon` | 当前已初始化注册仓库；未命中、未初始化或匹配多个时报错 |
| `kc daemon --repo-id keda` | 仅处理指定仓库 |
| `kc daemon --all` | 显式处理 `config.toml` 中所有 enabled registry entries |

历史命令 `kc run-once`、`kc review-once`、`kc issue-from-prd` 和 `kc recover-publish` 已被删除；请改用 `kc run` / `kc review` / `kc issue create` / `kc recover`。

## Workflow Templates（`kc workflow install`）

`kc workflow install <name>` 把 KedaCode 内嵌的 workflow 模板复制到当前 Git 仓库，并写入最小的 `[preview]` 占位段。当前 v1 只支持 `preview` 工作流，会复制下列 7 个文件（相对仓库根）：

- `.github/workflows/deploy-preview.yml`
- `deploy/vps-traefik/README.md`
- `deploy/vps-traefik/deploy-preview.sh`（保持 `0755` 权限）
- `deploy/vps-traefik/docker-compose.preview.yml`
- `deploy/vps-traefik/preview.env.example`
- `scripts/preview_env.py`
- `scripts/provision_preview_server.py`

典型用法：

```bash
# 在目标仓库里（必须先 `kc init`）：
uv run kc workflow install preview

# 只看将要写哪些路径与字节数，不实际落盘：
uv run kc workflow install preview --dry-run

# 已存在同名文件会被拒绝（非零退出），需要重建时显式加 --force。
uv run kc workflow install preview --force
```

行为约定：

- 目标文件已存在 → 默认拒绝并以非零退出；`--force` 同时覆盖 7 个模板文件和 `config.toml [preview]` 段
- `--dry-run` 全程不写盘，只打印 `would write` / `would overwrite` 清单
- `config.toml` 末尾追加最小 `[preview]` 段；已存在时默认跳过，`--force` 用占位段整体替换
- 占位段字段名直接派生自 `backend.infrastructure.config.settings.PreviewSettings.model_fields`，避免硬编码字段清单；字段值统一为 `<set-me>`（`enabled` 保留 schema 默认值以保证 pydantic-settings 可解析）
- 缺 `.kedacode.toml` 时拒绝并提示 `kc init`
- 接收 `--repo` / `--repo-id` / `--config` 时拒绝并不落盘（与 `kc init` 行为一致）

模板维护说明：模板文件随 KedaCode Python 包一起发布，路径在 `src/backend/engines/agent_runner/templates/<name>/`，**直接在那里修改**。

早先 keda 自己也跑预览部署，仓库根下因此存在一份同名副本，`just check-template-drift` 逐字节比对两边。keda 的预览部署已下线、根下副本随之删除，模板成为唯一副本，那组比对也就没有了比对对象。改由以下机制保证质量：

- `tests/test_deploy_preview_workflow.py` / `test_deploy_preview_script.py` / `test_preview_env_script.py` / `test_provision_preview_server.py` 直接针对模板路径验证，不再经由根下副本
- `test_every_expected_file_is_packaged` 经 `importlib.resources` 断言每个模板文件确实被打进了包
- `just check-template-drift` 保留字段表检查：`templates/preview/deploy/vps-traefik/README.md` 的字段表必须与 `PreviewSettings.model_fields` 对齐

注意模板脚本**不要在原地执行**：`__pycache__` 会落进模板目录，而安装时逐个以 UTF-8 读取模板文件，撞上字节码即 `UnicodeDecodeError`。测试里已通过 `sys.dont_write_bytecode` 规避。

## 子进程环境净化（child env sanitize）

凡经 runner **执行层**派发的子进程都不再原样继承父环境：`SubprocessRunner.run()` 的默认档（agent 内容生成的 plain 路径、git/gh/pytest 等工具命令、验证命令）与 agent 流式派发点（Claude 流式路径、PTY 路径、`plain` / `pi-json-lines` 协议）统一使用 `backend.infrastructure.child_env.build_sanitized_child_env()` 组装子进程环境，按固定名单 `AGENT_CHILD_ENV_DENYLIST` 剔除会话私有变量，其余变量（PATH、HOME、代理、API key 等）原样透传；`env=None` 不再等于「全量继承 `os.environ`」。console 托管 runner 子进程（`process_supervisor.spawn`）与 `iar container up` 传给 docker compose 的环境（`container_ops._build_compose_env`）同样以净化档为基底，会话私有变量不会沿进程谱系下传。

- 名单（8 个，硬编码于 `src/backend/infrastructure/child_env.py`）：`SERVER__PORT` 与 `CODEBUDDY_SERVICE_PROXY_URL`、`CODEBUDDY_SESSION_ID`、`CODEBUDDY_CONVERSATION_REQUEST_ID`、`CODEBUDDY_ROOT_REQUEST_ID`、`CODEBUDDY_CONVERSATION_MESSAGE_ID`、`CODEBUDDY_PROJECT_DIR`、`CODEBUDDY_CURRENT_MODEL_ID`
- 为什么剔除：交互式 CodeBuddy 会话会向 shell 注入 `SERVER__PORT`（会话 daemon 的监听端口）。headless 子进程继承后尝试绑定同一端口，触发 `EADDRINUSE` 并在首个模型请求前永久卡死（stdout 零输出，最终被 inactivity/timeout watchdog 杀掉，见 2026-09-28 Issue #156 事故）
- 为什么默认档也要净化：2026-10-07 Issue #229 事故确认，内容生成路径（`kc issue create --from-prompt` 派发的 `codebuddy -p ...`）走 `SubprocessRunner.run()` 且不传环境，当时的默认「全量继承」让 `SERVER__PORT` 原样透传，Issue 正文退化为模板渲染（修复：Issue #230）。环境构造收敛到 `run()` 一处后，**新增 denylist 变量无需改动任何执行层调用点**
- 已知例外（不经执行层的直接调用）：`core/use_cases/agent_runner_prd_activity.py` 的 PRD 活动锁命令（`git worktree list`、`scripts/shared/just/prd_lock.py`）与 `core/shared/prd_contract_client.py` 的 `prd_contract.py` 调用仍直接 `subprocess.run`，完整继承 `os.environ`。这三处都不监听端口，今日不会复现 `SERVER__PORT` 事故，但「新增名单变量无需改动调用点」只对上面列出的执行层路径成立；把它们接入执行层要改 core 的注入签名（core 不得直接 import infrastructure），动之前按 Issue #230 的口径重新审计
- 剔除按变量名去重告警：某变量在本进程内**首次**被剔除时记录一条 WARNING：`child env sanitized: removed KEY (value length N)`（不含完整值），之后同一变量再被剔除只记 DEBUG——默认档净化覆盖每一次 `SubprocessRunner.run()`，工具命令单轮可达数十次，逐次 WARNING 会把这条排障信号淹掉。若 agent 运行异常且日志出现该记录，优先怀疑名单误剔
- 因此**从交互式 AI 会话的 shell 里直接启动 `kc run` / `kc review` / `kc issue create --from-prompt` 是安全的**，无需手工 `env -u SERVER__PORT`
- 白名单档（浏览器 E2E 验证子进程）的 fail-fast 前提校验不因默认档净化而改变：前提不成立时依旧报错，绝不静默回退到任何继承形态
- 净化约定分两层守护：默认档「`run()` 一处构造、全分支透传」由 `tests/test_process_runner.py` 直接断言；守卫测试 `tests/guards/test_agent_spawn_env_guard.py` 只钉住可以被 `run()` **之外直接调用**的派发点（`run_filtered_claude_stream`、`_run_pty_stream` 与 `output_protocols/` 全目录），新增这类派发点必须自行接入净化环境。Issue #230 之前「工具命令路径不净化」的旧约定已废止

## 浏览器 E2E 验证命令形态（browser_e2e）

UI 类 Issue 的验证不能只看"测试绿了"——需要真实启动应用、真实操作页面、产出可门禁的浏览器产物。为此 `[agent_runner.runner].verification_commands` 在纯 shell 字符串之外支持**结构化 E2E 条目**（PRD：`tasks/archive/P1-FEAT-20260930-225500-browser-e2e-verification.md`，交付后归档于 `tasks/archive/`）。两类条目在同一队列按序执行，任一失败短路进入既有 `VERIFICATION_FAILED` recovery 通道；**纯字符串条目的解析与执行行为逐字段不变**，未配置 E2E 条目的仓库零变化。

### 配置形态

TOML 数组中可与字符串混排 inline table（注意：TOML inline table 不能跨行，整条条目写在一行内）：

```toml
[agent_runner.runner]
verification_commands = [
    "git diff --check",
    { kind = "browser_e2e", script = ".iar/evidence/scripts/admin-login-e2e.mjs", app_start = "just run frontend", ready_url = "http://localhost:5173/sign-in", ready_timeout_seconds = 60, timeout_seconds = 900, env_allow = ["E2E_APP_PORT"], artifacts = [ { path = ".iar/evidence/rv-1-admin-login.png", mime = "image/png", min_size = 50000, key_claim = "登录后的页面标题" }, { path = ".iar/evidence/rv-1-admin-login.trace.zip", mime = "application/zip" } ] },
]
```

| 字段 | 默认 | 语义 |
|---|---|---|
| `kind` | 必填 | 固定 `"browser_e2e"`，形态标记。 |
| `script` | 必填 | E2E 脚本入口（worktree 相对路径）。由脚本自身 shebang 选择解释器，需要执行位；keda 不绑定任何 E2E 框架（Playwright 等是目标仓库自己的 devDependency）。 |
| `app_start` | `None` | 被测应用的启动 shell 命令；后台启动，正常结束或超时时进程树一并回收。 |
| `ready_url` / `ready_timeout_seconds` | `None` / `60` | HTTP 就绪探测地址与等待上限（`curl -fsS`）；探测失败即分类 `app_not_ready` 并附应用日志尾部。 |
| `startup_wait_seconds` | `None` | 无 `ready_url` 时的固定等待（回退形态），默认 10 秒。 |
| `probe` | `None` | 浏览器运行时可用性探测命令，exit 0 视为可用；缺省用内置启发式（Playwright 浏览器缓存目录 / 常见浏览器可执行文件）。 |
| `timeout_seconds` / `inactivity_timeout_seconds` | `900` / `None` | wall-clock 上限（超时击杀整个进程组，不留僵尸）与无输出超时。 |
| `env_allow` | `[]` | **逐名**追加进子进程环境变量白名单的变量名。 |
| `artifacts` | `[]` | 产物声明，脚本 exit 0 后逐个过 FR-11a artifact health 硬层（存在 / 非 0 字节 / mime / min_size / 新鲜度——以本轮验证开始时间为 mtime 下限）。 |

字段级诊断：`extra="forbid"`，拼错/多余字段在配置加载期就报出；`ready_url` / `startup_wait_seconds` 没有 `app_start` 时同样加载期报错。

### 执行管线与失败分类

执行顺序：脚本入口预检 → 浏览器运行时预检 → 同一个受控子进程内（应用后台启动 → 就绪探测/固定等待 → 执行脚本 → EXIT trap 回收应用进程树）→ 声明产物过硬层。失败 stderr 携带 `BROWSER_E2E_FAILURE [category=<分类>]` 标记与修复指引，recovery prompt 与 Issue 评论渲染出子分类行，不会三类失败坍缩成一条裸 `VERIFICATION_FAILED`：

| category | 含义 | 典型修复 |
|---|---|---|
| `script_missing` | 脚本入口不存在或缺执行位 | 补齐脚本 / `chmod +x` |
| `browser_runtime_missing` | 浏览器运行时不可用 | 装浏览器（如 `pnpm exec playwright install chromium`）或自定义 `probe` |
| `app_not_ready` | 应用启动失败或就绪探测超时 | 检查 `app_start` / `ready_url` 与实际端口约定 |
| `script_failed` | 脚本非零退出（页面断言失败） | 这是真实功能失败，读脚本输出 |
| `script_timeout` | wall-clock 超时 | 进程树已被击杀；调大 `timeout_seconds` 或修挂起 |
| `artifact_unhealthy` | 脚本 exit 0 但产物过硬层失败 | 修产物声明或让脚本本轮真实产出 |

### 环境隔离（默认关闭的白名单）

E2E 条目预检与执行的所有子进程经 `backend.infrastructure.child_env.build_e2e_child_env()` 过滤：与 agent 派发的 denylist 净化相反，这是**默认关闭的白名单**——只保留基础运行变量（PATH/HOME/终端/locale/临时目录）、Node/pnpm 运行时位置、浏览器自动化框架配置族（`PLAYWRIGHT_*` / `PUPPETEER_*` 等前缀）与 `env_allow` 逐名追加项。runner 注入的凭据（GitHub token、模型 API key 等）因不在白名单内而对验证脚本不可见；白名单挡掉的凭据类变量名会记 INFO 日志（只记名不记值）。执行层（`IProcessRunner.run` 的 `env_profile`）拒绝无捕获/无超时的白名单档调用，绝不静默回退到全量环境继承。

CI 容器镜像预装浏览器运行时是后续工作；首版只保证本地 runner 机器（D-08）。

## worktree 中的本地 env 文件

`git worktree add` 只会物化被 Git 跟踪的文件，gitignored 的 `.env*`（密钥、本地配置）不会自动出现在新 worktree 里。为此 runner 在 worktree 创建/复用后会自动补齐缺失的 env 文件：

- `kc worktree create` 和 `kc run` 的 create/reuse 流程都会把主仓库目录下的 `.env*` 文件按相对路径复制到 worktree（含子目录，如 `tests/playwright-e2e/.env`）
- 只复制 worktree 中**缺失**的文件：被跟踪的 `.env*.example` 与 worktree 内已修改的 `.env` 永远不会被覆盖
- 复用已有 worktree 时同样补齐（旧 worktree 缺 `.env` 的，下一次 `kc run` 会自动治愈）
- 扫描会跳过 `.git`、`.iar-worktrees`、`.venv`、`node_modules` 等目录，避免把其他 worktree 的 env 文件复制串
- **不会**复制 `.env.run-state`：它记录主仓库 `just run` 分配的端口，拷进 worktree 会让 worktree 指向主仓库端口——目标仓的共享库守卫据此认为有 dev server 在写本库而拒绝跑测试（假阳性），worktree 内 `just run` 也会与主仓库 dev server 抢端口；worktree 会自行生成自己的 run-state（`just worktree` 路径走随机端口）
- 与旧的 `just worktree` 脚本不同，这里**不会**用 `.env.example` 兜底生成 `.env`：用示例值静默跑测试比明确的缺配置失败更危险
- 复制是 best effort：单个文件失败（如悬空 symlink）只记日志，不会中断 agent run
- 复制过来的文件保持 gitignored 状态，不会让 worktree 变脏，也不影响 `kc worktree cleanup` 的默认清理判定

## worktree 中的前端依赖（node_modules）

同理，gitignored 的 `node_modules` 也不会被 `git worktree add` 物化，否则 worktree 里跑 `vite` 等构建会报 `vite: command not found`。runner 在补齐 env 文件之后，会按以下优先级处理 worktree 中的前端依赖：

1. **优先在 worktree 内真实安装**：扫描 worktree（而非主仓库）中所有含 `package.json` 的前端项目，根据锁文件自动选择包管理器并执行安装：
   - `pnpm-lock.yaml` → `pnpm install --ignore-scripts`
   - `package-lock.json` → `npm ci --ignore-scripts`
   - `yarn.lock` → `yarn install --ignore-scripts`
   - `bun.lock` / `bun.lockb` → `bun install --ignore-scripts`
2. **无锁文件或安装失败时回退到软链**：如果项目没有锁文件，或者安装命令非零退出，则把 `node_modules` 软链到主仓库对应目录（等价于 `just worktree` 脚本旧的 `symlink-from-main` 策略）。

行为约束：

- 只处理 worktree 中**缺失**的 `node_modules`：worktree 内已有的真实目录或软链（例如已 `npm install` 过）永远不会被覆盖
- 复用已有 worktree 时同样补齐：旧 worktree 缺 `node_modules` 的，下一次 `kc run` 会自动治愈
- 真实安装优先是为了兼容所有前端工具链（包括 Next.js/Turbopack 等不允许跨根目录软链的工具）；pnpm 等内容可寻址存储会复用本机缓存，通常不会明显慢于软链
- 主仓库该项目缺 `node_modules` 且无法安装时**无法软链**，会记一条 `warning`（而非静默跳过），方便把后续 `vite: command not found` 追溯到"主仓库没装依赖"或"安装失败"
- 处理是 best effort：单个项目失败（权限、竞态、网络）只记日志，不会中断 agent run
- 软链同样保持 gitignored 状态，不会让 worktree 变脏

> 守护进程路径（`kc worktree create` / `kc run`）不读 `WORKTREE_FRONTEND_STRATEGY` 环境变量——该变量只对手动 `just worktree`（`scripts/shared/worktree/create.sh`）生效。daemon 路径固定采用上面的"先安装、后软链回退"策略。

## worktree 分支安全与自动修复

`kc run` 在把 agent 放入 worktree 前会执行两项准备：

1. **远程分支对齐**：如果 `refs/remotes/<remote>/issue-<number>` 存在，则 fetch 并仅当本地分支是其祖先、且 worktree 干净时做 fast-forward；dirty、diverged 或本地领先场景会保留本地状态并失败/继续，不会 destructive reset。
2. **分支状态自愈**：如果 worktree 处于 detached HEAD（例如 post-PR supervisor 正在 rebase 或被人工 checkout 到某个 commit），runner 会尝试自动恢复：
   - 处于 active rebase 时：无冲突则 `rebase --continue`；有冲突则调用配置的 AI agent 解决冲突并继续 rebase，和 post-PR supervisor 的冲突解决策略一致。只有 agent 在 `max_repair_attempts` 次尝试后仍无法解决，才会 fallback 到 `rebase --abort` 并 checkout 目标分支。
   - 单纯 detached HEAD：若 `issue-<number>` 分支不存在或当前 HEAD 领先于该分支，则把分支指到当前 HEAD 并 checkout；若已分叉则报错，避免静默丢失历史。

如果恢复失败，runner 会把 Issue 标记为 `failed` 并给出可操作的错误信息；成功则继续正常执行 agent、验证和发布流程。

## 清理 stale issue worktree

当 Issue 对应的 PR 合并并删除远端分支后，本地可能仍保留 `issue-<number>` 分支和 `.iar-worktrees/issue-<number>`。可以用 `kc worktree cleanup` 做一次安全清理：

```bash
# 只预览，不删除
kc worktree cleanup --dry-run

# 真正删除满足条件的本地分支和 KedaCode worktree
kc worktree cleanup --yes

# 谨慎：允许删除脏 worktree 或未合入远端 base branch 的分支
kc worktree cleanup --yes --force
```

没有传 `--yes` 时，命令会按 dry-run 处理。执行删除前会先 `git fetch <remote> --prune`，然后只清理同时满足以下条件的分支：

- 本地分支名匹配默认 KedaCode 模式 `issue-<number>`
- GitHub Issue 状态为 `CLOSED`
- `refs/remotes/<remote>/issue-<number>` 已不存在
- worktree 位于当前仓库的 `.iar-worktrees/` 下
- 默认模式下 worktree 没有未提交或未跟踪文件
- 默认模式下分支已经合入 `<remote>/<base_branch>`，或者 GitHub 上存在以该分支 head 的已合并 PR（覆盖 squash / rebase merge 场景）

历史 `<repo>-worktrees/tasks/issue-<number>` worktree 是旧 `just worktree`/`just implement` 路径，不会被 `kc worktree cleanup` 自动删除。确认安全后可手动执行：

```bash
git worktree remove /path/to/<repo>-worktrees/tasks/issue-<number>
git branch -d issue-<number>
```

## run 与 daemon 的执行语义与控制面

`kc run` 与 `kc daemon` 的职责边界是显式契约：**run = 手动单次、定向执行**（一条命令只处理一个目标，但**多条 `--issue` 命令之间互不阻塞，可并发**，见下节），不调度、不碰合并、不涉及 autopilot；**daemon = 无人值守常驻轮询**，是唯一运行 autopilot 调度阶段的地方。

### run 目标必填（breaking change）

`kc run` **必须带目标**，不再"默认按优先级捞 ready 队列"：

```bash
# 定向只跑一个 Issue
kc run --issue 42

# 跑一个 PRD（解析其头部的 - GitHub Issue: 回链；没有回链会报错，先 kc issue create）
kc run tasks/pending/P1-FEAT-xxx.md

# 处理整个 ready 队列（等价旧的 kc run 行为，必须显式）
kc run --all-ready
```

- `--issue` 与 PRD 路径互斥；三者（`--issue` / PRD 路径 / `--all-ready`）都不给时退出码 2（usage error）。
- 定向 run 只处理目标 Issue（仍走依赖门禁与 claim）；队列里其他 ready Issue 原封不动。
- `run` 没有 `--autopilot`，也没有 `--concurrency`：手动调度用 `kc backlog advance`。并行有两个来源——**进程内**归 daemon 的 `--concurrency`（默认 `max_concurrent_issues=1`，即串行），**进程间**就是给每个 Issue 各发一条 `kc run --issue <N>`。

**迁移**：旧脚本/文档里"无目标的 `kc run`"改为 `kc run --all-ready`（行为等价）；想精确跑某条 Issue 用 `--issue`。Console「开始此 PRD」已随本变更改为传 `--issue`，仓库级 run_once 动作改为传 `--all-ready`。

### 与 daemon 的互斥范围：只挡队列轮询

互斥保护的对象是**同一份 ready 队列**，不是"这个仓库有没有人在跑"。因此：

| 调用形态 | 同仓 daemon 存活时 |
|---|---|
| `kc run --all-ready`（队列轮询） | **拒绝**（退出码 5 conflict）：两个轮询者会双 claim 同一 ready 队列 |
| `kc run --issue <N>` 或 `kc run <PRD_PATH>`（显式单目标） | **照常执行**：单目标不轮询队列，领取冲突由认领状态单独把关（见下节） |

```text
conflict: A daemon for repository '<repo_id>' is already running (PID <N>) ...
next: kc registry stop --repo-id <repo_id> ... or rerun with --takeover ...
```

确认要手动接管时使用 `--takeover`（强警告 + 交互确认；脚本里加 `--yes`，`--json` 机器模式下必须 `--yes`）：

```bash
kc run --issue 42 --takeover --yes
```

接管流程：优雅停 daemon（SIGTERM → 等待超时 → 兜底 SIGKILL；**不会**把 SIGKILL 当首手段）→ 终止 daemon 的在途 agent 子进程树（不留孤儿 agent）→ 把在途 Issue reclaim 回 `agent/ready` → 执行定向 run。**接管会中断 daemon 当前所有在途 Issue**（不只是你指定的那个），它们会被 reclaim 后重跑——这是有意为之的破坏性动作，所以默认不发生。`--takeover` 跳过下节的准入判定：显式接管本身就是"强制回收在途 Issue"的入口，能不能领由 reclaim 决定。

### 显式定向的领取准入

`agent/ready` **只约束守护进程的自主挑选**。人显式点名一条 Issue 时不再要求它带就绪标签——手工开的、没有 PRD 锚点的 Issue 同样可以直接 `kc run --issue <N>`。放宽的代价是"领不领得到"必须响亮回报，而不是像守护进程那样把不合条件的 Issue 静默跳过：

| 目标状态 | 定向 run 的结果 |
|---|---|
| open、无 workflow 标签，或只带 `agent/ready` | 放行，进入 ready 通道执行 |
| `agent/running` 且最近一次认领的持有者仍存活 | 退出码 5 `conflict`，错误里点名持有者 `host` / `PID`；本机持有者提示 `kc run --issue <N> --takeover`，远端持有者只能等它结束（系统没有强制接管远端认领的入口） |
| `agent/running` 但本机持有者 PID 已不存在 | 放行，交给 running 通道的恢复路径（rework / 发布恢复） |
| `agent/blocked` 且没有未消费的解除请求 marker | 退出码 5 `conflict`，提示先 `kc blocked-continue --issue <N>` |
| 读不到、或不是 open | 退出码 3 `not_found` |
| `agent/review` / `agent/supervising` 等其他状态 | 准入层放行，但 `kc run` 没有对应通道，本轮静默跳过（返回 0）——这些状态的通道是 `kc review` / review-daemon |

**跨机器的认领无法探测远端进程，一律按有效处理（fail-closed）**：抢跑别人正在执行的 Issue 是双跑，而拒绝一次定向只是让人等一等。

守护进程侧不受本节影响：空队列仍是正常状态，`--all-ready` 与 `kc daemon` 挑不到合条件的 Issue 时依然静默返回 0。

### 首次领取的仲裁（claim marker 选举）

GitHub 的标签写入没有 compare-and-swap，所以"谁先领走这条 Issue"不能靠先到先得的标签写。首次领取改为**认领 marker 选举**：

1. 每个候选进程先在自己的认领评论里投标（`iar:claim` marker 带 `started_at`、host、PID、agent）；
2. 等一个短暂 grace 窗口后**回读评论线程**，筛出同一轮的投标（有 `started_at`、落在并发窗口内、本机已死 PID 的投标剔除；远端投标按存活处理）；
3. 按 `(started_at, host, PID)` 的全序取最小者。**只有赢家**把 Issue 切到 `agent/running`；
4. 落败方把自己那条评论改写成 `iar:claim-withdrawn`（解析器认不出它，因此不会被后续轮询误读成认领），并**跳过该 Issue 而不写任何标签**——输掉竞争不是执行失败，不该把赢家的 running 覆盖成 failed。

历史 marker（没有 `started_at` 的旧格式）与并发窗口外的投标都不算投标，所以 rework / 重新认领不会被自己过去的评论永久锁死。同一进程在 agent fallback 换 agent 时会再走一遍领取：读到的是**自己更早的那次投标**，按重入处理并继续执行。


### 快速合并旗标 `--fast-merge`

默认 run 在 builder 提交后会依次跑两道验证门禁——rv re-exec（重跑 PRD 声明的验证命令）与独立 verifier 复核——绿灯后才开 Draft PR。`--fast-merge` 是**本次 run 的一次性快速通道**：builder 提交后**跳过这两道门禁**，直接开 Draft PR，并在正文里显式标注该 PR **未经自动化验证**。

```bash
# 快速通道：完成即开 PR，跳过验证门禁（仅单一目标）
kc run --issue 42 --fast-merge
```

- **PR 正文自我声明**：机器可读 marker `<!-- iar:fast-merge issued=<N> -->` + 人读说明"本 PR 经快速通道发布，未经过自动化验证门禁，合并前请人工验证"。marker 只声明旁路事实；无 marker 也不能代替最终代码树和实际验证结果的核对。
- **只覆盖验证门禁本身**：发布路径（分支命名、push、PR 正文契约、pre-PR review）与"已提交干净工作树"的发布前提**照常执行**；builder 失败/恢复循环也不受影响——快速通道不会把失败掩盖成成功。
- **单一目标限定**：与 `--all-ready` 组合是用法错误（退出码 2）——快速通道绝不能开启"整队未验证爆发"。
- **stack 依赖拒绝（fail-closed）**：目标 Issue 若声明 `iar:depends-on ... mode="stack"` 顺序依赖，在启动任何 agent、乃至 `--takeover` 停 daemon 之前就报用法错误——未验证的上游会顺着 fork 基污染整条下游链；Issue 读不到时同样拒绝（无法证明不是 stack 就不走旁路）。
- **与 `direct-pr` 标签冲突时拒绝**：目标 Issue 带着 `direct-pr` 标签却请求 `--fast-merge`，是用法错误而非"取更强者"——两个旁路跳过的门禁不同，静默升级会掩盖调用者的真实意图。撤掉旗标或摘掉标签，二选一。
- **不加旗标时零变化**：默认 run 与今天完全一致（门禁照常）。daemon 没有 `--fast-merge`，也没有对应配置项——旁路只作用于这一次显式调用，且不触碰自动合并。

### 直发档旗标 `--direct-pr`

`--fast-merge` 只跳验证门禁，发布路径（review agent、仓库验证命令、supervisor）照常。`--direct-pr` 是**面向没有 PRD 的 Issue 的更粗档位**：builder 提交后，除了 rv re-exec 与独立 verifier，**再跳过 pre-PR review agent 与 runner 侧的验证命令**，并跳过发布链内联调用的 post-PR supervisor，直接开 Draft PR——质量门禁转移到该 PR 上的 CI。它不禁止独立运行的 review-daemon 后续审查，也不改变原有 workflow 标签选择；例如 Issue 仍进入 `agent/supervising` 时，后台 reviewer 可以选中它，DIRECT marker 不是后台审查排除规则。

```bash
# 无 PRD 锚点的 Issue：做完就直接开 Draft PR
kc run --issue 42 --direct-pr
```

- **适用边界（fail-closed）**：目标必须**没有 `- PRD path:` 锚点**。传 PRD 路径作为目标本身就是 PRD-backed，直接用法错误；`--issue <N>` 目标会读 Issue 正文证明无锚点，**读不到同样拒绝**——旁路不能留下一份未归档、未校验的 PRD。
- **PR 正文自我声明**：marker `<!-- iar:direct-pr issued=<N> -->` + 人读说明"runner 侧未运行审核 Agent 与仓库验证命令，合并前请确认 CI 变绿并人工验证"。
- **两个旁路旗标互斥**：`--fast-merge` 与 `--direct-pr` 同时给出是用法错误（退出码 2）。两者存在感不同（一个跳独立验证，一个连审核与仓库验证一起跳），所以刻意不"取更强者"，避免静默升级掩盖调用者的真实意图。
- **单一目标限定**：与 `--all-ready` 组合是用法错误——直发档绝不能开启"整队未审核爆发"。旗标只改写敲下它的那台机器；daemon 没有 `--direct-pr` 旗标，也没有对应的全局配置项。要让 daemon 或另一台机器直发某个 Issue，用下面的 Issue 标签，而不是给 daemon 开总闸。不触碰自动合并。

### Issue 上的 `direct-pr` 标签：跟着 Issue 走的直发档

恢复发现会优先识别检查点已关联的同轮成功 PR：ready 的新依赖、blocked 缺少 resolution marker、running 的 rework 不阻止只补交接。已有 PR 仅经只读关联证明后入选，并在取得认领/本地锁后再次核对；未发布的选择和历史 PR 不享有这个例外。

旗标作用于本次调用，不会成为其他认领进程的配置。`direct-pr` 标签把档位声明**绑到 Issue 本身**：升级后的认领方——另一台机器、`kc run --all-ready` 批量队列、或 daemon——在认领成功后重新 `get_issue` 读到它，就只把那个 Issue 解析为直发档。**所有可能认领的 runner 都必须升级并重启**；旧版可能忽略标签继续 NORMAL，更新 Skill 文件不等于更新已经运行的进程。

```bash
# 用现成的标签同步入口创建/改名标签，再贴到目标 Issue
kc labels sync
gh issue edit 42 --add-label direct-pr
# 先确认 #42 已有 agent/ready 且依赖满足；标签自身不会入队
kc daemon run            # 升级后的 daemon 认领 #42 后选择直发档
```

逐 Issue 解析表（同一次批量里的其他 Issue 各自独立判定，绝不共享档位）：

| 调用侧请求档位 | Issue 带 `direct-pr` 标签 | 解析结果 |
|---|---|---|
| `NORMAL`（默认） | 是 | `DIRECT` |
| `DIRECT`（`--direct-pr`） | 是 | `DIRECT`（来源记为旗标 + 标签） |
| `FAST`（`--fast-merge`） | 是 | 显式拒绝：两个旁路跳过的门禁不同，不静默取更强者 |
| 任意 | 否 | 沿用调用侧档位（默认行为零变化） |

- **只选档位，不放宽准入**：标签只决定"走哪一档"，直发档的准入条件仍由同一个 core 入口把关——**带 PRD 锚点的 Issue 一律拒绝**（无法证明不是 PRD-backed 的读取失败同样 fail-closed），依赖门禁照常执行。拒绝发生在 builder 与 PR 创建之前，不会被降级成 `NORMAL` 悄悄跑完。daemon 侧只隔离那一条 Issue，其余照常推进。
- **生命周期：保留 → 认领者消费 → 待恢复**：执行失败或建 PR 失败时标签**保留**，下一轮照常是直发意图。只有首次认领的胜者在**确认本次发布的那个 Draft PR** 之后删除它，且只删这一个标签；落败方既不建立档位也不删标签。删除失败与建 PR 不是原子操作，此时报告"发布成功、标签清理待恢复"并附上 PR URL，**不宣称本轮完全成功**。
- **本轮关联先于消费**：准入后的有效档位固定到本执行轮，Agent fallback 与恢复沿用它；运行中增删标签不动态切换阶段。runner 用现有 Issue 评论记录未完成发布轮、候选分支/head 及创建前无历史 PR 的事实。检查点只接受当前凭据作者，或经 fresh GitHub 仓库权限确认可管理 Issue 的作者；不将 authorAssociation 当作权限。检查点或权限查询失败时阻塞，不把失败当成无记录。创建前不存在旧 PR 的证明和同轮关联也使用严格完整 PR 查询：只有成功空列表代表不存在，查询失败、空输出或格式错误不能授权新发布或消费历史 PR。消费需要当前未完成轮关联，以及仓库、Issue、分支/head、DIRECT marker 匹配；历史 PR 即使同分支、同 head、同 marker，也不能自动消费新选择。
- **下一轮只补交接，不重建**：重新认领或 `kc recover --issue <N>` 先查未完成轮。确认对应的已发布 PR 后，仅补标签删除、移交到 `agent/review` 与完成记录，不重跑 Agent、不重复开 PR。DIRECT 的最终状态不随内联 supervisor 配置改变；只有最终状态切换成功后才完成发布轮检查点，切换失败仍可恢复。标签已删但移交前崩溃时也恢复原轮，不能退成新的 NORMAL 构建；发布后正文或 PRD 锚点变更时，清理专用路径可完成原交接，但不能借此启动新 DIRECT。关联无法核实则报告阻塞，不猜历史 PR 是本轮。
- **同名标签没有版本**：等标签消费且 workflow 交接完成后，才为新轮重加标签。清理窗口内并发重加同名标签表达新选择不受支持。同样，创建前无旧 PR 的检查与创建不是原子操作；外部操作者在窗口内手动创建完全同分支/head/marker PR，现有接口不能保证原子归属，需与认领者协调手动发布。
- **最终树验证责任在人**：标签与旗标一样只声明档位，PR 上的 marker 也不区分来源；直发 PR 的质量门禁转移到该 PR 的 CI 与人工对最终 Git tree 的验证——合并前确认 CI 变绿，并核对 landed tree 就是被验证过的那棵树。

### Operator 的发布档位判断与验证责任

| 档位 | runner 实际旁路 | 保留的责任 |
|---|---|---|
| NORMAL | 无 | 按配置执行 review、仓库验证命令、RV 重跑、独立 verifier 与 post-PR supervisor。 |
| FAST | RV 重跑、独立 verifier | pre-PR review Agent、仓库验证命令与配置启用的 supervisor 照常；保留干净已提交树与未验证声明。仅显式单目标，不适用于 stack 上游。 |
| DIRECT | FAST 两项，再加 pre-PR review Agent、runner 侧仓库验证命令、发布链内联 post-PR supervisor | builder 与发布前提、依赖/认领门禁、无 PRD 且正文可读准入、直发声明、PR CI，以及操作者对最终树的必要验证；不禁止独立后台 review。 |

选择档位先看实际影响与可证明结果，不能按文件数或一段理由自动决定：

- 公共构造函数或调用契约、四层之间的契约、schema/迁移、认证与安全边界、范围不明的任务使用 **NORMAL**。局部单测绿灯不足以证明跨层或真实入口安全，不给这些任务贴直发标签绕过失败。
- FAST 与 DIRECT 减少的是 runner 的重复步骤，**不取消对最终改动的必要验证**。跑适用的现有检查及最高可行保真度的真实入口；改代码后重跑受影响验证并绑定最终 Git tree。失败、过期、被弱化或保真度不足的证据不能当 PASS；不能造绿灯、放宽断言、略去必要检查，或把手工注入状态/临时组件预览称为真实流程。此时转 NORMAL 或说明阻塞。
- 正例：纯注释/docstring 修正，确认无可执行差异并通过相应 lint/文档检查；局部文字修正，已在实际 CLI `--help` 或生产页面正常布局/Provider 中观察到正确文本。文字局部不意味着可以用草稿页替代；涉及流程、Portal 或父级布局仍保留生产边界。
- 设置标签是现有 GitHub 写操作，检查会话里已授权的目标、档位和副作用；授权有效且范围未扩张就继续，不新增每轮批准、理由参数、授权子命令或签名服务。只读“看看”不授予执行权限。
- PR marker 与正文记录旁路事实，**不建立 PASS，也不自动合并**。合并前核对 CI、必要真实入口/人工结果和最终合并树；旧 head 证据不能替代最新代码验证。

随包 operator 的「跑一次」说明包含完整判据与交接恢复路径；daemon 仅消费显式 Issue 标签，不提供全局 DIRECT 开关。

### 多条 run 之间的并发边界

不同 Issue 的 `kc run --issue <N>` 天然并发，不需要排队——**同仓有 daemon 在跑也一样**（daemon 的互斥只挡队列轮询，见上两节）：

```bash
# 两个 Issue 各一条命令，同时推进；互不等待
kc run --issue 42
kc run --issue 43
```

- 前台 `run` **不获取 repo 级 daemon 锁**：`--all-ready` 只做"是否有 daemon 存活"的只读检查（互斥语义见上文），显式单目标连这个检查都不做。所以 run 与 run 之间、定向 run 与 daemon 之间都不互斥。
- 每个 Issue 自带隔离资源：worktree 在 `.iar-worktrees/issue-<N>`，claim / blocked-claim 锁也按 worktree 独立；状态库走 WAL 容忍并发写。两条命令撞向**同一条 Issue** 时，由首次领取选举裁决归属（见上节），落败方安静跳过。
- **例外**：`--all-ready` 领的是同一份 ready 队列，两条 `--all-ready` 并发会双 claim，不要这么用。要并发多个 Issue，就给每个 Issue 各发一条 `--issue`。
- 停止常驻进程没有 `kc daemon stop`：`kc daemon` 只暴露 `run` / `status`，托管进程用 `kc registry stop --repo-id <id>`（对未托管的手动 `kc daemon` 无效，需自行结束进程）。

### daemon 的 autopilot 按次覆盖

```bash
# 配置 autopilot.enabled=false，但本次 daemon 临时开调度
kc daemon --autopilot

# 配置 autopilot.enabled=true，但本次 daemon 临时关调度
kc daemon --no-autopilot
```

- 优先级：**flag > 仓库 `.kedacode.toml` > 全局**；传了旗标即锁定本次常驻进程（之后改 `.kedacode.toml` 不影响本次进程），不传则每轮热读配置。
- 旗标**只覆盖调度类 autopilot**（发现/晋升 pending PRD、补槽）；它**不能**打开自动合并——合并仍由 `safety.auto_merge` + `autopilot.enabled` 配置双开关决定，`process_merge_queue` 在配置未开时保持 no-op。

## 多仓库 Registry 兼容

`config.toml` 中的 `[agent_runner.repositories.*]` 现在作为显式 registry 兼容路径使用，适合 `--repo-id` 或 `--all`：

```toml
[agent_runner]
max_issues = 1

[agent_runner.repositories.keda]
path = "/Users/zata/code/keda"
enabled = true
# Optional ``owner/name`` for ``gh pr list --repo`` (PR column on
# ``kc issue list``). 缺省时 PR 列为空 + stderr 一次性 WARN。
github_repo = "ZataZhang/keda"

[agent_runner.repositories.backend_service]
path = "/Users/zata/code/backend-service"
enabled = true
github_repo = "owner/backend-service"
```

- 每个仓库必须有 `path`（本地绝对路径）。
- `enabled = false` 可临时禁用某个仓库。
- `github_repo` 是可选的 `owner/name` 字符串；缺省时 `kc issue list` 不会调用 `gh pr list --repo <repo_id>`，PR 列留空、stderr 一次性打印 WARN 指引用户去 `config.toml` 或 `.kedacode.toml` 补字段。**该字段不参与目录名推断**——kc 不做 `git remote` 解析，必须由用户显式声明。
- registry 通常只保留 `path` 和 `enabled`；仓库级 overrides 仍兼容，但建议迁移到目标仓库的 `.kedacode.toml`。
- 未指定 `--repo`、`--repo-id` 或 `--all` 时，单仓库命令（如 `kc run`、`kc review`）只处理当前 Git 仓库；`kc daemon` 和 `kc review-daemon` 同样只处理当前已初始化注册仓库，未命中、未初始化或匹配多个时报错。如需监控所有 enabled registry entries，请显式使用 `--all`。

迁移示例：

```toml
# 旧 config.toml
[agent_runner.repositories.backend_service]
path = "/Users/zata/code/backend-service"
enabled = true
display_name = "Backend Service"

[agent_runner.repositories.backend_service.git]
remote = "origin"
base_branch = "main"

[agent_runner.repositories.backend_service.runner]
verification_commands = ["git diff --check", "uv run pytest"]
```

迁移后保留轻量 registry：

```toml
# keda/config.toml
[agent_runner.repositories.backend_service]
path = "/Users/zata/code/backend-service"
enabled = true
```

把仓库细节放到目标仓库：

```toml
# /Users/zata/code/backend-service/.kedacode.toml
[agent_runner.repository]
id = "backend_service"
enabled = true
display_name = "Backend Service"

[agent_runner.git]
remote = "origin"
base_branch = "main"

[agent_runner.runner]
verification_commands = ["git diff --check", "uv run pytest"]
```

### Registry 生命周期管理

已注册仓库可以通过 `kc registry` 子命令重新初始化或取消托管，无需手动编辑 `config.toml` 或进目录改 `.kedacode.toml`。

#### 重新初始化（`kc registry reinit`）

当已接管仓库的 `.kedacode.toml` 配置（如 `git.remote`）与实际情况不一致时，可以重新初始化：

```bash
# 默认把 remote 重置为 origin，覆盖现有 .kedacode.toml
kc registry reinit --repo-id ZataZhang-fsense

# 显式指定 remote 和 base_branch
kc registry reinit --repo-id ZataZhang-fsense --remote upstream --base-branch develop

# 重新初始化后立刻重启 daemon 和 review-daemon
kc registry reinit --repo-id ZataZhang-fsense --start-daemons
```

#### 取消托管（`kc registry remove`）

停止 daemon/review-daemon 并从 registry 移除条目：

```bash
kc registry remove --repo-id ZataZhang-fsense
```

该命令只删除 `config.toml` 中的注册条目，**永不删除本地仓库目录**：托管 clone 与开发工作区都只能由人工在外部自行处理。管理终端「项目接入」页的「移除」按钮走同一条语义。

#### 查看已注册仓库与运行状态（`kc registry list`）

```bash
kc registry list
```

输出会列出 `~/.kedacode/config.toml` 中所有已注册仓库，并显示每个仓库的 `daemon` / `review-daemon` 是否在运行，以及对应的进程 ID：

```
                            Registered repositories
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ repo_id               ┃ display... ┃ path                   ┃ daemon  ┃ review-daemon ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ ZataZhang-fsense  │ fsense     │ /Users/.../fsense      │ running │ running       │
│                       │            │                        │ (p123)  │ (p124)        │
│ another-owner-repo    │ another    │ /Users/.../another     │ stopped │ stopped       │
└───────────────────────┴────────────┴────────────────────────┴─────────┴───────────────┘
```

这可以帮助你确认哪些仓库当前正在后台跑 agent，以及是否重复启动了 daemon。

> **Managed vs Unmanaged**：
> - 通过 `kc registry start` / console / `kc takeover` 启动的 daemon 是**托管进程**，会写入 `~/.kedacode/processes.json`，状态显示为 `running (<process_id>)`，可用 `kc registry stop` 停止。
> - 直接在命令行执行 `kc daemon` / `kc review-daemon` 启动的进程是**未托管进程**。`kc registry list` 会通过扫描系统进程把它们识别出来，状态显示为 `running (unmanaged)`，但**不会**被 `kc registry stop` 停止，也没有独立的日志文件被 `registry` 命令管理。
> - 同时存在托管与未托管进程时，列表优先显示托管状态。

> **不要混用**：同一时间、同一仓库，建议要么只使用 `kc registry start` 管理 daemon，要么只手动运行 `kc daemon`。混用可能导致两个进程同时 claim 同一仓库的 Issues，且 `registry stop` 不会清理手动启动的进程。

> **单实例保护（self-guard）**：`kc daemon` / `kc daemon run` 启动时会按 `repo_id` 获取单实例锁（`~/.kedacode/daemon-locks/<repo_id>.lock`）。若该仓库已有存活的 daemon（无论托管还是手动启动），新进程会**直接拒绝启动并返回非零退出码**，而不会与既有 daemon 并发轮询、重复 claim 同一仓库的 Issues。不同 `repo_id` 的 daemon 互不影响、可并行运行。被 `kill -9` 等异常终止后残留的过期锁，会在下次启动时自动回收（按记录的 PID 判活，死进程的锁可被抢占）。该保护用于防止反复执行 `kc daemon` 堆积出大量并发实例、成倍消耗 agent 调用与 token 预算。

#### 查看 daemon 进程明细（`kc daemon status`）

`kc registry list` 只显示每个仓库 daemon / review-daemon 的汇总状态。如果你需要查看具体进程的 PID、启动时间、可执行路径、命令行、日志文件路径，以及该进程是托管还是未托管，使用：

```bash
# 在当前仓库目录下查看当前仓库
kc daemon status

# 查看指定仓库
kc daemon status --repo-id keda-main

# 查看所有 enabled 注册仓库
kc daemon status --all
```

输出示例：

```
                                 Daemon status
┏━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ repo_id ┃ kind          ┃ status        ┃  pid ┃ process_id ┃ started_at ┃ log_path ┃ executable           ┃ command              ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ keda-m… │ daemon        │ managed run…  │ 1234 │ abc123def  │ 2026-06-2… │ …/proce… │ kc                  │ kc daemon --repo-i… │
│ keda-m… │ review_daemon │ unmanaged r…  │ 5678 │ unmanaged… │ 2026-06-2… │ -        │ /usr/bin…            │ /usr/bin/kc review… │
└─────────┴───────────────┴───────────────┴──────┴────────────┴────────────┴──────────┴──────────────────────┴──────────────────────┘
```

- `managed running`：通过 `kc registry start` / `kc takeover` / console 启动的托管进程。
- `unmanaged running`：直接在命令行执行 `kc daemon` / `kc review-daemon` 启动的进程。
- `log_path`：托管进程的真实日志文件路径；`-` 表示未托管进程（无独立日志文件）。

#### 查看进程日志（`kc logs`）

`kc logs` 让你直接从命令行查看 daemon / review-daemon 的进程日志，无需拼接文件路径或打开 Web 管理终端。

```bash
# 查看当前仓库 daemon 的最近 200 行日志
kc logs

# 指定仓库和进程类型
kc logs --repo-id keda-main --kind review_daemon

# 查看最近 50 行
kc logs --lines 50

# 实时跟随日志（Ctrl-C 退出）
kc logs -f

# 跟随 review-daemon 日志
kc logs --kind review_daemon -f
```

行为说明：

- `kc logs` 默认查看 `daemon` 进程；`--kind review_daemon` 可切换到 review-daemon。
- `-n / --lines N` 控制初始回看行数（默认 200）。
- `-f / --follow` 在初始回看后持续输出新增内容，直到 Ctrl-C 或进程退出。
- 无 running 进程时，打印回退指引（最近进程日志路径或全局 `logs/app-YYYY-MM-DD.log`），退出码 0。
- 全局日文件名只由 `infrastructure/logging/logger.py` 的 `daily_log_path()` 产出，回退提示与真正在写的文件因此不会漂移；跨午夜的长驻进程（`kc loop-daemon`、`kc registry start` 拉起的 runner / review-daemon）会在下一次写日志时自动切到当天文件，并在切换时按 `log_retention_days`（默认 14）清理过期日志。
- 仓库目标推断逻辑与 `kc daemon` 一致：未指定 `--repo-id` 时从当前工作目录推断唯一 enabled 注册仓。

#### 查看单个 Issue 的实时输出（`kc logs --issue`）

单次 `kc run` 或 daemon 处理某个 Issue 时，Agent 的可见输出会即时落到
`logs/agent-runner/issues/<repo_id>/issue-<N>-<时间戳>.log`。用 `--issue`
可以在**另一个终端**按 Issue 号查看这条流，无需知道进程 ID 或日志目录：

```bash
# 查看 Issue #42 最近一次尝试的最近输出（尾部窗口）
kc logs --repo-id keda-main --issue 42

# 持续跟随 Issue #42 的输出（重试时自动切换尝试，Ctrl-C 退出）
kc logs --repo-id keda-main --issue 42 --follow
```

行为说明：

- `--issue <N>` 与 `--kind` 互斥；不带 `--issue` 时保持原有进程日志语义。
- 首次输出给**尾部窗口**（默认最近 64 KiB，再按 `--lines` 截行），不会从头倾泻整份日志；之后按字节偏移增量续读。
- 默认跟随该 Issue 的**最新尝试**；重试产生新文件时 CLI 会提示并切换。
- Issue 尚未开始、日志已清理或仓库未注册时，显示明确的空态 / 不可用提示，不会回退到别的 Issue 或进程日志。
- 单次 `kc run`（串行）与并行 daemon 使用同一归属规则；串行时启动终端收到的就是这条 per-Issue 流的镜像，因此**同样带行首时间戳**——与未经路由时终端实时视图的显示一致（TTY 与重定向都一样），不是「无前缀的原始输出」。
- Agent 流式输出的每个物理行在写入 per-Issue 日志、实时看板与前台镜像时带 `[HH:MM:SS]` 行首时间戳，可与心跳行的完整日期时间前缀对照时间线。时间戳由**输出路由 sink** 统一添加（`core/use_cases/agent_runner_output_routing.py`）：agent 生产者交给 sink 的始终是可读原文，所以合议的 `workspaces/**/*.md` 等原文产物不会被塞进时间线。文本增量只在行首加一次时间戳，不会切断同一行；`[iar-attempt-end]` 终态标记不经 sink，保持裸行，`--follow` 仍能精确匹配它。
- 这条流里还带 `[iar-invocation-start]` / `[iar-invocation-end]` / `[iar-invocation-coverage-incomplete]` 三种结构化标记，用来核对"这段时间到底起了几次进程、每次谁在跑、跑成什么样"。字段读法、模型三态、`retry_of` 链与缺口解读见「Agent 调用记录与停滞诊断（Invocation Tracing）」。`grep '\[iar-invocation-' <日志文件>` 可以直接抽出调用清单。

`kc daemon` 本身继续作为启动 daemon 的快捷命令，等效于 `kc daemon run`。例如：

```bash
kc daemon --repo-id keda-main --interval 300
# 等效于
kc daemon run --repo-id keda-main --interval 300
```

#### 启动与停止托管 daemon（`kc registry start` / `kc registry stop`）

对于已经在 registry 中注册且已 init 的本地仓库（例如你手动 `kc init` 过的 `keda-main`），可以直接用 `start` / `stop` 管理 daemon 生命周期，无需 `reinit --start-daemons`（后者会重置 `.kedacode.toml`）：

```bash
# 启动单个仓库的 daemon + review-daemon
kc registry start --repo-id keda-main

# 只启动 daemon（不启动 review-daemon）
kc registry start --repo-id keda-main --no-review-daemon

# 启动所有 enabled 注册仓的 daemon + review-daemon
kc registry start --all

# 停止单个仓库的 daemon + review-daemon
kc registry stop --repo-id keda-main

# 停止所有 running 的 daemon + review-daemon
kc registry stop --all
```

`start` 会把进程登记到 `~/.kedacode/processes.json`，因此 `kc registry list` 会显示 `running`；`stop` 会从 processes.json 读取 running 记录并优雅停止对应进程。连续两次 `start --repo-id` 会因"已存在 running 进程"失败，需先 `stop`。

> **与 `reinit --start-daemons` / `takeover` 的区别**：`registry start` 不修改 `.kedacode.toml`，不重新初始化仓库配置，仅负责 spawn / stop 托管进程。适合本地开发目录或已经 init 过的仓库。

> **重启后恢复**：`kc` 不会自动复活重启前的 daemon。把 `kc registry start --repo-id <id>` 写进 macOS `launchd` plist 或 Linux systemd service，可实现开机自启。

## 全局多仓库接管（`kc takeover`）

全局安装 `kc` 后，你可以从任意目录直接接管 GitHub 仓库，无需手动 `git clone`、`kc init` 和编辑 registry。

### 前置条件

- 已全局安装 `kc`（见上文"安装"）。
- 已登录 GitHub CLI：`gh auth login -h github.com`。
- `kc` 会在首次需要时自动把默认配置复制到 `~/.kedacode/config.toml`，作为全局配置源。

### 交互式接管

```bash
# 列出当前 gh 用户可见的仓库，勾选后自动接管
kc takeover
```

流程：

1. 检查 `gh` 登录状态。
2. 调用 `gh repo list` 拉取仓库列表（默认 100 条）。
3. 过滤掉已经注册且本地路径存在的仓库。
4. 在终端展示 checkbox 多选界面，输入编号 toggle、`all` 全选、`none` 清空、`done` 确认、`quit` 取消。
5. 对每个选中的仓库：
   - `gh repo clone <owner>/<repo> ~/.kedacode/repos/<owner>/<repo>`
   - 在新 clone 的仓库执行 `kc init`
   - 写入 `~/.kedacode/config.toml` 的 `[agent_runner.repositories.<repo_id>]`
6. 默认启动 `kc daemon` 和 `kc review-daemon` 两个托管子进程（在目标仓库路径下启动，因此只监控该仓库）。

> 克隆目标目录 `~/.kedacode/repos/<owner>/<repo>` 已存在时不会被复用：该仓库直接判失败并报错，需人工先改名或移走那个目录（或改 `--clone-root`）再重试。

### 非交互式与批量接管

```bash
# 直接指定仓库，适合脚本
kc takeover --repos owner/repo-a owner/repo-b

# 指定组织或用户
kc takeover --owner myorg --limit 200

# 指定 clone 根目录（默认 ~/.kedacode/repos）
kc takeover --clone-root ~/kc-repos

# 只接管，不启动 daemon
kc takeover --repos owner/repo-a --no-start

# 预览将要执行的操作，不写入任何文件
kc takeover --repos owner/repo-a --dry-run
```

### 全局配置与进程日志

接管后的仓库注册在 `~/.kedacode/config.toml` 中，托管进程使用：

- `~/.kedacode/console.db`：运行历史与审计日志。
- `~/.kedacode/processes.json`：托管进程 pidfile registry。
- `~/.kedacode/process-logs/<repo_id>/`：daemon / review-daemon 的 stdout/stderr 日志。

你可以通过 `kc console` 启动管理终端查看、停止、重启这些进程；已运行的 FastAPI 服务也可以直接访问同一组 console API。

### 接管后的日常命令

```bash
# 查看所有托管进程状态（通过 HTTP API）
curl http://localhost:8000/api/v1/agent-runner/console/processes

# 停止某个托管进程
# 先在 list 响应中找到 process_id，再调用 stop 端点
curl -X POST http://localhost:8000/api/v1/agent-runner/console/processes/<process-id>/stop

# 查看进程日志
curl 'http://localhost:8000/api/v1/agent-runner/console/processes/<process-id>/logs?offset=0'

# 手动对某个接管的仓库跑一次
kc run --repo-id owner-repo-a
```

## 状态流转与两阶段审查

### 完整状态机

```text
agent/ready
    → claim → agent/running
    → implementation agent commit
    → Issue comment: Implementation Complete
    → push implementation branch to remote
    → pre-PR review (仍在 agent/running; reviewer 修复也会 push)
    → Draft PR creation
    → agent/supervising
    → Issue comment: Draft PR Created
    → post-PR supervisor cycle
    → supervisor approve → agent/review
    → supervisor repair/rebase → agent/running (existing PR branch rework)
    → supervisor human-input-needed → agent/blocked
    → supervisor failed → agent/failed
```

### Pre-PR Review

`kc run` 在实现 agent 完成并提交后会先 `git push` 推送到远程分支，再在 Draft PR 创建之前执行一次 pre-PR AI code review：

1. Runner 写 Issue comment `Implementation Complete`
2. Runner 立即调用 `push_changes()` 把当前 feature branch 推送到配置 remote（push 不再被 review 阻塞）
3. Runner 构建 review packet（Issue、PRD、diff、changed paths、verification results、AI standards、review workflow）
4. **Reviewer 必须通过 Skill 工具调用 `code-reviewer` skill** 并把 skill 输出的 findings 写进响应的 `findings` JSON 数组；review packet 默认模板（`agent_review.DEFAULT_REVIEW_PROMPT_TEMPLATE`）会显式提示 reviewer 这一行为，并提供 findings schema（`category` / `severity` / `file` / `line` / `title` / `description` / `recommendation`）。仓库可在 `[agent_runner.pre_pr_review].review_prompt_template` 覆盖该模板，未配置时回退到代码默认。
5. 打开新的 AI session 执行 review，并使用 `[agent_runner.pre_pr_review].timeout_seconds` 限制最长运行时间
6. Reviewer 可直接修改 worktree；修改后写入 `.agent-runner/commit-request.json`
7. Runner 通过 commit proxy 提交 reviewer 修改、重新运行 `verification_commands` 并立即 `push_changes()` 把修复推送到远程分支
8. Runner 写 Issue comment `Pre-PR Review Result`（含 findings 表格）
9. Review 通过后才调用 `create_draft_pr()` 创建 Draft PR；review 未收敛时跳过 PR 创建，runner 软失败并写 comment

review packet 现在是 **修复-再审查收敛模式**：轮数由 `[agent_runner.pre_pr_review].max_attempts` 控制，默认 2 轮。每一轮 reviewer 都可以通过 `commit-request.json` 自修复（runner 仅负责 commit proxy + verification 重新执行 + push callback 把修复推送到远程）。如果 reviewer 在一轮内报出了 findings 却未写 `commit-request.json`，runner 会追加一条提醒并把该轮内重新调用 reviewer 最多 `[agent_runner.pre_pr_review].commit_request_reminder_attempts` 次（默认 1 次），让 reviewer 有机会把 findings 落实为补丁，而不是直接放弃。最后一轮结束后若仍未 `approved` 但 reviewer 已写最终修复 commit request，runner 接受该最终修复并继续发布；否则写一条 findings 评论并走软失败路径（runner 不再抛出硬错误，但调用方会按 `agent/failed` 处理）。Reviewer 解析器会基于 findings 数组重新统计 `critical`/`high`/`medium`/`low` 计数，避免 reviewer 自填数字被信任；若 verdict 为 `approved` 但 findings 非空，verdict 会被降级为 `changes_requested` 以避免漏报。

> **执行事故的换人回退**：reviewer **进程层面**的失败——墙钟/静默期超时（`pre_pr_review.timeout_seconds`，默认 1800s 被杀）、agent 进程非零退出、启动 I/O 错误——不是「review 未通过」，也不会把 Issue 判死。它们与 provider 容量失败（429/529）一样被升级为一个总的可换人错误 `AgentExecutionError`，交由跨 agent fallback 链换下一个候选 agent 重试。只有 `UnrecoverableError`（安全/分支违规）与 `ForbiddenBlockedError`（禁改路径）这类每个 agent 都会撞同一堵墙的失败才直接冒泡，不换人。此前这些异常（`subprocess.TimeoutExpired` 等）不在任何 except 白名单里，会一路冒到顶层 `except Exception` 直接落 `agent/failed`。

当某一轮 reviewer 补丁在提交门禁上失败（`commit_requested_changes` 抛 `VerificationFailedError`，如 `verification_commands` 或 `pre_commit_verification_command` 复跑失败）时，runner 会做两件事再进入下一轮：(1) 把失败命令与截断后的 stdout/stderr（复用 `format_verification_failure`）写进下一轮 review packet 的 “Previous reviewer patch was REJECTED …” 段落，让 reviewer 针对真正的门禁失败调整做法，而不是蒙眼重复同一补丁；(2) 调用 `unstage_changes`（`git reset --mixed`）把失败补丁 unstage，改动仍留在工作区供下一轮修订，避免残留 staged 内容被 `git add -A` 原样重提、每轮撞同一堵墙。该反馈只投喂给紧接着的一轮；提交成功或首轮时不携带。

#### 审-修分工（`repair_agent`）

`[agent_runner.pre_pr_review].repair_agent` 决定这一段的 findings 由谁落实成代码，取三个值：

| 取值 | 含义 |
|---|---|
| `self`（默认） | 审核者既出 findings 又自己打补丁，与历史行为逐字节一致 |
| `executor` | 审核者只出 findings；改代码交回本次实现者（拿不到时按 Issue 标签回落并写日志） |
| `<agent 名>` | 审核者只出 findings，改代码交给这个名字；未注册则在该阶段开始前 fail-fast |

非 `self` 模式下这一段的执行顺序变为：

1. review packet 追加 `Read-only review mode` 段（禁止改文件、禁止写 `.agent-runner/commit-request.json`），审核者以 `deliberate` 用途启动——对声明了该用途的 agent（如 codex）是沙箱级硬只读；未声明该用途的 agent 回落到 `run` 用途并打 WARNING，此时只读约束仅由提示词与下面的丢弃规则兜底。
2. 审核者若仍写出 `commit-request.json`，该文件被**直接删除、不触发任何提交**，Issue 评论里会写明这件事；它已经改掉的**文件**不回滚（回滚是破坏性的），而是并入修复者本轮的提交。
3. verdict 为 `changes_requested` 且有 findings 时，runner 用共享构建器 `build_repair_prompt` 生成修复提示词（Issue 上下文 + 本轮 findings 清单 + 既有的提交请求约束），以解析出的修复者、`run` 用途启动。
4. 修复者没写出提交请求时，沿用 `commit_request_reminder_attempts` 的同轮提醒重试（提醒对象从审核者换成修复者）。
5. 后续提交代理、验证重跑、push callback、轮数上限、收敛与软失败语义全部不变：最后一轮仍未通过但本轮已产生可提交修复 → 接受并继续发布；否则软失败进入失败标签。

Issue 评论结构随之增加 `- Repairer: <agent>` 一行（仅非 `self` 模式），便于在 GitHub 上直接看出这一轮谁审、谁修。

#### 三处"配置写了不生效"的修正

同一批改动顺带修掉了三处静默失效的路由缺陷：

1. `allow_same_agent = false` 时审核者不再硬编码回落到 `codex`，而是从 agent 注册表里取第一个不等于实现者的 agent；注册表里只有实现者一个 agent 时保持原样并打 WARNING。
2. `kc review` 入口解析 supervisor 的优先级改为「命令行 `--agent` > `[agent_runner.post_pr_supervisor].supervisor_agent` > Issue 标签路由」，与发布路径共用 `resolve_supervisor_agent`。此前该入口完全不读配置里的 supervisor。
3. 审核 / 修复 / 收尾 / 校验 / 恢复 / 冲突解决等**二级调用点**此前都没有把运行时配置传下去，导致注册表里的自定义 agent 与内置 agent 的参数覆盖在这些阶段被静默忽略（自定义 agent 作审核者会直接报未注册）。现在 `src/backend` 内每个 `run_agent_with_prompt*` 调用点都传 `config`，并由 `tests/test_agent_config_consistency.py::test_every_agent_invocation_call_site_passes_config` 用 AST 守卫防止回退。**已显式配置过 agent 覆盖的仓库，这些阶段第一次会真正按配置执行。**


Pre-PR review 不产生独立的 durable label，整个过程仍在 `agent/running` 内。Runner 会记录 review start、cycle、reviewer exit code、parsed verdict、commit-request 处理、push callback、findings 计数和 result comment 写入等日志；底层进程 runner 对长时间运行的 agent 命令每 60 秒输出一次 heartbeat，并在达到 timeout 时终止子进程。

> **空 commit request 行为**：当 reviewer 写出了 `.agent-runner/commit-request.json` 但工作树已无任何可提交改动（例如 reviewer 的建议与现状一致，或上一轮 cycle 已经提交过修复），runner 会按 reviewer 解析出的真实 verdict 处理：
>
> - `approved` → 写一条 `Pre-PR Review Result` 评论（action summary 为 `reviewer approved with an empty commit request`），循环正常收敛。
> - `changes_requested` → 写一条评论（action summary 为 `reviewer requested changes but produced no committable diff`）并继续下一轮 cycle；用尽 `max_attempts` 后走 `Pre-PR review did not approve after N attempt(s): ...` 软失败路径。
>
> 若 reviewer stdout 没有可解析的 JSON verdict，runner 会在 commit request 中读取可选的 `verdict`、`summary`、`findings_high`、`findings_medium`、`findings_low` 元数据作为兜底。空提交信号由 `EmptyCommitRequestError`（`RuntimeError` 的子类，message 保持 `"Agent requested a commit but produced no file changes."`）承载，因此 `is_recoverable_commit_request_error(...)` 仍把它分类为可恢复，且不会被升级为 `Pre-PR review repair failed` 硬失败。

> **approved + 非空补丁行为**：当 reviewer verdict 为 `approved` 且写出了非空 `.agent-runner/commit-request.json` 时，runner 通过 commit proxy 提交补丁、重跑 `verification_commands` 并通过 push callback 把修复 push 到远程，成功后该轮直接收敛通过（action summary 为 `reviewer approved and runner committed follow-up patch`），不会被降级为 `changes_requested` 后在最后一轮硬失败。若 verdict 为 `changes_requested` 且补丁提交成功，则继续下一轮 cycle；用尽 `max_attempts` 时，软失败信息反映最后一轮的实际结果（`reviewer patched and runner committed follow-up changes`），不会显示更早 cycle 的陈旧摘要。

### Post-PR Supervisor

Draft PR 创建后，Issue 先进入 `agent/supervising`，并立即运行至少一次 supervisor cycle：

1. Runner 写 Issue comment `Draft PR Created`
2. Supervisor 收集 PR context、Issue comments、PR comments、base branch 状态、CI/check 状态、diff、verification results
3. 如果 worktree 在只读 supervisor cycle 开始前仍有未提交变更，runner 会自动 `git stash push -u` 把它们临时存起来，cycle 结束后再根据 supervisor 决策恢复（pop）或继续保留（`wait_for_checks`）
4. Supervisor 输出结构化 action：
   - `approve_for_human_review` → 恢复 stash 后若 worktree 干净则进入 `agent/review`，仍有未提交变更则进入 `agent/blocked`
   - `repair_pr_branch` / `resolve_conflict` → 恢复 stash 后进入 `agent/running` 做现有 PR branch 修复
   - `rebase_pr_branch` → 恢复 stash 后进入 `agent/running` 做 rebase
   - `wait_for_checks` → 保持 stash 与 `agent/supervising`，等待 PR checks 完成
   - `request_human_input` → 恢复 stash 后进入 `agent/blocked`，但必须带有可操作 summary
   - `mark_failed` → 恢复 stash 后进入 `agent/failed`

5. 需要代码修改时，runner 先写 `post_pr_rework_requested` event marker，再切到 `agent/running`
6. 后续 `kc run` 检测到该 pending marker 和 open PR 后，在现有 PR branch 上执行 rework
7. rework 成功后写 `rebase_repair_complete` marker，再进入后续 supervision/review 流程

#### Supervisor diff 分层注入与跨 cycle finding 累积

Supervisor 评审长 PR 时，整包 diff 会被截断到 `max_diff_chars`（默认 6000）字符——关键变更常常整个落在窗口外。配置 `key_paths` 后，命中的路径前缀下的文件 diff **全量**进入 prompt，其余文件共享剩余字符预算：

```toml
[agent_runner.post_pr_supervisor]
key_paths = ["src/backend/core/", "pyproject.toml"]
max_diff_chars = 6000
```

prompt 结构变为：`Changed files (N)` 清单 → `--- Key files (full diff) ---` → `--- Other files (truncated to N chars) ---`。`key_paths` 为空时退化为原来的整体截断。若配置的路径前缀一个文件都没命中，日志会打 WARNING 提示前缀写错。

Supervisor 还能跨 cycle 记住未解决的 findings：LLM 可以在 JSON 决策里附带 `findings[]`（每项含 `title`，以及可选的 `severity` / `file` / `line` / `description` / `status`）。kc 把它们合并进 `<worktree>/.iar/state/issue-<N>/findings.json`（已被 `.iar/` gitignore 排除），下一个 cycle 的 prompt 会带上 `Previous unresolved findings from cycles X..Y:` 段。已在后续 cycle 修好的 finding 用 `status: "resolved"` 上报即可出列。用 `previous_findings_injection_enabled = false` 关闭注入。

#### PR 后修复的审-修分工（`post_pr_supervisor.repair_agent`）

supervisor 本身始终是只读审阅；`[agent_runner.post_pr_supervisor].repair_agent` 只决定**判定需要改代码之后由谁动手**，取值语义与 pre-PR 那一段同名同义：

- `self`（默认）：supervisor 自己执行修复，与历史行为一致。
- `executor`：交回本次实现者。发布路径显式把本次实现者传下去；拿不到本次实现者的入口（独立跑 `kc review`、rework 路径等）按 Issue 标签回落，并在日志里写明用的是哪一种来源。
- `<agent 名>`：指定 agent；未注册时该阶段开始前 fail-fast。

两个修复调用点（supervisor 修复循环、rework 路径）共用解析器 `resolve_repair_agent`，避免两条路径语义分叉。修复提示词由 `build_repair_prompt` 生成，带 Issue 上下文与本轮 findings 清单（rework 路径取 `.iar/state/issue-<N>/findings.json` 里未解决的累积 findings），修复者不必自己重新推断要改什么。

#### Rebase Conflict Recovery Branch Guard

在 rebase conflict recovery 过程中，runner 会在继续 rebase 之前先校验当前 branch。如果 `git branch --show-current` 返回 PR branch 名称，说明工作区处于正常 branch 上，恢复流程继续执行。如果返回空（表示处于 detached HEAD 的 rebase 中间态），runner 不会仅凭空 branch 名称就继续，而是进一步读取 Git 的 active rebase metadata（`.git/rebase-merge/head-name` 或 `.git/rebase-apply/head-name`），确认 rebase 目标 branch 与预期的 PR branch 一致后才允许继续。

如果 rebase metadata 缺失、目标 branch 未知，或者解析出的目标与预期 PR branch 不匹配，runner 会拒绝继续并抛出带有诊断信息的错误，且**不会自动 abort rebase**。这样可以把工作区保留在冲突状态，供运维人员手动排查。该检查独立于普通的 commit proxy branch validation，专门用于保护 rebase 中间态。

- 正常 branch：直接校验 branch 名称与预期 PR branch 是否一致。
- Detached HEAD rebase：读取 `.git/rebase-merge/head-name` 或 `.git/rebase-apply/head-name` 解析目标 branch。
- 目标未知或不匹配：拒绝继续，保留 rebase 状态，输出诊断错误。

### 持续观察

```bash
# 单次检查所有 supervising/review Issues
uv run kc review

# 常驻 review daemon（默认每 120 秒轮询，可在 config.toml [agent_runner.daemon] 调整）
uv run kc review-daemon
```

`kc review` / `kc review-daemon` 会：
- 扫描 `agent/supervising` 和 `agent/review` 的 open Issues
- 加载 linked PR context、Issue comments、PR comments 和最新 `iar:event` marker
- 检测以下维度变化：
  - `head_sha` 或 `base_sha` 变化
  - `checks_state` 变化（如 CI 从 `PENDING` 变为 `FAILURE`）
  - `mergeable` 状态变化（如冲突出现或消失）
  - Issue comments 数量超过最新 supervisor marker 记录的游标
  - PR review comments 数量增加
- 任一维度变化时，先移回 `agent/supervising`，运行 supervisor cycle
- 无变化时不重复启动 supervisor；若最新 `post_pr_supervisor` marker 的 action 与当前标签不一致，则按已完成 action 对齐标签（approve → `agent/review`、wait → `agent/supervising`、request input → `agent/blocked`、repair/rebase → `agent/running`）。这能恢复 supervisor 已写结果 marker、但进程在标签更新前退出时留下的滞后状态。

Supervisor 结果评论会把自身写入后的 Issue comment 数量记录进 marker，
因此 runner 自己写出的 `Agent Runner Post-PR Supervisor` 评论不会触发下一轮重审。

例外：最新 supervisor marker 记录的 action 为 `mark_failed` 时（如 agent
基础设施崩溃重试耗尽、或输出不可解析触发 fail-closed），上一轮并没有产出
有效评审结论。此时人工把 label 从 `agent/failed` 拨回 `agent/supervising`
即视为明确的重试请求，即使上下文完全未变化也会重新运行 supervisor cycle，
不需要靠加评论或推新 commit 来"制造变化"。

PR context 读取使用当前 GitHub CLI 支持的 `statusCheckRollup` 字段聚合
checks 状态：

- 任一 check/status 失败时，`checks_state=FAILURE`
- 任一 check/status 仍在 queued、in_progress 或 pending 时，`checks_state=PENDING`
- 所有 check/status 成功、skipped 或 neutral 时，`checks_state=SUCCESS`
- 无 CI/check rollup 时，`checks_state` 为空，不阻断人工 review

平台不再按 checks 聚合状态改写 supervisor 动作：`checks_state`/`checks_summary` 是 Agent 的观察事实，不是动作指令。Supervisor Agent 结合 checks 摘要（可见的失败/进行中 check 名称、结论与链接）、PRD 验收要求、本地验证与其它证据，自行在合法动作中选择——真实执行的代码/测试失败可请求 `repair_pr_branch`，checks 仍在运行可选择 `wait_for_checks`，CI 未运行或基础设施不可用（如账单受限）且 PRD 未把该远端检查列为硬性验收项时可进入人工 review，但必须明确披露远端 CI 未验证，不得当作通过。runner 仍执行与 CI 分类无关的确定性安全门：

- PR 当前不可合并或存在冲突（`mergeable=false`）且 Agent 选择 approve、wait 或 request_human_input 时，动作会被改写为 `rebase_pr_branch`；该守卫不读取 checks 状态
- open PR 存在但完整 PR context 暂时无法读取时，本轮 supervision 会 defer 并保留待观察状态；发布、rework 后续评审和循环内再次评审都不会使用不完整 context 批准进入 review
- supervisor 输出无法解析、返回未知 action，或返回空 summary 的 `request_human_input` 时，本轮会进入 `agent/failed`，不会生成无原因的 `agent/blocked`；平台不得根据 checks 状态猜测替代动作
- supervisor agent 进程非零退出且 stdout 中识别不到任何 JSON 决策（如 claude CLI 遇到 API / 网络错误崩溃）时，视为基础设施级失败，会在同一 cycle 内重试至多 `max_agent_crash_retries` 次；重试之间指数退避（从 `crash_retry_initial_backoff_seconds` 起每次翻倍，单次等待封顶 `crash_retry_max_backoff_seconds`，默认 30s 起、上限 10 分钟），以扛住分钟级的 API 提供方中断。重试仍失败才进入 `agent/failed`，summary 会标明是 agent infrastructure failure。非零退出但 stdout 中仍能识别出 JSON 决策时直接使用该决策，不消耗重试次数

这样可以避免 `agent/review` label 覆盖仍需 `kc run` 消费的 rework/rebase 状态。

注意：merge queue 的自动合并仍会在合并前等待 checks 全绿（见下文"Autopilot 快速档（合并队列）"），这与 supervisor 的动作选择相互独立——进入人工 review 不代表验收完成，也不产生自动合并或归档资格。

`kc review` 的 CLI 日志会打印本轮 outcome，例如 `queued_rebase_pr_branch`、
`approved_for_human_review` 或 `deferred_pr_context_unavailable`。被 queue 的
rebase/repair 仍由下一次 `kc run` 在 PR branch worktree 中执行。

### Rework Guard

`kc run` 遇到 `agent/running` Issue 时，不会自动视为 rework。只有同时满足以下条件才会进入现有 PR branch rework 路径：

1. Issue comments 中存在尚未被 `rebase_repair_complete`、`draft_pr_created`、`implementation_complete` 或 `publish_recovered` 消费的 `phase=post_pr_rework_requested` marker
2. 该 marker 包含 PR branch
3. 该 PR branch 仍有 open PR，且 marker 中的 `head_sha` 与 open PR 当前 head 一致；不一致说明 PR 已被外部更新，旧的 rework 请求不再安全

后续 `post_pr_supervisor` 这类观察类 marker 不会覆盖 pending rework marker；只有明确的完成/发布类 marker 才会消费旧 rework 请求。

如果 pending rework 存在但对应 worktree 路径已不存在（例如被手动删除），runner 会将 Issue 转入 `agent/blocked` 并写评论说明缺失路径与恢复步骤，而不是在错误的目录上执行 repair/rebase。

否则 `kc run` 会跳过该 Issue，避免抢占另一个 runner 正在首次执行的任务。

## kc issue list

`kc issue list` 是一个**只读**视图命令，用于回答"哪些 issue 已经提交了 PR、哪些还在排队"。它拉取每个目标仓库的 Issue 列表，并补齐每个 Issue 关联的 Pull Request 状态。

### CWD 自动检测

不传 `--repo` / `--repo-id` / `--all-registered` 时，命令根据 `Path.cwd() / ".kedacode.toml"` 是否存在自动决定单仓 / 全仓模式：

- 存在 `.kedacode.toml`（即 `REPOSITORY_CONFIG_FILENAME`）→ 等价 `--repo Path.cwd()`，只列当前仓
- 不存在 → 等价 `--all-registered`，跨 `config.toml` 中所有 enabled 注册仓

### Flag 表

| Flag | 说明 |
|---|---|
| `--repo <path>` | 强制单仓模式，指向任意本地仓库路径 |
| `--repo-id <id>` | 强制单仓模式，指向 `config.toml` 注册项 |
| `--all-registered` | 强制多仓扫描，即使 cwd 是 KedaCode 项目仓 |
| `--state <open\|closed\|all>` | Issue 状态过滤（默认 `all`） |
| `--label <name>` | 仅显示带该 label 的 Issue |
| `--with-pr` | 仅显示至少有一个 PR 的 Issue |
| `--without-pr` | 仅显示无 PR 的 Issue；与 `--with-pr` 互斥 |
| `--limit <n>` | 每仓最多拉取 Issue 数（默认 100） |
| `--output <table\|json>` | 渲染格式（默认 `table`） |

### 示例：单仓（cwd 是 KedaCode 项目仓）

```bash
$ cd ~/code/keda
$ kc issue list
  #     TITLE                              LABELS              STATE   PRS
  42    Add issue list command              kc-agent-ready     open    #143 [draft]
  41    Fix label sync                      kc-bug, urgent     open    #140 [merged]
  40    Update docs                          kc-docs            closed  —
```

### 示例：全仓扫描（cwd 不是 KedaCode 项目仓）

```bash
$ cd /tmp
$ kc issue list
  REPO              #     TITLE                              STATE   PRS
  owner/repo-a      12    Refactor init                       open    #55 [open]
  owner/repo-a      11    Add tests                            open    —
  owner/repo-b      7     Fix crash                            open    #22 [merged]
```

### 示例：JSON 输出（脚本消费）

```bash
$ kc issue list --with-pr --output json | jq -c '.number, .pulls[0].state'
12
open
7
merged
```

JSON 输出每行一个 `IssueWithPulls` 对象，字段稳定：`repo?`、`number`、`title`、`state`、`labels`、`updated_at`、`url`、`pulls[]`（每个 pull 含 `number` / `state` / `url` / `is_draft` / `merged` / `title`）。

### 错误行为

- `--repo` 与 `--repo-id` 同时传 → 退出码非零，提示互斥
- `--with-pr` 与 `--without-pr` 同时传 → 退出码非零，提示互斥
- 单仓调用失败（gh 不可用、网络错误）→ 退出码非零，错误信息包含 repo 路径和错误原因
- 全仓模式下某仓 API 失败不影响其他仓，最终退出码非零，stderr 含该仓错误

## 常用命令

```bash
# 初始化当前目标仓库配置
kc init

# 重装/刷新用户级 Skill（不触碰 .kedacode.toml，仓库已 init 过也能用）
kc skill install --dry-run
kc skill install

# 清掉旧版 kc init 钉死在 .kedacode.toml 里的 generated_content（先预览再执行）
kc config migrate --dry-run
kc config migrate

# 同步当前仓库 Labels
kc labels sync

# 同步单个配置仓库
kc labels sync --repo-id keda

# 按 priority/P0 → P3、同级 Issue 编号升序预览 ready 队列；不运行 Agent
kc run --all-ready --dry-run --max-issues 3

# 从 PRD 创建 ready Issue（默认发布 PRD）
kc issue create tasks/pending/example.md --repo-id keda --type feature --agent codex --ready

# 一次从多个 PRD 创建 ready Issue（支持 shell glob）
kc issue create tasks/pending/*.md --repo-id keda --type feature --agent codex --ready

# 直接传文件夹，自动展开其中所有 *.md PRD
kc issue create tasks/pending --repo-id keda --type feature --agent codex --ready

# 多个 PRD 时不能共用 --title（每个 PRD 仍从自身的 H1 标题生成 Issue 标题）
# kc issue create tasks/pending/*.md --title "Shared"   # 会报错
# kc issue create tasks/pending --title "Shared"        # 同样会报错

# 一句话需求直接开 Issue（不产生也不引用任何 PRD；与 PRD 路径参数互斥）
kc issue create --from-prompt "登录页在 token 过期时应当跳转到 SSO 而不是白屏"

# 同上，但进队列由 daemon 挑；默认只建 Issue，等人显式 kc run --issue <N>
kc issue create --from-prompt "给 CLI 的 logs 命令补一个 --json 输出" --ready

# 需要该 Issue 自带验收清单时显式开启（默认正文没有 Realistic Validation 小节）
kc issue create --from-prompt "重排 ready 队列的排序规则" --require-validation

# 单次执行（dry-run 预览；目标必填）
kc run --issue 42 --dry-run
kc run --all-ready --dry-run

# 单次执行（当前仓库的某个 Issue）
kc run --issue 42

# 处理整个 ready 队列（等价删除前的无目标 `kc run`）
kc run --all-ready

# 显式处理所有 enabled registry entries
kc run --all --all-ready

# Daemon 模式（默认每 120 秒轮询一次，仅当前已初始化注册仓库；加 --all 才处理所有 enabled registry entries）
kc daemon

# 手动驱动一次 backlog 调度（一次 continuous-scheduling pass：reconcile + promote + discover，不用等 daemon 轮询）
iar backlog advance

# 单次 review 检查
kc review

# Review daemon 模式（默认每 120 秒轮询一次，仅当前已初始化注册仓库；加 --all 才处理所有 enabled registry entries）
kc review-daemon

# 恢复发布失败（仅用于已完成审查后的 push/PR 收尾失败）
kc recover --issue 5

# 恢复发布失败（显式确认分支名）
kc recover --issue 5 --branch issue-5
```

## REPL 入口

直接运行 `kc`（不带任何子命令）会进入交互式 REPL 入口。底层调用
[`claude` / `codex` / `kimi`](#repl-agent-command-protocol) 等本地
agent，把仓库上下文与自然语言指令直接转成 KedaCode 子命令并执行。

### 行为差异

| 触发方式 | 行为 |
|---|---|
| `kc`（TTY） | 启动 REPL，循环读取用户输入，调用 agent，把 agent 标注的 `<<IAR_EXEC>>` 命令翻译成 `kc <subcommand>` 并执行 |
| `kc`（非 TTY） | 打印 Typer 帮助文本并以非零退出码失败，避免 CI / pipe 脚本 hang 住 |
| `kc --help` / `kc -h` | 仍然打印帮助（不进入 REPL） |
| `kc repl` | 显式启动 REPL 的子命令形式 |
| `kc repl --agent codex` | 显式覆盖 REPL 默认 agent |

非 TTY 行为保证现有脚本（如 `cd repo && kc --help`）继续工作。

### REPL Agent & Command Protocol

- 默认 agent 来自 `[agent_runner.repl].default_agent`（默认 `claude`）。
- `--agent codex|kimi` 可覆盖；`--agent auto` 在 REPL 入口被拒绝并回退
  到 `[agent_runner.repl].default_agent`（auto 仅用于 `kc run`）。
- agent 的回复里通过标记协议请求执行 KedaCode 子命令：

  ```
  <<IAR_EXEC>> kc labels sync --dry-run <<END_IAR_EXEC>>
  ```

  REPL 把标记里的命令交给命令执行器，执行器按白名单与确认策略运行：
  - 默认白名单覆盖 `init` / `labels` / `issue` / `run` / `daemon` /
    `review` / `review-daemon` / `recover` / `blocked-continue` / `ask` /
    `deliberate` / `takeover` / `worktree` / `registry` / `workflow` /
    `completion`。
  - 只读 / dry-run 命令自动执行（`labels sync --dry-run`、
    `run --dry-run`、`ask --plan-only` 等）。
  - 写操作 / 高风险命令（`run`、`daemon`、`issue create`、`recover`、
    `blocked-continue`、`worktree create/remove` 等）执行前会询问
    `Execute? [y/N]`。
  - 不在白名单内的命令、含 shell 元字符的请求、`git push` / `git merge`
    / `git reset` 等直接 git 写操作都会被拒绝并反馈给 agent。

每次执行的结果以 `[IAR_EXEC_RESULT]` 块回写到对话历史；agent 在下一轮
回复里就能看到 stdout / stderr / exit_code。

### 退出与审计

- 用户输入 `/exit` 或 `Ctrl+C` / EOF 时退出 REPL，返回码为 0。
- 会话元数据、对话历史、命令执行记录写入
  `logs/agent-runner/repl/<session-id>/`，包含 `session.json`、
  `transcript.md` 与 `commands.json`。可通过 `[agent_runner.repl].default_output_dir`
  覆盖。
- REPL 内部最大 64 轮（防御性封顶），防止 agent 卡在循环里无限增长。

### 与 `kc ask` 的边界

- `kc ask <prompt>` 是单次决策入口：要求 agent 输出结构化 JSON
  DecisionPlan + 受控执行；适合 CI / 一次性自动执行场景。
- `kc`（REPL）是持续多轮对话入口：agent 可以反复请求执行 KedaCode 子命令，
  每轮都把结果反馈回对话；适合本地探索与人工协作场景。
- 二者共享 `[agent_runner.interactive_decision]` 与
  `[agent_runner.repl]` 配置段，但 settings 完全独立（默认 agent、
  超时、白名单都分开）。

### 示例：同步 Labels 并启动 daemon

```bash
$ cd /path/to/repo
$ kc
kc> sync the labels for me, then start the daemon.
I'll sync the labels.
<<IAR_EXEC>> kc labels sync <<END_IAR_EXEC>>
[Executed] kc labels sync
stdout: ✅ labels synced

Now I'll start the daemon.
<<IAR_EXEC>> kc daemon <<END_IAR_EXEC>>
This command starts a long-running daemon. Execute? [y/N] y
[Executed] kc daemon
stdout: Daemon started with PID 12345

kc> /exit
```

## PRD Rework Workflow

`kc` supports the reverse of `kc issue create`: automatically generating or rewriting a PRD from an existing GitHub Issue. This is useful when an Issue is created directly on GitHub and later needs a canonical PRD, or when an existing Issue receives new comments that require updating its PRD.

> **PRD 不是执行的前置条件。** 没有 `- PRD path:` 锚点的 Issue 一样可以跑：`kc run --issue <N>`
> 直接领取（见「显式定向的领取准入」），需要连审核与仓库验证一起省掉时用 `kc run --issue <N> --direct-pr`。
> 本节是**按需增强**路径——想让这条 Issue 事后拥有规范 PRD（可归档、有验收清单）时才打
> `agent/rework-prd`。注意锚点一旦写入，`--direct-pr` 与 Issue 上的 `direct-pr` 标签就不再适用于该
> Issue 的新执行：PRD-backed 的交付必须走
> PRD 交付门禁并归档其 PRD。已有未完成 DIRECT 轮的清理恢复只补交接，不借此重建。

### Triggering PRD Rework

To trigger the workflow, add the `agent/rework-prd` label to an open Issue:

```bash
gh issue edit <issue-number> --add-label agent/rework-prd
```

> **Note:** The `agent/rework-prd` label is provisioned automatically by `kc init` and `kc labels sync`. If your repository was initialized before this label was added, run `kc labels sync` once so the label exists before you apply it.

The next daemon pass or `kc run` will detect the label and process the Issue before normal ready-issue execution.

### What Happens During PRD Rework

1. **List**: The runner queries open Issues labeled `agent/rework-prd` (default limit 1 per pass).
2. **Worktree**: It creates or reuses the `issue-<N>` worktree (the same worktree/branch a downstream ready-issue run would use), so the PRD never touches the main working tree.
3. **Collect**: It loads the Issue body and all comments.
4. **Resolve Path** (inside the worktree):
   - If the Issue body already contains a `- PRD path: \`...\`` anchor, the runner rewrites that same file.
   - If no anchor exists, the runner generates a new filename under `tasks/pending/` using the pattern `P<priority>-<TYPE>-YYYYMMDD-HHMMSS-prd-<slug>.md` (priority/type are inferred from `priority/<p>` and `type/<t>` labels, or the `[Type]` title prefix).
5. **Generate**: It calls the configured content generator. In agent mode the prompt is built from the user-level `prd` skill that `kc init` fetched from remote `zata-codes-template` (the single source of the PRD methodology and output contract); if the skill is unreachable it falls back to the configured `prompt` template, then to a minimal fallback PRD.
6. **Write**: The PRD file is written inside the worktree (overwriting existing or creating new).
7. **Commit + Publish**: The PRD is committed to the `issue-<N>` branch and published via `publish_changes` — pushed to the remote and opened (or reused) as a **draft PR**. A regenerated-but-identical PRD that produces no new commit skips PR creation.
8. **Update Issue**:
   - Inserts/updates the `PRD path:` anchor in the Issue body. When the repository resolves to a GitHub remote, the anchor line gets a clickable suffix appended after the closing backtick (e.g. ``（[在 GitHub 打开](https://github.com/<owner>/<repo>/blob/HEAD/<path>)）``); the machine-readable anchor itself stays intact (see `issue_prd_github_link.py`).
   - Removes `agent/rework-prd`.
   - Adds `source/prd`.
   - Optionally adds `agent/ready`. Because the PRD is committed to the `issue-<N>` branch, a downstream ready-issue run reusing that worktree can read it, so `agent/ready` is safe to keep.
9. **Comment**: Posts a success comment with the PRD path, generation source, and the draft PR link.

Because the PRD lands on the `issue-<N>` branch behind a draft PR (instead of being written straight into `main`), the main working tree stays clean and the change is reviewable before merge.

### Label Transitions

```text
agent/rework-prd  →  source/prd (+ agent/ready optional)
```

On failure:

```text
agent/rework-prd  →  agent/failed
```

A failure comment is posted with the error and instructions to re-add `agent/rework-prd` to retry.

### Stopping at the PRD (skip auto-implementation)

By default, generation does **not** stop at the PRD. Step 8 adds `agent/ready`, and within the **same** `kc run` / daemon pass the PRD-rework phase (`process_prd_rework_issues`) runs *before* the ready-issue phase (`run_once`). So the freshly generated Issue can be claimed and implemented in that same pass; the implementation commits land on the same `issue-<N>` branch and accumulate in the same draft PR as the PRD.

If you want the runner to generate the PRD but **hold before implementing** (e.g. to review the PRD on its own first), use the dependency gate. Add an `iar:depends-on` marker to the **Issue body**, pointing at a sentinel Issue you keep open until you are ready:

```text
<!-- iar:depends-on #<sentinel-issue> -->
```

How it behaves:

- The gate is evaluated in the ready-issue phase (`run_once`), **not** during PRD generation. The PRD is still generated, committed, and published as a draft PR; only implementation is held.
- An unsatisfied dependency adds `agent/waiting` and the Issue is skipped. An Issue dependency is satisfied when the target Issue is **closed**. Close the sentinel Issue to release — the next pass clears `agent/waiting` and proceeds to implementation.
- Add the marker to the Issue body **before** the rework pass. Removing `agent/ready` after generation is racy, because generation and the first claim can happen in the same pass; the body marker is evaluated up front and avoids that race. The rework step only edits the `PRD path:` line, so a pre-existing marker is preserved. See the "Issue 依赖门禁（Dependency Gate）" section above for full marker semantics.

This reuses the inter-Issue ordering gate as a manual hold, so it needs a real sentinel Issue to point at. If you are fine reviewing the PRD and its implementation together, you do not need to block at all — nothing merges until the draft PR passes human review and validation sign-off.

### Configuration

The PRD-from-Issue generation target is configured under `[agent_runner.generated_content.prd_from_issue]`:

```toml
[agent_runner.generated_content.prd_from_issue]
enabled = true
mode = "agent"
agent = "auto"
timeout_seconds = 120
body_template = "..."
prompt = "..."   # fallback only — used when the prd skill spec is unreachable
```

In `mode = "agent"`, the PRD prompt is built from the `prd` skill spec rather than the inline `prompt`. The skill path is resolved as: explicit override → `KEDACODE_PRD_SKILL_PATH` → keda's own `~/.kedacode/skills/prd` → per-agent user-level skills directories derived from each agent's `auth_home` in the agent registry（如 `~/.codex/skills`、`~/.claude/skills`、`~/.kimi-code/skills`）. `KEDACODE_SKILLS_DIR` overrides the install root (CI and tests use it to land the install in a temporary directory). `kc init` fetches only `prd` and `code-reviewer` from the remote `ZataZhang/zata-codes-template` repository and installs them into keda's own skills root first, then into every detected agent's user-level skills root; Keda does not ship their contents in its wheel. Keda's own copy is resolved first on purpose: the agent directories belong to the agents and their user, so deleting the copy there must not cost the runner its ability to parse PRDs. The skill is the single source of the PRD methodology/output contract, so the inline `prompt` is kept only as a fallback for when the skill file cannot be read.

The skill is also the single source of PRD **parsing**: Acceptance Checklist and Change Log structure come from the skill's `scripts/prd_contract.py`, which the runner invokes as a subprocess and consumes as JSON. Keda carries no parser of its own, so the skill must be installed as a whole — `kc init` copies the full `skills/prd` directory, `scripts/` included. The startup preflight checks both the contract version and the presence of that sibling script, which means **the unit test suite needs a real installed skill too**: CI installs one (`kc init` with `KEDACODE_SKILLS_DIR` pointing at a temporary skills root, exported as `KEDACODE_PRD_SKILL_PATH`) and local runs need the skill installed (`kc init`) or `KEDACODE_PRD_SKILL_PATH` set.

See the "Generated Content 配置" section above for the full template variable list and example.

### Failure Recovery

If PRD generation fails (e.g., AI agent error, write permission issue, or invalid output), the runner:

- Removes `agent/rework-prd`.
- Adds `agent/failed`.
- Comments on the Issue with the error and retry instructions.

After fixing the root cause, manually re-add `agent/rework-prd` to retry.

## 失败重跑

Issue 执行失败后会被标记为 `agent/failed`，runner 不会再自动处理。以下是将失败 Issue 重新置为可执行状态的完整流程。

> 非 publish 阶段失败的 `Agent Runner Failed` 评论末尾自带 `How To Recover` 段，包含可直接复制的 relabel 命令和本章节指向；publish 阶段失败的评论则提示 `kc recover`。两类评论的指引与本章节命令保持一致。

### 错误分级与 fallback 链（escalation ladder）

在把 Issue 标成 `agent/failed` 之前，runner 会按错误性质走一条分级阶梯，尽量自动恢复，而不是一遇错就失败：

1. **瞬时网络/参数错误就地重试（Level 1）**：socket 断开（如 `The socket connection was closed unexpectedly`）、连接重置、网关超时、5xx，以及 provider 返回的 `400 invalid params` / `InvalidParameter` / `BadRequest` / `input should be a valid dictionary` 等临时性参数错误，会用**同一个 agent**就地重试 `transient_retry_attempts` 次（间隔 `transient_retry_delay_seconds` 秒）。实现阶段与 Pre-PR Review 阶段共用这套重试，因此一次 review 抖动或临时参数解析失败不再直接判负。
2. **同 agent recovery**：验证失败、未产出 commit、其他可修复的请求级错误走既有的 recovery 循环——带着失败摘要重新调用同一个 agent 修复，最多 `max_recovery_attempts` 轮。
3. **跨 agent fallback（Level 2）**：当某 agent **耗尽 recovery 预算仍失败**，或命中**供应商容量限制**（429 usage limit、529 overloaded——这类同一供应商重试也只会继续失败），runner 会切换到 `agent_fallback_order` 里的下一个 agent，在已落盘的进度上接力。切换次数受 `max_agent_switches` 封顶。配置中列出但本机未安装的 agent（命令不存在）会被自动跳过。
4. **不切换的情况**：安全违规（禁改路径、分支异常）等不可恢复错误换谁都失败，runner 直接停止、不浪费配额。

`agent_fallback_order` 默认包含 `["claude", "kimi", "codex"]`，主 agent 失败后会依次尝试链中的下一个可用 agent。未安装的 agent（命令不存在）会被自动跳过。将 `agent_fallback_order` 设为空列表即可关闭跨 agent fallback，回退到单 agent 行为。所有尝试（含跨 agent）都会汇总进失败评论的 **Attempt History** 表，表格包含 **Started (UTC)**（本轮开始时间）、**Agent**（执行 agent）、**Duration**（耗时）和 **Detail**（失败摘要）列；同时每轮 attempt 会实时写入本地 SQLite 运行历史库的 `attempt_records` 表，并更新 GitHub Issue 上一条带 `<!-- iar-attempt-history -->` marker 的增量评论。该实时评论按 `(repo_id, issue_number)` 从 SQLite 轨迹整表重渲染，因此跨 agent fallback 和重新 claim 都会**追加**到同一张表，不会把上一个 agent 的历史行覆盖掉；轨迹超过 50 行时只保留最近 50 行，并在表格上方注明 `Older attempts omitted`。

配置示例见上文 `[agent_runner.runner]`：`agent_fallback_order` / `max_agent_switches` / `transient_retry_attempts` / `transient_retry_delay_seconds`。

阶梯的每一级都是**一次新的真实进程调用**，因此在 per-Issue 日志里各自留下一对 `[iar-invocation-start]` / `[iar-invocation-end]` 标记，并用 `retry_of=` + `retry_reason=`（`transient_failure` / `executor_fallback` / `resume_not_started`）串成链；`executor=` 记的是**实际执行**的那个 agent，所以"到底回退到谁了"不必再从 Attempt History 表反推。字段读法见「Agent 调用记录与停滞诊断（Invocation Tracing）」。

### 并行处理 Issue（`kc daemon --concurrency`）

默认 `kc daemon` **逐个串行**处理 Issue。机器空闲、队列较多时，可以让同一轮并行跑多个 Issue：

```bash
# 本轮最多并行处理 3 个 Issue（自动领取至多 3 个）
kc daemon --concurrency 3
```

- **取值来源**：未传 `--concurrency` 时回退到 `[agent_runner.runner].max_concurrent_issues`（默认 `1` = 串行，行为与改动前逐字节一致）。
- **领取上限**：并行时单轮领取上限抬到 `max(max_issues, concurrency)`，所以单独一个 `--concurrency N` 即可领到并跑 N 个，无需再调 `--max-issues`。
- **隔离**：每个 Issue 仍各自 worktree / 分支；共享仓库的 worktree 创建被串行化以避开 `.git` 竞争，真正耗时的 agent 执行阶段全程并行。
- **作用范围**：仅 `kc daemon`（含 `kc daemon run`）。多仓库（`--all`）仍逐仓库串行、仓库内 Issue 并行。
- **成本提醒**：`--concurrency N` 即 N 路 agent 同时烧 token，请按额度与机器资源设定。

> “本次优先某个 agent”无需新参数：`kc daemon --agent claude` 已把该 agent 放到 fallback 链首位（见上文 escalation ladder）。

#### 并行时查看每个 Issue 的日志

并行时多个 agent 的输出若都打到同一个终端会交错成乱码，因此 runner 会按 Issue 分流：

- **每 Issue 日志文件**（始终写）：`logs/agent-runner/issues/<repo_id>/issue-<N>-<时间戳>.log`，含该 Issue 的 agent 流式输出与处理日志，可在 detached / 托管模式下 `tail -f` 回看，互不交错。agent 流式输出的每行行首带 `[HH:MM:SS]` 时间戳，与心跳行的时间前缀对得上。
- **实时看板**（前台 TTY）：在交互终端直接 `kc daemon --concurrency 3` 时，会显示一个仿 `kc deliberate` 的多列实时面板，每个运行中的 Issue 一列；非 TTY（重定向、`kc registry start` 托管、CI）自动退化为按行加 `[issue #N ...]` 前缀的纯文本 + 上述日志文件。看板与纯文本视图收到的都是路由 sink 那份文本，因此每行顺序是 `[issue #N status=...] [HH:MM:SS] 原文`。
- **按 Issue 从第二终端 / Console 查看**：`kc logs --repo-id <repo> --issue <N> [--follow]` 或 Console 的 PRD 详情「实时输出」标签，都读取同一份 per-Issue 日志文件；单次 `kc run`（串行）也走同一路径，只是不显示多列看板。
- **归属靠字段而不是靠相邻文本**：每个 worker 线程各自绑定一份观测上下文（`contextvars`），因此并行时每条 `[iar-invocation-start]` / `[iar-invocation-end]` 都自带 `invocation=` / `run=` / `issue=`，即使日志被交错写入也能准确归属到具体 Issue 与具体那次调用。

### 进度落盘与跨 claim 续作（checkpoint）

体量较大的 PRD 往往无法在单次 claim 的 `max_recovery_attempts` 轮内完成。为避免每次 claim 都从零开始、永远收敛不了，runner 在一轮实现失败（耗尽重试、交付门禁仍未通过）时，会把 Agent 已经产出的在途改动提交成一个 **WIP checkpoint**：

- 提交信息形如 `[Agent][WIP] Issue #<N> checkpoint (delivery gates not yet satisfied; not for merge)`，使用 `git commit --no-verify`（在途工作可能还不过 lint），但仍执行 forbidden-path 安全校验，绝不提交 `.env` / `secrets/*` 等敏感文件。
- checkpoint 只落在 Issue 本地分支 `issue-<N>` 上，**不会被推送或合入**：发布前的本地 commit 复用检查与 publication 仍会运行 `verification_commands`、PRD 交付门禁和 Realistic Validation 证据门禁。

下一次 claim（重新置为 `agent/ready` 后）会复用该分支：

- 已有提交**已达交付标准**（验证通过、PRD 清单全勾、证据齐备）→ 直接发布，不再调用 Agent。
- 已有提交**尚未达标**（典型：上一次的 WIP checkpoint）→ runner 不再硬失败，而是带着 “continue from committed progress” 的 prompt 重新调用 Agent，在已提交进度上补齐剩余工作。
  - continuation prompt 会附带上一次失败的 failure summary 和原始 verification 输出（失败命令、exit code、stdout/stderr），让续作 agent 直接针对未通过的检查继续修复，而不是重新摸索。
- 无本地提交 → 全新实现。

因此对体量大的 Issue，反复 `agent/failed → agent/ready` 会让进度逐轮累积，而不是空转；合并时这些 WIP commit 可通过 squash 收敛为干净历史。

> **重跑时不要删除分支**：`git worktree remove` 只删工作树目录、保留 `issue-<N>` 分支上的 checkpoint，是安全的；但**不要删除 `issue-<N>` 分支本身**，否则已落盘的进度会丢失，Agent 又得从零开始。

### 何时适合重跑

- 临时网络故障或 API 限流导致 Agent 中断
- 本地环境问题（如 API Key 失效、worktree 权限错误）已修复
- 目标仓库的 pre-commit hook 等外部检查临时失败

> **不建议重跑的情况**：Issue 描述本身有误、Agent 逻辑已正确执行但业务结果不符合预期。这类情况应修改 Issue 内容或人工接管，而不是简单重跑。

### 操作步骤

1. **可选：清理旧的 worktree**

   如果上一次失败时 worktree 已创建但处于脏状态，建议先清理，避免残留文件影响重跑：

   ```bash
   # 删除对应 issue 的 worktree（将 <issue-number> 替换为实际编号）
   # 默认路径为 <repo>-worktrees/tasks/issue-<issue-number>
   git worktree remove <repo>-worktrees/tasks/issue-<issue-number>
   ```

2. **根据失败评论选择恢复命令**

   大多数失败需要回到 `agent/ready` 让 runner 重新认领并继续工作：

   ```bash
   gh issue edit <issue-number> --add-label agent/ready --remove-label agent/failed
   ```

   但如果 Agent 已经完成实现、验证通过，只是在最后的 workflow 标签切换（如 `running → supervising`）时因 GitHub API 临时抖动失败，评论会提示：

   > The agent finished its work, but the final workflow label transition failed.
   > You can retry the transition without re-running the agent:

   此时应直接执行评论中给出的命令，例如：

   ```bash
   gh issue edit <issue-number> --add-label agent/supervising --remove-label agent/failed
   ```

   这样可以避免 runner 从头重跑已经完成的实现流程。如果目标标签是 `agent/review`，则使用对应的 `agent/review` 命令。

   也可以在 GitHub 网页上手动编辑 Issue 标签，移除 `agent/failed` 并添加对应的目标标签。

3. **触发 runner 执行**

   标签改回 `agent/ready` 后，runner 会在下一次轮询时自动拾取：

   ```bash
   # 单次轮询（立即执行）
   kc run

   # 或等待 daemon 下次轮询
   ```

### 状态流转回顾

```
agent/ready  →  agent/running  →  agent/supervising  →  agent/review  →  关闭
      ↑              ↓                                    ↓
      └──────  agent/failed  ←───────────────────────────┘
              （人工修复后改回 ready）

agent/supervising ── supervisor 要求 rework ──→ agent/running ── 修复/rebase ──→ agent/supervising

# recover 恢复路径（supervisor enabled）
agent/failed ── recover ──→ agent/supervising ── supervisor approve ──→ agent/review

# recover 恢复路径（supervisor disabled）
agent/failed ── recover ──→ agent/review

# forbidden path blocked 恢复路径
agent/running ── forbidden path 拦截 ──→ agent/blocked ── blocked-continue ──→ agent/running ── 继续执行 ──→ agent/supervising
```

## Forbidden Path 阻塞恢复

当 Agent 在 commit 阶段触发了 `forbidden_path_patterns`（如修改了 `.env.example`），runner 会将 Issue 标记为 `agent/blocked` 而不是 `agent/failed`，因为人工确认后可以继续完成剩余任务。

### 触发条件

- Agent 的变更中包含匹配 `forbidden_path_patterns` 的文件
- `validate_safe_changes()` 在 `commit_requested_changes()` 阶段拦截
- Issue 进入 `agent/blocked`，评论中包含被拦截的文件列表和恢复命令

### 恢复步骤

1. **查看 blocked 评论**

   在 Issue 评论中找到 `## Agent Runner Blocked`，确认被拦截的文件列表。

2. **在 worktree 中处理 forbidden 文件**

   进入对应 worktree，根据业务需求选择提交、修改或撤销这些文件：

   ```bash
   cd $(kc worktree path --branch issue-<number>)
   git status
   # 处理 forbidden 文件后确保 worktree 干净
   git add -A && git commit -m "resolve forbidden paths"
   ```

3. **运行 blocked-continue 继续执行**

   ```bash
   uv run kc blocked-continue --issue <number>
   ```

   CLI 会依次执行：
   - 校验 worktree 存在且分支正确
   - 校验 worktree 干净（无未提交变更）
   - 校验 pending diff 不再包含 forbidden paths
   - 写入 `blocked_resolution_requested` marker comment
   - 通过 label CAS（compare-and-swap）竞争认领：将 `agent/blocked` 切换为 `agent/running`
   - 认领成功后发送 continuation prompt，让 Agent 继续完成剩余任务

### 竞争安全

多个 runner 同时处理同一个 blocked Issue 时，只有第一个成功执行 label CAS 的 runner 会继续。其他 runner 会收到明确提示并跳过。即使 `kc blocked-continue` 只写了 marker 但 CAS 被其他进程抢占，后续 `kc run` 轮询时也会检测到该 marker 并完成认领。

### 与 run 兜底路径的关系

`kc run` 在消耗完 `agent/ready` 和 `agent/running` 配额后，也会扫描 `agent/blocked` Issue。对带有 `blocked_resolution_requested` marker 的 Issue，它会执行同样的 CAS 竞争认领。这意味着：

- 你可以只写 marker（通过脚本或评论），不运行 `blocked-continue`，由 daemon 自动认领
- 也可以运行 `blocked-continue` 立即触发 continuation

### 状态流转补充

```text
agent/running ── commit 时 forbidden path 拦截 ──→ agent/blocked
agent/blocked ── 人工处理 + blocked-continue ──→ agent/running ── 继续执行 ──→ agent/supervising
```

### 注意事项

- `blocked-continue` 只处理 commit 阶段的 forbidden 拦截，不处理 publish 阶段的拦截
- 继续执行后如果 Agent 再次触发 forbidden 拦截，会重新回到 `agent/blocked`
- worktree 不干净时 `blocked-continue` 会失败，必须先提交或 stash 所有变更
- 被拦截的文件路径会写入 `blocked_resolution_requested` marker，供 continuation prompt 引用

## 发布失败恢复

当 Agent 已完成代码修改、生成本地 commit，并且 runner 已经走到发布阶段（push、PR 创建、label 更新等）后失败时，Issue 会被标记为 `agent/failed`。此时重新运行 Agent 是浪费且可能引入不必要代码变更的。

`kc recover` 命令用于安全、幂等地完成发布收尾，无需重新启动 Agent。

### 何时使用 recover

- Agent 已执行完毕，本地 commit 已存在
- 发布阶段因网络错误、GitHub CLI 认证过期、API 限流等原因失败
- Issue 失败 comment 中包含 `kc recover --issue <number>` 提示
- 该本地 commit 已经由正常 runner 路径完成过配置启用的 pre-PR review，失败点只在 push、PR 创建或 label/comment 更新等发布收尾阶段

### 不适用的情况

- Agent 未产生任何 commit
- 工作区有未提交变更
- 需要修改 Agent 已生成的代码
- 当前分支是 base branch
- forbidden path 在 commit 阶段拦截后，由人工整理并提交了 worktree，但这些提交还没有经过 pre-PR review

> **注意**：`kc labels sync` 只同步 GitHub labels，**不**校验发布环境。`kc run` 在领取 Issue 前会检查 `[agent_runner.git].remote` 是否存在。

### 与 pre-PR review 和 post-PR supervisor 的关系

`kc recover` 只做发布收尾：校验 worktree 干净、校验分支、push、创建或复用 Draft PR、更新 Issue label 和 comment。它不会运行 pre-PR review（因为恢复路径要求本地 commit 已经由正常 runner 路径完成过 pre-PR review）。

新建 Draft PR 时，正文复用正常发布路径的 `create_draft_pr` 用例，因此和正常发布一致：内容生成（LLM 正文）+ contract anchors + validation checklist + contract block。内容生成需要内容生成器（`run_recover_command` 装配），生成失败时按 `generated_content` 配置退回 template / fallback。仅当 `get_issue` 失败、拿不到 Issue 标题与正文上下文时才退回确定性极简正文（`Recovered by issue-agent-runner.`）。复用已存在的 PR 时不会改写其正文。

**当 `post_pr_supervisor.enabled = true` 时**，成功恢复后会先进入 `agent/supervising` 并运行 post-PR supervisor；只有 supervisor `approve_for_human_review` 后，Issue 才会进入 `agent/review`。

**当 `post_pr_supervisor.enabled = false` 时**，成功恢复后直接移除 `agent/failed` / `agent/running` / `agent/ready`，添加 `agent/review`。

如果失败发生在 `Refusing to publish forbidden paths: ...` 这类 forbidden path 拦截处，并且人工已经确认这些文件可以提交、手动创建了本地 commit，应改走 `agent/running` 的本地 commit 复用路径，让 `kc run` 执行完整的 verification、pre-PR review、publish 和 post-PR supervisor：

```bash
# 1. 在对应 issue worktree 中确认已有本地 commit，且工作区干净
git status --short
git log -1 --oneline

# 2. 将 Issue 改回 running，让 run 通过本地 commit 恢复路径处理
gh issue edit <number> --add-label agent/running --remove-label agent/failed,agent/ready

# 3. 触发一次 runner 轮询；run 没有 --issue 参数，会扫描可处理的 Issues
uv run kc run
```

这条恢复路径要求 worktree 相对配置的 `{remote}/{base_branch}` 有本地 commit，且 `git status --short` 为空。若当前还有 `agent/ready` backlog，runner 会先消耗 ready 配额；必要时提高 `--max-issues`，或在没有 ready backlog 时执行。

### 使用方法

```bash
# 恢复 Issue #5 的发布
uv run kc recover --issue 5

# 如果当前分支名不包含 issue 编号，需要显式确认分支
uv run kc recover --issue 5 --branch feature-xyz
```

### 分支安全与 Issue number 边界

`kc recover` 默认要求当前分支名把 Issue number 当作**完整 token 或路径 segment** 包含在内。以下分支在恢复 Issue #42 时会被**拒绝**：

- `issue-421`（42 不是完整 segment）
- `feature/issue-420`（420 ≠ 42）
- `task-142`（142 ≠ 42）

以下分支会被**接受**：

- `issue-42`
- `feature/issue-42`
- `task-42`
- `issue_42`

如果当前分支确实不匹配但你想强制恢复，使用 `--branch` 显式确认：

```bash
uv run kc recover --issue 42 --branch issue-421
```

此时 runner 会精确比较当前分支与 `--branch` 参数，完全相等才放行。

### 恢复流程

1. 解析已存在的 issue worktree 路径
2. 校验工作区干净（无未提交变更）
3. 校验分支安全（非 base branch、分支名精确引用 issue 编号或显式 `--branch` 确认）
4. 校验配置的 remote 存在
5. Push 当前分支到配置 remote
6. 检查是否已有 open PR，有则复用，无则创建 draft PR（正文复用正常发布路径生成；见上文）
7. 发布成功 comment，记录分支、HEAD SHA、PR URL 和是否复用已有 PR
8. 更新 Issue labels：
   - `post_pr_supervisor.enabled = true`：移除 `agent/failed` / `agent/running` / `agent/ready` / `agent/review`，添加 `agent/supervising`，然后运行 supervisor
   - `post_pr_supervisor.enabled = false`：移除 `agent/failed` / `agent/running` / `agent/ready`，添加 `agent/review`

### 安全边界

`kc recover` **不会**执行以下操作：

- 运行 implementation Agent 命令或 recovery prompt（新建 PR 时的正文**内容生成**是独立于实现阶段的一次 LLM 调用，不属于此列）
- 执行 `git add` 或 `git commit`
- 创建新的 worktree
- 合并分支或删除分支
- 推送到非配置 remote

当 `post_pr_supervisor.enabled = true` 时，`kc recover` 会复用现有 supervisor repair loop，但 supervisor 本身仍然是只读审阅；需要代码修改时由现有 repair/rebase commit proxy 处理，不会由 supervisor 直接提交文件。

### 手动恢复回退

当无法使用 `kc recover` 命令、需要人工兜底时，可手动执行以下命令完成恢复。

**如果 `post_pr_supervisor.enabled = true`：**

```bash
# 1. 进入 issue worktree
cd <repo>-worktrees/tasks/issue-<number>

# 2. 确认当前分支和 commit
git branch --show-current
git log -1 --oneline

# 3. 推送分支到 remote
git push -u origin <branch>

# 4. 创建 draft PR（如果不存在）
gh pr create --draft --base main --title "[Agent] Issue #<number>" --body "Closes #<number>"

# 5. 更新 Issue labels 到 supervising（等待 supervisor 审批后再进入 review）
gh issue edit <number> --add-label agent/supervising --remove-label agent/failed,agent/running,agent/ready

# 6. 添加 comment 记录恢复结果
gh issue comment <number> --body "## Agent Runner Publish Recovered

- Branch: \`<branch>\`
- HEAD SHA: \`<sha>\`
- Draft PR: <pr-url>"
```

**如果 `post_pr_supervisor.enabled = false`（直接 review fallback）：**

```bash
# ...步骤 1-4 同上...

# 5. 更新 Issue labels 直接到 review
gh issue edit <number> --add-label agent/review --remove-label agent/failed,agent/running,agent/ready
```

## 多机部署与操作指南

`kc` 支持在单台或多台电脑上运行。以下介绍两种典型部署方式：

### 角色分工

| 电脑 | 角色 | 做什么 |
|------|------|--------|
| **A 电脑** | 任务管理端 | 写 PRD → 创建 GitHub Issue → 查看 AI 生成的 PR |
| **B 电脑** | Agent 执行端 | 常驻运行 `kc daemon`，轮询 Issue、执行 AI、提交代码 |
| **同一台电脑** | 混合 | A 和 B 的操作都在这台机器上执行 |

### A 电脑操作（任务管理端）

#### 1. 环境准备

```bash
# 克隆目标仓库和 keda CLI 项目
git clone <target-repo-url>
git clone <keda-repo-url> /path/to/keda
cd /path/to/keda

# 安装依赖
just sync

# 确保 GitHub CLI 已登录
gh auth login
```

#### 2. 初始化 Labels（只需一次）

```bash
cd /path/to/target-repo
uv run --project /path/to/keda kc init
uv run --project /path/to/keda kc labels sync
```

> `target-repo` 是你要 AI 改代码的目标仓库（不是 keda 本身）。如果已经安装了 `kc` 脚本，也可以在目标仓库中直接运行 `kc init` 和 `kc labels sync`。

#### 3. 写 PRD 并创建 Issue

```bash
# 写 PRD 文件，例如 tasks/pending/feature-login.md
# 然后创建 GitHub Issue
cd /path/to/target-repo
uv run --project /path/to/keda kc issue create tasks/pending/feature-login.md \
  --type feature \
  --agent codex \
  --ready
```

> `--agent` 可选任一已注册 agent（内置 `codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode`）以及 `auto` / `none`。`auto` 按 Issue label 自动路由，`none` 不添加 agent 路由 label。推荐在交给 runner 前保持默认 PRD 发布并加 `--ready`，确保 runner 的 base branch 能读取到已回写 Issue URL 的 canonical PRD。

#### PRD 发布边界（`--publish-prd` / `--no-publish-prd`）

`kc issue create` 默认发布 PRD（`--publish-prd` 默认开启）。传入 `--no-publish-prd` 时只创建 Issue 并本地回写 PRD，不执行 `git add`、`git commit` 或 `git push`，转而通过交互式 prompt 询问是否发布。

发布 PRD 时，命令会在 Issue URL 回写到目标 PRD 后执行 PRD-only 发布：只 `git add` 传入的 PRD 文件，只提交该 PRD 文件，然后 push 到 `config.toml` 中 `[agent_runner.git]` 配置的 remote。工作区其他未跟踪或已修改文件不会被加入这个 commit；如果 Git index 里已经 staged 了非目标 PRD 文件，命令会失败，避免把用户已有 staged changes 混入 PRD 发布 commit。

当发布 PRD 且传入 `--ready` 时，创建 Issue 的第一步不会带 `agent/ready`。只有 PRD commit push 成功后，命令才会通过 GitHub API 给 Issue 添加 `agent/ready`。如果 push 失败，命令返回失败，保留已创建但未 ready 的 backlog Issue，runner 不会领取它。

Ready 发布要求当前分支等于 `[agent_runner.git].base_branch`，因为 runner 默认从 base branch 创建 worktree。若当前分支不是 base branch，命令会失败并提示切换到 base branch 或改用 `--no-ready`。

如果 PRD 发布阶段的 Git 命令失败，例如 `git commit` 被 pre-commit hook 拦截，`kc issue create` 会在终端和日志中展示失败命令、退出码以及捕获到的 stdout/stderr，便于直接看到 hook 或 Git 返回的原始错误。

Runner 新建 issue worktree 时，默认会同步 base branch 的远程 tracking ref 作为起点，使新分支基于最新远程提交，而非可能过期的本地 base branch。复用已存在的 worktree 时，runner 会在 agent 执行前自动将当前 worktree 分支与配置的远程同名分支做安全对齐：仅当 worktree 干净且本地分支是远程分支的祖先时执行 fast-forward；本地已有未发布 commit 时保留本地状态；worktree 脏或分支已分叉时显式失败，要求人工处理，而不是自动 rebase、merge 或 reset。

#### 4. 查看结果

等待 B 电脑执行完毕后，去 GitHub 上 Review AI 生成的 Draft PR。

### B 电脑操作（Agent 执行端）

#### 1. 环境准备

```bash
# 克隆目标仓库（AI 要修改的代码仓库）
git clone <target-repo-url>
cd target-repo

# 安装 keda 项目依赖（需要 kc CLI）
git clone <keda-repo-url> ~/keda
cd ~/keda && just sync
```

#### 2. 安装 AI Agent CLI

根据你想用的 Agent，安装对应工具：

**用 Codex（OpenAI）：**

```bash
# 安装 Codex CLI
npm install -g @openai/codex

# 配置 API Key
export OPENAI_API_KEY="sk-your-openai-key"
```

**用 Claude Code（Anthropic）：**

```bash
# 安装 Claude Code
npm install -g @anthropic-ai/claude-code

# 配置 API Key
export ANTHROPIC_API_KEY="sk-ant-your-anthropic-key"
```

**用 Kimi：**

```bash
# 安装并配置本地 kimi CLI，确保 runner 可以直接执行 kimi
kimi --help
```

> 也可以把 API Key 写到 `.env` 文件里，kc 会自动加载。
> `kc` 以无人值守方式调用 Claude Code，会使用 `--dangerously-skip-permissions` 跳过文件编辑权限确认，并启用 verbose `stream-json`。runner 会过滤原始 JSON，只显示工具调用摘要、assistant 文本和最终错误。

#### 3. 单次执行（测试用）

```bash
cd /path/to/target-repo

# Dry run 预览（不实际执行）
uv run --project ~/keda kc run --dry-run

# 真正执行一次（当前仓库）
uv run --project ~/keda kc run --agent codex
```

#### 4. Daemon 常驻模式（生产用）

```bash
cd /path/to/target-repo

# 每 120 秒轮询一次（当前仓库）
uv run --project ~/keda kc daemon --agent auto
```

> 建议用 `tmux`、`screen` 或 `systemd` 保持后台运行。

**用 tmux 保持后台：**

```bash
tmux new -s kc-daemon
cd /path/to/target-repo && uv run --project ~/keda kc daemon
# 按 Ctrl+B 再按 D  detach
```

#### 5. Review Daemon（可选）

如果你希望 PR 创建后持续自动检查并维护 PR 状态：

```bash
cd /path/to/target-repo

# 每 120 秒检查一次 supervising/review Issues
uv run --project ~/keda kc review-daemon
```

### 同一台电脑运行

如果 A、B 是同一台电脑，直接合并操作：

```bash
# 1. 准备环境
git clone <keda-repo-url>
cd keda && just sync
gh auth login

# 2. 安装 AI Agent（codex 或 claude）
npm install -g @openai/codex
export OPENAI_API_KEY="sk-xxx"

# 3. 在目标仓库初始化并同步 labels（首次）
cd /path/to/target-repo
uv run --project /path/to/keda kc init
uv run --project /path/to/keda kc labels sync

# 4. 创建 Issue
uv run --project /path/to/keda kc issue create tasks/pending/xxx.md --agent codex --ready

# 5. 启动 daemon 自动执行
uv run --project /path/to/keda kc daemon
```

### 运行前检查清单

| 检查项 | 命令 |
|--------|------|
| GitHub CLI 已登录？ | `gh auth status` |
| `codex` 可用？ | `codex --version` |
| `claude` 可用？ | `claude --version` |
| `kimi` 可用？ | `kimi --help` |
| API Key 已设置？ | `echo $OPENAI_API_KEY` / `echo $ANTHROPIC_API_KEY` |
| 目标仓库路径正确？ | `ls /path/to/target-repo/.git` |

> **自动认证检测**：执行 `kc labels sync`、`kc issue create`、`kc run`、`kc daemon`、`kc review`、`kc review-daemon` 等需要 GitHub API 的命令前，`kc` 会自动检测 `gh` 认证状态。如果认证失效，会提示运行 `gh auth login -h github.com` 并以退出码 1 退出，避免暴露原始异常。
>
> 在 CI 或脚本环境中，可设置环境变量跳过该检查：
> ```bash
> KEDACODE_SKIP_GH_AUTH_CHECK=1 kc labels sync
> ```

## 配置

Agent Runner 的默认配置来自 keda 的 `config.toml`，目标仓库细节优先来自目标仓库 `.kedacode.toml`。两者使用相同的 `[agent_runner.*]` section shape；通常把通用默认值放在 `config.toml`，把仓库特定的 `git`、`runner`、`labels` 等覆盖项放在 `.kedacode.toml`。

```toml
[agent_runner]
max_issues = 1

[agent_runner.labels]
ready = "agent/ready"
running = "agent/running"
supervising = "agent/supervising"
review = "agent/review"
failed = "agent/failed"
blocked = "agent/blocked"
codex = "agent/codex"
claude = "agent/claude"
kimi = "agent/kimi"
direct_pr = "direct-pr"     # 跟着 Issue 走的直发档位声明；改名后 kc labels sync 会创建同名标签

[agent_runner.git]
remote = "origin"
base_branch = "main"

[agent_runner.worktree]
create_command = "kc worktree create --branch issue-{issue_number} --base-branch {base_branch}"
reuse_command = "kc worktree path --branch issue-{issue_number}"
path_command = "kc worktree path --branch issue-{issue_number}"
provision_database = true

[agent_runner.runner]
default_agent = "auto"
max_recovery_attempts = 5
recovery_retry_delay_seconds = 30
verification_commands = [
  "git diff --check",
  "just test",
  "uv run mkdocs build --strict",
]

[agent_runner.safety]
auto_merge = false
forbidden_path_patterns = [
  ".env",
  ".env.*",
  "secrets/*",
  "docker-compose.prod.yml",
]

[agent_runner.prompts]
default_phase = "execution"

[agent_runner.prompts.phases]
execution = [
  "Complete GitHub Issue #{issue_number}: {issue_title}",
  "",
  "Issue URL: {issue_url}",
  "Worktree: {worktree_path}",
  "{prd_line}",
  "",
  "Issue body:",
  "{issue_body}",
  "",
  "PRD map check (before coding):",
  "- The PRD predates this run; verify the file paths, symbols, and config keys it references still exist in the current worktree.",
  "- When a referenced path or interface has changed, follow the current code and adapt the plan; do not recreate structures the PRD describes that no longer exist.",
  "- When the PRD plans work that is not built yet (e.g. a file it asks you to create), implement it as specified.",
  "- In your final summary, include one line starting with `PRD map check:` listing stale references and how you adapted; write `PRD map check: none` when nothing is stale (an Issue without a PRD is trivially none).",
  "",
  "Execution rules:",
  "- Read AGENTS.md and follow repository instructions.",
  "- Only modify files inside the current worktree.",
  "- Do not merge main, delete branches, push, or create PRs; the runner handles publishing.",
  "- Do not run `git add` or `git commit`; the runner exposes a restricted commit proxy.",
  "- After finishing your changes, request a commit by writing `.agent-runner/commit-request.json` as JSON with `commit_message`.",
  "- Do not touch production systems or real business data.",
  "- Implement the requested task with focused tests and docs updates.",
  "- Finish with a concise summary, tests run, and remaining risk.",
]

[agent_runner.pre_pr_review]
enabled = true
review_agent = "auto"
repair_agent = "self"
allow_same_agent = true
max_attempts = 2
timeout_seconds = 1800
commit_request_reminder_attempts = 1

[agent_runner.post_pr_supervisor]
enabled = true
supervisor_agent = "auto"
repair_agent = "self"
max_repair_attempts = 2
max_agent_crash_retries = 5
crash_retry_initial_backoff_seconds = 30
crash_retry_max_backoff_seconds = 600

[agent_runner.daemon]
# daemon / review-daemon 的默认轮询间隔（秒），CLI --interval 可覆盖
review_interval_seconds = 120
run_interval_seconds = 120
# 每轮开头是否对账崩溃遗留的 agent/running 僵尸 attempt（续传 / 重新入队 / 判失败三出口留痕）。
# 保守判定:仅当 claim 标记的 host 是本机、且记录的 PID 已死或 claim 已老化时才处置。默认开。
reconcile_stale_attempts = true
# claim 老化阈值(秒):claim 含 started_at 且距 now 超过此值,即便 PID 仍活也视为 stale
reclaim_ttl_seconds = 10800
```

硬中断(SIGKILL / 崩溃 / 关机)不会把 Issue 从 `agent/running` 退回,而 daemon 只认领 `agent/ready`,任务会就此卡死。开启 `reconcile_stale_attempts` 后,daemon 每轮开头会对本机认领、进程已死的 running Issue 做对账:worktree 完好且可续传时回 `agent/ready` 并续上原会话,否则回 `agent/ready` 全新重跑,worktree 不可解析或恢复预算耗尽时判 `agent/failed`,三种出口都留一条对账 comment。关闭该开关即回到本特性落地前的现状。跨机器的孤儿需在同一台机器上重启 daemon 才会被回收。判定细节、幂等机制与配置优先级见前面「崩溃对账与 Agent 会话续传」一节。

配置优先级：目标仓库 `.kedacode.toml` 覆盖项 > 环境变量 > `config.toml` 全局默认值 > 代码默认值。目标仓库覆盖项只影响对应 repository context，不会改变 keda 全局设置。

Prompt 模板支持以下变量占位符：

| 变量 | 说明 |
|---|---|
| `{issue_number}` | GitHub Issue 编号 |
| `{issue_title}` | GitHub Issue 标题 |
| `{issue_url}` | GitHub Issue URL |
| `{worktree_path}` | 当前 worktree 的绝对路径 |
| `{issue_body}` | Issue 完整正文 |
| `{prd_line}` | 自动生成的 PRD 引用行（有 PRD 时提示读取，无 PRD 时给出通用建议） |

### PRD map check（执行开工前的 PRD 引用核验）

PRD 写下的时刻和执行它的时刻之间仓库还在变，PRD 点名的路径、符号、配置键可能已经失效。默认 `execution` 模板因此在 `Issue body:` 段之后、`Execution rules:` 段之前内置一段固定规则文本 `PRD map check (before coding):`（它不对应任何占位符，随默认模板分发）。

规则要求执行 agent 在动代码之前：

- 核验 PRD 引用的文件路径、符号、配置键是否仍存在于当前 worktree；
- 引用已变更时**以当前代码为准**调整路线，不重建 PRD 里已不存在的旧结构；
- PRD 计划新建、当前尚不存在的东西（例如它要求你新建的文件）**按计划实施**，不要误判为过期引用而跳过。

收尾声明：执行 agent 的最后总结里带一行以 `PRD map check:` 开头的结论，列出过期引用与适配方式；没有任何过期引用时写 `PRD map check: none`（未关联 PRD 的 Issue 同样写 `none`）。

这是**增益指令，不是门禁**：agent 未执行或核验不出结论时，不阻塞交付、不报错、不回滚，执行结果与现状一致——唯一的差别是总结里少一行声明。回退方式是把默认模板里这段规则删除；在本地配置里覆盖过 `[agent_runner.prompts.phases].execution` 的仓库完全不受影响（模板是仓库自有资产）。

## Generated Content 配置

`[agent_runner.generated_content]` 是面向人类阅读的 GitHub Issue 和 PR 内容生成配置。它与 `[agent_runner.prompts]`（实现 Agent 的任务提示词）是独立入口，不要新增 `[agent_runner.content_generation]`。

### 生成模式与回退

- `mode = "agent"`（**默认**）：用 `.format()` 渲染配置的 `prompt`，调用本地只读 agent，解析输出。
  `kc issue create`（从 PRD）、`kc issue create --from-prompt`（无 PRD）、开 Draft PR、rework-prd
  其中 Draft PR 正文默认 `draft_pr.enabled = false`，直接使用确定性正文；其余三处默认调一次 agent（超时上限
  `timeout_seconds`，默认 120 秒）。
- `mode = "template"`（**已废弃**）：跳过 agent，直接用 `.format()` 渲染 `title_template` 和
  `body_template`。仍被接受，但将在后续版本移除；模板的长期用途是下面的失败兜底。
  仓库 `.kedacode.toml` 里钉成 `mode = "template"` 的 target，加载配置时会记一条弃用警告（同一进程里
  每份配置只提示一次）；如果它是旧版 `kc init` 留下的，用 `kc config migrate` 清理
  （见「仓库本地配置」下的迁移小节）。

`output` 必须与提示词要求的回复格式一致，并且**按 target 区分**，不是全局默认：

| target | 默认 `mode` | 默认 `output` | 提示词要求的回复 |
|---|---|---|---|
| `issue_from_prd` | `agent` | `json` | 带 `title` / `body` 的 JSON |
| `issue_from_prompt` | `agent` | `json` | 带 `title` / `body` 的 JSON；产物**不含** `- PRD path:` 锚点，提示词与回退模板都不能写出它 |
| `draft_pr` | `agent` | `markdown` | Markdown，首个非空行 `Closes #N` |
| `prd_from_issue` | `agent` | `markdown` | 完整 PRD Markdown（不读取 `output`） |

错配不会报错（例如给 `issue_from_prd` 配 `markdown`，JSON 原文会被当成标题和正文）；JSON 解析失败时
日志里会有一条 `Agent output is not a JSON object` 警告。

agent 缺 `prompt`、超时、CLI 不可执行，或输出不合格（解析失败、缺下面的必需锚点），都**不会中断**
Issue 创建或 PR 发布，而是按下面的顺序落到下一级，并各记一条警告日志：

1. agent 生成的内容；
2. `fallback = "template"`（目前唯一支持的值）时，渲染 `title_template` / `body_template`；
3. 调用方内置的确定性文案（Issue 用 PRD 派生的标题与正文；PR 用
   `Closes #N` + Summary / Validation / Risk / Reviewer Notes，包含提交日志、diff stat、发布 HEAD 与发布 HEAD 中已提交的验证计划/证据报告/verifier 报告链接）。

Draft PR 默认不等待 AI 生成。需要 AI 撰写时显式设置
`[agent_runner.generated_content.draft_pr] enabled = true`；既有显式开启的配置继续生效，
不会自动迁移。历史配置如果只写了 `agent` / `prompt` 或自定义模板、未显式设置
`enabled`，升级后会继承关闭 AI 生成的默认值并使用内置事实正文；如需恢复之前的
AI 或模板生成路径，请显式设置 `enabled = true`。关闭仅影响 PR 标题/正文生成，不改变测试、review、verifier 或合并门禁。
确定性正文只引用已有事实，不推断 PASS，不替人勾选验收。链接报告的 Git tree 必须由 reviewer 核对；
未在发布 HEAD 中找到报告时明确披露缺失。自定义 `body_template` 仍被尊重，未配置时用完整内置正文。

### Issue 生成变量

| 变量 | 说明 |
|---|---|
| `{issue_type}` | Issue 类型（feature / bug / refactor） |
| `{title}` | 自动构造的 Issue 标题 |
| `{prd_title}` | PRD 文档标题 |
| `{relative_prd_path}` | PRD 相对于仓库根目录的路径 |
| `{acceptance_items}` | Acceptance Checklist 项目 |
| `{prd_text}` | PRD 完整正文 |
| `{prd_introduction}` | PRD Introduction 段落 |
| `{prd_goals}` | PRD Goals 段落 |
| `{prd_requirement_shape}` | PRD Requirement Shape 段落 |
| `{prd_change_impact_tree}` | PRD Change Impact Tree 段落 |

### PR 生成变量

| 变量 | 说明 |
|---|---|
| `{issue_number}` | GitHub Issue 编号 |
| `{issue_title}` | GitHub Issue 标题 |
| `{issue_body}` | Issue 完整正文 |
| `{branch}` | 当前分支名 |
| `{base_branch}` | 配置的基础分支名 |
| `{commit_log}` | branch 相对 base 的 commit message 列表 |
| `{commit_messages}` | `{commit_log}` 的兼容别名 |
| `{diff_stat}` | branch 相对 base 的 diff stat |
| `{git_diff_stat}` | `{diff_stat}` 的兼容别名 |

### 必需锚点

- Issue body 必须包含精确行：`- PRD path: \`<relative_prd_path>\``
- PR body 必须包含：`Closes #<issue_number>`
- PR 标题不能只是 `Closes #<issue_number>` 这类 closing 引用行：`output = "markdown"` 时
  首个非空行会被解析成标题，而约定的首行恰是 `Closes #N`；这类标题作废，回退到
  `[Agent] <issue 标题>`。

### 安全边界

- AI 内容生成是只读行为，不修改仓库文件
- 生成后若工作区变脏，视为生成失败并回退
- 生成失败不会阻断 Issue/PR 创建

### 配置示例

```toml
[agent_runner.generated_content]
enabled = true
fallback = "template"
max_input_chars = 20000
default_agent = "auto"

[agent_runner.generated_content.issue_from_prd]
enabled = true
mode = "agent"
output = "json"
# 模板只在 agent 失败时兜底
title_template = "{prd_title}"
body_template = [
  "## Summary",
  "",
  "{prd_introduction}",
  "",
  "## Canonical PRD",
  "",
  "- PRD path: `{relative_prd_path}`",
  "",
  "## Acceptance Summary",
  "",
  "{acceptance_items}",
]
agent = "auto"
timeout_seconds = 60
prompt = [
  "Generate a readable GitHub Issue from this PRD.",
  "Return strict JSON with keys: title, body.",
]

[agent_runner.generated_content.draft_pr]
enabled = true
mode = "agent"
output = "markdown"
include_commit_log = true
include_diff_stat = true
title_template = "[Agent] {issue_title}"
body_template = [
  "Closes #{issue_number}",
  "",
  "Generated by issue-agent-runner.",
]
agent = "auto"
timeout_seconds = 60
```

### PRD-from-Issue 生成变量

| 变量 | 说明 |
|---|---|
| `{issue_number}` | GitHub Issue 编号 |
| `{issue_title}` | GitHub Issue 标题 |
| `{issue_body}` | Issue 完整正文 |
| `{issue_comments}` | Issue 所有评论按时间顺序拼接 |
| `{existing_prd_text}` | 已有关联 PRD 时的现有 PRD 全文（无则为空字符串） |
| `{repo_structure_summary}` | 仓库结构摘要 |

### PRD-from-Issue 配置示例

```toml
[agent_runner.generated_content.prd_from_issue]
enabled = true
mode = "agent"
output = "markdown"
agent = "auto"
timeout_seconds = 120
include_commit_log = false
include_diff_stat = false
body_template = [
  "# PRD: {issue_title}",
  "",
  "- GitHub Issue: #{issue_number}",
  "",
  "## 1. Introduction & Goals",
  "",
  "{issue_body}",
  "",
  "## 2. Requirement Shape",
  "",
  "- **Actor**: User",
  "- **Trigger**: TBD",
  "- **Expected Behavior**: TBD",
  "- **Scope Boundary**: TBD",
  "",
  "## 3. Acceptance Checklist",
  "",
  "- [ ] Define requirements",
  "- [ ] Implement the feature",
  "- [ ] Run verification",
]
prompt = [
  "You are a technical product manager. Write a comprehensive PRD in Markdown format.",
  "",
  "GitHub Issue #{issue_number}: {issue_title}",
  "",
  "Issue Body:",
  "{issue_body}",
  "",
  "Issue Comments (chronological):",
  "{issue_comments}",
  "",
  "Existing PRD (to be rewritten if present):",
  "{existing_prd_text}",
  "",
  "Repository Structure Summary:",
  "{repo_structure_summary}",
  "",
  "Output only the PRD markdown, no extra commentary.",
]
```

## Issue Comment Event Markers

每个关键状态变化都会向 Issue 写入结构化 Markdown comment，并带隐藏 `iar:event` marker：

```markdown
<!-- iar:event version=1 phase=pre_pr_review cycle=1 head=abc123 -->

## Agent Runner Pre-PR Review

- Verdict: approved
- Reviewer: codex
- Head Before: `abc123`
- Head After: `def456`
- Verification: passed
- Findings: 0 high, 0 medium, 0 low
- Action: reviewer approved without changes
```

Marker 是幂等 cursor，不依赖本地状态文件。可读正文用于人类审计。支持的 phase 包括：

- `implementation_complete`
- `pre_pr_review`
- `draft_pr_created`
- `publish_recovered`
- `post_pr_supervisor`
- `post_pr_rework_requested`
- `rebase_repair_complete`
- `validation_passed`
- `validation_reset`

## Realistic Validation 证据门禁

PRD 的 Realistic Validation 默认**必须由执行 agent 实跑**，并由人工基于真实证据（截图/输出）签收后才允许合并 PR。完整链路：

```text
kc issue create        PRD 含 Realistic Validation 清单
                        → 物化为 <!-- iar:structured-evidence version=1 language="zh-CN" --> marker
                        → agent 解析每个 item 的格式要求（截图/pdf/txt等）
                        → 物化为 <!-- iar:evidence-format item=N kind=xxx --> marker
                        → Issue body 追加 "## Realistic Validation" 未勾清单
                        （PRD 显式声明 "Validation Waiver: <理由>" 时改为物化
                          <!-- iar:validation-waived --> marker，跳过证据要求；
                          配置 structured_evidence = false 时省略 structured marker）

agent 执行              prompt 强制要求实跑验证计划，证据写入 worktree 的
                        tasks/evidence/<prd-stem>/（无 PRD 的 Issue 兜底到
                        tasks/evidence/issue-<N>/）。git 语义由 kc init
                        provision 的 .gitignore 白名单保证：只有 *.md 文本
                        报告随 PR 进版本库，截图/录屏等原始产物被排除在
                        git 历史之外；发布前拦截仍是双保险。
                        PRD 格式约定（Change Log 条目结构、验收复选框语法、
                        分组标题、rv-id 证据命名、证据目录布局）不在 prompt
                        里复述，唯一出处是 prd skill 的 Machine Contract 章节；
                        prompt 只注入一行契约指针。解析实现也只有一份——skill
                        的 scripts/prd_contract.py；验收清单与 Change Log 由
                        kc 把文本交给该脚本、取回 JSON，kc 不再自带解析。
                        daemon 起执行循环前预检 prd skill 可解析、契约主版本在
                        受支持集合内（当前 v3/v4/v5），且该脚本与 SKILL.md 成套
                        存在；缺失、不支持版本或只装半套均 fail fast 并提示
                        安全修复方式。
                        所有 RV 脚本——截图采集、临时 server、探针，以及被
                        evidence.json command 引用的可复跑 oracle——一律留在
                        <证据目录>/scripts/，没有例外，任何 RV 脚本都不得
                        进入代码 diff。门禁按"目录前缀 + rv-<n>- 命名"两条规则
                        识别错放并打回 recovery；已提交在树里的历史违规只记
                        WARNING 日志，不阻塞交付。
                        带 iar:structured-evidence marker 的 Issue 还必须写
                        <证据目录>/evidence.json manifest，按 checklist item
                        分组描述命令、关键输出摘要、解释、风险及关联证据文件。

#### evidence.json 的 stdout_assertions（防假绿灯）

命令 exit 0 不等于检查点成立：agent 只要写 `[ -d x ] || true`、`grep ... || echo ok` 之类兜底，就能让 kc 复跑时看到绿灯，而真实断言从未执行。manifest 的每个 item 可以**可选**声明 `stdout_assertions`，由 kc 在自己复跑该命令后对真实 stdout/stderr 做子串断言：

```json
{
  "item_number": 1,
  "item_name": "健康检查真实验证",
  "command": "curl -s http://localhost:8080/health",
  "stdout_assertions": [
    {"severity": "high", "pattern": "200 OK", "source": "stdout", "must_match": true},
    {"pattern": "Traceback", "source": "stderr", "must_match": false}
  ]
}
```

- `pattern`：待匹配子串（**子串语义，不是正则**；用精确字符串，避免 `.*` 造成假阳）
- `source`：`stdout` 或 `stderr`，缺省 `stdout`
- `must_match`：缺省 `true` 表示必须出现；`false` 表示不得出现
- `severity`：仅用于失败信息标注，缺省 `high`

断言未通过时抛 `ValidationEvidenceError`，与 exit code 非零走同一条 recovery 循环，且不会被写进 RV 复跑缓存。旧 manifest（无该字段）行为不变，只校验退出码。

commit 前门禁           要求验证但证据与清单不匹配 → 进入 recovery，
                        重试耗尽后 agent/failed。

                        带 iar:structured-evidence marker 的 Issue 额外校验：
                        - evidence.json 存在且 version = 1
                        - language 与 marker 一致
                        - 每个 checklist item 有且仅有一个 evidence block
                        - 必填字段非空：item_number、item_name、command、
                          evidence_files、output_summary、explanation、risks
                        - evidence_files 中的每个文件存在，且命名匹配
                          rv-<item_number>-* 或 rv-<item_number>.*
                        - runner 计算每个证据文件的 SHA-256

                        未带 marker 的 Issue 保持原有行为：
                        - 证据目录（默认 tasks/evidence/<prd-stem>/）非空
                        - 第 n 个清单条目必须有 rv-<n>-* 证据文件
                        - Issue body 含 iar:evidence-format marker 时，
                          按 marker 的 kind 检查后缀（优先于正则匹配）
                        - 无 marker 时回退到正则关键词匹配：
                          截图/screenshot → 图片、pdf → .pdf、txt → .txt/.log、
                          word → .doc/.docx、excel → .xls/.xlsx、csv → .csv、
                          录屏/视频 → .mp4/.mov/.webm/.gif
                        逐项对账可关（关闭后仅要求证据目录非空）：
                        - 全局：validation.evidence_format_check = false
                        - 按任务：PRD 在 Realistic Validation 小节写
                          "Evidence Format Waiver: <理由>"，物化为
                          iar:evidence-format-waived marker

publish                 - diff 混入证据产物（.md 报告以外的证据路径）→ 拒绝
                          push（gitignore 白名单之外的强制加入在此兜底）
                        - PR body 末尾追加 marker 包裹的人工签收清单
                        - 证据经 git plumbing 推送到 orphan 分支
                          iar-evidence/issue-<N>（无父提交、永不合并）
                        - PR 上发证据评论：
                          - 带 structured marker：按 RV-1 / RV-2 分组展示
                            命令、证据文件、SHA-256、关键输出摘要、解释、风险
                          - 未带 marker：按文件名平铺（图片内联 blob 链接、
                            文本内联引用）

人工验收                reviewer 查看 PR 证据评论，对照清单逐项核实后，
                        直接在 PR body 点击 checkbox 打勾。
                        验收重点：命令可复现、输出摘要合理、解释成立、
                        风险说明充分、SHA-256 与本地复现结果一致。

相关 marker 一览（均为 `<!-- iar:... -->` 隐藏注释）：

| Marker | 位置 | 含义 |
|---|---|---|
| `iar:structured-evidence version=1 language="..."` | Issue body | 要求该 Issue 提供结构化 evidence.json manifest |
| `iar:validation-waived reason="..."` | Issue body | operator 显式豁免，跳过证据要求 |
| `iar:evidence-format-waived reason="..."` | Issue body | 按任务关闭逐项格式对账（证据仍必须存在） |
| `iar:evidence-format item=N kind=xxx` | Issue body | agent 解析的格式要求标记（优先于正则） |
| `iar:realistic-validation version=1 total=N` … `iar:realistic-validation-end` | PR body | 人工签收清单区块边界 |
| `iar:validation-evidence version=1 head=<sha> branch=<branch> count=N` | PR comment | 证据评论锚点（head 用于检测勾选后漂移） |
| `iar:event phase=validation_passed` | Issue comment | 人工签收完成审计（按 head 去重） |
| `iar:event phase=validation_reset` | PR comment | 签收因新 push 失效被重置 |

配置（`config.toml` 或 `.kedacode.toml`）：

```toml
[agent_runner.validation]
enabled = true                    # 关闭后整套门禁退化为不启用
evidence_dir = "tasks/evidence"   # 证据目录根；默认按任务分子目录（<prd-stem>/ 或 issue-<N>/），
                                  # .md 报告经 gitignore 白名单入版本库；显式改为 ".iar/evidence"
                                  # 则回到整目录 info/exclude 排除的 legacy 行为
branch_prefix = "iar-evidence/"   # orphan 证据分支前缀
evidence_format_check = true      # 逐项格式对账；false 退化为仅要求证据非空
parse_evidence_format_with_agent = true  # 用 agent 解析格式要求；false 只用正则
language = "zh-CN"                # 证据 prompt / PR 评论固定标签语言
structured_evidence = true        # 为新的 Realistic Validation Issue 物化 structured marker
require_negative_control = true    # 要求每项证据带 negative_control（红→绿判别力）
reexecute_commands = true          # keda 复跑每个证据项 command 确认真的通过；false 只信证据文件
reexecute_timeout_seconds = 300    # 复跑单条命令的超时秒数（命令须为自终止的检查）
reexecute_cache_enabled = true     # 工作区干净时按 HEAD^{tree} 缓存"已通过"，跳过重复复跑；脏则不缓存
verifier_enabled = true            # 开 PR 前换一个 ≠builder 的 agent 对抗复验;red 自动打回 builder,yellow 贴警告评论,green 打 validation/verifier-passed label。仅对带 iar:structured-evidence marker 且要求验证的 issue 生效
verifier_agent = "auto"            # verifier 用哪个 agent（auto=自动挑一个≠builder 的；显式指定的那个跑不起来时按 agent_fallback_order 顺延，封顶 max_agent_switches）
verifier_timeout_seconds = 1800    # verifier 运行墙钟上限；多条 RV + negative control 的 PRD 需要更大值
verifier_inactivity_timeout_seconds = 1200  # 连续多少秒没有任何输出才判卡死；与墙钟并存，让墙钟能放宽而真卡死仍被及时杀掉
frontend_visual_evidence_required = true   # 前端改动（git diff 命中 frontend_paths）强制证据含视觉文件(图片/视频);否则门禁失败。按 diff 判定,独立于 verifier
frontend_paths = ["frontend-admin", "frontend-public"]  # 判定为前端的目录前缀;runner 用它识别目标仓库前端改动

[agent_runner.labels]
validation_pending = "validation/pending"
validation_passed = "validation/passed"
```

语言配置只使用现有 TOML 配置体系（`config.toml` / `.kedacode.toml` 的 `[agent_runner.validation]`），不引入新的 `.iar/config` 文件，避免配置漂移。项目级默认语言写在 `config.toml`，单个仓库可通过 `.kedacode.toml` 覆盖。

### verifier 判定的可诊断性：`red` 与"没吐 verdict"是两件事

verifier 以 `capture_output` 运行，输出不进 stdout、也不逐行落日志。为了让阻断事后可查证：

- **原始响应落盘**：每次 verifier 跑完，完整响应写到 `<证据目录>/verifier-response.txt`（默认 `tasks/evidence/<prd-stem>/verifier-response.txt`），文件头记录 issue、verifier agent、builder sha、解析出的 risk、**是否找到 verdict marker**、响应字符数。候选因漏 marker 被替换时，另留 `verifier-response-<agent>.txt`，以免下一位候选覆盖前一位的诊断材料。写盘失败只降级为告警，不影响门禁本身。
- **无 verdict 时顺延独立候选**：缺少 verdict marker 仍 fail-closed，不会被当成通过；runner 把该候选视作 verifier 协议故障，按 `agent_fallback_order` 尝试下一个与 builder 不同的 agent。若候选池耗尽仍无可解析结果，run 以 verifier-side failure 结束，不触发 builder repair 循环，也不把无依据的发现交给 builder 修。
- **为什么必须区分**：`ValidationVerdict.findings` 总会被填入响应文本，所以"findings 是否为空"无法用来判断有没有 verdict——唯一可靠信号是 `marker_found`。混在一起时，verifier 只是漏了最后那行 marker，builder 却被指使去修一个不存在的发现，白烧一轮 attempt。
- **排查顺序**：daemon 被 verifier 挡下时，先读 `verifier-response.txt`；若里面没有任何实际发现，问题在 verifier agent（考虑用 `verifier_agent` 显式指定一个稳定的 agent，而不是 `auto`），不在被验的代码。

### verifier agent 跑不起来：顺延候选，全失败才降级阻断

`red`、"没吐 verdict"、**"agent 根本没跑起来"** 是第三种成因，三者都不能混。旧行为里 verifier 的 agent 调用没有任何兜底——agent CLI 缺失、额度耗尽或进程级失败都会把异常直接抛出 `run_verifier_gate`，整个 Issue 判 `agent/failed`。因为 verifier 是开 PR 前的必经步骤，一个被显式钉死的 verifier（如 `verifier = "codex"`）一旦额度耗尽，**所有** Issue 都会在这一步全灭，而失败信息里只有被截断的命令输出。

现在的行为：

- **顺延候选**：选定的 verifier agent 跑不起来时，按 `agent_fallback_order` 依次尝试下一个候选，封顶 `max_agent_switches`（与 builder 换 agent 共用同一套配置与预算，不新增开关）。候选池始终排除 builder——独立性靠"换 model 即换判定视角"保证，显式声明的 `verifier` 仍是首选，只在它跑不起来时才让位。每次候选都重新取证据快照，换 agent 重跑时起点干净。
- **超时不参与顺延**：`TimeoutExpired` 仍按运行事故处理（不伪造 verdict、也不换 agent），与上一节语义一致。
- **候选耗尽时停止**：所有候选都跑不起来或无法给出可解析 marker 时抛 `VerifierUnavailableError`。错误说明这是 **runner/agent 侧故障、不是被证实的代码缺陷**（"do not invent fixes for findings that do not exist"）；当前 run 结束并将 Issue 标为 failed，避免相同 builder recovery 轮次反复重试同一组失效 verifier。
- **额度措辞要认全**：provider-capacity 判定曾要求 `usage limit exceeded|reached`，认不出 codex 的 `You've hit your usage limit` —— 同一句额度耗尽因此既不算 capacity（builder 路径只重试不换 agent）也不触发顺延。现在裸 `usage limit` 与 `insufficient_quota` 都算，重置时刻同时支持 Claude 的 `resets at <ISO>` 与 codex 的 `try again at Oct 5th, 2026 10:24 AM`。

**排查顺序**：daemon 报 "independent verifier could not run" 时看 `Tried agent(s)` 与 `Last failure`；若是额度问题，换一个有余量的 verifier agent（或改 `verifier = "auto"`）再重跑，不必怀疑被验代码。

### verifier 超时：墙钟与静默期两条线

verifier 拿到的任务是"自己复跑真实入口 + 逐个跑 negative control"。RV 条目多、且带 E2E 的 PRD，这份工作量动辄超过半小时，而 builder 侧的墙钟是 `runner.timeout_seconds`（默认 14400）。只有一条墙钟线时，"复跑 E2E 的慢 verifier"和"彻底卡死的 verifier"无法区分：想让前者跑完就得把墙钟拉长，而拉长同样惠及后者。

- `verifier_timeout_seconds`：墙钟上限，按最慢的真实工作量给足。
- `verifier_inactivity_timeout_seconds`：连续无输出多久判卡死，语义与 builder 侧 `runner.inactivity_timeout_seconds` 相同。两条线并存后墙钟可以放宽，真卡死仍在静默期结束时被杀。

超时按**运行事故**处理，不伪造 verdict：

- 被杀前收到的部分输出写进 `verifier-response.txt`，文件头标 `TIMED OUT — no verdict`。否则"跑了半小时被杀"在磁盘上什么都不留。
- Issue 的失败评论只贴命令名 + 超时秒数 + 部分输出尾部。`subprocess.TimeoutExpired.__str__` 会把整条命令原样拼进消息，而 agent 命令里带着完整 prompt——以前一次 verifier 超时会把几千行 prompt 贴进评论，真正有用的信息反而被埋掉。
- 超时不进 builder 的 recovery 循环（那是 `red` 的语义）：Issue 判 `agent/failed`，人看清原因后再 relabel 重跑。

### verifier 复跑不得覆盖 builder 的最终树证据

verifier 能跑的就是 builder 写进 manifest 的那些 capture 脚本，而脚本会把输出写回同一批 `rv-*.txt`；跑 negative control 时它还会故意把代码改红。于是 verifier 一跑，已经通过门禁的证据就被它自己的复跑结果覆盖，被超时杀掉时更糟——证据目录停在最后一个 negative control 的破坏态，而门禁早就通过了，这份自相矛盾的证据会照原样发布给人审。

因此 verifier 启动前对 `<evidence_dir>` 做一份临时快照（落在系统临时目录，不进 worktree），verifier 结束后（拿到 verdict 或抛异常都一样）把被改写、被删除的文件恢复回 builder 版本，恢复清单记进日志。verifier 自己的产物（`verifier-response.txt`）不在恢复范围内；verifier 新建的其他文件保留——删掉别人刚写出来的文件比留下它风险更大，而新文件本身就是"verifier 动过证据目录"的可见线索。

### 前端改动强制真实视觉证据（fail-closed）

当目标仓库本轮 git 变更命中 `frontend_paths` 前缀（默认 `frontend-admin/` 与 `frontend-public/`）时，证据目录（默认 `tasks/evidence/<prd-stem>/`）第一层**必须至少有一个视觉证据文件**（图片 `.png/.jpg/.jpeg/.gif/.webp` 或视频 `.mp4/.mov/.webm`），否则证据门禁抛 `ValidationEvidenceError`，与其余门禁一样进入既有 recovery，不放行发布。

要点：

- **判定按 diff，不按清单文本**：即使某条 Realistic Validation 条目文字没写"截图/png"，只要改动碰了前端目录就要求视觉证据——覆盖"前端条目文本不含格式关键字导致逐项检查漏判"的盲区。
- **独立于 verifier**：`verifier_enabled = false` 的仓库也照样受本门禁保护；它是确定性机械门禁，与对抗复验互补。
- **默认开、可按仓关**：新键默认 `true`，既有 `.kedacode.toml`（无此键）升级后自动获得该门禁；确需关闭在 `[agent_runner.validation]` 写 `frontend_visual_evidence_required = false`，前端目录命名不同则用 `frontend_paths` 覆盖。
- **只拦不产**：本门禁只保证"缺视觉证据即拦下"，不负责替你把证据跑出来；让 runner 环境能跑前端 e2e 属容器化能力。

### Structured evidence manifest 格式

带 `iar:structured-evidence` marker 的 Issue 必须在 `<证据目录>/evidence.json`（默认 `tasks/evidence/<prd-stem>/evidence.json`）提供如下 manifest：

```json
{
  "version": 1,
  "language": "zh-CN",
  "items": [
    {
      "item_number": 1,
      "item_name": "Run worktree preparation 真实验证",
      "command": "uv run pytest tests/test_run_agent.py -k \"worktree_reconcile\" -v",
      "evidence_files": ["rv-1-worktree-reconcile.txt"],
      "output_summary": "pytest 目标用例通过，输出显示 run_once 在 agent 执行前完成远程分支对齐。",
      "explanation": "该用例使用真实 Git 仓库与裸远程，覆盖 remote-tracking ref 与 fast-forward 判定，因此能证明工作树准备路径生效。",
      "risks": "GitHub 与 agent 边界为 fake；该证据不证明 live GitHub API 可用。"
    }
  ]
}
```

规则：

- `version` 必须为 `1`。
- `language` 必须等于 Issue marker 与 config 中的语言。
- `items` 必须覆盖 Realistic Validation checklist 的全部 item，每个 item 出现一次。
- 每个 item 必填字段：`item_number`、`item_name`、`command`、`evidence_files`、`output_summary`、`explanation`、`risks`。
- `evidence_files` 可有多个文件；每个文件必须存在于证据目录（默认 `tasks/evidence/<prd-stem>/`），且文件名匹配 `rv-<item_number>-*` 或 `rv-<item_number>.*`。**条目只写纯文件名**（`"rv-1-run.txt"`），不带 `tasks/evidence/<prd-stem>/` 等目录前缀——存在性按 `evidence_dir / file_name` 解析；这与同一 manifest 中 `expected_artifacts[].path` 使用 worktree 相对路径的约定相反，是历史上 agent 最容易写错的一处。写成带前缀的路径时 runner 会剥掉目录并 warning 放过，不再判红。
- `command` 必须是可独立复现、自终止的检查命令。如果命令涉及多行 Python 或复杂 setup，应将其落到 `<证据目录>/scripts/` 下的独立脚本并在 `command` 中引用；避免把内联 `python -c "..."` 写进 manifest，否则 runner 复跑时难以维护，也容易被 keda 判定为不可复现。**所有 RV 脚本一律放 `<证据目录>/scripts/`，不存在可提交到代码树的例外**；每次复跑覆盖的是证据目录下的证据产物，不是脚本本身。
- 证据分支会连同 `<证据目录>/scripts/` 下的 oracle 源码一起上传（树形因此可含 `scripts/` 子目录，PR 评论中的条目名带相对路径前缀），审阅者能读到产出证据的断言本身。也因此 **oracle 脚本同样不得含密钥**。
- 复跑缓存键并入 oracle 目录的内容摘要：命令字符串不变但脚本被改写时，缓存必然失效并真实重跑，不会用旧 oracle 的结论蒙混。
- runner 在渲染 PR comment 时重新计算每个证据文件的 SHA-256，展示短 hash 与完整 hash。

### Reviewer 验收流程

1. 在 PR evidence comment 中按 `RV-1 / RV-2` 找到对应 checklist item。
2. 复现 `可复现命令`，确认输出与 `关键输出摘要` 一致。
3. 阅读 `为什么能证明该检查点成立`，判断解释是否合理、无逻辑跳跃。
4. 阅读 `潜在风险 / 不适用说明`，确认已知边界已被披露。
5. 本地计算证据文件 SHA-256，与 comment 中 runner 计算的 hash 核对。
6. 全部确认后在 PR body Realistic Validation checklist 中勾选对应项。

**branch protection 配置（一次性，operator 手动）**：GitHub 仓库 Settings → Branches → 对 `main` 添加/编辑 protection rule → Require status checks to pass → 勾选 `Realistic Validation sign-off`。配置后未全勾的 PR 物理无法合并；在 PR body 点勾会触发 `pull_request: edited` 事件自动重跑 check。

注意事项：

- 私有仓库中证据评论的内联图片可能不渲染，点击评论中的 `Open image` / `Open file` 链接进入 blob 页查看。
- 证据分支与代码历史零共同祖先（`git log iar-evidence/issue-<N>` 只有一个无父提交），永不合并；Issue 关闭后由 daemon 轮询清理。
- 其他目标仓库使用该能力时，需要把 `validation-gate.yml` 复制到该仓库的 `.github/workflows/` 并配置 required check。
- PRD 侧规范：Realistic Validation 默认必做；只有 operator 确认的 PRD 才允许在 `### Realistic Validation` 小节写 `Validation Waiver: <理由>` 行，`kc issue create` 会将其物化为豁免 marker。同一小节写 `Evidence Format Waiver: <理由>` 行则只关闭该任务的逐项格式对账（证据仍必须存在），物化为 `iar:evidence-format-waived` marker。

## 安全边界

- `auto_merge` 固定为 `false`，不会自动合并 PR
- `kc labels sync` 只同步 GitHub labels，不校验发布 remote；`kc run` 在领取 Issue 前会校验 `[agent_runner.git].remote` 必须存在，不存在时直接失败并列出当前可用 remote
- 发布变更前会检查 `forbidden_path_patterns`，拒绝匹配的文件变更
- Agent 执行在隔离 worktree 中进行，不影响主工作区
- Agent 不直接执行 `git add` 或 `git commit`；完成修改后写入 `.agent-runner/commit-request.json` 请求 runner 在 host 侧提交
- `commit-request.json` 必须提供 `commit_message`；pre-PR reviewer 可额外提供 `verdict`、`summary` 和 `findings_*` 元数据作为空提交兜底。runner 会校验当前 branch 未变化、删除请求文件、检查 `forbidden_path_patterns`，再执行 `git add -A` 和 `git commit`
- 不同仓库应在 `verification_commands` 中配置自己的验证命令，例如 `just test`、`npm test`、`pnpm lint` 或 `make test`
- runner 会在提交前先运行一次 `verification_commands`；发现未提交变更并执行 `git add -A` 后，会再次运行同一组验证命令，覆盖依赖 staged 状态的 commit hook 或测试标记
- 如果验证过程结束后工作区还剩下任何 `git add -A` 仍会 stage 的内容，runner 会在安全路径校验后补一次 `git add -A`，避免 `.last_tested_commit` 指向 working tree 而 commit hook 检查到过期 staged tree。探测口径（`_verification_left_unstaged_worktree_changes`）覆盖两类残留：formatter / lint 自动修复的**已跟踪**文件（`git diff --quiet`），以及门禁自己生成、**未跟踪且未被 gitignore** 的新文件（`git ls-files --others --exclude-standard`，如新增快照或 golden 文件）。第二类曾是 `check-test-flag` 硬失败的盲区：`just test` 写标记用的路径集包含未跟踪未 ignore 的文件，而 commit 钩子比对 staged 树，只补 `git add -u` 时这些新文件永远进不了索引，钩子会给出「请运行 `git add -A` 归一」——恰好是 runner 漏做的那一步。判定与首次入索引的 `git add -A` 同口径，被 gitignore 的构建产物两边都看不见，因此不会被卷进提交
- Agent CLI 非零退出或任一验证失败时，runner 最多按 `max_recovery_attempts` 重新调用同一个 Agent；每次 recovery 前会等待 `recovery_retry_delay_seconds` 秒，并把失败摘要以及失败命令的 exit code、stdout、stderr 放入 Fix Agent / Recovery Agent prompt；首次实现 prompt 也会预先列出完整的 `verification_commands` 并提醒检查项目规范，让 Agent 在写代码阶段就了解交付门禁。Agent 修复后仍只能写 commit request，不能直接提交
- Runner 通过 `classify_failure` 对每次尝试进行分层失败识别，覆盖 `UNCOMMITTED_CHANGES`、`NO_COMMITS`、`VERIFICATION_FAILED`、`AGENT_ERROR`、`UNRECOVERABLE` 等类型；不可恢复错误（如安全路径拦截）会立即终止 retry loop
- 每轮尝试的结果都会记录在 `AttemptResult` 中，包含执行 agent、起止时间、耗时；runner 会实时把结果写入本地 SQLite `attempt_records` 表，再用 `IRunHistoryStore.list_issue_attempts` 读回该 Issue 的完整轨迹（跨 agent、跨 claim）渲染 GitHub Issue 上带 `<!-- iar-attempt-history -->` marker 的增量评论。内存中的 attempt 列表每次换 agent 或重新 claim 都会从 1 重新计数，只能代表本轮，因此不作为渲染源（仅在没有配置 SQLite 存储时兜底）。最终失败评论中的「Attempt History」表格展示 attempt_number、started_at、agent、failure_type、recovered、duration、detail，便于人工 review 时追踪 Agent 的修复轨迹；由于 attempt_number 只在本轮内递增，整表会混排多轮（例如 `claude 1..6` 后紧跟 `kimi 1..2`），因此表格用 **Started (UTC)** 列给出时间锚点（`YYYY-MM-DD HH:MMZ`；缺失渲染为 `-`，无法解析则原样回显），并在表格下方固定附一行说明"编号在每次换 agent 与每次重新 claim 时从 1 重新开始"，避免被误读成编号错乱；Detail 列取每次失败输出的最后一行有效内容（实际报错几乎总在末尾），而不是从头截断的样板文字
- 失败评论会识别已知错误签名：命中 Claude API 用量限额（429 / usage limit）时，在评论顶部输出加粗的 Root cause 摘要并带上限额重置时间；`CalledProcessError` 的命令回显只保留命令名（如 `claude`），不会把完整 agent prompt 打进评论
- 如果 Agent 没有产生任何新 commit 且工作区也没有未提交变更，runner 仍会将 Issue 标记为 `agent/failed`
- Pre-PR reviewer 的修改同样必须通过 `verification_commands` 才能发布
- Post-PR supervisor 的 rebase 操作使用 `--force-with-lease` 且仅作用于 PR branch，不会推送 base branch
- 自动化 rebase 前会校验 HEAD 和 branch 名称，发现不匹配时中止，防止误操作
- rebase 遇到冲突时，runner 会调用 agent 进入有限次数的冲突解决循环（复用 `max_repair_attempts`）；agent 修改冲突文件并通过 commit proxy 提交后，runner 重新尝试 `git rebase --continue`；耗尽后安全 abort 并转人工

### PRD-backed Issue 的强制 Closeout

当 Issue body 中包含 `PRD path: \`tasks/pending/xxx.md\`` 时，runner 成功路径会强制完成 PRD closeout：

1. **Prompt 引导**：`build_prompt()` 从 `config.toml` 的 `[agent_runner.prompts.phases]` 模板渲染 prompt，默认模板会：
   - 列出 runner 将要执行的 `verification_commands`，让 Agent 在写代码阶段就了解交付门禁。
   - 提醒 Agent 在请求 commit 前检查项目规范（AGENTS.md、命名、依赖方向、文件编码、行长度限制等）。
   - 明确区分 PRD 的 `Change Log` 与 `Acceptance Checklist`：实现中可演进 PRD，但每次变更必须追加结构化 Change Log（类型、原文、变更后、原因、影响、审核）；Checklist 只表示已真实执行并留存证据的验收状态。归档动作由 runner 独占，规则必须双向理解：Agent 不得自行 `git mv` 到 `tasks/archive/`，**且 runner 归档之后任何人都不得把 PRD 挪回 `tasks/pending/`**。
   - `build_fix_prompt()` 在 Fix Agent 阶段给出当前 verification 失败输出以及完整 verification 命令列表，约束 Agent 只修导致失败的代码/测试，并提醒检查项目规范。
   - `build_recovery_prompt()` 在 recovery 阶段给出 failure summary 和原始 verification 失败输出，并给出同样的 closeout 与规范检查提醒。
   - `build_progress_continuation_prompt()` 在跨 claim 续作时附带上一次失败的 failure summary 和 verification 输出，并提醒检查项目规范。
2. **提交前 Delivery Gate**：runner 在 `publish_changes()` 之前执行 PRD delivery gate：
   - 无 PRD path：跳过 gate，保持现有行为。
   - PRD 仍在 `tasks/pending/`：归档只看**执行侧**条目（`Human-Confirmed` 组之外的条目，prd skill Machine Contract v5）。执行侧有未勾条目即阻塞提交（`CHECKLIST_UNCHECKED`，报错点名条目）；本轮 PRD 内容若相较执行开始时发生变更，须有完整结构化 `Change Log`；验收状态横幅须与清单一致（见下）。全部满足后 runner 执行 `git add -- tasks/pending/<name>.md` 与 `git mv tasks/pending/<name>.md tasks/archive/<name>.md`——**不论 `Human-Confirmed` 组是否还有空框**。归档不改 PRD 内容：人审空框与 🧍 横幅原样保留，留给人在 PR 上回答。
   - PRD 已在 `tasks/archive/`：同样校验 Change Log、执行侧条目与横幅；人审空框不拦交付与发布。
   - **横幅一致性**：skill（v5 起）在契约 JSON 里报告横幅状态。`Human-Confirmed` 组仍有 `- [ ]` 时横幅必须是 `🧍 **验收状态**：待人工验收`（`awaiting_human`），一个都没有时必须是 `✅ **验收状态**：已验收`（`accepted`）；`⬜ 未开工` 与横幅缺失都算不一致。不一致时不归档、不发布，抛 `ACCEPTANCE_BANNER_MISMATCH`，可交给 Closeout Agent：只改横幅这一行并追加一条 Change Log，不得勾选、取消或改写任何复选框。本机 skill 仍是 v3/v4（不报告横幅）时跳过这项检查，只按执行侧判据归档。横幅文本由 skill 解析，keda 不另写横幅解析。
   - **发布前检查**（`push_changes(require_prd_archived=True)`，正常交付路径）：PRD 必须已在 `tasks/archive/`，且执行侧条目与横幅校验通过；仍在 pending（包括带人审空框的情形）一律拒绝。两条既有例外不变：recovery 耗尽后的失败 Draft PR 与 PRD rework 提案传 `require_prd_archived=False`，二者都不调用交付检查，也都不归档——失败交付永不归档。
   - PRD 文件不存在、archive 目录缺失或 `Acceptance Checklist` section 缺失：进入 recovery loop，重试耗尽后标记 `agent/failed`。
3. **归档时机**：`git mv` 发生在 `git add -A` 之前，归档随实现一起提交，包括仍待人工验收的 PRD。**已归档 ≠ 已验收**：归档只记录执行侧交付完成，验收与否看横幅与 `Human-Confirmed` 组。人在 PR 上确认后勾选人审项、把横幅改为 ✅ 已验收（同一个 PR 的后续提交，或合并后补记；合并时自动补记是后续 PRD）。合并队列对人审未答的 PRD（不论在 pending 还是 archive）一律跳过等人，见"7 步门禁链"下的 PRD hold。
4. **Pre-PR Review 与归档的关系**：`build_prd_review_reference()` 读取 worktree 中实际存在的 pending 或 archive 路径。runner 已归档的 PRD 不得被 agent 或 reviewer 挪回 pending；人工验收驳回后是否重开 PRD 由人决定。

> **注意**：runner 不会只因 PRD 的 checkbox 而信任业务完成度；Realistic Validation 与独立 verifier 仍需提供实际证据。Change Log 解释需求为何演进，不能替代任何验收项或证据。

## FastAPI 状态端点

Agent Runner 同时暴露只读状态端点：

- `GET /api/v1/agent-runner/status` — 返回 runner 配置摘要与仓库列表
- `GET /api/v1/agent-runner/health` — 返回 runner 健康状态（GitHub CLI 可用性等）

## Agent Runner 日志与 Issue 上下文

Agent Runner 在启动和结束一次 agent 执行时，会显式记录当前处理的 GitHub Issue 编号和完整 URL：

```text
Starting agent for Issue #23: https://github.com/ZataZhang/fsense/issues/23
Agent finished for Issue #23: https://github.com/ZataZhang/fsense/issues/23 (exit_code=0)
```

对于运行时间超过心跳阈值（默认 60 秒）的长命令，process runner 的 watchdog 心跳日志也会携带 Issue 上下文：

```text
Claude stream (Issue #23: https://github.com/ZataZhang/fsense/issues/23) still running after 60s: claude --dangerously-skip-permissions ...
```

这样即使命令摘要因 `_summarize_command` 的 240 字符限制被截断，Issue URL 仍以独立字段完整保留，便于从日志直接定位当前处理的 Issue。

### 实现要点

- `IProcessRunner.run` 新增可选 `label` 参数，例如 `"Issue #23: https://github.com/..."`。
- `run_agent_once.run_agent_with_prompt` 在有 `IssueSummary` 时记录启动/结束日志，并把 `issue.number` 与 `issue.url` 作为 `label` 传给 process runner。
- `_ProcessWatchdog` 将 `label` 附加到原有 base label 之后，无 `label` 时保持原有日志格式不变。

## Agent 调用记录与停滞诊断（Invocation Tracing）

**入口没有变化**：仍然是 `kc logs --repo <path> --issue <N> [--follow]`，没有新命令、新旗标、新 JSON 汇总或新页面。变的只是这条流里多了三种结构化标记行。

### 为什么需要它

一次无 PRD 的重命名任务跑了约 9 小时、经历多轮 recovery，最终没有发布 PR。事后复盘发现两个盲区：

1. **日志有活动 ≠ 有交付**。Agent 一直在输出、进程一直活着、心跳一直在打，看起来"在干活"，但没有任何一次调用真正推进到发布。
2. **模型只在显式绑定时才被记录**。preset 之外的调用完全看不出实际用了哪个模型；换执行器回退后，"请求的模型"和"执行器自报的模型"更是无从区分。

调用记录给每一次**真实的顶层 Agent 子进程调用**一个唯一身份和一对起止事件，让"这段时间到底起了几次进程、每次谁在跑、跑成什么样"变成可核对的事实，而不是从相邻文本推断。

### 三种标记行

| 标记 | 出现时机 | 读法 |
|---|---|---|
| `[iar-invocation-start]` | 一次真实子进程调用**即将启动**（argv 已组装完成） | 这次调用存在了；后面必然有一条同 `invocation=` 的结束标记，或一个可解释的缺口 |
| `[iar-invocation-end]` | 同一次调用返回、抛异常或超时被杀之后 | 这次调用的结局、耗时与模型事实 |
| `[iar-invocation-coverage-incomplete]` | 观测事件写库失败、账本不可用或日志定位越界时（每个 run 最多一条） | **本次 run 的调用清单可能不完整**；业务结果不受影响，但不要拿这份清单当完整证据 |

起止标记共用同一段身份前缀，因此并发场景下靠 `invocation=` 归属，不靠"上下相邻"推断：

```text
[iar-invocation-start] invocation=inv-3f9a1c7b2d40 run=keda-main#issue-242#20261008T015223Z-a91f4c0b7e33 issue=242 attempt=1 phase=implementation role=implementer executor=claude model_requested=claude-sonnet-4-5 retry_of=- retry_reason=- log=agent-runner/issues/keda-main/issue-242-20261008-015223.log
[iar-invocation-end] invocation=inv-3f9a1c7b2d40 run=keda-main#issue-242#20261008T015223Z-a91f4c0b7e33 issue=242 attempt=1 phase=implementation role=implementer executor=claude outcome=ok exit_code=0 duration_s=1843.207 model_requested=claude-sonnet-4-5 model_reported=claude-sonnet-4-5-20250929 model_source=executor_report retry_of=- failure_category=-
```

身份字段：

| 字段 | 含义 |
|---|---|
| `invocation=` | 单次真实进程调用的唯一 id（`inv-` + 12 位十六进制），起止配对用它 |
| `run=` | 本次 Issue run 的身份：`<repo_id>#issue-<N>#<UTC 时间戳>-<随机 12 位十六进制>`。**不依赖 PRD**，无 PRD 的 Issue 同样有 |
| `issue=` | GitHub Issue 号 |
| `attempt=` | runner 侧的尝试序号；无尝试语义的调用（如 Fix Agent）为 `-` |
| `phase=` | 调用发生在哪个阶段，闭集：`implementation` / `fix` / `review` / `review_repair` / `verification` / `verification_recovery` / `rebase_recovery` / `closeout` / `supervisor` / `supervisor_repair` / `content_generation` / `unspecified` |
| `role=` | 该阶段承担的角色（`implementer` / `fixer` / `reviewer` / `verifier` / `supervisor` / `content_generator` / `unspecified`），由 `phase` 派生 |
| `executor=` | **实际执行**这次调用的 agent 名（回退后就是回退到的那个，不是配置里原本想要的那个） |

所有字段一律 `key=value` 且不含空格，缺失统一渲染成 `-`（`model_requested=` / `model_reported=` 例外，见下文），因此可以直接 `grep -o 'phase=[^ ]*'` 之类逐字段取值。

### 模型三态：请求的、自报的、来源

三个字段分开记，任何一环拿不到就如实留空，**绝不从当前配置反推回填**：

| 字段 | 取值 |
|---|---|
| `model_requested=` | 本次调用下发的模型；没有下发时显示 `未下发` |
| `model_reported=` | 执行器从自己的输出流里自报的模型；没报时显示 `未提供` |
| `model_source=` | 只有两种：`executor_report`（自报拿到了）或 `unknown`（没拿到） |

因此下面三种情况都会**停在 unknown**，这是设计而不是缺陷：

- 执行器没有自报模型（协议不产出、或输出被截断）；
- 换执行器回退时 preset 的模型绑定被丢弃（执行器变了，原绑定不再适用）；
- 本特性落地前的历史记录（当时根本没采集，事后补不出来）。

超时或被杀的调用仍可能带 `model_reported=`：执行器在崩溃前已经吐出过自报模型，那个事实会挂在异常上一并被记录。

### 结局与失败分类

`outcome=` 是闭集，`failure_category=` 只在非 `ok` 时有值：

| `outcome` | 含义 | 对应 `failure_category` |
|---|---|---|
| `ok` | 进程正常退出且退出码 0 | `-` |
| `failed` | 进程退出了但退出码非 0 | `nonzero_exit` |
| `timeout` | 墙钟超时或静默期超时被杀 | `timeout` |
| `error` | 进程没能正常收尾（agent 不存在、OS 错误、运行时异常） | `agent_unavailable` / `os_error` / `runtime_error` / `unknown` |

失败分类**只按异常类型判定，绝不读异常文本**——这是防止把提示词、密钥或 Agent 自由输出带进账本的结构性保证。整条标记行与整份 `detail_json` 只可能出现：闭集枚举、自生成的 id、整数退出码，以及经白名单校验的模型 / agent / session 字符串。

`duration_s=` 用单调时钟算墙钟耗时，不采信执行器自报的 `duration_seconds`（那是子进程视角，合成结果里恒为 0）。

### 重试与换执行器：永远是两条记录

原地瞬态重试、会话续传没起跑后的重跑、换执行器回退——三种都是**新的一次真实进程调用**，各自新建 `invocation=`，并用 `retry_of=` 指向上一次、`retry_reason=` 说明为什么：

| `retry_reason` | 触发场景 |
|---|---|
| `transient_failure` | 判定为瞬态错误后的原地重试 |
| `resume_not_started` | 请求了会话续传但 argv 组装不出续传形态，退回全新会话重跑 |
| `executor_fallback` | 换执行器（`agent_fallback_order` 前进、reviewer / supervisor 候选前进） |

"重试了三次才成功"因此在日志里是四条记录（三次失败 + 一次成功）串成一条链，不会被折叠成一条。没有 `retry_of` 的调用显示 `-`。

### 诚实解读缺口：`unclosed` 与 `incomplete` 不是一回事

只有 start、没有对应 end 时，**不要**替它编一个终态。区分两种读法：

- **`unclosed`（未闭合）**：默认读法。只能说明"结束事件没落盘"，可能是进程还在跑、可能是 daemon 被 `kill -9`、也可能是写库降级。此时 `finished_at` 与 `duration_seconds` 保持空。
- **`incomplete`（已中断）**：只有在**另外确认了进程已退出**（例如 claim 里的 PID 已不存活）之后才能这么读。这说明调用确实被打断了，而不是还在进行。

判断进程是否真的活着，仍然读现成事实：claim comment 里的 `pid=`、`kc logs --issue` 是否还在增长、worktree 现场文件状态。调用记录提供的是"起了几次、每次谁跑的"，不替你回答"现在活着吗"。

### 覆盖范围（与不覆盖的部分）

**在范围内**：implementation、fix、review、review_repair、independent verification 及其 recovery、rebase recovery、closeout、supervisor 及其 repair、启用状态下的 generated content。凡是走 `run_agent_with_prompt` 这个唯一真实子进程边界的顶层调用都被覆盖；content generation 走独立入口，单独记为 `phase=content_generation`。

**不在范围内**（读到空不要误判成"没跑过"）：

- **Agent 内部自己 spawn 的子 Agent**（例如 Claude 的 Task 工具）。事件里的 `internal_agent_coverage` 恒为 `unobserved`，这是显式披露而不是遗漏：我们只观测自己起的那一层进程。
- **跨机聚合**。账本是本地 `~/.kedacode/console.db`，A 机起的调用不会出现在 B 机的清单里。
- 合议（`kc deliberate` / transcript 路径）、`kc ask` 的 planner、`kc agent doctor`、REPL、idea 草稿、PRD 建 Issue、Phase 1 PRD rework——这些路径没有绑定 Issue run 的观测上下文，整体退化为 no-op，业务行为与接入前逐字节一致。

**无 PRD 的 Issue 同样被覆盖**：`run=` 的身份由 repo + Issue 号 + 时间戳构成，不读 PRD，因此"没有 PRD 所以没有调用记录"这种情况不存在。

### 旁路语义：观测永远不改变业务结果

写库失败、账本对象不具备该能力、日志定位越界——全部降级为一条 warning 加一次 coverage 标记，然后**照常返回业务结果**。Agent 成功就是成功，不会因为观测没记上而被判失败；反之亦然。

### 落盘位置

事件写入 `~/.kedacode/console.db` 的 `agent_invocation_events` 表（schema v9，附加式迁移：v8 旧库打开新代码自动补表，既有行与列值原样保留）。每条事件按 `event_key = <invocation_id>:<event_type>` 幂等，重放不产生重复行。日志标记本身仍落在既有的 per-Issue 日志文件里，`log=` 字段给的是相对 `logs/` 的定位串，可直接拼回绝对路径。

### 停滞时怎么办：用现成的恢复路径

调用记录帮你**看清**发生了什么，不替你决定要不要动手，也不引入任何新阈值。确认一次 run 确实没有推进后，走的仍是既有路径：

| 情况 | 既有路径 |
|---|---|
| 发布环节失败（代码是好的，push / PR / 状态没写成） | `kc recover --issue <N>` |
| 需要重跑 | 修好原因后把 label 改回 `agent/ready`，下一轮 pass 领取 |
| daemon 死了留下僵尸 attempt | 每轮 tick 开头的崩溃对账自动处置（`resume-session` / `re-enqueue` / `mark-failed`），见「崩溃对账与 Agent 会话续传」 |
| 需要人决策（如 forbidden path） | 处理原因后 `kc blocked-continue --issue <N>` |

### 显式非目标

- **不设停滞阈值**：没有"超过 N 分钟无输出即判停滞"这类新配置项。
- **不做停滞判定程序**：不提供任何自动判定"这次 run 卡住了"的代码路径。
- **不做自动接管**：不会因为读到缺口就自动重跑、自动换执行器或自动关 Issue。
- 不引入外部平台 / dashboard / OpenTelemetry，不做模型评分排名或自动选型，不采集完整提示词或任何密钥。

### 实现要点

- `core/use_cases/agent_invocation_tracing.py` 是唯一事实源：标记常量、闭集枚举、身份渲染、事件写入、时间线解读都在这里。
- 观测上下文用 `contextvars` 绑定（`bound_invocation_trace_context`），因此并行 daemon 的每个 worker 各有一份，天然隔离；未绑定时所有函数整体 no-op。
- 账本能力用鸭子类型探测（`resolve_invocation_store` 检查 `append_invocation_event` / `list_invocation_events`），不给既有构造器加参数，观测不泄漏进业务调用链。
- `start_invocation` / `finish_invocation` 在 `run_agent_once._invoke` 的 try/except 两侧成对调用，异常原样上抛，不改变失败语义。
- 执行器自报模型由输出协议解析（`infrastructure/agent_stream_usage.py` 的 `extract_reported_model_event` / `parse_reported_model_from_plain_stdout`），只认事件顶层的 `model` 字符串。

## Agent Runner Monitoring Dashboard

Dashboard 路由 `/app/dashboard`（即 `frontend-public/app/(app)/app/dashboard/page.tsx`）展示 Agent Runner 的监控视图。运维者打开 Web 就能看到当前队列、PR 状态、事件时间线和异常。监控 API 本身保持只读；写操作（重试 failed、继续 blocked、启停 runner 进程等）由独立的管理终端 API 承载，见下文「Agent Runner 统一管理终端（Operations Console）」一节。

> 历史注记：监控面板最初按"只读、无数据库、无进程管理"交付
> （`tasks/archive/20260524-162356-prd-agent-runner-operations-console.md`）。
> 这三条约束已被统一管理终端 PRD 显式取代。

### 端点

监控面板复用两个只读 API：

- `GET /api/v1/agent-runner/overview` — 按仓库返回健康、队列统计、Issue 摘要、最近事件和异常计数。这是**实时扫描**口径（现场调 `gh`），供显式请求使用。
- `GET /api/v1/agent-runner/issues/{issue_number}` — 单个 Issue 的 label、PR context、worktree 状态、event timeline、anomalies 和建议 CLI 命令。可选 `repo_id` 查询参数把查找限定在该仓库：Issue 编号跨仓库会撞号，不带参数时后端按 registry 顺序返回**第一个**带该编号的仓库的快照，Dashboard 因此始终带上点击的那个仓库。

Dashboard 首屏与轮询读的是第三个端点，见下节：

- `GET /api/v1/agent-runner/overview/snapshots` — 读取本地持久化快照，不触发任何 GitHub 调用。

这些端点都只读，不暴露任何修改 GitHub label、comment、PR 或 worktree 的能力。

### 本地快照与后台定时同步

Dashboard 的数据源是**本地快照**而不是现场扫描：打开页面先读 `GET /overview/snapshots`，
数据来自 `~/.kedacode/console.db` 的 `monitoring_snapshots` 表（每个仓库一行，存整份
per-repo overview JSON 与 `scanned_at`），因此即使 `gh` 慢或断网也能秒开并继续
显示最近一次同步的结果。页面每 15 秒轻量轮询一次该端点，后台同步一写回界面就自动跟上。

快照由一个后台同步循环维护：

- 循环由 `kc console` 的 FastAPI lifespan 启动与回收（进程内唯一），导入路由模块不会产生任何线程。
- 每个周期对当前启用仓库做一次全量扫描（仍走 `GET /overview` 的同一套构建逻辑），把**已经构建好的** payload 直接写回快照，不为写库重复扫描。
- 同一仓库同一时刻只允许一次扫描：周期同步与手动刷新共用一个按仓库协调器，不同仓库可并行。
- 全新环境没有快照时，由后端幂等触发一次首扫，前端只显示"尚未同步/正在同步"并轮询结果，不会重复创建扫描任务。
- 同步或写库失败时保留该仓库的旧快照、其余仓库照常更新，失败进入 job/批次状态并记日志，不伪装成刷新成功。
- 已从 registry 删除或禁用的仓库即使仍有历史快照行，也不会重新出现在页面上。

页面上可以看到"上次同步"时间（取各仓库快照 `scanned_at` 的最大值），点击"同步设置"
可内联展开设置面板：开关自动同步、在 1/5/15/30/60 分钟之间调整间隔。设置保存在同一个
本地库的 `monitor_settings` 表里（运行时覆盖值），改完立即生效并在进程重启后保持；
没有保存过时回落到配置项 `[agent_runner.console].monitor_sync_interval_seconds`。
对应 API：

```text
GET    /api/v1/agent-runner/console/monitor/settings
PATCH  /api/v1/agent-runner/console/monitor/settings   {sync_enabled, sync_interval_seconds}
```

`sync_interval_seconds` 合法区间为 60–3600 秒，两个字段都必填。保存成功后后端立即唤醒
调度循环，按新间隔重算下一次运行时间；关闭自动同步后不再有任何后台扫描，手动刷新仍可用。
这份设置是全局的：它同时约束 Dashboard 概览与 Backlog 列表两条后台同步循环
（见「Backlog（待办队列）→ 本地快照与后台刷新」），两条循环共享设置但各自独立调度。

### 异常检测

后端按以下规则对每个 Issue 推导异常，每条异常都带 `severity`、`message` 和 `suggested_cli`：

| 异常类型 | 触发条件 | severity | 推荐 CLI |
|---|---|---|---|
| `label_pr_mismatch` | PR 已创建但 Issue label 不在 `agent/supervising` / `agent/review` / `agent/blocked` / `agent/failed` 中 | warning | `kc labels sync`、`kc review --dry-run` |
| `pr_dirty_in_review` | PR `mergeable_state` 为 dirty/conflicted 且 label 是 `agent/review` | error | `kc review`、`kc run --max-issues 1` |
| `dirty_worktree_mismatch` | worktree 有未提交变更但 label 不是 `agent/running` | warning | `kc run --dry-run`、`git status` |
| `event_label_mismatch` | 最新 `iar:event` phase 隐含的状态与当前 label 不一致 | warning | `kc labels sync` |

Overview 还会按 severity 汇总 `anomaly_count` 和 `anomaly_summary`（`warning` / `error`），并把 `has_anomaly` 标在对应 Issue 行上。

### 事件时间线

`GET /api/v1/agent-runner/issues/{issue_number}` 返回 `timeline`，按时间顺序列出所有 `<!-- iar:event ... -->` 标记，附带 phase、cycle、head SHA、PR branch、checks_state、mergeable、action 等字段。解析复用 `backend.core.use_cases.agent_runner_events`，不会复制 marker parser。

### 建议 CLI 文本

每个 Issue 详情区都会列出当前状态推荐的 `kc` 命令文本（如 `kc review`）。命令旁有**复制**按钮，但**不直接执行**——所有恢复动作仍走 CLI，保留操作审计、避免 UI 端任意 shell。

### 显式非目标

监控 API（`/overview`、`/issues/{n}`）本身保持只读：

- 不暴露任何修改 label、comment、PR、worktree 的 API。
- 不执行任意 shell 命令、不能从 UI 改 label 或触发 agent。
- 不替代 `kc run` / `kc review` / `kc labels sync` 等恢复命令。
- Dashboard 展示的是本地快照（见「本地快照与后台定时同步」），不再每次进入页面现场扫描；但除快照表与同步设置表外不新增数据库表，也不引入 WebSocket 或独立调度服务；GitHub label/comment/PR 和本地 worktree 仍是事实来源。
- 不实现自动 rebase 冲突解决；冲突的 Issue 会带 `agent/blocked` 状态出现在监控面板，由人类决定下一步。

写操作统一收敛在管理终端 API（白名单动作 + 审计），见下一节。

## Agent Runner 统一管理终端（Operations Console）

管理终端把多项目的 Agent Runner 运维收敛到一个 Web 界面，页面：

| 页面 | 路由 | 能力 |
|---|---|---|
| Backlog | `/app/backlog` | **首屏落点**。左侧受管理仓库栏 + 右侧当前仓库的 PRD 队列（依赖图 / 时间轴 / 列表三视图） |
| 总览 | `/app/dashboard` | 队列监控 + 每仓库完成度摘要 + failed/blocked Issue 的重试/继续按钮 |
| 进程 | `/app/processes` | 启停每个仓库的 runner 进程，实时查看进程日志（offset 轮询） |
| 统计 | `/app/stats` | 实时完成度（GitHub 口径）+ 历史趋势与最近运行记录（本地 SQLite 口径） |
| 项目 | `/app/repositories` | 仓库 registry 列表 / 添加 / 启停（写回 `config.toml`）+ 审计日志 |
| 想法 | `/app/ideas` | 跨项目想法采集、AI 总结、PRD 草稿人审 |

### 首屏默认仓库（当前项目）

管理终端是多仓库面板，但在哪个仓库目录敲 `kc console`，打开就应该落在**那个
仓库**上，而不是 registry 声明顺序最靠前的那个。首屏选仓库的优先级：

1. **console 进程 cwd 匹配到的仓库** —— 后端把 cwd 归一到 git 仓库根，再去
   registry 匹配；只有唯一命中且启用的条目才算数。在 `~/code/keda` 敲
   `kc console` 就选中 `keda`，`cd` 到别的仓库再敲就切到那个仓库。
2. **上次手动选择的仓库**（localStorage `iar.console.lastRepoId`）—— cwd 不在
   git 仓库内、或所在仓库没登记进 registry 时的记忆兜底。
3. **registry 里第一个启用的条目** —— 以上都拿不到时的最终兜底。

落在 enabled 列表之外的候选一律忽略：停用或已移出 registry 的仓库不会成为首屏
默认目标。

后端侧推断在 `backend.core.use_cases.console_context.resolve_console_context`，
经`GET /api/v1/agent-runner/console/context` 暴露；前端侧三个带仓库选择的页面
（Backlog / 进程 / 想法）共用 `frontend-public/lib/console-repository-selection.ts`
的 `useRepositorySelection`，不再各自实现一套优先级。

该端点用 `status` 字段表达落空原因（`not_git_repo` / `not_registered` /
`disabled` / `ambiguous`），**不返回 4xx** —— cwd 匹配不上是正常状态，不是故障。

### 启动方式（`kc console`）

管理终端的前端静态产物随 wheel 打包。装好 `kc` 后在任意目录一条命令即可启动，无需 clone keda、无需 Node / pnpm / just：

```bash
# 启动 API + 内置面板并自动打开浏览器（前台运行）
kc console

# 指定端口（省略时先复用已在运行的实例，没有则在 [agent_runner.console].port 起自动挑选空闲端口）
kc console --port 8600

# 只启动服务，不自动开浏览器
kc console --no-browser
```

- 服务固定监听 `127.0.0.1`（见下文信任边界）；端口缺省值来自
  `config.toml` 的 `[agent_runner.console].port`，被占用时自动顺延挑选
  空闲端口，显式指定的端口不顺延。
- **重复调用不会起第二个实例**：启动前先在候选端口上探测是否已有 console 在
  服务（打开发端自带的版本端点，认得出才算命中，本机其它占端口的服务不会被
  误认），命中就只打开它当前监听的 URL 并退出。因此关掉浏览器后想再看面板，
  直接再跑一次 `kc console`，不需要先停掉服务；同理，现有实例当初因端口占用
  顺延到过别的端口，再次调用也会找回它，而不是在缺省端口新开一个。
  要并行跑第二个 console（例如另一套状态目录）就显式指定一个空闲 `--port`；
  显式端口被**非 console** 的服务占用时仍按原样报错退出。
- 复用现有实例时 `--no-browser` 依旧有效：只打印 URL、不拉起浏览器，适合脚本
  里取 URL 或配合 SSH 端口转发（`ssh -L 8313:127.0.0.1:8313 <host>`）。
- 源码开发模式（wheel 里没有静态产物）时，`kc console` 仍可启动并只
  提供 API，日志会提示先构建前端：`pnpm --filter frontend-public build`，
  再把 `frontend-public/out/` 复制到 `src/backend/api/static/console/`；
  或直接沿用 `just run` / `pnpm --filter frontend-public dev` 的开发双端口。
- 面板与 API 同源，开发代理仅在 `frontend-public dev` 模式生效。
- 改后端路由后必须**重启** `kc console` 才生效；`just console-sync` 只替换
  静态前端产物，不重载后端代码。注意复用逻辑发生在这次重启之前——旧版本进程
  仍在监听时，新装的 `kc console` 只会重开浏览器而不会把它换成新代码，需要先
  停掉旧实例。

### 信任边界与白名单动作

管理终端按**本机单用户部署**信任边界运行：`/api/auth/*` 返回固定的
本地 operator 会话，不做真实认证。所有写操作只能映射到硬编码白名单
动作，后端从枚举构建命令参数，**永不接受 UI 传入的原始命令字符串**：

| 动作 | 语义 |
|---|---|
| `start_daemon` / `start_review_daemon` | 为某仓库启动常驻 runner 进程（同仓库同类型只允许一个） |
| `run_once` / `review_once` | 启动一次性托管子进程（dashboard 仓库卡片「跑一轮」「复核一轮」即映射到这两个动作，以托管进程形态呈现于「进程」页，可看日志、可停止，**不产生常驻 daemon**） |
| `stop_process` | SIGTERM 停止托管进程，超时升级 SIGKILL |
| `retry_failed` | 把 failed Issue 的 label 翻转回 ready（与手工操作等价） |
| `recover_failed_publish` | 复用 CLI `kc recover` 的既有恢复用例，把发布失败的 Issue 重新推 PR（不可恢复时返回明确失败） |
| `blocked_continue` | 启动一次性 `kc blocked-continue` 托管子进程 |
| `enqueue_ready` | backlog「加入就绪」：无关联 Issue 时建 Issue、随后打 `agent/ready`，**绝不启动 runner**（队列资格交给 autopilot） |
| `update_issue_labels` | Issue 详情页在**已同步标准标签集内**增删标签（见下「Issue 标签的网页编辑」） |
| `create_issue_from_prompt` | backlog「一句话建 Issue」：经 CLI 同款 from-prompt 用例开一条无 PRD 的 Issue，建完停在未入队态 |
| `registry_add` / `registry_set_enabled` | registry 写回（路径必须存在、为 git 仓库，且未被其他 repo_id 占用） |
| `registry_remove` | 停该仓库常驻进程后删除 registry 条目；只删注册，不删本地仓库目录 |

故意不支持：任意 shell 命令、任意 label 编辑、PR merge、worktree 删除。
这些要么风险不可枚举（任意 shell），要么会绕过 workflow 状态机
（任意 label），要么属于必须人工签收的决策（merge）。

**标签编辑的例外**：网页允许增删，但**范围严格限定在 `kc labels sync` 同源的
标准标签集**（同一份 `standard_label_names` 定义，不存在第二份硬编码清单）；
集合外的标签（哪怕是拼错的 `agent/redy`）在写入前即被拒绝、GitHub 零变化，
网页也不创建新标签——确需新标签仍走终端 `kc labels sync`。写回一律以 GitHub 的
fresh read 结果作响应，因此页面状态与仓库实际状态不可能各说一套。

> **开放范围里含「机器会立即消费」的标签**（人工决定一时的呈递项，见 PRD
> `P1-FEAT-20261008-165241` 决定一）：白名单是同步集合的全量，不只是队列资格标签。
> `agent/running`、`agent/supervising`、`agent/failed`、`agent/waiting` 是被 claim /
> reclaim / 依赖判定读取的在途与等待状态——例如误打 `agent/running` 会让 daemon 跳过该
> Issue，且「加入就绪」对它永久返回 409；`agent/rework-prd`、`agent/deliberate` 会触发
> 重写 PRD 与多 agent 合议；`direct-pr` 选择发布档位；`validation/pending`、
> `validation/passed`、`validation/verifier-passed` 是验证门禁读取的人工 / verifier
> 签收信号。一次点击就能写出这些状态，因此它们被视作工作流操作而非「改个标签」；
> 收窄集合等于改需求，需要更小的口子请在终端用 `kc labels` 或按仓库调整配置。


所有写操作（含被拒绝的）都会写入审计日志，可在「项目」页或
`GET /api/v1/agent-runner/console/audit` 查看。Issue 侧同样如此：集合外标签写入、
非法 Issue 类型、被 core 拒绝的建 Issue 请求都会留下一条 `result="rejected"` 的审计，
detail 记录本次尝试想写的标签 / 类型与拒绝原因。

### 网页操作入口与对应端点（CLI 能力对齐）

管理终端补齐了一批原本只能在终端做的操作。每一项都映射到既有 CLI 语义或既有
core 用例，网页只是入口，不发明第二套执行规则：

| 入口（页面/位置） | 语义（对齐的 CLI/用例） | HTTP 端点 |
|---|---|---|
| dashboard 仓库卡片「跑一轮」「复核一轮」 | 一次性 `kc run` / `kc review` 托管子进程 | `POST .../console/repositories/{repo_id}/actions`（`run_once` / `review_once`） |
| dashboard runner 状态条 | 读既有 status/health，探测失败降级展示 | `GET /api/v1/agent-runner/status`、`/health` |
| dashboard 仓库概览「监控中 / 全部」切换 | 全量列举 open Issue，标注是否已被监控收录 | `GET .../console/repositories/{repo_id}/issues` |
| Issue 详情标签面板（增删） | `kc labels sync` 同源标准集内编辑 | `GET` / `PUT .../console/repositories/{repo_id}/issues/{issue_number}/labels` |
| Issue 详情 failed 态「恢复发布」 | 复用 `kc recover` 的恢复用例 | `POST .../console/repositories/{repo_id}/issues/{issue_number}/actions`（`recover_failed_publish`） |
| backlog PRD「加入就绪」 | 建 Issue + 打 ready，绝不启动 runner | `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/enqueue-ready`（无 `console` 段） |
| backlog「开始此 PRD」高级选项 | 逐项对应 `kc run` 同名旗标（快合 / agent / 预设 / 模型 / 推理力度），**全部默认关闭**；直出 PR 不在其中（见下） | `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/start`，`launch_options` 字段透传 |
| backlog「一句话建 Issue」 | CLI `kc issue create --from-prompt` 同一用例，`issue_type` 先过 `type/*` 标签纪律 | `POST .../console/repositories/{repo_id}/issues` |
| 高级选项 sheet 的候选下拉 | 只读列举该仓库的 agent 名单与预设名 | `GET .../console/repositories/{repo_id}/launch-options` |

要点：

- **启动选项的缺省契约不变**：不勾选任何高级选项时，序列化出的 `launch_options`
  折算成空 argv 片段，发出的 `kc run` 与引入该参数之前逐字节一致（由契约测试锁死）。
  非法组合在发起端即被拒绝，而不是在子进程里静默降级：既包括**选项自身**的规则（快合
  与直出 PR 互斥、模型/推理力度未带预设、取值不是单一 token），也包括 CLI 的**目标域**
  规则——队列级 `--all-ready` 不能配任何跳过闸门的旗标（`build_runner_argv` 直接拒绝），
  PRD-backed 目标不能配直出 PR（见下条）。
- **直出 PR 不进 PRD 启动入口**：CLI 的 `--direct-pr` 有一条**目标域**规则——目标 Issue
  带 PRD 锚点时硬性用法拒绝（PRD-backed Issue 必须过 PRD 交付门并归档 PRD）。backlog
  start 的目标按构造就是 PRD-backed Issue，因此该入口不暴露这个开关；请求体仍保留
  `direct_pr` 字段，带上它在动 Issue 之前就返回 400 并给出同款原因（`start_prd` 的
  目标域预检），不会留下「提示已开始、进程立刻退出」的半成品。直发档请按 CLI 用法
  作用于无 PRD 锚点的 Issue。
- **「一句话建 Issue」的类型受标签集合约束**：`issue_type` 会拼成 `type/<issue_type>`
  写进 GitHub，因此后端只接受该仓库 `kc labels sync` 已同步的 `type/*` 名（默认
  `feature` / `refactor` / `bug`），越界取值在调用用例之前被拒（400，GitHub 零变化），
  与 FR-5「网页不创建新标签」同一口径；CLI 侧同类输入由 argv 枚举挡住。
- **「加入就绪」不启动 runner**：它与「开始此 PRD」共用建 Issue / 打标签路径，
  唯一区别是最后不发 `kc run`；对运行中的 Issue 调用返回冲突，对已就绪的调用幂等。
- **本次未改 `kc` CLI 表面**：以上都是新增 HTTP 入口，子命令 / 旗标 / 退出码 /
  机器输出零变化，随包 `kedacode-operator` skill 无需同步。

### 进程托管与多项目并发

管理终端按 `(repo_id, kind)` 托管 runner 子进程。**每个仓库一个
daemon 进程**即获得多项目并发——不同仓库的 Issue 同时执行，互不阻塞
（CLI 的 `kc daemon` 在 cwd 命中唯一已初始化注册仓时只监控该仓，未命中、未初始化或匹配多个时报错；显式 `--all` 时才监控所有 enabled registry entries，但在单个进程内串行轮询）。

- 子进程以 `start_new_session` 脱离后端进程组：后端重启不影响执行中
  的 runner；重启后从 pidfile registry（`~/.kedacode/processes.json`）复活
  记录并重新探活。
- 子进程以 `<kc> <command> --repo-id <id>` 启动，cwd 为**目标仓库自身
  路径**：托管的 daemon 必须在目标仓库内读取该仓的 `.iar` 状态与 git
  上下文；全局安装场景下 keda 项目根与目标仓库无关。
- 默认启动命令运行时解析：`kc console` 入口直接取 `sys.argv[0]`（与
  当前安装态完全同源），否则从 PATH 解析 `kc`，兜底 `uv run kc`（源码
  树内的 uv 项目场景）。可用 `[agent_runner.console] runner_command`
  显式覆盖。
- 进程 stdout/stderr 写入 `logs/agent-runner/processes/<repo_id>/`，
  面板通过 offset 轮询续读，无 WebSocket/SSE。

### 运行历史与完成度统计

- **实时口径（GitHub）**：`GET /api/v1/agent-runner/console/stats/overview`
  以 `state=all` 查询全部 workflow label 的 Issue 并去重。closed 且不含
  failed/blocked label 计为 `completed`；单 label 查询命中 200 上限时
  响应标记 `truncated: true`。
- **历史口径（本地 SQLite）**：`run_once` 编排在每个 Issue 处理收尾时
  写入一条运行记录（outcome：completed / failed / blocked），CLI 直跑
  与面板托管共用 `~/.kedacode/console.db`（`history_db_path` 可配）。
  `GET .../console/stats/history` 返回按天聚合趋势。

SQLite 只是旁路记录，**不参与 workflow 状态机决策**——GitHub
labels/comments/PR 与本地 worktree 仍是唯一事实来源；落库失败只产生
日志警告，不会阻断 runner。

### PRD 生命周期账本与执行分析（Lifecycle Ledger）

上面的 `run_records` 记录的是**单次 Issue run**（一次 runner 调用），
无法回答"一个 PRD 从排队到归档总共花了多久、卡在哪一步"。为此
console SQLite 另加两张**追加式**账本表，把观测单位从"一次调用"提升到
"一个 PRD 的完整生命周期"：

- **`prd_lifecycle_runs`**：一次 PRD 执行的身份与结局。主身份是
  `repo_id + prd_path + stable run_id`，Issue 编号只是外部关联；PRD
  改名或多次重试都复用同一稳定 run id，避免互相覆盖。
- **`prd_lifecycle_events`**：按发生时间追加的语义事件，`event_key`
  在同一 run 内唯一，重复写（重试、崩溃重入）**不产生重复事件**；
  后续成功也不覆盖先前的失败事件。

两张表沿用现有旁路历史端口（`IRunHistoryStore` 族）的 WAL、迁移与
错误策略，schema 通过 `PRAGMA user_version` 升级，不新增数据库或服务。
现有 `run_records` / `attempt_records` 保持原 schema，仅按
`repo_id + issue_number + 时间窗口` 做**展示关联**，不反向伪造
lifecycle event。

**耗时口径（core 计算，前端只格式化）**

- 主指标是**端到端耗时**：从首次进入执行队列到归档的完整历时，而不是
  `run_records.duration_seconds`（那是单次 runner 调用）。
- 端到端拆成三类**互斥**时长：**有效执行**（Agent 实际工作）、
  **等待**（等待外部结果，如审阅 / CI）、**阻塞**（明确 blocked）。
  三者之和等于端到端，前端不得重新定义或聚合这些口径。
- 进行中的 run 端到端**计算到当前时刻**并在界面显示"进行中 / 阻塞中"，
  不伪造结束时间；失败事件与各次 attempt 耗时保留在时间线上。

**`current_phase` 闭集**：
`none | queued | executing | validating | reviewing | merging | blocked | failed | completed`。
**`event_type` 闭集**：
`queued | started | claimed | attempt | retry | recovered | implementation_completed |
validation_started | validation_passed | validation_failed | review_started |
review_passed | review_failed | merge_started | merged | archived | blocked |
unblocked | failed | agent_token_usage`。

**`status` 闭集（事件语义状态，时间线状态徽章的事实源）**：
每条事件在**写入时**按当时 `event_type` 冻结一个 `status`，落 `prd_lifecycle_events`
的 `status` 列（schema v8 追加，非空默认空串）。它与粗粒度 `phase` 分工不同：`phase`
只用于耗时归属与「当前阶段」推导，会把 `started / claimed / attempt / retry / recovered`
一并塌缩成 `executing`；`status` 保留每行「当时发生了什么」的可区分语义，避免
「开始执行」与「已被领取」在时间线上都显示「执行中」。闭集为
`none | queued | started | claimed | attempt | retry | recovered |
implementation_completed | validation_started | validation_passed | validation_failed |
review_started | review_passed | review_failed | merge_started | completed | archived |
blocked | unblocked | failed`，其中 `merged` 收成 `completed`（已完成）、`archived`
保留「已归档」、「已进入队列」保持既有的「排队中」显示。观测类 `agent_token_usage`
记为 `none`，前端据此不渲染误导徽章。v8 之前的历史行 `status` 为空串，序列化时回落
按 `event_type` 派生（`status_for_event_type`），**绝不取 run 的当前状态**——否则
历史事件会失去当时语义。`frontend-public` 的 Backlog「执行过程」标签据此渲染逐行
状态徽章，抽屉「状态」行同源。

**Token 用量统计（agent_token_usage 观测维度）**

每次 agent 子进程调用的官方 usage（claude stream-json 的 `result.usage`）
在子进程执行层**渲染前**捕获并挂进事件 `detail_json`：

- 实现/修复主调用记入 attempt 族事件的 `detail.token_usage`；验证
  （verifier）、评审（supervisor）等旁路调用发独立的 `agent_token_usage`
  观测事件（`detail.flow` ∈ `verify / supervise / fix / closeout`）。
- **口径**：总量 = 输入 + 输出 + 缓存读 + 缓存写（实际处理量，缓存命中
  计入）；缓存命中率 = 缓存读 ÷ 输入侧。agent 未上报 usage（部分协议、
  超时被杀、旧记录）时**不估算**，展示为「—」且不进入汇总。
- **CLI 查询**：`kc tokens [--repo-id <id>] [--days <N>] [--issue <n>] [--json]`
  在终端输出按流程 / 按 agent / 按 PRD（Issue）三张汇总表（与 Stats 端点
  同源同口径）；`--issue <n>` 把三张表收窄到单个 Issue，回答"这个 PRD 烧了
  多少"；`--json` 额外携带 `by_prd` 维度供脚本消费；空数据显示「—」或明确
  空态文案。
- **Stats 页展示**：Stats 页「Token 用量」区渲染同样三张表（按流程 / 按
  agent / 按 PRD），由 `stats/prd-lifecycle` 端点的 `token_usage` 与
  `token_usage_by_prd` 驱动，与 CLI 同源同口径；「按 PRD」表按总量降序，
  同一 PRD 的多次执行合并为一行并显示累计执行次数（`run_count`）。
- **不干扰**：`agent_token_usage` 不参与阶段推导与时长归属（推导时被
  过滤），写入失败与其它观测事件一样只落日志。

**两个只读 API**

```text
GET /api/v1/agent-runner/backlog/prds/{encoded_prd_path}/lifecycle
    单 PRD 明细：repo_id / prd_path / run_id / issue_number / trigger /
    current_phase / in_progress / outcome / history_complete / started_at /
    finished_at / durations{end_to_end_seconds, active_seconds,
    waiting_seconds, blocked_seconds} / events[]（每条含 event_type / phase /
    status / actor / occurred_at / detail，status 为逐行状态徽章事实源）/ has_data

GET /api/v1/agent-runner/console/stats/prd-lifecycle?repo_id=&days=
    仓库级统计：completed_runs / average_end_to_end_seconds /
    median_end_to_end_seconds / p90_end_to_end_seconds /
    average_blocked_seconds / bottleneck_phase / bottleneck_phase_seconds /
    unlinked_run_count / incomplete_run_count / runs[] /
    token_usage{by_flow, by_agent}（每行含 input/output/cache_read/
    cache_creation/total_tokens 与 usage_count）/
    token_usage_by_prd[]（按 PRD（Issue）维度，total_tokens 降序；每条含
    repo_id / prd_path / issue_number / run_count / totals{四项 + total +
    usage_count}；账本不可用时降级为空数组。响应新增字段，只增不改，
    既有消费者不受影响）
```

**降级语义**

- **观测旁路**：生命周期事件写入失败**不阻断 runner**——已完成的代码
  交付照常收敛；失败只落日志（带 run/event 上下文，不含敏感 payload）。
- **显式不完整**：对应的 run 标记 `history_complete=false`，明细 API 与
  PRD 详情页据此显示"数据不完整"告警，而不是静默假装完整。
- **旧记录降级**：只有 Issue 编号、无法可靠归属 PRD 的历史记录标为
  **"未关联 PRD"**，仍可在最近运行列表查看，但**不纳入**完成分位数统计
  （计入 `unlinked_run_count`）；不通过标题、模糊路径或当前 label 猜测归属。

**存储边界**：生命周期账本只落在本机 `~/.kedacode/console.db`（与运行历史、
审计同库，`history_db_path` 可配），**没有远程备份、不跨机器聚合**；
删除该 SQLite 文件即丢失全部生命周期历史。`frontend-public` 的 Backlog
详情与 Stats 只消费上述两个 API 的聚合结果，不自行计算耗时或状态。

### 项目接入

`config.toml` 的 `[agent_runner.repositories.*]` 仍是项目接入的唯一
事实来源。「项目」页通过 tomlkit 做 round-trip 写回（保留注释与
格式），添加前校验 repo_id 格式（`^[a-z0-9][a-z0-9-]*$`）、路径存在
且为 git 仓库。某个已注册路径失效时，监控与统计会跳过该仓库并在
总览页给出醒目警示，不会拖死整个面板。

「本地路径」和「扫描根目录」输入框都配有**目录选择器**：浏览器拿不到真实
绝对路径（`showDirectoryPicker` 只给不透明 handle，`webkitdirectory` 只有
相对路径），所以由本机后端列举子目录，前端弹窗逐级下钻。「选择此目录」选的是
当前所在目录；选定后回填路径，`repo_id` 与显示名为空时按目录名自动补全
（与 `kc registry` 扫描用的 `normalize_repository_id` 同一规则）。带
`git 仓库` 标记的目录才能通过「校验并添加」。

### Console API 一览

```text
GET    /api/v1/agent-runner/console/processes
POST   /api/v1/agent-runner/console/processes                  {repo_id, kind}
POST   /api/v1/agent-runner/console/processes/{id}/stop
GET    /api/v1/agent-runner/console/processes/{id}/logs?offset=0
GET    /api/v1/agent-runner/console/repositories/{repo}/issues/{n}/logs?offset=0&attempt_id=&tail=  Issue 实时输出（只读；tail=1 取尾部窗口）
POST   /api/v1/agent-runner/console/repositories/{repo}/actions             {action}
POST   /api/v1/agent-runner/console/repositories/{repo}/issues/{n}/actions  {action}
GET    /api/v1/agent-runner/console/stats/overview
GET    /api/v1/agent-runner/console/stats/history?repo_id=&days=30
GET    /api/v1/agent-runner/console/stats/prd-lifecycle?repo_id=&days=30
GET    /api/v1/agent-runner/console/runs?repo_id=&limit=100
GET    /api/v1/agent-runner/console/audit?limit=100
GET    /api/v1/agent-runner/backlog/prds/{encoded_prd_path}/lifecycle
GET    /api/v1/agent-runner/repositories
GET    /api/v1/agent-runner/repositories/browse?path=          目录选择器（只读）
GET    /api/v1/agent-runner/repositories/discover?scan_root=   扫描已初始化 KedaCode 的仓库
POST   /api/v1/agent-runner/repositories                       {repo_id, path, display_name}
POST   /api/v1/agent-runner/repositories/batch                 {repositories: [...]}
PATCH  /api/v1/agent-runner/repositories/{repo_id}             {enabled}
```

### 配置

```toml
[agent_runner.console]
history_db_path = "~/.kedacode/console.db"            # 运行历史与审计 SQLite
process_registry_path = "~/.kedacode/processes.json"  # 托管进程 pidfile
process_log_dir = "logs/agent-runner/processes"  # 进程日志目录（相对 keda 根）
runner_command = ["uv", "run", "kc"]            # 托管进程启动命令前缀（缺省运行时解析，见上文）
stop_timeout_seconds = 30                        # SIGTERM → SIGKILL 等待秒数
monitor_sync_interval_seconds = 300              # dashboard 后台自动同步的静态默认间隔（秒，60–3600）
host = "127.0.0.1"                               # 监听地址（固定本机，不开放配置其它网卡）
port = 8600                                      # kc console 缺省端口（已有实例在跑则复用，否则被占用时自动顺延）
```

`monitor_sync_interval_seconds` 只是**从来没有保存过界面设置**时的回落值；用户在
dashboard 上改过的间隔存在 `~/.kedacode/console.db` 的 `monitor_settings` 表里，优先于配置。

## deliberate 多 Agent 合议

`kc deliberate` 启动一次只读的多 Agent 合议会话，适合在编码前对复杂需求做多视角推演。

### 基本用法

```bash
# 使用默认 3 个 agent（architect、skeptic、implementer）合议 2 轮
uv run kc deliberate "实现一个用户认证系统"

# 指定参与 agent 和轮数
uv run kc deliberate "优化数据库查询性能" \
  --agents architect,implementer \
  --rounds 3 \
  --synthesizer claude

# 指定输出目录和 session ID（便于复现或测试）
uv run kc deliberate "设计缓存策略" \
  --output /tmp/deliberations \
  --session-id cache-strategy-001
```

### 输出文件

每次会话默认写入 `logs/agent-runner/deliberations/<session_id>/`：

- `events.jsonl`：机器可读事件流，每行一个 JSON 对象
- `transcript.md`：按轮次和 agent 分组的人类可读讨论记录
- `result.md`：最终结论（Recommendation、Consensus、Disagreements、Risks、Next Actions）
- `session.json`：会话元数据、profile 配置、命令参数
- `workspaces/<profile_id>/round-<n>-output.md`：单个参与 agent 在对应轮次的原始输出
- `workspaces/synthesizer/synthesis-output.md`：synthesizer 的原始结构化输出

### 实时输出文件

每个 agent 的 workspace 输出文件在子进程运行期间实时增长，而不是等进程结束后一次性写入：

- `workspaces/<profile_id>/round-<n>-output.md` 在对应 agent 子进程启动前或启动时创建
- 每个可读输出 chunk 到达时立即追加写入对应文件
- agent 失败时保留 partial output 文件，便于排查

### 终端实时输出

合议过程中终端会实时显示结构化事件：

```
[session-id] round=1 agent=architect event=agent_started
[session-id] round=1 agent=skeptic event=agent_started
[session-id] round=1 agent=architect event=agent_finished
[session-id] round=1 agent=skeptic event=agent_finished
[session-id] round=0 agent=synthesizer event=agent_started
[session-id] round=0 agent=synthesizer event=agent_finished
```

#### 交互式 TTY Live 视图

在交互式终端（TTY）中运行时，`kc deliberate` 会显示实时 live view，并**按终端宽度自适应版式**：

- **宽终端并排分栏**：当 `终端宽度 / 当前并发 agent 数 ≥ 40 列` 时，每个 agent 占一栏并排显示，栏数等于当前并发运行的 agent 数量（默认 `architect`、`skeptic`、`implementer` 为三栏）。
- **窄终端竖向堆叠**：当每栏宽度不足以容纳可读文本时，自动改为整宽面板上下堆叠，避免文字被压得过窄。
- **面板内容**：每个面板显示 round、agent、provider、状态，底部标注对应 workspace 目录，并展示最近输出。
- **文字不截断**：正文在面板内换行，绝不横向截断；面板只保留最近若干行（按终端高度裁剪），完整内容以 workspace 文件为准。
- **实时刷新**：输出到达时自动刷新；换轮时上一轮面板会冻结进终端滚动历史，再切换到新一轮。
- **实时推理/工具日志**：部分 provider（如 `codex`）会把 banner、推理过程和工具调用日志写到 stderr。这些内容会被捕获并实时显示在对应面板里作为进度，但**仅用于展示**——不会写入 `round-<n>-output.md`，也不会进入 transcript。落盘和 transcript 只保留 provider 在 stdout 上的可读最终输出。这样既能在运行中看到 agent 在做什么，又能保持 workspace 文件干净。

宽终端并排示例：

```
╭─ round=1 agent=architect provider=claude running ─╮ ╭─ round=1 agent=skeptic provider=kimi running ─╮ ╭─ round=1 agent=implementer provider=codex run─╮
│ 架构师：这是我的可读输出样例。              │ │ 质疑者：这是我的可读输出样例。            │ │ implementer：这是我的可读输出样例。       │
│ ...                                               │ │ ...                                         │ │ ...                                           │
╰────────── workspaces/architect/ ─────────────────╯ ╰───────── workspaces/skeptic/ ────────────────╯ ╰──────── workspaces/implementer/ ─────────────╯
```

窄终端竖向堆叠示例：

```
╭─ round=1 agent=architect provider=claude running ──────────────╮
│ 架构师：这是我的可读输出样例，正文在面板内换行，不会被横向截断。 │
╰──────────────────────── workspaces/architect/ ─────────────────╯
╭─ round=1 agent=skeptic provider=kimi running ──────────────────╮
│ 质疑者：这是我的可读输出样例。                                  │
╰───────────────────────── workspaces/skeptic/ ──────────────────╯
╭─ round=1 agent=implementer provider=codex running ─────────────╮
│ implementer：这是我的可读输出样例。                            │
╰─────────────────────── workspaces/implementer/ ────────────────╯
```

#### 非 TTY / CI / Plain 模式

在非交互式终端、CI 环境、重定向输出或显式 plain 模式下，退回带前缀的普通文本：

```
[round=1 agent=architect status=running] 架构师：这是我的可读输出样例。
[round=1 agent=skeptic status=running] 质疑者：这是我的可读输出样例。
[round=1 agent=implementer status=running] implementer：这是我的可读输出样例。

[round=0 agent=synthesizer status=running] ## 综合建议
[round=0 agent=synthesizer status=running] ...
```

### 验证命令

验证默认三 agent 输出文件：

```bash
# 运行合议
uv run kc deliberate "test prompt" --rounds 1 --session-id test-001

# 检查输出文件
ls logs/agent-runner/deliberations/test-001/workspaces/
# 应显示：architect  implementer  skeptic  synthesizer

# 查看各 agent 输出
cat logs/agent-runner/deliberations/test-001/workspaces/architect/round-1-output.md
cat logs/agent-runner/deliberations/test-001/workspaces/skeptic/round-1-output.md
cat logs/agent-runner/deliberations/test-001/workspaces/implementer/round-1-output.md
cat logs/agent-runner/deliberations/test-001/workspaces/synthesizer/synthesis-output.md
```

若任一参与 agent 或 synthesizer 子进程返回非 0 退出码，默认行为不再整体失败，而是记录失败并继续；`--strict` 可恢复为失败即非 0。

> **失败隔离与回退**：从本 PRD 起，`kc deliberate` 对单个 agent 失败默认隔离并继续。
> - 失败 agent 的 `workspaces/<profile_id>/round-<n>-output.md` 保留 partial 输出。
> - TTY 下会提示选择回退模型，5 分钟无选择自动切换到下一个可用模型。
> - 非 TTY / CI 下直接自动切换，不阻塞。
> - 使用 `--strict` 或设置 `continue_on_agent_error = false` 时，任一 agent 失败即让 CLI 返回非 0。
> - 全部参与 agent 和 synthesizer 均失败时，无论是否 `--strict` 都返回非 0。
>
> 注意：默认 `skeptic` profile 使用 `kimi`，此前 `kimi` deliberation 命令错误地传递了 `--quiet`，`kimi` CLI 不支持该选项。修复后 `kimi` 命令为 `kimi --input-format text`。

### 安全边界

- `kc deliberate` 不执行 `git add`、`git commit`、`git push`、`gh issue` 或 `gh pr`
- 每个 agent 在 `logs/agent-runner/deliberations/<session_id>/workspaces/<profile_id>/` 下获得独立工作目录
- 每次 agent 运行前后检查目标仓库 `git status --porcelain`；若发生变化，会话失败并写入 error event
- 合议 prompt 明确禁止文件修改、提交、推送、创建 PR 和触碰真实业务数据
- 本功能不展示模型隐藏 chain-of-thought；所谓“全过程”指可审计的公开回复、工具调用摘要、状态事件和报告

### 配置

在 `config.toml` 的 `[agent_runner.deliberation]` 段配置默认值和自定义 profile：

```toml
[agent_runner.deliberation]
default_rounds = 2
default_synthesizer = "claude"
default_output_dir = "logs/agent-runner/deliberations"
# 是否对单个 agent 失败继续执行（默认 true）
continue_on_agent_error = true
# TTY 下等待用户选择回退模型的超时秒数（默认 300）
agent_failure_timeout_seconds = 300

[agent_runner.deliberation.profiles.architect]
agent = "claude"
role = "architect"
behavior_prompt = "You are an experienced software architect..."

[agent_runner.deliberation.profiles.skeptic]
agent = "kimi"
role = "skeptic"
behavior_prompt = "You are a skeptical reviewer..."
```

`session.json` 新增 `failed_agents` 字段，记录失败的 `profile_id`、尝试的 agent、最终回退 agent（如有）和失败原因。

## 架构说明

Agent Runner 的代码分布在四层架构中：

- `core/shared/models/agent_runner.py` / `agent_deliberation.py` — 领域模型（frozen dataclasses）
- `core/shared/interfaces/agent_runner.py` — 抽象端口（`IGitHubClient`、`IProcessRunner`、`IAgentTranscriptRunner`）
- `core/shared/interfaces/agent_output_view.py` — 终端输出视图抽象端口（`IAgentOutputView`）
- `core/use_cases/` — 业务用例（`sync_labels`、`create_issue_from_prd`、`run_agent_once`、`run_agent_repositories_once`、`run_agent_daemon`、`agent_review`、`pr_supervisor`、`review_once`、`review_daemon`、`run_agent_deliberation`）
- `engines/agent_runner/factory.py` — 基础设施适配层（实例化实现并注入用例）
- `engines/agent_runner/live_terminal.py` — 终端 live view 适配器（Rich 自适应分栏/竖向堆叠 / plain fallback）
- `infrastructure/github_client.py` / `infrastructure/process_runner.py` — 外部系统实现
- `api/cli.py` — CLI 入口
- `api/routes/agent_runner.py` — FastAPI 只读路由

## Autopilot 快速档（合并队列）

默认（严格档）下，supervisor approve 的 PR 永远停在 `agent/review` 阶段——等待人工来合并。`autopilot.enabled = true` 切换到 **快速档**：review pass 末尾追加一个"合并队列"阶段，对该仓所有待合并的 PR 串行、自动执行 7 步门禁链后 `squash` 合并进 base 分支。

### 双同意开关

合并队列只在 **两个开关同时为真** 时激活——这是"防呆"设计，也是 `safety.auto_merge` 这个历史死配置第一次真正消费：

```toml
[safety]
auto_merge = true            # 历史开关：现在真正生效

[autopilot]
enabled = true               # 新键：仓库级显式打开
```

任一为假时合并队列整段 **no-op**——supervisor 仍按现状停在 `agent/review`。这意味着历史上即便误把 `safety.auto_merge` 设成 `true`，只要 `[autopilot] enabled` 没显式打开，就**不会**开始自动合并（过往误设的环境零风险）。

> 迁移注意：升级时检查所有仓库；如有 `safety.auto_merge = true` 残留，本 PRD 会把它激活。新键 `autopilot.enabled` 是显式的客户确认入口。

### 7 步门禁链

每条进入合并队列的 PR 都按下列顺序执行；任一步失败则**该 PR 转入既有失败/修复路径**，不阻塞队列中其余 PR：

1. **verifier 门禁**：若 Issue 需要 validation（`validation_required`）且 `autopilot.require_verifier_pass = true`，要求 Issue 已带 `validation/verifier-passed` 标签；缺失则本轮跳过，留给 verifier / repair。
2. **自动签核**：解析 PR body 的 Realistic Validation sign-off 清单区块，把 `- [ ]` 置 `- [x]` 后 `update_pull_request_body`；若已全勾则幂等跳过。同步发一条 `<!-- iar:auto-sign-off ... -->` 审计评论（带 `iar:event` marker），记录 verifier verdict 来源。
3. **rebase**：复用 `pr_supervisor.execute_rebase` 把 PR 分支 rebase 到最新 remote base；冲突走既有 conflict-resolution agent 路径。失败→转 `agent/supervising`，本 PR 跳过。
4. **全量验证**：在该 Issue 的 worktree 内调用 `agent_runner_git.run_verification(...)` 重跑 `runner.verification_commands`；红→转验证修复路径，本 PR 跳过不合并。
5. **禁改路径终扫**：取 PR diff 文件清单，用 `safety.forbidden_path_patterns` 做最终匹配（复用 `agent_runner_publish.is_forbidden_path(...)`）；命中→打 `agent/blocked` + 评论，永不自动合并。
6. **等 checks**：轮询 `get_pull_request_context` 直到 checks 全绿（自动签核后 sign-off check 应翻绿），超时上限 `autopilot.merge_check_timeout_seconds`。
7. **合并**：调用 `IGitHubClient.merge_pull_request(pr_number, method="squash")`（gh 实现 `gh pr merge <n> --squash`）；对"already merged"响应归一为幂等成功。成功后发 `iar:event (auto-merged)` 评论、摘除 `agent/review` 标签。

**PRD hold（自动签核之后、rebase 之前）**：PRD-backed Issue 在 rebase 前先读该 Issue worktree 里的 PRD——按实际位置定位（先 `tasks/pending/`，再 `tasks/archive/`）：

- `Human-Confirmed` 组仍有未回答的项（`- [ ]`，以及老 PRD 误标的 `- [~]`，保守口径）→ `skipped_human_review`，本轮不 rebase、不重跑验证、不合并，等人验收。PRD 在交付时就已归档，所以这一判断**不论 PRD 在 pending 还是 archive** 都生效；只看 pending 路径会让自动合并替人行使验收权。
- PRD 仍在 `tasks/pending/` 且无人审待办 → `skipped_prd_pending`（执行侧没交付完）。
- PRD 已在 `tasks/archive/` 且人审已全部回答 → 继续 rebase 及后续门禁。

人在 PR 分支上勾选人审项并把横幅改为 ✅ 已验收后，下一轮读到最新 PRD 即放行（worktree 需同步到 PR 最新 head，沿用既有行为）。

### 崩溃重入幂等

- **勾选幂等**：解析 `ValidationChecklistState`，已全勾则不改 body、不发评论。
- **评论幂等**：发评论前查 `iar:auto-sign-off` marker，避免重复评论。
- **合并幂等**：`gh pr merge --squash` 对已合并 PR 视为 no-op（基础设施层归一化）。
- **跨 pass**：已合并的 Issue 失去 open PR，下一轮 `find_open_pr_by_head` 返回空，自动跳过收尾。

### 串行与 FIFO

合并队列在同一 `kc review` / `kc review-daemon` pass 内按 **Issue 号升序** 串行处理；后一条的 rebase 天然基于前一条合并后的 base 推进。Repository 内没有并发合并——符合 GitHub 的 fast-forward 假设，单 PR 失败不外溢。

### 不在范围内

- 不做 GitHub 原生 auto-merge / merge queue / 分支保护配置。
- 不支持 squash 以外的合并方式（`merge_method` 在配置加载期仅接受 `"squash"`）。
- 不改变 PRD 归档、worktree 清理、Issue 关闭等既有职责。
- 不新建交叉评估 agent（复用已交付的 `independent-verifier-gate` PRD）；不引入新存储。

## 运行日志

`kc` 命令的运行日志按日期存放在 `logs/` 目录下，文件名格式为 `app-YYYY-MM-DD.log`：

```bash
# 查看今天的日志
cat logs/app-$(date +%Y-%m-%d).log

# 实时查看日志
tail -f logs/app-$(date +%Y-%m-%d).log

# 查看最近 7 天的日志
ls -la logs/app-*.log | head -7
```

### 日志特性

- **按日期命名**：每天生成一个独立的日志文件，便于按日期排查问题
- **可配置保留期**：自动清理超过 `log_retention_days`（默认 14，可用 `LOG_RETENTION_DAYS` 覆盖）天的旧日志文件；长驻进程跨天时也会顺带清理一次
- **时间戳格式**：日志条目使用 `YYYY-MM-DD HH:MM:SS` 格式
- **终端同步**：终端输出同时带有 `HH:MM:SS` 时间戳前缀，便于实时观察

### 日志内容

日志文件包含以下内容：

- CLI 启动和配置加载事件
- Agent 工具调用摘要（如 `[agent tool] Read: /path/to/file.py`）
- Agent 返回结果摘要（如 `[agent result] Task completed`）
- Agent 错误信息（如 `[agent error] API Error: 400`）
- Agent 输出文本（按消息边界汇总）
- 子进程输出（非 Claude agent 如 Codex/Kimi 的输出）

## `kc ask` 自然语言决策入口

`kc ask` 是受限自然语言决策入口，默认只生成计划并写入审计文件，不产生任何副作用。

### 基本用法

```bash
# 默认只输出计划
uv run kc ask "帮我判断现在应该创建 issue 还是启动任务"

# 显式选择 planner agent（默认 codex）
uv run kc ask "从 pending PRD 中挑一个最适合创建 issue 的任务" --agent codex

# 只打印计划，适合 CI 或脚本验证
uv run kc ask "现在可以跑一个 ready issue 吗" --plan-only

# 进入确认执行流程（TTY 中要求输入 decision_id）
uv run kc ask "从 tasks/pending/example.md 创建 issue" --execute

# 非交互执行（仅允许 low/medium 风险动作）
uv run kc ask "运行一次 dry-run 看看 ready 队列" --execute --yes
```

### 权限边界

- **白名单动作**：`show_status`、`run_deliberation`、`create_issue_from_prd`、`mark_issue_ready`、`run_once_dry_run`、`run_once`、`review_once_dry_run`、`review_once`、`needs_clarification`、`no_op`
- **禁止动作**：`git_push`、`git_merge`、`git_reset`、`daemon`、`review-daemon`、任意 shell 命令、自动 merge、直接关闭 Issue、删除分支等
- **Planner 安全**：只读 planner / `kc ask` 的 agent 必须有**已声明的** `generate` 用途且该用途 `read_only = true`——门禁只读这个声明字段，不按 agent 名白名单判断（`factories/content_generators.py`）。当前内置注册表里 `codex`、`claude`、`kimi`、`pi`、`codebuddy`、`qoder` 都声明了 `read_only = true` 的 `generate`，因此都能作为 planner 启动；`opencode` 没有 `generate` 用途，会被拒绝并指名。注意这只校验**声明**：声明本身是否等于运行时真的只读，取决于各 agent 的 argv 是否带沙箱/只读开关（例如 `codex` 用 `--sandbox read-only`，而 `claude` / `codebuddy` / `qoder` 用的是 `--dangerously-skip-permissions`，不是沙箱级只读）。

### 确认策略

- `--execute` 在 TTY 中要求输入 `decision_id` 确认写操作
- `--execute --yes` 只允许 low/medium 风险且允许非交互确认的动作
- 高风险动作（如 `run_once`）不允许 `--yes`，必须 TTY 交互确认

### 审计文件

每次计划写入 `logs/agent-runner/decisions/<decision_id>/`：

- `plan.json`：结构化计划
- `plan.md`：人类可读的计划摘要
- `context-summary.json`：决策上下文摘要

执行时还包含：

- `execution.json`：执行结果
- `execution.md`：执行摘要


## Backlog（待办队列）

管理终端提供 `/app/backlog` 页面，以 PRD 文件为粒度展示 `tasks/pending/` 与 `tasks/archive/` 中的任务全景；扫描 Markdown 文件时会排除文件名为 `README.md` 的目录说明文档。

### 视图说明

- 页面为两栏布局：左侧是受管理仓库列表（含启用/路径状态点），右侧是当前仓库的 PRD 画布与工具条。
- 默认只显示 `pending` PRD，勾选「显示已归档」后同时展示 `archived` PRD。
- 提供三种视图：依赖图（默认，按 PRD 依赖做拓扑分层绘制节点与连线）、时间轴、列表。列表视图按优先级（P0 → P3）与更新时间排序。
- 「时间轴」「列表」选择会通过 `PATCH /backlog/settings` 回写 `default_view`；「依赖图」是纯前端默认视图，不回写后端（后端 `default_view` 仅接受 `timeline`/`list`）。
- 每个 PRD 卡片展示：标题、当前状态、验收清单进度、关联 Issue、依赖关系与下一步操作。

### 本地快照与后台刷新

`GET /api/v1/agent-runner/backlog/prds` 的数据源是**本地快照**而不是请求时实时扫描：
列表响应对每个 PRD 都要串行查多次 GitHub API，冷访问可达数十秒；快照机制把它换成
"立即返回上一次扫描结果 + 后台异步刷新"。

- 快照存放在 `~/.kedacode/console.db` 的 `backlog_prd_snapshots` 表，按
  `(repo_id, include_archived)` 各存一行完整列表响应与 `scanned_at`，进程重启后仍然有效。
- 读路径只做单行 SELECT：快照缺失、损坏或构建时间超过 30 秒时响应携带 `stale=true`，
  同时向协调器申请一次**非阻塞**的后台重扫（同一仓库同一视图在途只跑一次）；
  请求线程绝不等待扫描结果。「损坏」按列表响应的完整结构判定（含每条 PRD 与其依赖、
  `next_action` 的字段），结构不符即视同没有快照，坏数据绝不下发。
- 后台重扫复用 dashboard 已验证的同步循环模式（独立一条 `MonitorSyncScheduler` 循环 +
  按任务键去重的协调器），开关与间隔复用上面的**全局同步设置**；console 启动首圈为所有
  启用仓库补建默认视图快照（启动预取），「显示已归档」变体首次访问时按需构建。
- 「开始 / 全局开始」成功后立即为对应仓库申请一次后台重扫，列表在下一次轮询起尽快反映新状态。
- 重扫或写库失败时旧快照原样保留（响应不清空），下个周期自动重试；后台重建期间 GitHub
  查询失败会**中止本次重建**，不会把「查不到」降级成「未开始 / 被阻塞」写进快照——
  宁可让用户继续看上一份好数据，也不落一份网络抖动造成的假状态。已禁用/已删除仓库
  返回 400，其历史快照不会回流到页面。
- 页面展示「数据截至 HH:MM:SS」；`stale=true` 时追加「后台更新中…」并把轮询间隔临时
  缩短到 3 秒，拿到新鲜数据后恢复 30 秒。没有快照时显示「正在同步…」空态；列表请求
  本身失败（后端不可达、仓库已移除等）时如实显示「加载失败」而不是伪装成正在同步。
- 旧的 30 秒内存缓存已整体删除，快照是列表读路径的唯一缓存机制；PRD 详情、验收证据、
  CI/CD、Autopilot 等点击时加载的接口保持每次实时读取不变。

### PRD 原文浏览

在任一视图（依赖图 / 时间轴 / 列表）选中 PRD，右侧会以 **master-detail** 方式打开统一详情：左侧画布保持原样（依赖图的上下游关系因此不会丢失），详情头部展示标题、状态、路径与动作，下方是可扩展的标签容器。

- 默认标签「PRD 原文」按需拉取该 PRD 的完整 Markdown 原文并渲染——标题层级、表格、代码块与验收清单的勾选状态都会保留。窄屏自动退化为上下堆叠，不引入 modal 或第二个路由。
- 原文经只读端点 `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/content` 获取，路径编码与「开始此 PRD」按钮复用同一套 base64url 约定。
- 只有 `tasks/pending/` 与 `tasks/archive/` 下后缀为 `.md` 的文件可读；目录穿越、绝对路径、非 `.md` 后缀与符号链接逃逸一律返回 4xx，响应体不含任何文件内容。
- 全文按需单独取，`GET /backlog/prds` 列表响应仍只携带元数据，不随 PRD 篇幅膨胀。
- 读取失败（PRD 已被删除、后端不可达等）时详情视图显示明确错误态并提供「重试」，不会白屏或永久加载。

### 状态映射

PRD 的 GitHub Issue label 被映射为统一状态：

| 状态 | 来源 |
|---|---|
| 未开始 | 无 Issue 或 Issue 无 workflow label |
| 就绪 | `agent/ready` |
| 运行中 | `agent/running` |
| 监督中 | `agent/supervising` |
| 待审阅 | `agent/review` 或存在 open PR |
| 失败 | `agent/failed` |
| 阻塞 | `agent/blocked` |
| 已合并 | Issue 关闭且 PR 已合并 |
| 已归档 | PRD 位于 `tasks/archive/`：执行侧交付已完成，**不等于已验收**——验收状态看 PRD 顶部横幅（🧍 待人工验收 / ✅ 已验收） |
| 等待中 | 依赖未满足 |

### 单个开始

三种视图都可以在详情头部「开始此 PRD」——包括默认视图依赖图里的节点（过去只有时间轴/列表有这个入口）。按钮的可见与禁用由同一个 `canStartBacklogPrd` 规则决定：`state` 为 `not_started` / `failed` / `waiting` 且没有 `block_reason`。存在未满足依赖时按钮禁用并显示阻塞原因，不允许绕过依赖门禁。点击后后端会：

1. 若 PRD 无 Issue，调用 `create_issue_from_prd` 的安全路径（`publish_prd=True, queue_ready=True`），在 PRD 成功发布到 base branch 后添加 `agent/ready`。
2. 若 PRD 已有 Issue，直接添加 `agent/ready` 并移除 `agent/failed`。
3. 启动一次 `kc run` 托管进程。

启动成功后页面会立刻重新拉取该仓的 PRD 列表，并把详情头部状态刷新为服务端返回的最新状态——不是本地乐观值，也不是只弹一个 toast。

「全局开始」与「停止全局调度」的行为没有变化：前者仍是一次性批量启动，后者仍只清空等待队列。它们与仓库级 Autopilot 开关是两件事，见下两节。

### 验收证据浏览（归档 PRD）

勾选「显示已归档」并选中已归档 PRD，详情里会出现「验收证据」标签，展示该 PRD 在仓库中**当前仍保留**的报告与附件——不用离开 Backlog 去编辑器里翻目录。

- 事实源是仓库里配置证据目录下该 PRD 的子目录（默认 `tasks/evidence/<prd-stem>/`，显式 legacy 目录沿用扁平语义），由既有的 `resolve_evidence_dir` 单一入口解析。
- 清单只列一层普通非隐藏文件，带文件名、大小、类型与角色（证据报告 / 验收报告 / 验证计划 / 附件）；Markdown 与纯文本可内联预览，图片直接显示，其它类型可下载。数量与大小来自真实 `stat`，**不会**用验收清单的勾选数冒充文件数。
- 归档后 Issue 上的临时证据 orphan 分支会被清理，因此这里承诺的是仓库保留的长期事实源；历史 PRD 若本来就没有证据目录，页面显示「尚无可用证据」与实际查找位置，而不是报错或空白。
- 访问边界：文件名以 base64url token 传递，解码后必须是证据目录的直接子文件；路径穿越、符号链接逃逸、子目录伪装与超过 10 MiB 的文件一律返回 4xx，不泄露仓外内容。服务端每次请求都重新读盘，不做任何缓存。

### 仓库级 Autopilot 开关

Backlog 顶部为当前选中仓库提供「Autopilot 自动推进」开关，写入该仓库根目录 `.kedacode.toml` 的 `[agent_runner.autopilot].enabled`。

- 只改这一个布尔键：注释、顺序、同级键与未知子表在写回后逐字保留，写入采用同目录临时文件 + 完整加载校验 + 原子替换；写成功与否以**写后 fresh load 的生效配置**为判据，页面显示的值就是重新读出来的值。
- 修改后不要求重启 console：daemon 每轮本来就读取对应仓库上下文，最迟下一轮生效；正在进行的那一轮不会被中断，页面文案写的是「将在下一轮生效」。
- 页面把三件事分开显示，缺哪件都给出明确降级文案：
  - **Autopilot 是否开启**（自动发现、依赖解锁、队列补位）；
  - **daemon 是否运行中**（状态来自既有 process supervisor 记录；daemon 没跑时显示「自动推进暂不执行」，开关值仍可保存，可用 Processes 页面启动 daemon）；
  - **自动合并是否启用**（`safety.auto_merge`）。
- **开关不会联动打开 `safety.auto_merge`**。自动合并需要 `autopilot.enabled` 与 `safety.auto_merge` 同时为真（既有的双重危险动作门禁）；只有前者为真时流程会停在「待审阅」，人工合并后再由持续调度自动推进下游。想放开自动合并必须自己改 `.kedacode.toml`，UI 不会替你做这个决定。
- 目标仓没有 `.kedacode.toml`、配置非法或不可写时返回 409，原文件保持不变。

### 全局调度

在控制面板设置并发数（1–10）后点击「全局开始」：

- 系统扫描所有无依赖且可安全进入 ready 的 pending PRD。
- 按优先级排序，同时启动最多 N 个 PRD。
- 超出槽位的 PRD 进入 `backlog_queue` 等待队列。
- 点击「停止全局调度」可清空等待队列，已运行的进程不会被中断。

### 持续调度（Continuous Scheduling）

「全局开始」是一次性动作：启动后即使有 PRD 跑完，空出来的槽位也不会自动补位。持续调度补的就是这一环——把「对账 → 释放槽位 → 晋升下一批」做成一个幂等的调度阶段，由 daemon 在每个 pass 开始时自动执行，也可以手动触发。

调度阶段由 `core/use_cases/backlog_actions.py::advance_backlog_queue` 实现，一次 pass 分三步：

1. **对账（reconcile）**：把 `backlog_queue` 中 `queued`/`running` 的条目与 PRD 的真实 GitHub 状态对齐。
   - MERGED / ARCHIVED → `completed`，槽位释放。
   - FAILED → `failed` 并写入 `error_detail`（**失败泊车**），槽位释放；泊车条目不会被重试，需要人工在 `/backlog` 页面处理后再回到调度。
   - BLOCKED → 保留为 `running`，但**不占槽也不晋升**，等人工解除阻塞。
   - WAITING → 不动，依赖未满足的 PRD 继续等待。
2. **槽位核算**：`free_slots = max_parallel - RUNNING 条目数`。只有 RUNNING 计数，BLOCKED 不占槽；结果为负时按 0 处理。`max_parallel` **沿用控制台「全局调度」里持久化的并发数**（1–10，缺省 1），不是独立配置项——想调整在途数量就改控制台那个值，或用 `--dry-run` 先预检当前口径。
3. **晋升（promote）**：候选集 = 队列中 `queued` 的条目 ∪ 新发现的未入队 pending PRD（**发现式入队**）。候选经依赖重算后过滤出 `NOT_STARTED` 且无 `block_reason` 的 PRD，按 `P0 > P1 > P2 > P3`、再按 `updated_at` 升序排序，最多晋升 `free_slots` 个。

排序与过滤复用 `_select_eligible_prds` 这一个共享 helper，手动「全局开始」与自动调度走的是同一段代码，两条路径不会漂移。

daemon 路径下的晋升**只**做幂等的 Issue 创建/复用 + 打上 `agent/ready` label，**不 spawn 进程**；真正的进程拉起仍由 daemon 的 Phase 2 在消费 `agent/ready` 时统一完成。该阶段仅在 `autopilot.enabled` 时执行，任何异常都会被记录且不影响 daemon 后续阶段。这个已有的开关正是 Backlog 顶部「Autopilot 自动推进」所控制的那个值（见上文「仓库级 Autopilot 开关」）；开关关闭后同一个场景零晋升，用 Backlog 页面与其它配置入口改这个键的效果完全一致。

#### 手动触发：kc backlog advance

```bash
# 预演：输出完整调度计划，零副作用
uv run kc backlog advance --dry-run

# 实际执行
uv run kc backlog advance --repo <repo-id>
```

`--dry-run` 会打印对账结果（completed / failed 泊车）、`max_parallel` 与 `free_slots`、将要晋升的 PRD 与因槽位不足继续排队的 PRD，但不写库、不建 Issue、不打 label。

#### 幂等与竞态

- 同一 pass 内重复执行不会重复建 Issue 或重复打 label；没有可晋升候选时是零写入。
- 已 `failed` 泊车的 PRD 不会被下一 pass 重新发现；已 `running` 的条目不会被重复晋升。
- 手动「开始」与调度阶段并发时不会双开：晋升只负责把 PRD 推到 `agent/ready`，进程拉起是单点行为。

#### 使用注意

- **开启持续调度后，`tasks/pending/` 的语义收紧为「放进去就会被自动执行」**。发现式入队会捡起任何满足条件的新 pending PRD，包括只是想先记下来的实验性草稿。不想被自动执行的草稿请放 `tasks/inbox/`（既有惯例），成熟后再由 PRD 流程升级到 `tasks/pending/`。
- **状态解析依赖 GitHub 可达性**：`gh` 调用失败时 resolver 保守返回（依赖判定不通过），本轮会少晋升而不是误晋升，下一轮 pass 自愈；调度阶段自身的异常只记日志，不影响 daemon 后续阶段。

### CI/CD 交付尾段与自动修复策略

有 PR 的 PRD 在实现与本地验证结束后进入「等待真实 CI/CD」阶段：daemon 的 review pass 持续刷新 GitHub checks，Backlog 右侧 `CI/CD` 标签同时展示原始 checks 状态与 Agent 决定/未验证说明。**checks 状态本身从不映射为动作**——是否修复由 Supervisor Agent 依据 checks 证据与 PRD 验收要求决定，平台只按策略约束 Agent 选出的 `repair_pr_branch` 动作。

#### 策略模型：全局默认 + 单 PRD 三态覆盖

- **全局默认**：仓库级 `post_pr_supervisor.auto_repair_ci`（`.kedacode.toml`，默认 `false`），由 Backlog 顶部「全局自动修复 CI/CD」开关或 `kc backlog ci policy --global on|off` 控制；只改这一个布尔键，与 Autopilot、`safety.auto_merge`、提交前 Fix Agent 互不联动（Autopilot 的合并队列等 checks 全绿是合并门禁，不会因失败而触发自动修复）。
- **单 PRD 覆盖**：`inherit`（跟随全局，默认）/ `on`（强制开启）/ `off`（强制关闭），事实源是对应 GitHub Issue 最新一条 `<!-- iar:ci-auto-repair-policy value=... -->` marker；无 Issue 的 PRD 只能跟随全局。
- **最终生效值**由服务端按 `显式 on/off ?? fresh 全局值` 计算（`on → true`、`off → false`、`inherit → 全局值`），API/CLI 只回显，前端不得自行推断。

Supervisor 的 `repair_pr_branch` JSON 决策包含 `repair_scope`：纯代码审查问题使用 `code_review`，CI 失败修复或混合问题使用 `ci`。缺失或无效值保守按 `ci` 处理。`auto_repair_ci` 与单 PRD 覆盖只限制 `ci`；`code_review` 可正常交回 executor，避免 CI 全绿时仍因 CI 自动修复关闭而滞留。两类修复共享 `max_repair_attempts` 上限及同一 head 的幂等请求，不扩大总重试预算。Supervisor 评论回显范围，便于核对实际政策。

#### 多轮修复与重入去重

Agent 选择 repair 且策略开启时，复用既有 `execute_repair` 修复同一 PR 分支；推送新 head 后重新等待 checks，允许跨新 head 多轮，直到 `post_pr_supervisor.max_repair_attempts` 上限。轮次事实源是既有 `post_pr_rework_requested` marker（`action=repair_pr_branch`）与 PR head SHA，不新增数据库表；同一 head SHA 的修复请求（daemon 重入、页面重试、手动/自动路径）幂等，最多触发一次。耗尽、repair 失败或 worktree 不可恢复时停止自动副作用，问题保留在右侧详情中，可显式请求一次手动修复（仍受上限、worktree 与禁止路径门禁约束）。

#### CLI 一等入口：kc backlog ci

```bash
# 只读观察；--json 输出与 Console Backlog API 的 ci_delivery DTO 同构（stdout 纯 JSON）
uv run kc backlog ci status --json --repo-id <repo>
uv run kc backlog ci status --prd tasks/pending/xxx.md --repo-id <repo>

# 仓库级默认 / 单 PRD 三态（两目标互斥）
uv run kc backlog ci policy --global on --repo-id <repo>
uv run kc backlog ci policy --prd tasks/pending/xxx.md inherit|on|off --repo-id <repo>

# 显式单次修复（幂等、受门禁约束；--dry-run 零副作用报告）
uv run kc backlog ci repair --prd tasks/pending/xxx.md --dry-run --repo-id <repo>
```

`status --json` 的数据只走 stdout，进度与警告走 stderr。全局 `--output json` 语义退出码契约由 `P1-FEAT-20260930-141135` 统一，本组命令保持前向兼容。CLI 与 Console API 共用同一批 core 用例与写回事实源：任一侧设置策略或请求修复，另一侧 fresh 读取后立即生效，两侧都不各自计算 effective 值。

#### 失败语义

`PENDING`、GitHub 不可达与零 job / billing 限制导致的 aggregate FAILURE 都按原始观察展示（`unavailable` / 未验证），不会标成代码失败或通过，也不会仅因 FAILURE 自动触发 repair；问题卡只呈递 `checks_summary` 的原始摘要，不伪造 job 名、日志或根因。

### 依赖等待

PRD 的 `Delivery Dependencies` 小节会解析为两类依赖边（Issue 与 PRD 引用）：

- `Depends on tasks/issues: #42`：等待上游 Issue 关闭。
- `Depends on tasks/issues: tasks/pending/xxx.md`：等待上游 PRD 合并或归档。归档随交付 PR 一起合并进主线，解锁时机就是该 PR 合并；上游 PRD 可能带着 🧍 待人工验收归档（已归档 ≠ 已验收），下游照常解锁。
- `Depends on tasks/issues: tasks/archive/xxx.md`：视为已完成上游；默认 pending 视图不展示 archived PRD，但仍会用它们解析依赖。

存在未满足依赖的 PRD 显示为「等待中」并给出阻塞原因；无法解析的 PRD 引用显示为「依赖未解析」；形成环的依赖会标红提示修正。

## Idea Inbox（想法采集 + 草稿审阅）

`/ideas` 页面把现有的 `tasks/inbox/ideas.md` 原话日志接入管理终端，作为 PRD 待办队列的上游：用户先在 inbox 累积想法，AI 生成 PRD 草稿，人确认后才落入 `tasks/pending/`。该能力复用 `agent-runner` 命名空间下的仓库 registry 与 `IContentGenerator`，不引入第二个项目映射表。

### 事实源与目录约定

- `tasks/inbox/ideas.md` 是 append-only 原话日志，AI 永不重写已有条目（仅在末尾追加 `## YYYY-MM-DD HH:MM · <source> · <author> (idea-id)` 块）。
- `tasks/inbox/summary.md` 是 AI 派生的可重写总结，刷新时整文覆盖并在文件头明确标注「事实以 `ideas.md` 为准」。
- `tasks/inbox/prd-drafts/` 存放待审阅的 PRD 草稿；文件名 `<YYYYMMDD-HHMMSS>-<slug>.md`，草稿顶部 metadata 块（`Draft Status: pending-review|approved|rejected`、`Source Idea Refs:` 等）由 `core/use_cases/idea_prd_drafts.py` 注入。
- 草稿经人在 `/ideas` 页面确认后复制到 `tasks/pending/`，命名为 `<PRIORITY>-<TYPE>-<YYYYMMDD-HHMMSS>-<slug>.md`；草稿状态同步改为 `approved`，并把目标 pending 路径写回 metadata。

### API 端点（`/api/v1/agent-runner/idea-inbox/*`）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/repositories/{repo_id}` | 读取 inbox 快照（ideas / summary / drafts） |
| `POST` | `/repositories/{repo_id}/ideas` | 前端追加想法到 `ideas.md` |
| `POST` | `/repositories/{repo_id}/summary/refresh` | 重写 `summary.md` |
| `POST` | `/repositories/{repo_id}/drafts` | 生成 PRD 草稿到 `prd-drafts/` |
| `POST` | `/repositories/{repo_id}/drafts/{encoded}/approve` | 草稿确认入 pending |
| `POST` | `/inbound` | 外部 IM / webhook 通用入站（签名校验） |
| `GET` | `/metadata` | 列出可用 priority / type 与 inbound 配置 |

### 跨平台接入（inbound）

`POST /api/v1/agent-runner/idea-inbox/inbound` 接受 provider-neutral 负载：

```json
{
  "provider": "feishu",
  "repo_id": "keda-main",
  "sender": "user-or-open-id",
  "text": "想法原文",
  "occurred_at": "2026-06-15T20:15:00+08:00"
}
```

安全要求：

1. 请求必须携带 `X-IAR-Signature` header，值为 `sha256=<HMAC_SHA256(secret, raw_body)>`。secret 来自环境变量 `KEDACODE_IDEA_INBOX_INBOUND_SECRET`，**不写入** `config.toml` / `.kedacode.toml`。
2. `repo_id` 必须显式给出并能在 registry 中解析；缺少或被禁用则返回 `400`。
3. Feishu 等 provider adapter 只做 payload 转换：消息永远先写入 `ideas.md`（不会被猜项目路由到默认仓库，也不会直接创建 pending PRD）。如果消息只携带 `@项目名` 而没有 `repo_id`，adapter 应当返回明确错误或把消息放进 `unassigned` 队列；当前实现要求 `repo_id` 显式。

签名计算的 body 必须是 HTTP 请求的原始字节（不要重新序列化后再 HMAC），以免空格 / 字段顺序差异导致签名失败。

### 安全边界与已知限制

- secrets 不进 `config.toml` / `.kedacode.toml`；inbound secret 走环境变量。
- 草稿 AI 生成复用 `IContentGenerator` / `generated_content.py` 模式，不新增 LLM SDK；当 generator 不可用时草稿退化为带原话摘录的 fallback 模板，待人补全。
- Idea → Draft → Pending 三段是单向流：草稿不会自动跑 runner，必须等人在 `/ideas` 确认才进入 pending。
- 飞书自定义机器人 webhook 主要用于向群发送消息，不应被当作入站通道；事件订阅或自建通用 inbound 才是正确路径。

## 本地记忆持久化与 Skill 蒸馏（Memory Persistence & Skill Distillation）

Agent Runner 默认开启两层**本地**记忆与 skill 蒸馏循环，用于把同类 Issue 的成功修复经验沉淀给后续 Issue 复用。所有数据保存在**目标仓库的主检出** `.iar/memory/` 与 `.iar/skills/` 下（默认被 `.gitignore` 排除），不引入外部数据库或服务。即使每个 Issue 在独立的工作副本里跑，记忆与 skill 也都在主检出里累积并跨 Issue 共享——目录的解析由 `backend.engines.agent_runner.factory._anchor_memory_config` 在构造 `RepositoryRunContext` 时一次性绝对化到 `repo_path`。

### 数据落点

| 用途 | 路径 | 格式 |
|---|---|---|
| 短期记忆（每个 Issue 的执行轨迹） | `<repo_path>/.iar/memory/short_term/<repo_id>/<issue_number>/context.json` | JSON |
| 长期记忆（项目约定 / 模式） | `<repo_path>/.iar/memory/long_term/<category>/<topic>.md` | Markdown + YAML front matter |
| Skill 草稿 | `<repo_path>/.iar/skills/drafts/<name>.md` | Markdown + YAML front matter (`draft: true`) |
| 已晋升 skill | `<repo_path>/.iar/skills/<name>.md`（默认；可配置） | Markdown + YAML front matter (`draft: false`) |

注：相对路径 **相对目标仓库主检出根** 解析，而不是相对每个 Issue 的工作副本。

### 锚点与并发语义

- `config.toml` `[agent_runner.memory]` 的 `base_dir` / `skill_drafts_dir` / `promoted_skills_dirs` 中的**相对路径**在 engines 层会被解析为 `<repo_path>/<rel>` 的绝对路径；**绝对路径或 ``~``** 会被原样使用，不再挂到任何锚点下。
- 想把记忆放在仓库外（防 `git clean -fdx` 误删或多机共享），运营者把上述字段改为绝对路径即可，例如：

```toml
[agent_runner.memory]
base_dir = "~/.kedacode/memory/keda-main"
skill_drafts_dir = "~/.kedacode/memory/keda-main/skills/drafts"
promoted_skills_dirs = ["~/.kedacode/memory/keda-main/skills"]
```

- 共享锚点下三个 store 的写入统一为 ``tmp + os.replace`` 原子替换；并发场景是 ``last-write-wins``，不会出现半写损坏文件。
- 运行时 `build_default_memory_services(worktree_path, config.memory)` 收到相对路径时会发 `logger.warning`，提示预期由 factory 绝对化；这是防回归告警，正常生产路径不应出现。

### 触发点

- `kc run <issue>` 启动：`build_prompt` 通过 `core/agent/memory/memory_loader.load_relevant_memory` 检索与当前 Issue 标签/标题/正文相关的长期记忆和已晋升 skills，以**目录形式**（name / description / 路径，不含全文）注入 prompt。
- `run_agent_until_committed` 每轮尝试结束：调用 `core/agent/memory/short_term_memory.save_short_term_memory` 写入 `context.json`，记录尝试序号、失败类型、完整详情；成功时另存最终方案摘要，供后续提炼回看。
- `run_agent_until_committed` 成功返回、`_finish_implementation_publication` 开头：只对明确记录为成功且带实际 Agent 名称的执行生成 skill。蒸馏会读取该 Issue 的完整短期尝试历史、已记录的最终方案、相对 base branch 的真实提交 diff 与验证命令结果，再由最后一个成功 Agent 的只读 `generate` 能力提炼。生成内容必须包含触发条件、步骤、验证、陷阱和证据，并通过格式与本地路径 / Issue 编号过滤后才写入草稿；证据、生成或写盘失败都不阻塞后续 push / review / PR。
- 自动提炼只会创建 `draft: true` 草稿。生成草稿时 `usage_count` 和 `success_count` 保持为 0，因为 runner 目前无法确认 Agent 是否实际读取并采用了被推荐的 skill；因此不能把“被检索到”或“本次 Issue 成功”误作技能使用效果，草稿不会自动晋升。维护者审阅后再手动晋升。

### 关闭与降级

- `config.toml` 的 `[agent_runner.memory] enabled = false` 完全关闭记忆读写与 skill 蒸馏；现有 runner 路径无回归。
- `auto_promote = false` 时草稿不会自动晋升到 `.iar/skills/`，维护者手动移动文件并（可选）编辑 description。当前自动提炼产生的草稿即使 `auto_promote = true` 也不会自动晋升；只有未来接入可核验的实际使用记录后，该门槛才会对这些草稿生效。
- 维护者可手工创建 `<worktree>/.iar/memory/long_term/facts/<topic>.md` 写入项目约定，runner 会在相关 Issue 的 prompt 中自动引用。

### 与已有机制的关系

- **不替换** checkpoint / WIP commit 机制：短期记忆只存上下文摘要，文件状态仍由 git 与 checkpoint 负责。
- **不替换** hooks（如 `check_guidelines_consistency.py`）：hooks 负责静态检查，记忆 / skill 负责从执行历史中提取可复用知识。
- **不修改** 外部 agent CLI 协议：所有注入通过 runner 侧 `build_prompt` 完成。

## 容器化运行（Containerized Runner）

kc 默认跑在本机，需要本机预先安装你打算使用的 agent CLI（内置注册表提供 codex / claude / kimi / pi / codebuddy / qoder / opencode 的形态，但只有装了对应 CLI 才能真正跑起来）以及 gh / Node / uv / just / git 等工具链。当本机还同时用于交互式 AI 编程（例如通过 cc-switch 切换多个 claude 账号）时，daemon 进程会与本机共享 `~/.claude`、`~/.codex`、`~/.kimi-code` 配置，账号切换会污染正在跑的任务。

`kc container` 子命令组提供"把 runner 跑进 Docker 容器"的纯 opt-in 能力：

- 容器内预装全套工具链（claude / codex / kimi / gh / Node / uv / just / git + kc 自身）。
- 认证通过 `kc container auth import` 一次性快照到 `~/.kedacode/container-auth/`，与本机 cc-switch 当前 profile 隔离；本机 cc-switch 后续切换不影响容器。
- 目标仓库挂载进容器，agent 在挂载目录的 `.iar-worktrees/` 建 worktree，宿主机可直接 `kc worktree open` 接管。
- 单仓库先跑通（一个容器跑一个仓库的 daemon）。

### 一次性导入认证

在 cc-switch 切到要给容器用的账号后：

```bash
kc container auth import
```

产出 `~/.kedacode/container-auth/<派生目录>/`，派生规则是去掉 `~` 与各段引导点：`claude`、`codex`、`kimi-code`、`pi/agent`（pi 的 `auth_home` 是 `~/.pi/agent`），以及内置注册表里其它声明了 `auth_home` 的 agent——`codebuddy`、`qoder-cn`、`config/opencode`（opencode 的 `auth_home` 是 XDG 路径 `~/.config/opencode`，所以派生结果有两段）。每个子目录含：

- claude: `settings.json`（含 `ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`）+ `skills/`
- codex: `auth.json`（`OPENAI_API_KEY`）+ `skills/`
- kimi: `config.toml` + `credentials/` + `oauth/` + `device_id` + `skills/`

运行时状态（`history.jsonl`、`sessions/`、`cache/`、`paste-cache/`、`file-history/` 等）**不会**复制进容器。目标根目录权限 0700，仅宿主用户可读。

想换容器用的账号？本机 cc-switch 切过去再跑一次 `kc container auth import`（覆盖）。

### 准备 GitHub Token

`gh` 在 macOS 用 keychain 存 token，容器读不到。`kc container up` 从当前 shell 环境变量（或 `--gh-token`）读取 `GH_TOKEN` 并注入容器，因此需在启动容器前导出：

```bash
export GH_TOKEN="$(gh auth token)"
kc container up --repo /absolute/path/to/your-repo --repo-id keda
```

> 注意：写入目标仓库 `.env.local` 不会生效--`kc container up` 不加载仓库级 dotenv，只读 shell 环境变量与 `--gh-token` 参数。若希望持久化，把 `export GH_TOKEN=...` 放进 shell rc 或用 `--gh-token` 显式传参。

### 启动容器 Runner

```bash
kc container up --repo /absolute/path/to/your-repo --repo-id keda
```

- `--repo` 指定目标仓库绝对路径（也可用环境变量 `REPO_PATH`）
- `--repo-id` 是 `config.toml` 里 `[agent_runner.repositories.<id>]` 的 id；传入后 kc 会先检查同 id 的本机 daemon lock，若已有活 daemon 则拒绝启动并提示先停本机 daemon（避免两个 daemon 抢同一个 `agent/running` Issue）
- `GH_TOKEN` 自动从环境变量（或 `--gh-token`）注入到容器内 `gh` CLI

容器内 `kc daemon` 走与本机完全一致的执行路径：轮询 Issue → 建 worktree → 调 agent CLI → 验证 → commit → push → 开 PR。

### 本机可见 Worktree

agent 在容器内建的 worktree 落在挂载目录的 `<repo>/.iar-worktrees/issue-<N>/`，宿主 IDE 可直接打开：

```bash
kc worktree open
ls /path/to/your-repo/.iar-worktrees/
```

容器以宿主机 `os.getuid()`/`os.getgid()` 运行，worktree 文件属主 = 宿主当前用户，非 root。

### 查看日志与停止

```bash
# streaming 容器日志
kc container logs

# 停止容器（保留容器外的工作目录、container-auth 快照）
kc container down
```

### 容器资产定位

runner 容器资产（`Dockerfile.runner`、`docker-compose.runner.yml`、`.env.example`、`kedacode-runner-entrypoint.sh`）随 keda 包发布在 `backend.engines.agent_runner.templates.runner_container/`，由 `kc container up` 通过 `importlib.resources` 自动定位。无需克隆 keda 源码，全局安装 `kc` 后即可使用。

资产路径解析：

```bash
uv run python -c "from importlib.resources import files; print(files('backend.engines.agent_runner.templates').joinpath('runner_container/docker-compose.runner.yml'))"
```

> **kedacode-runner-entrypoint.sh**：容器以 root 启动（不在 compose 顶层设 user），entrypoint 把 `/home/runner` chown 到 `RUNNER_UID:RUNNER_GID`，再用 `gosu` 切到目标 UID 跑 `CMD`。`gosu` 切用户时会按 `/etc/passwd` 重置 `HOME`（无对应记录时回退到 `/`）；宿主映射的 UID（macOS 501）在容器内通常没有 passwd 记录，entrypoint 会用 `gosu UID:GID env HOME=/home/runner KEDACODE_HOME=... PATH=... "$@"` 显式注入关键环境变量，让 `gh` / `claude` / `codex` / `kimi` 在 `RUNNER_UID != 1000` 的宿主机（如 macOS 当前用户 UID 通常是 501）上也能正常创建 `~/.config` / `~/.cache` 子目录。

### 排障速查

| 症状 | 可能原因 | 排查 |
|---|---|---|
| `docker: command not found` | Docker 未装 | `brew install --cask docker` 或装 Docker Desktop |
| 容器内 `claude --version` 失败 | kimi 安装脚本不可达 / npm 包镜像问题 | `docker compose -f <compose> run --rm iar-runner sh -c 'which claude; echo $PATH'` |
| 容器内 `gh auth status` 401 | `GH_TOKEN` 未传或过期 | `.env.local` 里确认 `GH_TOKEN=<有效 token>` |
| worktree 属主变 root | `RUNNER_UID`/`RUNNER_GID` 未对齐宿主 | `id -u` / `id -g` 与 `.env.local` 校对 |
| `kc container up` 拒绝启动 | 同 repo_id 的本机 daemon 已活 | `kc daemon stop --repo-id <id>` 后重试 |
| `kc run --dry-run` 在容器内失败 | 挂载仓库未 `kc init` | 进容器：`docker compose exec iar-runner kc init` |
| 目标仓库缺失 `agent/*` 标签：`kc` 无法识别可执行 Issue、贴不上目标状态标签，典型表现是 `gh issue edit` 报 label 不存在、Issue 卡在无法流转的状态 | 该仓库从未运行过 `kc labels sync`，或仓库初始化时间早于某些后加的标签（例如 `agent/rework-prd`） | 在目标仓库运行 `kc labels sync` 补齐缺失的 `agent/*` 标签（幂等，可重复执行；仓库根需已有有效 `.kedacode.toml`，否则先 `kc init`） |

### 架构边界

`kc container` 子命令严格遵循 `api → core → engines` 依赖方向：

- `cli_typer_container.py` 只做 Typer 参数解析；不直接 import `backend.engines.*`。
- `core/use_cases/agent_runner_container.py` 作为薄 facade，编排 daemon lock 互斥、env 注入、engines 调用。
- `engines/agent_runner/container_auth.py` + `container_ops.py` 是实现层，前者负责认证复制，后者负责 `docker compose` 子进程封装。

未引入 docker SDK 依赖；`container_ops` 仅通过 `subprocess.run(["docker", "compose", ...])` 调用 docker CLI。`dry-run` 模式完全跳过 docker 调用，便于 CI 验证。

### 与已有 `kc daemon` 的关系

- `kc container up` 与 `kc daemon` 是**互斥**选项：同一仓库不能同时跑本机 daemon 和容器 daemon。
- 不装 Docker 的用户零影响：`kc container` 全部子命令都是 opt-in。

## 接入一个新 agent（声明式注册表）

一个 agent 的全部调用差异——可执行文件、认证/skills 路径、各用途的 argv 片段、提示词投递方式、输出协议——都沉淀为**纯数据**的注册块（`[agent_runner.agents.<name>]`），由统一的命令构造器 `core/use_cases/agent_invocation.py` 组装。`src/` 下没有任何 agent 专有的分支代码：接入新 agent **不需要改 Python 代码**，只需写配置（内置 `codex` / `claude` / `kimi` / `pi` / `codebuddy` / `qoder` / `opencode` 的出厂默认已在代码注册表中生效）。

### 注册块字段表

agent 级字段：

| 字段 | 取值域 | 说明 |
|---|---|---|
| `bin` | 可执行文件名 | 按 `PATH` 解析，不内建嗅探 |
| `label` | `agent/<name>` | agent 路由标签（`choose_agent` 按注册顺序匹配） |
| `label_color` | 6 位 hex，不含 `#` | GitHub 标签颜色 |
| `label_description` | 任意文案 | GitHub 标签描述 |
| `auth_home` | 支持 `~` 的目录 | 本机认证/配置根目录；容器认证导入与用户级 skills 目录派生源 |
| `auth_include` | 相对 `auth_home` 的顶层条目 | 容器认证导入白名单 |
| `auth_exclude` | 相对 `auth_home` 的子路径 | 容器认证导入排除项（运行时状态、缓存等） |
| `project_skills_dir` | 相对仓库根的目录 | 项目级 skills 目录 |

profile 级字段（`profiles.<profile>`，用途为 4 种闭集：`run` / `deliberate` / `generate` / `repl`）：

| 字段 | 取值域 | 说明 |
|---|---|---|
| `args` | 字面量 argv 片段 | 支持 `{cwd}` / `{worktree}` / `{prompt}` 占位符；**不做 shell 展开** |
| `prompt_flag` | 参数名 | 仅 `prompt_delivery = "flag"` 时使用（如 kimi 的 `--prompt`） |
| `prompt_delivery` | `argv_tail` / `flag` / `stdin` | 提示词投递方式 |
| `output_protocol` | 已注册协议 id（默认 `plain`） | 经 `kedacode.agent_output_protocols` entry point 注册表解析；内置 `plain` / `claude-stream-json` / `pi-json-lines` |
| `tail_args` | 字面量 argv 片段 | 追加在展开器之后、提示词之前（如 codex 的 `["exec"]`） |
| `expand` | `"<展开器>:<flag>"` | 运行期参数注入；展开器为**闭集**（当前仅 `git_writable_roots`），如 codex 的 `"git_writable_roots:--add-dir"` |
| `read_only` | bool，默认 `false` | 是否为可验证的只读调用；只读决策入口（planner / `kc ask`）以此做 fail-fast 门禁 |

argv 组装顺序：`[bin] + args(占位符替换) + expanders + tail_args`，再按 `prompt_delivery` 决定提示词落在 argv 尾部、指定 flag 后还是 stdin。字段的三层合并语义与 shell 展开边界详见 [配置说明](configuration.md#agent-runner-agent-注册表配置)。

### 自检流程（`kc agent doctor`）

配置写完后用 doctor 做只读自检，它按**与真实执行完全相同的路径**打印各用途的完整 argv：

```bash
# 列出全部已注册 agent 及各自声明的用途
uv run kc agent list

# 打印某个 agent 全部用途的命令行（含沙箱告警）
uv run kc agent doctor pi --all-profiles

# 机读快照（agent / profile / argv / prompt_delivery，稳定排序，可入 golden diff）
uv run kc agent doctor pi --all-profiles --json

# 列出全部已注册输出协议 id
uv run kc agent doctor --protocols
```

doctor 对三类坏输入分别非零退出并在 stderr 指名原因：agent 未注册、`bin` 不在 `PATH`、引用未注册协议。改完配置后先跑 doctor，再考虑投真实任务。

### 完整范例：pi

pi（`@earendil-works/pi-coding-agent`）的注册块如下（内置默认已生效；写进 `config.toml` 只是为了作为出厂注册块的可抄写参考）：

```toml
[agent_runner.agents.pi]
bin = "pi"
label = "agent/pi"
label_color = "7C3AED"
label_description = "Use pi for local runner execution."
auth_home = "~/.pi/agent"
auth_include = ["auth.json", "settings.json", "models.json", "skills"]
auth_exclude = ["sessions", "pi-crash.log", "models-store.json"]
project_skills_dir = ".pi/skills"

[agent_runner.agents.pi.profiles.run]
args = ["--approve", "--mode", "json"]
prompt_delivery = "stdin"
output_protocol = "pi-json-lines"

[agent_runner.agents.pi.profiles.deliberate]
args = ["--approve", "--no-tools", "--print"]
prompt_delivery = "stdin"
output_protocol = "plain"
read_only = true

[agent_runner.agents.pi.profiles.generate]
args = ["--no-tools", "--print"]
prompt_delivery = "stdin"
output_protocol = "plain"
read_only = true

[agent_runner.agents.pi.profiles.repl]
args = ["--approve", "--print"]
prompt_delivery = "stdin"
output_protocol = "plain"
```

几个值得注意的取舍：

- pi 无内置沙箱，只读语义用 `--no-tools`（禁用全部工具）表达，因此 `generate` / `deliberate` 都声明 `read_only = true` 供只读门禁校验。
- pi 的 `-p/--print` 模式会把管道 stdin 并入初始 prompt，四种用途全部采用 `prompt_delivery = "stdin"`，规避长 prompt 撑爆 argv 上限。
- `run` 用途用 `--mode json` 输出 JSON Lines 事件流，由内置的 `pi-json-lines` 协议做流式渲染；其余用途走 `plain`。

doctor 对它的输出：

```text
pi · deliberate
  argv: pi --approve --no-tools --print
  prompt_delivery: stdin
pi · generate
  argv: pi --no-tools --print
  prompt_delivery: stdin
pi · repl
  argv: pi --approve --print
  prompt_delivery: stdin
pi · run
  argv: pi --approve --mode json
  prompt_delivery: stdin
```

接入后：`--agent` 参数、agent 路由标签、容器认证导入、live 面板协议渲染都会自动识别新 agent，无需任何代码改动。

### 内置 agent 的形态速查（codebuddy / qoder / opencode）

这三个 agent 与前面四个一样是内置默认，注册块见 `config.toml`。它们各自有一处**容易踩的点**，接入或排障前先看这里：

| agent | bin | 形态要点 |
|---|---|---|
| `codebuddy` | `codebuddy` | 与 `claude` 完全同构（`-p` + `--output-format stream-json` + `--include-partial-messages`），四用途齐备，`run` / `deliberate` 复用内置 `claude-stream-json` 协议。配置与凭据在 `~/.codebuddy/`。 |
| `qoder` | **`qodercn`** | 注册名与可执行名**不一致**：`qoder` 只是本机 shell 别名，`bin` 必须写 `qodercn`，否则 doctor 报 executable not found。它**没有** `--verbose` / `--include-partial-messages`；`run` 用 `-o stream-json`（包内 schema 与 claude 同形）走 `claude-stream-json`；`deliberate` 刻意保留 `-p` 并改用 `stdin` + `plain` —— 流式协议会把 `-p` 从 argv 剥离再经 stdin 投递，而 qoder 在缺 `-p` 时的行为未经验证，保留 `-p` 既不依赖未验证行为，也避免长 transcript 撑爆 argv。配置在 `~/.qoder-cn/`。 |
| `opencode` | `opencode` | 非交互入口是 `run` 子命令（消息为位置参数）。**只声明 `run` / `deliberate` / `repl` 三个用途，刻意没有 `generate`**：它没有任何沙箱或只读开关，而 planner / `kc ask` 依赖 `generate` 用途上的只读声明做 fail-fast 门禁，声明一个无法验证的"只读"会让门禁形同虚设。需要 planner 时请选别的 agent。输出用 `--format default`（逐行文本）走 `plain`；`--format json` 是另一套事件形状，本项目没有对应协议。配置在 `~/.config/opencode/`（XDG 路径）。 |

> 三者的容器支持不在本轮范围内：runner 镜像目前只预装 claude / codex / kimi，容器里选中它们会在启动子进程时报可执行文件不存在。

### 未声明用途的行为

一个 agent 不必声明全部四种用途。请求它未声明的用途时 `build_agent_invocation` 会抛 `UnknownProfileError` 并列出该 agent 已声明的用途名，**不会**回落到别的用途或别的 agent。`kc agent doctor <name> <profile>` 同样会以非零退出码指名报错。
